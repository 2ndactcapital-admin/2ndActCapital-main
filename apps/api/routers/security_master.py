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
    POST /admin/security-data/master/review/{review_id}/decision  resolve / dismiss an item (records the decision;
                                                                changes NO security — the fix is made on the security
                                                                itself, by a person)

Envelopes: permissions (from the server, no client default) + vocabularies (labels). Org ids never come from a
request: the platform org is a constant (services.security_master_jobs.PLATFORM_ORG_ID)."""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from services import security_master_jobs as J
from services import security_master_load as M
from services import security_master_summary as SUM
from services.authz import require_super_admin
from services.database import get_pool, platform_scope

router = APIRouter(tags=["security-master"])
_P = "/admin/security-data/master"
OUTCOMES = {"resolved": "Resolved", "dismissed": "Dismissed"}
REVIEW_STATUSES = {"open": "Open", "resolved": "Resolved", "dismissed": "Dismissed"}


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DecisionIn(_Body):
    outcome: str
    note: str = Field(min_length=1, max_length=2000)


class ScheduleIn(_Body):
    active: bool


class CloseIn(_Body):
    note: str = Field(min_length=1, max_length=2000)


def _envelope(principal, body: dict) -> dict:
    su = bool(principal.is_super_admin)
    perms = {"can_read": su, "can_write": su, "can_run": su, "can_decide": su, "can_close_held": su,
             "is_super_admin": su, "read_permission": "super_admin", "write_permission": "super_admin"}
    vocab = {"review_kinds": [{"key": k, "label": M.REVIEW_LABELS[k]} for k in M.REVIEW_KINDS],
             "review_statuses": [{"key": k, "label": v} for k, v in REVIEW_STATUSES.items()],
             "outcomes": [{"key": k, "label": v} for k, v in OUTCOMES.items()] if su else [],
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
    """SECMASTER2 — the load summary (services.security_master_summary). Super Admin, checked first."""
    principal = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with platform_scope(conn):
            out = await SUM.summary(conn)
    return _envelope(principal, {"summary": out, "list_filters": list(SUM.FILTERS)})


@router.get(f"{_P}/securities")
async def master_securities(request: Request, security_type: str | None = None, fund_structure: str | None = None,
                            id_type: str | None = None, missing_id_type: str | None = None,
                            provider: str | None = None, field: str | None = None, missing_field: str | None = None,
                            price_coverage: str | None = None, has_price: str | None = None, limit: int = 100,
                            offset: int = 0):
    """SECMASTER2 — the securities behind a summary count (the SAME predicates the count used). Super Admin."""
    principal = require_super_admin(request)
    from services import securities_global as SG

    filters = {"security_type": security_type, "fund_structure": fund_structure, "id_type": id_type,
               "missing_id_type": missing_id_type, "provider": provider, "field": field,
               "missing_field": missing_field, "price_coverage": price_coverage, "has_price": has_price}
    for k, allowed in (("security_type", SG.SECURITY_TYPES),
                       ("id_type", SG.IDENTIFIER_TYPES), ("missing_id_type", SG.IDENTIFIER_TYPES),
                       ("field", M.REFERENCE_FIELDS), ("missing_field", M.REFERENCE_FIELDS),
                       ("price_coverage", SG.PRICE_COVERAGES),
                       ("fund_structure", ("mutual_fund", "money_market", "etf", SUM.NOT_STATED)),
                       ("has_price", ("true", "false"))):
        if filters[k] is not None and filters[k] not in allowed:
            raise HTTPException(status_code=422, detail=f"{k}: not one of the allowed values")
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
    ordered = [jobs[k] for k in J.JOBS if k in jobs]
    return _envelope(principal, {"jobs": ordered, "missing": [k for k in J.JOBS if k not in jobs],
                                 "price_gap_note": J.PRICE_GAP_NOTE,
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
async def master_review(request: Request, status: str = "open", kind: str | None = None, limit: int = 200):
    principal = require_super_admin(request)
    if status not in REVIEW_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(REVIEW_STATUSES)}")
    if kind is not None and kind not in M.REVIEW_KINDS:
        raise HTTPException(status_code=422, detail="unknown review kind")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with platform_scope(conn):
            rows = await conn.fetch(
                "SELECT r.id, r.kind, r.subject_key, r.field, r.incoming_value, r.existing_value, r.provider_key, "
                "r.global_security_id, s.name AS security_name, r.detail, r.status, r.decided_by, r.decided_at, "
                "r.decision_note, r.created_at FROM portfolio.security_master_review r LEFT JOIN "
                "portfolio.securities_global s ON s.id = r.global_security_id WHERE r.valid_to IS NULL AND "
                "r.system_to IS NULL AND r.status = $1 AND ($2::text IS NULL OR r.kind = $2::text) "
                "ORDER BY r.created_at DESC LIMIT $3", status, kind, max(1, min(int(limit), 1000)))
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if isinstance(d["detail"], str) else d["detail"]
        d["kind_label"] = M.REVIEW_LABELS.get(d["kind"], d["kind"])
        d["actions"] = list(OUTCOMES) if d["status"] == "open" and principal.is_super_admin else []
        out.append(d)
    return _envelope(principal, {"rows": out, "filters": {"status": status, "kind": kind}})


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
