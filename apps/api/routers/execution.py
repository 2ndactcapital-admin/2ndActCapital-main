"""REP2 — execution instructions (/execution).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_execution     GET  /execution/instructions                   list + envelope
                     GET  /execution/instructions/{id}              detail + timeline + task + actions
                     POST /execution/instructions/{id}/letter       letter of instruction PDF (on demand)
                     POST /execution/instructions/{id}/settlement   rep7b: DVP settlement instruction PDF (on demand)
                     GET  /execution/accounts                       account picker (masked numbers)
                     GET  /execution/accounts/{id}/lots             open lots of the account (lot picker)
                     GET  /execution/accounts/{id}/pending          pending differences
                     GET  /execution/securities                     security-master search
  manage_execution   POST  /execution/instructions                  route a trade list → draft instructions
                     PATCH /execution/instructions/{id}             edit a draft
                     POST  /execution/instructions/{id}/submit      draft → awaiting approval (opens the task)
                     POST  /execution/instructions/{id}/sent        mark sent (channel)
                     POST  /execution/instructions/{id}/fills       enter a fill
                     POST  /execution/instructions/{id}/reconcile   reconcile
                     POST  /execution/instructions/{id}/reconcile-held-away   rep7b: reconcile to a held-away record
                     POST  /execution/instructions/{id}/close       close
                     POST  /execution/instructions/{id}/cancel      cancel (reason)
  approve_execution  POST  /execution/instructions/{id}/approve     (never for a maker — services.execution)
                     POST  /execution/instructions/{id}/reject

Super Admin first (services.authz). org_id is only ever the caller's
(get_org_id) — never a body or path value; every request model forbids extras.
Another org's instruction is a 404 (RLS + an explicit org predicate).
Quantities and prices travel as decimal STRINGS; a JSON float is a 422.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from routers.entities import get_org_id
from services import execution as X
from services import human_tasks as HT
from services.authz import require_permission
from services.custody.trading import LotInstruction, PriceGuidance, TradeList, _decimal, route_trade_list
from services.database import get_pool

router = APIRouter(tags=["execution"])

VIEW, MANAGE, APPROVE = X.VIEW, X.MANAGE, X.APPROVE


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InstructionEdit(_Body):
    quantity: Decimal | None = None
    notional: Decimal | None = None
    price_guidance: PriceGuidance | None = None
    wash_sale_notes: str | None = Field(default=None, max_length=4000)
    executor_type: Literal["custodian_desk", "broker", "member", "other_adviser", "trader"] | None = None
    executor_name: str | None = Field(default=None, max_length=300)
    executor_contact: str | None = Field(default=None, max_length=500)
    execute_by: date | None = None
    if_not_filled: Literal["cancel", "carry", "ask"] | None = None
    notes: str | None = Field(default=None, max_length=4000)
    lot_instructions: list[LotInstruction] | None = Field(default=None, max_length=200)

    @field_validator("quantity", "notional", mode="before")
    @classmethod
    def _amt(cls, v, info):
        return _decimal(v, info.field_name)


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


class Sent(_Body):
    channel: str = Field(min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=2000)


class Fill(_Body):
    quantity: Decimal
    price: Decimal
    filled_at: datetime
    lots: list[LotInstruction] = Field(default_factory=list, max_length=200)
    completes: bool = False
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("quantity", "price", mode="before")
    @classmethod
    def _amt(cls, v, info):
        d = _decimal(v, info.field_name)
        if d is None:
            raise ValueError(f"{info.field_name} is required")
        return d


class Reconcile(_Body):
    method: Literal["custodian_data", "manual"]
    reference: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=2000)
    transaction_ids: list[UUID] = Field(default_factory=list, max_length=100)


class ReconcileHeldAway(_Body):
    record_id: UUID
    note: str | None = Field(default=None, max_length=2000)


class Close(_Body):
    note: str | None = Field(default=None, max_length=2000)


class Cancel(_Body):
    reason: str = Field(min_length=1, max_length=2000)


class Letter(_Body):
    include_full_number: bool = False
    full_account_number: str | None = Field(default=None, max_length=64)


def _raise(exc: Exception):
    if isinstance(exc, (X.ExecutionNotFound, HT.HumanTaskNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (X.MakerCheckerViolation, HT.MakerCheckerError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, HT.HumanTaskPermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (X.ExecutionConflict, HT.HumanTaskConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (X.ExecutionError, HT.HumanTaskError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.CheckViolationError):
        raise HTTPException(status_code=422, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
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
    v = X.vocabularies()
    v["editable"] = list(X.EDITABLE_FIELDS) if perms["can_write"] else []
    return v


def _lots_out(lots: list[dict]) -> list[dict]:
    return [{**lot, "quantity": None if lot["quantity"] is None else str(lot["quantity"]),
             "adjusted_cost": None if lot["adjusted_cost"] is None else str(lot["adjusted_cost"]),
             "original_cost": None if lot["original_cost"] is None else str(lot["original_cost"])} for lot in lots]


@router.get("/execution/instructions")
async def list_instructions(request: Request, status: str | None = Query(default=None, max_length=40),
                            account_id: UUID | None = None,
                            executor_type: str | None = Query(default=None, max_length=40),
                            due_from: date | None = None, due_to: date | None = None):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await X.list_instructions(conn, org_id, status=status, account_id=account_id,
                                             executor_type=executor_type, due_from=due_from, due_to=due_to)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"rows": rows, "permissions": perms, "vocabularies": _vocab(perms)}


@router.post("/execution/instructions", status_code=201)
async def create_instructions(request: Request, body: TradeList):
    """Route a trade list (one or more trades for one account). In this build
    every route ends as DRAFT execution instructions — the result says why."""
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            account = await X.load_account(conn, org_id, body.account_id)
        result = await route_trade_list(pool, org_id=org_id, actor_id=principal.user_id, account=account,
                                        trade_list=body)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"routing": result.as_dict()}


@router.get("/execution/instructions/{instruction_id}")
async def get_instruction(request: Request, instruction_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            d = await X.detail(conn, org_id, instruction_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    open_task = next((t for t in d["tasks"] if t["status"] == "open"), None)
    is_maker = principal.user_id in set(d["maker_ids"])
    perms = {**perms, "is_maker": is_maker,
             "can_decide": bool(open_task) and HT.can_decide(open_task, user_id=principal.user_id,
                                                              permissions=principal.permissions,
                                                              is_super_admin=principal.is_super_admin)
             and not is_maker}
    actions = X.actions_for({**d, "maker_ids": d["maker_ids"]}, can_write=perms["can_write"],
                            can_approve=perms["can_decide"], user_id=principal.user_id,
                            task_open=open_task is not None)
    return {"instruction": d, "permissions": perms, "vocabularies": _vocab(perms), "actions": actions}


@router.patch("/execution/instructions/{instruction_id}")
async def edit_instruction(request: Request, instruction_id: str, body: InstructionEdit):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    sent = body.model_fields_set
    changes: dict = {}
    for k in ("quantity", "notional", "wash_sale_notes", "executor_type", "executor_name", "executor_contact",
              "execute_by", "if_not_filled", "notes"):
        if k in sent:
            changes[k] = getattr(body, k)
    if "quantity" in sent and body.quantity is not None and "notional" not in sent:
        changes["notional"] = None
    if "notional" in sent and body.notional is not None and "quantity" not in sent:
        changes["quantity"] = None
    if "price_guidance" in sent and body.price_guidance is not None:
        pg = body.price_guidance
        changes.update({"price_kind": pg.kind, "limit_price": pg.limit_price, "range_low": pg.range_low,
                        "range_high": pg.range_high, "reference_price": pg.reference_price,
                        "reference_at": pg.reference_at})
    for k in ("executor_type", "execute_by", "if_not_filled"):
        if k in changes and changes[k] is None:
            raise HTTPException(status_code=422, detail=f"{k} cannot be cleared")
    lots = None
    if "lot_instructions" in sent:
        lots = [(str(li.tax_lot_id), li.quantity) for li in (body.lot_instructions or [])]
    if not changes and lots is None:
        raise HTTPException(status_code=422, detail="nothing to change")
    pool = await get_pool()
    try:
        row = await X.edit_instruction(pool, org_id=org_id, instruction_id=instruction_id,
                                       actor_id=principal.user_id, changes=changes, lots=lots)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"instruction": X.instruction_out(row)}


async def _act(coro):
    try:
        row = await coro
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    if isinstance(row, tuple):
        row = row[0]
    return {"instruction": X.instruction_out(row)}


@router.post("/execution/instructions/{instruction_id}/submit")
async def submit(request: Request, instruction_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(X.submit(pool, org_id=org_id, instruction_id=instruction_id, actor_id=principal.user_id))


@router.post("/execution/instructions/{instruction_id}/approve")
async def approve(request: Request, instruction_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(X.decide(pool, org_id=org_id, instruction_id=instruction_id, actor_id=principal.user_id,
                               outcome="approve", note=body.note))


@router.post("/execution/instructions/{instruction_id}/reject")
async def reject(request: Request, instruction_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(X.decide(pool, org_id=org_id, instruction_id=instruction_id, actor_id=principal.user_id,
                               outcome="reject", note=body.note))


@router.post("/execution/instructions/{instruction_id}/sent")
async def mark_sent(request: Request, instruction_id: str, body: Sent):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(X.mark_sent(pool, org_id=org_id, instruction_id=instruction_id, actor_id=principal.user_id,
                                  channel=body.channel, note=body.note))


@router.post("/execution/instructions/{instruction_id}/fills")
async def enter_fill(request: Request, instruction_id: str, body: Fill):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(X.enter_fill(pool, org_id=org_id, instruction_id=instruction_id, actor_id=principal.user_id,
                                   quantity=body.quantity, price=body.price, filled_at=body.filled_at,
                                   lots=[(str(li.tax_lot_id), li.quantity) for li in body.lots],
                                   completes=body.completes, note=body.note))


@router.post("/execution/instructions/{instruction_id}/reconcile")
async def reconcile(request: Request, instruction_id: str, body: Reconcile):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(X.reconcile(pool, org_id=org_id, instruction_id=instruction_id, actor_id=principal.user_id,
                                  method=body.method, reference=body.reference, note=body.note,
                                  transaction_ids=[str(t) for t in body.transaction_ids]))


@router.post("/execution/instructions/{instruction_id}/close")
async def close(request: Request, instruction_id: str, body: Close):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(X.close(pool, org_id=org_id, instruction_id=instruction_id, actor_id=principal.user_id,
                              note=body.note))


@router.post("/execution/instructions/{instruction_id}/cancel")
async def cancel(request: Request, instruction_id: str, body: Cancel):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(X.cancel(pool, org_id=org_id, instruction_id=instruction_id, actor_id=principal.user_id,
                               reason=body.reason))


@router.post("/execution/instructions/{instruction_id}/letter")
async def letter(request: Request, instruction_id: str, body: Letter):
    """PDF on demand. Masked account number unless the caller explicitly
    chooses to include the full number AND types it (checked against the
    account's hash; the choice is logged, the digits never are)."""
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        pdf, filename = await X.letter(pool, org_id=org_id, instruction_id=instruction_id,
                                       actor_id=principal.user_id, include_full_number=body.include_full_number,
                                       full_account_number=body.full_account_number)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "Cache-Control": "no-store"})


@router.post("/execution/instructions/{instruction_id}/settlement")
async def settlement(request: Request, instruction_id: str, body: Letter):
    """rep7b — the DVP settlement instruction PDF (managed, another custodian),
    on demand. Masked account numbers unless the full number is explicitly
    chosen and typed (rep2's rule)."""
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        pdf, filename = await X.settlement_letter(pool, org_id=org_id, instruction_id=instruction_id,
                                                  actor_id=principal.user_id,
                                                  include_full_number=body.include_full_number,
                                                  full_account_number=body.full_account_number)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "Cache-Control": "no-store"})


@router.post("/execution/instructions/{instruction_id}/reconcile-held-away")
async def reconcile_held_away(request: Request, instruction_id: str, body: ReconcileHeldAway):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(X.reconcile_from_held_away(pool, org_id=org_id, instruction_id=instruction_id,
                                                 actor_id=principal.user_id, record_id=body.record_id,
                                                 note=body.note))


@router.get("/execution/accounts")
async def accounts(request: Request):
    require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await X.list_accounts(conn, org_id)
    return {"rows": rows}


@router.get("/execution/accounts/{account_id}/lots")
async def account_lots(request: Request, account_id: str, security_id: UUID | None = None):
    require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await X.load_account(conn, org_id, account_id)
            lots = await X.account_lots(conn, org_id, account_id, security_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"rows": _lots_out(lots)}


@router.get("/execution/accounts/{account_id}/pending")
async def account_pending(request: Request, account_id: str):
    require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await X.load_account(conn, org_id, account_id)
            rows = await X.pending_differences(conn, org_id, account_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"rows": [{**r, "pending_quantity": str(r["pending_quantity"]),
                      "pending_notional": str(r["pending_notional"])} for r in rows]}


@router.get("/execution/securities")
async def securities(request: Request, q: str = Query(min_length=2, max_length=80)):
    require_permission(request, VIEW)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await X.search_securities(conn, q)
    return {"rows": rows}
