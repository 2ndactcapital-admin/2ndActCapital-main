"""Straight-through-processing routing for extracted note-terms rows.

WHAT THIS DECIDES, AND WHAT IT DOES NOT TOUCH
──────────────────────────────────────────────────────────────────────────────
This module answers exactly one question about an already-extracted row: does a
human look at it, or not. It runs AFTER extraction and the hazard ensemble have
both finished and been recorded. It does not extract, does not compare models,
does not pick a winner between two answers, and does not alter
``extraction_confidence``. Every one of those is somebody else's job
(``services/note_terms_extraction.py``) and stays that way.

THE RULE, IN ORDER
──────────────────────────────────────────────────────────────────────────────
1. Any hazard-ensemble disagreement  ->  ``queued``. ALWAYS. No policy overrides
   this. STP is a statement of trust in AGREEMENT between the two readers; it is
   never a bypass of disagreement detection. The ensemble runs on every row
   regardless of STP status and its result is recorded identically either way —
   STP changes who looks at the row, not what is computed or stored about it.
2. Otherwise, an ACTIVE policy for (reference_filings.cik, form_type)
   ->  ``stp``.
3. Otherwise  ->  ``queued``. The safe default. An issuer/form pairing nobody has
   explicitly trusted stays in the queue, forever, until somebody grants it.

ONE REFINEMENT TO STEP 2, STATED RATHER THAN SLIPPED IN
──────────────────────────────────────────────────────────────────────────────
Step 2 fires only when ``extraction_confidence = 'high'``. The prompt's rule
scopes the STP branch to "an AGREEING row", and 'high' is the ONLY confidence
value that means the two readers agreed and the comparison actually happened:

  needs_review  either the readers disagreed (step 1 already caught it) or a
                numeric validator failed. A validator failure is not agreement
                about anything — the row is arithmetically inconsistent and a
                person has to look. Routing it 'stp' would also directly
                contradict the queue query, which returns every needs_review row
                by definition; the row would read ``routing_decision='stp'``
                while sitting in the review queue.
  low           the ensemble did not run, or ran on the primary model and so
                compared nothing. The hazard fields are UNMEASURED, not
                confirmed. Straight-through on an unmeasured row would let an
                Anthropic outage silently clear a whole batch — the exact
                failure the extraction sprint's confidence ladder was built to
                prevent.

So STP requires an active policy AND a measured agreement. Trust in an issuer is
not trust in a run that did not happen.

DISAGREEMENT IS READ FROM TWO PLACES, AND EITHER ONE QUEUES
──────────────────────────────────────────────────────────────────────────────
:func:`route_note_terms_row` takes the caller's in-memory ``hazard_disagreements``
AND independently queries the recorded ensemble rows in
``document_field_corrections``. Evidence from EITHER source queues the row.
That asymmetry is deliberate: the extraction pipeline logs disagreements in a
``try/except`` that deliberately never loses a good row over a logging failure,
so the database can under-report. Trusting the database alone would let a failed
INSERT promote a disagreeing row to straight-through. Both sources must be
silent for a row to go STP.

GRANTING IS A HUMAN ACT
──────────────────────────────────────────────────────────────────────────────
Nothing here infers trust from accuracy statistics. :func:`grant_stp` is called
only from a Super-Admin-gated endpoint, with an explicit ``is_super_admin=True``
passed by the caller that has already checked it — the platform's standing
escape-hatch convention (the flag is a parameter, checked FIRST, never inferred
from ambient context). Postgres RLS is the second line: the policy table's write
policies require ``app.is_super_admin``, so a caller that somehow slips past the
Python guard still gets rejected by the database.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Mapping

from services.note_terms_corrections import SOURCE_HAZARD_ENSEMBLE, SOURCE_HUMAN

POLICY_TABLE = "portfolio.note_terms_stp_policy"
TERMS_TABLE = "portfolio.securities_global_note_terms"
FILINGS_TABLE = "portfolio.reference_filings"
CORRECTIONS_TABLE = "document_field_corrections"
TARGET_TYPE = "note_terms"

# The two routing outcomes. Matches
# securities_global_note_terms_routing_decision_chk exactly; changing one
# without the other is a bug.
QUEUED = "queued"
STP = "stp"
ROUTING_DECISIONS: frozenset[str] = frozenset({QUEUED, STP})

# The only form types the corpus contains, and the only ones the policy table's
# CHECK accepts. Kept here so the API layer can reject a bad value with a 400
# instead of surfacing a raw constraint violation.
POLICY_FORM_TYPES: frozenset[str] = frozenset({"424B2", "FWP"})

# Only a measured agreement is eligible for straight-through. See the module
# docstring — this is a deliberate narrowing of step 2, not an accident.
STP_ELIGIBLE_CONFIDENCE = "high"


class NoteTermsRoutingError(ValueError):
    """A routing or policy operation could not be performed as specified."""


class NoteTermsRoutingPermissionError(PermissionError):
    """A policy write was attempted without Super Admin."""


# ── Reading the recorded ensemble result ──────────────────────────────────────


async def recorded_disagreement_fields(conn, note_terms_id: str) -> set[str]:
    """The hazard fields the ensemble RECORDED a disagreement on for this row.

    Reads ``document_field_corrections`` rows whose ``notes`` envelope carries
    ``source = hazard_ensemble_disagreement``. Readable with no org context:
    the global read policy on that table is ``USING (target_type <> 'document')``.

    Note the envelope is matched by parsing the JSON, not by ``LIKE`` — a human
    correction whose free-text rationale happened to quote the phrase
    "hazard_ensemble_disagreement" would otherwise be misread as a machine
    observation and hold a resolved row in the queue forever.
    """
    rows = await conn.fetch(
        f"""
        SELECT field_name, notes
        FROM {CORRECTIONS_TABLE}
        WHERE target_type = $1 AND target_id = $2::uuid
        """,
        TARGET_TYPE, str(note_terms_id),
    )
    return {
        r["field_name"] for r in rows
        if envelope_source(r["notes"]) == SOURCE_HAZARD_ENSEMBLE
    }


async def resolved_disagreement_fields(conn, note_terms_id: str) -> set[str]:
    """The hazard fields a PERSON has since resolved on this row.

    A field counts as resolved once a ``human_review`` correction exists for it.
    Used to tell a queue entry that still needs work from one that is done —
    without touching ``extraction_confidence``, which records what extraction
    concluded and must not be rewritten to make a screen look tidier.
    """
    rows = await conn.fetch(
        f"""
        SELECT field_name, notes
        FROM {CORRECTIONS_TABLE}
        WHERE target_type = $1 AND target_id = $2::uuid
        """,
        TARGET_TYPE, str(note_terms_id),
    )
    return {
        r["field_name"] for r in rows
        if envelope_source(r["notes"]) == SOURCE_HUMAN
    }


def envelope_source(notes: Any) -> str | None:
    """Read ``source`` out of a corrections ``notes`` envelope, defensively.

    ``notes`` is a text column holding JSON written by
    ``log_note_terms_correction``. Anything that is not a JSON object with a
    string ``source`` returns None — an unparseable envelope is not evidence of
    a machine disagreement, and must not be guessed at either way.
    """
    if notes is None:
        return None
    if isinstance(notes, Mapping):
        payload = notes
    else:
        try:
            payload = json.loads(notes)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, Mapping):
        return None
    source = payload.get("source")
    return source if isinstance(source, str) else None


# ── Policy lookup ─────────────────────────────────────────────────────────────


async def active_policy(conn, cik: str, form_type: str) -> dict | None:
    """The one ACTIVE policy for this pairing, or None.

    At most one row can come back — ``note_terms_stp_policy_active_unique`` is a
    partial unique index on ``(cik, form_type) WHERE enabled``. Revoked rows for
    the same pairing may sit alongside it and are ignored here; they are history.
    """
    if not cik or not form_type:
        return None
    row = await conn.fetchrow(
        f"""
        SELECT id, cik, form_type, enabled, granted_by, granted_at, notes
        FROM {POLICY_TABLE}
        WHERE cik = $1 AND form_type = $2 AND enabled = true
        """,
        str(cik), str(form_type),
    )
    return dict(row) if row is not None else None


async def list_policies(conn, *, include_revoked: bool = False) -> list[dict]:
    """Every policy, active first, newest grant first within each group.

    ``filer_name`` is joined on for display ONLY — it is not the key and is not
    stable (the corpus holds 'JPMORGAN CHASE & CO' at cik 19617 and
    'JPMorgan Chase Financial Co. LLC' at cik 1665650: different issuers a
    name-based key would merge or split arbitrarily). ``DISTINCT ON`` picks one
    of the names EDGAR has filed under that CIK; the cik is what the policy
    matches on.
    """
    where = "" if include_revoked else "WHERE p.enabled = true"
    rows = await conn.fetch(
        f"""
        SELECT p.id, p.cik, p.form_type, p.enabled, p.granted_by, p.granted_at,
               p.revoked_by, p.revoked_at, p.notes, n.filer_name
        FROM {POLICY_TABLE} p
        LEFT JOIN LATERAL (
            SELECT DISTINCT ON (f.cik) f.filer_name
            FROM {FILINGS_TABLE} f
            WHERE f.cik = p.cik
            ORDER BY f.cik, f.filing_date DESC
        ) n ON true
        {where}
        ORDER BY p.enabled DESC, p.granted_at DESC
        """
    )
    return [dict(r) for r in rows]


# ── The routing decision ──────────────────────────────────────────────────────


async def route_note_terms_row(
    pool,
    note_terms_row,
    *,
    hazard_disagreements: Mapping[str, Any] | None = None,
    persist: bool = True,
) -> Literal["queued", "stp"]:
    """Decide whether one extracted terms row is queued for review or goes STP.

    Args:
        pool: an asyncpg pool (or the RLS-wrapped pool). Its own connection is
            acquired; the caller's is not borrowed.
        note_terms_row: a mapping or asyncpg Record for the row just written.
            Must carry ``id``. ``reference_filing_id`` and
            ``extraction_confidence`` are read from it when present and looked
            up from the database when not, so a caller holding only an id still
            gets a correct answer rather than a wrong one built from defaults.
        hazard_disagreements: the ensemble's in-memory result, when the caller
            has it. A NON-EMPTY value queues the row on its own, even if nothing
            was recorded in the database — see the module docstring.
        persist: write the decision onto the row. False is for callers that want
            the answer without the side effect (the queue screen's preview, and
            the verification script's read-only assertions).

    Returns:
        ``'queued'`` or ``'stp'``.

    Raises:
        NoteTermsRoutingError: the row carries no id, or its id does not exist.
    """
    row = dict(note_terms_row) if note_terms_row is not None else {}
    note_terms_id = row.get("id")
    if not note_terms_id:
        raise NoteTermsRoutingError("note_terms_row must carry an id")
    note_terms_id = str(note_terms_id)

    # A caller-supplied disagreement is decisive on its own and needs no
    # database round trip to be believed.
    caller_disagreed = bool(hazard_disagreements)

    async with pool.acquire() as conn:
        stored = await conn.fetchrow(
            f"""
            SELECT t.id, t.reference_filing_id, t.extraction_confidence,
                   f.cik, f.form_type
            FROM {TERMS_TABLE} t
            LEFT JOIN {FILINGS_TABLE} f ON f.id = t.reference_filing_id
            WHERE t.id = $1::uuid
            """,
            note_terms_id,
        )
        if stored is None:
            raise NoteTermsRoutingError(
                f"note_terms row {note_terms_id} does not exist — nothing to route"
            )

        confidence = row.get("extraction_confidence") or stored["extraction_confidence"]
        recorded = await recorded_disagreement_fields(conn, note_terms_id)

        # ── Rule 1 — disagreement always queues. Checked FIRST, and no policy
        #    lookup can reach past it.
        if caller_disagreed or recorded:
            decision: Literal["queued", "stp"] = QUEUED
        else:
            # ── Rule 2 — an agreeing, MEASURED row under an active policy.
            policy = None
            if confidence == STP_ELIGIBLE_CONFIDENCE:
                policy = await active_policy(conn, stored["cik"], stored["form_type"])
            # ── Rule 3 — everything else stays in the queue.
            decision = STP if policy is not None else QUEUED

        if persist:
            async with conn.transaction():
                # The terms table's UPDATE policy is super-admin-only. This is a
                # machine write on global reference data with no tenant and no
                # human actor, so it self-elevates for the length of this
                # statement exactly as the extraction pipeline does. SET LOCAL
                # semantics: it does not leak past the transaction.
                await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
                await conn.execute(
                    f"""
                    UPDATE {TERMS_TABLE}
                    SET routing_decision = $2, routed_at = now()
                    WHERE id = $1::uuid
                    """,
                    note_terms_id, decision,
                )

    return decision


# ── Policy writes — Super Admin only ──────────────────────────────────────────


def _require_super_admin(is_super_admin: bool, action: str) -> None:
    """The explicit escape-hatch check, first, before any work.

    A parameter rather than an ambient lookup, per the platform convention: the
    caller that authenticated the actor is the only thing that knows whether
    this is a Super Admin, and passing it makes the check visible at the call
    site instead of buried in context.
    """
    if not is_super_admin:
        raise NoteTermsRoutingPermissionError(
            f"{action} requires Super Admin — straight-through processing is granted "
            "by an explicit human act, never inferred and never delegated"
        )


async def grant_stp(
    pool,
    cik: str,
    form_type: str,
    granted_by: str | None,
    notes: str | None,
    *,
    is_super_admin: bool = False,
) -> str:
    """Grant straight-through processing for one (cik, form_type) pairing.

    Inserts a NEW active row. It does not resurrect a previously revoked one:
    the revoked row is the record of who trusted this pairing before and why,
    and flipping it back would erase that. The partial unique index guarantees
    the new row is the only active one.

    Returns:
        The new policy id, as a string.

    Raises:
        NoteTermsRoutingPermissionError: ``is_super_admin`` is not True.
        NoteTermsRoutingError: missing cik, or a form_type outside the CHECK, or
            an active policy for the pairing already exists.
    """
    _require_super_admin(is_super_admin, "grant_stp")

    cik = str(cik or "").strip()
    form_type = str(form_type or "").strip()
    if not cik:
        raise NoteTermsRoutingError("cik is required — it is the stable issuer key")
    if form_type not in POLICY_FORM_TYPES:
        raise NoteTermsRoutingError(
            f"form_type {form_type!r} is not one of {sorted(POLICY_FORM_TYPES)}"
        )

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            existing = await active_policy(conn, cik, form_type)
            if existing is not None:
                # Checked explicitly so the caller gets a sentence rather than a
                # raw unique-index violation. The index is still the real
                # guarantee — this is the message, not the enforcement.
                raise NoteTermsRoutingError(
                    f"an active STP policy already exists for cik={cik} {form_type} "
                    f"(granted {existing['granted_at']}); revoke it before re-granting"
                )
            policy_id = await conn.fetchval(
                f"""
                INSERT INTO {POLICY_TABLE} (cik, form_type, enabled, granted_by, notes)
                VALUES ($1, $2, true, $3, $4)
                RETURNING id
                """,
                cik, form_type, granted_by, notes,
            )
    return str(policy_id)


async def revoke_stp(
    pool,
    cik: str,
    form_type: str,
    revoked_by: str | None,
    *,
    is_super_admin: bool = False,
) -> None:
    """Revoke the active policy for a pairing. Rows route to the queue again.

    The revoke stamps ``enabled=false`` plus ``revoked_by``/``revoked_at`` ON THE
    GRANT ROW — that row becomes the record of the whole grant→revoke episode,
    which is why the revocation CHECK insists the stamps and the flag move
    together. Already-routed rows are NOT re-routed: a decision made under a
    policy that was live at the time is a fact about that moment, and rewriting
    it would falsify the audit trail.

    Idempotent: revoking a pairing with no active policy is a no-op, not an
    error — the desired end state is already true.
    """
    _require_super_admin(is_super_admin, "revoke_stp")

    cik = str(cik or "").strip()
    form_type = str(form_type or "").strip()
    if not cik or not form_type:
        raise NoteTermsRoutingError("cik and form_type are both required")

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            await conn.execute(
                f"""
                UPDATE {POLICY_TABLE}
                SET enabled = false, revoked_by = $3, revoked_at = now()
                WHERE cik = $1 AND form_type = $2 AND enabled = true
                """,
                cik, form_type, revoked_by,
            )
