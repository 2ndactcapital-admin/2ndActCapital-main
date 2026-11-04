"""Replication Studio (rep12) — the NOTE-VERSUS-REPLICA CHECK across new issues.

THE HOOK AND THE SWEEP
──────────────────────────────────────────────────────────────────────────────
Every time a live ``portfolio.securities_global_note_terms`` row is written —
``services.note_terms_extraction.store_extraction`` (the extraction's one write
path, sync or a forced re-extraction) and a human correction
(``POST /admin/pricing/note-terms/{id}/resolve``) — :func:`on_terms_written`
checks that terms version for every org that uses replication. The daily
document-reading run (aibudget1) writes readings and staging only, never a live
terms row, so it has no hook of its own: whatever reaches the terms table goes
through the two writers above, and :func:`sweep` (each scheduler tick, right
after rep11's verdict job) catches anything the hook missed.

IDEMPOTENT BY TERMS VERSION. A check's ``input_key`` = rep11's verdict
fingerprint of the terms (payoff fields, field_status, confidence, routing, the
features read, rep11's RULE_VERSION) + the note's underlying edges (state,
target, proposal). A live check with the same terms id and key ⇒ nothing is
written. A new terms id (a restatement, or a final superseding a preliminary),
an in-place correction, or a changed underlying (a Super Admin's confirmation)
⇒ the live check is closed (valid_to) and a new one written. "One live check
per (org, note)" is a partial UNIQUE index, not a convention.

WHAT A CHECK DOES, per org
──────────────────────────────────────────────────────────────────────────────
1. rep11's verdict (assess, through the platform verdict job — idempotent).
2. A DRAFT replica only when rep11's ``buildable`` holds: static / approximate,
   terms approved (extraction_confidence 'high', no failed used field, nothing
   out of scale, not queued), the spec complete, and EXACTLY ONE RESOLVED
   underlying. Built by rep11's own builder with NO person (``prepare_draft``):
   status 'draft', no maker — it is never submitted or approved here. An open or
   approved replica of the note already in the org is linked instead
   ('existing'). Otherwise 'not_built' with rep11's reasons, word for word.
3. The comparison, when a replica exists: rep11's ``compute_cost`` (the same
   math) as an insert-only comparison row marked 'automatic_check'. Priced only
   when the note's price and every leg have a quote; otherwise "not priced —
   enter a quote" naming each missing input.
4. Alerts (a system-opened human task, eligible permission view_replication):
   a priced embedded-fee estimate ABOVE the org's threshold, or a watched issuer
   / underlying / archetype. Holders of view_replication are notified through
   the human-task fan-out; a recipient who is not an internal role has the to-do
   dismissed at once (members never receive note alerts).

NO AUTOMATIC CONFIRMATION, NO AUTOMATIC APPROVAL. Nothing here writes
link_state. The underlying queue's confirm calls
``services.underlying_resolution.confirm_resolution`` — still the one writer of
'resolved' — once per edge, as the Super Admin who clicked. Review / dismiss of
a check is a person's decision, recorded with the user (through the alert task
when one is open).

"A NEW ISSUE" — the note's 424B2 filing date (reference_filings.filing_date);
when the terms have no filing, the day they were first written (system_from).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, timezone
from decimal import Decimal, InvalidOperation

import asyncpg

from services import human_tasks as HT
from services import note_replication as N
from services import options_sleeve as OS
from services.database import get_pool, reset_rls_context, set_rls_context

log = logging.getLogger(__name__)

T_CHECK = "public.note_checks"
T_SET = "public.note_check_settings"
VIEW, MANAGE, APPROVE = N.VIEW, N.MANAGE, N.APPROVE
TASK_ALERT = "note_check_alert"
SUBJECT = "structured_note"

#: A verify sets this to True to let the hook and the sweep reach its fixture orgs (rep6 fix 2: no scheduled or
#: event-driven pass acts on an ``organizations.is_test`` org by default).
HOOK_INCLUDES_TEST_ORGS = False
#: A verify also sets this to its own fixture org ids, so the hook and a re-check after an underlying decision never
#: write a check for a FIXTURE note into a real org. None (production) = every org that uses replication.
HOOK_ONLY_ORGS: list | None = None

TRIGGER_LABELS = {"extraction": "Terms extracted", "correction": "Terms corrected", "sweep": "Daily sweep",
                  "underlying_confirmed": "Underlying confirmed", "manual": "Checked on request"}
STATUS_LABELS = {"new": "New", "reviewed": "Reviewed", "dismissed": "Dismissed"}
OUTCOME_LABELS = {"prepared": "Draft replica prepared", "existing": "Replica already on file",
                  "not_built": "No replica"}
PRICING_LABELS = {"priced": "Priced (estimate)", "not_priced": N.NOT_PRICED, "no_replica": "No replica to price"}
UNDERLYING_LABELS = {"resolved": "Confirmed", "ambiguous": "Proposed — needs confirmation",
                     "unresolved": "Not matched", "multiple": "Several underlyings", "none": "None recorded"}
ALERT_LABELS = {"fee_threshold": "Embedded-fee estimate above the threshold", "watched_issuer": "Watched issuer",
                "watched_underlying": "Watched underlying", "watched_archetype": "Watched archetype"}
SORTS = {"newest": "Newest first", "fee": "Embedded-fee estimate (highest first; unpriced last)"}
ISSUED_BASIS_LABELS = {"filing_date": "424B2 filing date", "first_extracted": "first extracted (no filing)"}
QUEUE_GROUP_LABELS = {"one_proposal": "One registry proposal — confirm in one click",
                      "manual": "No proposal — open the manual match"}
REVIEW_OUTCOMES = {"reviewed": "done", "dismissed": "reject"}
HUNDRED = Decimal(100)


class CheckError(ValueError):
    pass


class CheckNotFound(CheckError, LookupError):
    pass


class CheckConflict(CheckError):
    pass


def _iso(v):
    return v.isoformat() if v is not None else None


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _dumps(v) -> str:
    return json.dumps(v, default=str)


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


class _Ctx:
    """The RLS context for one unit of work (org scope, or platform scope with super admin)."""

    def __init__(self, org_id=None, super_admin: bool = False):
        self.org_id, self.super_admin = org_id, super_admin

    def __enter__(self):
        self.tokens = set_rls_context(str(self.org_id) if self.org_id else None, self.super_admin)
        return self

    def __exit__(self, *exc):
        reset_rls_context(self.tokens)
        return False


async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"note_checks.{action}", "note_check", str(resource_id),
        _dumps(payload))


# ═══ Inputs (pure) ═════════════════════════════════════════════════════════
def edges_key(edges: list[dict]) -> list:
    return sorted([[e.get("raw_underlying_text"), e.get("link_state"), str(e.get("to_global_security_id") or ""),
                    str(e.get("proposed_global_security_id") or "")] for e in edges])


def input_key(t: dict, edges: list[dict], readings: dict | None) -> str:
    """rep11's verdict fingerprint of the terms + the underlying edges. Unchanged ⇒ the check is not repeated."""
    payload = {"terms_id": str(t["id"]),
               "verdict_fp": N.fingerprint(t, n_underlyings=len(edges), readings=readings),
               "edges": edges_key(edges)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def issued(t: dict) -> tuple[date, str]:
    """"A new issue" = the 424B2 filing date; without a filing, the day the terms were first written."""
    if t.get("filing_date"):
        return t["filing_date"], "filing_date"
    sf = t.get("system_from") or t.get("valid_from")
    return sf.astimezone(timezone.utc).date(), "first_extracted"


def underlying_text(edges: list[dict]) -> str:
    return ", ".join(e.get("target_name") or e.get("proposed_name") or e.get("raw_underlying_text") or "?"
                     for e in edges) or "—"


def _contains(needles, *hay) -> list[str]:
    text = " ".join(h for h in hay if h).lower()
    return [n for n in (needles or []) if n and n.lower() in text]


def alert_reasons(*, settings: dict, t: dict, edges: list[dict], pricing_status: str,
                  fee: Decimal | None) -> list[dict]:
    """Why this check alerts staff (empty = no alert). Pure."""
    out = []
    thr = settings.get("fee_alert_threshold_pct")
    if pricing_status == "priced" and fee is not None and thr is not None and fee > thr:
        out.append({"code": "fee_threshold", "text": f"Embedded-fee estimate {OS.dstr(fee)} per 100 is above the "
                                                     f"threshold of {OS.dstr(thr)} — {N.ESTIMATE}"})
    hit = _contains(settings.get("watch_issuers"), t.get("filer_name"), t.get("credit_entity"), t.get("issuer_group"))
    if hit:
        out.append({"code": "watched_issuer", "text": f"Watched issuer: {', '.join(hit)}"})
    names = []
    for e in edges:
        names += [e.get("raw_underlying_text"), e.get("target_name"), e.get("proposed_name")]
    hit = _contains(settings.get("watch_underlyings"), *names)
    if hit:
        out.append({"code": "watched_underlying", "text": f"Watched underlying: {', '.join(hit)}"})
    if t.get("product_archetype") and t["product_archetype"] in (settings.get("watch_archetypes") or []):
        out.append({"code": "watched_archetype",
                    "text": f"Watched archetype: {N.ARCHETYPE_LABELS.get(t['product_archetype'])}"})
    return out


# ═══ Settings (ORG; valid axis) ════════════════════════════════════════════
DEFAULT_SETTINGS = {"enabled": True, "fee_alert_threshold_pct": None, "watch_archetypes": [], "watch_issuers": [],
                    "watch_underlyings": []}


async def settings_of(conn, org_id) -> dict:
    row = await conn.fetchrow(f"SELECT * FROM {T_SET} WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS "
                              "NULL", str(org_id))
    if row is None:
        return {**DEFAULT_SETTINGS, "id": None, "is_default": True}
    return {"id": str(row["id"]), "is_default": False, "enabled": row["enabled"],
            "fee_alert_threshold_pct": _dec(row["fee_alert_threshold_pct"]),
            "watch_archetypes": list(row["watch_archetypes"] or []), "watch_issuers": list(row["watch_issuers"] or []),
            "watch_underlyings": list(row["watch_underlyings"] or []), "created_by": str(row["created_by"]),
            "valid_from": _iso(row["valid_from"])}


def settings_out(s: dict) -> dict:
    return {**s, "fee_alert_threshold_pct": OS.dstr(s.get("fee_alert_threshold_pct"))}


def _clean_list(v, what: str) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
        raise CheckError(f"{what} is a list of text")
    out = [x.strip() for x in v if x.strip()]
    if len(out) > 50 or any(len(x) > 200 for x in out):
        raise CheckError(f"{what}: at most 50 entries of up to 200 characters")
    return out


async def put_settings(pool, *, org_id, actor_id, enabled: bool, fee_alert_threshold_pct, watch_archetypes,
                       watch_issuers, watch_underlyings) -> dict:
    """A person changes the org's settings: the live row is closed and the new one written (Rule 3), audited."""
    thr = None
    if fee_alert_threshold_pct not in (None, ""):
        thr = _dec(fee_alert_threshold_pct)
        if thr is None or thr < 0 or thr > HUNDRED:
            raise CheckError("the alert threshold is an embedded-fee estimate per 100 of principal, 0 to 100")
    arch = _clean_list(watch_archetypes, "watched archetypes")
    bad = [a for a in arch if a not in N.ARCHETYPE_LABELS]
    if bad:
        raise CheckError(f"unknown archetype(s) {bad}; one of {list(N.ARCHETYPE_LABELS)}")
    iss, und = _clean_list(watch_issuers, "watched issuers"), _clean_list(watch_underlyings, "watched underlyings")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        async with conn.transaction():
            await conn.execute(f"UPDATE {T_SET} SET valid_to = clock_timestamp() WHERE org_id = $1::uuid AND "
                               "valid_to IS NULL AND system_to IS NULL", str(org_id))
            row = await conn.fetchrow(
                f"""INSERT INTO {T_SET} (org_id, enabled, fee_alert_threshold_pct, watch_archetypes, watch_issuers,
                        watch_underlyings, created_by, valid_from)
                    VALUES ($1::uuid, $2, $3::numeric, $4::text[], $5::text[], $6::text[], $7::uuid, clock_timestamp())
                    RETURNING id""", str(org_id), bool(enabled), thr, arch, iss, und, str(actor["id"]))
            await _audit(conn, org_id, actor["id"], "settings_changed", row["id"],
                         {"enabled": bool(enabled), "fee_alert_threshold_pct": OS.dstr(thr), "watch_archetypes": arch,
                          "watch_issuers": iss, "watch_underlyings": und})
        return await settings_of(conn, org_id)


# ═══ Which orgs are checked ════════════════════════════════════════════════
async def check_orgs(pool, *, include_test_orgs: bool = False, only=None) -> list[str]:
    """Every org that uses replication (a role in the org holds view_replication) and has not turned the check
    off. Never a verify fixture org unless asked (rep6 fix 2)."""
    with _Ctx(None, True):
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT DISTINCT o.id FROM public.organizations o
                    JOIN public.roles r ON r.org_id = o.id
                    JOIN public.role_permissions rp ON rp.role_id = r.id
                    JOIN public.permissions p ON p.id = rp.permission_id AND p.name = $1
                    WHERE ($2::boolean OR NOT COALESCE(o.is_test, false))
                      AND NOT EXISTS (SELECT 1 FROM {T_SET} s WHERE s.org_id = o.id AND s.valid_to IS NULL
                                      AND s.system_to IS NULL AND NOT s.enabled)
                    ORDER BY o.id""", VIEW, bool(include_test_orgs))
    out = [str(r["id"]) for r in rows]
    if only is not None:
        want = {str(x) for x in only}
        out = [o for o in out if o in want]
    return out


# ═══ The check ═════════════════════════════════════════════════════════════
async def _platform_inputs(pool, terms_id) -> dict | None:
    """The terms version, its verdict (computed now if the job has not reached it), edges and readings. None when
    the row is not live or is not the note's CURRENT terms (a preliminary superseded by a final)."""
    with _Ctx(None, True):
        async with pool.acquire() as conn:
            rows = await N.live_terms(conn, terms_ids=[terms_id])
        if not rows:
            return None
        t = rows[0]
        async with pool.acquire() as conn:
            cur = N.current_of(await N.live_terms(conn, note_ids=[t["global_security_id"]]))
        if not cur or str(cur[0]["id"]) != str(t["id"]):
            return None
        await N.run_verdicts(pool, terms_ids=[t["id"]])
        async with pool.acquire() as conn:
            v = await N.verdict_of(conn, t["id"])
            edges = (await N.underlyings_of(conn, [t["global_security_id"]]))[str(t["global_security_id"])]
            rd = (await N.readings_of(conn, [t["reference_filing_id"]])).get(str(t["reference_filing_id"])) \
                if t["reference_filing_id"] else None
    if v is None:
        return None
    return {"t": t, "v": v, "edges": edges, "readings": rd, "key": input_key(t, edges, rd)}


async def _broad_based(conn, und: dict) -> tuple[bool, str | None]:
    """The automatic draft's Section 1256 flag: an index → the flag of a contract already defined on it, else
    broad-based (rep8's default for an index). A person confirms it on the draft."""
    if und.get("security_type") != "index":
        return False, None
    got = await conn.fetchval("SELECT broad_based_index FROM portfolio.option_contracts WHERE "
                              "underlying_global_security_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL "
                              "ORDER BY created_at DESC LIMIT 1", str(und["id"]))
    if got is not None:
        return bool(got), f"Section 1256 flag taken from a contract already defined on {und['name']} — confirm it"
    return True, (f"{und['name']} assumed broad-based (rep8's default for an index; it decides Section 1256) — a "
                  "person confirms it on the draft")


async def _replica_step(pool, org_id, inp: dict) -> dict:
    t, v, edges = inp["t"], inp["v"], inp["edges"]
    async with pool.acquire() as conn:
        rep = await conn.fetchrow(
            f"SELECT * FROM {N.T_REP} WHERE org_id = $1::uuid AND global_security_id = $2::uuid AND system_to IS NULL "
            "AND status IN ('draft', 'pending_approval', 'approved') ORDER BY (status = 'approved') DESC, created_at "
            "DESC LIMIT 1", str(org_id), str(t["global_security_id"]))
    if rep is not None:
        notes = [f"An {N.STATUS_LABELS[rep['status']].lower()} replica of this note is already on file — linked, "
                 "not rebuilt."]
        if str(rep["note_terms_id"]) != str(t["id"]):
            notes.append("It was built on an earlier terms version: archive it and the next check prepares a new "
                         "draft from these terms.")
        return {"outcome": "existing", "replica_id": str(rep["id"]), "reasons": [], "notes": notes}
    b = N.buildable(v, edges)
    if not b["ok"]:
        return {"outcome": "not_built", "replica_id": None, "reasons": b["why"], "notes": []}
    us = N.underlying_state(edges)
    async with pool.acquire() as conn:
        und = await OS._security(conn, us["security_id"])
        broad, broad_note = await _broad_based(conn, und)
    try:
        d = await N.prepare_draft(pool, org_id=org_id, note_id=t["global_security_id"], broad_based_index=broad)
    except N.NoteRefused as exc:
        return {"outcome": "not_built", "replica_id": None, "reasons": list(exc.reasons) or [str(exc)], "notes": []}
    except (N.NoteError, OS.OptionsError) as exc:
        return {"outcome": "not_built", "replica_id": None, "reasons": [f"the builder refused: {exc}"], "notes": []}
    notes = ["A DRAFT replica was prepared automatically — never submitted or approved here: a person completes it, "
             "submits it, and a second person approves it."]
    if broad_note:
        notes.append(broad_note)
    if d["replica"]["start_level"] is None:
        notes.append(d["replica"]["start_level_text"])
    return {"outcome": "prepared", "replica_id": d["replica"]["id"], "reasons": [], "notes": notes}


async def _open_alert(pool, org_id, *, t: dict, check_id: str, reasons: list[dict]) -> str | None:
    """A system-opened human task to holders of view_replication; an open one for the note is reused. Any
    recipient who is not an internal role has the to-do dismissed at once."""
    title = f"New issue to look at: {t['security_name']}"
    try:
        task = await HT.open_task(pool, org_id=org_id, kind=TASK_ALERT, subject_type=SUBJECT,
                                  subject_id=t["global_security_id"], maker_id=None, eligible_permission=VIEW,
                                  title=title, payload={"check_id": check_id, "reasons": reasons},
                                  action_path="/portfolio/replication/notes/new-issues", actor_kind="system")
    except HT.HumanTaskConflict:
        async with pool.acquire() as conn:
            open_ = await HT.list_tasks(conn, org_id, status="open", kind=TASK_ALERT, subject_type=SUBJECT,
                                        subject_id=t["global_security_id"], limit=1)
        return open_[0]["id"] if open_ else None
    await _withhold_from_clients(pool, org_id, task["id"])
    return task["id"]


async def _withhold_from_clients(pool, org_id, task_id) -> int:
    from services.authz import INTERNAL_ROLE_NAMES

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """UPDATE member_todos m SET status = 'dismissed', updated_at = now()
               WHERE m.org_id = $1::uuid AND m.related_type = 'workflow_run_step' AND m.related_id = $2::uuid
                 AND m.status = 'open'
                 AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = m.user_id AND u.role = 'super_admin')
                 AND NOT EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
                                 WHERE ur.user_id = m.user_id AND r.name = ANY($3::text[]))
               RETURNING m.user_id""", str(org_id), str(task_id), sorted(INTERNAL_ROLE_NAMES))
        if rows:
            await _audit(conn, org_id, None, "alert_withheld", task_id,
                         {"users": [str(r["user_id"]) for r in rows], "reason": "not an internal role"})
    return len(rows)


async def _archive(conn, check_id) -> None:
    got = await conn.fetchval(
        f"""INSERT INTO {T_CHECK}
            SELECT (jsonb_populate_record(NULL::{T_CHECK}, to_jsonb(c) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', c.id, 'system_to', clock_timestamp()))).*
            FROM {T_CHECK} c WHERE c.id = $1::uuid AND c.system_to IS NULL RETURNING id""", str(check_id))
    if got is None:
        raise CheckConflict("the check is not live — refusing to change it")


async def _update(conn, check_id, fields: dict) -> dict:
    """System axis: copy the outgoing state to a fresh id, then change the live row (which keeps its id)."""
    await _archive(conn, check_id)
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    row = await conn.fetchrow(f"UPDATE {T_CHECK} SET {sets}, updated_at = clock_timestamp(), system_from = "
                              "clock_timestamp() WHERE id = $1::uuid AND system_to IS NULL RETURNING *",
                              str(check_id), *fields.values())
    if row is None:
        raise CheckConflict("the check changed underneath this request — reload and retry")
    return dict(row)


async def check_org(pool, org_id, inp: dict, *, trigger: str) -> dict:
    """One org's check of one terms version (the caller holds no RLS context; this sets the org's)."""
    t = inp["t"]
    with _Ctx(org_id, False):
        async with pool.acquire() as conn:
            live = await conn.fetchrow(f"SELECT id, note_terms_id, input_key FROM {T_CHECK} WHERE org_id = $1::uuid "
                                       "AND global_security_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL",
                                       str(org_id), str(t["global_security_id"]))
            settings = await settings_of(conn, org_id)
        if live and str(live["note_terms_id"]) == str(t["id"]) and live["input_key"] == inp["key"]:
            return {"org_id": str(org_id), "action": "unchanged", "check_id": str(live["id"])}
        rep = await _replica_step(pool, org_id, inp)
        cmp_ = None
        if rep["replica_id"]:
            cmp_ = await N.price_automatic(pool, org_id=org_id, replica_id=rep["replica_id"])
        if cmp_ is None:
            pricing, fee, missing = "no_replica", None, []
        elif cmp_["status"] == "priced":
            pricing, fee, missing = "priced", _dec(cmp_["embedded_fee_pct"]), []
        else:
            pricing, fee, missing = "not_priced", None, cmp_["missing"]
        reasons = alert_reasons(settings=settings, t=t, edges=inp["edges"], pricing_status=pricing, fee=fee)
        on, basis = issued(t)
        v = inp["v"]
        snap = {"verdict_label": N.VERDICT_LABELS[v["verdict"]], "reasons": _loads(v["reasons"]) or [],
                "review_reasons": _loads(v["review_reasons"]) or [], "needs_review": v["needs_review"],
                "is_preliminary": v["is_preliminary"], "rule_version": v["rule_version"],
                "terms_status": t["terms_status"], "extraction_confidence": t["extraction_confidence"]}
        us = N.underlying_state(inp["edges"])
        async with pool.acquire() as conn:
            try:
                async with conn.transaction():
                    cur = await conn.fetchrow(
                        f"SELECT id, note_terms_id, input_key FROM {T_CHECK} WHERE org_id = $1::uuid AND "
                        "global_security_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL FOR UPDATE",
                        str(org_id), str(t["global_security_id"]))
                    if cur and str(cur["note_terms_id"]) == str(t["id"]) and cur["input_key"] == inp["key"]:
                        return {"org_id": str(org_id), "action": "unchanged", "check_id": str(cur["id"])}
                    if cur:
                        await conn.execute(f"UPDATE {T_CHECK} SET valid_to = clock_timestamp() WHERE id = $1::uuid",
                                           cur["id"])
                    row = await conn.fetchrow(
                        f"""INSERT INTO {T_CHECK} (org_id, global_security_id, note_terms_id, replicability_id,
                                input_key, trigger, issued_on, issued_basis, verdict, mapping, verdict_snapshot,
                                underlying_state, replica_outcome, not_built_reasons, replica_id, replica_notes,
                                comparison_id, pricing_status, embedded_fee_pct, missing, alert_reasons, checked_at,
                                valid_from, system_from)
                            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8, $9, $10, $11::jsonb, $12,
                                    $13, $14::jsonb, $15::uuid, $16::jsonb, $17::uuid, $18, $19::numeric, $20::jsonb,
                                    $21::jsonb, clock_timestamp(), clock_timestamp(), clock_timestamp())
                            RETURNING *""",
                        str(org_id), str(t["global_security_id"]), str(t["id"]), str(v["id"]), inp["key"], trigger,
                        on, basis, v["verdict"], v["mapping"], _dumps(snap), us["state"], rep["outcome"],
                        _dumps(rep["reasons"]), rep["replica_id"], _dumps(rep["notes"]),
                        cmp_["id"] if cmp_ else None, pricing, fee, _dumps(missing), _dumps(reasons))
                    await _audit(conn, org_id, None, "checked", row["id"],
                                 {"note": str(t["global_security_id"]), "terms": str(t["id"]), "trigger": trigger,
                                  "verdict": v["verdict"], "replica_outcome": rep["outcome"], "pricing": pricing,
                                  "superseded": str(cur["id"]) if cur else None})
            except asyncpg.UniqueViolationError:
                return {"org_id": str(org_id), "action": "unchanged", "check_id": None,
                        "note": "a concurrent check wrote it first"}
        check_id = str(row["id"])
        task_id = None
        if reasons:
            task_id = await _open_alert(pool, org_id, t=t, check_id=check_id, reasons=reasons)
            if task_id:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await _update(conn, check_id, {"alert_task_id": task_id})
        return {"org_id": str(org_id), "action": "checked", "check_id": check_id, "verdict": v["verdict"],
                "replica_outcome": rep["outcome"], "replica_id": rep["replica_id"], "pricing_status": pricing,
                "embedded_fee_pct": OS.dstr(fee), "missing": missing, "alert_reasons": reasons,
                "alert_task_id": task_id, "superseded": str(cur["id"]) if cur else None}


async def check_terms(pool, terms_id, *, trigger: str, orgs=None, include_test_orgs: bool = False) -> dict:
    """Check one terms version for every org that uses replication (or the named ones). Idempotent."""
    if trigger not in TRIGGER_LABELS:
        raise CheckError(f"trigger is one of {list(TRIGGER_LABELS)}")
    inp = await _platform_inputs(pool, terms_id)
    if inp is None:
        return {"terms_id": str(terms_id), "action": "skipped",
                "reason": "not the note's current live terms (closed, or superseded by a later status)", "orgs": []}
    targets = await check_orgs(pool, include_test_orgs=include_test_orgs, only=orgs)
    out = {"terms_id": str(terms_id), "note_id": str(inp["t"]["global_security_id"]), "action": "checked",
           "orgs": [], "errors": []}
    for org in targets:
        try:
            out["orgs"].append(await check_org(pool, org, inp, trigger=trigger))
        except Exception as exc:  # noqa: BLE001 — one org's failure never stops another's check
            log.exception("note check failed for org %s terms %s", org, terms_id)
            out["errors"].append({"org_id": org, "error": f"{type(exc).__name__}: {exc}"})
    return out


async def on_terms_written(pool, note_terms_id, *, source: str = "extraction") -> dict:
    """THE HOOK: a new or restated live terms version exists. ``pool`` may be a plain asyncpg pool (the
    extraction script's); the check always runs on the app's RLS pool, in each org's own context."""
    rls = await get_pool()
    return await check_terms(rls, note_terms_id, trigger=source, orgs=HOOK_ONLY_ORGS,
                             include_test_orgs=HOOK_INCLUDES_TEST_ORGS)


async def recheck_notes(pool, note_ids, *, trigger: str = "underlying_confirmed", orgs=None,
                        include_test_orgs: bool | None = None) -> list[dict]:
    """Re-check the CURRENT terms of these notes (after an underlying is confirmed or a proposal rejected)."""
    inc = HOOK_INCLUDES_TEST_ORGS if include_test_orgs is None else include_test_orgs
    orgs = HOOK_ONLY_ORGS if orgs is None else orgs
    ids = sorted({str(x) for x in note_ids if x})
    if not ids:
        return []
    with _Ctx(None, True):
        async with pool.acquire() as conn:
            cur = N.current_of(await N.live_terms(conn, note_ids=ids))
    return [await check_terms(pool, t["id"], trigger=trigger, orgs=orgs, include_test_orgs=inc) for t in cur]


async def sweep(pool=None, *, include_test_orgs: bool = False, orgs=None, note_ids=None) -> dict:
    """Catch anything the hook missed: every note's CURRENT live terms whose live check (per org) is absent or
    stale; then the alert sweep. Idempotent — a second run writes nothing. ``note_ids`` narrows it to those notes
    (a verify's fixtures; production never passes it)."""
    pool = pool or await get_pool()
    targets = await check_orgs(pool, include_test_orgs=include_test_orgs, only=orgs)
    out = {"orgs": len(targets), "checked": 0, "unchanged": 0, "skipped": 0, "errors": [], "alerts": 0}
    if not targets:
        return out
    with _Ctx(None, True):
        async with pool.acquire() as conn:
            terms = N.current_of(await N.live_terms(conn, note_ids=note_ids))
            unds = await N.underlyings_of(conn, {t["global_security_id"] for t in terms})
            reads = await N.readings_of(conn, {t["reference_filing_id"] for t in terms})
            live = {(str(r["org_id"]), str(r["global_security_id"])): (str(r["note_terms_id"]), r["input_key"])
                    for r in await conn.fetch(f"SELECT org_id, global_security_id, note_terms_id, input_key FROM "
                                              f"{T_CHECK} WHERE valid_to IS NULL AND system_to IS NULL AND "
                                              "org_id = ANY($1::uuid[])", targets)}
    for t in terms:
        edges = unds.get(str(t["global_security_id"]), [])
        rd = reads.get(str(t["reference_filing_id"])) if t["reference_filing_id"] else None
        key = input_key(t, edges, rd)
        stale = [o for o in targets if live.get((o, str(t["global_security_id"]))) != (str(t["id"]), key)]
        if not stale:
            out["unchanged"] += len(targets)
            continue
        res = await check_terms(pool, t["id"], trigger="sweep", orgs=stale, include_test_orgs=include_test_orgs)
        if res["action"] == "skipped":
            out["skipped"] += 1
        for o in res.get("orgs", []):
            out["checked" if o["action"] == "checked" else "unchanged"] += 1
        out["errors"] += res.get("errors", [])
        out["unchanged"] += len(targets) - len(stale)
    for org in targets:
        try:
            out["alerts"] += await alert_sweep(pool, org)
        except Exception as exc:  # noqa: BLE001
            out["errors"].append({"org_id": org, "error": f"alert sweep: {type(exc).__name__}: {exc}"})
    return out


async def alert_sweep(pool, org_id) -> int:
    """A live check whose replica was priced LATER (a person entered the quotes and priced it) above the threshold,
    and that has not alerted yet: alert now. Never a second alert for one check."""
    n = 0
    with _Ctx(org_id, False):
        async with pool.acquire() as conn:
            settings = await settings_of(conn, org_id)
            thr = settings.get("fee_alert_threshold_pct")
            if thr is None:
                return 0
            rows = await conn.fetch(
                f"""SELECT c.id, c.global_security_id, c.alert_reasons, cmp.embedded_fee_pct, cmp.id AS cmp_id
                    FROM {T_CHECK} c
                    JOIN LATERAL (SELECT * FROM {N.T_CMP} x WHERE x.replica_id = c.replica_id AND x.org_id = c.org_id
                                  AND x.status = 'priced' ORDER BY x.created_at DESC LIMIT 1) cmp ON true
                    WHERE c.org_id = $1::uuid AND c.valid_to IS NULL AND c.system_to IS NULL AND c.status = 'new'
                      AND c.alert_task_id IS NULL AND cmp.embedded_fee_pct > $2::numeric""", str(org_id), thr)
        for r in rows:
            with _Ctx(None, True):
                async with pool.acquire() as conn:
                    t = (await N.live_terms(conn, note_ids=[r["global_security_id"]]))
            if not t:
                continue
            t = N.current_of(t)[0]
            reasons = (_loads(r["alert_reasons"]) or []) + [{
                "code": "fee_threshold", "text": f"Embedded-fee estimate {OS.dstr(_dec(r['embedded_fee_pct']))} per "
                                                 f"100 is above the threshold of {OS.dstr(thr)} — {N.ESTIMATE} (priced "
                                                 "after the check)"}]
            task_id = await _open_alert(pool, org_id, t=t, check_id=str(r["id"]), reasons=reasons)
            if task_id:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await _update(conn, r["id"], {"alert_task_id": task_id, "alert_reasons": _dumps(reasons)})
                n += 1
    return n


# ═══ Review / dismiss (a person's decision, recorded) ══════════════════════
async def _apply_review(pool, *, org_id, check_id, status: str, actor_id, note: str | None) -> dict:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(f"SELECT * FROM {T_CHECK} WHERE id = $1::uuid AND org_id = $2::uuid AND "
                                      "system_to IS NULL FOR UPDATE", str(check_id), str(org_id))
            if row is None:
                raise CheckNotFound("check not found")
            if row["status"] == status and str(row["reviewed_by"]) == str(actor_id):
                return dict(row)            # idempotent re-apply of the same decision
            if row["status"] != "new":
                raise CheckConflict(f"the check is already {row['status']}")
            at = await conn.fetchval("SELECT clock_timestamp()")
            new = await _update(conn, check_id, {"status": status, "reviewed_by": str(actor_id),
                                                 "reviewed_at": at, "review_note": (note or "").strip() or None})
            await _audit(conn, org_id, actor_id, f"check_{status}", check_id, {"note": note})
    return new


async def review(pool, *, org_id, actor_id, check_id, outcome: str, note: str | None = None) -> dict:
    """Mark a check reviewed or dismissed (dismissing needs a note). With an open alert task, the decision goes
    through the human-task engine (the task records who decided) and its on_complete applies it; otherwise it is
    applied directly, with the user and an audit row."""
    if outcome not in REVIEW_OUTCOMES:
        raise CheckError(f"outcome is one of {list(REVIEW_OUTCOMES)}")
    if outcome == "dismissed" and not (note or "").strip():
        raise CheckError("a dismissal needs a note saying why")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        row = await conn.fetchrow(f"SELECT * FROM {T_CHECK} WHERE id = $1::uuid AND org_id = $2::uuid AND "
                                  "system_to IS NULL", _uuid(check_id), str(org_id))
        if row is None:
            raise CheckNotFound("check not found")
        if row["status"] != "new":
            raise CheckConflict(f"the check is already {row['status']}")
        task = None
        if row["alert_task_id"]:
            try:
                task = await HT.get_task(conn, org_id, row["alert_task_id"])
            except HT.HumanTaskNotFound:
                task = None
    if task and task["status"] == "open":
        done = await HT.complete_task(pool, org_id=org_id, task_id=task["id"], actor_id=actor["id"],
                                      outcome=REVIEW_OUTCOMES[outcome], note=note)
        sub = done.get("subject_result") or {}
        if str(sub.get("id") or "") == str(row["id"]):
            return sub
    return check_out(await _apply_review(pool, org_id=org_id, check_id=row["id"], status=outcome,
                                         actor_id=actor["id"], note=note))


async def _on_alert_complete(pool, task, outcome, actor_id, note):
    """The alert task decided: reviewed ('done') or dismissed ('reject'), applied to the check it was opened for
    (or, if that check was superseded, the note's live check). Idempotent."""
    status = "reviewed" if outcome == "done" else "dismissed"
    org_id = task["org_id"]
    async with pool.acquire() as conn:
        target = (task.get("payload") or {}).get("check_id")
        row = await conn.fetchrow(f"SELECT id, valid_to FROM {T_CHECK} WHERE id = $1::uuid AND org_id = $2::uuid AND "
                                  "system_to IS NULL", str(target), str(org_id)) if target else None
        if row is None or row["valid_to"] is not None:
            row = await conn.fetchrow(f"SELECT id, valid_to FROM {T_CHECK} WHERE org_id = $1::uuid AND "
                                      "global_security_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL",
                                      str(org_id), str(task["subject_id"]))
    if row is None:
        return None
    try:
        return check_out(await _apply_review(pool, org_id=org_id, check_id=row["id"], status=status,
                                             actor_id=actor_id, note=note))
    except CheckConflict:
        return None


HT.register_kind(TASK_ALERT, label="New-issue note alert", outcomes=("done", "reject"), maker_checker=False,
                 on_complete=_on_alert_complete, system_open=True)


def _uuid(v) -> str:
    import uuid as _u

    try:
        return str(_u.UUID(str(v)))
    except (TypeError, ValueError):
        raise CheckNotFound("check not found") from None


# ═══ Reading: the feed ═════════════════════════════════════════════════════
def check_out(c: dict) -> dict:
    snap = _loads(c["verdict_snapshot"]) or {}
    return {"id": str(c["id"]), "global_security_id": str(c["global_security_id"]),
            "note_terms_id": str(c["note_terms_id"]), "trigger": c["trigger"],
            "trigger_label": TRIGGER_LABELS[c["trigger"]], "issued_on": _iso(c["issued_on"]),
            "issued_basis": c["issued_basis"], "issued_basis_label": ISSUED_BASIS_LABELS[c["issued_basis"]],
            "verdict": c["verdict"], "verdict_label": N.VERDICT_LABELS[c["verdict"]], "mapping": c["mapping"],
            "mapping_label": N.MAPPING_LABELS.get(c["mapping"]) if c["mapping"] else None,
            "verdict_snapshot": snap, "underlying_state": c["underlying_state"],
            "underlying_state_label": UNDERLYING_LABELS[c["underlying_state"]],
            "replica_outcome": c["replica_outcome"], "replica_outcome_label": OUTCOME_LABELS[c["replica_outcome"]],
            "not_built_reasons": _loads(c["not_built_reasons"]) or [], "replica_notes": _loads(c["replica_notes"]) or [],
            "replica_id": str(c["replica_id"]) if c["replica_id"] else None,
            "comparison_id": str(c["comparison_id"]) if c["comparison_id"] else None,
            "pricing_status": c["pricing_status"], "pricing_label": PRICING_LABELS[c["pricing_status"]],
            "embedded_fee_pct": OS.dstr(_dec(c["embedded_fee_pct"])), "missing": _loads(c["missing"]) or [],
            "alert_reasons": _loads(c["alert_reasons"]) or [],
            "alert_task_id": str(c["alert_task_id"]) if c["alert_task_id"] else None,
            "status": c["status"], "status_label": STATUS_LABELS[c["status"]],
            "reviewed_by": str(c["reviewed_by"]) if c["reviewed_by"] else None, "reviewed_at": _iso(c["reviewed_at"]),
            "review_note": c["review_note"], "checked_at": _iso(c["checked_at"]),
            "is_live": c["valid_to"] is None}


def _not_priced_text(missing: list, pricing_status: str) -> str:
    if pricing_status == "no_replica":
        return "not priced — no replica (see why)"
    return f"{N.NOT_PRICED} — needs " + "; ".join(missing) if missing else N.NOT_PRICED


async def feed(conn, org_id, *, verdict: str | None = None, archetype: str | None = None, issuer: str | None = None,
               underlying: str | None = None, date_from: date | None = None, date_to: date | None = None,
               status: str | None = None, sort: str = "newest") -> list[dict]:
    """The org's live checks — "New issues". Newest first (issue date, then check time) or by the embedded-fee
    estimate (priced only, highest first; unpriced last, each saying what it needs). The figure is the replica's
    LATEST comparison (a person's later pricing included), labelled an estimate."""
    if sort not in SORTS:
        raise CheckError(f"sort is one of {list(SORTS)}")
    if status and status not in STATUS_LABELS:
        raise CheckError(f"status is one of {list(STATUS_LABELS)}")
    if verdict and verdict not in N.VERDICT_LABELS:
        raise CheckError(f"verdict is one of {list(N.VERDICT_LABELS)}")
    if archetype and archetype not in N.ARCHETYPE_LABELS:
        raise CheckError(f"archetype is one of {list(N.ARCHETYPE_LABELS)}")
    args: list = [str(org_id)]
    sql = f"SELECT * FROM {T_CHECK} WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL"
    if verdict:
        args.append(verdict)
        sql += f" AND verdict = ${len(args)}"
    if status:
        args.append(status)
        sql += f" AND status = ${len(args)}"
    if date_from:
        args.append(date_from)
        sql += f" AND issued_on >= ${len(args)}::date"
    if date_to:
        args.append(date_to)
        sql += f" AND issued_on <= ${len(args)}::date"
    checks = [dict(r) for r in await conn.fetch(sql, *args)]
    if not checks:
        return []
    note_ids = [str(c["global_security_id"]) for c in checks]
    terms = {str(t["id"]): t for t in await N.live_terms(conn, terms_ids=[str(c["note_terms_id"]) for c in checks])}
    unds = await N.underlyings_of(conn, note_ids)
    out = []
    for c in checks:
        t = terms.get(str(c["note_terms_id"]))
        if t is None:
            continue           # the terms version was closed with no successor yet — the sweep re-checks
        if archetype and t["product_archetype"] != archetype:
            continue
        if issuer and issuer.lower() not in (f"{t.get('filer_name') or ''} {t.get('issuer_group') or ''} "
                                             f"{t.get('credit_entity') or ''}").lower():
            continue
        edges = unds.get(str(c["global_security_id"]), [])
        und = underlying_text(edges)
        if underlying and underlying.lower() not in und.lower():
            continue
        rec = check_out(c)
        latest = N.comparison_out(await N.latest_comparison(conn, org_id, c["replica_id"])) if c["replica_id"] else None
        fee = _dec(latest["embedded_fee_pct"]) if latest and latest["status"] == "priced" else None
        rec.update({
            "note": {"id": str(t["global_security_id"]), "name": t["security_name"], "cusip": t.get("cusip"),
                     "issuer": t.get("credit_entity") or t.get("filer_name"), "issuer_group": t.get("issuer_group"),
                     "product_archetype": t["product_archetype"],
                     "archetype_label": N.ARCHETYPE_LABELS.get(t["product_archetype"], t["product_archetype"]),
                     "terms_status": t["terms_status"], "filing_url": t.get("source_url"),
                     "link": f"/portfolio/replication/notes/{t['global_security_id']}"},
            "underlyings": und,
            "estimate": {"priced": fee is not None, "embedded_fee_pct": OS.dstr(fee), "label": N.ESTIMATE,
                         "as_of": latest["as_of"] if latest else None,
                         "source": latest.get("computed_source") if latest else None,
                         "text": (f"{OS.dstr(fee)} per 100 — {N.ESTIMATE}" if fee is not None else
                                  _not_priced_text((latest or {}).get("missing") or rec["missing"],
                                                   rec["pricing_status"])),
                         "missing": [] if fee is not None else ((latest or {}).get("missing") or rec["missing"])},
            "replica_link": f"/portfolio/replication/notes/replicas/{c['replica_id']}" if c["replica_id"] else None})
        out.append(rec)
    if sort == "fee":
        out.sort(key=lambda r: (0 if r["estimate"]["priced"] else 1,
                                -(_dec(r["estimate"]["embedded_fee_pct"]) or Decimal(0)),
                                -(r["issued_on"] and date.fromisoformat(r["issued_on"]).toordinal() or 0)))
    else:
        out.sort(key=lambda r: (r["issued_on"] or "", r["checked_at"] or ""), reverse=True)
    return out


async def check_history(conn, org_id, note_id) -> list[dict]:
    rows = await conn.fetch(f"SELECT * FROM {T_CHECK} WHERE org_id = $1::uuid AND global_security_id = $2::uuid AND "
                            "system_to IS NULL ORDER BY valid_from DESC", str(org_id), str(note_id))
    return [check_out(dict(r)) for r in rows]


# ═══ The underlying queue (Super Admin) ════════════════════════════════════
async def underlying_queue(conn) -> dict:
    """Edges awaiting a person, grouped by what the click can do: ONE registry proposal (an 'ambiguous' edge
    carries exactly one proposed target — confirm in one click) vs none (open the manual match). Counts per state
    across every live underlying edge, resolved included."""
    from services.underlying_resolution import load_queue

    counts = {k: 0 for k in ("resolved", "ambiguous", "unresolved")}
    for r in await conn.fetch("SELECT link_state, count(*) AS n FROM portfolio.securities_global_relationships WHERE "
                              "relationship_type = 'underlying_of' AND valid_to IS NULL AND system_to IS NULL "
                              "GROUP BY 1"):
        counts[r["link_state"]] = int(r["n"])
    items = await load_queue(conn)
    groups = {"one_proposal": [], "manual": []}
    for it in items:
        one = it["link_state"] == "ambiguous" and bool((it.get("proposal") or {}).get("global_security_id"))
        it["group"] = "one_proposal" if one else "manual"
        it["actions"] = ["confirm", "reject"] if one else ["manual_match"]
        it["note"]["link"] = f"/portfolio/replication/notes/{it['note']['global_security_id']}"
        groups[it["group"]].append(it)
    return {"counts": counts, "groups": groups,
            "group_counts": {k: len(v) for k, v in groups.items()},
            "manual_match": {"path": "/api/v1/admin/pricing/underlying-queue/{relationship_id}/confirm",
                             "text": "A manual match names the security (global_security_id) or creates a new one "
                                     "(create_new) on the existing pricing-admin confirm route — still "
                                     "confirm_resolution."}}


async def confirm_edges(pool, *, actor_id, is_super_admin: bool, relationship_ids: list) -> dict:
    """Confirm each one-proposal edge as its standing proposal — ONE confirm_resolution call per edge, recorded as
    that Super Admin's own confirmation (resolved_by). An edge without exactly one proposal is refused (open the
    manual match). Each edge stands alone: a refusal never undoes another's confirmation. Then the affected notes
    are re-checked."""
    from services.underlying_resolution import UnderlyingResolutionError, confirm_resolution

    if not is_super_admin:
        raise PermissionError("Super Admin required")
    ids = []
    for x in relationship_ids or []:
        sid = _uuid(x)
        if sid not in ids:
            ids.append(sid)
    if not ids:
        raise CheckError("choose at least one edge")
    if len(ids) > 200:
        raise CheckError("at most 200 edges in one action")
    results, notes = [], set()
    for rid in ids:
        async with pool.acquire() as conn:
            e = await conn.fetchrow("SELECT id, link_state, proposed_global_security_id, from_global_security_id FROM "
                                    "portfolio.securities_global_relationships WHERE id = $1::uuid AND valid_to IS "
                                    "NULL AND system_to IS NULL", rid)
        if e is None:
            results.append({"relationship_id": rid, "ok": False, "error": "edge not found"})
            continue
        if e["link_state"] != "ambiguous" or e["proposed_global_security_id"] is None:
            results.append({"relationship_id": rid, "ok": False,
                            "error": f"not a one-proposal edge ({e['link_state']}) — open the manual match"})
            continue
        try:
            res = await confirm_resolution(pool, rid, actor_id=str(actor_id), is_super_admin=True)
        except UnderlyingResolutionError as exc:
            results.append({"relationship_id": rid, "ok": False, "error": str(exc)})
            continue
        results.append({"relationship_id": rid, "ok": True, **res})
        notes.add(str(e["from_global_security_id"]))
    rechecked = await recheck_notes(pool, notes, trigger="underlying_confirmed")
    return {"results": results, "confirmed": sum(1 for r in results if r["ok"]), "rechecked": rechecked}


async def reject_edge(pool, *, actor_id, is_super_admin: bool, relationship_id) -> dict:
    from services.underlying_resolution import reject_proposal

    if not is_super_admin:
        raise PermissionError("Super Admin required")
    rid = _uuid(relationship_id)
    async with pool.acquire() as conn:
        note = await conn.fetchval("SELECT from_global_security_id FROM portfolio.securities_global_relationships "
                                   "WHERE id = $1::uuid AND valid_to IS NULL AND system_to IS NULL", rid)
    res = await reject_proposal(pool, rid, actor_id=str(actor_id), is_super_admin=True)
    rechecked = await recheck_notes(pool, [note], trigger="underlying_confirmed") if note else []
    return {"result": res, "rechecked": rechecked}


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"verdicts": pairs(N.VERDICT_LABELS), "archetypes": pairs(N.ARCHETYPE_LABELS),
            "statuses": pairs(STATUS_LABELS), "triggers": pairs(TRIGGER_LABELS),
            "replica_outcomes": pairs(OUTCOME_LABELS), "pricing_statuses": pairs(PRICING_LABELS),
            "underlying_states": pairs(UNDERLYING_LABELS), "alert_reasons": pairs(ALERT_LABELS),
            "sorts": pairs(SORTS), "issued_basis": pairs(ISSUED_BASIS_LABELS),
            "queue_groups": pairs(QUEUE_GROUP_LABELS), "review_outcomes": pairs({"reviewed": "Mark reviewed",
                                                                                 "dismissed": "Dismiss"}),
            "estimate_label": N.ESTIMATE, "not_priced": N.NOT_PRICED,
            "new_issue_rule": "A new issue is dated by its 424B2 filing date (or, with no filing, the day its terms "
                              "were first extracted).",
            "rules": ["No underlying is ever confirmed automatically: a Super Admin confirms each one.",
                      "A replica the check prepares is a DRAFT: a person completes and submits it, and a second "
                      "person approves it.",
                      "The embedded-fee figure is an estimate, and only where the note's price and every leg are "
                      "quoted."]}
