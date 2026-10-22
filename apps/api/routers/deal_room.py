"""DEALROOM1 — Marketplace Deal Room, Phase 1 (all routes under /deal-room).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads the
helpers below):

  _require_deal_manager   the deal's publisher users, its delegates, staff with
                          manage_deals and org admins in the deal's org, Super
                          Admin (services.deal_room.deal_rights) — else 403
  _require_deal_viewer    a manager, or view_deals on a member-visible deal;
                          another org's deal or a hidden one is a 404
  _require_publisher_or_staff  create: acts for a publisher entity, or manage_deals
  _require_grid_scope     the access grid: Super Admin (all), manage_deals or
                          manage_org_settings (the firm), a publisher user or
                          delegate of ≥1 deal (those deals) — else 403
  _require_signature_reader  a signed copy: its signer, or a manager of its deal
  require_permission / require_any_permission / require_super_admin / require_active
                          as named on each route

Existing marketplace routes and their gates are untouched; publisher and
delegate editing lives here. org_id is only ever the caller's (get_org_id) or
the deal row's own — never a body or path value. Request models forbid extras.
"""

from __future__ import annotations

import html
import json
import os
import uuid
from datetime import date
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from routers.entities import get_org_id
from services import deal_room as R
from services import deal_room_templates as T
from services import storage
from services.authz import (
    require_active,
    require_any_permission,
    require_permission,
    require_super_admin,
)
from services.database import get_pool

router = APIRouter(tags=["deal-room"])

PUBLISHER_ADMIN_PERMS = ("manage_deals", "manage_org_settings")
EDITABLE_DEAL_FIELDS = (
    "name", "description", "asset_super_class", "asset_class", "asset_sub_category",
    "sponsor_entity_id", "sponsor_name_override", "target_raise", "minimum_investment",
    "expected_return_pct", "term_months", "deal_date", "close_date", "location", "highlights", "tags",
)
SETTINGS_EDITABLE = ("eligibility_rule", "approval_mode", "sponsor_display", "code_name")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublisherCreate(_Body):
    entity_id: UUID
    user_ids: list[UUID] = []


class PublisherUser(_Body):
    user_id: UUID


class DealRoomCreate(_Body):
    name: str = Field(min_length=1)
    description: str | None = None
    asset_super_class: str | None = None
    asset_class: str | None = None
    asset_sub_category: str | None = None
    sponsor_entity_id: UUID | None = None
    sponsor_name_override: str | None = None
    target_raise: float | None = None
    minimum_investment: float | None = None
    expected_return_pct: float | None = None
    term_months: int | None = None
    deal_date: date | None = None
    close_date: date | None = None
    location: str | None = None
    highlights: list[str] | None = None
    tags: list[str] | None = None
    is_featured: bool | None = None
    publisher_entity_id: UUID | None = None


class DealRoomEdit(_Body):
    name: str | None = None
    description: str | None = None
    asset_super_class: str | None = None
    asset_class: str | None = None
    asset_sub_category: str | None = None
    sponsor_entity_id: UUID | None = None
    sponsor_name_override: str | None = None
    target_raise: float | None = None
    minimum_investment: float | None = None
    expected_return_pct: float | None = None
    term_months: int | None = None
    deal_date: date | None = None
    close_date: date | None = None
    location: str | None = None
    highlights: list[str] | None = None
    tags: list[str] | None = None


class SettingsEdit(_Body):
    eligibility_rule: str | None = None
    approval_mode: str | None = None
    sponsor_display: str | None = None
    code_name: str | None = None


class DelegateAdd(_Body):
    user_id: UUID


class FolderCreate(_Body):
    parent_id: UUID
    label: str = Field(min_length=1, max_length=120)
    access_level: str


class FolderEdit(_Body):
    access_level: str | None = None
    label: str | None = None


class NamedAdd(_Body):
    user_id: UUID


class DocumentMove(_Body):
    folder_id: UUID


class AgreementCreate(_Body):
    template_id: UUID
    sort_order: int | None = None
    field_values: dict = {}
    exhibit_document_id: UUID | None = None


class AgreementEdit(_Body):
    sort_order: int | None = None
    field_values: dict | None = None
    exhibit_document_id: UUID | None = None


class AccessRequest(_Body):
    entity_id: UUID


class SignRequest(_Body):
    typed_name: str
    consent: bool
    rendered_text_sha256: str


class Decision(_Body):
    access_ids: list[UUID] = Field(min_length=1, max_length=200)
    action: str
    level: str | None = None
    reason: str | None = None


def _http(exc: R.DealRoomError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _jsonable(v):
    if isinstance(v, UUID):
        return str(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "is_finite"):  # Decimal
        return float(v)
    return v


# ── Gates ──────────────────────────────────────────────────────────────────
async def _deal_or_404(conn, principal, deal_id):
    deal = await R.fetch_deal(conn, deal_id)
    if deal is None or (str(deal["org_id"]) != str(principal.org_id) and not principal.is_super_admin):
        raise HTTPException(status_code=404, detail="Deal not found")
    return dict(deal)


async def _require_deal_manager(request: Request, conn, deal_id):
    principal = require_active(request)
    deal = await _deal_or_404(conn, principal, deal_id)
    rights = await R.deal_rights(conn, principal, deal)
    if not rights.can_manage:
        if not rights.can_view:
            raise HTTPException(status_code=404, detail="Deal not found")
        raise HTTPException(status_code=403, detail=R.MANAGE_DENIED)
    return principal, deal, rights


async def _require_deal_viewer(request: Request, conn, deal_id):
    principal = require_active(request)
    deal = await _deal_or_404(conn, principal, deal_id)
    rights = await R.deal_rights(conn, principal, deal)
    if not (rights.can_manage or rights.can_view):
        raise HTTPException(status_code=404, detail="Deal not found")
    return principal, deal, rights


async def _require_publisher_or_staff(request: Request, conn):
    principal = require_active(request)
    staff = principal.is_super_admin or principal.has("manage_deals")
    acts = await R.publisher_entities_for_user(conn, principal.org_id, principal.user_id)
    if not staff and not acts:
        raise HTTPException(status_code=403, detail=(
            "Creating a deal needs a publisher: act for a publisher entity "
            "(POST /deal-room/publishers/self makes your CRM record one), or staff."))
    return principal, staff, acts


async def _require_grid_scope(request: Request, conn):
    principal = require_active(request)
    if principal.is_super_admin:
        return principal, "all", None
    if principal.has("manage_deals") or principal.has("manage_org_settings"):
        return principal, "firm", None
    ids = await R.managed_deal_ids(conn, principal.org_id, principal.user_id)
    if not ids:
        raise HTTPException(status_code=403, detail=R.GRID_DENIED)
    return principal, "deals", ids


async def _require_signature_reader(request: Request, conn, signature_id):
    principal = require_active(request)
    sig = await conn.fetchrow(f"SELECT {R.SIGNATURE_COLS} FROM deal_signatures WHERE id = $1",
                              UUID(str(signature_id)))
    if sig is None:
        raise HTTPException(status_code=404, detail="Signature not found")
    if str(sig["user_id"]) == str(principal.user_id):
        return principal, dict(sig)
    deal = await R.fetch_deal(conn, sig["deal_id"])
    if deal is None or not (await R.deal_rights(conn, principal, dict(deal))).can_manage:
        raise HTTPException(status_code=404, detail="Signature not found")
    return principal, dict(sig)


# ── Context ────────────────────────────────────────────────────────────────
@router.get("/deal-room/me")
async def deal_room_me(request: Request):
    """The caller's own deal-room context (self-scoped read)."""
    principal = require_active(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        acts = await R.publisher_entities_for_user(conn, principal.org_id, principal.user_id)
        names = {r["id"]: r["display_name"] for r in await conn.fetch(
            "SELECT id, display_name FROM entities WHERE id = ANY($1::uuid[]) AND valid_to IS NULL "
            "AND system_to IS NULL", acts)} if acts else {}
        person = await R.crm_person_of(conn, principal.org_id, principal.user_id)
        investing = await R.investing_entities(pool, conn, principal.org_id, principal.user_id)
        managed = await R.managed_deal_ids(conn, principal.org_id, principal.user_id)
    staff = principal.is_super_admin or principal.has("manage_deals")
    return {
        "publisher_entities": [{"id": str(e), "name": names.get(e)} for e in acts],
        "crm_person_id": str(person) if person else None,
        "investing_entities": investing,
        "can_create_deal": bool(staff or acts),
        "can_self_enrol": bool(person and person not in acts and principal.has("submit_deal")),
        "has_access_grid": bool(principal.is_super_admin or principal.has("manage_deals")
                                or principal.has("manage_org_settings") or managed),
    }


# ── Publishers (CRM capability) ────────────────────────────────────────────
@router.get("/deal-room/publishers")
async def list_publishers(request: Request):
    require_any_permission(request, PUBLISHER_ADMIN_PERMS)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.entity_id, p.is_house, p.granted_at, e.display_name, e.entity_type::text AS entity_type,
                   COALESCE(json_agg(json_build_object('user_id', u.id, 'name', COALESCE(u.full_name, u.email)))
                            FILTER (WHERE u.id IS NOT NULL), '[]') AS users
            FROM entity_publishers p
            JOIN entities e ON e.id = p.entity_id AND e.valid_to IS NULL AND e.system_to IS NULL
            LEFT JOIN entity_publisher_users pu ON pu.entity_id = p.entity_id AND pu.revoked_at IS NULL
            LEFT JOIN users u ON u.id = pu.user_id
            WHERE p.org_id = $1 AND p.revoked_at IS NULL
            GROUP BY p.entity_id, p.is_house, p.granted_at, e.display_name, e.entity_type
            ORDER BY e.display_name
            """, UUID(str(org_id)))
    return {"publishers": [{**_jsonable(dict(r)), "users": json.loads(r["users"]) if isinstance(r["users"], str)
                            else r["users"]} for r in rows]}


@router.post("/deal-room/publishers", status_code=201)
async def create_publisher(request: Request, body: PublisherCreate):
    principal = require_any_permission(request, PUBLISHER_ADMIN_PERMS)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            ok = await conn.fetchval("SELECT 1 FROM entities WHERE id = $1 AND org_id = $2 AND valid_to IS NULL "
                                     "AND system_to IS NULL", body.entity_id, UUID(str(org_id)))
            if not ok:
                raise HTTPException(status_code=404, detail="Entity not found")
            for uid in body.user_ids:
                if not await conn.fetchval("SELECT 1 FROM users WHERE id = $1 AND org_id = $2", uid,
                                           UUID(str(org_id))):
                    raise HTTPException(status_code=404, detail="User not found")
            created = await R.ensure_publisher(conn, org_id, body.entity_id, actor=principal.user_id,
                                               user_ids=body.user_ids)
            await R.audit(conn, org_id=org_id, actor=principal.user_id, action="deal_room.publisher_grant",
                          resource_id=body.entity_id, payload={"user_ids": body.user_ids, "created": created})
    return {"entity_id": str(body.entity_id), "created": created}


@router.post("/deal-room/publishers/self", status_code=201)
async def self_enrol_publisher(request: Request):
    """Everyone can publish at launch: the caller's own CRM person becomes a
    publisher entity with the caller acting for it."""
    principal = require_permission(request, "submit_deal")
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            person = await R.crm_person_of(conn, org_id, principal.user_id)
            if person is None:
                raise HTTPException(status_code=422, detail=(
                    "Your account is not linked to a CRM person record yet — ask staff to link it."))
            created = await R.ensure_publisher(conn, org_id, person, actor=principal.user_id,
                                               user_ids=[principal.user_id], note="Self-enrolled publisher")
            await R.audit(conn, org_id=org_id, actor=principal.user_id, action="deal_room.publisher_self",
                          resource_id=person, payload={"created": created})
    return {"entity_id": str(person), "created": created}


@router.post("/deal-room/publishers/{entity_id}/users", status_code=201)
async def add_publisher_user(request: Request, entity_id: UUID, body: PublisherUser):
    principal = require_any_permission(request, PUBLISHER_ADMIN_PERMS)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await R.is_publisher_entity(conn, org_id, entity_id):
                raise HTTPException(status_code=404, detail="Publisher not found")
            if not await conn.fetchval("SELECT 1 FROM users WHERE id = $1 AND org_id = $2", body.user_id,
                                       UUID(str(org_id))):
                raise HTTPException(status_code=404, detail="User not found")
            await R.ensure_publisher(conn, org_id, entity_id, actor=principal.user_id, user_ids=[body.user_id])
            await R.audit(conn, org_id=org_id, actor=principal.user_id, action="deal_room.publisher_user_add",
                          resource_id=entity_id, payload={"user_id": body.user_id})
    return {"entity_id": str(entity_id), "user_id": str(body.user_id)}


@router.delete("/deal-room/publishers/{entity_id}/users/{user_id}")
async def remove_publisher_user(request: Request, entity_id: UUID, user_id: UUID):
    principal = require_any_permission(request, PUBLISHER_ADMIN_PERMS)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            res = await conn.execute(
                "UPDATE entity_publisher_users SET revoked_at = now(), revoked_by = $3 WHERE org_id = $4 "
                "AND entity_id = $1 AND user_id = $2 AND revoked_at IS NULL",
                entity_id, user_id, UUID(str(principal.user_id)), UUID(str(org_id)))
            if res.endswith(" 0"):
                raise HTTPException(status_code=404, detail="Publisher user not found")
            await R.audit(conn, org_id=org_id, actor=principal.user_id, action="deal_room.publisher_user_remove",
                          resource_id=entity_id, payload={"user_id": user_id})
    return {"revoked": True}


# ── Deals ──────────────────────────────────────────────────────────────────
@router.post("/deal-room/deals", status_code=201)
async def create_room_deal(request: Request, body: DealRoomCreate):
    from schemas.marketplace import DealCreate
    from services.deal_creation import insert_deal
    from services.taxonomy import validate_taxonomy_fields

    org_id = get_org_id(request)
    pool = await get_pool()
    errors = await validate_taxonomy_fields(str(org_id), body.asset_super_class, body.asset_class,
                                            body.asset_sub_category)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, staff, acts = await _require_publisher_or_staff(request, conn)
            pub = body.publisher_entity_id
            if pub is not None:
                if staff:
                    if not await R.is_publisher_entity(conn, org_id, pub):
                        raise HTTPException(status_code=422, detail="That entity is not a publisher.")
                elif pub not in acts:
                    raise HTTPException(status_code=403, detail="You do not act for that publisher entity.")
            elif not staff:
                person = await R.crm_person_of(conn, org_id, principal.user_id)
                pub = person if person in acts else acts[0]
            fields = body.model_dump(exclude={"publisher_entity_id"})
            try:
                row = await insert_deal(conn, org_id, DealCreate(**fields), returning=R.DEAL_COLS,
                                        created_by=principal.user_id, publisher_entity_id=pub, staff=staff)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
    return _jsonable(dict(row))


@router.patch("/deal-room/deals/{deal_id}")
async def edit_room_deal(request: Request, deal_id: UUID, body: DealRoomEdit):
    from services import deal_lifecycle as dlc
    from services.taxonomy import validate_taxonomy_fields

    updates = body.model_dump(exclude_unset=True)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            if any(k in updates for k in ("asset_super_class", "asset_class", "asset_sub_category")):
                errors = await validate_taxonomy_fields(str(deal["org_id"]), updates.get("asset_super_class"),
                                                        updates.get("asset_class"), updates.get("asset_sub_category"))
                if errors:
                    raise HTTPException(status_code=422, detail=errors)
            if not updates:
                return _jsonable(deal)
            await dlc.archive_version(conn, deal["org_id"], deal_id)
            sets, params = ["system_from = now()", "updated_at = now()"], [deal_id]
            for f in EDITABLE_DEAL_FIELDS:
                if f in updates:
                    params.append(updates[f])
                    sets.append(f"{f} = ${len(params)}")
            row = await conn.fetchrow(
                f"UPDATE deals SET {', '.join(sets)} WHERE id = $1 AND valid_to IS NULL AND system_to IS NULL "
                f"RETURNING {R.DEAL_COLS}", *params)
            await conn.execute(
                "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
                "VALUES ($1, $2, 'deal_room.deal_edit', 'deals', $3, $4::jsonb)",
                deal["org_id"], UUID(str(principal.user_id)), deal_id,
                json.dumps({"changed": sorted(updates)}, default=str))
    return _jsonable(dict(row))


async def _room_envelope(pool, conn, principal, deal, rights) -> dict:
    lab = await R.labels(conn, deal["org_id"])
    settings = await R.room_settings(conn, deal["id"])
    folders = await R.folders_of(conn, deal["id"])
    by_id = {f["id"]: f for f in folders}
    access = await R.live_access(conn, deal["id"], principal.user_id)
    manage = rights.can_manage
    open_ids = None if manage else await R.open_folder_ids(conn, deal["id"], principal.user_id)
    docs = await conn.fetch(
        f"SELECT {R.DOC_COLS} FROM deal_documents WHERE deal_id = $1 AND folder_id IS NOT NULL "
        f"AND deleted_at IS NULL AND ($2::boolean OR status = 'approved') ORDER BY created_at",
        deal["id"], manage)
    named = {}
    if manage:
        for r in await conn.fetch(
                "SELECT n.folder_id, n.user_id, COALESCE(u.full_name, u.email) AS name FROM deal_folder_named n "
                "JOIN users u ON u.id = n.user_id JOIN deal_folders f ON f.id = n.folder_id "
                "WHERE f.deal_id = $1 AND n.removed_at IS NULL", deal["id"]):
            named.setdefault(r["folder_id"], []).append({"user_id": str(r["user_id"]), "name": r["name"]})
    status = access["status"] if access else None
    out_folders = []
    for f in folders:
        eff = R.effective_level(f, by_id)
        is_open = manage or f["id"] in open_ids
        reason = None
        if not is_open:
            key = {"revoked": "revoked", "declined": "declined", "not_eligible": "not_eligible"}.get(status) \
                or (eff if status in ("requested", "signed_awaiting", "granted") else "view")
            reason = lab.get(f"deal_room_lock_{key}")
        entry = {
            "id": str(f["id"]), "key": f["key"], "label": R.folder_label(f, lab), "sort_order": f["sort_order"],
            "access_level": f["access_level"], "effective_level": eff,
            "access_level_label": lab.get(f"deal_room_level_{eff}"),
            "parent_id": str(f["parent_id"]) if f["parent_id"] else None, "is_standard": f["is_standard"],
            "open": bool(is_open), "lock_reason": reason,
        }
        if is_open:
            entry["documents"] = [
                {"id": str(d["id"]), "file_name": d["file_name"], "file_type": d["file_type"],
                 "file_size_bytes": d["file_size_bytes"], "status": d["status"],
                 "created_at": d["created_at"].isoformat()}
                for d in docs if d["folder_id"] == f["id"]]
        if manage:
            entry["named"] = named.get(f["id"], [])
        out_folders.append(entry)

    agreements = await R.agreements_of(conn, deal["id"], published_only=not manage)
    mine = await R.my_signatures(conn, deal["id"], principal.user_id)
    sponsor = None
    if settings["sponsor_display"] == "code_name":
        sponsor = settings["code_name"]
    elif deal.get("sponsor_entity_id"):
        sponsor = await conn.fetchval("SELECT display_name FROM entities WHERE id = $1 AND valid_to IS NULL "
                                      "AND system_to IS NULL", deal["sponsor_entity_id"])
    sponsor = sponsor or (deal.get("sponsor_name_override") if settings["sponsor_display"] == "name" else None)
    entity_name = None
    if access and access["entity_id"]:
        entity_name = await conn.fetchval("SELECT display_name FROM entities WHERE id = $1 AND valid_to IS NULL "
                                          "AND system_to IS NULL", access["entity_id"])
    elig = access["eligibility"] if access else None
    if isinstance(elig, str):
        elig = json.loads(elig)
    envelope = {
        "deal": {
            "id": str(deal["id"]), "name": deal["name"], "description": deal.get("description"),
            "deal_status": deal["deal_status"], "target_raise": _jsonable(deal.get("target_raise")),
            "minimum_investment": _jsonable(deal.get("minimum_investment")), "sponsor": sponsor,
            "location": deal.get("location"), "highlights": deal.get("highlights") or [],
        },
        "rights": {"role": rights.role, "can_manage": manage},
        "permissions": {
            "can_read": True, "can_write": manage, "is_super_admin": bool(principal.is_super_admin),
            "read_permission": "view_deals", "write_permission": "deal publisher / delegate / manage_deals",
        },
        "folders": out_folders,
        "my_access": None if access is None else {
            "id": str(access["id"]), "status": access["status"],
            "status_label": lab.get(f"deal_room_status_{access['status']}"),
            "level_granted": access["level_granted"], "entity_id": str(access["entity_id"])
            if access["entity_id"] else None, "entity_name": entity_name,
            "missing": (elig or {}).get("missing", []), "reason": access["reason"],
            "decided_at": access["decided_at"].isoformat() if access["decided_at"] else None,
        },
        "agreements": [
            {"id": str(a["id"]), "title": a["title"], "template_key": a["template_key"],
             "template_version": a["template_version"], "template_status": a["template_status"],
             "sort_order": a["sort_order"], "status": a["status"],
             "signed_at": mine[a["id"]]["signed_at"].isoformat() if a["id"] in mine else None,
             "signature_id": str(mine[a["id"]]["id"]) if a["id"] in mine else None,
             **({"field_values": a["field_values"], "exhibit_document_id": str(a["exhibit_document_id"])
                 if a["exhibit_document_id"] else None,
                 "fields": [f for f in a["fields"] if f["source"] in (T.SOURCE_PUBLISHER, T.SOURCE_DEAL)],
                 "blockers": R.publish_blockers(a, await R.agreement_values(conn, deal, a))} if manage else {})}
            for a in agreements],
        "vocabularies": {**R.vocabularies(lab),
                         "editable": ["deal", "settings", "folders", "documents", "agreements", "delegates"]
                         if manage else []},
    }
    if manage:
        envelope["settings"] = _jsonable(settings)
        envelope["delegates"] = [
            {"user_id": str(r["user_id"]), "name": r["name"]} for r in await conn.fetch(
                "SELECT d.user_id, COALESCE(u.full_name, u.email) AS name FROM deal_delegates d "
                "JOIN users u ON u.id = d.user_id WHERE d.deal_id = $1 AND d.revoked_at IS NULL", deal["id"])]
    else:
        envelope["investing_entities"] = await R.investing_entities(pool, conn, principal.org_id,
                                                                    principal.user_id)
    return envelope


@router.get("/deal-room/deals/{deal_id}")
async def get_room(request: Request, deal_id: UUID):
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
        return await _room_envelope(pool, conn, principal, deal, rights)


@router.put("/deal-room/deals/{deal_id}/settings")
async def put_settings(request: Request, deal_id: UUID, body: SettingsEdit):
    changes = body.model_dump(exclude_unset=True)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            try:
                out = await R.update_settings(conn, deal["org_id"], deal_id, changes, actor=principal.user_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
            await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.settings",
                          resource_id=deal_id, payload=changes)
    return _jsonable(out)


@router.post("/deal-room/deals/{deal_id}/delegates", status_code=201)
async def add_delegate(request: Request, deal_id: UUID, body: DelegateAdd):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            if not await conn.fetchval("SELECT 1 FROM users WHERE id = $1 AND org_id = $2 AND status = 'active'",
                                       body.user_id, deal["org_id"]):
                raise HTTPException(status_code=404, detail="User not found")
            await conn.execute(
                "INSERT INTO deal_delegates (org_id, deal_id, user_id, granted_by) SELECT $1, $2, $3, $4 "
                "WHERE NOT EXISTS (SELECT 1 FROM deal_delegates WHERE deal_id = $2 AND user_id = $3 "
                "AND revoked_at IS NULL)", deal["org_id"], deal_id, body.user_id, UUID(str(principal.user_id)))
            await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.delegate_add",
                          resource_id=deal_id, payload={"user_id": body.user_id})
    return {"deal_id": str(deal_id), "user_id": str(body.user_id)}


@router.delete("/deal-room/deals/{deal_id}/delegates/{user_id}")
async def remove_delegate(request: Request, deal_id: UUID, user_id: UUID):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            res = await conn.execute(
                "UPDATE deal_delegates SET revoked_at = now(), revoked_by = $3 WHERE deal_id = $1 AND user_id = $2 "
                "AND revoked_at IS NULL", deal_id, user_id, UUID(str(principal.user_id)))
            if res.endswith(" 0"):
                raise HTTPException(status_code=404, detail="Delegate not found")
            await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.delegate_remove",
                          resource_id=deal_id, payload={"user_id": user_id})
    return {"revoked": True}


# ── Folders ────────────────────────────────────────────────────────────────
async def _folder(conn, deal_id, folder_id):
    row = await conn.fetchrow(
        "SELECT id, key, label, sort_order, access_level, parent_id, is_standard FROM deal_folders "
        "WHERE id = $1 AND deal_id = $2 AND system_to IS NULL", folder_id, deal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return dict(row)


@router.post("/deal-room/deals/{deal_id}/folders", status_code=201)
async def add_subfolder(request: Request, deal_id: UUID, body: FolderCreate):
    if body.access_level not in R.LEVELS:
        raise HTTPException(status_code=422, detail=f"access_level must be one of {', '.join(R.LEVELS)}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            parent = await _folder(conn, deal_id, body.parent_id)
            fid = await conn.fetchval(
                "INSERT INTO deal_folders (org_id, deal_id, key, label, sort_order, access_level, parent_id, "
                "is_standard, created_by) VALUES ($1, $2, $3, $4, $5, $6, $7, false, $8) RETURNING id",
                deal["org_id"], deal_id, f"sub_{uuid.uuid4().hex[:12]}", body.label.strip(),
                parent["sort_order"], body.access_level, parent["id"], UUID(str(principal.user_id)))
            await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.folder_add",
                          resource_id=fid, payload={"parent_id": parent["id"], "level": body.access_level})
    return {"id": str(fid)}


@router.patch("/deal-room/deals/{deal_id}/folders/{folder_id}")
async def edit_folder(request: Request, deal_id: UUID, folder_id: UUID, body: FolderEdit):
    changes = body.model_dump(exclude_unset=True)
    if "access_level" in changes and changes["access_level"] not in R.LEVELS:
        raise HTTPException(status_code=422, detail=f"access_level must be one of {', '.join(R.LEVELS)}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            f = await _folder(conn, deal_id, folder_id)
            if "label" in changes and f["is_standard"]:
                raise HTTPException(status_code=422, detail="The nine standard folders keep their configured names.")
            await R.archive_folder_version(conn, folder_id)
            row = await conn.fetchrow(
                "UPDATE deal_folders SET access_level = COALESCE($2, access_level), label = COALESCE($3, label), "
                "system_from = now() WHERE id = $1 AND system_to IS NULL RETURNING id, access_level, label",
                folder_id, changes.get("access_level"), (changes.get("label") or None))
            await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.folder_edit",
                          resource_id=folder_id, payload={"from": f["access_level"], **changes})
    return _jsonable(dict(row))


@router.post("/deal-room/deals/{deal_id}/folders/{folder_id}/named", status_code=201)
async def name_investor(request: Request, deal_id: UUID, folder_id: UUID, body: NamedAdd):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            await _folder(conn, deal_id, folder_id)
            if not await conn.fetchval("SELECT 1 FROM users WHERE id = $1 AND org_id = $2", body.user_id,
                                       deal["org_id"]):
                raise HTTPException(status_code=404, detail="User not found")
            await conn.execute(
                "INSERT INTO deal_folder_named (org_id, folder_id, user_id, added_by) SELECT $1, $2, $3, $4 "
                "WHERE NOT EXISTS (SELECT 1 FROM deal_folder_named WHERE folder_id = $2 AND user_id = $3 "
                "AND removed_at IS NULL)", deal["org_id"], folder_id, body.user_id, UUID(str(principal.user_id)))
            await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.folder_name",
                          resource_id=folder_id, payload={"user_id": body.user_id})
    return {"folder_id": str(folder_id), "user_id": str(body.user_id)}


@router.delete("/deal-room/deals/{deal_id}/folders/{folder_id}/named/{user_id}")
async def unname_investor(request: Request, deal_id: UUID, folder_id: UUID, user_id: UUID):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            await _folder(conn, deal_id, folder_id)
            res = await conn.execute(
                "UPDATE deal_folder_named SET removed_at = now(), removed_by = $3 WHERE folder_id = $1 "
                "AND user_id = $2 AND removed_at IS NULL", folder_id, user_id, UUID(str(principal.user_id)))
            if res.endswith(" 0"):
                raise HTTPException(status_code=404, detail="Not named on this folder")
            await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.folder_unname",
                          resource_id=folder_id, payload={"user_id": user_id})
    return {"removed": True}


# ── Documents ──────────────────────────────────────────────────────────────
@router.post("/deal-room/deals/{deal_id}/folders/{folder_id}/documents", status_code=201)
async def upload_room_document(request: Request, deal_id: UUID, folder_id: UUID,
                               file: UploadFile = File(...), document_type: str | None = Form(None)):
    pool = await get_pool()
    data = await file.read()
    async with pool.acquire() as conn:
        principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
        folder = await _folder(conn, deal_id, folder_id)
        bucket = os.environ.get("R2_BUCKET_NAME", storage.DEFAULT_BUCKET)
        key = f"deals/{deal_id}/{folder['key']}/{uuid.uuid4()}_{file.filename}"
        await run_in_threadpool(storage.upload_bytes, key, data, file.content_type, bucket)
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                INSERT INTO deal_documents (org_id, deal_id, folder_id, file_name, file_type, file_size_bytes,
                    document_type, r2_key, r2_bucket, processing_status, uploaded_by, status, reviewed_by,
                    reviewed_at, visible_to_members)
                VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, 'general'), $8, $9, 'pending', $10, 'approved', $10,
                        now(), false)
                RETURNING {R.DOC_COLS}
                """, deal["org_id"], deal_id, folder_id, file.filename, file.content_type or "application/octet-stream",
                len(data), document_type, key, bucket, UUID(str(principal.user_id)))
            await conn.execute(
                "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
                "VALUES ($1, $2, 'deal_room.document_upload', 'deal_documents', $3, $4::jsonb)",
                deal["org_id"], UUID(str(principal.user_id)), row["id"],
                json.dumps({"folder_id": str(folder_id), "file_name": file.filename}))
    out = _jsonable(dict(row))
    out.pop("r2_key", None)
    out.pop("r2_bucket", None)
    return out


@router.patch("/deal-room/deals/{deal_id}/documents/{doc_id}")
async def move_room_document(request: Request, deal_id: UUID, doc_id: UUID, body: DocumentMove):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            await _folder(conn, deal_id, body.folder_id)
            prev = await conn.fetchval("SELECT folder_id FROM deal_documents WHERE id = $1 AND deal_id = $2 "
                                       "AND deleted_at IS NULL", doc_id, deal_id)
            row = await conn.fetchrow(
                "UPDATE deal_documents SET folder_id = $3, updated_at = now() WHERE id = $1 AND deal_id = $2 "
                "AND deleted_at IS NULL RETURNING id, folder_id", doc_id, deal_id, body.folder_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Document not found")
            await conn.execute(
                "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
                "VALUES ($1, $2, 'deal_room.document_move', 'deal_documents', $3, $4::jsonb)",
                deal["org_id"], UUID(str(principal.user_id)), doc_id,
                json.dumps({"from": str(prev) if prev else None, "to": str(body.folder_id)}))
    return _jsonable(dict(row))


@router.delete("/deal-room/deals/{deal_id}/documents/{doc_id}")
async def delete_room_document(request: Request, deal_id: UUID, doc_id: UUID):
    """Soft delete: the row and its R2 object stay (audit, Phase 3 analytics);
    it leaves every listing and download at once."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            row = await conn.fetchrow(
                "UPDATE deal_documents SET deleted_at = now(), deleted_by = $3, updated_at = now() WHERE id = $1 "
                "AND deal_id = $2 AND deleted_at IS NULL RETURNING id", doc_id, deal_id, UUID(str(principal.user_id)))
            if row is None:
                raise HTTPException(status_code=404, detail="Document not found")
            await conn.execute(
                "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
                "VALUES ($1, $2, 'deal_room.document_delete', 'deal_documents', $3, '{}'::jsonb)",
                deal["org_id"], UUID(str(principal.user_id)), doc_id)
    return {"deleted": True}


@router.get("/deal-room/deals/{deal_id}/documents/{doc_id}/download")
async def download_room_document(request: Request, deal_id: UUID, doc_id: UUID):
    """A short-lived signed R2 URL — only when the document's folder is open to
    the caller. Every refusal is the SAME 404 (no existence leak)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
        if rights.can_manage:
            doc = await conn.fetchrow("SELECT id, file_name, r2_key, r2_bucket FROM deal_documents WHERE id = $1 "
                                      "AND deal_id = $2 AND deleted_at IS NULL", doc_id, deal_id)
        else:
            doc = await conn.fetchrow(
                f"SELECT id, file_name, r2_key, r2_bucket FROM deal_documents dd WHERE id = $1 AND deal_id = $2 "
                f"AND folder_id IS NOT NULL AND {R.member_doc_sql('dd', '$3::uuid')}",
                doc_id, deal_id, UUID(str(principal.user_id)))
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
    expires = 300
    url = await run_in_threadpool(storage.get_signed_url, doc["r2_key"], expires, doc["r2_bucket"])
    return {"url": url, "expires_in": expires, "file_name": doc["file_name"]}


# ── Agreements ─────────────────────────────────────────────────────────────
@router.get("/deal-room/templates")
async def list_templates(request: Request):
    require_active(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, key, version, title, scope, status, fields, source_path FROM agreement_templates "
            "WHERE status <> 'retired' ORDER BY key, version")
    return {"templates": [{**_jsonable(dict(r)), "fields": json.loads(r["fields"]) if isinstance(r["fields"], str)
                           else r["fields"]} for r in rows]}


@router.post("/deal-room/deals/{deal_id}/agreements", status_code=201)
async def add_agreement(request: Request, deal_id: UUID, body: AgreementCreate):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            if not await conn.fetchval("SELECT 1 FROM agreement_templates WHERE id = $1 AND status <> 'retired'",
                                       body.template_id):
                raise HTTPException(status_code=404, detail="Template not found")
            if body.exhibit_document_id and not await conn.fetchval(
                    "SELECT 1 FROM deal_documents WHERE id = $1 AND deal_id = $2 AND deleted_at IS NULL",
                    body.exhibit_document_id, deal_id):
                raise HTTPException(status_code=404, detail="Exhibit document not found on this deal")
            order = body.sort_order
            if order is None:
                order = int(await conn.fetchval(
                    "SELECT COALESCE(max(sort_order), 0) + 1 FROM deal_agreements WHERE deal_id = $1 "
                    "AND system_to IS NULL AND status <> 'withdrawn'", deal_id))
            try:
                aid = await conn.fetchval(
                    "INSERT INTO deal_agreements (org_id, deal_id, template_id, sort_order, field_values, "
                    "exhibit_document_id, created_by) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7) RETURNING id",
                    deal["org_id"], deal_id, body.template_id, order, json.dumps(body.field_values),
                    body.exhibit_document_id, UUID(str(principal.user_id)))
            except Exception as exc:  # the live-template unique index
                if "deal_agreements_live_template_uq" in str(exc):
                    raise HTTPException(status_code=409, detail="This deal already requires that agreement.") from exc
                raise
            await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.agreement_add",
                          resource_id=aid, payload={"template_id": body.template_id, "sort_order": order})
    return {"id": str(aid), "sort_order": order}


async def _agreement(conn, deal_id, agreement_id) -> dict:
    a = next((x for x in await R.agreements_of(conn, deal_id, published_only=False)
              if str(x["id"]) == str(agreement_id)), None)
    if a is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return a


@router.patch("/deal-room/deals/{deal_id}/agreements/{agreement_id}")
async def edit_agreement(request: Request, deal_id: UUID, agreement_id: UUID, body: AgreementEdit):
    changes = body.model_dump(exclude_unset=True)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            a = await _agreement(conn, deal_id, agreement_id)
            if a["status"] != "draft":
                raise HTTPException(status_code=409, detail=(
                    "A published agreement is fixed; new versions with re-signing come in Phase 2."))
            # system-axis history copy, then the live row (id kept for signatures)
            await conn.execute(
                "INSERT INTO deal_agreements (org_id, deal_id, template_id, sort_order, field_values, "
                "exhibit_document_id, status, created_by, created_at, system_from, system_to, history_of) "
                "SELECT org_id, deal_id, template_id, sort_order, field_values, exhibit_document_id, 'withdrawn', "
                "created_by, created_at, system_from, now(), id FROM deal_agreements WHERE id = $1",
                agreement_id)
            merged = {**(a["field_values"] or {}), **(changes.get("field_values") or {})}
            await conn.execute(
                "UPDATE deal_agreements SET field_values = $2::jsonb, sort_order = COALESCE($3, sort_order), "
                "exhibit_document_id = COALESCE($4, exhibit_document_id), system_from = now() WHERE id = $1",
                agreement_id, json.dumps(merged), changes.get("sort_order"), changes.get("exhibit_document_id"))
            await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.agreement_edit",
                          resource_id=agreement_id, payload=changes)
            a = await _agreement(conn, deal_id, agreement_id)
            blockers = R.publish_blockers(a, await R.agreement_values(conn, deal, a))
    return {"id": str(agreement_id), "field_values": merged, "blockers": blockers}


@router.post("/deal-room/deals/{deal_id}/agreements/{agreement_id}/publish")
async def publish_agreement(request: Request, deal_id: UUID, agreement_id: UUID):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            a = await _agreement(conn, deal_id, agreement_id)
            if a["status"] == "published":
                return {"id": str(agreement_id), "status": "published"}
            blockers = R.publish_blockers(a, await R.agreement_values(conn, deal, a))
            if blockers:
                raise HTTPException(status_code=422, detail={"message": R.blocked_message(blockers),
                                                             "missing": blockers})
            await conn.execute("UPDATE deal_agreements SET status = 'published', published_at = now(), "
                               "published_by = $2, system_from = now() WHERE id = $1",
                               agreement_id, UUID(str(principal.user_id)))
            await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.agreement_publish",
                          resource_id=agreement_id, payload={"template_key": a["template_key"]})
    return {"id": str(agreement_id), "status": "published"}


# ── Investor path ──────────────────────────────────────────────────────────
@router.post("/deal-room/deals/{deal_id}/access/request")
async def request_room_access(request: Request, deal_id: UUID, body: AccessRequest):
    require_permission(request, "indicate_interest")
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_viewer(request, conn, deal_id)
            try:
                caller = principal
                out = await R.request_access(pool, conn, principal=caller, deal=deal, entity_id=body.entity_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
        lab = await R.labels(conn, deal["org_id"])
    acc = out["access"]
    elig = out["eligibility"]
    if isinstance(elig, str):
        elig = json.loads(elig)
    if acc["status"] == "granted":
        await _notify(pool, deal, [principal.user_id], "deal_access_granted", "Deal room open",
                      f"Your access to {deal['name']} is open.")
    return {"status": acc["status"], "status_label": lab.get(f"deal_room_status_{acc['status']}"),
            "level_granted": acc["level_granted"], "eligible": bool((elig or {}).get("eligible")),
            "missing": (elig or {}).get("missing", [])}


@router.get("/deal-room/deals/{deal_id}/agreements/{agreement_id}/render")
async def render_agreement(request: Request, deal_id: UUID, agreement_id: UUID):
    require_permission(request, "indicate_interest")
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, deal, _ = await _require_deal_viewer(request, conn, deal_id)
        access = await R.live_access(conn, deal_id, principal.user_id)
        if access is None or access["status"] not in ("requested", "signed_awaiting", "granted"):
            raise HTTPException(status_code=409, detail="Request access and pass the eligibility check first.")
        a = next((x for x in await R.agreements_of(conn, deal_id, published_only=True)
                  if str(x["id"]) == str(agreement_id)), None)
        if a is None:
            raise HTTPException(status_code=404, detail="Agreement not found")
        try:
            text, sha = await R.render_for_signer(conn, deal, a, entity_id=access["entity_id"],
                                                  user_id=principal.user_id, on=date.today())
        except R.DealRoomError as exc:
            raise _http(exc) from exc
    return {"id": str(agreement_id), "title": a["title"], "template_status": a["template_status"],
            "text": text, "rendered_text_sha256": sha}


@router.post("/deal-room/deals/{deal_id}/agreements/{agreement_id}/sign", status_code=201)
async def sign_agreement(request: Request, deal_id: UUID, agreement_id: UUID, body: SignRequest):
    require_permission(request, "indicate_interest")
    pool = await get_pool()
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or \
        (request.client.host if request.client else None)
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_viewer(request, conn, deal_id)
            try:
                caller = principal
                out = await R.sign(conn, principal=caller, deal=deal, agreement_id=agreement_id,
                                   typed_name=body.typed_name, consent=body.consent,
                                   shown_sha256=body.rendered_text_sha256, ip=ip,
                                   user_agent=request.headers.get("user-agent"), today=date.today())
            except R.DealRoomError as exc:
                raise _http(exc) from exc
    access = out["access"] or {}
    if out["complete"]:
        if access.get("status") == "granted":
            await _notify(pool, deal, [principal.user_id], "deal_access_granted", "Deal room open",
                          f"Your access to {deal['name']} is open.")
    return {"signature_id": str(out["signature"]["id"]), "record_sha256": out["signature"]["record_sha256"],
            "signed_at": out["signature"]["signed_at"].isoformat(), "complete": out["complete"],
            "status": access.get("status"), "level_granted": access.get("level_granted")}


async def _notify(pool, deal, user_ids, event, title, body) -> None:
    try:
        from services.notifications import notification_bus

        await notification_bus.publish(pool, deal["org_id"], event, title, body, [str(u) for u in user_ids],
                                       resource_type="deal", resource_id=deal["id"])
    except Exception as exc:  # noqa: BLE001 — best effort, never blocks the decision
        print(f"[deal_room] notify failed ({event}): {exc}")


SIGNED_COPY_STYLE = (
    "body{background:#FAF9F6;color:#0F172A;font-family:'Hanken Grotesk',system-ui,sans-serif;font-size:17px;"
    "margin:0;padding:40px}main{background:#fff;border:1px solid #ece8dd;border-radius:6px;max-width:760px;"
    "margin:0 auto;padding:40px}h1{font-family:'Spectral',Georgia,serif;font-weight:500;color:#1B2B4B}"
    "pre{white-space:pre-wrap;font-family:inherit;line-height:1.55}.sig{border-top:1px solid #E2E8F0;"
    "margin-top:32px;padding-top:16px;color:#334155;font-size:15px}.draft{color:#9B2335;font-size:14px}"
    "code{font-size:13px;color:#64748B;word-break:break-all}")


@router.get("/deal-room/signatures/{signature_id}/copy", response_class=HTMLResponse)
async def signed_copy(request: Request, signature_id: UUID):
    """The signed copy: the exact rendered text the signer saw + the signature
    block (plain HTML — no PDF generator exists in the app, DEALROOM1 1e)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        _, sig = await _require_signature_reader(request, conn, signature_id)
        title = await conn.fetchval("SELECT title FROM agreement_templates WHERE id = $1", sig["template_id"])
        status = await conn.fetchval("SELECT status FROM agreement_templates WHERE id = $1", sig["template_id"])
        entity = await conn.fetchval("SELECT display_name FROM entities WHERE id = $1 AND valid_to IS NULL "
                                     "AND system_to IS NULL", sig["entity_id"])
    esc = html.escape
    draft = '<p class="draft">Template marked DRAFT — pending counsel review.</p>' if status == "draft" else ""
    page = (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title or 'Signed agreement')}</title>"
        f"<style>{SIGNED_COPY_STYLE}</style></head><body><main><h1>{esc(title or '')}</h1>{draft}"
        f"<pre>{esc(sig['rendered_text'])}</pre><div class='sig'>"
        f"<p>Signed electronically by <strong>{esc(sig['typed_name'])}</strong> for {esc(entity or '')} "
        f"on {esc(sig['signed_at'].isoformat())}, having agreed to sign electronically.</p>"
        f"<p>Template {esc(str(title or ''))} v{sig['template_version']} · signature {esc(str(sig['id']))}</p>"
        f"<p>Rendered text SHA-256 <code>{esc(sig['rendered_text_sha256'])}</code><br>"
        f"Record SHA-256 <code>{esc(sig['record_sha256'])}</code><br>"
        f"Chain {esc(sig['chain'])} #{sig['chain_seq']}, previous <code>{esc(sig['prev_signature_sha256'] or '—')}"
        f"</code></p></div></main></body></html>")
    return HTMLResponse(page)


@router.get("/deal-room/my-access")
async def my_access(request: Request):
    """The caller's own deals, what they signed and when (self-scoped)."""
    principal = require_active(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        lab = await R.labels(conn, principal.org_id)
        rows = await conn.fetch(
            """
            SELECT a.deal_id, d.name AS deal_name, a.status, a.level_granted, a.decided_at, a.valid_from,
                   e.display_name AS entity_name
            FROM deal_access a
            JOIN deals d ON d.id = a.deal_id AND d.valid_to IS NULL AND d.system_to IS NULL
            LEFT JOIN entities e ON e.id = a.entity_id AND e.valid_to IS NULL AND e.system_to IS NULL
            WHERE a.user_id = $1 AND a.valid_to IS NULL
            ORDER BY a.valid_from DESC
            """, UUID(str(principal.user_id)))
        sigs = await conn.fetch(
            """
            SELECT s.id, s.deal_id, s.signed_at, s.typed_name, t.title, t.version
            FROM deal_signatures s JOIN agreement_templates t ON t.id = s.template_id
            WHERE s.user_id = $1 ORDER BY s.signed_at
            """, UUID(str(principal.user_id)))
    by_deal: dict = {}
    for s in sigs:
        by_deal.setdefault(s["deal_id"], []).append({
            "signature_id": str(s["id"]), "title": s["title"], "version": s["version"],
            "signed_at": s["signed_at"].isoformat(), "copy_href": f"/api/deal-room/signatures/{s['id']}/copy"})
    return {"rows": [{
        "deal_id": str(r["deal_id"]), "deal_name": r["deal_name"], "status": r["status"],
        "status_label": lab.get(f"deal_room_status_{r['status']}"), "level_granted": r["level_granted"],
        "level_label": lab.get(f"deal_room_level_{r['level_granted']}") if r["level_granted"] else None,
        "entity_name": r["entity_name"], "since": r["valid_from"].isoformat(),
        "signatures": by_deal.get(r["deal_id"], [])} for r in rows]}


# ── The access grid ────────────────────────────────────────────────────────
GRID_SORTS = {"investor": "investor_name", "deal": "deal_name", "publisher": "publisher_name",
              "status": "status", "decided_at": "decided_at", "since": "since"}
GROUPS = ("deal", "publisher", "firm", "all")


@router.get("/deal-room/access-grid")
async def access_grid(
    request: Request,
    status: str | None = None,
    deal_id: UUID | None = None,
    publisher_entity_id: UUID | None = None,
    unsigned: bool = False,
    group_by: str = Query("deal"),
    sort: str = Query("since"),
    desc: bool = True,
    page: int = Query(0, ge=0),
    page_size: int = Query(50, ge=1, le=200),
):
    if group_by not in GROUPS:
        raise HTTPException(status_code=422, detail=f"group_by must be one of {', '.join(GROUPS)}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, scope, ids = await _require_grid_scope(request, conn)
        cond, params = ["a.valid_to IS NULL"], []
        if scope == "firm":
            params.append(UUID(str(principal.org_id)))
            cond.append(f"a.org_id = ${len(params)}")
        elif scope == "deals":
            params.append(ids)
            cond.append(f"a.deal_id = ANY(${len(params)}::uuid[])")
        if status:
            params.append(status)
            cond.append(f"a.status = ${len(params)}")
        if deal_id:
            params.append(deal_id)
            cond.append(f"a.deal_id = ${len(params)}")
        if publisher_entity_id:
            params.append(publisher_entity_id)
            cond.append(f"d.publisher_entity_id = ${len(params)}")
        if unsigned:
            cond.append("(SELECT count(*) FROM deal_signatures s WHERE s.deal_id = a.deal_id AND s.user_id = a.user_id"
                        " AND s.deal_agreement_id IN (SELECT id FROM deal_agreements x WHERE x.deal_id = a.deal_id"
                        " AND x.status = 'published' AND x.system_to IS NULL)) < (SELECT count(*) FROM deal_agreements"
                        " x WHERE x.deal_id = a.deal_id AND x.status = 'published' AND x.system_to IS NULL)")
        where = " AND ".join(cond)
        base = f"""
            FROM deal_access a
            JOIN deals d ON d.id = a.deal_id AND d.valid_to IS NULL AND d.system_to IS NULL
            JOIN users u ON u.id = a.user_id
            JOIN organizations o ON o.id = a.org_id
            LEFT JOIN entities e ON e.id = a.entity_id AND e.valid_to IS NULL AND e.system_to IS NULL
            LEFT JOIN entities pe ON pe.id = d.publisher_entity_id AND pe.valid_to IS NULL AND pe.system_to IS NULL
            LEFT JOIN compliance_records cr ON cr.entity_id = a.entity_id AND cr.valid_to IS NULL
              AND cr.system_to IS NULL
            LEFT JOIN users db ON db.id = a.decided_by
            WHERE {where}"""
        total = int(await conn.fetchval(f"SELECT count(*) {base}", *params))
        group_expr = {"deal": "d.name", "publisher": "COALESCE(pe.display_name, '—')", "firm": "o.name",
                      "all": "''"}[group_by]
        order = GRID_SORTS.get(sort, "since")
        params_page = params + [page_size, page * page_size]
        rows = await conn.fetch(
            f"""
            SELECT a.id, a.deal_id, a.user_id, a.entity_id, a.status, a.level_granted, a.decided_at, a.reason,
                   a.valid_from AS since, d.name AS deal_name, d.publisher_entity_id,
                   COALESCE(pe.display_name, '—') AS publisher_name, o.name AS firm_name,
                   COALESCE(u.full_name, u.email) AS investor_name, e.display_name AS entity_name,
                   cr.accreditation_status::text AS accreditation_status, cr.accreditation_verified_date,
                   cr.kyc_status::text AS kyc_status, cr.ofac_screen_status::text AS ofac_screen_status,
                   COALESCE(db.full_name, db.email) AS decided_by_name, {group_expr} AS group_label
            {base}
            ORDER BY group_label, {order} {'DESC' if desc else 'ASC'} NULLS LAST, a.id
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
            """, *params_page)
        deal_ids = list({r["deal_id"] for r in rows})
        agreements = await conn.fetch(
            """
            SELECT a.id, a.deal_id, a.sort_order, t.key, t.title FROM deal_agreements a
            JOIN agreement_templates t ON t.id = a.template_id
            WHERE a.deal_id = ANY($1::uuid[]) AND a.status = 'published' AND a.system_to IS NULL
            ORDER BY a.sort_order
            """, deal_ids) if deal_ids else []
        sigs = await conn.fetch(
            "SELECT deal_id, user_id, deal_agreement_id, signed_at FROM deal_signatures WHERE deal_id = ANY($1::uuid[])",
            deal_ids) if deal_ids else []
        lab = await R.labels(conn, principal.org_id)
    signed = {(s["deal_id"], s["user_id"], s["deal_agreement_id"]): s["signed_at"] for s in sigs}
    columns: list[dict] = []
    for a in agreements:
        if a["key"] not in {c["key"] for c in columns}:
            columns.append({"key": a["key"], "title": a["title"]})
    out_rows = []
    for r in rows:
        req = {a["key"]: a["id"] for a in agreements if a["deal_id"] == r["deal_id"]}
        out_rows.append({
            "id": str(r["id"]), "deal_id": str(r["deal_id"]), "deal_name": r["deal_name"],
            "publisher_entity_id": str(r["publisher_entity_id"]) if r["publisher_entity_id"] else None,
            "publisher_name": r["publisher_name"], "firm_name": r["firm_name"],
            "investor_user_id": str(r["user_id"]), "investor_name": r["investor_name"],
            "entity_id": str(r["entity_id"]) if r["entity_id"] else None, "entity_name": r["entity_name"],
            "eligibility": {"accreditation_status": r["accreditation_status"],
                            "verified_date": r["accreditation_verified_date"].isoformat()
                            if r["accreditation_verified_date"] else None,
                            "kyc_status": r["kyc_status"], "ofac_screen_status": r["ofac_screen_status"]},
            "agreements": {k: (signed[(r["deal_id"], r["user_id"], aid)].isoformat()
                               if (r["deal_id"], r["user_id"], aid) in signed else None) if aid else "n/a"
                           for k, aid in ((c["key"], req.get(c["key"])) for c in columns)},
            "level_granted": r["level_granted"],
            "level_label": lab.get(f"deal_room_level_{r['level_granted']}") if r["level_granted"] else None,
            "status": r["status"], "status_label": lab.get(f"deal_room_status_{r['status']}"),
            "decided_by": r["decided_by_name"], "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
            "reason": r["reason"], "group": r["group_label"]})
    return {
        "rows": out_rows, "total": total, "page": page, "page_size": page_size, "scope": scope,
        "group_by": group_by, "agreement_columns": columns,
        "permissions": {"can_read": True, "can_write": True, "is_super_admin": bool(principal.is_super_admin),
                        "read_permission": "deal publisher / delegate / manage_deals / manage_org_settings",
                        "write_permission": "deal publisher / delegate / manage_deals / manage_org_settings"},
        "vocabularies": {**R.vocabularies(lab), "group_by": list(GROUPS), "actions": ["approve", "decline", "revoke"],
                         "grant_levels": [v for v in R.vocabularies(lab)["levels"] if v["key"] in R.GRANT_LEVELS],
                         "editable": ["status"], "inline_editable": []},
    }


@router.post("/deal-room/access/decide")
async def decide_access(request: Request, body: Decision):
    """approve (choose level) / decline (reason) / revoke — single or bulk.
    Every row is re-checked against the caller's scope; every change is
    audited in its own transaction with the change."""
    pool = await get_pool()
    results = []
    async with pool.acquire() as conn:
        principal, scope, ids = await _require_grid_scope(request, conn)
        for access_id in body.access_ids:
            try:
                async with conn.transaction():
                    row = await conn.fetchrow("SELECT deal_id, org_id FROM deal_access WHERE id = $1 "
                                              "AND valid_to IS NULL", access_id)
                    if row is None:
                        raise R.DealRoomError(404, "Access request not found (it may have changed — reload).")
                    deal = await R.fetch_deal(conn, row["deal_id"])
                    if deal is None or not (await R.deal_rights(conn, principal, dict(deal))).can_manage:
                        raise R.DealRoomError(404, "Access request not found (it may have changed — reload).")
                    new = await R.decide(conn, actor=principal.user_id, access_id=access_id, action=body.action,
                                         level=body.level, reason=body.reason)
                results.append({"id": str(access_id), "ok": True, "new_id": str(new["id"]), "status": new["status"]})
                if new["status"] == "granted":
                    await _notify(pool, dict(deal), [new["user_id"]], "deal_access_granted", "Deal room open",
                                  f"Your access to {deal['name']} is open.")
            except R.DealRoomError as exc:
                results.append({"id": str(access_id), "ok": False, "status_code": exc.status, "detail": exc.detail})
    if all(not r["ok"] for r in results):
        first = results[0]
        raise HTTPException(status_code=first["status_code"], detail={"message": first["detail"], "results": results})
    return {"results": results}


@router.get("/deal-room/signatures/verify")
async def verify_signature_chain(request: Request, chain: str = Query("platform")):
    """Recompute the hash chain and report the first break (Super Admin)."""
    require_super_admin(request)
    if chain not in ("platform", "test"):
        raise HTTPException(status_code=422, detail="chain must be 'platform' or 'test'")
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await R.verify_chain(conn, chain)
