"""REP6 — the tax engine (/tax/…, /harvest/…).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_tax     GET  /tax/overview                         harvest dashboard across households + envelope
               GET  /tax/subjects                         households / members with their accounts
               GET  /tax/securities                       security search for the forms
               GET  /tax/profiles                         profiles, every version, with actions
               GET  /tax/carryforwards                    carryforwards, every version, with actions
               GET  /tax/substitutions                    sets (every version) and denials, with actions
               GET  /tax/substitutions/suggestions        the model's alternates — a starting point only
               GET  /tax/declared-trades                  declared outside trades
               GET  /tax/loss-bank                        one subject's ledger and netting for a year
               GET  /tax/accounts/{id}                    the account page's Tax panel
               GET  /harvest/runs                         harvest runs (an account's, or all)
               GET  /harvest/runs/{id}                    one run with every candidate and its reason
  manage_tax   POST /tax/profiles                         enter a profile version (opens the approval task)
               POST /tax/carryforwards                    enter a carryforward version (opens the task)
               POST /tax/substitutions/sets               propose a set version (opens the task)
               POST /tax/substitutions/denials            propose a denial (opens the task)
               POST /tax/substitutions/denials/seed       seed the rule denials (same index, share class)
               POST /tax/declared-trades                  declare an outside trade (or correct one)
               POST /tax/declared-trades/{id}/withdraw    withdraw one (a reason)
               POST /tax/loss-bank/refresh                ingest the sources now (idempotent)
               POST /harvest/accounts/{id}/scan           run today's harvest scan for an account now
  approve_tax  POST /tax/profiles/{id}/approve|reject                 (never the maker)
               POST /tax/carryforwards/{id}/approve|reject            (never the maker)
               POST /tax/substitutions/sets/{id}/approve|reject|retire    (never the maker)
               POST /tax/substitutions/denials/{id}/approve|reject|retire (never the maker)
A harvest PROPOSAL is decided on rep5's routes (approve_proposals).

Super Admin first (services.authz). org_id is only ever the caller's
(get_org_id) — never a body or path value; every request model forbids extras.
Another org's row is a 404 (RLS + an explicit org predicate). Amounts and rates
travel as decimal STRINGS; a JSON float is a 422. Every tax number in a
response carries the label "estimate" with its rates and as-of, or says why it
is not estimated.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from routers.entities import get_org_id
from services import harvest as HV
from services import human_tasks as HT
from services import loss_bank as LB
from services import rebalance_proposals as RP
from services import substitutions as SB
from services import tax_overview as TO
from services import tax_profiles as TP
from services import wash_sale as WS
from services.authz import require_permission
from services.custody.trading import _decimal
from services.database import get_pool

router = APIRouter(tags=["tax"])

VIEW, MANAGE, APPROVE = TP.VIEW, TP.MANAGE, TP.APPROVE
PROFILE_FIELDS = ("subject_type", "subject_id", "tax_year", "filing_status", "federal_ordinary_rate",
                  "federal_lt_rate", "niit_applies", "state_code", "state_rate", "state_lt_rate", "city", "city_rate",
                  "city_lt_rate", "as_of", "source_note", "notes")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


SubjectType = Literal["household", "entity"]


class ProfileIn(_Body):
    subject_type: SubjectType
    subject_id: UUID
    tax_year: int = Field(ge=2000, le=2100)
    filing_status: str = Field(max_length=40)
    federal_ordinary_rate: Decimal
    federal_lt_rate: Decimal
    niit_applies: bool
    state_code: str | None = Field(default=None, max_length=10)
    state_rate: Decimal | None = None
    state_lt_rate: Decimal | None = None
    city: str | None = Field(default=None, max_length=100)
    city_rate: Decimal | None = None
    city_lt_rate: Decimal | None = None
    as_of: date
    source_note: str = Field(min_length=1, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("federal_ordinary_rate", "federal_lt_rate", "state_rate", "state_lt_rate", "city_rate",
                     "city_lt_rate", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class CarryforwardIn(_Body):
    subject_type: SubjectType
    subject_id: UUID
    tax_year: int = Field(ge=2000, le=2100)
    st_amount: Decimal
    lt_amount: Decimal
    source_document: str = Field(min_length=1, max_length=1000)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("st_amount", "lt_amount", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class CandidateIn(_Body):
    replacement_security_id: UUID
    reason: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(min_length=1, max_length=2000)
    source: Literal["adviser", "model_alternate"] = "adviser"


class SetIn(_Body):
    global_security_id: UUID
    candidates: list[CandidateIn] = Field(min_length=1, max_length=20)
    notes: str | None = Field(default=None, max_length=2000)


class DenialIn(_Body):
    security_a_id: UUID
    security_b_id: UUID
    reason: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(min_length=1, max_length=2000)


class DeclaredIn(_Body):
    subject_type: SubjectType
    subject_id: UUID
    account_description: str = Field(min_length=1, max_length=500)
    account_tax_class: Literal["taxable", "tax_advantaged", "unknown"] = "unknown"
    global_security_id: UUID
    side: Literal["buy", "sell"]
    trade_date: date
    quantity: Decimal | None = None
    loss: Literal["yes", "no", "unknown"] | None = None
    realized_amount: Decimal | None = None
    term: Literal["short", "long"] | None = None
    source_note: str = Field(min_length=1, max_length=2000)
    replaces_id: UUID | None = None

    @field_validator("quantity", "realized_amount", mode="before")
    @classmethod
    def _dec(cls, v, info):
        return _decimal(v, info.field_name)


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


class Reason(_Body):
    reason: str = Field(min_length=1, max_length=2000)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (TP.TaxNotFound, HT.HumanTaskNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (TP.MakerCheckerViolation, HT.MakerCheckerError, PermissionError, HT.HumanTaskPermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (TP.TaxConflict, HT.HumanTaskConflict, RP.ProposalConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.CheckViolationError):
        raise HTTPException(status_code=422, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        raise HTTPException(status_code=409, detail=str(exc).split("\n")[0]) from exc
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (TP.TaxError, HT.HumanTaskError, RP.ProposalError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


async def _act(coro):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


def _permissions(principal) -> dict:
    return {
        "can_read": principal.is_super_admin or VIEW in principal.permissions,
        "can_write": principal.is_super_admin or MANAGE in principal.permissions,
        "can_approve": principal.is_super_admin or APPROVE in principal.permissions,
        "is_super_admin": principal.is_super_admin,
        "read_permission": VIEW, "write_permission": MANAGE, "approve_permission": APPROVE,
    }


def _vocab(perms: dict) -> dict:
    from services.account_models import HARVESTING_ENGINE_LABELS

    v = {**TP.vocabularies(), **SB.vocabularies(), **WS.vocabularies(), **HV.vocabularies(), **LB.vocabularies(),
         "harvesting_engines": [{"key": k, "label": x} for k, x in HARVESTING_ENGINE_LABELS.items()]}
    v["editable"] = list(PROFILE_FIELDS) if perms["can_write"] else []
    return v


def _env(principal, **body) -> dict:
    perms = _permissions(principal)
    return {**body, "permissions": perms, "vocabularies": _vocab(perms)}


# ── Reads ───────────────────────────────────────────────────────────────────
@router.get("/tax/overview")
async def overview(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            d = await TO.dashboard(conn, org_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    perms = _permissions(principal)
    actions = ["refresh_loss_bank", "scan_account"] if perms["can_write"] else []
    return {**_env(principal, dashboard=d), "actions": actions}


@router.get("/tax/subjects")
async def subjects(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await TP.subjects(conn, org_id)
    return _env(principal, rows=rows)


@router.get("/tax/securities")
async def securities(request: Request, q: str = Query(default="", max_length=100)):
    principal = require_permission(request, VIEW)
    from services import execution as X

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await X.search_securities(conn, q)
    return _env(principal, rows=rows)


@router.get("/tax/profiles")
async def profiles(request: Request, subject_type: SubjectType | None = None, subject_id: UUID | None = None,
                   tax_year: int | None = Query(default=None, ge=2000, le=2100)):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await TP.list_profiles(conn, org_id, subject_type=subject_type, subject_id=subject_id,
                                          tax_year=tax_year)
            cfs = await TP.list_carryforwards(conn, org_id, subject_type=subject_type, subject_id=subject_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    for r in rows + cfs:
        r["actions"] = TP.actions_for(r, can_approve=perms["can_approve"], user_id=principal.user_id)
    actions = ["create_profile", "create_carryforward"] if perms["can_write"] else []
    return {**_env(principal, rows=rows, carryforwards=cfs), "actions": actions}


@router.get("/tax/carryforwards")
async def carryforwards(request: Request, subject_type: SubjectType | None = None, subject_id: UUID | None = None):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await TP.list_carryforwards(conn, org_id, subject_type=subject_type, subject_id=subject_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    for r in rows:
        r["actions"] = TP.actions_for(r, can_approve=perms["can_approve"], user_id=principal.user_id)
    return {**_env(principal, rows=rows), "actions": ["create_carryforward"] if perms["can_write"] else []}


@router.get("/tax/substitutions")
async def substitutions(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        sets = await SB.list_sets(conn, org_id)
        denials = await SB.list_denials(conn, org_id)
    for r in sets:
        r["actions"] = SB.set_actions(r, can_approve=perms["can_approve"], user_id=principal.user_id)
    for r in denials:
        r["actions"] = SB.denial_actions(r, can_approve=perms["can_approve"], user_id=principal.user_id)
    actions = ["propose_set", "propose_denial", "seed_denials"] if perms["can_write"] else []
    return {**_env(principal, sets=sets, denials=denials), "actions": actions}


@router.get("/tax/substitutions/suggestions")
async def suggestions(request: Request, security_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await SB.suggestions(conn, org_id, security_id)
    return _env(principal, rows=rows)


@router.get("/tax/declared-trades")
async def declared(request: Request, include_closed: bool = False):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await TP.list_declared(conn, org_id, include_closed=include_closed)
    for r in rows:
        r["actions"] = ["withdraw"] if perms["can_write"] and r["live"] else []
    return {**_env(principal, rows=rows), "actions": ["declare"] if perms["can_write"] else []}


@router.get("/tax/loss-bank")
async def loss_bank(request: Request, subject_type: SubjectType, subject_id: UUID,
                    tax_year: int = Query(ge=2000, le=2100)):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await TP.check_subject(conn, org_id, subject_type, subject_id)
            s = await LB.summary(conn, org_id, subject_type, subject_id, tax_year)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _env(principal, ledger=s)


@router.get("/tax/accounts/{account_id}")
async def account_tax(request: Request, account_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            panel = await TO.account_panel(conn, org_id, account_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    perms = _permissions(principal)
    return {**_env(principal, panel=panel), "actions": ["scan_account"] if perms["can_write"] else []}


@router.get("/harvest/runs")
async def harvest_runs(request: Request, account_id: UUID | None = None):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await HV.runs_for(conn, org_id, account_id=account_id)
    return _env(principal, rows=rows)


@router.get("/harvest/runs/{run_id}")
async def harvest_run(request: Request, run_id: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(f"SELECT * FROM {HV.T_RUN} WHERE id = $1::uuid AND org_id = $2::uuid "
                                      "AND system_to IS NULL", TP._uuid(run_id, "harvest run"), str(org_id))
            if row is None:
                raise TP.TaxNotFound("harvest run not found")
            cands = await HV.candidates_of(conn, org_id, row["id"])
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _env(principal, run=HV.run_out(dict(row)), candidates=cands)


# ── Writes (manage_tax) ─────────────────────────────────────────────────────
def _row_out(fn, row: dict) -> dict:
    out = fn(row)
    if row.get("task"):
        out["task"] = {"id": row["task"]["id"], "status": row["task"]["status"]}
    return out


@router.post("/tax/profiles", status_code=201)
async def create_profile(request: Request, body: ProfileIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    data = body.model_dump(exclude={"subject_type", "subject_id", "tax_year"})
    row = await _act(TP.propose_profile(pool, org_id=org_id, actor_id=principal.user_id, subject_type=body.subject_type,
                                        subject_id=body.subject_id, tax_year=body.tax_year, data=data))
    return {"profile": _row_out(TP.profile_out, row)}


@router.post("/tax/carryforwards", status_code=201)
async def create_carryforward(request: Request, body: CarryforwardIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(TP.propose_carryforward(pool, org_id=org_id, actor_id=principal.user_id,
                                             subject_type=body.subject_type, subject_id=body.subject_id,
                                             tax_year=body.tax_year, st_amount=body.st_amount,
                                             lt_amount=body.lt_amount, source_document=body.source_document,
                                             notes=body.notes))
    return {"carryforward": _row_out(TP.carryforward_out, row)}


@router.post("/tax/substitutions/sets", status_code=201)
async def create_set(request: Request, body: SetIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(SB.propose_set(pool, org_id=org_id, actor_id=principal.user_id, security_id=body.global_security_id,
                                    candidates=[c.model_dump(mode="json") for c in body.candidates], notes=body.notes))
    return {"set": {"id": str(row["id"]), "status": row["status"], "version_no": row["version_no"],
                    "task": {"id": row["task"]["id"], "status": row["task"]["status"]}}}


@router.post("/tax/substitutions/denials", status_code=201)
async def create_denial(request: Request, body: DenialIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(SB.propose_denial(pool, org_id=org_id, actor_id=principal.user_id,
                                       security_a_id=body.security_a_id, security_b_id=body.security_b_id,
                                       reason=body.reason, evidence=body.evidence))
    return {"denial": {"id": str(row["id"]), "status": row["status"],
                       "task": {"id": row["task"]["id"], "status": row["task"]["status"]}}}


@router.post("/tax/substitutions/denials/seed")
async def seed_denials(request: Request):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    made = await _act(SB.seed_rule_denials(pool, org_id=org_id, actor_id=principal.user_id))
    return {"seeded": made}


@router.post("/tax/declared-trades", status_code=201)
async def declare(request: Request, body: DeclaredIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    data = body.model_dump(exclude={"subject_type", "subject_id"})
    row = await _act(TP.declare_trade(pool, org_id=org_id, actor_id=principal.user_id, subject_type=body.subject_type,
                                      subject_id=body.subject_id, data=data))
    return {"declared_trade": TP.declared_out(row)}


@router.post("/tax/declared-trades/{trade_id}/withdraw")
async def withdraw(request: Request, trade_id: str, body: Reason):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(TP.withdraw_trade(pool, org_id=org_id, actor_id=principal.user_id, trade_id=trade_id,
                                       reason=body.reason))
    return {"declared_trade": TP.declared_out(row)}


@router.post("/tax/loss-bank/refresh")
async def refresh_loss_bank(request: Request):
    require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return {"ingest": await _act(LB.ingest(pool, org_id=org_id))}


@router.post("/harvest/accounts/{account_id}/scan")
async def scan(request: Request, account_id: str):
    require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return {"scan": await _act(HV.scan_account(pool, org_id=org_id, account_id=account_id))}


# ── Decisions (approve_tax; never the maker) ────────────────────────────────
async def _decide(request: Request, module, kind: str, row_id: str, outcome: str, note: str | None):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(module.decide(pool, org_id=org_id, actor_id=principal.user_id, kind=kind, row_id=row_id,
                                   outcome=outcome, note=note))
    return {"id": str(row["id"]), "status": row["status"]}


@router.post("/tax/profiles/{row_id}/approve")
async def approve_profile(request: Request, row_id: str, body: Decision):
    return await _decide(request, TP, "profile", row_id, "approve", body.note)


@router.post("/tax/profiles/{row_id}/reject")
async def reject_profile(request: Request, row_id: str, body: Decision):
    return await _decide(request, TP, "profile", row_id, "reject", body.note)


@router.post("/tax/carryforwards/{row_id}/approve")
async def approve_carryforward(request: Request, row_id: str, body: Decision):
    return await _decide(request, TP, "carryforward", row_id, "approve", body.note)


@router.post("/tax/carryforwards/{row_id}/reject")
async def reject_carryforward(request: Request, row_id: str, body: Decision):
    return await _decide(request, TP, "carryforward", row_id, "reject", body.note)


@router.post("/tax/substitutions/sets/{row_id}/approve")
async def approve_set(request: Request, row_id: str, body: Decision):
    return await _decide(request, SB, "set", row_id, "approve", body.note)


@router.post("/tax/substitutions/sets/{row_id}/reject")
async def reject_set(request: Request, row_id: str, body: Decision):
    return await _decide(request, SB, "set", row_id, "reject", body.note)


@router.post("/tax/substitutions/sets/{row_id}/retire")
async def retire_set(request: Request, row_id: str, body: Reason):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(SB.retire_set(pool, org_id=org_id, actor_id=principal.user_id, set_id=row_id, reason=body.reason))
    return {"id": str(row["id"]), "status": row["status"]}


@router.post("/tax/substitutions/denials/{row_id}/approve")
async def approve_denial(request: Request, row_id: str, body: Decision):
    return await _decide(request, SB, "denial", row_id, "approve", body.note)


@router.post("/tax/substitutions/denials/{row_id}/reject")
async def reject_denial(request: Request, row_id: str, body: Decision):
    return await _decide(request, SB, "denial", row_id, "reject", body.note)


@router.post("/tax/substitutions/denials/{row_id}/retire")
async def retire_denial(request: Request, row_id: str, body: Reason):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(SB.retire_denial(pool, org_id=org_id, actor_id=principal.user_id, denial_id=row_id,
                                      reason=body.reason))
    return {"id": str(row["id"]), "status": row["status"]}
