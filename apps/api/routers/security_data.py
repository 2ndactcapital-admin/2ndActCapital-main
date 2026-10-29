"""Security-data layer screens (marketdata1, Task 7). Every route's gate:

  Super Admin (require_super_admin)
    GET  /admin/security-data/providers                providers, licence facts, budgets, calls by day
    PUT  /admin/security-data/providers/{key}          status / licence facts / budget (valid-axis restatement)
    GET  /admin/security-data/call-log                 provider_call_log, newest first
    GET  /admin/security-data/rank                     platform rank per data type
    PUT  /admin/security-data/rank/{data_type}         set the platform rank
    GET  /admin/security-data/runs/latest              the last nightly summary row

  Org (view: view_replication OR manage_org_settings; write: manage_org_settings)
    GET  /security-data/rank                           effective rank per type (override or platform)
    PUT  /security-data/rank/{data_type}               org override within the allowed providers ([] = clear)
    GET  /security-data/quality                        data-quality events (org + platform)
    GET  /security-data/lookup                         one value through the rank (source + as-of + licence)
    GET  /security-data/uploads                        file uploads (licence, counts, rejects, status)
    POST /security-data/uploads                        stage a file (maker) — nothing counts yet
    POST /security-data/uploads/{upload_id}/decision   approve / reject (checker ≠ maker)

Envelopes: permissions + vocabularies (labels from the server); a view-only
caller gets empty editable lists, never omitted."""

from __future__ import annotations

import json
from datetime import date

import asyncpg
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field

from services.authz import require_any_permission, require_permission, require_super_admin
from services.database import get_pool
from routers.entities import get_org_id
from services.security_data import admin as A
from services.security_data import layer as L
from services.security_data import uploads as UP
from services.security_data.types import SecurityDataError, SecurityDataNotFound

router = APIRouter(tags=["security-data"])

VIEW_PERMS = ("view_replication", "manage_org_settings")
MANAGE = "manage_org_settings"
PROVIDER_EDITABLE = list(A.PROVIDER_FIELDS)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderIn(_Body):
    status: str | None = None
    lic_internal_use: bool | None = None
    lic_member_display: str | None = None
    lic_redistribution: bool | None = None
    retention_after_cancel: str | None = None
    attribution: str | None = Field(default=None, max_length=500)
    terms_note: str | None = Field(default=None, max_length=4000)
    confirmed_on: date | None = None
    daily_call_budget: int | None = Field(default=None, ge=0, le=10_000_000)


class RankIn(_Body):
    providers: list[str] = Field(default_factory=list, max_length=20)


class DecisionIn(_Body):
    approve: bool
    note: str | None = Field(default=None, max_length=2000)


def _raise(exc: Exception):
    from services import model_upload as U

    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, SecurityDataNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (UP.UploadMakerChecker, PermissionError)):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, U.UploadRefused):
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.text}) from exc
    if isinstance(exc, asyncpg.UniqueViolationError):
        raise HTTPException(status_code=409, detail=f"refused by the database: {exc.constraint_name}") from exc
    if isinstance(exc, (asyncpg.CheckViolationError, asyncpg.InsufficientPrivilegeError)):
        raise HTTPException(status_code=422, detail=str(exc).split("\n")[0]) from exc
    if isinstance(exc, (SecurityDataError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _perms(principal, *, platform: bool = False) -> dict:
    su = principal.is_super_admin
    perms = principal.permissions
    if platform:
        return {"can_read": su, "can_write": su, "is_super_admin": su, "read_permission": "super_admin",
                "write_permission": "super_admin"}
    return {"can_read": su or any(p in perms for p in VIEW_PERMS), "can_write": su or MANAGE in perms,
            "can_approve": su or MANAGE in perms, "is_super_admin": su,
            "read_permission": " or ".join(VIEW_PERMS), "write_permission": MANAGE}


def _envelope(principal, body: dict, *, platform: bool = False) -> dict:
    perms = _perms(principal, platform=platform)
    vocab = {**A.vocabularies(), **UP.vocabulary(),
             "editable": PROVIDER_EDITABLE if (platform and perms["can_write"]) else [],
             "rank_editable": ["providers"] if perms["can_write"] else [],
             "upload_editable": ["file", "data_type", "licence", "mapping"] if (not platform and perms["can_write"])
             else []}
    return jsonable_encoder({**body, "permissions": perms, "vocabularies": vocab})


# ── Super Admin ─────────────────────────────────────────────────────────────
@router.get("/admin/security-data/providers")
async def providers(request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await A.list_providers(conn, today=date.today())
    return _envelope(principal, {"providers": rows}, platform=True)


@router.put("/admin/security-data/providers/{key}")
async def update_provider(request: Request, key: str, body: ProviderIn):
    principal = require_super_admin(request)
    pool = await get_pool()
    try:
        row = await A.update_provider(pool, key=key, changes=body.model_dump(exclude_unset=True),
                                      actor_id=principal.user_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {"provider": row}, platform=True)


@router.get("/admin/security-data/call-log")
async def call_log(request: Request, provider: str | None = None, limit: int = 200):
    principal = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await A.call_log(conn, provider_key=provider, limit=max(1, min(limit, 1000)))
    return _envelope(principal, {"calls": rows}, platform=True)


@router.get("/admin/security-data/rank")
async def platform_rank(request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await A.rank_view(conn, None)
    return _envelope(principal, {"ranks": rows}, platform=True)


@router.put("/admin/security-data/rank/{data_type}")
async def set_platform_rank(request: Request, data_type: str, body: RankIn):
    principal = require_super_admin(request)
    pool = await get_pool()
    try:
        keys = await A.set_platform_rank(pool, data_type=data_type, keys=body.providers, actor_id=principal.user_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {"data_type": data_type, "platform": keys}, platform=True)


@router.get("/admin/security-data/runs/latest")
async def latest_run(request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        run = await A.latest_run(conn)
    return _envelope(principal, {"run": run}, platform=True)


# ── Org ─────────────────────────────────────────────────────────────────────
@router.get("/security-data/rank")
async def org_rank(request: Request):
    principal = require_any_permission(request, VIEW_PERMS)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await A.rank_view(conn, org_id)
    return _envelope(principal, {"ranks": rows})


@router.put("/security-data/rank/{data_type}")
async def set_org_rank(request: Request, data_type: str, body: RankIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        keys = await A.set_org_rank(pool, org_id=org_id, data_type=data_type, keys=body.providers,
                                    actor_id=principal.user_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {"data_type": data_type, "org_override": keys})


@router.get("/security-data/quality")
async def quality(request: Request, kind: str | None = None):
    principal = require_any_permission(request, VIEW_PERMS)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await A.quality_events(conn, org_id, kind=kind)
    return _envelope(principal, {"events": rows})


@router.get("/security-data/lookup")
async def lookup(request: Request, data_type: str, subject: str, as_of: date | None = None,
                 purpose: str = "internal"):
    principal = require_any_permission(request, VIEW_PERMS)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            res = await L.get(conn, org_id, data_type, subject, as_of=as_of or date.today(), purpose=purpose)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {"result": res.as_dict()})


@router.get("/security-data/uploads")
async def uploads(request: Request):
    principal = require_any_permission(request, VIEW_PERMS)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await UP.list_uploads(conn, org_id)
    for r in rows:
        r["can_decide"] = bool(r["status"] == "pending_approval" and _perms(principal)["can_approve"]
                               and str(r["uploaded_by"]) != str(principal.user_id))
    return _envelope(principal, {"uploads": rows})


@router.post("/security-data/uploads", status_code=201)
async def upload(request: Request, file: UploadFile = File(...), data_type: str = Form(..., max_length=40),
                 licence: str = Form(..., max_length=4000), mapping: str | None = Form(default=None, max_length=20000)):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        lic = json.loads(licence)
        mp = json.loads(mapping) if mapping else None
        data = await file.read()
        row = await UP.stage(pool, org_id=org_id, actor_id=principal.user_id, data_type=data_type, data=data,
                             filename=file.filename or "upload.csv", licence=lic, mapping=mp)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="licence and mapping must be JSON objects") from exc
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {"upload": row})


@router.post("/security-data/uploads/{upload_id}/decision")
async def decide(request: Request, upload_id: str, body: DecisionIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        row = await UP.decide(pool, org_id=org_id, actor_id=principal.user_id, upload_id=upload_id,
                              approve=body.approve, note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, {"upload": row})
