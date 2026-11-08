"""REP13 — the long/short extension (/long-short/…). services.long_short holds the rules; every route names its gate
(docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_proposals     GET  /long-short/accounts/{id}                      the account panel: eligibility (each piece),
                                                                         margin agreement, settings + draft + history,
                                                                         the short book, realized short-term YTD
                     GET  /long-short/borrow-rates                       the entered borrow-rate table (platform + org)
                     GET  /long-short/proposals/{id}                     a long/short proposal's details: both books,
                                                                         borrow, cost comparison, tracking error,
                                                                         §1259 blocks, harvest, disclosures, margin,
                                                                         the member summary, blocking, acknowledgement
  view_tax           GET  /long-short/accounts/{id}/tax-estimate          the account's estimated tax position (estimate)
  manage_proposals   PUT  /long-short/accounts/{id}/settings/draft        save a draft of the settings
                     POST /long-short/accounts/{id}/settings              save the settings (a new version)
                     POST /long-short/accounts/{id}/margin-agreement      record the margin agreement (opens the task)
                     POST /long-short/accounts/{id}/margin-agreement/remove   remove it (a reason; at once)
                     POST /long-short/borrow-rates                        an org borrow rate (with as-of)
                     POST /long-short/proposals/{id}/acknowledge          the maker acknowledges the disclosures
                     POST /long-short/proposals/{id}/execute              route an approved proposal (adapter | manual)
  approve_proposals  POST /long-short/margin-agreements/{id}/approve      (never the maker)
                     POST /long-short/margin-agreements/{id}/reject       (a note)
  Super Admin        POST /long-short/borrow-rates/platform               a platform borrow rate

Super Admin first (services.authz). org_id is only ever the caller's (get_org_id) — never a body or path value;
every request model forbids extras. Another org's account / proposal / record is a 404. Amounts and rates travel
as decimal STRINGS; a JSON float is a 422.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from routers.entities import get_org_id
from services import human_tasks as HT
from services import disclosures as DL
from services import long_short as LS
from services import rebalance_proposals as RP
from services.authz import require_permission, require_super_admin
from services.custody.trading import ShortSaleNotDeclared, _decimal
from services.database import get_pool
from services import sod  # S30 — the ONE maker-checker rule

router = APIRouter(tags=["long-short"])

VIEW, MANAGE, APPROVE = "view_proposals", "manage_proposals", "approve_proposals"
VIEW_TAX = "view_tax"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExclusionIn(_Body):
    global_security_id: UUID
    reason: str = Field(default="never shorted in this account", max_length=500)


class SettingsIn(_Body):
    extension: Literal["130_30", "150_50", "custom"]
    gross_long: Decimal | None = None
    gross_short: Decimal | None = None
    tracking_error_limit: Decimal
    max_short_per_name: Decimal
    borrow_cost_ceiling_bps: Decimal
    exclusions: list[ExclusionIn] = Field(default_factory=list, max_length=500)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("gross_long", "gross_short", "tracking_error_limit", "max_short_per_name",
                     "borrow_cost_ceiling_bps", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)

    def values(self) -> dict:
        return {"extension": self.extension, "gross_long": self.gross_long, "gross_short": self.gross_short,
                "tracking_error_limit": self.tracking_error_limit, "max_short_per_name": self.max_short_per_name,
                "borrow_cost_ceiling_bps": self.borrow_cost_ceiling_bps,
                "exclusions": [{"global_security_id": str(x.global_security_id), "reason": x.reason}
                               for x in self.exclusions], "note": self.note}


class MarginAgreementIn(_Body):
    agreement_date: date
    document_id: UUID
    note: str | None = Field(default=None, max_length=2000)


class Reason(_Body):
    reason: str = Field(min_length=1, max_length=2000)


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


class BorrowRateIn(_Body):
    global_security_id: UUID
    rate_bps: Decimal
    rebate_bps: Decimal | None = None
    as_of: date
    source: str = Field(min_length=1, max_length=300)

    @field_validator("rate_bps", "rebate_bps", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class AcknowledgeIn(_Body):
    items: list[str] = Field(min_length=1, max_length=20)


class ExecuteIn(_Body):
    route: Literal["adapter", "manual"] = "adapter"


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    from services import account_models as AM

    if isinstance(exc, (LS.LongShortNotFound, RP.ProposalNotFound, HT.HumanTaskNotFound, AM.AccountModelNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (LS.MakerCheckerViolation, RP.MakerCheckerViolation, HT.MakerCheckerError, PermissionError,
                        HT.HumanTaskPermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (LS.LongShortConflict, RP.ProposalConflict, HT.HumanTaskConflict, ShortSaleNotDeclared,
                        AM.AccountModelConflict)):
        detail = {"message": str(exc)}
        if isinstance(exc, LS.LongShortNotEligible):
            detail["missing"] = exc.missing
        if isinstance(exc, LS.ConstructiveSaleBlocked):
            detail["constructive_sale"] = exc.hits
        raise HTTPException(status_code=409, detail=detail if len(detail) > 1 else str(exc)) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.CheckViolationError):
        raise HTTPException(status_code=422, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, (LS.LongShortError, LS.R.LongShortRuleError, RP.ProposalError, HT.HumanTaskError,
                        AM.AccountModelError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


async def _act(coro):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


def _permissions(principal) -> dict:
    sa = principal.is_super_admin
    return {"can_read": sa or VIEW in principal.permissions, "can_write": sa or MANAGE in principal.permissions,
            "can_approve": sa or APPROVE in principal.permissions, "is_super_admin": sa,
            "can_enter_platform_rates": sa, "read_permission": VIEW, "write_permission": MANAGE,
            "approve_permission": APPROVE}


def _vocab(perms: dict) -> dict:
    v = LS.vocabularies()
    v["editable"] = list(LS.SETTING_FIELDS) if perms["can_write"] is True else []
    v["inline_editable"] = []
    return v


def _panel_actions(panel: dict, perms: dict, user_id: str) -> list[str]:
    out = []
    el = panel["eligibility"]
    ma = panel["margin_agreement"]
    if perms["can_write"] is True:
        if el["eligible"]:
            out += ["save_settings", "save_draft"]
        if ma is None:
            out.append("record_margin_agreement")
        elif ma["status"] == "approved":
            out.append("remove_margin_agreement")
    if perms["can_approve"] is True and ma and ma["status"] == "pending_approval" and sod.may_check_display(user_id, [ma["maker_id"]]):
        out += ["approve_margin_agreement", "reject_margin_agreement"]
    return out


# ── Reads ───────────────────────────────────────────────────────────────────
@router.get("/long-short/accounts/{account_id}")
async def account_panel(request: Request, account_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            panel = await LS.account_panel(conn, org_id, account_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {**panel, "permissions": perms, "vocabularies": _vocab(perms),
            "actions": _panel_actions(panel, perms, principal.user_id)}


@router.get("/long-short/accounts/{account_id}/tax-estimate")
async def tax_estimate(request: Request, account_id: str):
    principal = require_permission(request, VIEW_TAX)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            est = await LS.tax_estimate(conn, org_id, account_id)
            note = await DL.resolve_safe(conn, "tax.estimate_label", org_id=org_id, account_id=account_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"tax_estimate": est, "estimate_disclosure": DL.public(note), "permissions": {"can_read": True, "is_super_admin": principal.is_super_admin,
                                                 "read_permission": VIEW_TAX}}


@router.get("/long-short/borrow-rates")
async def borrow_rates(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await LS.list_borrow_rates(conn, org_id)
    actions = (["enter_org_rate"] if perms["can_write"] is True else []) + (
        ["enter_platform_rate"] if perms["can_enter_platform_rates"] is True else [])
    return {"rows": rows, "permissions": perms, "vocabularies": _vocab(perms), "actions": actions}


@router.get("/long-short/proposals/{proposal_id}")
async def proposal_details(request: Request, proposal_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            p = await RP.load(conn, org_id, proposal_id)
            d = LS.details_out(await LS.details_of(conn, p["id"]))
            if d is None:
                raise LS.LongShortNotFound("not a long/short proposal")
            why = await LS.approval_refusal(conn, org_id, p) if p["status"] == "pending_approval" else None
            ds = await LS.disclosure_state(conn, org_id, p)      # DISCLOSURES1 — the version in force NOW
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    res = ds["resolved"]
    d["acknowledgement_items"] = ds["items"]
    d["disclosure_library"] = {"key": LS.RISKS_KEY, "source": res.get("source"), "why": res.get("why"),
                               "version_id": res.get("version_id"), "version_no": res.get("version_no"),
                               "content_sha256": res.get("content_sha256"), "title": res.get("title"),
                               "file_href": res.get("file_href"), "required": ds["required"],
                               "acknowledged": ds["acknowledged"], "current": ds["current"],
                               "risks": res.get("items")}
    is_maker = p["maker_id"] is not None and str(p["maker_id"]) == principal.user_id
    needs_ack = d["acknowledged_by"] is None or (ds["required"] and not ds["current"])
    actions = []
    if perms["can_write"] is True and is_maker and p["status"] == "draft" and needs_ack:
        actions.append("acknowledge")
    if perms["can_write"] is True and p["status"] == "approved" and not (RP._loads(p["execution"]) or {}).get(
            "instruction_ids"):
        actions += ["execute_adapter", "execute_manual"]
    return {"details": d, "proposal": {"id": str(p["id"]), "status": p["status"], "maker_id": str(p["maker_id"])
                                       if p["maker_id"] else None},
            "approval_refusal": why, "permissions": {**perms, "is_maker": is_maker}, "vocabularies": _vocab(perms),
            "actions": actions}


# ── Settings ────────────────────────────────────────────────────────────────
@router.put("/long-short/accounts/{account_id}/settings/draft")
async def save_draft(request: Request, account_id: str, body: SettingsIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(LS.save_settings(pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                                      values=body.values(), status="draft"))
    return {"settings": LS.settings_out(row)}


@router.post("/long-short/accounts/{account_id}/settings", status_code=201)
async def save(request: Request, account_id: str, body: SettingsIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(LS.save_settings(pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                                      values=body.values(), status="saved"))
    return {"settings": LS.settings_out(row)}


# ── Margin agreement (suitability) ──────────────────────────────────────────
@router.post("/long-short/accounts/{account_id}/margin-agreement", status_code=201)
async def record_margin(request: Request, account_id: str, body: MarginAgreementIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(LS.record_margin_agreement(pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                                                agreement_date=body.agreement_date, document_id=body.document_id,
                                                note=body.note))
    return {"record": LS.suitability_out(row)}


@router.post("/long-short/accounts/{account_id}/margin-agreement/remove")
async def remove_margin(request: Request, account_id: str, body: Reason):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(LS.remove_margin_agreement(pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                                                reason=body.reason))
    return {"record": LS.suitability_out(row)}


@router.post("/long-short/margin-agreements/{record_id}/approve")
async def approve_margin(request: Request, record_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(LS.decide_margin_agreement(pool, org_id=org_id, actor_id=principal.user_id, record_id=record_id,
                                                outcome="approve", note=body.note))
    return {"record": LS.suitability_out(row)}


@router.post("/long-short/margin-agreements/{record_id}/reject")
async def reject_margin(request: Request, record_id: str, body: Reason):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(LS.decide_margin_agreement(pool, org_id=org_id, actor_id=principal.user_id, record_id=record_id,
                                                outcome="reject", note=body.reason))
    return {"record": LS.suitability_out(row)}


# ── Borrow rates ────────────────────────────────────────────────────────────
@router.post("/long-short/borrow-rates", status_code=201)
async def enter_org_rate(request: Request, body: BorrowRateIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(LS.enter_borrow_rate(pool, org_id=org_id, actor_id=principal.user_id,
                                          is_super_admin=principal.is_super_admin, scope="org",
                                          global_security_id=body.global_security_id, rate_bps=body.rate_bps,
                                          rebate_bps=body.rebate_bps, as_of=body.as_of, source=body.source))
    return {"rate": LS.rate_out(row)}


@router.post("/long-short/borrow-rates/platform", status_code=201)
async def enter_platform_rate(request: Request, body: BorrowRateIn):
    principal = require_super_admin(request)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(LS.enter_borrow_rate(pool, org_id=org_id, actor_id=principal.user_id,
                                          is_super_admin=principal.is_super_admin, scope="platform",
                                          global_security_id=body.global_security_id, rate_bps=body.rate_bps,
                                          rebate_bps=body.rebate_bps, as_of=body.as_of, source=body.source))
    return {"rate": LS.rate_out(row)}


# ── Proposals ───────────────────────────────────────────────────────────────
@router.post("/long-short/proposals/{proposal_id}/acknowledge")
async def acknowledge(request: Request, proposal_id: str, body: AcknowledgeIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(LS.acknowledge(pool, org_id=org_id, actor_id=principal.user_id, proposal_id=proposal_id,
                                    items=body.items))
    return {"details": LS.details_out(row)}


@router.post("/long-short/proposals/{proposal_id}/execute")
async def execute(request: Request, proposal_id: str, body: ExecuteIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            p = await RP.load(conn, org_id, proposal_id)
            if await LS.details_of(conn, p["id"]) is None:
                raise LS.LongShortNotFound("not a long/short proposal")
            actor = await HT.assert_human(conn, principal.user_id, org_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    row = await _act(LS.execute(pool, org_id=org_id, p=p, actor=actor, route=body.route))
    return {"proposal": RP.proposal_out(row)}
