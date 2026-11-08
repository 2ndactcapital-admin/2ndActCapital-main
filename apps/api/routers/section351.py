"""REP14 — the Section 351 exit (/section351/…). services.section351 holds the rules; every route names its gate
(docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it). Endpoint functions carry an ``s351_`` prefix so the route
scanner never confuses one with a service function of the same name.

  view_proposals     GET  /section351/candidates                          candidates + ineligible (reasons), run-dry
                     GET  /section351/sponsor-profiles                    sponsor profiles (live + history)
                     GET  /section351/thresholds                          run-dry thresholds (live + history)
                     GET  /section351/accounts/{id}/test                  the 25 / 50 test at a valuation date
                     GET  /section351/plans                               plans
                     GET  /section351/plans/{id}                          a plan: accounts, readiness, timeline,
                                                                          basis records, transfers
                     GET  /section351/plans/{id}/instruction-sheet        the manual instruction sheet (PDF)
                     GET  /section351/basis-records/{id}/file/{kind}      a stored basis record (csv | pdf)
  manage_proposals   POST /section351/accounts/{id}/fixup                 the fix-up proposal (a rep5 proposal)
                     POST /section351/plans                               create a plan (draft + its tracker)
                     PATCH /section351/plans/{id}                         edit a draft
                     POST /section351/plans/{id}/accounts                 add a contributing account
                     POST /section351/plans/{id}/retest                   re-run every account's test
                     POST /section351/plans/{id}/ready | reopen | submit | cancel
                     POST /section351/plans/{id}/basis-record             export a new basis-record version
                     POST /section351/plans/{id}/route                    route an account's transfers (adapter|manual)
                     POST /section351/plan-accounts/{id}/remove | us-person | sponsor-review | consent |
                          tax-adviser | boot | acknowledge | confirm
                     POST /section351/basis-records/{id}/delivered        record the delivery
                     PATCH /section351/steps/{id}                         a tracker step's owner / due date / note
                     POST /section351/steps/{id}/done                     complete a step (its human task)
                     POST /section351/asset-classes                       an org classification
                     POST /section351/asset-classes/{id}/retire
  approve_proposals  POST /section351/plans/{id}/approve | reject         (never the maker or submitter)
  manage_org_settings POST /section351/sponsor-profiles                   a new sponsor profile (org admin)
                     POST /section351/sponsor-profiles/{key}/versions     a new version of one
                     PUT  /section351/thresholds                          a new thresholds version
  Super Admin        POST /section351/asset-classes/platform              a platform classification

Super Admin first (services.authz). org_id is only ever the caller's (get_org_id) — never a body or path value;
every request model forbids extras. Another org's plan / account / record is a 404. Amounts travel as decimal
STRINGS; a JSON float is a 422.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from routers.entities import get_org_id
from services import human_tasks as HT
from services import rebalance_proposals as RP
from services import section351 as S
from services.authz import require_permission, require_super_admin
from services.custody.trading import _decimal
from services.database import get_pool

router = APIRouter(tags=["section351"])

VIEW, MANAGE, APPROVE, ADMIN = S.VIEW, S.MANAGE, S.APPROVE, S.ADMIN


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WindowIn(_Body):
    opens: date
    closes: date
    note: str | None = Field(default=None, max_length=500)


class ProfileIn(_Body):
    name: str = Field(min_length=1, max_length=300)
    adviser_name: str | None = Field(default=None, max_length=300)
    distributor_name: str | None = Field(default=None, max_length=300)
    contact: str | None = Field(default=None, max_length=1000)
    launch_windows: list[WindowIn] = Field(default_factory=list, max_length=50)
    eligible_owner_types: list[str] | None = None
    review_owner_types: list[str] | None = None
    us_persons_only: bool | None = None
    ineligible_assets: list[str] | None = None
    cash_rule: Literal["not_contributed", "contributed"] | None = None
    note: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "retired"] = "active"

    def data(self) -> dict:
        d = {k: v for k, v in self.model_dump().items() if v is not None}
        d["launch_windows"] = [w.model_dump() for w in self.launch_windows]
        return d


class ThresholdsIn(_Body):
    max_harvestable_loss_pct: Decimal
    min_unrealized_gain_pct: Decimal
    min_years_since_inception: Decimal
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("max_harvestable_loss_pct", "min_unrealized_gain_pct", "min_years_since_inception",
                     mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class AssetClassIn(_Body):
    global_security_id: UUID
    asset_class: str
    source: str = Field(min_length=1, max_length=500)
    as_of: date


class FixupIn(_Body):
    valuation_date: date
    sponsor_profile_id: UUID


class PlanIn(_Body):
    name: str = Field(min_length=1, max_length=300)
    target_etf: str = Field(min_length=1, max_length=300)
    sponsor_profile_id: UUID
    valuation_date: date
    seed_capital: Decimal = Decimal(0)
    seed_note: str | None = Field(default=None, max_length=2000)
    sponsor_trust: str | None = Field(default=None, max_length=300)
    etf_custodian_account: str | None = Field(default=None, max_length=300)

    @field_validator("seed_capital", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class PlanPatch(_Body):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    target_etf: str | None = Field(default=None, min_length=1, max_length=300)
    valuation_date: date | None = None
    seed_capital: Decimal | None = None
    seed_note: str | None = Field(default=None, max_length=2000)
    sponsor_trust: str | None = Field(default=None, max_length=300)
    etf_custodian_account: str | None = Field(default=None, max_length=300)

    @field_validator("seed_capital", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class AddAccountIn(_Body):
    account_id: UUID
    lot_ids: list[UUID] | None = Field(default=None, max_length=5000)


class Reason(_Body):
    reason: str = Field(min_length=1, max_length=2000)


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


class UsPersonIn(_Body):
    us_person: Literal["yes", "no"]
    source: str = Field(min_length=1, max_length=500)
    as_of: date


class SponsorReviewIn(_Body):
    decision: Literal["accepted", "declined"]
    note: str = Field(min_length=1, max_length=1000)
    reviewed_on: date


class ConsentIn(_Body):
    document_id: UUID
    signed_on: date
    signer: str = Field(min_length=1, max_length=300)


class TaxAdviserIn(_Body):
    adviser_name: str = Field(min_length=1, max_length=300)
    confirmed_on: date
    note: str | None = Field(default=None, max_length=2000)


class BootIn(_Body):
    amount: Decimal

    @field_validator("amount", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class AcknowledgeIn(_Body):
    version_id: UUID


class DeliveredIn(_Body):
    delivered_on: date
    note: str | None = Field(default=None, max_length=2000)


class StepPatch(_Body):
    owner_id: UUID | None = None
    due_on: date | None = None
    note: str | None = Field(default=None, max_length=2000)


class RouteIn(_Body):
    account_id: UUID
    route: Literal["adapter", "manual"]
    channel: str | None = Field(default=None, max_length=300)


class ConfirmIn(_Body):
    received_on: date
    etf_global_security_id: UUID
    etf_shares: Decimal

    @field_validator("etf_shares", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    from services import account_models as AM
    from services import disclosures as DL

    if isinstance(exc, (S.Section351NotFound, RP.ProposalNotFound, HT.HumanTaskNotFound, AM.AccountModelNotFound,
                        DL.DisclosureNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (S.MakerCheckerViolation, RP.MakerCheckerViolation, HT.MakerCheckerError, PermissionError,
                        HT.HumanTaskPermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, S.NotReady):
        raise HTTPException(status_code=409, detail={"message": str(exc), "gates": exc.gates}) from exc
    if isinstance(exc, S.InKindTransferNotDeclared):
        raise HTTPException(status_code=409, detail={"message": str(exc), "manual_route": True}) from exc
    if isinstance(exc, (S.Section351Conflict, RP.ProposalConflict, HT.HumanTaskConflict, AM.AccountModelConflict,
                        DL.DisclosureConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.CheckViolationError):
        raise HTTPException(status_code=422, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, (S.Section351Error, S.R.Section351RuleError, RP.ProposalError, HT.HumanTaskError,
                        AM.AccountModelError, DL.DisclosureError, ValueError)):
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
            "can_approve": sa or APPROVE in principal.permissions, "can_admin": sa or ADMIN in principal.permissions,
            "is_super_admin": sa, "read_permission": VIEW, "write_permission": MANAGE, "approve_permission": APPROVE,
            "admin_permission": ADMIN}


def _env(principal, body: dict, actions: list[str] | None = None) -> dict:
    perms = _permissions(principal)
    return {**body, "permissions": perms,
            "vocabularies": S.vocabularies(can_write=perms["can_write"] is True, can_admin=perms["can_admin"] is True),
            "actions": actions or []}


async def _plan_env(principal, org_id, plan_id) -> dict:
    perms = _permissions(principal)
    pool = await get_pool()
    can_write = perms["can_write"] is True
    async with pool.acquire() as conn:
        d = await S.detail(conn, org_id, plan_id)
        # what a writer may add (a draft only): open replica accounts not already in the plan — eligibility is
        # checked in full when one is added (the refusal names each reason)
        d["addable_accounts"] = await S.addable_accounts(conn, org_id, d["plan"]["id"]) \
            if can_write and d["plan"]["status"] == "draft" else []
    for st in d["timeline"]["steps"]:
        st["actions"] = ["done", "edit"] if can_write and st["status"] == "open" else []
    return _env(principal, d, S.plan_actions(d, can_write=can_write, can_approve=perms["can_approve"] is True,
                                             user_id=principal.user_id))


# ── Reads ───────────────────────────────────────────────────────────────────
@router.get("/section351/candidates")
async def s351_candidates(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            body = await S.candidates(conn, org_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    perms = _permissions(principal)
    return _env(principal, body, (["create_plan", "fixup"] if perms["can_write"] is True else [])
                + (["edit_thresholds"] if perms["can_admin"] is True else []))


@router.get("/section351/sponsor-profiles")
async def s351_profiles(request: Request, history: bool = False):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await S.list_profiles(conn, org_id, include_history=history)
    perms = _permissions(principal)
    return _env(principal, {"rows": rows, "defaults": S.DEFAULT_RULES},
                ["create_profile", "new_version"] if perms["can_admin"] is True else [])


@router.get("/section351/thresholds")
async def s351_thresholds(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        live = await S.thresholds(conn, org_id)
        hist = await S.threshold_history(conn, org_id)
    perms = _permissions(principal)
    return _env(principal, {"thresholds": live, "history": hist},
                ["save_thresholds"] if perms["can_admin"] is True else [])


@router.get("/section351/accounts/{account_id}/test")
async def s351_account_test(request: Request, account_id: str, valuation_date: date, profile_id: UUID,
                            lot_ids: str | None = None):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    lots = [x.strip() for x in lot_ids.split(",") if x.strip()] if lot_ids else None
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            test = await S.test_account(conn, org_id, account_id, valuation_date=valuation_date, profile_id=profile_id,
                                        lot_ids=lots)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    perms = _permissions(principal)
    return _env(principal, {"test": test},
                ["fixup"] if perms["can_write"] is True and test["verdict"] == "fail" else [])


@router.get("/section351/plans")
async def s351_plans(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await S.list_plans(conn, org_id)
    perms = _permissions(principal)
    return _env(principal, {"rows": rows}, ["create_plan"] if perms["can_write"] is True else [])


@router.get("/section351/plans/{plan_id}")
async def s351_plan(request: Request, plan_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    try:
        return await _plan_env(principal, org_id, plan_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.get("/section351/plans/{plan_id}/instruction-sheet")
async def s351_instruction_sheet(request: Request, plan_id: str):
    require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            data = await S.sheet(conn, org_id, plan_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="section351-transfer-{plan_id}.pdf"'})


@router.get("/section351/basis-records/{record_id}/file/{kind}")
async def s351_basis_file(request: Request, record_id: str, kind: str):
    require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            data, ctype, name = await S.basis_file(conn, org_id, record_id, kind)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return Response(content=data, media_type=ctype or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ── Writes: manage_proposals ───────────────────────────────────────────────
@router.post("/section351/accounts/{account_id}/fixup", status_code=201)
async def s351_fixup(request: Request, account_id: str, body: FixupIn):
    principal = require_permission(request, MANAGE)
    out = await _act(S.fixup(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                             account_id=account_id, valuation_date=body.valuation_date,
                             profile_id=body.sponsor_profile_id))
    return _env(principal, {"fixup": out})


@router.post("/section351/plans", status_code=201)
async def s351_create_plan(request: Request, body: PlanIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    d = await _act(S.create_plan(await get_pool(), org_id=org_id, actor_id=principal.user_id, name=body.name,
                                 target_etf=body.target_etf, sponsor_profile_id=body.sponsor_profile_id,
                                 valuation_date=body.valuation_date, seed_capital=body.seed_capital,
                                 seed_note=body.seed_note, sponsor_trust=body.sponsor_trust,
                                 etf_custodian_account=body.etf_custodian_account))
    return await _act(_plan_env(principal, org_id, d["plan"]["id"]))


@router.patch("/section351/plans/{plan_id}")
async def s351_update_plan(request: Request, plan_id: str, body: PlanPatch):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    if not fields:
        raise HTTPException(status_code=422, detail="change at least one field")
    await _act(S.update_plan(await get_pool(), org_id=org_id, actor_id=principal.user_id, plan_id=plan_id,
                             fields=fields))
    return await _act(_plan_env(principal, org_id, plan_id))


@router.post("/section351/plans/{plan_id}/accounts", status_code=201)
async def s351_add_account(request: Request, plan_id: str, body: AddAccountIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    a = await _act(S.add_account(await get_pool(), org_id=org_id, actor_id=principal.user_id, plan_id=plan_id,
                                 account_id=body.account_id, lot_ids=body.lot_ids))
    return _env(principal, {"account": a})


@router.post("/section351/plans/{plan_id}/retest")
async def s351_retest(request: Request, plan_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    await _act(S.retest(await get_pool(), org_id=org_id, actor_id=principal.user_id, plan_id=plan_id))
    return await _act(_plan_env(principal, org_id, plan_id))


@router.post("/section351/plans/{plan_id}/ready")
async def s351_ready(request: Request, plan_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    await _act(S.mark_ready(await get_pool(), org_id=org_id, actor_id=principal.user_id, plan_id=plan_id))
    return await _act(_plan_env(principal, org_id, plan_id))


@router.post("/section351/plans/{plan_id}/reopen")
async def s351_reopen(request: Request, plan_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    await _act(S.reopen(await get_pool(), org_id=org_id, actor_id=principal.user_id, plan_id=plan_id))
    return await _act(_plan_env(principal, org_id, plan_id))


@router.post("/section351/plans/{plan_id}/submit")
async def s351_submit(request: Request, plan_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    await _act(S.submit(await get_pool(), org_id=org_id, actor_id=principal.user_id, plan_id=plan_id))
    return await _act(_plan_env(principal, org_id, plan_id))


@router.post("/section351/plans/{plan_id}/cancel")
async def s351_cancel(request: Request, plan_id: str, body: Reason):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    await _act(S.cancel(await get_pool(), org_id=org_id, actor_id=principal.user_id, plan_id=plan_id,
                        reason=body.reason))
    return await _act(_plan_env(principal, org_id, plan_id))


@router.post("/section351/plans/{plan_id}/basis-record", status_code=201)
async def s351_export_basis(request: Request, plan_id: str):
    principal = require_permission(request, MANAGE)
    rec = await _act(S.export_basis_record(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                           plan_id=plan_id))
    return _env(principal, {"basis_record": rec})


@router.post("/section351/plans/{plan_id}/route")
async def s351_route(request: Request, plan_id: str, body: RouteIn):
    principal = require_permission(request, MANAGE)
    out = await _act(S.route(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                             plan_id=plan_id, account_id=body.account_id, route_kind=body.route, channel=body.channel))
    return _env(principal, out)


@router.post("/section351/plan-accounts/{plan_account_id}/remove")
async def s351_remove_account(request: Request, plan_account_id: str, body: Reason):
    principal = require_permission(request, MANAGE)
    a = await _act(S.remove_account(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                    plan_account_id=plan_account_id, reason=body.reason))
    return _env(principal, {"account": a})


@router.post("/section351/plan-accounts/{plan_account_id}/us-person")
async def s351_us_person(request: Request, plan_account_id: str, body: UsPersonIn):
    principal = require_permission(request, MANAGE)
    a = await _act(S.record_us_person(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                      plan_account_id=plan_account_id, us_person=body.us_person, source=body.source,
                                      as_of=body.as_of))
    return _env(principal, {"account": a})


@router.post("/section351/plan-accounts/{plan_account_id}/sponsor-review")
async def s351_sponsor_review(request: Request, plan_account_id: str, body: SponsorReviewIn):
    principal = require_permission(request, MANAGE)
    a = await _act(S.record_sponsor_review(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                           plan_account_id=plan_account_id, decision=body.decision, note=body.note,
                                           reviewed_on=body.reviewed_on))
    return _env(principal, {"account": a})


@router.post("/section351/plan-accounts/{plan_account_id}/consent")
async def s351_consent(request: Request, plan_account_id: str, body: ConsentIn):
    principal = require_permission(request, MANAGE)
    a = await _act(S.record_consent(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                    plan_account_id=plan_account_id, document_id=body.document_id,
                                    signed_on=body.signed_on, signer=body.signer))
    return _env(principal, {"account": a})


@router.post("/section351/plan-accounts/{plan_account_id}/tax-adviser")
async def s351_tax_adviser(request: Request, plan_account_id: str, body: TaxAdviserIn):
    principal = require_permission(request, MANAGE)
    a = await _act(S.record_tax_adviser(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                        plan_account_id=plan_account_id, adviser_name=body.adviser_name,
                                        confirmed_on=body.confirmed_on, note=body.note))
    return _env(principal, {"account": a})


@router.post("/section351/plan-accounts/{plan_account_id}/boot")
async def s351_boot(request: Request, plan_account_id: str, body: BootIn):
    principal = require_permission(request, MANAGE)
    a = await _act(S.set_cash_to_member(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                        plan_account_id=plan_account_id, amount=body.amount))
    return _env(principal, {"account": a})


@router.post("/section351/plan-accounts/{plan_account_id}/acknowledge")
async def s351_acknowledge(request: Request, plan_account_id: str, body: AcknowledgeIn):
    principal = require_permission(request, MANAGE)
    st = await _act(S.acknowledge_disclosure(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                             plan_account_id=plan_account_id, version_id=body.version_id))
    return _env(principal, {"disclosure": st})


@router.post("/section351/plan-accounts/{plan_account_id}/confirm")
async def s351_confirm(request: Request, plan_account_id: str, body: ConfirmIn):
    principal = require_permission(request, MANAGE)
    out = await _act(S.confirm_account(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                       plan_account_id=plan_account_id, received_on=body.received_on,
                                       etf_global_security_id=body.etf_global_security_id,
                                       etf_shares=body.etf_shares))
    return _env(principal, out)


@router.post("/section351/basis-records/{record_id}/delivered")
async def s351_delivered(request: Request, record_id: str, body: DeliveredIn):
    principal = require_permission(request, MANAGE)
    rec = await _act(S.record_delivery(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                       record_id=record_id, delivered_on=body.delivered_on, note=body.note))
    return _env(principal, {"basis_record": rec})


@router.patch("/section351/steps/{step_id}")
async def s351_update_step(request: Request, step_id: str, body: StepPatch):
    principal = require_permission(request, MANAGE)
    st = await _act(S.update_step(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                  step_id=step_id, owner_id=body.owner_id, due_on=body.due_on, note=body.note))
    return _env(principal, {"step": st})


@router.post("/section351/steps/{step_id}/done")
async def s351_step_done(request: Request, step_id: str, body: Decision):
    principal = require_permission(request, MANAGE)
    st = await _act(S.complete_step(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                    step_id=step_id, note=body.note))
    return _env(principal, {"step": st})


@router.post("/section351/asset-classes", status_code=201)
async def s351_add_class(request: Request, body: AssetClassIn):
    principal = require_permission(request, MANAGE)
    row = await _act(S.add_asset_class(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                       global_security_id=body.global_security_id, asset_class=body.asset_class,
                                       source=body.source, as_of=body.as_of))
    return _env(principal, {"asset_class": row})


@router.post("/section351/asset-classes/{class_id}/retire")
async def s351_retire_class(request: Request, class_id: str):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        scope = await conn.fetchval(f"SELECT owner_scope FROM {S.T_CLASS} WHERE id = $1::uuid AND system_to IS NULL "
                                    "AND valid_to IS NULL", str(class_id)) if _is_uuid(class_id) else None
    if scope is None:
        raise HTTPException(status_code=404, detail="classification not found")
    if scope == "platform" and not principal.is_super_admin:
        raise HTTPException(status_code=403, detail="Super Admin required: a platform classification")
    out = await _act(S.retire_asset_class(pool, org_id=org_id, actor_id=principal.user_id, class_id=class_id))
    return _env(principal, out)


def _is_uuid(v) -> bool:
    try:
        UUID(str(v))
        return True
    except (TypeError, ValueError):
        return False


# ── approve_proposals ─────────────────────────────────────────────────────
@router.post("/section351/plans/{plan_id}/approve")
async def s351_approve(request: Request, plan_id: str, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    await _act(S.decide(await get_pool(), org_id=org_id, actor_id=principal.user_id, plan_id=plan_id,
                        outcome="approve", note=body.note))
    return await _act(_plan_env(principal, org_id, plan_id))


@router.post("/section351/plans/{plan_id}/reject")
async def s351_reject(request: Request, plan_id: str, body: Reason):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    await _act(S.decide(await get_pool(), org_id=org_id, actor_id=principal.user_id, plan_id=plan_id,
                        outcome="reject", note=body.reason))
    return await _act(_plan_env(principal, org_id, plan_id))


# ── manage_org_settings (org admin) ───────────────────────────────────────
@router.post("/section351/sponsor-profiles", status_code=201)
async def s351_create_profile(request: Request, body: ProfileIn):
    principal = require_permission(request, ADMIN)
    row = await _act(S.save_profile(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                    data=body.data()))
    return _env(principal, {"profile": row})


@router.post("/section351/sponsor-profiles/{profile_key}/versions", status_code=201)
async def s351_profile_version(request: Request, profile_key: str, body: ProfileIn):
    principal = require_permission(request, ADMIN)
    row = await _act(S.save_profile(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                    data=body.data(), profile_key=profile_key))
    return _env(principal, {"profile": row})


@router.put("/section351/thresholds")
async def s351_save_thresholds(request: Request, body: ThresholdsIn):
    principal = require_permission(request, ADMIN)
    out = await _act(S.save_thresholds(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                       max_harvestable_loss_pct=body.max_harvestable_loss_pct,
                                       min_unrealized_gain_pct=body.min_unrealized_gain_pct,
                                       min_years_since_inception=body.min_years_since_inception, note=body.note))
    return _env(principal, {"thresholds": out})


# ── Super Admin ───────────────────────────────────────────────────────────
@router.post("/section351/asset-classes/platform", status_code=201)
async def s351_add_platform_class(request: Request, body: AssetClassIn):
    principal = require_super_admin(request)
    row = await _act(S.add_asset_class(await get_pool(), org_id=get_org_id(request), actor_id=principal.user_id,
                                       global_security_id=body.global_security_id, asset_class=body.asset_class,
                                       source=body.source, as_of=body.as_of, platform=True))
    return _env(principal, {"asset_class": row})
