"""S30 — Admin → Segregation of duties (/admin/sod/…). services.sod holds the ONE rule; services.sod_admin the
catalog, the rule editing (maker-checker) and the reports. Every route names its gate (docs/AUTHZ1_ROUTES.json):

  manage_org_settings | manage_compliance | view_audit_log
                      GET  /admin/sod                                  the page: duties, item types + policy in force,
                                                                       rules (floor / default / policy / org, with
                                                                       history), the conflict report, the self-approval
                                                                       log, refused checks (last 90 days)
                      GET  /admin/sod/reports/{name}                   one report as JSON, or ?format=csv
  manage_org_settings POST /admin/sod/rules                            draft an org rule / policy version (or, Super
                                                                       Admin, a platform floor / default / policy)
                      POST /admin/sod/rules/{rule_id}/decide           approve / reject a draft — a DIFFERENT person
                                                                       (maker-checker through services.sod itself);
                                                                       a platform draft only by a Super Admin

Super Admin first (services.authz). org_id is only ever the caller's (get_org_id) — never a body or path value; every
request model forbids extras. The read publishes the permission envelope: write controls render only on
``permissions.can_write`` and ``vocabularies.editable`` is an EMPTY list for a view-only caller.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import sod_admin as SA
from services.authz import require_any_permission, require_permission
from services.database import get_pool

router = APIRouter(tags=["segregation_of_duties"])

READ_PERMS = SA.READ_PERMISSIONS
WRITE_PERM = SA.WRITE_PERMISSION


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleIn(_Body):
    owner_scope: Literal["org", "platform"] = "org"
    rule_kind: Literal["org", "policy", "floor", "default"]
    item_type: str = Field(min_length=2, max_length=64)
    rule_key: str | None = Field(default=None, min_length=3, max_length=80)
    maker_duty: str | None = Field(default=None, max_length=64)
    scope: Literal["same_item", "same_household", "same_period"] | None = None
    period_days: int | None = Field(default=None, ge=1, le=3660)
    severity: Literal["block", "warn"] | None = None
    self_approval: Literal["never", "when_no_other_checker"] | None = None
    enforce_floor: bool | None = None
    description: str = Field(min_length=1, max_length=2000)
    retires: bool = False


class DecideIn(_Body):
    outcome: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, SA.SoDAdminRefused):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, SA.SoDAdminConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, SA.SoDAdminError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc


def _envelope(principal) -> dict:
    can_write = bool(principal.has(WRITE_PERM))
    return {
        "permissions": {"can_read": True, "can_write": can_write, "is_super_admin": bool(principal.is_super_admin),
                        "can_edit_platform": bool(principal.is_super_admin),
                        "read_permission": "|".join(READ_PERMS), "write_permission": WRITE_PERM},
        "vocabularies": {"editable": (["org_rule", "policy"] + (["platform_rule"] if principal.is_super_admin else []))
                         if can_write else [],
                         "scopes": list(SA.ORG_RULE_SCOPES), "severities": ["block", "warn"],
                         "self_approval": ["never", "when_no_other_checker"], "reports": list(SA.REPORTS)},
    }


@router.get("/admin/sod")
async def sod_overview(request: Request):
    principal = require_any_permission(request, READ_PERMS)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            body = await SA.overview(conn, org_id=get_org_id(request))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {**body, "viewer_id": principal.user_id, **_envelope(principal)}


@router.get("/admin/sod/reports/{name}")
async def sod_report(name: str, request: Request, format: Literal["json", "csv"] = Query(default="json"),
                     days: int = Query(default=90, ge=1, le=3660)):
    principal = require_any_permission(request, READ_PERMS)
    if name not in SA.REPORTS:
        raise HTTPException(status_code=404, detail=f"unknown report (one of {', '.join(SA.REPORTS)})")
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await SA.report(conn, name, org_id=get_org_id(request), days=days)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    if format == "csv":
        return Response(content=SA.to_csv(rows), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="sod_{name}.csv"'})
    return {"report": name, "rows": rows, **_envelope(principal)}


@router.post("/admin/sod/rules", status_code=201)
async def sod_draft_rule(request: Request, body: RuleIn):
    principal = require_permission(request, WRITE_PERM)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            out = await SA.draft_rule(conn, org_id=get_org_id(request), actor_id=principal.user_id,
                                      is_super_admin=principal.is_super_admin,
                                      body=body.model_dump(exclude_none=True))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"rule": out}


@router.post("/admin/sod/rules/{rule_id}/decide")
async def sod_decide_rule(rule_id: UUID, request: Request, body: DecideIn):
    principal = require_permission(request, WRITE_PERM)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            out = await SA.decide_rule(conn, org_id=get_org_id(request), actor_id=principal.user_id,
                                       is_super_admin=principal.is_super_admin, rule_id=rule_id,
                                       outcome=body.outcome, note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"rule": out}
