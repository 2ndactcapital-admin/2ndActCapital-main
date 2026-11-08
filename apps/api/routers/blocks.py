"""REP9 — block trading, allocation and DVP settlement (/execution/blocks).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_blocks      GET  /execution/blocks                                 list + envelope
                   GET  /execution/blocks/candidates                      eligible / ineligible (reasons) + suggestions
                   POST /execution/blocks/preview                         the allocation a block would fix (no write)
                   GET  /execution/blocks/routers                         execution routers (FIX: not connected)
                   GET  /execution/blocks/settings/policies               allocation policy versions
                   GET  /execution/blocks/settings/execution-accounts     executing (omnibus) accounts
                   GET  /execution/blocks/{id}                            detail + timeline + tasks + actions
                   POST /execution/blocks/{id}/sheet                      order execution sheet PDF (on demand, logged)
  view_execution   POST /execution/blocks/instructions/{id}/sheet         the sheet for ONE account instruction (rep2's
                                                                           letter gate — every broker / desk / trader
                                                                           instruction offers it)
  manage_blocks    POST  /execution/blocks                                build a draft block
                   POST  /execution/blocks/{id}/submit                    draft → awaiting approval (opens the task)
                   POST  /execution/blocks/{id}/sent                      mark sent (channel, person)
                   POST  /execution/blocks/{id}/fills                     enter a block fill
                   POST  /execution/blocks/{id}/allocate                  book the allocation at the average price
                   POST  /execution/blocks/{id}/allocations/{aid}/settlement        settlement status
                   POST  /execution/blocks/{id}/allocations/{aid}/settlement-match  settled, matched to a record
                   POST  /execution/blocks/{id}/corrections               ask for an allocation correction
                   POST  /execution/blocks/{id}/charges                   commission / fee
                   POST  /execution/blocks/{id}/charges/{cid}/close       close a charge (reason)
                   POST  /execution/blocks/{id}/close                     settled → closed
                   POST  /execution/blocks/{id}/cancel                    cancel (reason)
                   POST  /execution/blocks/settings/policies              a new policy version (draft)
                   POST  /execution/blocks/settings/execution-accounts    add an executing account
                   PATCH /execution/blocks/settings/execution-accounts/{id}   change / retire one
  approve_blocks   POST  /execution/blocks/{id}/approve | /reject         (never a maker — services.blocks)
                   POST  /execution/blocks/corrections/{cid}/approve | /reject
                   POST  /execution/blocks/settings/policies/{pid}/approve | /reject

Super Admin first (services.authz). org_id is only ever the caller's
(get_org_id) — never a body or path value; every request model forbids extras.
Another org's block is a 404 (RLS + an explicit org predicate). Quantities and
prices travel as decimal STRINGS; a JSON float is a 422.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from routers.entities import get_org_id
from services import blocks as BK
from services import execution as X
from services import human_tasks as HT
from services.authz import require_permission
from services.custody.trading import _decimal
from services.database import get_pool
from services.execution import VIEW as VIEW_EXECUTION
from services import sod  # S30 — the ONE maker-checker rule

router = APIRouter(tags=["blocks"])

VIEW, MANAGE, APPROVE = BK.VIEW, BK.MANAGE, BK.APPROVE


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _req_dec(v, name):
    d = _decimal(v, name)
    if d is None:
        raise ValueError(f"{name} is required")
    return d


class Selection(_Body):
    instruction_ids: list[UUID] = Field(min_length=1, max_length=500)
    reference_price: Decimal | None = None
    reference_at: datetime | None = None

    @field_validator("reference_price", mode="before")
    @classmethod
    def _p(cls, v, info):
        return _decimal(v, info.field_name)


class BuildBlock(_Body):
    instruction_ids: list[UUID] = Field(min_length=1, max_length=500)
    route: Literal["manual", "fix"] = "manual"
    manual_reason: str | None = Field(default=None, max_length=2000)
    execution_account_id: UUID | None = None
    executor_name: str | None = Field(default=None, max_length=300)
    executor_contact: str | None = Field(default=None, max_length=500)
    price_kind: Literal["market", "limit", "range"] = "market"
    limit_price: Decimal | None = None
    range_low: Decimal | None = None
    range_high: Decimal | None = None
    reference_price: Decimal | None = None
    reference_at: datetime | None = None
    if_not_filled: Literal["cancel", "carry", "ask"] = "ask"
    cross_reason: str | None = Field(default=None, max_length=2000)
    firm_contact: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("limit_price", "range_low", "range_high", "reference_price", mode="before")
    @classmethod
    def _p(cls, v, info):
        return _decimal(v, info.field_name)


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


class Sent(_Body):
    channel: str = Field(min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=2000)


class BlockFill(_Body):
    quantity: Decimal
    price: Decimal
    filled_at: datetime
    venue: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("quantity", "price", mode="before")
    @classmethod
    def _amt(cls, v, info):
        return _req_dec(v, info.field_name)


class Allocate(_Body):
    note: str | None = Field(default=None, max_length=2000)


class Settlement(_Body):
    status: Literal["pending", "affirmed", "settled", "failed"]
    note: str | None = Field(default=None, max_length=2000)
    fail_reason: str | None = Field(default=None, max_length=2000)
    method: Literal["manual", "custodian_data"] | None = None
    reference: str | None = Field(default=None, max_length=500)


class SettlementMatch(_Body):
    record_kind: Literal["transaction", "held_away_record"]
    record_id: UUID
    note: str | None = Field(default=None, max_length=2000)


class CorrectionLine(_Body):
    allocation_id: UUID
    quantity: Decimal

    @field_validator("quantity", mode="before")
    @classmethod
    def _q(cls, v, info):
        return _req_dec(v, info.field_name)


class Correction(_Body):
    changes: list[CorrectionLine] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=2000)


class Charge(_Body):
    kind: Literal["commission", "fee", "other"]
    amount: Decimal
    currency_code: str = Field(default="USD", min_length=3, max_length=3)
    payee: str | None = Field(default=None, max_length=300)
    trade_away: bool = False
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("amount", mode="before")
    @classmethod
    def _a(cls, v, info):
        return _req_dec(v, info.field_name)


class Reason(_Body):
    reason: str = Field(min_length=1, max_length=2000)


class Close(_Body):
    note: str | None = Field(default=None, max_length=2000)


class Sheet(_Body):
    manual_reason: str | None = Field(default=None, max_length=2000)


class PolicyVersion(_Body):
    minimum_allocation: Decimal
    policy_text: str = Field(min_length=1, max_length=20000)
    adv_reference: str | None = Field(default=None, max_length=500)
    reason: str = Field(min_length=1, max_length=2000)
    method: Literal["pro_rata_requested"] = "pro_rata_requested"

    @field_validator("minimum_allocation", mode="before")
    @classmethod
    def _m(cls, v, info):
        return _req_dec(v, info.field_name)


class ExecutionAccountNew(_Body):
    broker_name: str = Field(min_length=1, max_length=300)
    reference_last4: str = Field(min_length=1, max_length=4)
    dvp_capable: bool
    contact: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class ExecutionAccountEdit(_Body):
    broker_name: str | None = Field(default=None, max_length=300)
    dvp_capable: bool | None = None
    contact: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "retired"] | None = None


def _raise(exc: Exception):
    if isinstance(exc, (BK.BlockNotFound, X.ExecutionNotFound, HT.HumanTaskNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (BK.MakerCheckerViolation, X.MakerCheckerViolation, HT.MakerCheckerError,
                        HT.HumanTaskPermissionError, PermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (BK.BlockConflict, X.ExecutionConflict, HT.HumanTaskConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (BK.BlockError, X.ExecutionError, HT.HumanTaskError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.CheckViolationError):
        raise HTTPException(status_code=422, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, (asyncpg.InsufficientPrivilegeError, asyncpg.UniqueViolationError)):
        raise HTTPException(status_code=409, detail=str(exc).split("\n")[0]) from exc
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
    v = BK.vocabularies()
    v["editable"] = ["instruction_ids", "route", "execution_account_id", "price_kind", "if_not_filled"] \
        if perms["can_write"] is True else []
    return v


async def _act(coro):
    try:
        row = await coro
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"block": BK.block_out(row) if isinstance(row, dict) and "total_quantity" in row else row}


def _pdf(pdf: bytes, filename: str) -> Response:
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


# ── Static paths first (they would otherwise match /{block_id}) ─────────────
@router.get("/execution/blocks")
async def list_blocks(request: Request, status: str | None = Query(default=None, max_length=40)):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await BK.list_blocks(conn, org_id, status=status)
            policy = await BK.current_policy(conn, org_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"rows": rows, "policy": BK.policy_out(policy), "permissions": perms, "vocabularies": _vocab(perms),
            "actions": ["build"] if perms["can_write"] is True else []}


@router.get("/execution/blocks/candidates")
async def candidates(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            c = await BK.candidates(conn, org_id)
            policy = await BK.current_policy(conn, org_id)
            accounts = await BK.list_execution_accounts(conn, org_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {**c, "policy": BK.policy_out(policy), "execution_accounts": [a for a in accounts if a["status"] == "active"],
            "permissions": perms, "vocabularies": _vocab(perms)}


@router.post("/execution/blocks/preview")
async def preview(request: Request, body: Selection):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            p = await BK.preview(conn, org_id, body.instruction_ids, typed_reference=body.reference_price,
                                 typed_at=body.reference_at)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"preview": p, "permissions": perms, "vocabularies": _vocab(perms)}


@router.get("/execution/blocks/routers")
async def routers_(request: Request):
    principal = require_permission(request, VIEW)
    perms = _permissions(principal)
    return {"rows": BK.RT.routers(), "permissions": perms, "vocabularies": _vocab(perms)}


@router.get("/execution/blocks/settings/policies")
async def policies(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await BK.list_policies(conn, org_id)
        tasks = await HT.list_tasks(conn, org_id, status="open", kind=BK.TASK_POLICY, limit=5)
    actions = {}
    for r in rows:
        acts = []
        if r["status"] == "draft" and perms["can_approve"] is True and sod.may_check_display(principal.user_id, [r["maker_id"]]) \
                and any(t["subject_id"] == r["id"] for t in tasks):
            acts += ["approve", "reject"]
        actions[r["id"]] = acts
    return {"rows": rows, "actions": actions, "permissions": perms, "vocabularies": _vocab(perms),
            "can_request": perms["can_write"] is True and not any(r["status"] == "draft" for r in rows)}


@router.post("/execution/blocks/settings/policies", status_code=201)
async def request_policy(request: Request, body: PolicyVersion):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        out = await BK.request_policy(pool, org_id=org_id, actor_id=principal.user_id,
                                      minimum_allocation=body.minimum_allocation, policy_text=body.policy_text,
                                      adv_reference=body.adv_reference, reason=body.reason, method=body.method)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"policy": out}


@router.post("/execution/blocks/settings/policies/{policy_id}/approve")
async def approve_policy(request: Request, policy_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        out = await BK.decide_policy(pool, org_id=org_id, actor_id=principal.user_id, policy_id=policy_id,
                                     outcome="approve", note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"policy": out}


@router.post("/execution/blocks/settings/policies/{policy_id}/reject")
async def reject_policy(request: Request, policy_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        out = await BK.decide_policy(pool, org_id=org_id, actor_id=principal.user_id, policy_id=policy_id,
                                     outcome="reject", note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"policy": out}


@router.get("/execution/blocks/settings/execution-accounts")
async def execution_accounts(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await BK.list_execution_accounts(conn, org_id)
    return {"rows": rows, "permissions": perms, "vocabularies": _vocab(perms)}


@router.post("/execution/blocks/settings/execution-accounts", status_code=201)
async def add_execution_account(request: Request, body: ExecutionAccountNew):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        out = await BK.create_execution_account(pool, org_id=org_id, actor_id=principal.user_id,
                                                broker_name=body.broker_name, reference_last4=body.reference_last4,
                                                dvp_capable=body.dvp_capable, contact=body.contact, notes=body.notes)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"execution_account": out}


@router.patch("/execution/blocks/settings/execution-accounts/{account_id}")
async def edit_execution_account(request: Request, account_id: str, body: ExecutionAccountEdit):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if not changes:
        raise HTTPException(status_code=422, detail="nothing to change")
    try:
        out = await BK.update_execution_account(pool, org_id=org_id, actor_id=principal.user_id,
                                                account_id=account_id, changes=changes)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"execution_account": out}


@router.post("/execution/blocks/instructions/{instruction_id}/sheet")
async def instruction_sheet(request: Request, instruction_id: str, body: Sheet):
    """The order execution sheet for ONE account instruction (broker / desk /
    trader) — the manual route on every trade. Recorded on its timeline, with
    the reason when an automated route was available."""
    principal = require_permission(request, VIEW_EXECUTION)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        pdf, filename = await BK.instruction_sheet(pool, org_id=org_id, instruction_id=instruction_id,
                                                   actor_id=principal.user_id, manual_reason=body.manual_reason)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _pdf(pdf, filename)


@router.post("/execution/blocks/corrections/{correction_id}/approve")
async def approve_correction(request: Request, correction_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        out = await BK.decide_correction(pool, org_id=org_id, actor_id=principal.user_id,
                                         correction_id=correction_id, outcome="approve", note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"correction": out}


@router.post("/execution/blocks/corrections/{correction_id}/reject")
async def reject_correction(request: Request, correction_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        out = await BK.decide_correction(pool, org_id=org_id, actor_id=principal.user_id,
                                         correction_id=correction_id, outcome="reject", note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"correction": out}


# ── Blocks ──────────────────────────────────────────────────────────────────
@router.post("/execution/blocks", status_code=201)
async def build(request: Request, body: BuildBlock):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(BK.build_block(
        pool, org_id=org_id, actor_id=principal.user_id, instruction_ids=body.instruction_ids, route=body.route,
        manual_reason=body.manual_reason, execution_account_id=body.execution_account_id,
        executor_name=body.executor_name, executor_contact=body.executor_contact, price_kind=body.price_kind,
        limit_price=body.limit_price, range_low=body.range_low, range_high=body.range_high,
        reference_price_typed=body.reference_price, reference_at=body.reference_at, if_not_filled=body.if_not_filled,
        cross_reason=body.cross_reason, firm_contact=body.firm_contact, notes=body.notes))


@router.get("/execution/blocks/{block_id}")
async def get_block(request: Request, block_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            d = await BK.detail(conn, org_id, block_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    open_task = next((t for t in d["tasks"] if t["status"] == "open"), None)
    is_maker = not sod.may_check_display(principal.user_id, d["maker_ids"])
    can_decide = bool(open_task) and HT.can_decide(open_task, user_id=principal.user_id,
                                                   permissions=principal.permissions,
                                                   is_super_admin=principal.is_super_admin) and not is_maker
    perms = {**perms, "is_maker": is_maker, "can_decide": can_decide}
    actions = BK.actions_for(d, can_write=perms["can_write"] is True, can_approve=can_decide,
                             user_id=principal.user_id, task_open=open_task is not None)
    if perms["can_approve"] is not True:
        actions = [a for a in actions if a != "decide_correction"]
    return {"block": d, "permissions": perms, "vocabularies": _vocab(perms), "actions": actions}


@router.post("/execution/blocks/{block_id}/submit")
async def submit(request: Request, block_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(BK.submit(pool, org_id=org_id, block_id=block_id, actor_id=principal.user_id))


@router.post("/execution/blocks/{block_id}/approve")
async def approve(request: Request, block_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(BK.decide(pool, org_id=org_id, block_id=block_id, actor_id=principal.user_id,
                                outcome="approve", note=body.note))


@router.post("/execution/blocks/{block_id}/reject")
async def reject(request: Request, block_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(BK.decide(pool, org_id=org_id, block_id=block_id, actor_id=principal.user_id,
                                outcome="reject", note=body.note))


@router.post("/execution/blocks/{block_id}/sent")
async def sent(request: Request, block_id: str, body: Sent):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(BK.mark_sent(pool, org_id=org_id, block_id=block_id, actor_id=principal.user_id,
                                   channel=body.channel, note=body.note))


@router.post("/execution/blocks/{block_id}/fills")
async def fill(request: Request, block_id: str, body: BlockFill):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(BK.enter_fill(pool, org_id=org_id, block_id=block_id, actor_id=principal.user_id,
                                    quantity=body.quantity, price=body.price, filled_at=body.filled_at,
                                    venue=body.venue, note=body.note))


@router.post("/execution/blocks/{block_id}/allocate")
async def allocate(request: Request, block_id: str, body: Allocate):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(BK.allocate(pool, org_id=org_id, block_id=block_id, actor_id=principal.user_id,
                                  note=body.note))


@router.post("/execution/blocks/{block_id}/allocations/{allocation_id}/settlement")
async def settlement(request: Request, block_id: str, allocation_id: str, body: Settlement):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(BK.set_settlement(pool, org_id=org_id, block_id=block_id, allocation_id=allocation_id,
                                        actor_id=principal.user_id, status=body.status, note=body.note,
                                        fail_reason=body.fail_reason, method=body.method, reference=body.reference))


@router.post("/execution/blocks/{block_id}/allocations/{allocation_id}/settlement-match")
async def settlement_match(request: Request, block_id: str, allocation_id: str, body: SettlementMatch):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(BK.match_settlement(pool, org_id=org_id, block_id=block_id, allocation_id=allocation_id,
                                          actor_id=principal.user_id, record_kind=body.record_kind,
                                          record_id=body.record_id, note=body.note))


@router.post("/execution/blocks/{block_id}/corrections", status_code=201)
async def correction(request: Request, block_id: str, body: Correction):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        out = await BK.request_correction(pool, org_id=org_id, block_id=block_id, actor_id=principal.user_id,
                                          changes=[{"allocation_id": c.allocation_id, "quantity": c.quantity}
                                                   for c in body.changes], reason=body.reason)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"correction": out}


@router.post("/execution/blocks/{block_id}/charges", status_code=201)
async def charge(request: Request, block_id: str, body: Charge):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        out = await BK.add_charge(pool, org_id=org_id, block_id=block_id, actor_id=principal.user_id, kind=body.kind,
                                  amount=body.amount, currency_code=body.currency_code, payee=body.payee,
                                  trade_away=body.trade_away, note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"charge": out}


@router.post("/execution/blocks/{block_id}/charges/{charge_id}/close")
async def close_charge(request: Request, block_id: str, charge_id: str, body: Reason):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        out = await BK.close_charge(pool, org_id=org_id, block_id=block_id, charge_id=charge_id,
                                    actor_id=principal.user_id, reason=body.reason)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"charge": out}


@router.post("/execution/blocks/{block_id}/close")
async def close(request: Request, block_id: str, body: Close):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(BK.close(pool, org_id=org_id, block_id=block_id, actor_id=principal.user_id, note=body.note))


@router.post("/execution/blocks/{block_id}/cancel")
async def cancel(request: Request, block_id: str, body: Reason):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(BK.cancel(pool, org_id=org_id, block_id=block_id, actor_id=principal.user_id,
                                reason=body.reason))


@router.post("/execution/blocks/{block_id}/sheet")
async def sheet(request: Request, block_id: str):
    """The order execution sheet PDF, on demand (never stored); logged."""
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        pdf, filename = await BK.block_sheet(pool, org_id=org_id, block_id=block_id, actor_id=principal.user_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _pdf(pdf, filename)
