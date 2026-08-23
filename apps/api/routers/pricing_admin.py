"""Admin endpoints: the note-terms review queue, the STP trust policy, and the
underlying-resolution review queue.

    GET    /admin/pricing/note-terms/queue        rows a human still has to look at
    POST   /admin/pricing/note-terms/{id}/resolve settle one disagreed field
    GET    /admin/pricing/stp-policy              active policies (the panel)
    POST   /admin/pricing/stp-policy              grant STP for (cik, form_type)
    DELETE /admin/pricing/stp-policy/{id}         revoke it
    GET    /admin/pricing/underlying-queue        unresolved + proposed edges
    POST   /admin/pricing/underlying-queue/{id}/confirm  settle one edge
    POST   /admin/pricing/underlying-queue/{id}/reject   discard its proposal

SUPER ADMIN ONLY, AND NOT ORG-SCOPED
──────────────────────────────────────────────────────────────────────────────
Everything here operates on GLOBAL public reference data derived from SEC
filings: ``portfolio.securities_global_note_terms``,
``portfolio.reference_filings``, ``portfolio.note_terms_stp_policy``. None of
those tables has an ``org_id`` and none of these endpoints accepts one. The gate
is ``services.rbac.is_super_admin``, resolved server-side from the caller's
principal exactly as ``routers/restricted_access.py`` and
``routers/trading_authority.py`` do — the same helper shape, deliberately, so
there is one staff-only admin pattern rather than three.

The corrections this router writes carry ``org_id = NULL`` for the same reason
(see ``services/note_terms_corrections``): the terms of a Morgan Stanley 424B2
are a public fact, not a tenant's data, and attributing a reviewer's correction
to one tenant would both misrepresent it and leak review activity into a shared
row.

WHY THE QUEUE RETURNS AN EXCERPT AND NOT THE FILING
──────────────────────────────────────────────────────────────────────────────
``reference_filings.extracted_text`` is a whole prospectus — hundreds of
kilobytes. The screen never shows it whole; it shows the sentence extraction
actually read, at ``[source_char_start:source_char_end]``. So the slice is taken
in Postgres and only the slice crosses the wire. The full length is reported
alongside it so a reviewer can see the excerpt is an excerpt.
"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from models.note_terms import validate_field_status
from services.database import get_pool
from services.note_terms_corrections import (
    SOURCE_HUMAN,
    log_note_terms_correction,
)
from services.note_terms_extraction import coerce_field, load_registry
from services.note_terms_routing import (
    NoteTermsRoutingError,
    NoteTermsRoutingPermissionError,
    active_policy,
    envelope_source,
    grant_stp,
    list_policies,
    recorded_disagreement_fields,
    resolved_disagreement_fields,
    revoke_stp,
)
from services.rbac import is_super_admin, load_principal
from services.underlying_resolution import (
    UnderlyingResolutionError,
    UnderlyingResolutionPermissionError,
    confirm_resolution,
    load_queue,
    reject_proposal,
)
from services.users import ensure_user

router = APIRouter(tags=["admin", "pricing"])

TERMS_TABLE = "portfolio.securities_global_note_terms"
FILINGS_TABLE = "portfolio.reference_filings"
POLICY_TABLE = "portfolio.note_terms_stp_policy"
CORRECTIONS_TABLE = "document_field_corrections"

# The term columns a reviewer may write, as an explicit allow-list. The field
# name arrives in a request body and is interpolated into an UPDATE's SET
# clause — a column identifier cannot be a bind parameter — so it is checked
# against this frozen set FIRST. The registry is checked too (a field must be
# governed to be corrected), but the registry lives in a table and this does
# not: an allow-list that a row could widen is not an allow-list.
UPDATABLE_TERM_COLUMNS: frozenset[str] = frozenset({
    "terms_status",
    "product_archetype",
    "protection_type",
    "basket_type",
    "return_basis",
    "is_decrement_index",
    "notional_currency",
    "protection_pct",
    "cap_pct",
    "participation_rate",
    "coupon_rate",
    "coupon_barrier_pct",
    "autocall_barrier_pct",
    "autocall_frequency",
    "has_no_call_period",
    "no_call_months",
    "initial_valuation_date",
    "final_valuation_date",
    "tenor_years",
})

RESOLVE_SOURCES: frozenset[str] = frozenset({"primary", "secondary", "manual"})


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class ResolveField(BaseModel):
    field: str
    chosen_value: str | None = None
    source: str  # 'primary' | 'secondary' | 'manual'
    notes: str | None = None


class GrantPolicy(BaseModel):
    cik: str
    form_type: str
    notes: str | None = None


class ConfirmUnderlying(BaseModel):
    """The reviewer's decision about one unresolved underlying edge.

    Both fields optional and both defaulting to "no": an empty body means
    "accept the standing proposal", which is the overwhelmingly common action
    and should not require the client to echo back an id the server already has.
    """

    global_security_id: UUID | None = None
    create_new: bool = False


# --------------------------------------------------------------------------
# Auth helper — Super Admin only, no org resolution
# --------------------------------------------------------------------------
async def _require_super_admin(request: Request) -> str:
    """Resolve the caller and reject anyone who is not a Super Admin.

    Returns the actor's ``users.id``. Unlike the org-scoped admin routers this
    one returns no org_id — there is no org in play, and inventing one to match
    a helper signature would be the kind of quiet dishonesty that later reads as
    a real scoping rule.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if not is_super_admin(principal):
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return actor_id


# --------------------------------------------------------------------------
# Read: the review queue
# --------------------------------------------------------------------------
@router.get("/admin/pricing/note-terms/queue")
async def note_terms_queue(request: Request):
    """Every current terms row a human still has to look at.

    The membership rule is exactly:

        extraction_confidence = 'needs_review'  OR  routing_decision = 'queued'

    restricted to CURRENT rows (``valid_to``/``system_to`` NULL), because a
    superseded version is history and nobody reviews history.

    The OR is doing real work in both directions. ``routing_decision='queued'``
    catches an agreeing, high-confidence row for an untrusted issuer — nothing
    is wrong with it, it just has not earned straight-through yet. And
    ``needs_review`` catches rows from before routing existed (all 54 of them,
    whose ``routing_decision`` is NULL by design) plus any row whose routing
    step failed. Either half alone would silently drop a population.
    """
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT t.id, t.global_security_id, t.reference_filing_id,
                   t.terms_status, t.product_archetype, t.protection_type,
                   t.basket_type, t.return_basis, t.is_decrement_index,
                   t.autocall_frequency, t.field_status, t.extraction_confidence,
                   t.routing_decision, t.routed_at,
                   t.source_char_start, t.source_char_end,
                   f.cik, f.filer_name, f.form_type, f.filing_date,
                   f.accession_number, f.source_url,
                   length(f.extracted_text) AS extracted_text_length,
                   CASE
                       WHEN t.source_char_start IS NULL OR t.source_char_end IS NULL
                            OR t.source_char_end <= t.source_char_start
                       THEN NULL
                       -- Postgres substr is 1-based; the stored offsets are
                       -- Python slice offsets, so start+1 and a length.
                       ELSE substr(f.extracted_text, t.source_char_start + 1,
                                   t.source_char_end - t.source_char_start)
                   END AS source_excerpt
            FROM {TERMS_TABLE} t
            JOIN {FILINGS_TABLE} f ON f.id = t.reference_filing_id
            WHERE t.valid_to IS NULL AND t.system_to IS NULL
              AND (t.extraction_confidence = 'needs_review'
                   OR t.routing_decision = 'queued')
            ORDER BY f.filing_date DESC, f.accession_number
            """
        )

        term_ids = [r["id"] for r in rows]
        # One query for every disagreement/correction on the whole page rather
        # than N round trips from the detail view.
        detail_rows = await conn.fetch(
            f"""
            SELECT target_id, field_name, original_value, corrected_value,
                   notes, corrected_by, corrected_at
            FROM {CORRECTIONS_TABLE}
            WHERE target_type = 'note_terms' AND target_id = ANY($1::uuid[])
            ORDER BY corrected_at
            """,
            term_ids,
        ) if term_ids else []

        # Which pairings already have STP, so the screen can show the panel and
        # suppress a pointless "grant?" prompt for one already granted.
        policies = await list_policies(conn)

    by_target: dict[str, dict] = {}
    for r in detail_rows:
        bucket = by_target.setdefault(str(r["target_id"]), {"disagreements": [], "corrections": []})
        source = envelope_source(r["notes"])
        record = {
            "field_name": r["field_name"],
            "primary_value": r["original_value"],
            "secondary_value": r["corrected_value"],
            "notes": r["notes"],
            "corrected_by": str(r["corrected_by"]) if r["corrected_by"] else None,
            "corrected_at": r["corrected_at"],
            "source": source,
        }
        if source == SOURCE_HUMAN:
            bucket["corrections"].append(record)
        else:
            bucket["disagreements"].append(record)

    active_pairs = {(p["cik"], p["form_type"]) for p in policies}

    items = []
    for r in rows:
        bucket = by_target.get(str(r["id"]), {"disagreements": [], "corrections": []})
        disagreed = [d["field_name"] for d in bucket["disagreements"]]
        resolved = {c["field_name"] for c in bucket["corrections"]}
        unresolved = [f for f in disagreed if f not in resolved]
        field_status = r["field_status"]
        if isinstance(field_status, str):
            field_status = json.loads(field_status)
        items.append({
            "id": str(r["id"]),
            "global_security_id": str(r["global_security_id"]),
            "reference_filing_id": str(r["reference_filing_id"]),
            "cik": r["cik"],
            "filer_name": r["filer_name"],
            "form_type": r["form_type"],
            "filing_date": r["filing_date"],
            "accession_number": r["accession_number"],
            "source_url": r["source_url"],
            "terms_status": r["terms_status"],
            "product_archetype": r["product_archetype"],
            "extraction_confidence": r["extraction_confidence"],
            "routing_decision": r["routing_decision"],
            "routed_at": r["routed_at"],
            "field_status": field_status or {},
            "source_char_start": r["source_char_start"],
            "source_char_end": r["source_char_end"],
            "source_excerpt": r["source_excerpt"],
            "extracted_text_length": r["extracted_text_length"],
            "disagreed_fields": disagreed,
            "unresolved_fields": unresolved,
            # Fully worked through: every field the ensemble flagged now carries
            # a human decision. The row stays in the returned set — the query
            # definition is fixed — but the screen can show it as done.
            "resolved": bool(disagreed) and not unresolved,
            "disagreements": bucket["disagreements"],
            "corrections": bucket["corrections"],
            "has_active_stp": (r["cik"], r["form_type"]) in active_pairs,
        })

    return {
        "queue": items,
        "policies": [
            {
                "id": str(p["id"]),
                "cik": p["cik"],
                "filer_name": p["filer_name"],
                "form_type": p["form_type"],
                "granted_by": p["granted_by"],
                "granted_at": p["granted_at"],
                "notes": p["notes"],
            }
            for p in policies
        ],
    }


# --------------------------------------------------------------------------
# Write: resolve one disagreed field
# --------------------------------------------------------------------------
@router.post("/admin/pricing/note-terms/{note_terms_id}/resolve")
async def resolve_note_terms_field(
    request: Request, note_terms_id: UUID, body: ResolveField
):
    """Settle one field on a queued row: log the correction, then write the value.

    THE CORRECTION IS LOGGED FIRST, ON PURPOSE. ``log_note_terms_correction`` is
    the ledger; the column write is the convenience copy. If the ledger INSERT
    fails, nothing is written and the reviewer sees an error — better than a
    row whose value changed with no record of who changed it or what it was.

    WHY THIS IS AN IN-PLACE UPDATE AND NOT A RULE-3 SUPERSEDE
      Rule 3 exists so a changed fact does not erase the previous one. Here
      nothing is erased: ``document_field_corrections`` records the original
      value, the chosen value, who chose it and when — that ledger IS the
      history, and it is richer than a superseded row would be.

      A supersede would also break the screen it serves. Corrections and
      ensemble disagreements are keyed to ``target_id`` = this row's id. A row
      with three disagreed fields is resolved one field at a time; superseding
      on the first resolve would hand the next two a NEW row id with none of the
      disagreement records attached, and the reviewer would be looking at a
      blank detail view halfway through the job. Stable identity is a
      requirement of per-field resolution, not a shortcut around Rule 3.

    ``routing_decision`` is NOT changed. The row was queued because at
    extraction time it had a disagreement; that remains true about that moment.
    """
    actor_id = await _require_super_admin(request)

    field_name = (body.field or "").strip()
    if body.source not in RESOLVE_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"source must be one of {sorted(RESOLVE_SOURCES)}",
        )
    if field_name not in UPDATABLE_TERM_COLUMNS:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name!r} is not a correctable term field",
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT t.id, t.field_status, t.extraction_confidence, t.routing_decision,
                   f.cik, f.form_type, f.filer_name
            FROM {TERMS_TABLE} t
            JOIN {FILINGS_TABLE} f ON f.id = t.reference_filing_id
            WHERE t.id = $1::uuid AND t.valid_to IS NULL AND t.system_to IS NULL
            """,
            str(note_terms_id),
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Note-terms row not found")

        registry = await load_registry(conn)
        entry = next((e for e in registry if e.field_key == field_name), None)
        if entry is None:
            raise HTTPException(
                status_code=400,
                detail=f"{field_name!r} is not in the note-terms field registry",
            )

        # Coerce through the SAME function the extraction pipeline uses, so a
        # human-entered '70' and an extracted 70 land in the column identically.
        try:
            coerced = coerce_field(field_name, entry.data_type, body.chosen_value)
        except Exception as exc:  # noqa: BLE001 — a bad value is a 400, not a 500
            raise HTTPException(
                status_code=400,
                detail=f"{body.chosen_value!r} is not a valid {entry.data_type} "
                       f"for {field_name}: {exc}",
            ) from exc
        if coerced is None and body.chosen_value not in (None, "", "null"):
            raise HTTPException(
                status_code=400,
                detail=f"{body.chosen_value!r} could not be read as a "
                       f"{entry.data_type} value for {field_name}",
            )

        original_value = await conn.fetchval(
            f"SELECT {field_name} FROM {TERMS_TABLE} WHERE id = $1::uuid",
            str(note_terms_id),
        )

        correction_id = await log_note_terms_correction(
            conn,
            note_terms_id=str(note_terms_id),
            field_name=field_name,
            original_value=original_value,
            corrected_value=coerced,
            notes=body.notes or f"resolved from {body.source} answer",
            corrected_by=str(actor_id),
            source=SOURCE_HUMAN,
        )

        # field_status[field] -> 'extracted': the value in the column now came
        # off the filing and is trusted. Validated through the same gate every
        # other writer of this column uses; the database will not catch a bad
        # state string on its own (see models.note_terms.validate_field_status).
        current_status = row["field_status"]
        if isinstance(current_status, str):
            current_status = json.loads(current_status)
        new_status = dict(current_status or {})
        new_status[field_name] = "extracted"
        validate_field_status(new_status)

        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            try:
                await conn.execute(
                    f"""
                    UPDATE {TERMS_TABLE}
                    SET {field_name} = $2, field_status = $3::jsonb
                    WHERE id = $1::uuid
                    """,
                    str(note_terms_id), coerced, json.dumps(new_status),
                )
            except Exception as exc:  # noqa: BLE001
                # terms_status participates in the current-row unique index, so
                # a correction can genuinely collide with a sibling version.
                raise HTTPException(
                    status_code=409,
                    detail=f"Could not write {field_name}: {type(exc).__name__}: {exc}",
                ) from exc

        # Is this the LAST unresolved item for the pairing? Drives the grant
        # prompt on the detail screen. Computed from the ledger, never from an
        # accuracy statistic — the system proposes the moment, a person decides.
        pairing_state = await _pairing_state(conn, row["cik"], row["form_type"])

    return {
        "ok": True,
        "correction_id": correction_id,
        "note_terms_id": str(note_terms_id),
        "field": field_name,
        "value": coerced,
        "field_status": new_status,
        "cik": row["cik"],
        "form_type": row["form_type"],
        "filer_name": row["filer_name"],
        **pairing_state,
    }


async def _pairing_state(conn, cik: str, form_type: str) -> dict:
    """How much of one (cik, form_type) pairing still needs a human.

    Counts CURRENT queued rows for the pairing and, for each, whether every
    field the ensemble flagged now carries a human correction.
    """
    rows = await conn.fetch(
        f"""
        SELECT t.id
        FROM {TERMS_TABLE} t
        JOIN {FILINGS_TABLE} f ON f.id = t.reference_filing_id
        WHERE t.valid_to IS NULL AND t.system_to IS NULL
          AND f.cik = $1 AND f.form_type = $2
          AND (t.extraction_confidence = 'needs_review'
               OR t.routing_decision = 'queued')
        """,
        cik, form_type,
    )
    outstanding = 0
    for r in rows:
        disagreed = await recorded_disagreement_fields(conn, str(r["id"]))
        resolved = await resolved_disagreement_fields(conn, str(r["id"]))
        if disagreed - resolved:
            outstanding += 1
    policy = await active_policy(conn, cik, form_type)
    return {
        "pairing_queued_rows": len(rows),
        "pairing_outstanding_rows": outstanding,
        # True => offer the grant. Never grants anything by itself.
        "pairing_cleared": outstanding == 0 and policy is None,
        "pairing_has_active_stp": policy is not None,
    }


# --------------------------------------------------------------------------
# STP policy
# --------------------------------------------------------------------------
@router.get("/admin/pricing/stp-policy")
async def list_stp_policies(request: Request, include_revoked: bool = False):
    """Active policies for the panel; revoked history on request."""
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        policies = await list_policies(conn, include_revoked=include_revoked)
    return {
        "policies": [
            {
                "id": str(p["id"]),
                "cik": p["cik"],
                "filer_name": p["filer_name"],
                "form_type": p["form_type"],
                "enabled": p["enabled"],
                "granted_by": p["granted_by"],
                "granted_at": p["granted_at"],
                "revoked_by": p["revoked_by"],
                "revoked_at": p["revoked_at"],
                "notes": p["notes"],
            }
            for p in policies
        ]
    }


@router.post("/admin/pricing/stp-policy")
async def create_stp_policy(request: Request, body: GrantPolicy):
    """Grant straight-through processing for one issuer/form pairing.

    ``granted_by`` is the authenticated caller's ``users.id``, resolved
    server-side. It is not in the request body and never will be — "who trusted
    this issuer" is the whole point of the audit trail.
    """
    actor_id = await _require_super_admin(request)
    pool = await get_pool()
    try:
        policy_id = await grant_stp(
            pool,
            cik=body.cik,
            form_type=body.form_type,
            granted_by=str(actor_id),
            notes=body.notes,
            is_super_admin=True,  # checked above; passed explicitly, never inferred
        )
    except NoteTermsRoutingPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NoteTermsRoutingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "id": policy_id, "cik": body.cik, "form_type": body.form_type}


@router.delete("/admin/pricing/stp-policy/{policy_id}")
async def delete_stp_policy(request: Request, policy_id: UUID):
    """Revoke a policy. The row is stamped, not deleted — it is the audit trail."""
    actor_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT id, cik, form_type, enabled FROM {POLICY_TABLE} WHERE id = $1::uuid",
            str(policy_id),
        )
    if row is None:
        raise HTTPException(status_code=404, detail="STP policy not found")
    if not row["enabled"]:
        # Already revoked. Reported rather than silently 200'd, so a
        # double-click does not read as a fresh revocation in the UI.
        return {"ok": True, "already_revoked": True, "id": str(policy_id)}

    try:
        await revoke_stp(
            pool,
            cik=row["cik"],
            form_type=row["form_type"],
            revoked_by=str(actor_id),
            is_super_admin=True,
        )
    except NoteTermsRoutingPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except NoteTermsRoutingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "id": str(policy_id), "cik": row["cik"], "form_type": row["form_type"]}


# --------------------------------------------------------------------------
# Underlying resolution — the review queue
# --------------------------------------------------------------------------
#
# WHY THESE LIVE HERE AND NOT IN A NEW ROUTER
# The gate, the scope and the data are identical to the note-terms queue above:
# Super Admin only, no org anywhere, global reference data derived from SEC
# filings. A second router would mean a second copy of _require_super_admin and
# a second place for the two to drift apart.
#
# WHAT THE CONFIRM ENDPOINT IS
# It is the ONLY route in this application to link_state='resolved'. That is
# enforced three deep: this function is the sole caller of
# services.underlying_resolution.confirm_resolution; that function is the sole
# setter of the app.underlying_confirm GUC; and trg_sec_global_rel_confirm_gate
# rejects the UPDATE outright without it. The proposal pipeline is not trusted
# to stay on its side of the line — it is prevented from crossing it.


@router.get("/admin/pricing/underlying-queue")
async def underlying_queue(request: Request):
    """Edges awaiting a decision, each joined to the note that references it.

    Membership is ``link_state IN ('unresolved', 'ambiguous')`` — the two
    non-terminal states. 'ambiguous' means a proposal is standing and a person
    has to accept or refuse it; 'unresolved' means either nothing has been
    proposed yet or the matcher looked and declined. Both need a human, so both
    are in the queue, and ``proposal.confidence`` on each row is what tells them
    apart on screen.
    """
    await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        items = await load_queue(conn)

    counts = {
        "total": len(items),
        "proposed": 0,
        "needs_manual_match": 0,
        "unexamined": 0,
    }
    for item in items:
        proposal = item["proposal"]
        if proposal is None:
            counts["unexamined"] += 1
        elif proposal["confidence"] == "high":
            counts["proposed"] += 1
        else:
            counts["needs_manual_match"] += 1

    return {"queue": items, "counts": counts}


@router.post("/admin/pricing/underlying-queue/{relationship_id}/confirm")
async def confirm_underlying(
    request: Request, relationship_id: UUID, body: ConfirmUnderlying | None = None
):
    """Resolve one edge. THE only path that sets ``link_state='resolved'``.

    ``resolved_by`` is the authenticated caller, resolved server-side and never
    read from the body — for the same reason ``granted_by`` is not in the STP
    grant body. "Who decided this underlying is the S&P 500" is the audit trail.
    """
    actor_id = await _require_super_admin(request)
    body = body or ConfirmUnderlying()
    pool = await get_pool()
    try:
        result = await confirm_resolution(
            pool,
            str(relationship_id),
            actor_id=str(actor_id),
            global_security_id=(
                str(body.global_security_id) if body.global_security_id else None
            ),
            create_new=body.create_new,
            is_super_admin=True,  # checked above; passed explicitly, never inferred
        )
    except UnderlyingResolutionPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except UnderlyingResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@router.post("/admin/pricing/underlying-queue/{relationship_id}/reject")
async def reject_underlying(request: Request, relationship_id: UUID):
    """Discard a standing proposal; the edge returns to a clean 'unresolved'.

    Takes no body. There is nothing to parameterise — refusing a proposal is one
    act with one outcome, and the alternative (a free-text reason) would be an
    unread field on a queue that already records who acted and when.
    """
    actor_id = await _require_super_admin(request)
    pool = await get_pool()
    try:
        result = await reject_proposal(
            pool,
            str(relationship_id),
            actor_id=str(actor_id),
            is_super_admin=True,
        )
    except UnderlyingResolutionPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except UnderlyingResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}
