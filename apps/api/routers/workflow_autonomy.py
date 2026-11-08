"""S29B — Admin → Workflows → Autonomy (/admin/workflow-autonomy/…). services.workflow_autonomy holds the rules; this
router only gates and translates. Every route names its gate (docs/AUTHZ1_ROUTES.json):

  manage_org_settings | author_workflows | view_workflow_runs
                        GET  /admin/workflow-autonomy                       switches, limits + today's use, eligible
                                                                            verbs with their class, settings history,
                                                                            requests awaiting a decision, shadow runs
                                                                            awaiting review, kill switches
  manage_org_settings   POST /admin/workflow-autonomy/settings              ask to change an org setting (autonomy,
                                                                            kill_switch, limits, shadow_runs_required):
                                                                            tightening at once, loosening a draft
  author_workflows      POST /admin/workflow-autonomy/versions/{version_id} a version's state: off / shadow (at once)
                                                                            or on (a draft; N clean shadow runs)
  manage_org_settings | author_workflows
                        POST /admin/workflow-autonomy/settings/{id}/decide  a DIFFERENT person decides (services.sod;
                                                                            for an opt-in the trigger owner is a maker
                                                                            too). An org setting needs
                                                                            manage_org_settings
  configure_workflow_triggers | author_workflows
                        POST /admin/workflow-autonomy/runs/{run_id}/shadow-review   the trigger owner reviews a shadow run
  Super Admin           POST /admin/workflow-autonomy/platform-kill-switch  stop / resume ALL unattended execution

org_id is only ever the caller's (get_org_id); every request model forbids extras. The read publishes the permission
envelope; ``vocabularies.editable`` is an EMPTY list for a view-only caller.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import workflow_autonomy as WA
from services.authz import require_any_permission, require_permission, require_super_admin
from services.database import get_pool

router = APIRouter(tags=["workflow_autonomy"])

READ_PERMS = ("manage_org_settings", "author_workflows", "view_workflow_runs")
ORG_WRITE_PERM = "manage_org_settings"
VERSION_WRITE_PERM = "author_workflows"
REVIEW_PERMS = ("configure_workflow_triggers", "author_workflows")
DECIDE_PERMS = ("manage_org_settings", "author_workflows")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SettingIn(_Body):
    key: Literal["autonomy", "kill_switch", "limits", "shadow_runs_required"]
    value: Any
    reason: str = Field(min_length=1, max_length=2000)


class VersionIn(_Body):
    state: Literal["off", "shadow", "on"]
    reason: str = Field(min_length=1, max_length=2000)


class DecideIn(_Body):
    outcome: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)


class ReviewIn(_Body):
    outcome: Literal["clean", "not_clean"]
    note: str | None = Field(default=None, max_length=2000)


class KillIn(_Body):
    on: bool
    reason: str = Field(min_length=1, max_length=2000)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, WA.AutonomyRefused):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, WA.AutonomyConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, WA.AutonomyError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _envelope(principal) -> dict:
    can_org = bool(principal.has(ORG_WRITE_PERM))
    can_version = bool(principal.has(VERSION_WRITE_PERM))
    can_review = any(principal.has(p) for p in REVIEW_PERMS)
    editable = []
    if can_org:
        editable += ["autonomy", "kill_switch", "limits", "shadow_runs_required"]
    if can_version:
        editable += ["version_autonomy"]
    if can_review:
        editable += ["shadow_review"]
    if principal.is_super_admin:
        editable += ["platform_kill_switch"]
    return {
        "permissions": {"can_read": True, "can_write": can_org or can_version or bool(principal.is_super_admin),
                        "can_manage_org": can_org, "can_author": can_version, "can_review_shadow": can_review,
                        "can_decide": any(principal.has(p) for p in DECIDE_PERMS),
                        "is_super_admin": bool(principal.is_super_admin),
                        "read_permission": "|".join(READ_PERMS), "write_permission": ORG_WRITE_PERM},
        "vocabularies": {"editable": editable, "inline_editable": []},
    }


@router.get("/admin/workflow-autonomy")
async def autonomy_overview(request: Request):
    principal = require_any_permission(request, READ_PERMS)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            body = await WA.overview(conn, org_id=get_org_id(request))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {**body, "viewer_id": principal.user_id, **_envelope(principal)}


@router.post("/admin/workflow-autonomy/settings", status_code=201)
async def autonomy_change_setting(request: Request, body: SettingIn):
    principal = require_permission(request, ORG_WRITE_PERM)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            out = await WA.change_setting(conn, org_id=get_org_id(request), actor_id=principal.user_id,
                                          is_super_admin=principal.is_super_admin, key=body.key, value=body.value,
                                          reason=body.reason)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"setting": out}


@router.post("/admin/workflow-autonomy/versions/{version_id}", status_code=201)
async def autonomy_version_state(version_id: UUID, request: Request, body: VersionIn):
    principal = require_permission(request, VERSION_WRITE_PERM)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            out = await WA.change_setting(conn, org_id=get_org_id(request), actor_id=principal.user_id,
                                          is_super_admin=principal.is_super_admin, key=WA.KEY_VERSION,
                                          value={"state": body.state}, reason=body.reason,
                                          workflow_version_id=version_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"setting": out}


@router.post("/admin/workflow-autonomy/settings/{setting_id}/decide")
async def autonomy_decide(setting_id: UUID, request: Request, body: DecideIn):
    principal = require_any_permission(request, DECIDE_PERMS)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            key = await conn.fetchval("SELECT setting_key FROM public.workflow_autonomy_settings WHERE id = $1 "
                                      "AND org_id = $2::uuid", setting_id, str(org_id))
            if key is None:
                raise HTTPException(status_code=404, detail="setting request not found")
            if key != WA.KEY_VERSION and not principal.has(ORG_WRITE_PERM):
                raise HTTPException(status_code=403, detail=f"deciding an organization setting needs "
                                                            f"{ORG_WRITE_PERM}")
            out = await WA.decide_setting(conn, org_id=org_id, actor_id=principal.user_id, setting_id=setting_id,
                                          outcome=body.outcome, note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"setting": out}


@router.post("/admin/workflow-autonomy/runs/{run_id}/shadow-review")
async def autonomy_shadow_review(run_id: UUID, request: Request, body: ReviewIn):
    principal = require_any_permission(request, REVIEW_PERMS)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            out = await WA.review_shadow_run(conn, org_id=get_org_id(request), actor_id=principal.user_id,
                                             run_id=run_id, outcome=body.outcome, note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"review": out}


@router.post("/admin/workflow-autonomy/platform-kill-switch", status_code=201)
async def autonomy_platform_kill_switch(request: Request, body: KillIn):
    principal = require_super_admin(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            out = await WA.change_setting(conn, org_id=get_org_id(request), actor_id=principal.user_id,
                                          is_super_admin=True, key=WA.KEY_KILL, value=body.on, reason=body.reason,
                                          platform=True)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"setting": out}
