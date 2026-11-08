"""DISCLOSURES1 — the disclosure library (/disclosures/…). services.disclosures holds the rules; every route names
its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_disclosures    GET  /disclosures                                   the list: every key, its title / audience
                                                                          and what is in force for this org and why
                      GET  /disclosures/keys/{key}                        one key: its versions (platform + this org,
                                                                          households, accounts), maker / checker
                      GET  /disclosures/households/{household_id}         a household's page panel: what is in force
                      GET  /disclosures/accounts/{account_id}             an account's page panel (and its overrides)
                      GET  /disclosures/resolve                           resolve keys for an org / household /
                                                                          account on an as-of date
  manage_disclosures  POST /disclosures/keys/{key}/versions               upload a new version (a DRAFT) — platform
                                                                          scope: Super Admin only
                      POST /disclosures/versions/{id}/publish             publish (never the uploader)
                      POST /disclosures/versions/{id}/retire              retire (a reason; on a date)
  active user         GET  /disclosures/mine                              a member's published member-audience
                                                                          disclosures that apply to them
                      GET  /disclosures/versions/{id}/file                the stored file, PROXIED (staff with
                                                                          view_disclosures: any version their org can
                                                                          see; anyone else: only one in /mine)
                      POST /disclosures/acknowledgements                  acknowledge the version in force for a
                                                                          subject

Super Admin first (services.authz). org_id is only ever the caller's (get_org_id) — never a body or path value;
every request model forbids extras. Another org's version is a 404.
"""
from __future__ import annotations

import base64
import binascii
from datetime import date
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import disclosures as DL
from services.authz import require_active, require_permission, require_super_admin
from services.database import get_pool
from services import sod  # S30 — the ONE maker-checker rule

router = APIRouter(tags=["disclosures"])

VIEW, MANAGE = DL.VIEW, DL.MANAGE


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionIn(_Body):
    scope: Literal["platform", "org", "household", "account"]
    household_id: UUID | None = None
    account_id: UUID | None = None
    file_name: str = Field(min_length=3, max_length=255)
    content_base64: str = Field(min_length=4, max_length=7_200_000)
    summary: str = Field(min_length=1, max_length=6000)
    effective_from: date | None = None
    note: str | None = Field(default=None, max_length=2000)


class RetireIn(_Body):
    reason: str = Field(min_length=1, max_length=1000)
    retire_on: date | None = None


class AckIn(_Body):
    version_id: UUID
    subject_type: Literal["long_short_proposal", "section351_plan", "account", "backtest", "proposal", "disclosure"]
    subject_id: UUID | None = None
    account_id: UUID | None = None
    household_id: UUID | None = None
    screen: str = Field(min_length=1, max_length=200)


def _raise(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, DL.DisclosureNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from None
    if isinstance(exc, DL.DisclosureConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if isinstance(exc, DL.DisclosureError):
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if isinstance(exc, (DL.MakerCheckerViolation, DL.DisclosureForbidden)):
        raise HTTPException(status_code=403, detail=str(exc)) from None
    if isinstance(exc, DL.StorageUnavailable):
        raise HTTPException(status_code=503, detail=str(exc)) from None
    if isinstance(exc, asyncpg.exceptions.InsufficientPrivilegeError):
        raise HTTPException(status_code=403, detail="not permitted at this scope") from None
    if isinstance(exc, asyncpg.exceptions.CheckViolationError):
        raise HTTPException(status_code=422, detail=f"refused by the database: {exc.constraint_name}") from None
    raise exc


def _permissions(principal) -> dict:
    can_write = principal.has(MANAGE)
    return {"can_read": principal.has(VIEW), "can_write": can_write, "is_super_admin": principal.is_super_admin,
            "can_manage_platform": principal.is_super_admin, "read_permission": VIEW, "write_permission": MANAGE}


def _envelope(principal, **body) -> dict:
    perms = _permissions(principal)
    return {**body, "permissions": perms,
            "vocabularies": DL.vocabularies(can_write=perms["can_write"] is True,
                                            can_manage_platform=perms["can_manage_platform"] is True)}


def _version_actions(v: dict, principal) -> list[str]:
    """What THIS caller may do to a version (server-decided; the page renders only these)."""
    if not principal.has(MANAGE):
        return []
    if v["scope"] == "platform" and not principal.is_super_admin:
        return []
    out = []
    if v["status"] == "draft" and sod.may_check_display(principal.user_id, [v["drafted_by"]]):
        out.append("publish")
    if v["status"] in ("draft", "published"):
        out.append("retire")
    return out


async def _key_rows(conn, keys) -> list[dict]:
    rows = {r["key"]: dict(r) for r in await conn.fetch(
        f"SELECT * FROM {DL.T_DISC} WHERE system_to IS NULL ORDER BY key")}
    return [rows.get(k) or {"key": k, "id": None, **DL.KEYS[k]} for k in keys] + \
        [r for k, r in rows.items() if k not in keys]


@router.get("/disclosures")
async def list_route(request: Request):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    keys = list(DL.KEYS)
    try:
        async with pool.acquire() as conn:
            cats = await _key_rows(conn, keys)
            allkeys = [c["key"] for c in cats]
            res = await DL.resolve_many(conn, allkeys, org_id=org_id)
            counts = {(r["disclosure_id"], r["scope"], r["status"]): r["n"] for r in await conn.fetch(
                f"SELECT disclosure_id, scope, status, count(*) AS n FROM {DL.T_VER} WHERE system_to IS NULL AND "
                "(scope = 'platform' OR org_id = $1::uuid) GROUP BY 1, 2, 3", str(org_id))}
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    rows = []
    for c in cats:
        r = res[c["key"]]
        did = c.get("id")
        per = {s: {st: int(counts.get((did, s, st), 0)) for st in DL.STATUS_LABELS} for s in DL.SCOPES} if did else {}
        rows.append({"key": c["key"], "title": c["title"], "category": c["category"], "audience": c["audience"],
                     "description": c.get("description"), "in_catalog": did is not None,
                     "in_force": DL.public(r), "why": r["why"], "counts": per})
    return _envelope(principal, rows=rows, actions=["upload"] if principal.has(MANAGE) else [])


@router.get("/disclosures/resolve")
async def resolve_route(request: Request, keys: str = Query(min_length=3, max_length=2000),
                  household_id: UUID | None = None, account_id: UUID | None = None, as_of: date | None = None):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            out = await DL.resolve_many(conn, [k for k in keys.split(",") if k.strip()], org_id=org_id,
                                        household_id=household_id, account_id=account_id, as_of=as_of)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, resolved={k: DL.public(v) for k, v in out.items()},
                     as_of=(as_of or date.today()).isoformat())


@router.get("/disclosures/keys/{key}")
async def key_route(request: Request, key: str):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        k = DL.check_key(key)
        async with pool.acquire() as conn:
            cat = await DL.catalog_row(conn, k)
            if cat is None and k not in DL.KEYS:
                raise DL.DisclosureNotFound("disclosure key not found")
            vers = await DL.versions_of(conn, cat["id"], org_id=org_id) if cat else []
            names = await DL.user_names(conn, [v["drafted_by"] for v in vers] + [v["published_by"] for v in vers])
            res = await DL.resolve(conn, k, org_id=org_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    spec = cat or {"key": k, **DL.KEYS[k]}
    out = []
    for v in vers:
        vo = DL.version_out(v, names)
        vo["actions"] = _version_actions(vo, principal)
        vo["in_force"] = vo["id"] == res.get("version_id")
        out.append(vo)
    return _envelope(principal, disclosure={"key": k, "title": spec["title"], "category": spec["category"],
                                            "audience": spec["audience"], "description": spec.get("description")},
                     in_force=DL.public(res), versions=out, actions=["upload"] if principal.has(MANAGE) else [])


@router.get("/disclosures/households/{household_id}")
async def household_route(request: Request, household_id: UUID):
    """A household's own page: per key, what is in force for it and why, and its overrides."""
    return await _record(request, "household", household_id)


@router.get("/disclosures/accounts/{account_id}")
async def account_route(request: Request, account_id: UUID):
    """An account's own page: per key, what is in force for it and why, and its overrides."""
    return await _record(request, "account", account_id)


async def _record(request: Request, record_type: str, record_id: UUID):
    """Upload an override from the record's page with scope = the record (POST …/keys/{key}/versions)."""
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    hh = record_id if record_type == "household" else None
    ac = record_id if record_type == "account" else None
    try:
        async with pool.acquire() as conn:
            if record_type == "household":
                ok = await conn.fetchval("SELECT 1 FROM public.households WHERE id = $1 AND org_id = $2::uuid",
                                         record_id, str(org_id))
            else:
                ok = await conn.fetchval("SELECT 1 FROM public.accounts WHERE id = $1 AND org_id = $2::uuid AND "
                                         "system_to IS NULL AND valid_to IS NULL", record_id, str(org_id))
            if not ok:
                raise DL.DisclosureNotFound(f"{record_type} not found")
            cats = await _key_rows(conn, list(DL.KEYS))
            res = await DL.resolve_many(conn, [c["key"] for c in cats], org_id=org_id, household_id=hh, account_id=ac)
            rows = []
            for c in cats:
                vers = await DL.versions_of(conn, c["id"], org_id=org_id, household_id=hh, account_id=ac,
                                            scopes=(record_type,)) if c.get("id") else []
                names = await DL.user_names(conn, [v["drafted_by"] for v in vers] + [v["published_by"] for v in vers])
                vo = []
                for v in vers:
                    x = DL.version_out(v, names)
                    x["actions"] = _version_actions(x, principal)
                    vo.append(x)
                rows.append({"key": c["key"], "title": c["title"], "audience": c["audience"],
                             "in_force": DL.public(res[c["key"]]), "why": res[c["key"]]["why"], "overrides": vo})
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return _envelope(principal, record={"type": record_type, "id": str(record_id)}, rows=rows,
                     actions=["upload_override"] if principal.has(MANAGE) else [])


@router.post("/disclosures/keys/{key}/versions", status_code=201)
async def upload_route(request: Request, key: str, body: VersionIn):
    principal = require_super_admin(request) if body.scope == "platform" else require_permission(request, MANAGE)
    org_id = get_org_id(request)
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="content_base64 is not valid base64") from None
    pool = await get_pool()
    try:
        row = await DL.upload_version(pool, org_id=org_id, actor_id=principal.user_id,
                                      is_super_admin=principal.is_super_admin, key=key, scope=body.scope,
                                      file_name=body.file_name, data=data, summary=body.summary,
                                      effective_from=body.effective_from, household_id=body.household_id,
                                      account_id=body.account_id, note=body.note)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    vo = DL.version_out(row)
    vo["actions"] = _version_actions(vo, principal)
    return {"version": vo, "key": row["key"]}


async def _scope_of(version_id: UUID) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        s = await conn.fetchval(f"SELECT scope FROM {DL.T_VER} WHERE id = $1 AND system_to IS NULL", version_id)
    if s is None:
        raise HTTPException(status_code=404, detail="disclosure version not found")
    return s


@router.post("/disclosures/versions/{version_id}/publish")
async def publish_route(request: Request, version_id: UUID):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    if await _scope_of(version_id) == "platform":
        principal = require_super_admin(request)
    pool = await get_pool()
    try:
        row = await DL.publish(pool, org_id=org_id, actor_id=principal.user_id,
                               is_super_admin=principal.is_super_admin, version_id=version_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"version": DL.version_out(row), "key": row["key"]}


@router.post("/disclosures/versions/{version_id}/retire")
async def retire_route(request: Request, version_id: UUID, body: RetireIn):
    principal = require_permission(request, MANAGE)
    org_id = get_org_id(request)
    if await _scope_of(version_id) == "platform":
        principal = require_super_admin(request)
    pool = await get_pool()
    try:
        row = await DL.retire(pool, org_id=org_id, actor_id=principal.user_id, is_super_admin=principal.is_super_admin,
                              version_id=version_id, reason=body.reason, retire_on=body.retire_on)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"version": DL.version_out(row), "key": row["key"]}


@router.get("/disclosures/mine")
async def mine_route(request: Request):
    principal = require_active(request)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await DL.member_disclosures(pool, conn, org_id, principal.user_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"rows": [{**DL.public(r), "applies_to": r["applies_to"]} for r in rows],
            "permissions": {"can_read": True, "can_write": False, "is_super_admin": principal.is_super_admin,
                            "read_permission": "active", "write_permission": None},
            "vocabularies": {"editable": [], "inline_editable": []}}


@router.get("/disclosures/versions/{version_id}/file")
async def file_route(request: Request, version_id: UUID):
    """The stored file, proxied. Staff holding view_disclosures read any version their org can see (RLS: their own
    org's + the platform's); anyone else reads only a published member disclosure that applies to them."""
    principal = require_active(request)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await DL.load_version(conn, version_id)
            if not principal.has(VIEW):
                mine_ids = {r["version_id"] for r in await DL.member_disclosures(pool, conn, org_id,
                                                                                 principal.user_id)}
                if str(row["id"]) not in mine_ids:
                    raise DL.DisclosureNotFound("disclosure version not found")
            elif row["scope"] != "platform" and str(row["org_id"]) != str(org_id):
                raise DL.DisclosureNotFound("disclosure version not found")
            data = await DL.read_file(conn, row)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    safe = "".join(ch for ch in row["file_name"] if ch.isalnum() or ch in "._- ") or "disclosure"
    return Response(content=data, media_type=row["content_type"],
                    headers={"Content-Disposition": f'inline; filename="{safe}"', "Cache-Control": "no-store",
                             "X-Content-SHA256": row["content_sha256"]})


@router.post("/disclosures/acknowledgements", status_code=201)
async def acknowledge_route(request: Request, body: AckIn):
    """Acknowledge the version IN FORCE for the subject — a version that is not (another org's, a draft, superseded)
    is refused, so a stale screen cannot record an old version as current."""
    principal = require_active(request)
    org_id = get_org_id(request)
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            v = await DL.load_version(conn, body.version_id)
            if v["scope"] != "platform" and str(v["org_id"]) != str(org_id):
                raise DL.DisclosureNotFound("disclosure version not found")
            res = await DL.resolve(conn, v["key"], org_id=org_id, household_id=body.household_id,
                                   account_id=body.account_id)
            if res.get("version_id") != str(v["id"]):
                raise DL.DisclosureConflict("that is not the version in force — reload and read the current one")
            if not principal.has(VIEW):
                mine_ids = {r["version_id"] for r in await DL.member_disclosures(pool, conn, org_id,
                                                                                 principal.user_id)}
                if str(v["id"]) not in mine_ids:
                    raise DL.DisclosureNotFound("disclosure version not found")
            subj = DL.Subject(body.subject_type, str(body.subject_id) if body.subject_id else None,
                              str(body.account_id) if body.account_id else None,
                              str(body.household_id) if body.household_id else None)
            async with conn.transaction():
                row = await DL.acknowledge(conn, org_id=org_id, user_id=principal.user_id, resolved=res,
                                           subject=subj, screen=body.screen)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    return {"acknowledgement": {"id": str(row["id"]), "version_id": str(row["version_id"]),
                                "content_sha256": row["content_sha256"], "subject_type": row["subject_type"],
                                "subject_id": str(row["subject_id"]) if row["subject_id"] else None,
                                "acknowledged_at": row["acknowledged_at"].isoformat()}}
