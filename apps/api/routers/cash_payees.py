"""CASH08 — Household → Treasury → PAYEES and INVOICES; the org's THIRD-PARTY switch (stays off).

READ (``view_portfolio | manage_payees | approve_payees``, then the ONE member / VISIBILITY1 scope — another family's
household is a 404, the same as a missing one; a member reads their family's ACTIVE payees read-only — name, kind, last
four — with the ``cash.third_party`` disclosure):
    GET  /api/v1/cash/households/{household_id}/payees                       list (status, SLOA state, flags)
    GET  /api/v1/cash/households/{household_id}/payees/{payee_id}            detail (+ history; staff)
MANAGE (``manage_payees``; a client role is refused even if it ever held it):
    POST /api/v1/cash/households/{household_id}/payees                       create (draft)
    POST /api/v1/cash/households/{household_id}/payees/{payee_id}/{action}
         action ∈ edit · submit · request_bank_change · suspend · resume · retire · attach_sloa · expire_sloa ·
                  revoke_sloa
APPROVE (``approve_payees``; the S30 FLOOR — never a maker / the requester, Super Admin included):
    POST /api/v1/cash/households/{household_id}/payees/{payee_id}/approve | /reject
    POST /api/v1/cash/households/{household_id}/payees/{payee_id}/bank-change/approve   (the call-back attestation)
    POST /api/v1/cash/households/{household_id}/payees/{payee_id}/bank-change/reject
INVOICES (read: staff with a read permission; write: ``manage_payees`` AND ``manage_portfolio`` — cash01's
obligation write):
    GET  /api/v1/cash/households/{household_id}/invoices                     awaiting recording + recorded + the form
    POST /api/v1/cash/households/{household_id}/invoices/tag                 tag a Chancery document as an invoice
    POST /api/v1/cash/households/{household_id}/invoices/record              → a firm vendor_invoice obligation
THIRD-PARTY SWITCH (Super Admin only; the approval is refused in this release — counsel first):
    GET  /api/v1/cash/third-party                                            the org's state (off) + any open request
    POST /api/v1/cash/third-party/request                                    a Super Admin asks (counsel document)
    POST /api/v1/cash/third-party/approve | /reject                          a DIFFERENT Super Admin decides

Every write goes through services.cash_payees / services.cash_invoices (the number guard, the floor, audit). ``org_id``
comes from the verified session only (Rule 6); every body model forbids extra fields.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from routers.entities import get_org_id
from routers.payments import _holds, _household, _not_client
from services import authz
from services import cash_invoices as CI
from services import cash_payees as CP
from services.authz import require_permission, require_super_admin
from services.database import get_pool

router = APIRouter(tags=["cash-payees"])

READ_PERMISSIONS = CP.READ_PERMISSIONS
ACTIONS = ("edit", "submit", "request_bank_change", "suspend", "resume", "retire", "attach_sloa", "expire_sloa",
           "revoke_sloa")


class PayeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    display_name: str
    legal_name: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    bank_name: str | None = None
    account_type: str | None = None
    last_four: str | None = None
    payee_reference: str | None = None
    notes: str | None = None


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict[str, Any] | None = None           # edit / request_bank_change (the new details)
    request_contact: str | None = None              # request_bank_change: how the request arrived
    request_phone: str | None = None                # request_bank_change: a number given IN the request
    reason: str | None = None                       # request_bank_change / suspend / retire / revoke_sloa
    document_id: UUID | None = None                 # attach_sloa
    account_ids: list[UUID] | None = None           # attach_sloa
    signed_on: date | None = None                   # attach_sloa
    expires_on: date | None = None                  # attach_sloa


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = None


class AttestationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person: str | None = None                       # who you called
    number: str | None = None                       # the number you called (must be the one ON FILE)
    called_at: datetime | None = None               # when
    note: str | None = None


class TagIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID


class RecordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    payee_id: UUID
    entity_id: UUID
    invoice_number: str
    amount: str
    due_date: date
    notes: str | None = None


class SwitchRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    counsel_document_id: UUID | None = None
    reason: str | None = None


def _err(exc: Exception) -> HTTPException | None:
    import asyncpg
    from services import human_tasks as HTK

    if isinstance(exc, (CP.PayeeNotFound, CI.InvoiceNotFound, HTK.HumanTaskNotFound)):
        return HTTPException(status_code=404, detail=str(exc) or "Not found")
    if isinstance(exc, (CP.MakerCheckerViolation, CP.PayeeRefused, PermissionError, HTK.HumanTaskPermissionError,
                        HTK.MakerCheckerError, HTK.AgentActorRefused)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (CP.PayeeConflict, CI.InvoiceDuplicate, HTK.HumanTaskConflict, asyncpg.UniqueViolationError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (asyncpg.CheckViolationError, asyncpg.RaiseError)) or getattr(exc, "sqlstate", None) == "23514":
        # never the row (a CHECK's DETAIL echoes it): the constraint / the guard's own column-naming message
        name = getattr(exc, "constraint_name", None)
        msg = str(exc).split("\n", 1)[0]
        return HTTPException(status_code=422, detail=f"refused by the database: {name or msg}")
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        return HTTPException(status_code=409, detail=str(exc).split("\n", 1)[0])
    if isinstance(exc, (CP.PayeeError, CI.InvoiceError, HTK.HumanTaskError)):
        return HTTPException(status_code=422, detail=str(exc))
    return None


async def _run(coro):
    try:
        return await coro
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        h = _err(exc)
        if h is None:
            raise
        raise h from exc


async def _open(request: Request, household_id: UUID):
    """The payments router's scope rule (routers/payments._open), with the payee read permissions."""
    p = authz.require_any_permission(request, READ_PERMISSIONS)
    org_id, pool = get_org_id(request), await get_pool()
    member = authz.is_member_principal(p)
    if not member and p.is_internal and not (p.is_super_admin or p.is_staff):
        from services import family_visibility as FV

        staff = await FV.scope_for_request(request, pool)
        scope = None if staff is None else set(staff.entity_ids)
    else:
        scope = await authz.member_entity_scope(request, pool)
    async with pool.acquire() as conn:
        await _household(conn, org_id, household_id, scope)
    return p, org_id, pool, member


async def _labels(conn, org_id, household_id) -> dict:
    from services import disclosures as D

    return {CP.DISCLOSURE_KEY: D.public(await D.resolve_safe(conn, CP.DISCLOSURE_KEY, org_id=org_id,
                                                               household_id=household_id))}


async def _envelope(conn, p, member: bool, org_id, household_id) -> dict:
    can_m = not member and _holds(p, CP.MANAGE)
    can_a = not member and _holds(p, CP.APPROVE)
    return {"household": {"id": str(household_id)}, "member_view": member,
            "labels": await _labels(conn, org_id, household_id),
            "permissions": {"can_read": True, "can_write": can_m, "can_approve": can_a,
                            "is_super_admin": bool(p.is_super_admin), "read_permission": "view_portfolio",
                            "write_permission": CP.MANAGE if can_m else None,
                            "approve_permission": CP.APPROVE if can_a else None},
            "third_party": {"enabled": await CP.third_party_enabled(conn, org_id),
                            "reason": await CP.third_party_refusal(conn, org_id)},
            "vocabularies": CP.vocabularies(can_write=can_m)}


def _with_actions(items: list[dict], p, member: bool) -> list[dict]:
    return [{**x, "actions": CP.actions_for(x, can_manage=not member and _holds(p, CP.MANAGE),
                                            can_approve=not member and _holds(p, CP.APPROVE), user_id=p.user_id,
                                            member=member)} for x in items]


async def _own(pool, org_id, household_id, payee_id) -> None:
    async with pool.acquire() as conn:
        row = await _run(CP.load(conn, org_id, payee_id))
    if str(row["household_id"]) != str(household_id):
        raise HTTPException(status_code=404, detail="payee not found")


async def _detail_out(pool, p, org_id, payee_id) -> dict:
    async with pool.acquire() as conn:
        d = await CP.detail(conn, org_id, payee_id)
    return {"payee": _with_actions([d], p, False)[0]}


# ── reads ───────────────────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/cash/households/{household_id}/payees")
async def list_payees_route(request: Request, household_id: UUID, status: str | None = None):
    p, org_id, pool, member = await _open(request, household_id)
    async with pool.acquire() as conn:
        items = await _run(CP.list_payees(conn, org_id, household_id, member=member, status=status))
        env = await _envelope(conn, p, member, org_id, household_id)
    offer = [{"key": "new", "label": "New payee"}] if env["permissions"]["can_write"] else []
    return {**env, "today": CP.today().isoformat(), "items": _with_actions(items, p, member), "actions": offer}


@router.get("/cash/households/{household_id}/payees/{payee_id}")
async def payee_detail_route(request: Request, household_id: UUID, payee_id: UUID):
    p, org_id, pool, member = await _open(request, household_id)
    async with pool.acquire() as conn:
        d = await _run(CP.detail(conn, org_id, payee_id, member=member))
        if d["household_id"] != str(household_id):
            raise HTTPException(status_code=404, detail="payee not found")
        env = await _envelope(conn, p, member, org_id, household_id)
    return {**env, "payee": _with_actions([d], p, member)[0]}


# ── manage ──────────────────────────────────────────────────────────────────────────────────────────────────────────
@router.post("/cash/households/{household_id}/payees", status_code=201)
async def create_payee_route(request: Request, household_id: UUID, body: PayeeIn):
    p = require_permission(request, "manage_payees")
    _not_client(p)
    _p, org_id, pool, _m = await _open(request, household_id)
    row = await _run(CP.create(pool, org_id=org_id, actor_id=p.user_id, household_id=household_id,
                               data=body.model_dump()))
    return await _detail_out(pool, p, org_id, row["id"])


async def _decide(request: Request, household_id: UUID, payee_id: UUID, outcome: str, note: str | None):
    p = require_permission(request, "approve_payees")
    _not_client(p)
    _p, org_id, pool, _m = await _open(request, household_id)
    await _own(pool, org_id, household_id, payee_id)
    await _run(CP.decide(pool, org_id=org_id, actor_id=p.user_id, payee_id=payee_id, outcome=outcome, note=note))
    return await _detail_out(pool, p, org_id, payee_id)


@router.post("/cash/households/{household_id}/payees/{payee_id}/approve")
async def approve_payee_route(request: Request, household_id: UUID, payee_id: UUID, body: DecideIn | None = None):
    return await _decide(request, household_id, payee_id, "approve", body.note if body else None)


@router.post("/cash/households/{household_id}/payees/{payee_id}/reject")
async def reject_payee_route(request: Request, household_id: UUID, payee_id: UUID, body: DecideIn | None = None):
    return await _decide(request, household_id, payee_id, "reject", body.note if body else None)


async def _decide_bank(request: Request, household_id: UUID, payee_id: UUID, outcome: str, body: AttestationIn | None):
    p = require_permission(request, "approve_payees")
    _not_client(p)
    _p, org_id, pool, _m = await _open(request, household_id)
    await _own(pool, org_id, household_id, payee_id)
    b = body or AttestationIn()
    att = {"person": b.person, "number": b.number, "called_at": b.called_at} if outcome == "approve" else None
    await _run(CP.decide_bank_change(pool, org_id=org_id, actor_id=p.user_id, payee_id=payee_id, outcome=outcome,
                                     attestation=att, note=b.note))
    return await _detail_out(pool, p, org_id, payee_id)


@router.post("/cash/households/{household_id}/payees/{payee_id}/bank-change/approve")
async def approve_bank_change_route(request: Request, household_id: UUID, payee_id: UUID,
                                    body: AttestationIn | None = None):
    return await _decide_bank(request, household_id, payee_id, "approve", body)


@router.post("/cash/households/{household_id}/payees/{payee_id}/bank-change/reject")
async def reject_bank_change_route(request: Request, household_id: UUID, payee_id: UUID,
                                   body: AttestationIn | None = None):
    return await _decide_bank(request, household_id, payee_id, "reject", body)


@router.post("/cash/households/{household_id}/payees/{payee_id}/{action}")
async def payee_action_route(request: Request, household_id: UUID, payee_id: UUID, action: str, body: ActionIn):
    if action not in ACTIONS:
        raise HTTPException(status_code=404, detail="Not found")
    p = require_permission(request, "manage_payees")
    _not_client(p)
    _p, org_id, pool, _m = await _open(request, household_id)
    await _own(pool, org_id, household_id, payee_id)
    kw = {"org_id": org_id, "actor_id": p.user_id, "payee_id": payee_id}
    b = body
    calls = {
        "edit": lambda: CP.edit(pool, changes=dict(b.changes or {}), **kw),
        "submit": lambda: CP.submit(pool, **kw),
        "request_bank_change": lambda: CP.request_bank_change(pool, data={
            **dict(b.changes or {}), "request_contact": b.request_contact, "request_phone": b.request_phone,
            "reason": b.reason}, **kw),
        "suspend": lambda: CP.suspend(pool, reason=b.reason or "", **kw),
        "resume": lambda: CP.resume(pool, **kw),
        "retire": lambda: CP.retire(pool, reason=b.reason or "", **kw),
        "attach_sloa": lambda: CP.attach_sloa(pool, document_id=b.document_id, account_ids=list(b.account_ids or []),
                                              signed_on=b.signed_on, expires_on=b.expires_on, **kw),
        "expire_sloa": lambda: CP.expire_sloa(pool, **kw),
        "revoke_sloa": lambda: CP.revoke_sloa(pool, reason=b.reason or "", **kw),
    }
    if action == "attach_sloa" and b.document_id is None:
        raise HTTPException(status_code=422, detail="document_id is required")
    await _run(calls[action]())
    return await _detail_out(pool, p, org_id, payee_id)


# ── invoices ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _can_record(p) -> bool:
    return _holds(p, CP.MANAGE) and _holds(p, "manage_portfolio")


@router.get("/cash/households/{household_id}/invoices")
async def invoices_route(request: Request, household_id: UUID):
    """Staff only: the household's tagged Chancery invoices awaiting recording, the recorded ones, and the record form
    (active payees; the household's members as payers)."""
    p, org_id, pool, member = await _open(request, household_id)
    if member:
        raise HTTPException(status_code=403, detail=authz.INTERNAL_ACCESS_DETAIL)
    from services import household_total as HHT

    async with pool.acquire() as conn:
        waiting = await CI.awaiting(conn, org_id, household_id)
        done = await CI.recorded(conn, org_id, household_id)
        payees = [x for x in await CP.list_payees(conn, org_id, household_id) if x["status"] == "active"]
        members = await HHT.household_members(conn, org_id, household_id)
        ents = [{"id": str(r["id"]), "name": r["display_name"]} for r in await conn.fetch(
            "SELECT DISTINCT ON (id) id, display_name FROM entities WHERE id = ANY($1::uuid[]) AND org_id = $2::uuid "
            "ORDER BY id, valid_to NULLS FIRST", members, str(org_id))] if members else []
        env = await _envelope(conn, p, False, org_id, household_id)
    can = _can_record(p)
    env["permissions"]["can_write"] = can
    env["permissions"]["write_permission"] = "manage_payees + manage_portfolio" if can else None
    return {**env, "awaiting": waiting, "recorded": done,
            "form": {"payees": [{"id": x["id"], "label": f"{x['display_name']} (····{x['last_four']})"}
                                for x in payees] if can else [],
                     "payers": sorted(ents, key=lambda e: e["name"] or "") if can else []},
            "actions": [{"key": "record", "label": "Record invoice"}] if can else []}


@router.post("/cash/households/{household_id}/invoices/tag", status_code=201)
async def tag_invoice_route(request: Request, household_id: UUID, body: TagIn):
    p = require_permission(request, "manage_payees")
    _not_client(p)
    _p, org_id, pool, _m = await _open(request, household_id)
    return await _run(CI.tag(pool, org_id=org_id, actor_id=p.user_id, household_id=household_id,
                             document_id=body.document_id))


@router.post("/cash/households/{household_id}/invoices/record", status_code=201)
async def record_invoice_route(request: Request, household_id: UUID, body: RecordIn):
    p = require_permission(request, "manage_payees")
    require_permission(request, "manage_portfolio")
    _not_client(p)
    _p, org_id, pool, _m = await _open(request, household_id)
    return await _run(CI.record(pool, org_id=org_id, actor_id=p.user_id, household_id=household_id,
                                document_id=body.document_id, payee_id=body.payee_id, entity_id=body.entity_id,
                                invoice_number=body.invoice_number, amount=body.amount, due_date=body.due_date,
                                notes=body.notes))


# ── the third-party switch (org level; Super Admin only) ────────────────────────────────────────────────────────────
@router.get("/cash/third-party")
async def third_party_state_route(request: Request):
    require_super_admin(request)
    org_id, pool = get_org_id(request), await get_pool()
    async with pool.acquire() as conn:
        task = await CP.switch_task(conn, org_id)
        return {"setting": CP.SETTING_THIRD_PARTY, "enabled": await CP.third_party_enabled(conn, org_id),
                "release_locked": CP.THIRD_PARTY_RELEASE_LOCKED,
                "release_lock_reason": CP.RELEASE_LOCK_REASON if CP.THIRD_PARTY_RELEASE_LOCKED else None,
                "open_request": ({"task_id": task["id"], "maker_id": task["maker_id"],
                                  "counsel_document_id": (task.get("payload") or {}).get("counsel_document_id")}
                                 if task else None)}


@router.post("/cash/third-party/request", status_code=201)
async def third_party_request_route(request: Request, body: SwitchRequestIn):
    p = require_super_admin(request)
    org_id, pool = get_org_id(request), await get_pool()
    task = await _run(CP.request_third_party_switch(pool, org_id=org_id, actor_id=p.user_id,
                                                    counsel_document_id=body.counsel_document_id,
                                                    reason=body.reason or ""))
    return {"task_id": task["id"], "status": task["status"]}


async def _decide_switch(request: Request, outcome: str, body: DecideIn | None):
    p = require_super_admin(request)
    org_id, pool = get_org_id(request), await get_pool()
    return await _run(CP.decide_third_party_switch(pool, org_id=org_id, actor_id=p.user_id, outcome=outcome,
                                                   note=body.note if body else None))


@router.post("/cash/third-party/approve")
async def third_party_approve_route(request: Request, body: DecideIn | None = None):
    return await _decide_switch(request, "approve", body)


@router.post("/cash/third-party/reject")
async def third_party_reject_route(request: Request, body: DecideIn | None = None):
    return await _decide_switch(request, "reject", body)
