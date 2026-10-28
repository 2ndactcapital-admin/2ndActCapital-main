"""REP8 — Replication Studio: the OPTIONS SLEEVE (/replication/options/…).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_replication   GET  /replication/options/entries/{entry_id}               the Options sleeve: packages, legs,
                                                                               outcome period, cap / buffer / floor,
                                                                               minimum account, quotes with age,
                                                                               viability note + envelope
                     GET  /replication/options/packages/{package_id}            one package (legs, contracts, quotes)
                     GET  /replication/options/packages/{package_id}/payoff     the payoff lab
                     GET  /replication/options/packages/{package_id}/settlements  expiry settlement entries
                     GET  /replication/options/quotes                           adviser quotes (with age)
                     GET  /replication/options/contracts                        option contracts (security master)
  manage_models      POST /replication/options/entries/{entry_id}/build         the "Holdings of …" builder (core +
  + manage_replication                                                         options sleeve; find or build)
  manage_replication POST /replication/options/packages/{package_id}/price      price a DRAFT (start level, cap)
                     POST /replication/options/packages/{package_id}/submit     open the approval task
                     POST /replication/options/quotes                           enter a quote (a new row, never an edit)
                     POST /replication/options/contracts                        define an option contract (idempotent)
  approve_replication POST /replication/options/packages/{package_id}/approve   (never a maker)
                     POST /replication/options/packages/{package_id}/reject     (a note)
  manage_proposals   POST /replication/options/accounts/{account_id}/proposals  an options proposal (draft; rep5's
                                                                               submit / approve routes decide it)
                     POST /replication/options/proposals/{proposal_id}/gate     re-run the options tax gate
  view_proposals     GET  /replication/options/proposals/{proposal_id}/gate     the tax-gate results per leg
  manage_execution   POST /replication/options/instructions/{instruction_id}/book  book an options instruction's
                                                                               fills as option lots

Options proposals are approved through rep5's POST /proposals/{id}/approve
(approve_proposals; the tax gate is that task's pre-check — a block is a 409
naming the rule). Super Admin first (services.authz). org_id is only ever the
caller's (get_org_id) — never a body or path value; request models forbid
extras; amounts travel as decimal STRINGS. Another org's entry, package,
quote, proposal or instruction is a 404. THE CUSTODY CLIFF: nothing here
trades — every options trade is a manual execution instruction.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import account_models as AM
from services import execution as X
from services import human_tasks as HT
from services import models as M
from services import options_proposals as OP
from services import options_sleeve as OS
from services import options_tax_gate as G
from services import rebalance_proposals as RP
from services import replica_terms as T
from services.authz import require_permission
from services.database import get_pool

router = APIRouter(tags=["replication-options"])

VIEW, MANAGE, APPROVE = OS.VIEW, OS.MANAGE, OS.APPROVE
BUILD_MODELS = M.MANAGE
VIEW_PROPOSALS, MANAGE_PROPOSALS = AM.VIEW, AM.MANAGE
MANAGE_EXECUTION = X.MANAGE
BUILD_FIELDS = ["underlying_global_security_id", "core_model_id", "core_global_security_id", "broad_based_index",
                "is_flex", "exercise_style", "settlement", "multiplier", "fresh_period_start",
                "start_level_quote_id", "call_strike_pct"]
PRICE_FIELDS = ["start_level_quote_id", "cap_strike_pct", "call_strike_pct", "broad_based_index"]
QUOTE_FIELDS = ["global_security_id", "kind", "value", "source", "as_of"]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuildIn(_Body):
    underlying_global_security_id: str = Field(min_length=1, max_length=64)
    core_model_id: str | None = Field(default=None, max_length=64)
    core_global_security_id: str | None = Field(default=None, max_length=64)
    broad_based_index: bool = True
    is_flex: bool = True
    exercise_style: Literal["european", "american"] = "european"
    settlement: Literal["cash", "physical"] | None = None
    multiplier: str = Field(default="100", max_length=20)
    fresh_period_start: date | None = None
    start_level_quote_id: str | None = Field(default=None, max_length=64)
    call_strike_pct: str | None = Field(default=None, max_length=20)


class PriceIn(_Body):
    start_level_quote_id: str | None = Field(default=None, max_length=64)
    cap_strike_pct: str | None = Field(default=None, max_length=20)
    call_strike_pct: str | None = Field(default=None, max_length=20)
    broad_based_index: bool | None = None


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


class QuoteIn(_Body):
    global_security_id: str = Field(min_length=1, max_length=64)
    kind: Literal["premium", "index_level", "underlying_price", "implied_vol"]
    value: str = Field(min_length=1, max_length=40)
    source: str = Field(min_length=1, max_length=500)
    as_of: datetime


class ContractIn(_Body):
    underlying_global_security_id: str = Field(min_length=1, max_length=64)
    option_type: Literal["call", "put"]
    strike: str = Field(min_length=1, max_length=40)
    expiry: date
    exercise_style: Literal["european", "american"]
    settlement: Literal["cash", "physical"]
    is_flex: bool
    multiplier: str = Field(default="100", max_length=20)
    occ_symbol: str | None = Field(default=None, max_length=21)
    exchange_listed: bool = True
    broad_based_index: bool | None = None
    is_mini: bool = False
    mini_ratio: str | None = Field(default=None, max_length=20)


class ProposalIn(_Body):
    package_id: str = Field(min_length=1, max_length=64)
    action: Literal["open", "close", "roll"] = "open"
    executor_type: Literal["custodian_desk", "broker", "trader"] | None = None
    executor_name: str | None = Field(default=None, max_length=300)
    executor_contact: str | None = Field(default=None, max_length=500)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (OS.OptionsNotFound, T.ReplicaNotFound, M.ModelNotFound, RP.ProposalNotFound,
                        AM.AccountModelNotFound, X.ExecutionNotFound, HT.HumanTaskNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (OS.MakerCheckerViolation, RP.MakerCheckerViolation, HT.MakerCheckerError,
                        HT.HumanTaskPermissionError, HT.AgentActorRefused, M.MakerCheckerViolation, PermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, OS.OptionsConflict):
        raise HTTPException(status_code=409, detail={"message": str(exc), "missing": exc.missing}) from exc
    if isinstance(exc, (RP.ProposalConflict, HT.HumanTaskConflict, M.ModelConflict, T.ReplicaConflict,
                        AM.AccountModelConflict, X.ExecutionConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, (asyncpg.CheckViolationError, asyncpg.InsufficientPrivilegeError)):
        raise HTTPException(status_code=422, detail=str(exc).split("\n")[0]) from exc
    if isinstance(exc, (OS.OptionsError, RP.ProposalError, M.ModelError, X.ExecutionError, HT.HumanTaskError,
                        ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


async def _act(coro):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


def _permissions(principal) -> dict:
    su = principal.is_super_admin
    perms = principal.permissions
    return {
        "can_read": su or VIEW in perms, "can_write": su or MANAGE in perms, "can_approve": su or APPROVE in perms,
        "can_build": su or (MANAGE in perms and BUILD_MODELS in perms),
        "can_propose": su or MANAGE_PROPOSALS in perms, "can_view_proposals": su or VIEW_PROPOSALS in perms,
        "can_book": su or MANAGE_EXECUTION in perms, "is_super_admin": su,
        "read_permission": VIEW, "write_permission": MANAGE, "approve_permission": APPROVE,
        "build_permission": BUILD_MODELS, "propose_permission": MANAGE_PROPOSALS,
    }


def _vocab(perms: dict) -> dict:
    return {**OS.vocabularies(), **G.vocabularies(), **OP.vocabularies(),
            "editable": list(BUILD_FIELDS) if perms["can_build"] else [],
            "price_editable": list(PRICE_FIELDS) if perms["can_write"] else [],
            "quote_editable": list(QUOTE_FIELDS) if perms["can_write"] else []}


def _envelope(principal, body: dict) -> dict:
    perms = _permissions(principal)
    return jsonable_encoder({**body, "permissions": perms, "vocabularies": _vocab(perms)})


async def _detail_with_actions(conn, principal, org_id, package_id) -> dict:
    d = await OS.package_detail(conn, org_id, package_id)
    perms = _permissions(principal)
    task = d.get("task")
    task_open = bool(task and task.get("status") == "open")
    can_decide = task_open and HT.can_decide(task, user_id=principal.user_id, permissions=principal.permissions,
                                             is_super_admin=principal.is_super_admin)
    d["actions"] = OS.actions_for(d["package"], can_manage=perms["can_write"], can_approve=bool(can_decide),
                                  user_id=principal.user_id, task_open=task_open, needs=d["needs"])
    return d


@router.get("/replication/options/entries/{entry_id}")
async def options_sleeve(request: Request, entry_id: str,
                         custodian_code: str = Query(default="altruist", min_length=1, max_length=40)):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            entry = await T.load_entry(conn, org_id, entry_id)
            is_options = entry["instrument_class"] in OS.OPTIONS_CLASSES
            kind = OS.KIND_FOR_CLASS.get(entry["instrument_class"])
            terms = await T.live_approved(conn, entry["id"])
            pkgs = await OS.packages_of_entry(conn, org_id, entry["id"]) if is_options else []
            current = await _detail_with_actions(conn, principal, org_id, pkgs[0]["id"]) if pkgs else None
            via = await OS.options_viability(conn, custodian_code, kind) if is_options else None
            accounts = [{"id": str(r["id"]), "account_number_masked": r["account_number_masked"]}
                        for r in await conn.fetch(
                            f"SELECT a.id, a.account_number_masked FROM {AM.T_LINK} l JOIN public.accounts a "
                            "ON a.id = l.account_id AND a.valid_to IS NULL AND a.system_to IS NULL "
                            "WHERE l.org_id = $1::uuid AND l.model_id = $2::uuid AND l.status = 'active' "
                            "AND l.valid_to IS NULL AND l.system_to IS NULL ORDER BY a.account_number_masked",
                            str(org_id), str(pkgs[0]["model_id"]))] if pkgs else []
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {
        "entry": {"id": str(entry["id"]), "display_name": entry["display_name"], "ticker": entry["ticker"],
                  "instrument_class": entry["instrument_class"], "disclosure": T.disclosure_for(entry["ticker"])},
        "is_options_class": is_options, "kind": kind,
        "terms": {"version_id": str(terms["id"]) if terms else None,
                  "missing": OS.missing_terms(terms, kind) if is_options else []},
        "packages": [OS.package_out(p) for p in pkgs], "current": current, "viability": via,
        "accounts_following": accounts})


@router.post("/replication/options/entries/{entry_id}/build", status_code=201)
async def build(request: Request, entry_id: str, body: BuildIn):
    principal = require_permission(request, BUILD_MODELS)
    require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    out = await _act(OS.build(pool, org_id=org_id, actor_id=principal.user_id, entry_id=entry_id,
                              underlying_global_security_id=body.underlying_global_security_id,
                              core_model_id=body.core_model_id, core_global_security_id=body.core_global_security_id,
                              broad_based_index=body.broad_based_index, is_flex=body.is_flex,
                              exercise_style=body.exercise_style, settlement=body.settlement,
                              multiplier=body.multiplier, fresh_period_start=body.fresh_period_start,
                              start_level_quote_id=body.start_level_quote_id, call_strike_pct=body.call_strike_pct))
    return _envelope(principal, out)


@router.get("/replication/options/packages/{package_id}")
async def get_package(request: Request, package_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            d = await _detail_with_actions(conn, principal, org_id, package_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, d)


@router.get("/replication/options/packages/{package_id}/payoff")
async def payoff(request: Request, package_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            d = await OS.payoff_lab(conn, org_id, package_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, d)


@router.get("/replication/options/packages/{package_id}/settlements")
async def settlements(request: Request, package_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            p = await OS.load_package(conn, org_id, package_id)
            rows = await OS.settlements_of(conn, org_id, p["id"])
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {"package_id": str(p["id"]), "rows": rows})


@router.post("/replication/options/packages/{package_id}/price")
async def price(request: Request, package_id: str, body: PriceIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    d = await _act(OS.price_draft(pool, org_id=org_id, actor_id=principal.user_id, package_id=package_id,
                                  start_level_quote_id=body.start_level_quote_id, cap_strike_pct=body.cap_strike_pct,
                                  call_strike_pct=body.call_strike_pct, broad_based_index=body.broad_based_index))
    return _envelope(principal, d)


@router.post("/replication/options/packages/{package_id}/submit")
async def submit(request: Request, package_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    d = await _act(OS.submit_package(pool, org_id=org_id, actor_id=principal.user_id, package_id=package_id))
    return _envelope(principal, d)


@router.post("/replication/options/packages/{package_id}/approve")
async def approve(request: Request, package_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    d = await _act(OS.decide_package(pool, org_id=org_id, actor_id=principal.user_id, package_id=package_id,
                                     outcome="approve", note=body.note))
    return _envelope(principal, d)


@router.post("/replication/options/packages/{package_id}/reject")
async def reject(request: Request, package_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    d = await _act(OS.decide_package(pool, org_id=org_id, actor_id=principal.user_id, package_id=package_id,
                                     outcome="reject", note=body.note))
    return _envelope(principal, d)


@router.get("/replication/options/quotes")
async def list_quotes(request: Request, global_security_id: str | None = Query(default=None, max_length=64),
                      kind: str | None = Query(default=None, max_length=30)):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await OS.list_quotes(conn, org_id, global_security_ids=[global_security_id] if global_security_id
                                        else None, kind=kind)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {"rows": rows})


@router.post("/replication/options/quotes", status_code=201)
async def enter_quote(request: Request, body: QuoteIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    q = await _act(OS.enter_quote(pool, org_id=org_id, actor_id=principal.user_id,
                                  global_security_id=body.global_security_id, kind=body.kind, value=body.value,
                                  source=body.source, as_of=body.as_of))
    return _envelope(principal, {"quote": q})


@router.get("/replication/options/contracts")
async def list_contracts(request: Request, underlying_global_security_id: str | None = Query(default=None,
                                                                                           max_length=64)):
    principal = require_permission(request, VIEW)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await OS.list_contracts(conn, underlying_global_security_id=underlying_global_security_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {"rows": rows})


@router.post("/replication/options/contracts", status_code=201)
async def define_contract(request: Request, body: ContractIn):
    principal = require_permission(request, MANAGE)
    pool = await get_pool()
    c = await _act(OS.find_or_create_contract(
        pool, actor_id=principal.user_id, underlying_global_security_id=body.underlying_global_security_id,
        option_type=body.option_type, strike=body.strike, expiry=body.expiry, exercise_style=body.exercise_style,
        settlement=body.settlement, is_flex=body.is_flex, multiplier=body.multiplier, occ_symbol=body.occ_symbol,
        exchange_listed=body.exchange_listed, broad_based_index=body.broad_based_index, is_mini=body.is_mini,
        mini_ratio=body.mini_ratio))
    return _envelope(principal, {"contract": OS.contract_out(c)})


@router.post("/replication/options/accounts/{account_id}/proposals", status_code=201)
async def propose(request: Request, account_id: str, body: ProposalIn):
    principal = require_permission(request, MANAGE_PROPOSALS)
    org_id = get_org_id(request)
    pool = await get_pool()
    d = await _act(OP.generate(pool, org_id=org_id, account_id=account_id, package_id=body.package_id,
                               maker_id=principal.user_id, action=body.action, executor_type=body.executor_type,
                               executor_name=body.executor_name, executor_contact=body.executor_contact))
    return _envelope(principal, {"proposal": d})


@router.get("/replication/options/proposals/{proposal_id}/gate")
async def gate(request: Request, proposal_id: str):
    principal = require_permission(request, VIEW_PROPOSALS)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            p = await RP.load(conn, org_id, proposal_id)
            s = await G.stored(conn, org_id, p["id"])
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {"proposal_id": str(p["id"]), "proposal_kind": p.get("proposal_kind"), **s})


@router.post("/replication/options/proposals/{proposal_id}/gate")
async def rerun_gate(request: Request, proposal_id: str):
    principal = require_permission(request, MANAGE_PROPOSALS)
    org_id = get_org_id(request)
    pool = await get_pool()
    s = await _act(G.run_gate(pool, org_id=org_id, proposal_id=proposal_id, actor_id=principal.user_id))
    return _envelope(principal, {"proposal_id": proposal_id, **s})


@router.post("/replication/options/instructions/{instruction_id}/book")
async def book(request: Request, instruction_id: str):
    principal = require_permission(request, MANAGE_EXECUTION)
    org_id = get_org_id(request)
    pool = await get_pool()
    out = await _act(OS.book_fills(pool, org_id=org_id, actor_id=principal.user_id, instruction_id=instruction_id))
    return _envelope(principal, out)
