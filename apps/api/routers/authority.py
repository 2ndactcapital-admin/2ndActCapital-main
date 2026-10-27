"""REP7B — account authority and custody (/authority/…). A household-level
authority the adviser sets (read_only | advise | managed), overridable per
account, with the account's custody; every change approved by a second person.
Nothing here trades.

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_authority     GET  /authority/vocabulary                     the authority × custody matrix and labels
                     GET  /authority/households/{id}                current setting, history, accounts (each
                                                                    effective authority + source), pending change
                     GET  /authority/accounts/{id}                  effective authority / custody + history
  manage_authority   POST /authority/households/{id}/changes        request a household change → approval task
                     POST /authority/accounts/{id}/changes          request an account override / custody change
  approve_authority  POST /authority/changes/{id}/approve           (never the person who asked)
                     POST /authority/changes/{id}/reject            (a note)

Super Admin first (services.authz). org_id is only ever the caller's
(get_org_id); every request model forbids extras; another org's row is a 404.
Billing and disclosure for advising on held-away assets are an open decision
(Joe and counsel) — nothing here shows or implies billing.
"""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import authority as AU
from services import human_tasks as HT
from services.authz import require_permission
from services.database import get_pool

router = APIRouter(tags=["authority"])

VIEW, MANAGE, APPROVE = AU.VIEW, AU.MANAGE, AU.APPROVE
EDITABLE_HOUSEHOLD = ("authority", "effective_from", "reason")
EDITABLE_ACCOUNT = ("authority_override", "custody", "receiving", "effective_from", "reason")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HouseholdChangeIn(_Body):
    authority: Literal["read_only", "advise", "managed"]
    effective_from: date
    reason: str = Field(min_length=1, max_length=2000)


class ReceivingIn(_Body):
    custodian_name: str | None = Field(default=None, max_length=200)
    participant_number: str | None = Field(default=None, max_length=5)
    agent_bank: str | None = Field(default=None, max_length=200)
    account_last4: str | None = Field(default=None, max_length=4)
    contra_broker: str | None = Field(default=None, max_length=200)


class AccountChangeIn(_Body):
    authority_override: Literal["read_only", "advise", "managed"] | None = None
    custody: Literal["integrated", "other_custodian", "outside"]
    receiving: ReceivingIn | None = None
    effective_from: date
    reason: str = Field(min_length=1, max_length=2000)


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (AU.AuthorityNotFound, HT.HumanTaskNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (AU.MakerCheckerViolation, HT.MakerCheckerError, PermissionError, HT.HumanTaskPermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (AU.AuthorityConflict, HT.HumanTaskConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.CheckViolationError):
        raise HTTPException(status_code=422, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        raise HTTPException(status_code=409, detail=str(exc).split("\n")[0]) from exc
    if isinstance(exc, (AU.AuthorityError, HT.HumanTaskError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


async def _act(coro):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


def _permissions(principal) -> dict:
    has = (lambda p: principal.is_super_admin or p in principal.permissions)
    return {"can_read": has(VIEW), "can_write": has(MANAGE), "can_approve": has(APPROVE),
            "is_super_admin": principal.is_super_admin, "read_permission": VIEW, "write_permission": MANAGE,
            "approve_permission": APPROVE}


def _env(principal, editable: tuple[str, ...], **body) -> dict:
    perms = _permissions(principal)
    v = AU.vocabularies()
    v["editable"] = list(editable) if perms["can_write"] else []
    v["inline_editable"] = []
    return {**body, "permissions": perms, "vocabularies": v}


def _decorate(panel: dict, principal) -> dict:
    """Server-side actions on each pending change; a change control renders
    only for an action listed here."""
    perms = _permissions(principal)
    raw = {str(r["id"]): r for r in panel.pop("raw")}
    for h in panel["history"]:
        h["actions"] = AU.change_actions(raw[h["id"]], can_approve=perms["can_approve"], user_id=principal.user_id)
    if panel.get("pending"):
        panel["pending"]["actions"] = AU.change_actions(raw[panel["pending"]["id"]], can_approve=perms["can_approve"],
                                                        user_id=principal.user_id)
    panel["actions"] = ["request_change"] if (perms["can_write"] and not panel.get("pending")) else []
    return panel


@router.get("/authority/vocabulary")
async def vocabulary(request: Request):
    principal = require_permission(request, VIEW)
    return _env(principal, ())


@router.get("/authority/households/{household_id}")
async def household(request: Request, household_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            panel = await AU.household_panel(conn, org_id, household_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _env(principal, EDITABLE_HOUSEHOLD, **_decorate(panel, principal))


@router.get("/authority/accounts/{account_id}")
async def account(request: Request, account_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            panel = await AU.account_panel(conn, org_id, account_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _env(principal, EDITABLE_ACCOUNT, **_decorate(panel, principal))


@router.post("/authority/households/{household_id}/changes", status_code=201)
async def request_household_change(request: Request, household_id: UUID, body: HouseholdChangeIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    out = await _act(AU.request_household_change(pool, org_id=org_id, actor_id=principal.user_id,
                                                 household_id=household_id, authority=body.authority,
                                                 effective_from=body.effective_from, reason=body.reason))
    return {"change": {k: v for k, v in out.items() if k != "task"}, "task_id": (out.get("task") or {}).get("id")}


@router.post("/authority/accounts/{account_id}/changes", status_code=201)
async def request_account_change(request: Request, account_id: UUID, body: AccountChangeIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    out = await _act(AU.request_account_change(
        pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
        authority_override=body.authority_override, custody=body.custody, effective_from=body.effective_from,
        reason=body.reason, receiving=body.receiving.model_dump(exclude_none=True) if body.receiving else None))
    return {"change": {k: v for k, v in out.items() if k != "task"}, "task_id": (out.get("task") or {}).get("id")}


@router.post("/authority/changes/{change_id}/approve")
async def approve(request: Request, change_id: UUID, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return {"change": await _act(AU.decide(pool, org_id=org_id, actor_id=principal.user_id, change_id=change_id,
                                           outcome="approve", note=body.note))}


@router.post("/authority/changes/{change_id}/reject")
async def reject(request: Request, change_id: UUID, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return {"change": await _act(AU.decide(pool, org_id=org_id, actor_id=principal.user_id, change_id=change_id,
                                           outcome="reject", note=body.note))}
