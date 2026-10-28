"""LOTMETHOD — the lot-relief method (/lot-method/…): a household default, an
account override (with the custodian's own cost-basis method and the
securities it carries at average cost), each approved by a second person, and
the per-proposal override with its lot picker. Nothing here trades.

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it).
The method is a proposal setting, so the gates are rep5's proposal permissions:

  view_proposals     GET  /lot-method/vocabulary                     methods, preferences, custodian methods, labels
                     GET  /lot-method/households/{id}                current default, history, pending change
                     GET  /lot-method/accounts/{id}                  effective method (and its source), custodian
                                                                     method, average-cost securities, history
                     GET  /lot-method/proposals/{id}/lots            the lot picker: each sale line's open lots
  manage_proposals   POST /lot-method/households/{id}/changes        request a household default → approval task
                     POST /lot-method/accounts/{id}/changes          request an account override → approval task
                     POST /lot-method/proposals/{id}/override        a draft / pending proposal re-made with the
                                                                     chosen method or picked lots (a NEW proposal;
                                                                     the old one superseded)
  approve_proposals  POST /lot-method/changes/{id}/approve           (never the person who asked)
                     POST /lot-method/changes/{id}/reject            (a note)

Super Admin first (services.authz). org_id is only ever the caller's
(get_org_id); every request model forbids extras; another org's row is a 404.
Amounts and quantities travel as decimal STRINGS; a JSON float is a 422.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from routers.entities import get_org_id
from services import human_tasks as HT
from services import lot_method as LM
from services import rebalance_proposals as RP
from services.authz import require_permission
from services.custody.trading import _decimal
from services.database import get_pool

router = APIRouter(tags=["lot-method"])

VIEW, MANAGE, APPROVE = LM.VIEW, LM.MANAGE, LM.APPROVE
EDITABLE_HOUSEHOLD = ("lot_method", "gain_preference", "realize_gains_target", "reason")
EDITABLE_ACCOUNT = ("lot_method_override", "gain_preference_override", "realize_gains_target",
                    "custodian_cost_basis_method", "custodian_method_source", "average_cost_security_ids", "reason")
EDITABLE_PROPOSAL = ("lot_method", "gain_preference", "realize_gains_target", "lots")
Method = Literal["tax_optimized", "hifo", "fifo", "lifo", "lowest_cost", "highest_cost", "specific"]
Preference = Literal["prefer_long_term", "st_losses_first", "realize_gains"]
CustodianMethod = Literal["unknown", "fifo", "lifo", "hifo", "lowest_cost", "highest_cost", "average_cost", "specific",
                          "tax_optimized"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HouseholdChangeIn(_Body):
    lot_method: Method
    gain_preference: Preference = "prefer_long_term"
    realize_gains_target: Decimal | None = None
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("realize_gains_target", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class AccountChangeIn(_Body):
    lot_method_override: Method | None = None
    gain_preference_override: Preference | None = None
    realize_gains_target: Decimal | None = None
    custodian_cost_basis_method: CustodianMethod = "unknown"
    custodian_method_source: Literal["custodian", "adviser"] | None = None
    average_cost_security_ids: list[UUID] = Field(default_factory=list, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("realize_gains_target", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class PickIn(_Body):
    global_security_id: UUID
    lot_id: UUID
    quantity: Decimal

    @field_validator("quantity", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class OverrideIn(_Body):
    lot_method: Method | None = None
    gain_preference: Preference | None = None
    realize_gains_target: Decimal | None = None
    lots: list[PickIn] | None = Field(default=None, max_length=500)

    @field_validator("realize_gains_target", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (LM.LotMethodNotFound, RP.ProposalNotFound, HT.HumanTaskNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (LM.MakerCheckerViolation, RP.MakerCheckerViolation, HT.MakerCheckerError, PermissionError,
                        HT.HumanTaskPermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (LM.LotMethodConflict, RP.ProposalConflict, HT.HumanTaskConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.CheckViolationError):
        raise HTTPException(status_code=422, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        raise HTTPException(status_code=409, detail=str(exc).split("\n")[0]) from exc
    if isinstance(exc, (LM.LotMethodError, RP.ProposalError, HT.HumanTaskError, ValueError)):
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
    v = LM.vocabularies()
    v["editable"] = list(editable) if perms["can_write"] else []
    v["inline_editable"] = []
    return {**body, "permissions": perms, "vocabularies": v}


def _decorate(panel: dict, principal) -> dict:
    """Server-side actions on each pending change; a control renders only for
    an action listed here."""
    perms = _permissions(principal)
    raw = {str(r["id"]): r for r in panel.pop("raw")}
    for h in panel["history"]:
        h["actions"] = LM.change_actions(raw[h["id"]], can_approve=perms["can_approve"], user_id=principal.user_id)
    if panel.get("pending"):
        panel["pending"]["actions"] = LM.change_actions(raw[panel["pending"]["id"]], can_approve=perms["can_approve"],
                                                        user_id=principal.user_id)
    panel["actions"] = ["request_change"] if (perms["can_write"] and not panel.get("pending")) else []
    return panel


@router.get("/lot-method/vocabulary")
async def vocabulary(request: Request):
    principal = require_permission(request, VIEW)
    return _env(principal, ())


@router.get("/lot-method/households/{household_id}")
async def household(request: Request, household_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            panel = await LM.household_panel(conn, org_id, household_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _env(principal, EDITABLE_HOUSEHOLD, **_decorate(panel, principal))


@router.get("/lot-method/accounts/{account_id}")
async def account(request: Request, account_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            panel = await LM.account_panel(conn, org_id, account_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _env(principal, EDITABLE_ACCOUNT, **_decorate(panel, principal))


@router.post("/lot-method/households/{household_id}/changes", status_code=201)
async def request_household_change(request: Request, household_id: UUID, body: HouseholdChangeIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    out = await _act(LM.request_household_change(
        pool, org_id=org_id, actor_id=principal.user_id, household_id=household_id, lot_method=body.lot_method,
        gain_preference=body.gain_preference, realize_gains_target=body.realize_gains_target, reason=body.reason))
    return {"change": {k: v for k, v in out.items() if k != "task"}, "task_id": (out.get("task") or {}).get("id")}


@router.post("/lot-method/accounts/{account_id}/changes", status_code=201)
async def request_account_change(request: Request, account_id: UUID, body: AccountChangeIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    out = await _act(LM.request_account_change(
        pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
        lot_method_override=body.lot_method_override, gain_preference_override=body.gain_preference_override,
        realize_gains_target=body.realize_gains_target, custodian_cost_basis_method=body.custodian_cost_basis_method,
        custodian_method_source=body.custodian_method_source,
        average_cost_security_ids=[str(x) for x in body.average_cost_security_ids], reason=body.reason))
    return {"change": {k: v for k, v in out.items() if k != "task"}, "task_id": (out.get("task") or {}).get("id")}


@router.post("/lot-method/changes/{change_id}/approve")
async def approve(request: Request, change_id: UUID, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return {"change": await _act(LM.decide(pool, org_id=org_id, actor_id=principal.user_id, change_id=change_id,
                                           outcome="approve", note=body.note))}


@router.post("/lot-method/changes/{change_id}/reject")
async def reject(request: Request, change_id: UUID, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return {"change": await _act(LM.decide(pool, org_id=org_id, actor_id=principal.user_id, change_id=change_id,
                                           outcome="reject", note=body.note))}


@router.get("/lot-method/proposals/{proposal_id}/lots")
async def proposal_lots(request: Request, proposal_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            p = await RP.load(conn, org_id, proposal_id)
            options = await LM.lot_options(conn, org_id, p, await RP.trades_of(conn, p["id"]))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    can = perms["can_write"] and p["status"] in ("draft", "pending_approval") and RP.lot_method_overridable(p)
    return _env(principal, EDITABLE_PROPOSAL if can else (),
                proposal={"id": str(p["id"]), "status": p["status"], "lot_method": p.get("lot_method"),
                          "lot_method_source": p.get("lot_method_source"), "gain_preference": p.get("gain_preference"),
                          "realize_gains_target": None if p.get("realize_gains_target") is None
                          else str(p["realize_gains_target"]), "lot_picks": RP._loads(p.get("lot_picks"))},
                lines=options, actions=["override_lot_method"] if can else [])


@router.post("/lot-method/proposals/{proposal_id}/override", status_code=201)
async def override(request: Request, proposal_id: UUID, body: OverrideIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    raw = {"lot_method": body.lot_method, "gain_preference": body.gain_preference,
           "realize_gains_target": body.realize_gains_target,
           "lots": [{"global_security_id": str(x.global_security_id), "lot_id": str(x.lot_id), "quantity": x.quantity}
                    for x in body.lots] if body.lots else None}
    out = await _act(RP.override_lot_method(pool, org_id=org_id, proposal_id=proposal_id,
                                            actor_id=principal.user_id, override=raw))
    return {"proposal": RP.proposal_out(out), "superseded_id": str(proposal_id)}
