"""Security master (SECMASTER1) — the Super Admin screens' API. Every route's gate: Super Admin
(require_super_admin), checked FIRST, before anything is read.

    GET  /admin/security-data/master/status                     counts by type / source, last run per job, review +
                                                                13(f) unmapped counts
    GET  /admin/security-data/master/jobs                       the platform jobs (schedule, last run, steps)
    POST /admin/security-data/master/jobs/{job_key}/run-now     start a job now (same engine path as the scheduler)
    POST /admin/security-data/master/runs/{run_id}/close        close a HELD platform-job run so its schedule resumes
    POST /admin/security-data/master/jobs/{job_key}/schedule    BUGFIX4: switch the job's schedule on / off (audited)
    GET  /admin/security-data/master/summary                    SECMASTER2: what the master holds — per type /
                                                                identifier / provider / field / price coverage,
                                                                review queue, last run per job; each count with
                                                                the filter that lists it (one query per section)
    GET  /admin/security-data/master/securities                 SECMASTER2: the filtered list behind a count
    GET  /admin/security-data/master/review                     the review queue
    POST /admin/security-data/master/review/{review_id}/decision  LEGACY resolve / dismiss (records the decision;
                                                                changes NO security). The screen uses the choices below.
    POST /admin/security-data/master/review/run                 REVIEWQ1: the one Run — {choices: [{id, choice}]} with
                                                                choice use_incoming / use_existing / keep_both; each item
                                                                its own savepoint, refusals listed; audited
    POST /admin/security-data/master/review/bulk/estimate       REVIEWQ1: how many OPEN items a filter selects and how
                                                                many of them offer the choice (writes nothing)
    POST /admin/security-data/master/review/bulk                REVIEWQ1: one choice for every item the filter selects
                                                                (re-evaluated here; must equal the confirmed count)
    POST /admin/security-data/master/review/{review_id}/move-back  REVIEWQ1: a Known discrepancy back to the queue
                                                                (note required; audited)
    GET  /admin/security-data/master/tracked                    TRACK1: the Tracked view — named lists, progress
                                                                ("1,240 of 2,700 loaded"), filter vocabularies, caps
    POST /admin/security-data/master/tracked/estimate           TRACK1: count + calls / days for ids | filter | list
    POST /admin/security-data/master/tracked/track              TRACK1: track ids | filter (re-evaluated here, must
                                                                equal the confirmed count) | a named list; audited
    POST /admin/security-data/master/tracked/untrack            TRACK1: untrack ids | filter (a note is required)

Envelopes: permissions (from the server, no client default) + vocabularies (labels). Org ids never come from a
request: the platform org is a constant (services.security_master_jobs.PLATFORM_ORG_ID)."""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from services import disclosures as DL
from services import security_master_jobs as J
from services import security_master_load as M
from services import security_master_review as RQ
from services import security_master_summary as SUM
from services.authz import require_super_admin
from services.database import get_pool, platform_scope

router = APIRouter(tags=["security-master"])
_P = "/admin/security-data/master"
OUTCOMES = {"resolved": "Resolved", "dismissed": "Dismissed"}
REVIEW_STATUSES = {"open": "Open", "resolved": "Resolved", "dismissed": "Dismissed",
                   # REVIEWQ1 — the three choices' statuses; 'known' = kept_existing + kept_both
                   "used_incoming": "Used incoming", "kept_existing": "Kept existing", "kept_both": "Kept both",
                   "known": "Known discrepancies"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DecisionIn(_Body):
    outcome: str
    note: str = Field(min_length=1, max_length=2000)


class ChoiceItemIn(_Body):
    id: UUID
    choice: str = Field(max_length=20)


class RunIn(_Body):
    """REVIEWQ1 — the one Run. Never an org id (the review queue is platform scope)."""
    choices: list[ChoiceItemIn] = Field(min_length=1, max_length=RQ.BULK_CAP)
    note: str | None = Field(default=None, max_length=RQ.NOTE_MAX)


class BulkEstimateIn(_Body):
    filters: dict[str, str]
    choice: str = Field(max_length=20)


class BulkIn(BulkEstimateIn):
    expected_total: int = Field(ge=0)
    note: str | None = Field(default=None, max_length=RQ.NOTE_MAX)


class MoveBackIn(_Body):
    note: str = Field(min_length=1, max_length=RQ.NOTE_MAX)


class ScheduleIn(_Body):
    active: bool


class CloseIn(_Body):
    note: str = Field(min_length=1, max_length=2000)


class TrackSelectionIn(_Body):
    """TRACK1 — exactly ONE of ids / filters / list_key. Never an org id (tracked securities are platform-wide)."""
    ids: list[str] | None = Field(default=None, max_length=3000)
    filters: dict[str, str] | None = None
    list_key: str | None = Field(default=None, max_length=63)


class TrackIn(TrackSelectionIn):
    note: str | None = Field(default=None, max_length=2000)
    expected_total: int | None = Field(default=None, ge=0)


class EstimateIn(TrackSelectionIn):
    removing: bool = False


def _envelope(principal, body: dict) -> dict:
    su = bool(principal.is_super_admin)
    perms = {"can_read": su, "can_write": su, "can_run": su, "can_decide": su, "can_close_held": su,
             # REVIEWQ1 — the per-row choice + Run, bulk by filter, move back
             "can_choose": su, "can_bulk": su, "can_move_back": su,
             "is_super_admin": su, "read_permission": "super_admin", "write_permission": "super_admin"}
    vocab = {"review_kinds": [{"key": k, "label": M.REVIEW_LABELS[k]} for k in M.REVIEW_KINDS],
             "review_statuses": [{"key": k, "label": v} for k, v in REVIEW_STATUSES.items()],
             "outcomes": [{"key": k, "label": v} for k, v in OUTCOMES.items()] if su else [],
             # REVIEWQ1 — the choices (labels from here, never the client); empty for anyone without the write
             "choices": [{"key": k, "label": RQ.CHOICE_LABELS[k]} for k in RQ.CHOICES] if su else [],
             "review_filters": list(RQ.FILTERS), "bulk_cap": RQ.BULK_CAP, "note_max": RQ.NOTE_MAX,
             "editable": ["outcome", "note"] if su else [], "inline_editable": []}
    return jsonable_encoder({**body, "permissions": perms, "vocabularies": vocab})


@router.get(f"{_P}/status")
async def master_status(request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with platform_scope(conn):
            st = await J.status(conn)
    return _envelope(principal, {"status": st})


@router.get(f"{_P}/summary")
async def master_summary(request: Request):
    """SECMASTER2 — the load summary (services.security_master_summary). Super Admin, checked first. PRICES2 adds
    ``price_refresh`` (prices.refresh_status)."""
    principal = require_super_admin(request)
    pool = await get_pool()
    from services.security_data import prices as P

    async with pool.acquire() as conn:
        async with platform_scope(conn):
            out = await SUM.summary(conn)
        # PRICES2 — "covered N · refreshed today M · oldest price date D" + the latest day rows of the price refresh
        price_refresh = await P.refresh_status(conn)
    return _envelope(principal, {"summary": out, "list_filters": list(SUM.FILTERS), "price_refresh": price_refresh})


def _check_filters(filters: dict) -> dict:
    """The master list's filters, each checked against its allowed values (422 names the filter, never the value).
    TRACK1 adds q (2–100 chars, dailyops1's search), exchange / category (free values, ≤ 200 chars — they are bound
    parameters), tracked (true / false), in_list (a list key)."""
    from services import securities_global as SG

    for k, allowed in (("security_type", SG.SECURITY_TYPES),
                       ("id_type", SG.IDENTIFIER_TYPES), ("missing_id_type", SG.IDENTIFIER_TYPES),
                       ("field", M.REFERENCE_FIELDS), ("missing_field", M.REFERENCE_FIELDS),
                       ("price_coverage", SG.PRICE_COVERAGES),
                       ("fund_structure", ("mutual_fund", "money_market", "etf", SUM.NOT_STATED)),
                       ("has_price", ("true", "false")), ("tracked", ("true", "false"))):
        if filters.get(k) is not None and filters[k] not in allowed:
            raise HTTPException(status_code=422, detail=f"{k}: not one of the allowed values")
    q = filters.get("q")
    if q is not None and not (2 <= len(q.strip()) <= 100 and all(ord(c) >= 32 for c in q)):
        raise HTTPException(status_code=422, detail="q: 2 to 100 characters, no control characters")
    for k in ("exchange", "category", "in_list"):
        v = filters.get(k)
        if v is not None and not (0 < len(v) <= 200 and all(ord(c) >= 32 for c in v)):
            raise HTTPException(status_code=422, detail=f"{k}: 1 to 200 characters, no control characters")
    for k in filters:
        if k not in SUM.FILTERS:
            raise HTTPException(status_code=422, detail=f"unknown filter (one of {', '.join(SUM.FILTERS)})")
    return filters


@router.get(f"{_P}/securities")
async def master_securities(request: Request, security_type: str | None = None, fund_structure: str | None = None,
                            id_type: str | None = None, missing_id_type: str | None = None,
                            provider: str | None = None, field: str | None = None, missing_field: str | None = None,
                            price_coverage: str | None = None, has_price: str | None = None, q: str | None = None,
                            exchange: str | None = None, category: str | None = None, tracked: str | None = None,
                            in_list: str | None = None, limit: int = 100, offset: int = 0):
    """SECMASTER2 — the securities behind a summary count (the SAME predicates the count used). Super Admin.
    TRACK1 — also the Tracked view's filtered list (q / exchange / category / tracked / in_list)."""
    principal = require_super_admin(request)
    filters = _check_filters({
        "security_type": security_type, "fund_structure": fund_structure, "id_type": id_type,
        "missing_id_type": missing_id_type, "provider": provider, "field": field, "missing_field": missing_field,
        "price_coverage": price_coverage, "has_price": has_price, "q": q, "exchange": exchange,
        "category": category, "tracked": tracked, "in_list": in_list})
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with platform_scope(conn):
            out = await SUM.securities(conn, filters=filters, limit=limit, offset=offset)
    return _envelope(principal, out)


@router.get(f"{_P}/jobs")
async def master_jobs(request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with platform_scope(conn):
            jobs = await J.job_rows(conn, J.platform_org())
            notice = await DL.resolve_safe(conn, "prices.internal_use_notice", org_id=principal.org_id)
    ordered = [jobs[k] for k in J.JOBS if k in jobs]
    return _envelope(principal, {"jobs": ordered, "missing": [k for k in J.JOBS if k not in jobs],
                                 "price_gap_note": notice["text"], "price_gap_disclosure": DL.public(notice),
                                 "actions": ["switch_schedule", "run_now"] if principal.is_super_admin else []})


@router.post(f"{_P}/jobs/{{job_key}}/schedule")
async def master_switch_schedule(request: Request, job_key: str, body: ScheduleIn):
    """BUGFIX4 — a Super Admin switches a platform job's schedule on or off; audited (audit_log
    security_master.schedule_switched). The sprint switched nothing on — the operator decides."""
    principal = require_super_admin(request)
    if job_key not in J.JOBS:
        raise HTTPException(status_code=404, detail=f"unknown platform job {job_key!r}")
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                out = await J.set_schedule(conn, job_key=job_key, active=body.active, actor_id=principal.user_id)
    except J.PlatformJobError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _envelope(principal, {"schedule": out})


async def _run_in_background(pool, job_key: str, actor_id) -> None:
    try:
        await J.run_now(pool, job_key=job_key, actor_id=actor_id)
    except Exception:  # noqa: BLE001 — a failing run is HELD and alerted by the engine; nothing to add here
        pass


@router.post(f"{_P}/jobs/{{job_key}}/run-now", status_code=202)
async def master_run_now(request: Request, job_key: str, background: BackgroundTasks):
    """Accepted at once; the run proceeds through workflow_engine.start_workflow_run (started_by = this Super
    Admin) and appears on the Runs screen — held and alerted like any run if a step fails."""
    principal = require_super_admin(request)
    if job_key not in J.JOBS:
        raise HTTPException(status_code=404, detail=f"unknown platform job {job_key!r}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with platform_scope(conn):
            jobs = await J.job_rows(conn)
    if job_key not in jobs:
        raise HTTPException(status_code=409, detail=f"{J.JOBS[job_key].name} is not seeded in the platform org "
                                                    "(scripts/_apply_secmaster1.py --seed-jobs)")
    background.add_task(_run_in_background, pool, job_key, principal.user_id)
    return _envelope(principal, {"accepted": True, "job": job_key, "name": J.JOBS[job_key].name})


@router.post(f"{_P}/runs/{{run_id}}/close")
async def master_close_held(request: Request, run_id: UUID, body: CloseIn):
    principal = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with platform_scope(conn):
            row = await J.close_held_run(conn, run_id=run_id, actor_id=principal.user_id, note=body.note)
    if row is None:
        raise HTTPException(status_code=409, detail="not a held run of a platform job — nothing closed")
    return _envelope(principal, {"run": row})


@router.get(f"{_P}/review")
async def master_review(request: Request, status: str = "open", kind: str | None = None, field: str | None = None,
                        provider: str | None = None, q: str | None = None, home_vs_us: str | None = None,
                        global_security_id: str | None = None, limit: int = 200, offset: int = 0):
    """The review queue (status open), the Known discrepancies (status known = Use existing + Keep both, with seen
    again / last seen) or any decided status (used_incoming + global_security_id = a security's history). REVIEWQ1
    filters: field / provider / q / home_vs_us (incoming ISIN not US, existing US). ``total`` is the filter's count;
    each open row carries the ``choices`` the SERVER offers it (+ ``choice_notes``: why one is not)."""
    principal = require_super_admin(request)
    if status not in REVIEW_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(REVIEW_STATUSES)}")
    if kind is not None and kind not in M.REVIEW_KINDS:
        raise HTTPException(status_code=422, detail="unknown review kind")
    su = bool(principal.is_super_admin)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                out = await RQ.list_items(conn, status=status, filters={
                    "kind": kind, "field": field, "provider": provider, "q": q, "home_vs_us": home_vs_us,
                    "global_security_id": global_security_id}, limit=limit, offset=offset, can_write=su)
                facets = await RQ.facets(conn, "known" if status == "known" else "open")
    except RQ.ReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for d in out["rows"]:
        # the legacy decision route's outcomes stay on an open row (verify_secmaster1); the screen uses ``choices``
        if d["status"] == "open":
            d["actions"] = list(OUTCOMES) if su else []
    env = _envelope(principal, {**out, "filters": {"status": status, "kind": kind, **out["filters"]}})
    env["vocabularies"]["facets"] = facets
    return env


def _review_error(exc: Exception):
    status = 409 if isinstance(exc, RQ.ReviewRefused) else 422
    raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post(f"{_P}/review/run")
async def master_review_run(request: Request, body: RunIn):
    """REVIEWQ1 — the one Run: every row the Super Admin gave a choice. Use incoming → the security takes the value as
    a new version (history kept); Use existing → nothing on the security; Keep both → the value as an alternate
    identifier. Each item in its own savepoint; a refusal (e.g. a ticker that would land on a second live security)
    leaves that item open and is listed with its reason. Audited per item."""
    principal = require_super_admin(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                out = await RQ.run(conn, [{"id": str(c.id), "choice": c.choice} for c in body.choices],
                                   actor_id=principal.user_id, note=body.note)
    except RQ.ReviewError as exc:
        _review_error(exc)
    return _envelope(principal, {"result": out})


@router.post(f"{_P}/review/bulk/estimate")
async def master_review_bulk_estimate(request: Request, body: BulkEstimateIn):
    """REVIEWQ1 — the count a bulk choice would touch (OPEN items the filter selects) and how many of them offer that
    choice. Writes nothing."""
    principal = require_super_admin(request)
    if body.choice not in RQ.CHOICES:
        raise HTTPException(status_code=422, detail=f"choice must be one of {', '.join(RQ.CHOICES)}")
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                out = await RQ.estimate(conn, dict(body.filters), body.choice)
    except RQ.ReviewError as exc:
        _review_error(exc)
    return _envelope(principal, out)


@router.post(f"{_P}/review/bulk")
async def master_review_bulk(request: Request, body: BulkIn):
    """REVIEWQ1 — one choice for every OPEN item the filter selects, re-evaluated here: it must equal
    ``expected_total`` (the count the Super Admin confirmed) or nothing is written. Same Run path, per item."""
    principal = require_super_admin(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                out = await RQ.bulk(conn, dict(body.filters), body.choice, expected_total=body.expected_total,
                                    actor_id=principal.user_id, note=body.note)
    except RQ.ReviewError as exc:
        _review_error(exc)
    return _envelope(principal, {"result": out})


@router.post(f"{_P}/review/{{review_id}}/move-back")
async def master_review_move_back(request: Request, review_id: UUID, body: MoveBackIn):
    """REVIEWQ1 — a Known discrepancy back to the queue (audited; a Keep both item's alternate is retired). The next
    source run treats it as open."""
    principal = require_super_admin(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                out = await RQ.move_back(conn, review_id, actor_id=principal.user_id, note=body.note)
    except RQ.ReviewError as exc:
        _review_error(exc)
    return _envelope(principal, {"result": out})


@router.post(f"{_P}/review/{{review_id}}/decision")
async def master_review_decision(request: Request, review_id: UUID, body: DecisionIn):
    """Valid-axis restatement: the open row is closed and the decided row inserted (with who, when, why). No
    security, identifier or attribute is changed by a decision."""
    principal = require_super_admin(request)
    if body.outcome not in OUTCOMES:
        raise HTTPException(status_code=422, detail=f"outcome must be one of {', '.join(OUTCOMES)}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with platform_scope(conn):
            cur = await conn.fetchrow(
                "SELECT * FROM portfolio.security_master_review WHERE id = $1 AND valid_to IS NULL AND "
                "system_to IS NULL FOR UPDATE", review_id)
            if cur is None:
                raise HTTPException(status_code=404, detail="review item not found")
            if cur["status"] != "open":
                raise HTTPException(status_code=409, detail=f"already {cur['status']}")
            await conn.execute("UPDATE portfolio.security_master_review SET valid_to = now() WHERE id = $1", review_id)
            new_id = await conn.fetchval(
                "INSERT INTO portfolio.security_master_review (kind, subject_key, field, incoming_value, "
                "existing_value, provider_key, global_security_id, detail, status, decided_by, decided_at, "
                "decision_note, run_id, created_at) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10::uuid, "
                "now(), $11, $12, $13) RETURNING id",
                cur["kind"], cur["subject_key"], cur["field"], cur["incoming_value"], cur["existing_value"],
                cur["provider_key"], cur["global_security_id"],
                cur["detail"] if isinstance(cur["detail"], str) else json.dumps(cur["detail"]), body.outcome,
                str(principal.user_id), body.note, cur["run_id"], cur["created_at"])
    return _envelope(principal, {"id": new_id, "status": body.outcome})


# ── TRACK1 — track any security ─────────────────────────────────────────────
def _tracked_envelope(principal, body: dict) -> dict:
    """The Tracked view's envelope: write controls only for a Super Admin (the gate already refused anyone else);
    the editable list is EMPTY for anyone without it — never a client default."""
    from services.security_data import tracking as TR

    su = bool(principal.is_super_admin)
    env = _envelope(principal, body)
    env["permissions"].update({"can_track": su, "can_untrack": su, "write_permission": "super_admin"})
    env["vocabularies"].update({"editable": ["track", "untrack", "note"] if su else [],
                                "track_cap": TR.TRACK_CAP, "untrack_cap": TR.UNTRACK_CAP,
                                "note_max": TR.NOTE_MAX, "filters": list(SUM.FILTERS)})
    return env


def _selection(body: TrackSelectionIn) -> str:
    given = [k for k in ("ids", "filters", "list_key") if getattr(body, k) not in (None, [], {})]
    if len(given) != 1:
        raise HTTPException(status_code=422, detail="give exactly one of ids, filters or list_key")
    if body.filters is not None:
        _check_filters(dict(body.filters))
    return given[0]


def _tracking_refusal(exc: Exception):
    from services.security_data import tracking as TR

    status = 409 if isinstance(exc, TR.TrackingRefused) else 422
    raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get(f"{_P}/tracked")
async def master_tracked(request: Request):
    """TRACK1 — the Tracked view: the named lists (members, tracked, as-of, misses), the progress line, the filter
    vocabularies the view offers (security types, exchanges, fund categories — the master's own values), the caps."""
    principal = require_super_admin(request)
    from services import securities_global as SG
    from services.security_data import tracking as TR

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with platform_scope(conn):
            lists = await TR.list_rows(conn)
            prog = await TR.progress(conn)
            vals = await conn.fetch(
                "SELECT field, value, count(*) AS n FROM portfolio.security_reference_attributes WHERE field IN "
                "('exchange', 'fund_category') AND valid_to IS NULL AND system_to IS NULL GROUP BY field, value "
                "ORDER BY field, n DESC, value")
    exchanges = [{"key": r["value"], "label": r["value"], "count": int(r["n"])} for r in vals if r["field"] == "exchange"]
    categories = [{"key": r["value"], "label": r["value"], "count": int(r["n"])} for r in vals
                  if r["field"] == "fund_category"]
    env = _tracked_envelope(principal, {"lists": lists, "progress": prog})
    env["vocabularies"].update({"security_types": [{"key": t, "label": t.replace("_", " ")} for t in
                                                   sorted(SG.SECURITY_TYPES)],
                                "exchanges": exchanges, "categories": categories,
                                "lists": [{"key": x["list_key"], "label": x["name"]} for x in lists],
                                "has_price": [{"key": "true", "label": "has prices"},
                                              {"key": "false", "label": "no prices"}],
                                "tracked": [{"key": "true", "label": "tracked"},
                                            {"key": "false", "label": "not tracked"}]})
    return env


async def _resolve_ids(conn, body: TrackSelectionIn, kind: str, *, cap: int) -> tuple[list[str] | None, int, dict]:
    from services.security_data import tracking as TR

    if kind == "ids":
        return list(body.ids), len(set(body.ids)), {}
    if kind == "list_key":
        ids = await TR.list_member_ids(conn, body.list_key)
        return ids, len(ids), {}
    sel = await TR.select_by_filter(conn, dict(body.filters), cap=cap)
    return sel["ids"], sel["total"], sel["filters"]


@router.post(f"{_P}/tracked/estimate")
async def master_tracked_estimate(request: Request, body: EstimateIn):
    """TRACK1 — the count a bulk action would touch and what it costs (calls, days at the daily budget, nights through
    the nightly alone, the daily upkeep). Over the cap: the count and ``over_cap: true`` — never the ids. Writes nothing."""
    principal = require_super_admin(request)
    from services.security_data import tracking as TR

    kind = _selection(body)
    cap = TR.UNTRACK_CAP if body.removing else TR.TRACK_CAP
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                ids, total, filters = await _resolve_ids(conn, body, kind, cap=cap)
                over = total > cap
                est = await TR.estimate(conn, ids, removing=body.removing) if (ids and not over) else None
    except TR.TrackingError as exc:
        _tracking_refusal(exc)
    return _tracked_envelope(principal, {"selection": kind, "total": total, "cap": cap, "over_cap": over,
                                         "filters": filters, "estimate": est})


@router.post(f"{_P}/tracked/track")
async def master_tracked_track(request: Request, body: TrackIn):
    """TRACK1 — track ids, every security a filter selects (re-evaluated here; must equal ``expected_total``), or a
    named list's members. Audited (audit_log security_data.tracked). Nothing is fetched here: the prices backfill and
    the nightly prices job load the history (reason 'tracked')."""
    principal = require_super_admin(request)
    from services.security_data import tracking as TR

    kind = _selection(body)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                if kind == "ids":
                    if body.expected_total is not None and body.expected_total != len(set(body.ids)):
                        raise TR.TrackingRefused("the selection changed — nothing was changed; confirm again")
                    out = await TR.track(conn, body.ids, actor_id=principal.user_id, note=body.note)
                elif kind == "filters":
                    out = await TR.track_by_filter(conn, dict(body.filters), actor_id=principal.user_id, note=body.note,
                                                   expected_total=body.expected_total)
                else:
                    out = await TR.track_list(conn, body.list_key, actor_id=principal.user_id, note=body.note,
                                              expected_total=body.expected_total)
                prog = await TR.progress(conn)
    except TR.TrackingError as exc:
        _tracking_refusal(exc)
    return _tracked_envelope(principal, {"result": out, "progress": prog})


@router.post(f"{_P}/tracked/untrack")
async def master_tracked_untrack(request: Request, body: TrackIn):
    """TRACK1 — untrack ids or a filter's selection (a note is required). Loaded prices stay (history kept)."""
    principal = require_super_admin(request)
    from services.security_data import tracking as TR

    kind = _selection(body)
    if kind == "list_key":
        raise HTTPException(status_code=422, detail="untrack by ids or by a filter (in_list selects a list's members)")
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                if kind == "ids":
                    if body.expected_total is not None and body.expected_total != len(set(body.ids)):
                        raise TR.TrackingRefused("the selection changed — nothing was changed; confirm again")
                    out = await TR.untrack(conn, body.ids, actor_id=principal.user_id, note=body.note)
                else:
                    out = await TR.untrack_by_filter(conn, dict(body.filters), actor_id=principal.user_id,
                                                     note=body.note, expected_total=body.expected_total)
                prog = await TR.progress(conn)
    except TR.TrackingError as exc:
        _tracking_refusal(exc)
    return _tracked_envelope(principal, {"result": out, "progress": prog})
