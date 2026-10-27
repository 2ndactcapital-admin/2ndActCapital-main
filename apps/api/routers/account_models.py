"""REP5 — accounts join models; rebalance proposals (/accounts/{id}/model…, /proposals/…).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_proposals     GET  /proposals/accounts                           Portfolio → Accounts (model, drift, last
                                                                        proposal, open instructions) + envelope
                     GET  /accounts/{id}/model                          the account's link, pending change, history,
                                                                        registry offer, plan, proposals + envelope
                     GET  /accounts/{id}/model/transition               transition preview (keep / sell now / later)
                     GET  /proposals                                    proposals list + envelope
                     GET  /proposals/{id}                               detail: trades, lots, prices, weights, gains,
                                                                        warnings, wash-sale flags, tasks, actions
  manage_proposals   POST /accounts/{id}/model/join                     join an approved model (opens the task)
                     POST /accounts/{id}/model/settings                 change settings (a new version + task)
                     POST /accounts/{id}/model/change                   change model (a new version + task)
                     POST /accounts/{id}/model/end                      leave the model (a new version + task)
                     POST /accounts/{id}/proposals                      "Rebalance now" / a staff-entered withdrawal
                     POST /proposals/{id}/submit                        draft → awaiting approval (the maker)
                     POST /proposals/{id}/execute                       retry routing an approved proposal
                     POST /proposals/{id}/refresh                       executed once every instruction is done
  approve_proposals  POST /accounts/{id}/model/changes/{link_id}/approve   (never the maker)
                     POST /accounts/{id}/model/changes/{link_id}/reject    (a note)
                     POST /proposals/{id}/approve                       (never the maker) → execution instructions
                     POST /proposals/{id}/reject                        (a note)

Super Admin first (services.authz). org_id is only ever the caller's
(get_org_id) — never a body or path value; every request model forbids extras.
Another org's account or proposal is a 404 (RLS + an explicit org predicate).
Amounts and fractions travel as decimal STRINGS; a JSON float is a 422.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from routers.entities import get_org_id
from services import account_models as AM
from services import human_tasks as HT
from services import rebalance_proposals as RP
from services.authz import require_permission
from services.custody.trading import _decimal
from services.database import get_pool

router = APIRouter(tags=["proposals"])

VIEW, MANAGE, APPROVE = AM.VIEW, AM.MANAGE, AM.APPROVE


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExclusionIn(_Body):
    global_security_id: UUID
    reason: str = Field(min_length=1, max_length=500)


class SettingsIn(_Body):
    cash_floor: Decimal | None = None
    drift_threshold: Decimal | None = None
    rebalance_frequency: str | None = Field(default=None, max_length=40)
    exclusions: list[ExclusionIn] | None = Field(default=None, max_length=500)
    exclusion_policy: str | None = Field(default=None, max_length=40)
    gain_budget: Decimal | None = None
    avoid_short_term: bool | None = None
    short_term_wait_days: int | None = None
    harvesting_engine: str | None = Field(default=None, max_length=40)
    fee_schedule_override_id: UUID | None = None
    harvest_enabled: bool | None = None
    harvest_loss_pct: Decimal | None = None
    harvest_loss_min: Decimal | None = None
    harvest_to_cash: bool | None = None
    harvest_swap_back: bool | None = None

    @field_validator("cash_floor", "drift_threshold", "gain_budget", "harvest_loss_pct", "harvest_loss_min",
                     mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)

    def changes(self) -> dict:
        """Only what the request sent (a sparse change); explicit null clears
        a nullable setting."""
        out = {}
        for k in self.model_fields_set:
            v = getattr(self, k)
            if k == "exclusions":
                v = [{"global_security_id": str(e.global_security_id), "reason": e.reason} for e in (v or [])]
            elif k == "fee_schedule_override_id" and v is not None:
                v = str(v)
            elif k in ("cash_floor", "exclusion_policy", "avoid_short_term", "short_term_wait_days",
                       "harvesting_engine", "harvest_enabled") and v is None:
                raise HTTPException(status_code=422, detail=f"{k} cannot be cleared")
            out[k] = v
        return out


class JoinIn(_Body):
    model_id: UUID
    settings: SettingsIn = Field(default_factory=SettingsIn)
    notes: str | None = Field(default=None, max_length=2000)


class SettingsChangeIn(_Body):
    settings: SettingsIn
    notes: str | None = Field(default=None, max_length=2000)


class EndIn(_Body):
    reason: str = Field(min_length=1, max_length=2000)


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


class GenerateIn(_Body):
    trigger: Literal["manual", "cash_flow"] = "manual"
    withdrawal_amount: Decimal | None = None

    @field_validator("withdrawal_amount", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (AM.AccountModelNotFound, RP.ProposalNotFound, HT.HumanTaskNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (AM.MakerCheckerViolation, RP.MakerCheckerViolation, HT.MakerCheckerError, PermissionError,
                        HT.HumanTaskPermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (AM.AccountModelConflict, RP.ProposalConflict, HT.HumanTaskConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.CheckViolationError):
        raise HTTPException(status_code=422, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        raise HTTPException(status_code=409, detail=str(exc).split("\n")[0]) from exc
    if isinstance(exc, (AM.AccountModelError, RP.ProposalError, HT.HumanTaskError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _permissions(principal) -> dict:
    return {
        "can_read": principal.is_super_admin or VIEW in principal.permissions,
        "can_write": principal.is_super_admin or MANAGE in principal.permissions,
        "can_approve": principal.is_super_admin or APPROVE in principal.permissions,
        "is_super_admin": principal.is_super_admin,
        "read_permission": VIEW, "write_permission": MANAGE, "approve_permission": APPROVE,
    }


def _vocab(perms: dict) -> dict:
    v = RP.vocabularies()
    v["editable"] = list(AM.SETTING_FIELDS) if perms["can_write"] else []
    return v


async def _act(coro):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


def _account_out(a: dict) -> dict:
    return {"id": str(a["id"]), "account_number_masked": a["account_number_masked"],
            "custodian_code": a["custodian_code"], "registration_type": a["registration_type"],
            "tax_status": a["tax_status"], "is_held_away": a["is_held_away"],
            "is_discretionary": a["is_discretionary"], "closed": AM.is_closed(a),
            "closed_on": a["closed_on"].isoformat() if a["closed_on"] else None}


# ── Reads ───────────────────────────────────────────────────────────────────
@router.get("/proposals/accounts")
async def accounts_overview(request: Request, model_id: UUID | None = None,
                            filter: str | None = Query(default=None, max_length=40),  # noqa: A002
                            q: str | None = Query(default=None, max_length=100)):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await RP.accounts_overview(conn, org_id, model_id=model_id, status=filter, q=q)
            models = await AM.registry_offer(conn, org_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"rows": rows, "models": [{"id": str(m["id"]), "name": m["name"]} for m in models],
            "permissions": perms, "vocabularies": _vocab(perms)}


@router.get("/accounts/{account_id}/model")
async def account_model(request: Request, account_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            acct = await AM.load_account(conn, org_id, account_id)
            links = await AM.links_detail(conn, org_id, acct["id"])
            offer = await AM.registry_offer(conn, org_id) if perms["can_write"] else []
            engines = await AM.harvesting_options(conn, acct)
            plan = await RP.current_plan(conn, org_id, acct["id"])
            proposals = await RP.list_proposals(conn, org_id, account_id=acct["id"], limit=50)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    pending = links["pending"]
    can_decide = bool(pending) and perms["can_approve"] and pending["maker_id"] != principal.user_id
    actions = []
    if perms["can_write"] and not AM.is_closed(acct) and not pending:
        actions += ["change_settings", "change_model", "end"] if links["active"] else ["join"]
    if perms["can_write"] and links["active"] and not AM.is_closed(acct):
        actions.append("generate")
    if can_decide:
        actions += ["approve_change", "reject_change"]
    if acct["is_held_away"]:
        # rep7 — a held-away account is never traded: no join, model change, settings or proposal control
        actions = [a for a in actions if a in ("end", "approve_change", "reject_change")]
        offer = []
    return {"account": _account_out(acct), "links": links,
            "registry": [{"id": str(m["id"]), "name": m["name"], "source": m["source"],
                          "live_version_no": m["live_version_no"]} for m in offer],
            "harvesting_engines": engines, "transition_plan": plan, "proposals": proposals,
            "permissions": {**perms, "can_decide_change": can_decide}, "actions": actions,
            "vocabularies": _vocab(perms)}


@router.get("/accounts/{account_id}/model/transition")
async def transition(request: Request, account_id: str, model_id: UUID | None = None):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            out = await RP.transition_preview(conn, org_id, account_id, model_id=model_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    perms = _permissions(principal)
    return {**out, "permissions": perms, "vocabularies": _vocab(perms)}


@router.get("/proposals")
async def list_proposals(request: Request, account_id: UUID | None = None,
                         status: str | None = Query(default=None, max_length=40)):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await RP.list_proposals(conn, org_id, account_id=account_id, status=status)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"rows": rows, "permissions": perms, "vocabularies": _vocab(perms)}


@router.get("/proposals/{proposal_id}")
async def get_proposal(request: Request, proposal_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            d = await RP.detail(conn, org_id, proposal_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    open_task = next((t for t in d["tasks"] if t["status"] == "open"), None)
    is_maker = d["maker_id"] is not None and d["maker_id"] == principal.user_id
    can_decide = bool(open_task) and HT.can_decide(open_task, user_id=principal.user_id,
                                                   permissions=principal.permissions,
                                                   is_super_admin=principal.is_super_admin) and not is_maker
    perms = {**perms, "is_maker": is_maker, "can_decide": can_decide}
    actions = RP.actions_for(d, can_write=perms["can_write"], can_approve=can_decide, user_id=principal.user_id,
                             task_open=open_task is not None)
    return {"proposal": d, "permissions": perms, "actions": actions, "vocabularies": _vocab(perms)}


# ── Link changes ────────────────────────────────────────────────────────────
def _link_result(row: dict) -> dict:
    out = {"link": AM.link_out(row)}
    if row.get("task"):
        out["task"] = {"id": row["task"]["id"], "status": row["task"]["status"]}
    for k in ("transition_proposal_id", "transition_proposal_error"):
        if k in row:
            out[k] = str(row[k]) if row[k] is not None else None
    return out


@router.post("/accounts/{account_id}/model/join", status_code=201)
async def join(request: Request, account_id: str, body: JoinIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(AM.propose_change(pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                                       change_kind="join", model_id=body.model_id, settings=body.settings.changes(),
                                       notes=body.notes))
    return _link_result(row)


@router.post("/accounts/{account_id}/model/settings", status_code=201)
async def change_settings(request: Request, account_id: str, body: SettingsChangeIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(AM.propose_change(pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                                       change_kind="settings", settings=body.settings.changes(), notes=body.notes))
    return _link_result(row)


@router.post("/accounts/{account_id}/model/change", status_code=201)
async def change_model(request: Request, account_id: str, body: JoinIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(AM.propose_change(pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                                       change_kind="change_model", model_id=body.model_id,
                                       settings=body.settings.changes(), notes=body.notes))
    return _link_result(row)


@router.post("/accounts/{account_id}/model/end", status_code=201)
async def end_link(request: Request, account_id: str, body: EndIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(AM.propose_change(pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                                       change_kind="end", reason=body.reason))
    return _link_result(row)


async def _decide_change(request: Request, account_id: str, link_id: str, outcome: str, note: str | None):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            acct = await AM.load_account(conn, org_id, account_id)
            link = await AM.link_row(conn, org_id, link_id)
        if str(link["account_id"]) != str(acct["id"]):
            raise AM.AccountModelNotFound("account model link not found")
        row = await AM.decide(pool, org_id=org_id, actor_id=principal.user_id, link_id=link_id, outcome=outcome,
                              note=note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _link_result(row)


@router.post("/accounts/{account_id}/model/changes/{link_id}/approve")
async def approve_change(request: Request, account_id: str, link_id: str, body: Decision):
    return await _decide_change(request, account_id, link_id, "approve", body.note)


@router.post("/accounts/{account_id}/model/changes/{link_id}/reject")
async def reject_change(request: Request, account_id: str, link_id: str, body: Decision):
    return await _decide_change(request, account_id, link_id, "reject", body.note)


# ── Proposals ───────────────────────────────────────────────────────────────
def _proposal_result(row: dict) -> dict:
    out = {"proposal": RP.proposal_out(row)}
    if "execution_error" in row:
        out["execution_error"] = row["execution_error"]
    return out


@router.post("/accounts/{account_id}/proposals", status_code=201)
async def generate(request: Request, account_id: str, body: GenerateIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    if body.trigger == "manual" and body.withdrawal_amount is not None:
        raise HTTPException(status_code=422, detail="a withdrawal is entered with trigger cash_flow")
    if body.trigger == "cash_flow" and body.withdrawal_amount is None:
        raise HTTPException(status_code=422, detail="a staff-entered cash flow is a withdrawal amount")
    pool = await get_pool()
    row = await _act(RP.generate(pool, org_id=org_id, account_id=account_id, trigger=body.trigger,
                                 maker_kind="user", maker_id=principal.user_id, withdrawal=body.withdrawal_amount,
                                 trigger_ref={"entered_by": principal.user_id}))
    return _proposal_result(row)


@router.post("/proposals/{proposal_id}/submit")
async def submit(request: Request, proposal_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return _proposal_result(await _act(RP.submit(pool, org_id=org_id, proposal_id=proposal_id,
                                                 actor_id=principal.user_id)))


@router.post("/proposals/{proposal_id}/execute")
async def execute(request: Request, proposal_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return _proposal_result(await _act(RP.execute(pool, org_id=org_id, proposal_id=proposal_id,
                                                  actor_id=principal.user_id)))


@router.post("/proposals/{proposal_id}/refresh")
async def refresh(request: Request, proposal_id: str):
    require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return _proposal_result(await _act(RP.sync_executed(pool, org_id=org_id, proposal_id=proposal_id)))


@router.post("/proposals/{proposal_id}/approve")
async def approve(request: Request, proposal_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return _proposal_result(await _act(RP.decide(pool, org_id=org_id, actor_id=principal.user_id,
                                                 proposal_id=proposal_id, outcome="approve", note=body.note)))


@router.post("/proposals/{proposal_id}/reject")
async def reject(request: Request, proposal_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return _proposal_result(await _act(RP.decide(pool, org_id=org_id, actor_id=principal.user_id,
                                                 proposal_id=proposal_id, outcome="reject", note=body.note)))
