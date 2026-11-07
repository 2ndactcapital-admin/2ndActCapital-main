"""S28 — the AI drift monitor's API (Super Admin → AI → Drift monitor; org admins read their own org).

    GET /admin/ai/drift                     the task table: one row per task — calls (24 h / 7 d), cost per call, p95
                                            latency, fallback / failure / bypass rates, models used, each with its
                                            baseline and a status (ok / drift / not enough data), open alerts
    GET /admin/ai/drift/tasks/{task_type}   one task: 90 daily points, per-org evaluation, open / closed alerts with
                                            their history, the thresholds that apply and their versions
    GET /admin/ai/drift/thresholds          every live threshold (platform defaults + per-task overrides) + history
    PUT /admin/ai/drift/thresholds          Super Admin: a new VERSION of one rule's threshold (platform default when
                                            task_type is null, else that task's override), audited

GATES (checked FIRST, before anything is read): reads — ``manage_org_settings`` (Super Admin first): a Super Admin
sees every org; an org admin sees their OWN org only (RLS agrees, and every read is filtered by the principal's org
too); a member is refused (403). The write — Super Admin only; an org admin's valid edit is a 403 and writes nothing.
Org ids never come from a request: the scope is the principal's. Envelopes carry permissions + vocabularies from the
server — an org admin's ``editable`` is an empty list.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from services import ai_drift as AD
from services.authz import require_permission, require_super_admin
from services.database import get_pool, platform_scope

router = APIRouter(tags=["ai-drift"])
_P = "/admin/ai/drift"
READ_PERMISSION = "manage_org_settings"
EDITABLE = ["threshold", "min_recent", "min_baseline", "enabled", "note"]


class ThresholdIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_key: str
    task_type: str | None = Field(default=None, max_length=200)
    threshold: Decimal | None = None
    min_recent: int
    min_baseline: int
    enabled: bool
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("threshold", mode="before")
    @classmethod
    def _no_float(cls, v):
        if isinstance(v, float):
            raise ValueError("send the threshold as a string or an integer, never a float")
        return v

    @field_validator("min_recent", "min_baseline", mode="before")
    @classmethod
    def _whole(cls, v):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("a whole number")
        return v


def _envelope(principal, body: dict) -> dict:
    su = bool(principal.is_super_admin)
    perms = {"can_read": True, "can_write": su, "is_super_admin": su, "scope": "platform" if su else "org",
             "read_permission": READ_PERMISSION, "write_permission": "super_admin"}
    vocab = {"rules": [{"key": k, "label": AD.RULE_LABELS[k], "unit": AD.RULE_UNITS[k],
                        "unit_label": AD.UNIT_LABELS.get(AD.RULE_UNITS[k]) if AD.RULE_UNITS[k] else None,
                        "has_threshold": k not in AD.NO_THRESHOLD} for k in AD.RULES],
             "statuses": [{"key": k, "label": v} for k, v in AD.STATUSES.items()],
             "alert_statuses": [{"key": k, "label": v} for k, v in AD.ALERT_STATUSES.items()],
             "editable": list(EDITABLE) if su else [], "inline_editable": []}
    return AD.jsonable({**body, "permissions": perms, "vocabularies": vocab})


def _scope(principal):
    """None = every org (Super Admin); [own org] otherwise."""
    return None if principal.is_super_admin else [str(principal.org_id)]


def _with_test(principal) -> bool:
    """The Super Admin's every-org view hides verify fixture (is_test) orgs, as every sweep does; a caller scoped to
    their OWN org sees that org whatever it is (it is the only org they can see)."""
    return not principal.is_super_admin


async def _read(principal, fn):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if principal.is_super_admin:
            async with platform_scope(conn):
                return await fn(conn)
        return await fn(conn)


@router.get(_P)
async def drift_overview(request: Request):
    principal = require_permission(request, "manage_org_settings")
    now = datetime.now(timezone.utc)
    out = await _read(principal, lambda conn: AD.overview(conn, now=now, org_ids=_scope(principal),
                                                          include_test_orgs=_with_test(principal)))
    return _envelope(principal, out)


@router.get(f"{_P}/tasks/{{task_type}}")
async def drift_task(request: Request, task_type: str):
    principal = require_permission(request, "manage_org_settings")
    if not task_type.strip() or len(task_type) > 200:
        raise HTTPException(status_code=422, detail="task_type: 1–200 characters")
    now = datetime.now(timezone.utc)
    out = await _read(principal, lambda conn: AD.task_detail(conn, now=now, task_type=task_type,
                                                             org_ids=_scope(principal),
                                                             include_test_orgs=_with_test(principal)))
    return _envelope(principal, out)


@router.get(f"{_P}/thresholds")
async def drift_thresholds(request: Request):
    principal = require_permission(request, "manage_org_settings")

    async def fn(conn):
        live = await AD.live_thresholds(conn)
        return {"live": [t.as_dict() for t in live], "history": await AD.threshold_history(conn)}

    return _envelope(principal, await _read(principal, fn))


@router.put(f"{_P}/thresholds")
async def drift_set_threshold(request: Request, body: ThresholdIn):
    principal = require_super_admin(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            async with platform_scope(conn):
                out = await AD.set_threshold(conn, rule_key=body.rule_key, task_type=body.task_type,
                                             threshold=body.threshold, min_recent=body.min_recent,
                                             min_baseline=body.min_baseline, enabled=body.enabled,
                                             actor_id=principal.user_id, actor_org_id=principal.org_id,
                                             note=body.note)
    except AD.ThresholdError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AD.DriftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _envelope(principal, {"threshold": out})
