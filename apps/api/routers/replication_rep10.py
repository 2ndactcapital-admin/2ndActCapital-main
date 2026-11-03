"""REP10 — tilts, completion, consolidation, backtests and the hypothetical-performance policy (/replication/…).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_replication  GET  /replication/tilts/accounts/{account_id}               tilt sets + envelope
                    POST /replication/tilts/accounts/{account_id}/preview       live preview (nothing written)
                    GET  /replication/completion/accounts/{account_id}          completion preview + settings
                    GET  /replication/consolidation/households/{household_id}   overlapping funds + actions
                    GET  /replication/backtests                                 backtests (hypothetical)
                    GET  /replication/backtests/{backtest_id}                   one backtest with its disclosure
                    POST /replication/backtests/{backtest_id}/pdf               its PDF (on demand, never stored)
                    GET  /replication/hypothetical-policy                       policy versions + gate
  manage_replication POST /replication/tilts/accounts/{account_id}              a new draft tilt set
                    POST /replication/tilts/sets/{set_id}/lines                 replace a draft's lines
                    POST /replication/tilts/sets/{set_id}/submit                draft → awaiting approval (task)
                    POST /replication/tilts/sets/{set_id}/archive               discard a draft / retire a set
                    POST /replication/completion/accounts/{account_id}          completion settings (a new version)
                    POST /replication/completion/accounts/{account_id}/confirm  ask to confirm stale held-away data
                    POST /replication/consolidation/households/{household_id}   act on one overlapping holding
                    POST /replication/backtests                                 run a backtest (a person, never a sweep)
  approve_replication POST /replication/tilts/sets/{set_id}/approve | /reject   (never the maker)
  manage_hypothetical_policy POST /replication/hypothetical-policy              a new policy version (draft)
                    POST /replication/hypothetical-policy/{policy_id}/approve | /reject   (never the writer)
  active user (own scope) GET /replication/member/backtests                    a MEMBER's view: empty unless the
                                                                               approved policy allows members; only
                                                                               the member's own households

Super Admin first (services.authz). org_id is only ever the caller's (get_org_id) — never a body or path value;
every request model forbids extras. Another org's row is a 404. Weights and amounts travel as decimal STRINGS; a
JSON float is a 422. Every backtest and projected tracking error is labelled hypothetical and carries the
policy's disclosure (or "staff use only").
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
from services import backtest as BT
from services import completion as CO
from services import human_tasks as HT
from services import hypothetical_policy as HP
from services import rep10_common as C
from services import tilts as TL
from services.authz import require_active, require_permission
from services.custody.trading import _decimal
from services.database import get_pool

router = APIRouter(tags=["replication-rep10"])

VIEW, MANAGE, APPROVE = "view_replication", "manage_replication", "approve_replication"
POLICY = HP.MANAGE


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TiltLine(_Body):
    kind: Literal["security", "sector", "factor"]
    subject: str = Field(min_length=1, max_length=200)
    delta: Decimal
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("delta", mode="before")
    @classmethod
    def _d(cls, v, info):
        d = _decimal(v, info.field_name)
        if d is None:
            raise ValueError("delta is required")
        return d


class TiltPreview(_Body):
    lines: list[TiltLine] = Field(max_length=200)


class TiltCreate(_Body):
    name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    lines: list[TiltLine] = Field(min_length=1, max_length=200)


class Lines(_Body):
    lines: list[TiltLine] = Field(min_length=1, max_length=200)


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


class Reason(_Body):
    reason: str = Field(min_length=1, max_length=2000)


class Completion(_Body):
    count_elsewhere: bool
    count_authorities: list[Literal["read_only", "advise", "managed"]] = Field(min_length=1, max_length=3)
    stale_after_days: int | None = Field(default=None, ge=1, le=366)
    reason: str = Field(min_length=1, max_length=2000)


class Confirm(_Body):
    held_away_account_id: UUID


class Consolidate(_Body):
    account_id: UUID
    fund_security_id: UUID
    executor_type: Literal["member", "other_adviser"] = "member"


class BacktestRun(_Body):
    model_id: UUID
    fund_security_id: UUID
    window: Literal["2008", "2020", "2022", "custom"]
    start: date | None = None
    end: date | None = None
    cadence: Literal["monthly", "quarterly", "annually", "none"] = "quarterly"
    harvest: bool = True
    loss_pct: Decimal | None = None
    loss_min: Decimal | None = None
    notional: Decimal | None = None
    household_id: UUID | None = None

    @field_validator("loss_pct", "loss_min", "notional", mode="before")
    @classmethod
    def _d(cls, v, info):
        return _decimal(v, info.field_name)


class PolicyVersion(_Body):
    policy_text: str = Field(min_length=1, max_length=20000)
    audience: Literal["staff_only", "staff_and_members"]
    disclosure_text: str = Field(min_length=1, max_length=5000)
    reason: str = Field(min_length=1, max_length=2000)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, C.Rep10NotFound) or isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (C.MakerCheckerViolation, HT.MakerCheckerError, HT.HumanTaskPermissionError, PermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, TL.TiltRefused):
        raise HTTPException(status_code=422, detail={"message": str(exc), "refused": exc.reasons}) from exc
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
    from services.xray import XrayConflict, XrayError
    if isinstance(exc, (AccountModelConflict, ProposalConflict, XrayConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, XrayError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _perms(principal) -> dict:
    sa = principal.is_super_admin
    return {"can_read": sa or VIEW in principal.permissions, "can_write": sa or MANAGE in principal.permissions,
            "can_approve": sa or APPROVE in principal.permissions,
            "can_manage_policy": sa or POLICY in principal.permissions, "is_super_admin": sa,
            "read_permission": VIEW, "write_permission": MANAGE, "approve_permission": APPROVE,
            "policy_permission": POLICY}


def _vocab(perms: dict) -> dict:
    v = {**TL.vocabularies(), **CO.vocabularies(), **BT.vocabularies(), **HP.vocabularies()}
    v["editable"] = ["name", "reason", "lines"] if perms["can_write"] is True else []
    v["completion_editable"] = ["count_elsewhere", "count_authorities", "stale_after_days"] \
        if perms["can_write"] is True else []
    v["policy_editable"] = ["policy_text", "audience", "disclosure_text"] if perms["can_manage_policy"] is True else []
    return v


def _pdf(pdf: bytes, filename: str) -> Response:
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


def _lines(body_lines) -> list[dict]:
    return [ln.model_dump() for ln in body_lines]


# ── Tilts ───────────────────────────────────────────────────────────────────
@router.get("/replication/tilts/accounts/{account_id}")
async def tilt_sets(request: Request, account_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _perms(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await TL.sets_for(conn, org_id, account_id)
            base = await TL.preview(conn, org_id, account_id, [])
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    for r in rows:
        r["actions"] = TL.actions_for(r, can_write=perms["can_write"] is True, can_approve=perms["can_approve"] is True,
                                      user_id=principal.user_id)
    open_set = any(r["status"] in ("draft", "pending_approval") for r in rows)
    return {"rows": rows, "base": base, "permissions": perms, "vocabularies": _vocab(perms),
            "actions": ["create"] if perms["can_write"] is True and not open_set else [],
            "label": C.HYPOTHETICAL, "hypothetical_note": TL.HYPOTHETICAL_NOTE}


@router.post("/replication/tilts/accounts/{account_id}/preview")
async def tilt_preview(request: Request, account_id: str, body: TiltPreview):
    require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            return C.clean(await TL.preview(conn, org_id, account_id, _lines(body.lines)))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.post("/replication/tilts/accounts/{account_id}", status_code=201)
async def tilt_create(request: Request, account_id: str, body: TiltCreate):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    try:
        out = await TL.create(await get_pool(), org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                              name=body.name, reason=body.reason, lines=_lines(body.lines))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"tilt_set": out}


@router.post("/replication/tilts/sets/{set_id}/lines")
async def tilt_lines(request: Request, set_id: str, body: Lines):
    principal = require_permission(request, MANAGE)
    try:
        out = await TL.replace_lines(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                     set_id=set_id, lines=_lines(body.lines))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"tilt_set": out}


@router.post("/replication/tilts/sets/{set_id}/submit")
async def tilt_submit(request: Request, set_id: str):
    principal = require_permission(request, MANAGE)
    try:
        out = await TL.submit(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id, set_id=set_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"tilt_set": C.clean(out)}


@router.post("/replication/tilts/sets/{set_id}/archive")
async def tilt_archive(request: Request, set_id: str, body: Reason):
    principal = require_permission(request, MANAGE)
    try:
        out = await TL.archive_set(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                   set_id=set_id, reason=body.reason)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"tilt_set": out}


@router.post("/replication/tilts/sets/{set_id}/approve")
async def tilt_approve(request: Request, set_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    try:
        out = await TL.decide(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id, set_id=set_id,
                              outcome="approve", note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"tilt_set": out}


@router.post("/replication/tilts/sets/{set_id}/reject")
async def tilt_reject(request: Request, set_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    try:
        out = await TL.decide(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id, set_id=set_id,
                              outcome="reject", note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"tilt_set": out}


# ── Completion ──────────────────────────────────────────────────────────────
@router.get("/replication/completion/accounts/{account_id}")
async def completion_view(request: Request, account_id: str):
    principal = require_permission(request, VIEW)
    perms = _perms(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            out = await CO.preview(conn, get_org_id(request), account_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    acts = []
    if perms["can_write"] is True and out["account"].get("authority") in (None, "managed"):
        acts.append("settings")
        if out.get("needs_confirmation"):
            acts.append("confirm")
    return {**C.clean(out), "permissions": perms, "vocabularies": _vocab(perms), "actions": acts}


@router.post("/replication/completion/accounts/{account_id}")
async def completion_settings(request: Request, account_id: str, body: Completion):
    principal = require_permission(request, MANAGE)
    try:
        out = await CO.set_settings(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                    account_id=account_id, count_elsewhere=body.count_elsewhere,
                                    count_authorities=list(body.count_authorities),
                                    stale_after_days=body.stale_after_days, reason=body.reason)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"settings": out}


@router.post("/replication/completion/accounts/{account_id}/confirm")
async def completion_confirm(request: Request, account_id: str, body: Confirm):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            prev = await CO.preview(conn, org_id, account_id)
        if str(body.held_away_account_id) not in {f["account_id"] for f in prev.get("needs_confirmation") or []}:
            raise C.Rep10Conflict("that held-away account's data does not need confirmation for this completion")
        out = await CO.confirm_request(pool, org_id=org_id, actor_id=principal.user_id,
                                       account_id=body.held_away_account_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"confirmation": out}


# ── Consolidation ───────────────────────────────────────────────────────────
@router.get("/replication/consolidation/households/{household_id}")
async def consolidation_view(request: Request, household_id: str):
    principal = require_permission(request, VIEW)
    perms = _perms(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            out = await CO.consolidation(conn, get_org_id(request), household_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    for ln in out["lines"]:
        ln["actions"] = ["consolidate"] if perms["can_write"] is True and ln["action"] != "listed" else []
    return {**C.clean(out), "permissions": perms, "vocabularies": _vocab(perms)}


@router.post("/replication/consolidation/households/{household_id}")
async def consolidation_act(request: Request, household_id: str, body: Consolidate):
    principal = require_permission(request, MANAGE)
    try:
        out = await CO.consolidate(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                   household_id=household_id, account_id=body.account_id,
                                   fund_security_id=body.fund_security_id, executor_type=body.executor_type)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return C.clean(out)


# ── Backtests ───────────────────────────────────────────────────────────────
@router.get("/replication/backtests")
async def backtests(request: Request, model_id: UUID | None = Query(default=None)):
    principal = require_permission(request, VIEW)
    perms = _perms(principal)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            gate = HP.gate(await HP.current(conn, org_id))
            rows = [BT.backtest_out(r, gate) for r in await BT.list_backtests(conn, org_id, model_id=model_id)]
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"rows": rows, "policy": gate, "label": C.HYPOTHETICAL, "disclosure": gate["disclosure"],
            "permissions": perms, "vocabularies": _vocab(perms), "actions": ["run"] if perms["can_write"] is True else []}


@router.post("/replication/backtests", status_code=201)
async def backtest_run(request: Request, body: BacktestRun):
    principal = require_permission(request, MANAGE)
    try:
        out = await BT.run(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                           model_id=body.model_id, fund_security_id=body.fund_security_id, window_preset=body.window,
                           start=body.start, end=body.end, cadence=body.cadence, harvest=body.harvest,
                           loss_pct=body.loss_pct, loss_min=body.loss_min, notional=body.notional,
                           household_id=body.household_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"backtest": out, "label": C.HYPOTHETICAL, "disclosure": out["disclosure"]}


@router.get("/replication/backtests/{backtest_id}")
async def backtest_detail(request: Request, backtest_id: str):
    principal = require_permission(request, VIEW)
    perms = _perms(principal)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            gate = HP.gate(await HP.current(conn, org_id))
            out = BT.backtest_out(await BT.load(conn, org_id, backtest_id), gate)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"backtest": out, "label": C.HYPOTHETICAL, "disclosure": gate["disclosure"], "permissions": perms,
            "vocabularies": _vocab(perms)}


@router.post("/replication/backtests/{backtest_id}/pdf")
async def backtest_pdf(request: Request, backtest_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            gate = HP.gate(await HP.current(conn, org_id))
            b = BT.backtest_out(await BT.load(conn, org_id, backtest_id), gate)
            org = await conn.fetchval("SELECT name FROM organizations WHERE id = $1::uuid", str(org_id))
            mname = await conn.fetchval("SELECT name FROM portfolio.models WHERE id = $1::uuid", b["model_id"])
            fname = await conn.fetchval("SELECT name FROM portfolio.securities_global WHERE id = $1::uuid",
                                        b["fund_security_id"])
            await C.audit(conn, org_id, principal.user_id, "backtest_pdf", "backtest", b["id"], {})
        pdf = BT.build_pdf(b, org_name=org or "", model_name=mname or "", fund_name=fname or "")
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _pdf(pdf, f"backtest-hypothetical-{b['id'][:8]}.pdf")


@router.get("/replication/member/backtests")
async def member_backtests(request: Request):
    """A MEMBER's view — own scope: the caller's own visible households only, and nothing at all unless the approved
    policy's audience is staff_and_members. The same rule for staff calling it (it is the member envelope)."""
    principal = require_active(request)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            return C.clean(await BT.member_view(pool, conn, org_id, principal.user_id))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


# ── The hypothetical-performance policy ─────────────────────────────────────
@router.get("/replication/hypothetical-policy")
async def policy_list(request: Request):
    principal = require_permission(request, VIEW)
    perms = _perms(principal)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await HP.list_policies(conn, org_id)
            cur = await HP.current(conn, org_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    for r in rows:
        r["actions"] = (["approve", "reject"] if r["status"] == "draft" and perms["can_manage_policy"] is True
                        and r["maker_id"] != str(principal.user_id) else [])
    pending = any(r["status"] == "draft" for r in rows)
    return {"rows": rows, "current": HP.policy_out(cur), "gate": HP.gate(cur), "permissions": perms,
            "vocabularies": _vocab(perms),
            "actions": ["request"] if perms["can_manage_policy"] is True and not pending else []}


@router.post("/replication/hypothetical-policy", status_code=201)
async def policy_request(request: Request, body: PolicyVersion):
    principal = require_permission(request, POLICY)
    try:
        out = await HP.request(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                               policy_text=body.policy_text, audience=body.audience,
                               disclosure_text=body.disclosure_text, reason=body.reason)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"policy": out}


@router.post("/replication/hypothetical-policy/{policy_id}/approve")
async def policy_approve(request: Request, policy_id: str, body: Decision):
    principal = require_permission(request, POLICY)
    try:
        out = await HP.decide(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                              policy_id=policy_id, outcome="approve", note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"policy": out}


@router.post("/replication/hypothetical-policy/{policy_id}/reject")
async def policy_reject(request: Request, policy_id: str, body: Decision):
    principal = require_permission(request, POLICY)
    try:
        out = await HP.decide(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                              policy_id=policy_id, outcome="reject", note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"policy": out}
