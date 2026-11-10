"""CASH06 — Household → Treasury → RULES (stage A): standing cash rules that DRAFT payment instructions for approval.

READ (``view_portfolio | initiate_payment | approve_payment``, then the ONE member / VISIBILITY1 scope — the SAME guard
as Payments, routers.payments._open: another family's household is a 404; a member reads their family's ACTIVE rules
read-only, last four only, with the ``cash.rules`` disclosure):
    GET  /api/v1/cash/households/{household_id}/rules                         list (status, last run, next run, holds)
    GET  /api/v1/cash/households/{household_id}/rules/{version_id}            one version + its versions + run history
WRITE (``initiate_payment``; a client role is refused even if it ever held it):
    GET  /api/v1/cash/households/{household_id}/rules/options                 the editor's choices, decided server-side
    POST /api/v1/cash/households/{household_id}/rules/preview                 what it WOULD draft today (writes nothing)
    POST /api/v1/cash/households/{household_id}/rules                         a new rule (draft; the drafter owns it)
    POST /api/v1/cash/households/{household_id}/rules/{version_id}/{action}
         action ∈ edit (a draft) · new_version (a running rule: a new draft version) · submit · pause · resume · retire
APPROVE (``approve_payment``; the S30 FLOOR — never a maker or the owner, Super Admin included):
    POST /api/v1/cash/households/{household_id}/rules/{version_id}/approve | /reject

Every write goes through services.cash_rules (same owner only, declared rails, the owner's authority, caps within the
org ceiling, maker-checker, audit). ``org_id`` comes from the verified session only (Rule 6); every body model forbids
extra fields. Nothing here approves, sends or moves money: a rule's firing drafts instructions a person approves.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from routers.payments import _holds, _not_client, _open
from services import authz
from services import cash_rules as CR
from services import payments as PS
from services.authz import require_permission

router = APIRouter(tags=["cash-rules"])

ACTIONS = ("edit", "new_version", "submit", "pause", "resume", "retire")
READ_PERMISSIONS = ("view_portfolio", "initiate_payment", "approve_payment")


async def _scope(request: Request, household_id: UUID):
    """The read gate, named here, then THE SAME scope as Payments (routers.payments._open: the member path or
    VISIBILITY1 — another family's household is a 404). One guard for both screens; never a copy of it."""
    authz.require_any_permission(request, READ_PERMISSIONS)
    return await _open(request, household_id)


class DestinationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: UUID
    pct: str | None = None
    amount: str | None = None


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    rule_type: str
    source_account_id: UUID
    destinations: list[DestinationIn] = []
    basis: str | None = None
    condition: str | None = None
    target_amount: str | None = None
    supporting_account_id: UUID | None = None
    frequency: str | None = None
    day_of_week: int | None = None
    day_of_month: int | None = None
    rail: str
    cap_per_firing: str
    max_per_day: int | None = None
    reason: str | None = None


class PreviewIn(RuleIn):
    example_credit: str | None = None


class ActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict[str, Any] | None = None      # edit / new_version (merged onto the version's terms)
    reason: str | None = None                  # pause / retire / a new version's reason
    note: str | None = None


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = None


def _data(body: RuleIn) -> dict:
    d = body.model_dump(exclude={"reason", "example_credit"})
    d["source_account_id"] = str(d["source_account_id"])
    d["supporting_account_id"] = str(d["supporting_account_id"]) if d.get("supporting_account_id") else None
    d["destinations"] = [{k: (str(v) if k == "account_id" else v) for k, v in x.items() if v is not None}
                         for x in d.get("destinations") or []]
    return d


def _err(exc: Exception) -> HTTPException | None:
    import asyncpg
    from services import human_tasks as HTK

    if isinstance(exc, (CR.RuleNotFound, PS.PaymentNotFound, HTK.HumanTaskNotFound)):
        return HTTPException(status_code=404, detail=str(exc) or "Not found")
    if isinstance(exc, CR.RuleError):
        return HTTPException(status_code=422, detail={"message": str(exc), "reasons": exc.reasons})
    if isinstance(exc, (CR.MakerCheckerViolation, PermissionError, HTK.HumanTaskPermissionError, HTK.MakerCheckerError,
                        HTK.AgentActorRefused)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (CR.RuleConflict, HTK.HumanTaskConflict, asyncpg.UniqueViolationError)):
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


async def _labels(conn, org_id, household_id) -> dict:
    from services import disclosures as D

    out = {}
    for key in (CR.DISCLOSURE_KEY, PS.DISCLOSURE_KEY):
        out[key] = D.public(await D.resolve_safe(conn, key, org_id=org_id, household_id=household_id))
    return out


def _envelope(p, member: bool, household_id, labels: dict) -> dict:
    can_w = not member and _holds(p, CR.INITIATE)
    can_a = not member and _holds(p, CR.APPROVE)
    return {"household": {"id": str(household_id)}, "member_view": member, "labels": labels,
            "permissions": {"can_read": True, "can_write": can_w, "can_approve": can_a,
                            "is_super_admin": bool(p.is_super_admin), "read_permission": "view_portfolio",
                            "write_permission": CR.INITIATE if can_w else None,
                            "approve_permission": CR.APPROVE if can_a else None},
            "vocabularies": CR.vocabularies(can_write=can_w)}


def _with_actions(v: dict, p, member: bool) -> dict:
    return {**v, "actions": CR.actions_for(v, can_write=not member and _holds(p, CR.INITIATE),
                                           can_approve=not member and _holds(p, CR.APPROVE), user_id=p.user_id,
                                           member=member)}


@router.get("/cash/households/{household_id}/rules")
async def list_rules_route(request: Request, household_id: UUID):
    """The household's cash rules (a member: their family's ACTIVE rules, read-only, with the cash.rules text)."""
    p, org_id, pool, member = await _scope(request, household_id)
    async with pool.acquire() as conn:
        items = await _run(CR.list_rules(conn, org_id, household_id, member=member))
        labels = await _labels(conn, org_id, household_id)
    env = _envelope(p, member, household_id, labels)
    for it in items:
        it["current"] = _with_actions(it["current"], p, member)
        if it.get("open_version"):
            it["open_version"] = _with_actions(it["open_version"], p, member)
    offer = [{"key": "new", "label": "New rule"}] if env["permissions"]["can_write"] else []
    return {**env, "today": CR.today().isoformat(), "items": items, "actions": offer}


@router.get("/cash/households/{household_id}/rules/options")
async def rule_options_route(request: Request, household_id: UUID):
    """The editor: sources this caller may make a rule on (both authorities; the others with their reasons), each
    source's SAME-OWNER destinations and declared rails, the org ceilings."""
    p = require_permission(request, "initiate_payment")
    _not_client(p)
    _p, org_id, pool, _m = await _scope(request, household_id)
    async with pool.acquire() as conn:
        out = await _run(CR.options(pool, conn, org_id, household_id, p.user_id))
        labels = await _labels(conn, org_id, household_id)
    return {**_envelope(p, False, household_id, labels), **out}


@router.post("/cash/households/{household_id}/rules/preview")
async def rule_preview_route(request: Request, household_id: UUID, body: PreviewIn):
    """What this rule WOULD draft today, from live balances — nothing is written, nothing is sent."""
    p = require_permission(request, "initiate_payment")
    _not_client(p)
    _p, org_id, pool, _m = await _scope(request, household_id)
    data = {**_data(body), "_owner_id": p.user_id, "example_credit": body.example_credit}
    async with pool.acquire() as conn:
        out = await _run(CR.preview(pool, conn, org_id, household_id, data))
    return {"preview": out}


@router.post("/cash/households/{household_id}/rules", status_code=201)
async def rule_create_route(request: Request, household_id: UUID, body: RuleIn):
    p = require_permission(request, "initiate_payment")
    _not_client(p)
    _p, org_id, pool, _m = await _scope(request, household_id)
    row = await _run(CR.save_draft(pool, org_id=org_id, actor_id=p.user_id, household_id=household_id,
                                   data=_data(body), reason=body.reason))
    async with pool.acquire() as conn:
        d = await CR.detail(conn, org_id, row["id"])
    return {"rule": {**d, "version": _with_actions(d["version"], p, False)}}


async def _own(pool, org_id, household_id, version_id) -> dict:
    async with pool.acquire() as conn:
        row = await _run(CR.load(conn, org_id, version_id))
    if str(row["household_id"]) != str(household_id):
        raise HTTPException(status_code=404, detail="cash rule not found")
    return row


@router.get("/cash/households/{household_id}/rules/{version_id}")
async def rule_detail_route(request: Request, household_id: UUID, version_id: UUID):
    p, org_id, pool, member = await _scope(request, household_id)
    row = await _own(pool, org_id, household_id, version_id)
    if member and row["status"] != "active":
        raise HTTPException(status_code=404, detail="cash rule not found")
    async with pool.acquire() as conn:
        labels = await _labels(conn, org_id, household_id)
        if member:
            accts = await CR._labels(conn, org_id, CR._rule_accounts(row))
            out = {"version": CR.version_out(row, accts, {}, member=True)}
        else:
            d = await CR.detail(conn, org_id, version_id)
            out = {**d, "version": _with_actions(d["version"], p, False),
                   "versions": [_with_actions(v, p, False) for v in d["versions"]]}
    return {**_envelope(p, member, household_id, labels), "rule": out}


async def _decide(request: Request, household_id: UUID, version_id: UUID, outcome: str, note: str | None):
    p = require_permission(request, "approve_payment")
    _not_client(p)
    _p, org_id, pool, _m = await _scope(request, household_id)
    await _own(pool, org_id, household_id, version_id)
    await _run(CR.decide(pool, org_id=org_id, actor_id=p.user_id, version_id=version_id, outcome=outcome, note=note))
    async with pool.acquire() as conn:
        d = await CR.detail(conn, org_id, version_id)
    return {"rule": {**d, "version": _with_actions(d["version"], p, False)}}


@router.post("/cash/households/{household_id}/rules/{version_id}/approve")
async def rule_approve_route(request: Request, household_id: UUID, version_id: UUID, body: DecideIn | None = None):
    return await _decide(request, household_id, version_id, "approve", body.note if body else None)


@router.post("/cash/households/{household_id}/rules/{version_id}/reject")
async def rule_reject_route(request: Request, household_id: UUID, version_id: UUID, body: DecideIn | None = None):
    return await _decide(request, household_id, version_id, "reject", body.note if body else None)


@router.post("/cash/households/{household_id}/rules/{version_id}/{action}")
async def rule_action_route(request: Request, household_id: UUID, version_id: UUID, action: str, body: ActionIn):
    """The maker's steps, each through services.cash_rules (edit / a new version are drafts — nothing activates)."""
    if action not in ACTIONS:
        raise HTTPException(status_code=404, detail="Not found")
    p = require_permission(request, "initiate_payment")
    _not_client(p)
    _p, org_id, pool, _m = await _scope(request, household_id)
    row = await _own(pool, org_id, household_id, version_id)
    kw = {"org_id": org_id, "actor_id": p.user_id, "version_id": version_id}
    if action in ("edit", "new_version"):
        if action == "edit" and row["status"] != "draft":
            raise HTTPException(status_code=409, detail="only a draft is edited — a running rule takes a new version")
        if action == "new_version" and row["status"] not in ("active", "paused"):
            raise HTTPException(status_code=409, detail="a new version is drafted from a running (active or paused) rule")
        merged = {**CR.terms_out(row), **{k: v for k, v in (body.changes or {}).items() if k in CR.EDITABLE}}
        bad = sorted(set(body.changes or {}) - set(CR.EDITABLE))
        if bad:
            raise HTTPException(status_code=422, detail=f"not editable: {bad}")
        res = await _run(CR.save_draft(pool, org_id=org_id, actor_id=p.user_id, household_id=household_id,
                                       data=merged, rule_id=row["rule_id"], reason=body.reason))
        target = res["id"]
    else:
        calls = {
            "submit": lambda: CR.submit(pool, **kw),
            "pause": lambda: CR.pause(pool, reason=body.reason or "", **kw),
            "resume": lambda: CR.request_resume(pool, **kw),
            "retire": lambda: CR.retire(pool, reason=body.reason or "", **kw),
        }
        res = await _run(calls[action]())
        target = res["id"]
    async with pool.acquire() as conn:
        d = await CR.detail(conn, org_id, target)
    return {"rule": {**d, "version": _with_actions(d["version"], p, False)}}
