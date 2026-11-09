"""CASH07 — Household → Treasury → PAYMENTS (stage A, manual): payment instructions between a family's OWN accounts.

READ (``view_portfolio | initiate_payment | approve_payment``, then the ONE member / VISIBILITY1 scope — another family's
household is a 404, the same as a missing one; a member reads their family's payments READ-ONLY, last four only, with
the ``cash.payments`` disclosure):
    GET  /api/v1/cash/households/{household_id}/payments                    list + filters (status, rail, account, dates)
    GET  /api/v1/cash/households/{household_id}/payments/{payment_id}       detail + the lifecycle timeline (events)
INITIATE (``initiate_payment``; a client role is refused even if it ever held it):
    GET  /api/v1/cash/households/{household_id}/payments/options            the new-instruction form, decided server-side
    POST /api/v1/cash/households/{household_id}/payments                    draft
    POST /api/v1/cash/households/{household_id}/payments/{payment_id}/sheet the printable payment sheet (PDF, masked)
    GET  /api/v1/cash/households/{household_id}/payments/{payment_id}/matches   candidate custodian-feed rows
    POST /api/v1/cash/households/{household_id}/payments/{payment_id}/{action}
         action ∈ edit · submit · mark_sent · consent_requested · acknowledge · settle · custodian_reject · return ·
                  reconcile · close · cancel
APPROVE (``approve_payment``; the S30 FLOOR — never a maker, Super Admin included):
    POST /api/v1/cash/households/{household_id}/payments/{payment_id}/approve | /reject

Every write goes through services.payments (both authority checks, same owner only, declared rails, business days,
maker-checker, the DB transition trigger, audit + payment_events). ``org_id`` comes from the verified session only
(Rule 6); every body model forbids extra fields. Nothing here sends money (the manual adapter renders a sheet).
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from routers.entities import get_org_id
from services import authz
from services import payments as PS
from services.authz import require_permission
from services.database import get_pool

router = APIRouter(tags=["cash-payments"])

READ_PERMISSIONS = ("view_portfolio", "initiate_payment", "approve_payment")
ACTIONS = ("edit", "submit", "mark_sent", "consent_requested", "acknowledge", "settle", "custodian_reject", "return",
           "reconcile", "close", "cancel")


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_account_id: UUID
    destination_account_id: UUID
    amount: str
    rail: str
    value_date: date
    purpose: str
    currency_code: str | None = None
    purpose_ref_type: str | None = None
    purpose_ref_id: UUID | None = None


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = None


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict[str, Any] | None = None          # edit
    channel: str | None = None                      # mark_sent
    external_ref: str | None = None                 # mark_sent / acknowledge
    note: str | None = None
    settled_on: date | None = None                  # settle
    reason: str | None = None                       # custodian_reject / return / cancel
    return_code: str | None = None                  # return
    method: str | None = None                       # reconcile
    transaction_ids: list[UUID] | None = None       # reconcile (custodian_data)
    reference: str | None = None                    # reconcile


def _err(exc: Exception) -> HTTPException | None:
    import asyncpg
    from services import human_tasks as HTK

    if isinstance(exc, (PS.PaymentNotFound, HTK.HumanTaskNotFound)):
        return HTTPException(status_code=404, detail=str(exc) or "Not found")
    if isinstance(exc, PS.PaymentAuthorityError):
        return HTTPException(status_code=403, detail={"message": str(exc), "reasons": exc.reasons})
    if isinstance(exc, (PS.MakerCheckerViolation, PermissionError, HTK.HumanTaskPermissionError, HTK.MakerCheckerError,
                        HTK.AgentActorRefused)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (PS.PaymentConflict, HTK.HumanTaskConflict, asyncpg.UniqueViolationError)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (asyncpg.CheckViolationError, asyncpg.RaiseError)) or getattr(exc, "sqlstate", None) == "23514":
        return HTTPException(status_code=422, detail=f"refused by the database: {getattr(exc, 'constraint_name', None) or exc}")
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (PS.PaymentError, HTK.HumanTaskError)):
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


def _not_client(p) -> None:
    """A client role is refused a payment write even if it ever held the permission (fails closed)."""
    if not p.is_super_admin and authz.is_member_principal(p):
        raise HTTPException(status_code=403, detail=authz.INTERNAL_ACCESS_DETAIL)


def _holds(p, perm: str) -> bool:
    """The exact rule of the write gates below, for the envelope (fails closed)."""
    if p is None or not p.admitted or not p.is_active:
        return False
    if p.is_super_admin:
        return True
    return perm in p.permissions and not authz.is_member_principal(p)


async def _household(conn, org_id, household_id, scope) -> None:
    """404 unless the household is this org's AND (for a scoped caller) one of their families."""
    name = await conn.fetchval("SELECT name FROM households WHERE id = $1::uuid AND org_id = $2::uuid",
                               str(household_id), str(org_id))
    if name is None:
        raise HTTPException(status_code=404, detail="household not found")
    if scope is not None:
        from services import household_total as HHT

        members = set(await HHT.household_members(conn, org_id, household_id)) & {str(x) for x in scope}
        if not members:
            raise HTTPException(status_code=404, detail="household not found")


async def _open(request: Request, household_id: UUID):
    p = authz.require_any_permission(request, READ_PERMISSIONS)
    org_id, pool = get_org_id(request), await get_pool()
    scope = await authz.member_entity_scope(request, pool)       # the ONE member / VISIBILITY1 scope
    member = authz.is_member_principal(p)
    async with pool.acquire() as conn:
        await _household(conn, org_id, household_id, scope)
    return p, org_id, pool, member


async def _label(conn, org_id, household_id) -> dict:
    from services import disclosures as D

    return {PS.DISCLOSURE_KEY: D.public(await D.resolve_safe(conn, PS.DISCLOSURE_KEY, org_id=org_id,
                                                               household_id=household_id))}


def _envelope(p, member: bool, household_id, labels: dict) -> dict:
    can_i = not member and _holds(p, PS.INITIATE)
    can_a = not member and _holds(p, PS.APPROVE)
    return {"household": {"id": str(household_id)}, "member_view": member, "labels": labels,
            "permissions": {"can_read": True, "can_write": can_i, "can_approve": can_a,
                            "is_super_admin": bool(p.is_super_admin), "read_permission": "view_portfolio",
                            "write_permission": PS.INITIATE if can_i else None,
                            "approve_permission": PS.APPROVE if can_a else None},
            "vocabularies": PS.vocabularies(can_write=can_i)}


def _with_actions(items: list[dict], p, member: bool) -> list[dict]:
    t = PS.today()
    return [{**x, "actions": PS.actions_for(x, can_initiate=not member and _holds(p, PS.INITIATE),
                                            can_approve=not member and _holds(p, PS.APPROVE), user_id=p.user_id,
                                            member=member, t=t)} for x in items]


@router.get("/cash/households/{household_id}/payments")
async def list_payments_route(request: Request, household_id: UUID, status: str | None = Query(default=None,
                              max_length=32), rail: str | None = Query(default=None, max_length=16),
                              account_id: UUID | None = Query(default=None), date_from: date | None = Query(default=None),
                              date_to: date | None = Query(default=None)):
    """The household's payment instructions (a member: their own family's, read-only, last four only)."""
    p, org_id, pool, member = await _open(request, household_id)
    async with pool.acquire() as conn:
        items = await _run(PS.list_payments(conn, org_id, household_id, status=status, rail=rail, account_id=account_id,
                                            date_from=date_from, date_to=date_to, member=member))
        labels = await _label(conn, org_id, household_id)
    env = _envelope(p, member, household_id, labels)
    new_offer = ([{"key": "new", "label": "New payment instruction"}] if env["permissions"]["can_write"] else [])
    return {**env, "today": PS.today().isoformat(), "items": _with_actions(items, p, member), "actions": new_offer}


@router.get("/cash/households/{household_id}/payments/options")
async def payment_options_route(request: Request, household_id: UUID):
    """The new-instruction form: source accounts (with both authority reasons), same-owner destinations, declared
    rails, obligations / cash proposals to link, the business-day rule."""
    p = require_permission(request, "initiate_payment")
    _not_client(p)
    _p, org_id, pool, _member = await _open(request, household_id)
    async with pool.acquire() as conn:
        out = await _run(PS.options(pool, conn, org_id, household_id, p.user_id))
        labels = await _label(conn, org_id, household_id)
    return {**_envelope(p, False, household_id, labels), **out}


@router.get("/cash/households/{household_id}/payments/{payment_id}")
async def payment_detail_route(request: Request, household_id: UUID, payment_id: UUID):
    p, org_id, pool, member = await _open(request, household_id)
    async with pool.acquire() as conn:
        d = await _run(PS.detail(conn, org_id, payment_id, member=member))
        if d["household_id"] != str(household_id):
            raise HTTPException(status_code=404, detail="payment instruction not found")
        labels = await _label(conn, org_id, household_id)
    return {**_envelope(p, member, household_id, labels), "payment": _with_actions([d], p, member)[0]}


@router.get("/cash/households/{household_id}/payments/{payment_id}/matches")
async def payment_matches_route(request: Request, household_id: UUID, payment_id: UUID):
    """Candidate custodian-feed transactions (exact amount, direction per leg, the date window)."""
    p = require_permission(request, "initiate_payment")
    _not_client(p)
    _p, org_id, pool, _m = await _open(request, household_id)
    async with pool.acquire() as conn:
        row = await _run(PS.load(conn, org_id, payment_id))
        if str(row["household_id"]) != str(household_id):
            raise HTTPException(status_code=404, detail="payment instruction not found")
        found = await PS.matches(conn, org_id, row)
    return {"payment_id": str(payment_id), "matches": found,
            "window": {"before_business_days": PS.MATCH_BEFORE_BD, "after_business_days": PS.MATCH_AFTER_BD}}


@router.post("/cash/households/{household_id}/payments", status_code=201)
async def payment_draft_route(request: Request, household_id: UUID, body: DraftIn):
    p = require_permission(request, "initiate_payment")
    _not_client(p)
    _p, org_id, pool, _m = await _open(request, household_id)
    row = await _run(PS.draft(pool, org_id=org_id, actor_id=p.user_id, household_id=household_id,
                              data=body.model_dump()))
    async with pool.acquire() as conn:
        d = await PS.detail(conn, org_id, row["id"])
    return {"payment": _with_actions([d], p, False)[0]}


async def _own(pool, org_id, household_id, payment_id) -> None:
    async with pool.acquire() as conn:
        row = await _run(PS.load(conn, org_id, payment_id))
    if str(row["household_id"]) != str(household_id):
        raise HTTPException(status_code=404, detail="payment instruction not found")


@router.post("/cash/households/{household_id}/payments/{payment_id}/sheet")
async def payment_sheet_route(request: Request, household_id: UUID, payment_id: UUID):
    """The manual adapter's printable PAYMENT SHEET (PDF, generated on request, never stored; masked accounts only).
    Logged; the status does not change — the operator then confirms the channel (mark_sent)."""
    p = require_permission(request, "initiate_payment")
    _not_client(p)
    _p, org_id, pool, _m = await _open(request, household_id)
    await _own(pool, org_id, household_id, payment_id)
    pdf, filename = await _run(PS.sheet(pool, org_id=org_id, actor_id=p.user_id, payment_id=payment_id))
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


async def _decide(request: Request, household_id: UUID, payment_id: UUID, outcome: str, note: str | None):
    p = require_permission(request, "approve_payment")
    _not_client(p)
    _p, org_id, pool, _m = await _open(request, household_id)
    await _own(pool, org_id, household_id, payment_id)
    await _run(PS.decide(pool, org_id=org_id, actor_id=p.user_id, payment_id=payment_id, outcome=outcome, note=note))
    async with pool.acquire() as conn:
        d = await PS.detail(conn, org_id, payment_id)
    return {"payment": _with_actions([d], p, False)[0]}


@router.post("/cash/households/{household_id}/payments/{payment_id}/approve")
async def payment_approve_route(request: Request, household_id: UUID, payment_id: UUID, body: DecideIn | None = None):
    return await _decide(request, household_id, payment_id, "approve", body.note if body else None)


@router.post("/cash/households/{household_id}/payments/{payment_id}/reject")
async def payment_reject_route(request: Request, household_id: UUID, payment_id: UUID, body: DecideIn | None = None):
    return await _decide(request, household_id, payment_id, "reject", body.note if body else None)


@router.post("/cash/households/{household_id}/payments/{payment_id}/{action}")
async def payment_action_route(request: Request, household_id: UUID, payment_id: UUID, action: str, body: ActionIn):
    """The operator's lifecycle steps (each a logged human action through services.payments)."""
    if action not in ACTIONS:
        raise HTTPException(status_code=404, detail="Not found")
    p = require_permission(request, "initiate_payment")
    _not_client(p)
    _p, org_id, pool, _m = await _open(request, household_id)
    await _own(pool, org_id, household_id, payment_id)
    kw = {"org_id": org_id, "actor_id": p.user_id, "payment_id": payment_id}
    b = body
    calls = {
        "edit": lambda: PS.edit(pool, changes={k: (str(v) if isinstance(v, UUID) else v)
                                               for k, v in (b.changes or {}).items()}, **kw),
        "submit": lambda: PS.submit(pool, **kw),
        "mark_sent": lambda: PS.mark_sent(pool, channel=b.channel or "", note=b.note, external_ref=b.external_ref, **kw),
        "consent_requested": lambda: PS.consent_requested(pool, note=b.note, **kw),
        "acknowledge": lambda: PS.acknowledge(pool, external_ref=b.external_ref, note=b.note, **kw),
        "settle": lambda: PS.settle(pool, settled_on=b.settled_on, note=b.note, **kw),
        "custodian_reject": lambda: PS.custodian_reject(pool, reason=b.reason or "", **kw),
        "return": lambda: PS.record_return(pool, return_code=b.return_code or "", reason=b.reason, **kw),
        "reconcile": lambda: PS.reconcile(pool, method=b.method or "", transaction_ids=b.transaction_ids,
                                          reference=b.reference, note=b.note, **kw),
        "close": lambda: PS.close(pool, note=b.note, **kw),
        "cancel": lambda: PS.cancel(pool, reason=b.reason or "", **kw),
    }
    if action == "settle" and b.settled_on is None:
        raise HTTPException(status_code=422, detail="settled_on is required")
    await _run(calls[action]())
    async with pool.acquire() as conn:
        d = await PS.detail(conn, org_id, payment_id)
    return {"payment": _with_actions([d], p, False)[0]}
