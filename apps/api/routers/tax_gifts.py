"""REP10 — charitable lot gifting (/tax/gifts/…).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_tax     GET  /tax/gifts                              gift proposals + envelope
               GET  /tax/gifts/donees                       donees
               GET  /tax/gifts/candidates                   a subject's candidate lots, ordered, with exclusions
               GET  /tax/gifts/{gift_id}                    one gift: lots, comparison (estimate), reminders, actions
  manage_tax   POST /tax/gifts/donees                       add a donee (account stored masked + hash)
               POST /tax/gifts/donees/{donee_id}/retire     retire a donee
               POST /tax/gifts                              propose a gift (draft; lots or a target amount)
               POST /tax/gifts/{gift_id}/submit             draft → awaiting approval (opens the task)
               POST /tax/gifts/{gift_id}/letter             the transfer letter PDF (on demand, never stored)
               POST /tax/gifts/{gift_id}/sent               mark the letter sent (channel)
               POST /tax/gifts/{gift_id}/received           the donee's account received the shares (= gift date)
               POST /tax/gifts/{gift_id}/acknowledged       the donee's receipt noted
               POST /tax/gifts/{gift_id}/rebuy              the rebuy as rep5 proposals
               POST /tax/gifts/{gift_id}/cancel             cancel (a reason)
  approve_tax  POST /tax/gifts/{gift_id}/approve | /reject  (never the maker)

Delivery is MANUAL, always. Every tax figure is an estimate with its rates and as-of. Super Admin first; org_id
only from the session; extra='forbid'; amounts are decimal strings (a JSON float is a 422).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from routers.entities import get_org_id
from services import gifts as G
from services import human_tasks as HT
from services import rep10_common as C
from services.authz import require_permission
from services.custody.trading import _decimal
from services.database import get_pool

router = APIRouter(tags=["tax-gifts"])
VIEW, MANAGE, APPROVE = G.VIEW, G.MANAGE, G.APPROVE


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Donee(_Body):
    name: str = Field(min_length=1, max_length=300)
    kind: Literal["public_charity", "daf", "private_foundation"]
    ein: str = Field(min_length=10, max_length=10)
    receiving_broker: str = Field(min_length=1, max_length=300)
    dtc_participant: str = Field(min_length=4, max_length=4)
    account_number: str = Field(min_length=1, max_length=64)
    contact: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class GiftLot(_Body):
    tax_lot_id: UUID
    quantity: Decimal | None = None

    @field_validator("quantity", mode="before")
    @classmethod
    def _q(cls, v, info):
        return _decimal(v, info.field_name)


class Gift(_Body):
    subject_type: Literal["household", "entity"]
    subject_id: UUID
    member_entity_id: UUID
    donee_id: UUID
    tax_year: int = Field(ge=2000, le=2200)
    target_amount: Decimal | None = None
    lots: list[GiftLot] | None = Field(default=None, max_length=500)
    agi_estimate: Decimal | None = None
    agi_source: str | None = Field(default=None, max_length=500)
    agi_as_of: date | None = None
    itemizes: Literal["yes", "no"]
    itemizes_source: str = Field(min_length=1, max_length=500)
    itemizes_as_of: date
    prior_carryforward: Decimal = Decimal(0)
    prior_carryforward_source: str = Field(min_length=1, max_length=500)
    prior_carryforward_as_of: date
    rebuy: Literal["none", "same_security", "replica_consistent"] = "none"
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("target_amount", "agi_estimate", "prior_carryforward", mode="before")
    @classmethod
    def _d(cls, v, info):
        d = _decimal(v, info.field_name)
        return Decimal(0) if d is None and info.field_name == "prior_carryforward" else d


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


class Sent(_Body):
    channel: str = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=2000)


class ReceiptPrice(_Body):
    price: Decimal
    source: str = Field(min_length=1, max_length=300)

    @field_validator("price", mode="before")
    @classmethod
    def _p(cls, v, info):
        d = _decimal(v, info.field_name)
        if d is None or d <= 0:
            raise ValueError("a receipt price is a decimal above zero")
        return d


class Received(_Body):
    received_on: date
    note: str | None = Field(default=None, max_length=2000)
    prices: dict[UUID, ReceiptPrice] | None = None


class Acknowledged(_Body):
    acknowledged_on: date
    reference: str | None = Field(default=None, max_length=300)


class Reason(_Body):
    reason: str = Field(min_length=1, max_length=2000)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (C.MakerCheckerViolation, HT.MakerCheckerError, HT.HumanTaskPermissionError, PermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (C.Rep10Conflict, HT.HumanTaskConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (C.Rep10Error, HT.HumanTaskError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.CheckViolationError):
        raise HTTPException(status_code=422, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, (asyncpg.InsufficientPrivilegeError, asyncpg.UniqueViolationError)):
        raise HTTPException(status_code=409, detail=str(exc).split("\n")[0]) from exc
    from services.account_models import AccountModelConflict
    from services.rebalance_proposals import ProposalConflict
    if isinstance(exc, (AccountModelConflict, ProposalConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


def _perms(principal) -> dict:
    sa = principal.is_super_admin
    return {"can_read": sa or VIEW in principal.permissions, "can_write": sa or MANAGE in principal.permissions,
            "can_approve": sa or APPROVE in principal.permissions, "is_super_admin": sa,
            "read_permission": VIEW, "write_permission": MANAGE, "approve_permission": APPROVE}


def _vocab(perms: dict) -> dict:
    v = G.vocabularies()
    v["editable"] = ["donee_id", "lots", "target_amount", "agi_estimate", "itemizes", "prior_carryforward", "rebuy"] \
        if perms["can_write"] is True else []
    v["donee_editable"] = ["name", "kind", "ein", "receiving_broker", "dtc_participant", "account_number"] \
        if perms["can_write"] is True else []
    return v


async def _detail(request, principal, gift_id) -> dict:
    perms = _perms(principal)
    async with (await get_pool()).acquire() as conn:
        out = await G.detail(conn, get_org_id(request), gift_id)
    out["actions"] = G.actions_for(out, can_write=perms["can_write"] is True, can_approve=perms["can_approve"] is True,
                                   user_id=principal.user_id)
    return {"gift": out, "permissions": perms, "vocabularies": _vocab(perms)}


async def _act(request, principal, coro):
    try:
        out = await coro
        env = await _detail(request, principal, out["id"])
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    if out.get("rebuy_note"):
        env["rebuy_note"] = out["rebuy_note"]
    return env


@router.get("/tax/gifts")
async def gifts(request: Request, status: str | None = Query(default=None, max_length=40)):
    principal = require_permission(request, VIEW)
    perms = _perms(principal)
    try:
        async with (await get_pool()).acquire() as conn:
            rows = await G.list_gifts(conn, get_org_id(request), status=status)
            donees = await G.list_donees(conn, get_org_id(request))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    for r in rows:
        r["actions"] = G.actions_for(r, can_write=perms["can_write"] is True, can_approve=perms["can_approve"] is True,
                                     user_id=principal.user_id)
    return {"rows": rows, "donees": donees, "permissions": perms, "vocabularies": _vocab(perms),
            "actions": ["propose", "add_donee"] if perms["can_write"] is True else [], "label": C.ESTIMATE}


@router.get("/tax/gifts/donees")
async def donees(request: Request):
    principal = require_permission(request, VIEW)
    perms = _perms(principal)
    try:
        async with (await get_pool()).acquire() as conn:
            rows = await G.list_donees(conn, get_org_id(request))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    for r in rows:
        r["actions"] = ["retire"] if perms["can_write"] is True and r["status"] == "active" else []
    return {"rows": rows, "permissions": perms, "vocabularies": _vocab(perms),
            "actions": ["add_donee"] if perms["can_write"] is True else []}


@router.get("/tax/gifts/candidates")
async def candidates(request: Request, subject_type: Literal["household", "entity"] = Query(...),
                     subject_id: UUID = Query(...)):
    principal = require_permission(request, VIEW)
    perms = _perms(principal)
    try:
        async with (await get_pool()).acquire() as conn:
            out = await G.candidates(conn, get_org_id(request), subject_type, subject_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {**out, "permissions": perms, "vocabularies": _vocab(perms)}


@router.post("/tax/gifts/donees", status_code=201)
async def add_donee(request: Request, body: Donee):
    principal = require_permission(request, MANAGE)
    try:
        out = await G.create_donee(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                   name=body.name, kind=body.kind, ein=body.ein, receiving_broker=body.receiving_broker,
                                   dtc_participant=body.dtc_participant, account_number=body.account_number,
                                   contact=body.contact, notes=body.notes)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"donee": out}


@router.post("/tax/gifts/donees/{donee_id}/retire")
async def retire_donee(request: Request, donee_id: str):
    principal = require_permission(request, MANAGE)
    try:
        out = await G.retire_donee(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                   donee_id=donee_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"donee": out}


@router.post("/tax/gifts", status_code=201)
async def propose(request: Request, body: Gift):
    principal = require_permission(request, MANAGE)
    lots = [{"tax_lot_id": str(x.tax_lot_id), "quantity": x.quantity} for x in body.lots] if body.lots else None
    return await _act(request, principal, G.create(
        await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id, subject_type=body.subject_type,
        subject_id=body.subject_id, member_entity_id=body.member_entity_id, donee_id=body.donee_id,
        tax_year=body.tax_year, target_amount=body.target_amount, lots=lots, agi_estimate=body.agi_estimate,
        agi_source=body.agi_source, agi_as_of=body.agi_as_of, itemizes=body.itemizes,
        itemizes_source=body.itemizes_source, itemizes_as_of=body.itemizes_as_of,
        prior_carryforward=body.prior_carryforward, prior_carryforward_source=body.prior_carryforward_source,
        prior_carryforward_as_of=body.prior_carryforward_as_of, rebuy=body.rebuy, notes=body.notes))


@router.get("/tax/gifts/{gift_id}")
async def gift(request: Request, gift_id: str):
    principal = require_permission(request, VIEW)
    try:
        return await _detail(request, principal, gift_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.post("/tax/gifts/{gift_id}/submit")
async def submit(request: Request, gift_id: str):
    principal = require_permission(request, MANAGE)
    return await _act(request, principal, G.submit(await get_pool(), org_id=get_org_id(request),
                                                   actor_id=principal.user_id, gift_id=gift_id))


@router.post("/tax/gifts/{gift_id}/approve")
async def approve(request: Request, gift_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    return await _act(request, principal, G.decide(await get_pool(), org_id=get_org_id(request),
                                                   actor_id=principal.user_id, gift_id=gift_id, outcome="approve",
                                                   note=body.note))


@router.post("/tax/gifts/{gift_id}/reject")
async def reject(request: Request, gift_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    return await _act(request, principal, G.decide(await get_pool(), org_id=get_org_id(request),
                                                   actor_id=principal.user_id, gift_id=gift_id, outcome="reject",
                                                   note=body.note))


@router.post("/tax/gifts/{gift_id}/letter")
async def letter(request: Request, gift_id: str):
    principal = require_permission(request, MANAGE)
    try:
        pdf, filename = await G.letter(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                       gift_id=gift_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@router.post("/tax/gifts/{gift_id}/sent")
async def sent(request: Request, gift_id: str, body: Sent):
    principal = require_permission(request, MANAGE)
    return await _act(request, principal, G.mark_sent(await get_pool(), org_id=get_org_id(request),
                                                      actor_id=principal.user_id, gift_id=gift_id,
                                                      channel=body.channel, note=body.note))


@router.post("/tax/gifts/{gift_id}/received")
async def received(request: Request, gift_id: str, body: Received):
    principal = require_permission(request, MANAGE)
    prices = {str(k): {"price": v.price, "source": v.source} for k, v in (body.prices or {}).items()}
    return await _act(request, principal, G.mark_received(await get_pool(), org_id=get_org_id(request),
                                                          actor_id=principal.user_id, gift_id=gift_id,
                                                          received_on=body.received_on, note=body.note, prices=prices))


@router.post("/tax/gifts/{gift_id}/acknowledged")
async def acknowledged(request: Request, gift_id: str, body: Acknowledged):
    principal = require_permission(request, MANAGE)
    return await _act(request, principal, G.acknowledge(await get_pool(), org_id=get_org_id(request),
                                                        actor_id=principal.user_id, gift_id=gift_id,
                                                        acknowledged_on=body.acknowledged_on,
                                                        reference=body.reference))


@router.post("/tax/gifts/{gift_id}/rebuy")
async def rebuy(request: Request, gift_id: str):
    principal = require_permission(request, MANAGE)
    return await _act(request, principal, G.rebuy(await get_pool(), org_id=get_org_id(request),
                                                  actor_id=principal.user_id, gift_id=gift_id))


@router.post("/tax/gifts/{gift_id}/cancel")
async def cancel(request: Request, gift_id: str, body: Reason):
    principal = require_permission(request, MANAGE)
    return await _act(request, principal, G.cancel(await get_pool(), org_id=get_org_id(request),
                                                   actor_id=principal.user_id, gift_id=gift_id, reason=body.reason))
