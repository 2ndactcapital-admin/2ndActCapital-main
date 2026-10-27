"""REP7 — held-away accounts (/held-away/…). Read-only positions and lots for
accounts held elsewhere; nothing here trades.

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_held_away    GET  /held-away/accounts/{id}                 the account's held-away panel + envelope
                    GET  /held-away/accounts/{id}/positions       positions and lots (live, or ?as_of= a version)
                    GET  /held-away/imports/{id}                  one import: rows, rejects with reasons, status
                    GET  /held-away/providers                     the aggregator registry (all "not connected")
  manage_held_away  POST /held-away/accounts/{id}/sources         add a source (aggregator → not connected)
                    POST /held-away/sources/{id}                  change stale_after_days / notes, or retire
                    POST /held-away/accounts/{id}/imports/manual  keyed positions / lots / trades → a DRAFT
                    POST /held-away/accounts/{id}/imports/file    xlsx / xls / csv through rep3's mapping → a DRAFT
                    POST /held-away/accounts/{id}/statements      link a Chancery document to the account
                    POST /held-away/accounts/{id}/imports/statement  extract a linked statement → a DRAFT
                    POST /held-away/accounts/{id}/confirmations   ask for the stale-data confirmation task
                    POST /held-away/confirmations/{id}/confirm    confirm the data (completes the task)
  approve_held_away POST /held-away/imports/{id}/approve|reject   (never the person who entered it)

Super Admin first (services.authz). org_id is only ever the caller's
(get_org_id); every request model forbids extras; another org's row is a 404.
"""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import held_away as HA
from services import human_tasks as HT
from services.authz import require_permission
from services.database import get_pool

router = APIRouter(tags=["held_away"])

VIEW, MANAGE, APPROVE = HA.VIEW, HA.MANAGE, HA.APPROVE
MAX_FILE_BYTES = 5 * 1024 * 1024
EDITABLE = ("sources", "imports", "statements", "confirmations")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceIn(_Body):
    kind: Literal["manual", "file", "statement", "aggregator", "custodian_feed"]
    provider: str | None = Field(default=None, max_length=60)
    stale_after_days: int | None = Field(default=None, ge=1, le=3650)
    notes: str | None = Field(default=None, max_length=2000)


class SourceChange(_Body):
    stale_after_days: int | None = Field(default=None, ge=1, le=3650)
    notes: str | None = Field(default=None, max_length=2000)
    retire: bool = False


class RowIn(_Body):
    ticker: str | None = Field(default=None, max_length=20)
    cusip: str | None = Field(default=None, max_length=12)
    isin: str | None = Field(default=None, max_length=12)
    sedol: str | None = Field(default=None, max_length=7)
    name: str | None = Field(default=None, max_length=300)
    quantity: str | None = Field(default=None, max_length=40)
    market_value: str | None = Field(default=None, max_length=40)
    acquired_date: date | None = None
    cost: str | None = Field(default=None, max_length=40)
    term: Literal["short", "long"] | None = None
    side: Literal["buy", "sell"] | None = None
    trade_date: date | None = None
    price: str | None = Field(default=None, max_length=40)
    realized_gain_loss: str | None = Field(default=None, max_length=40)


class ManualIn(_Body):
    as_of: date
    rows: list[RowIn] = Field(min_length=1, max_length=2000)
    document_id: UUID | None = None
    notes: str | None = Field(default=None, max_length=2000)


class StatementLink(_Body):
    document_id: UUID


class StatementIn(_Body):
    document_id: UUID
    as_of: date
    notes: str | None = Field(default=None, max_length=2000)


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


class ConfirmRequest(_Body):
    proposal_id: UUID | None = None


def _raise(exc: Exception):
    from services import model_upload as U

    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (HA.HeldAwayNotFound, HT.HumanTaskNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (HA.MakerCheckerViolation, HT.MakerCheckerError, PermissionError, HT.HumanTaskPermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (HA.HeldAwayConflict, HT.HumanTaskConflict)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.CheckViolationError):
        raise HTTPException(status_code=422, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, asyncpg.InsufficientPrivilegeError):
        raise HTTPException(status_code=409, detail=str(exc).split("\n")[0]) from exc
    if isinstance(exc, U.UploadRefused):
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.text}) from exc
    if isinstance(exc, (HA.HeldAwayError, HT.HumanTaskError, ValueError)):
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


def _env(principal, **body) -> dict:
    perms = _permissions(principal)
    v = HA.vocabularies()
    v["editable"] = list(EDITABLE) if perms["can_write"] else []
    return {**body, "permissions": perms, "vocabularies": v}


# ── Reads ───────────────────────────────────────────────────────────────────
@router.get("/held-away/accounts/{account_id}")
async def account_panel(request: Request, account_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            panel = await HA.account_panel(conn, org_id, account_id)
            imports = [HA.import_out(r) for r in await HA.list_imports(conn, org_id, account_id)]
            raw = {str(r["id"]): r for r in await HA.list_imports(conn, org_id, account_id)}
            positions = await HA.positions_of(conn, org_id, account_id)
            txns = await HA.transactions_of(conn, org_id, account_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    for i in imports:
        i["actions"] = HA.import_actions(raw[i["id"]], can_approve=perms["can_approve"], user_id=principal.user_id)
    held = panel["account"]["is_held_away"]
    actions = []
    if perms["can_write"] and held:
        actions = ["add_source", "manual_import", "file_import", "link_statement", "request_confirmation"]
        if panel["statement_extraction"]["available"]:
            actions.append("statement_import")
    # a held-away account never shows a trade control: the envelope says so and lists none
    return {**_env(principal, panel=panel, imports=imports, positions=positions, transactions=txns),
            "actions": actions, "trade_actions": []}


@router.get("/held-away/accounts/{account_id}/positions")
async def account_positions(request: Request, account_id: UUID, as_of: date | None = None):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            await HA.load_account(conn, org_id, account_id)
            out = await HA.positions_of(conn, org_id, account_id, as_of=as_of)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _env(principal, **out, as_of=as_of.isoformat() if as_of else None)


@router.get("/held-away/imports/{import_id}")
async def get_import(request: Request, import_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    perms = _permissions(principal)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await HA.get_import(conn, org_id, import_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    out = HA.import_out(row)
    out["actions"] = HA.import_actions(row, can_approve=perms["can_approve"], user_id=principal.user_id)
    return _env(principal, row=out)


@router.get("/held-away/providers")
async def providers(request: Request):
    principal = require_permission(request, VIEW)
    from services import held_away_adapters as AD

    return _env(principal, rows=AD.providers())


# ── Writes ──────────────────────────────────────────────────────────────────
@router.post("/held-away/accounts/{account_id}/sources", status_code=201)
async def add_source(request: Request, account_id: UUID, body: SourceIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    row = await _act(HA.add_source(pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                                   kind=body.kind, provider=body.provider, stale_after_days=body.stale_after_days,
                                   notes=body.notes))
    async with pool.acquire() as conn:
        days = await HA._setting_days(conn, org_id)
    return HA.source_out(row, today=date.today(), default_days=days)


@router.post("/held-away/sources/{source_id}")
async def change_source(request: Request, source_id: UUID, body: SourceChange):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    changes = {k: getattr(body, k) for k in body.model_fields_set if k in ("stale_after_days", "notes")}
    if body.retire:
        changes["status"] = "retired"
    if not changes:
        raise HTTPException(status_code=422, detail="nothing to change")
    pool = await get_pool()
    row = await _act(HA.update_source(pool, org_id=org_id, actor_id=principal.user_id, source_id=source_id,
                                      changes=changes))
    async with pool.acquire() as conn:
        days = await HA._setting_days(conn, org_id)
    return HA.source_out(row, today=date.today(), default_days=days)


@router.post("/held-away/accounts/{account_id}/imports/manual", status_code=201)
async def manual_import(request: Request, account_id: UUID, body: ManualIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    rows = [r.model_dump(exclude_none=True) for r in body.rows]
    out = await _act(HA.create_manual_import(pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                                             as_of=body.as_of, rows=rows, document_id=body.document_id,
                                             notes=body.notes))
    return HA.import_out(out)


@router.post("/held-away/accounts/{account_id}/imports/file", status_code=201)
async def file_import(request: Request, account_id: UUID, file: UploadFile = File(...),
                      as_of: date = Form(...), mapping: str | None = Form(default=None, max_length=20000),
                      sheet: str | None = Form(default=None, max_length=200),
                      header_row: int | None = Form(default=None, ge=1, le=30),
                      notes: str | None = Form(default=None, max_length=2000)):
    import json

    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    data = await file.read(MAX_FILE_BYTES + 1)
    try:
        mp = json.loads(mapping) if mapping else None
    except ValueError:
        raise HTTPException(status_code=422, detail="mapping is a JSON object {header: field}") from None
    if mp is not None and not (isinstance(mp, dict) and all(isinstance(k, str) and isinstance(v, str)
                                                            for k, v in mp.items())):
        raise HTTPException(status_code=422, detail="mapping is a JSON object {header: field}")
    pool = await get_pool()
    out = await _act(HA.create_file_import(pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                                           as_of=as_of, data=data, filename=file.filename or "upload", mapping=mp,
                                           sheet=sheet, header_row=header_row, notes=notes))
    return HA.import_out(out)


@router.post("/held-away/accounts/{account_id}/statements", status_code=201)
async def link_statement(request: Request, account_id: UUID, body: StatementLink):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    return await _act(HA.link_statement(pool, org_id=org_id, actor_id=principal.user_id, account_id=account_id,
                                        document_id=body.document_id))


@router.post("/held-away/accounts/{account_id}/imports/statement", status_code=201)
async def statement_import(request: Request, account_id: UUID, body: StatementIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    out = await _act(HA.create_statement_import(pool, org_id=org_id, actor_id=principal.user_id,
                                                account_id=account_id, document_id=body.document_id,
                                                as_of=body.as_of, notes=body.notes))
    return HA.import_out(out)


@router.post("/held-away/imports/{import_id}/approve")
async def approve_import(request: Request, import_id: UUID, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    out = await _act(HA.decide_import(pool, org_id=org_id, actor_id=principal.user_id, import_id=import_id,
                                      outcome="approve", note=body.note))
    return HA.import_out(out)


@router.post("/held-away/imports/{import_id}/reject")
async def reject_import(request: Request, import_id: UUID, body: Decision):
    principal = require_permission(request, APPROVE)
    org_id = get_org_id(request)
    pool = await get_pool()
    out = await _act(HA.decide_import(pool, org_id=org_id, actor_id=principal.user_id, import_id=import_id,
                                      outcome="reject", note=body.note))
    return HA.import_out(out)


@router.post("/held-away/accounts/{account_id}/confirmations", status_code=201)
async def request_confirmation(request: Request, account_id: UUID, body: ConfirmRequest):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    if body.proposal_id:
        from services import rebalance_proposals as RP

        async with pool.acquire() as conn:
            try:
                await RP.load(conn, org_id, body.proposal_id)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=404, detail="proposal not found") from exc
    out = await _act(HA.request_confirmation(pool, org_id=org_id, account_id=account_id, proposal_id=body.proposal_id,
                                             actor_id=principal.user_id,
                                             purpose="proposal" if body.proposal_id else "general"))
    return HA.confirmation_out(out)


@router.post("/held-away/confirmations/{confirmation_id}/confirm")
async def confirm(request: Request, confirmation_id: UUID, body: Decision):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    out = await _act(HA.confirm(pool, org_id=org_id, actor_id=principal.user_id, confirmation_id=confirmation_id,
                                note=body.note))
    return HA.confirmation_out(out)
