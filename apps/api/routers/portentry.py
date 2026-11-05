"""PORTENTRY1 — direct assets, private investments, net-worth recalculation, manual-asset relinks and the
integrated-account one-time import.

Routes (all under /api/v1):
  GET   /entities/{entity_id}/direct-assets            staff any record; a member only their own family's (404 else)
  POST  /entities/{entity_id}/direct-assets            internal + manage_members (org admin admitted)
  POST  /direct-assets/{asset_id}/valuations           internal + manage_members — a NEW valuation row
  GET   /entities/{entity_id}/private-investments      staff any; a member their own family's
  POST  /entities/{entity_id}/private-investments      internal + manage_members
  POST  /private-investments/{commitment_id}/flows     internal + manage_members — a capital call / distribution
  POST  /private-investments/{commitment_id}/nav       internal + manage_members — a new NAV valuation
  GET   /households/{household_id}/assets              internal — every member's direct assets + private investments
  POST  /entities/{entity_id}/net-worth/recalculate    internal + manage_members | manage_portfolio (family-scoped)
  GET   /portfolio/manual-assets                       view_held_away — org manual assets + the relink report
  POST  /portfolio/manual-assets/relink                manage_held_away — relink any whose identifier now resolves
  GET   /admin/integrated-imports                      Super Admin
  POST  /admin/integrated-imports                      Super Admin — upload a CSV → a DRAFT with its preview
  GET   /admin/integrated-imports/{import_id}          Super Admin
  POST  /admin/integrated-imports/{import_id}/approve  Super Admin, never the uploader (maker-checker)
  POST  /admin/integrated-imports/{import_id}/reject   Super Admin, never the uploader

org_id is only ever the caller's (get_org_id); every request model forbids extras; another org's row is a 404.
Every read publishes the permission envelope (rows / permissions / vocabularies with EMPTY editable lists for a
view-only caller).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from routers.crm_detail import _member_scoped_entity
from routers.entities import get_org_id
from services import direct_holdings as DH
from services import human_tasks as HT
from services import integrated_import as II
from services import portfolio_manual as PM
from services import portfolio_refresh as PR
from services.authz import (
    principal_of,
    require_internal,
    require_internal_permission,
    require_permission,
    require_super_admin,
)
from services.database import get_pool

router = APIRouter(tags=["portentry"])
WRITE = "manage_members"
RECALC = ("manage_members", "manage_portfolio")
MAX_FILE_BYTES = 5 * 1024 * 1024


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Valuation(_Body):
    value: str
    date: date
    method: Literal["appraisal", "estimate", "statement"]
    source: str | None = Field(default=None, max_length=300)


class Owner(_Body):
    entity_id: UUID
    ownership_pct: str
    cost_basis: str | None = None


class DirectAssetIn(_Body):
    kind: str
    name: str = Field(max_length=300)
    taxonomy_key: str | None = Field(default=None, max_length=80)
    sub_class: str | None = Field(default=None, max_length=120)
    ownership_pct: str | None = None
    owners: list[Owner] | None = None
    cost_basis: str | None = None
    valuation: Valuation
    notes: str | None = Field(default=None, max_length=4000)


class Commitment(_Body):
    amount: str
    date: date
    vintage_year: int | None = None


class Nav(_Body):
    value: str
    date: date
    source: str | None = Field(default=None, max_length=300)


class PrivateIn(_Body):
    kind: str
    name: str = Field(max_length=300)
    taxonomy_key: str | None = Field(default=None, max_length=80)
    commitment: Commitment
    called_to_date: str | None = None
    distributed_to_date: str | None = None
    nav: Nav | None = None
    as_of: date | None = None


class FlowIn(_Body):
    type: Literal["call", "distribution"]
    amount: str
    date: date


class Decision(_Body):
    note: str | None = Field(default=None, max_length=2000)


def _can(request: Request, perms) -> bool:
    p = principal_of(request)
    return bool(p is not None and p.admitted and (p.is_super_admin or p.is_staff
                                                  or (p.is_internal and any(x in p.permissions for x in perms))))


def _env(request: Request, rows, vocab: dict, editable: list[str], *, perms=(WRITE,), read="internal or own family"):
    can = _can(request, perms)
    p = principal_of(request)
    return {"rows": rows, "permissions": {"can_read": True, "can_write": can,
                                          "is_super_admin": bool(p and p.is_super_admin), "read_permission": read,
                                          "write_permission": " or ".join(perms)},
            "vocabularies": {**vocab, "editable": list(editable) if can else [], "inline_editable": []}}


def _raise(exc: Exception):
    if isinstance(exc, (DH.HoldingNotFound, II.ImportNotFound, HT.HumanTaskNotFound)):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (PermissionError, HT.MakerCheckerError, HT.HumanTaskPermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, HT.HumanTaskConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (DH.HoldingError, II.ImportError_, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


# ── Direct assets ───────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/entities/{entity_id}/direct-assets")
async def list_direct_assets(request: Request, entity_id: UUID):
    # BUGFIX2 — authz: member-scoped (staff any record; a member only their own family's, else 404)
    pool = await get_pool()
    await _member_scoped_entity(request, pool, entity_id)
    org_id = get_org_id(request)
    async with pool.acquire() as conn:
        rows = await DH.list_direct_assets(conn, org_id, [entity_id])
    return _env(request, rows, DH.vocab(), ["add", "revalue"])


@router.post("/entities/{entity_id}/direct-assets", status_code=201)
async def add_direct_asset(request: Request, entity_id: UUID, body: DirectAssetIn):
    p = require_internal_permission(request, "manage_members")
    data = body.model_dump(mode="json", exclude_none=True)
    try:
        return await DH.add_direct_asset(await get_pool(), org_id=get_org_id(request), actor_id=p.user_id,
                                         entity_id=entity_id, data=data)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.post("/direct-assets/{asset_id}/valuations", status_code=201)
async def add_valuation(request: Request, asset_id: UUID, body: Valuation):
    p = require_internal_permission(request, "manage_members")
    try:
        return await DH.add_valuation(await get_pool(), org_id=get_org_id(request), actor_id=p.user_id,
                                      asset_id=asset_id, data=body.model_dump(mode="json"))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


# ── Private investments ─────────────────────────────────────────────────────────────────────────────────────────
@router.get("/entities/{entity_id}/private-investments")
async def list_private(request: Request, entity_id: UUID):
    # BUGFIX2 — authz: member-scoped (staff any record; a member only their own family's, else 404)
    pool = await get_pool()
    await _member_scoped_entity(request, pool, entity_id)
    org_id = get_org_id(request)
    async with pool.acquire() as conn:
        rows = await DH.list_private_investments(conn, org_id, [entity_id])
    return _env(request, rows, DH.vocab(), ["add", "flows", "nav"])


@router.post("/entities/{entity_id}/private-investments", status_code=201)
async def add_private(request: Request, entity_id: UUID, body: PrivateIn):
    p = require_internal_permission(request, "manage_members")
    try:
        return await DH.add_private_investment(await get_pool(), org_id=get_org_id(request), actor_id=p.user_id,
                                               entity_id=entity_id, data=body.model_dump(mode="json",
                                                                                         exclude_none=True))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.post("/private-investments/{commitment_id}/flows", status_code=201)
async def add_flow(request: Request, commitment_id: UUID, body: FlowIn):
    p = require_internal_permission(request, "manage_members")
    try:
        return await DH.add_flow(await get_pool(), org_id=get_org_id(request), actor_id=p.user_id,
                                 commitment_id=commitment_id, data=body.model_dump(mode="json"))
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.post("/private-investments/{commitment_id}/nav", status_code=201)
async def add_nav(request: Request, commitment_id: UUID, body: Nav):
    p = require_internal_permission(request, "manage_members")
    org_id = get_org_id(request)
    pool = await get_pool()
    from services.portfolio_commitments import get_commitment

    async with pool.acquire() as conn:
        c = await get_commitment(conn, org_id=str(org_id), commitment_id=str(commitment_id))
        if c is None:
            raise HTTPException(status_code=404, detail="Commitment not found")
        asset_id = await conn.fetchval("SELECT asset_id FROM portfolio.positions WHERE id = $1::uuid",
                                       str(c["position_id"]))
    try:
        return await DH.add_valuation(pool, org_id=org_id, actor_id=p.user_id, asset_id=asset_id,
                                      data={**body.model_dump(mode="json"), "method": "statement"}, nav=True)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.get("/households/{household_id}/assets")
async def household_assets(request: Request, household_id: UUID):
    require_internal(request)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        members = [r["entity_id"] for r in await conn.fetch(
            "SELECT m.entity_id FROM household_memberships m JOIN households h ON h.id = m.household_id "
            "WHERE m.household_id = $1 AND h.org_id = $2::uuid", household_id, str(org_id))]
        if not members:
            exists = await conn.fetchval("SELECT 1 FROM households WHERE id = $1 AND org_id = $2::uuid",
                                         household_id, str(org_id))
            if not exists:
                raise HTTPException(status_code=404, detail="Household not found")
        rows = {"direct_assets": await DH.list_direct_assets(conn, org_id, members),
                "private_investments": await DH.list_private_investments(conn, org_id, members)}
    return _env(request, rows, DH.vocab(), ["add"], read="internal")


# ── Net worth: Recalculate ──────────────────────────────────────────────────────────────────────────────────────
@router.post("/entities/{entity_id}/net-worth/recalculate")
async def recalculate(request: Request, entity_id: UUID):
    p = require_internal_permission(request, "manage_members", "manage_portfolio")
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        ok = await conn.fetchval("SELECT 1 FROM entities WHERE id = $1 AND org_id = $2::uuid AND valid_to IS NULL "
                                 "AND system_to IS NULL", entity_id, str(org_id))
    if not ok:
        raise HTTPException(status_code=404, detail="Entity not found")
    res = await PR.recalculate(pool, org_id, entity_id, actor_id=p.user_id)
    from services import liabilities as L

    async with pool.acquire() as conn:
        nw = await L.entity_net_worth(conn, org_id, entity_id)
    return {"rollup": res.get("ran"), "family_size": len(res.get("entity_ids") or []), "net_worth": nw}


# ── Manual assets + relinks ─────────────────────────────────────────────────────────────────────────────────────
@router.get("/portfolio/manual-assets")
async def manual_assets(request: Request):
    require_permission(request, "view_held_away")
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await PM.list_manual_assets(conn, org_id)
    p = principal_of(request)
    can = bool(p and (p.is_super_admin or "manage_held_away" in p.permissions))
    return {"rows": rows, "permissions": {"can_read": True, "can_write": can, "is_super_admin": bool(p and
                                                                                                    p.is_super_admin),
                                          "read_permission": "view_held_away", "write_permission": "manage_held_away"},
            "vocabularies": {"editable": ["relink"] if can else [], "inline_editable": []}}


@router.post("/portfolio/manual-assets/relink")
async def relink(request: Request):
    p = require_permission(request, "manage_held_away")
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        done = await PM.relink_manual_assets(conn, org_id, trigger="manual", actor_id=p.user_id)
    return {"relinked": done, "count": len(done)}


# ── Integrated-account one-time import (Super Admin) ────────────────────────────────────────────────────────────
@router.get("/admin/integrated-imports")
async def list_integrated(request: Request):
    require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await II.list_imports(conn, get_org_id(request))
    return {"rows": rows, "permissions": {"can_read": True, "can_write": True, "is_super_admin": True,
                                          "read_permission": "super_admin", "write_permission": "super_admin"},
            "vocabularies": {**II.vocab(), "editable": ["upload", "decide"], "inline_editable": []}}


@router.post("/admin/integrated-imports", status_code=201)
async def upload_integrated(request: Request, file: UploadFile = File(...), as_of: date | None = Form(default=None),
                            notes: str | None = Form(default=None)):
    p = require_super_admin(request)
    data = await file.read()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="the file is larger than 5 MB")
    try:
        return await II.create_draft(await get_pool(), org_id=get_org_id(request), actor_id=p.user_id, data=data,
                                     filename=file.filename, as_of=as_of, notes=notes)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.get("/admin/integrated-imports/{import_id}")
async def get_integrated(request: Request, import_id: UUID):
    p = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            imp = II.import_out(await II.get_import(conn, get_org_id(request), import_id))
        except Exception as exc:  # noqa: BLE001
            _raise(exc)
    imp["actions"] = (["approve", "reject"] if imp["status"] == "draft" and imp["entered_by"] != str(p.user_id)
                      else [])
    return imp


@router.post("/admin/integrated-imports/{import_id}/approve")
async def approve_integrated(request: Request, import_id: UUID, body: Decision):
    return await _decide(request, import_id, "approve", body.note)


@router.post("/admin/integrated-imports/{import_id}/reject")
async def reject_integrated(request: Request, import_id: UUID, body: Decision):
    if not (body.note or "").strip():
        raise HTTPException(status_code=422, detail="a rejection needs a note saying why")
    return await _decide(request, import_id, "reject", body.note)


async def _decide(request: Request, import_id: UUID, outcome: str, note: str | None) -> Any:
    p = require_super_admin(request)
    try:
        return await II.decide(await get_pool(), org_id=get_org_id(request), actor_id=p.user_id,
                               import_id=import_id, outcome=outcome, note=note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
