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
  _require_signature_reader  a signed copy (HTML or PDF): its signer, or a manager
                          of its deal
  _require_publisher_manager  a publisher's master NDA: Super Admin, manage_deals
                          or manage_org_settings in its org, or a user acting
                          for that publisher entity — else 403 (404 off-org)
  _library_target         the firm template library: manage_org_settings (org
                          admin) for the firm's own templates; platform
                          templates need Super Admin as well
  require_permission / require_any_permission / require_super_admin / require_active
                          as named on each route

Existing marketplace routes and their gates are untouched; publisher and
delegate editing lives here.

DEALROOM2 adds: agreement versions (keep / require re-signing), uploaded PDF
agreements with a page viewer, the firm template library, the master NDA, and
the signed copy as a PDF with a certificate page. org_id is only ever the caller's (get_org_id) or
the deal row's own — never a body or path value. Request models forbid extras.

DEALROOM3 adds (services/deal_room_control.py): watermarked renditions and the
view-only page viewer (documents/{id}/download | file | pages), access expiry
and pause (decide → extend; the scheduler sweep), the activity log and its
manager views / CSV, Q&A, investor advisors, the return-or-destroy notice, the
DRAFT 506(b) / 506(c) offering rules and the CRM relationship date. One more
scope gate:

  _require_deal_party     a deal viewer (above), or a user holding an access
                          row on the deal in their own org (a revoked / expired
                          investor acknowledging a return-or-destroy notice)
"""

from __future__ import annotations

import html
import json
import os
import uuid
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from routers.entities import get_org_id
from services import deal_room as R
from services import deal_room_control as C
from services import deal_room_library as L
from services import deal_room_pdf as P
from services import deal_room_templates as T
from services import deal_room_watermark as W
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
SETTINGS_EDITABLE = ("eligibility_rule", "approval_mode", "sponsor_display", "code_name", "downloads", "watermark",
                     "access_period", "access_until", "access_days", "expiry_notice_days", "notify_publisher_on",
                     "offering_type")


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
    downloads: str | None = None              # DEALROOM3
    watermark: str | None = None
    access_period: str | None = None
    access_until: date | None = None
    access_days: int | None = None
    expiry_notice_days: int | None = None
    notify_publisher_on: list[str] | None = None
    offering_type: str | None = None


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
    rendered_text_sha256: str | None = None   # a template agreement: the text's hash
    file_sha256: str | None = None            # an uploaded PDF: the file's hash


class NewVersion(_Body):
    field_values: dict | None = None
    exhibit_document_id: UUID | None = None
    resign_policy: str | None = None          # keep | resign (required once signed)


class MasterNdaSet(_Body):
    deal_agreement_id: UUID
    scope: str = "all"
    deal_ids: list[UUID] = []
    accepted_min_version: int | None = None


class LibraryCreate(_Body):
    scope: str = "org"                        # org | platform (Super Admin)
    source_template_id: UUID | None = None    # copy this one …
    key: str | None = None
    title: str | None = None                  # … or write a new one
    body: str | None = None
    fields: list[dict] | None = None


class LibraryEdit(_Body):
    title: str | None = None
    body: str | None = None
    fields: list[dict] | None = None


class Decision(_Body):
    access_ids: list[UUID] = Field(min_length=1, max_length=200)
    action: str
    level: str | None = None
    reason: str | None = None
    ends_on: date | None = None               # DEALROOM3: extend
    send_return_notice: bool = False          # DEALROOM3: revoke


class QuestionCreate(_Body):
    body: str = Field(min_length=3, max_length=4000)
    folder_id: UUID | None = None


class AnswerBody(_Body):
    body: str = Field(min_length=2, max_length=8000)


class ReasonBody(_Body):
    reason: str = Field(min_length=1, max_length=1000)


class MergeBody(_Body):
    into_id: UUID
    reason: str = Field(min_length=1, max_length=1000)


class AdvisorInvite(_Body):
    name: str = Field(min_length=2, max_length=200)
    firm: str | None = Field(default=None, max_length=200)
    role: str
    email: str = Field(min_length=3, max_length=320)


class AdvisorAck(_Body):
    typed_name: str
    consent: bool
    text_sha256: str


class NoticeAck(_Body):
    typed_name: str


class ShareUnwatermarked(_Body):
    share: bool


class RelationshipDate(_Body):
    established_on: date
    basis: str | None = Field(default=None, max_length=500)


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


async def _require_deal_party(request: Request, conn, deal_id):
    """A deal viewer, or (DEALROOM3) a user with an access row on the deal in
    their own org — a revoked / expired investor reading or acknowledging
    their return-or-destroy notice after the deal left the marketplace."""
    principal = require_active(request)
    deal = await _deal_or_404(conn, principal, deal_id)
    rights = await R.deal_rights(conn, principal, deal)
    if rights.can_manage or rights.can_view:
        return principal, deal, rights
    if str(deal["org_id"]) == str(principal.org_id) and await conn.fetchval(
            "SELECT 1 FROM deal_access WHERE deal_id = $1 AND user_id = $2 LIMIT 1", deal["id"],
            UUID(str(principal.user_id))):
        return principal, deal, rights
    raise HTTPException(status_code=404, detail="Deal not found")


def _refuse_advisor(rights) -> None:
    if rights.role == "advisor":
        raise HTTPException(status_code=403, detail=C.ADVISOR_REFUSED)


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


async def _require_publisher_manager(request: Request, conn, entity_id):
    """A publisher entity's own settings (its master NDA): Super Admin first;
    manage_deals / manage_org_settings in its org; a user acting for it."""
    principal = require_active(request)
    org = await conn.fetchval("SELECT org_id FROM entity_publishers WHERE entity_id = $1 AND revoked_at IS NULL",
                              UUID(str(entity_id)))
    if org is None or (str(org) != str(principal.org_id) and not principal.is_super_admin):
        raise HTTPException(status_code=404, detail="Publisher not found")
    if principal.is_super_admin or principal.has("manage_deals") or principal.has("manage_org_settings"):
        return principal, org
    if UUID(str(entity_id)) in await R.publisher_entities_for_user(conn, org, principal.user_id):
        return principal, org
    raise HTTPException(status_code=403, detail="Only this publisher's users or staff can set its master NDA.")


async def _library_target(request: Request, conn, template_id):
    """One library template: the firm's own (manage_org_settings in that firm)
    or a platform template (Super Admin only). Another firm's is a 404."""
    principal = require_permission(request, "manage_org_settings")
    t = await L.get(conn, template_id)
    if t is None or (t["org_id"] is not None and str(t["org_id"]) != str(principal.org_id)
                     and not principal.is_super_admin):
        raise HTTPException(status_code=404, detail="Template not found")
    if t["org_id"] is None:
        require_super_admin(request)
    return principal, t


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
        "can_manage_library": bool(principal.is_super_admin or principal.has("manage_org_settings")),
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
        f"SELECT {R.DOC_COLS}, share_unwatermarked FROM deal_documents WHERE deal_id = $1 AND folder_id IS NOT NULL "
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
    # DEALROOM3: an advisor reads AS the investor they advise, and only after
    # acknowledging; every document carries what the caller may do with it.
    advisor_pending = rights.role == "advisor" and rights.advisor_status != "active"
    if rights.role == "advisor":
        inv_access = await R.live_access(conn, deal["id"], rights.advisor_of)
        status = inv_access["status"] if inv_access else None
        if advisor_pending:
            open_ids = set()
    out_folders = []
    for f in folders:
        eff = R.effective_level(f, by_id)
        is_open = manage or f["id"] in open_ids
        reason = None
        if not is_open:
            key = {"revoked": "revoked", "declined": "declined", "not_eligible": "not_eligible",
                   "resign_required": "resign_required", "expired": "expired", "paused": "paused"}.get(status) \
                or (eff if status in ("requested", "signed_awaiting", "granted") else "view")
            reason = lab.get(f"deal_room_lock_{key}")
            if advisor_pending:
                reason = "Sign the advisor acknowledgement to open the investor's folders."
        entry = {
            "id": str(f["id"]), "key": f["key"], "label": R.folder_label(f, lab), "sort_order": f["sort_order"],
            "access_level": f["access_level"], "effective_level": eff,
            "access_level_label": lab.get(f"deal_room_level_{eff}"),
            "parent_id": str(f["parent_id"]) if f["parent_id"] else None, "is_standard": f["is_standard"],
            "open": bool(is_open), "lock_reason": reason,
        }
        if is_open:
            entry["documents"] = []
            for d in docs:
                if d["folder_id"] != f["id"]:
                    continue
                pol = C.document_policy(settings, dict(d), manage=manage)
                entry["documents"].append({
                    "id": str(d["id"]), "file_name": d["file_name"], "file_type": d["file_type"],
                    "file_size_bytes": d["file_size_bytes"], "status": d["status"],
                    "created_at": d["created_at"].isoformat(),
                    "can_download": pol["download"] is not None, "can_view": pol["pages"],
                    "watermarked": pol["watermark"], "blocked_reason": pol["reason"],
                    **({"share_unwatermarked": bool(d["share_unwatermarked"]),
                        "watermarkable": pol["kind"] is not None} if manage else {})})
            if f["key"] == "07_qa":
                entry["answered_questions"] = [
                    {"id": q["id"], "body": q["body"], "answer": q["answer"], "answered_at": q["answered_at"]}
                    for q in await C.questions_for(conn, deal, manage=manage, user_id=principal.user_id, lab=lab)
                    if q["status"] == "answered"]
        if manage:
            entry["named"] = named.get(f["id"], [])
        out_folders.append(entry)

    agreements = await R.required_agreements(conn, deal, published_only=not manage)
    state = await R.signature_state(conn, deal, agreements, principal.user_id,
                                    access["entity_id"] if access else None,
                                    access["resign_after"] if access else None)
    nsigs = {}
    if manage and agreements:
        nsigs = {r["deal_agreement_id"]: int(r["n"]) for r in await conn.fetch(
            "SELECT deal_agreement_id, count(*) AS n FROM deal_signatures WHERE deal_agreement_id = ANY($1::uuid[]) "
            "GROUP BY deal_agreement_id", [a["id"] for a in agreements])}
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
            "access_ends_on": access["access_ends_on"].isoformat() if access["access_ends_on"] else None,
            "paused_reason": access["paused_reason"],
        },
        "agreements": [
            {"id": str(a["id"]), "title": a["title"], "kind": a["kind"], "template_key": a["template_key"],
             "template_version": a["template_version"], "template_status": a["template_status"],
             "sort_order": a["sort_order"], "status": a["status"], "current_version": a["current_version"],
             "is_master": bool(a.get("is_master")), "virtual": bool(a.get("virtual")),
             "signed_at": state[a["id"]]["signed_at"].isoformat() if state[a["id"]]["ok"] else None,
             "signature_id": str(state[a["id"]]["signature_id"]) if state[a["id"]]["ok"] else None,
             "signed_version": state[a["id"]]["version"],
             "covered_by_master": state[a["id"]]["covered"], "stale": state[a["id"]]["stale"],
             **({"field_values": a["field_values"], "exhibit_document_id": str(a["exhibit_document_id"])
                 if a["exhibit_document_id"] else None,
                 "fields": [f for f in a["fields"] if f["source"] in (T.SOURCE_PUBLISHER, T.SOURCE_DEAL)],
                 "file_sha256": a["file_sha256"], "signatures": nsigs.get(a["id"], 0),
                 "blockers": R.publish_blockers(a, await R.agreement_values(conn, deal, a))} if manage else {})}
            for a in agreements],
        "vocabularies": {**R.vocabularies(lab),
                         "editable": ["deal", "settings", "folders", "documents", "agreements", "delegates",
                                      "versions", "master_nda", "share_unwatermarked", "questions", "advisors",
                                      "activity"]
                         if manage else []},
    }
    envelope["room_rules"] = {"downloads": settings["downloads"], "watermark": settings["watermark"],
                              "offering_type": settings["offering_type"]}
    envelope["notices"] = {
        "activity": C.ACTIVITY_NOTICE if not manage else None,
        "view_only": C.VIEW_ONLY_NOTICE if settings["downloads"] == "view_only" else None,
        "watermark": C.WATERMARK_NOTICE if settings["watermark"] == "on" and not manage else None,
        "offering_draft": C.OFFERING_DRAFT_NOTE if settings["offering_type"] in ("rule_506b", "rule_506c") else None,
    }
    if rights.role == "advisor":
        adv = await R.advisor_row(conn, deal["id"], principal.user_id)
        envelope["advisor"] = {
            "status": rights.advisor_status, "needs_ack": advisor_pending,
            "investor_name": await conn.fetchval("SELECT COALESCE(full_name, email) FROM users WHERE id = $1",
                                                 rights.advisor_of),
            "role": adv["role"] if adv else None,
            "role_label": lab.get(f"deal_room_advisor_role_{adv['role']}") if adv else None,
            "investor_status": status, "investor_status_label": lab.get(f"deal_room_status_{status}") if status else None}
        envelope["agreements"] = []
    elif not manage and access is not None:
        envelope["advisors"] = await C.advisors_of(conn, deal["id"], principal.user_id, lab)
        notice = await C.live_notice(conn, deal["id"], principal.user_id)
        envelope["return_notice"] = None if notice is None else {
            "text": notice["notice_text"], "sent_at": notice["sent_at"].isoformat(),
            "acknowledged_at": notice["acknowledged_at"].isoformat() if notice["acknowledged_at"] else None}
    if not manage:
        reader = await C.reader_context(conn, deal, principal, rights)
        await C.log_activity(conn, deal, reader, "room_open")
    if manage:
        envelope["settings"] = _jsonable(settings)
        master = await R.live_master(conn, deal.get("publisher_entity_id"))
        envelope["publisher_entity_id"] = str(deal["publisher_entity_id"]) if deal.get("publisher_entity_id") else None
        envelope["master_nda"] = None if master is None else {
            **_jsonable({k: master[k] for k in ("id", "deal_agreement_id", "scope", "accepted_min_version")}),
            "deal_ids": [str(d) for d in master["deal_ids"]],
            "covers_this_deal": (await R.master_for_deal(conn, deal)) is not None}
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
            # DEALROOM3: everyone the folder is open to right now (investors and
            # active advisors) — and nobody else — hears of the new document.
            readers = await C.folder_readers(conn, deal_id, folder_id)
            lab = await R.labels(conn, deal["org_id"])
    outbox = C.Outbox()
    outbox.add(deal, readers, "deal_room_new_document", "New document in the data room",
               f"{file.filename} was added to {R.folder_label(folder, lab)} in {deal['name']}.",
               {"document_id": str(row["id"]), "folder_id": str(folder_id)})
    await outbox.publish(pool)
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


async def _room_document(request: Request, conn, deal_id, doc_id):
    """ONE gate for a data-room document's content (DEALROOM3). Managers: any
    live document of the deal. Anyone else: the document's folder must be open
    to them (member_doc_sql → deal_room_folder_open + the offering rule), and an
    advisor must have acknowledged. Every refusal is the SAME 404. Returns
    (principal, deal, rights, doc, settings, policy)."""
    principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
    cols = "id, deal_id, folder_id, file_name, file_type, r2_key, r2_bucket, share_unwatermarked"
    if rights.can_manage:
        doc = await conn.fetchrow(f"SELECT {cols} FROM deal_documents WHERE id = $1 AND deal_id = $2 "
                                  "AND deleted_at IS NULL", doc_id, deal_id)
    else:
        doc = await conn.fetchrow(
            f"SELECT {cols} FROM deal_documents dd WHERE id = $1 AND deal_id = $2 "
            f"AND folder_id IS NOT NULL AND {R.member_doc_sql('dd', '$3::uuid')}",
            doc_id, deal_id, UUID(str(principal.user_id)))
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if rights.role == "advisor" and rights.advisor_status != "active":
        raise HTTPException(status_code=404, detail="Document not found")   # nothing before the acknowledgement
    settings = await R.room_settings(conn, deal_id)
    pol = C.document_policy(settings, dict(doc), manage=rights.can_manage)
    return principal, deal, rights, dict(doc), settings, pol


@router.get("/deal-room/deals/{deal_id}/documents/{doc_id}/download")
async def download_room_document(request: Request, deal_id: UUID, doc_id: UUID):
    """What a download hands out, by the room's settings (services.deal_room_control.document_policy):
    managers — a short-lived signed URL to the original; watermark off and downloads allowed — today's
    signed URL; watermark on — NO URL: the API's own rendition route, which streams a freshly
    watermarked copy; view-only — the same 404 as any refusal (no download route answers); a type
    that cannot be watermarked — 409 with the reason, unless the manager shared it unwatermarked."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, deal, rights, doc, settings, pol = await _room_document(request, conn, deal_id, doc_id)
        if pol["download"] is None:
            if settings["downloads"] == "view_only" and not rights.can_manage:
                raise HTTPException(status_code=404, detail="Document not found")
            raise HTTPException(status_code=409, detail=pol["reason"] or "This document cannot be downloaded.")
        if pol["download"] == "watermarked":
            return {"href": f"/api/deal-room/deals/{deal_id}/documents/{doc_id}/file", "watermarked": True,
                    "file_name": doc["file_name"]}
        if not rights.can_manage:
            reader = await C.reader_context(conn, deal, principal, rights)
            await C.log_activity(conn, deal, reader, "download", folder_id=doc["folder_id"], document_id=doc["id"],
                                 detail={"mode": "original"})
    expires = 300
    url = await run_in_threadpool(storage.get_signed_url, doc["r2_key"], expires, doc["r2_bucket"])
    return {"url": url, "expires_in": expires, "file_name": doc["file_name"], "watermarked": False}


@router.get("/deal-room/deals/{deal_id}/documents/{doc_id}/file")
async def watermarked_room_document(request: Request, deal_id: UUID, doc_id: UUID):
    """The watermarked rendition, generated on request and streamed (never a
    URL to the original): every page stamped with the reader's name, investing
    entity, the deal, the date and time and a watermark id recorded in
    deal_room_watermarks. Only when the document's policy is 'watermarked'."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, deal, rights, doc, settings, pol = await _room_document(request, conn, deal_id, doc_id)
        if pol["download"] != "watermarked":
            raise HTTPException(status_code=404, detail="Document not found")
        try:
            _, data = await R.read_document_bytes(conn, doc["id"])
            kind = W.kind_of(None, None, data[:16])
            if kind is None:
                raise R.DealRoomError(409, W.NOT_WATERMARKABLE)
            source = await run_in_threadpool(W.as_pdf, data, kind)
            if len(source) > W.MAX_BYTES or await run_in_threadpool(W.page_count, source) > W.MAX_PAGES:
                raise R.DealRoomError(413, W.TOO_LARGE)
        except R.DealRoomError as exc:
            raise _http(exc) from exc
        reader = await C.reader_context(conn, deal, principal, rights)
        at = datetime.now(timezone.utc)
        wid, lines = await C.record_watermark(conn, deal, doc, reader, kind="download", page=None,
                                              source_sha256=R.sha256_bytes(data), at=at)
        pdf = await run_in_threadpool(W.watermark_pdf, source, lines)
        await C.log_activity(conn, deal, reader, "download", folder_id=doc["folder_id"], document_id=doc["id"],
                             watermark_id=wid, detail={"mode": "watermarked"})
    stem = (doc["file_name"] or "document").rsplit(".", 1)[0]
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{stem}-watermarked.pdf"', "Cache-Control": "no-store",
        "X-Watermark-Id": str(wid)})


@router.get("/deal-room/deals/{deal_id}/documents/{doc_id}/pages")
async def room_document_pages(request: Request, deal_id: UUID, doc_id: UUID):
    """The viewer's page list: how many pages, whether they are watermarked."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        _, _, _, doc, _, pol = await _room_document(request, conn, deal_id, doc_id)
        if not pol["pages"]:
            raise HTTPException(status_code=409, detail=pol["reason"] or W.NOT_VIEWABLE)
        try:
            _, data = await R.read_document_bytes(conn, doc["id"])
        except R.DealRoomError as exc:
            raise _http(exc) from exc
    kind = W.kind_of(None, None, data[:16])
    if kind is None:
        raise HTTPException(status_code=409, detail=W.NOT_VIEWABLE)
    n = await run_in_threadpool(W.page_count, await run_in_threadpool(W.as_pdf, data, kind))
    return {"document_id": str(doc_id), "file_name": doc["file_name"], "page_count": n,
            "watermarked": pol["watermark"], "download": pol["download"] is not None,
            "pages": [f"/api/deal-room/deals/{deal_id}/documents/{doc_id}/pages/{i}" for i in range(1, n + 1)]}


@router.get("/deal-room/deals/{deal_id}/documents/{doc_id}/pages/{page}")
async def room_document_page(request: Request, deal_id: UUID, doc_id: UUID, page: int):
    """One page as PNG (the view-only viewer; the original never leaves the
    server). Watermarked when the room's watermark is on; every view logged."""
    if page < 1:
        raise HTTPException(status_code=422, detail="pages start at 1")
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, deal, rights, doc, settings, pol = await _room_document(request, conn, deal_id, doc_id)
        if not pol["pages"]:
            raise HTTPException(status_code=409, detail=pol["reason"] or W.NOT_VIEWABLE)
        try:
            _, data = await R.read_document_bytes(conn, doc["id"])
        except R.DealRoomError as exc:
            raise _http(exc) from exc
        kind = W.kind_of(None, None, data[:16])
        if kind is None:
            raise HTTPException(status_code=409, detail=W.NOT_VIEWABLE)
        source = await run_in_threadpool(W.as_pdf, data, kind)
        try:
            one = await run_in_threadpool(W.single_page, source, page - 1)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="No such page") from exc
        wid = None
        if not rights.can_manage:
            reader = await C.reader_context(conn, deal, principal, rights)
            if pol["watermark"]:
                wid, lines = await C.record_watermark(conn, deal, doc, reader, kind="page", page=page,
                                                      source_sha256=R.sha256_bytes(data), at=datetime.now(timezone.utc))
                one = await run_in_threadpool(W.watermark_pdf, one, lines)
            await C.log_activity(conn, deal, reader, "view_page", folder_id=doc["folder_id"], document_id=doc["id"],
                                 page=page, watermark_id=wid)
    png = await run_in_threadpool(W.page_png, one, 0)
    headers = {"Cache-Control": "no-store"}
    if wid:
        headers["X-Watermark-Id"] = str(wid)
    return Response(content=png, media_type="image/png", headers=headers)


@router.put("/deal-room/deals/{deal_id}/documents/{doc_id}/share-unwatermarked")
async def share_unwatermarked(request: Request, deal_id: UUID, doc_id: UUID, body: ShareUnwatermarked):
    """A manager lets investors have a file that cannot be watermarked as it is (recorded + audited)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            row = await conn.fetchrow(
                "UPDATE deal_documents SET share_unwatermarked = $3, share_unwatermarked_by = CASE WHEN $3 THEN $4 END, "
                "share_unwatermarked_at = CASE WHEN $3 THEN now() END, updated_at = now() WHERE id = $1 AND deal_id = $2 "
                "AND deleted_at IS NULL RETURNING id, share_unwatermarked, share_unwatermarked_at",
                doc_id, deal_id, body.share, UUID(str(principal.user_id)))
            if row is None:
                raise HTTPException(status_code=404, detail="Document not found")
            await conn.execute(
                "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
                "VALUES ($1, $2, 'deal_room.share_unwatermarked', 'deal_documents', $3, $4::jsonb)",
                deal["org_id"], UUID(str(principal.user_id)), doc_id, json.dumps({"share": body.share}))
    return _jsonable(dict(row))


@router.post("/deal-room/deals/{deal_id}/folders/{folder_id}/open")
async def open_room_folder(request: Request, deal_id: UUID, folder_id: UUID):
    """An investor (or advisor) opens a folder: logged; the publisher is told
    of the first open when the deal's settings ask for it. A closed folder is
    a 404 and logs nothing."""
    pool = await get_pool()
    outbox = C.Outbox()
    async with pool.acquire() as conn:
        principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
        f = await _folder(conn, deal_id, folder_id)
        if rights.can_manage:
            return {"folder_id": str(folder_id), "logged": False}
        if rights.role == "advisor" and rights.advisor_status != "active":
            raise HTTPException(status_code=404, detail="Folder not found")
        if not await conn.fetchval("SELECT deal_room_folder_open($1, $2)", folder_id, UUID(str(principal.user_id))):
            raise HTTPException(status_code=404, detail="Folder not found")
        reader = await C.reader_context(conn, deal, principal, rights)
        await C.log_activity(conn, deal, reader, "folder_open", folder_id=folder_id)
        if await C.first_folder_open(conn, deal_id, principal.user_id, folder_id):
            lab = await R.labels(conn, deal["org_id"])
            await C.publisher_notify(conn, outbox, deal, "first_folder_open", "deal_room_first_folder_open",
                                     "Folder opened", f"An investor opened {R.folder_label(f, lab)} in "
                                                      f"{deal['name']} for the first time.")
    await outbox.publish(pool)
    return {"folder_id": str(folder_id), "logged": True}


# ── Agreements ─────────────────────────────────────────────────────────────
@router.get("/deal-room/templates")
async def list_templates(request: Request):
    """Templates a deal may require: platform (not retired) for everyone; the
    firm's ACTIVE library templates only for callers who publish or manage
    deals in the firm (DEALROOM2 — a member never sees the firm library)."""
    principal = require_active(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        firm = bool(principal.is_super_admin or principal.has("manage_deals") or principal.has("manage_org_settings")
                    or await R.publisher_entities_for_user(conn, principal.org_id, principal.user_id)
                    or await R.managed_deal_ids(conn, principal.org_id, principal.user_id))
        rows = await L.usable(conn, principal.org_id, include_firm=firm)
    return {"templates": [_jsonable({k: r[k] for k in ("id", "key", "version", "title", "scope", "status", "fields",
                                                       "source_path")}) for r in rows]}


@router.post("/deal-room/deals/{deal_id}/agreements", status_code=201)
async def add_agreement(request: Request, deal_id: UUID, body: AgreementCreate):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            if await L.usable_for_deal(conn, body.template_id, deal["org_id"]) is None:
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
                    "A published agreement is fixed: save a new version (POST …/agreements/{id}/versions) and "
                    "choose whether existing signers keep access or must re-sign."))
            if a["kind"] == "upload" and changes.get("field_values"):
                raise HTTPException(status_code=422, detail="An uploaded agreement has no fill-in fields.")
            # system-axis history copy, then the live row (id kept for signatures)
            await R.archive_agreement(conn, agreement_id)
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
            try:  # version 1 — the immutable snapshot investors sign
                await R.ensure_version(conn, await R.agreement_by_id(conn, agreement_id), actor=principal.user_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
            await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.agreement_publish",
                          resource_id=agreement_id, payload={"template_key": a["template_key"]})
    return {"id": str(agreement_id), "status": "published"}


# ── Investor path ──────────────────────────────────────────────────────────
@router.post("/deal-room/deals/{deal_id}/access/request")
async def request_room_access(request: Request, deal_id: UUID, body: AccessRequest):
    require_permission(request, "indicate_interest")
    pool = await get_pool()
    outbox = C.Outbox()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
            _refuse_advisor(rights)
            before = await R.live_access(conn, deal["id"], principal.user_id)
            try:
                caller = principal
                out = await R.request_access(pool, conn, principal=caller, deal=deal, entity_id=body.entity_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
            if before is None or before["status"] in ("viewing", "not_eligible"):
                await C.publisher_notify(conn, outbox, deal, "request", "deal_room_access_requested",
                                         "New access request", f"An investor requested access to {deal['name']}.")
        lab = await R.labels(conn, deal["org_id"])
    await outbox.publish(pool)
    acc = out["access"]
    elig = out["eligibility"]
    if isinstance(elig, str):
        elig = json.loads(elig)
    if acc["status"] == "granted":
        await _notify(pool, deal, [principal.user_id], "deal_access_granted", "Deal room open",
                      f"Your access to {deal['name']} is open.", channels=C.CHANNELS)
    return {"status": acc["status"], "status_label": lab.get(f"deal_room_status_{acc['status']}"),
            "level_granted": acc["level_granted"], "eligible": bool((elig or {}).get("eligible")),
            "missing": (elig or {}).get("missing", [])}


@router.get("/deal-room/deals/{deal_id}/agreements/{agreement_id}/render")
async def render_agreement(request: Request, deal_id: UUID, agreement_id: UUID):
    require_permission(request, "indicate_interest")
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, deal, _ = await _require_deal_viewer(request, conn, deal_id)
        access, a = await _investor_agreement(conn, deal, principal, agreement_id)
        try:
            v = await R.get_version(conn, a["version_id"]) if a.get("version_id") else None
            exhibit = await _exhibit_info(conn, v)
            if a["kind"] == "upload":
                if v is None:
                    raise R.DealRoomError(409, "This agreement has no published version yet.")
                return {"id": str(agreement_id), "kind": "upload", "title": a["title"], "version": v["version"],
                        "file_name": v["file_name"], "file_sha256": v["file_sha256"], "page_count": v["page_count"],
                        "fill_in_fields": False, "exhibit": exhibit}
            text, sha = await R.render_for_signer(conn, deal, a, entity_id=access["entity_id"],
                                                  user_id=principal.user_id, on=date.today())
        except R.DealRoomError as exc:
            raise _http(exc) from exc
    return {"id": str(agreement_id), "kind": "template", "title": a["title"],
            "template_status": (v or a)["template_status"], "version": v["version"] if v else None,
            "text": text, "rendered_text_sha256": sha, "exhibit": exhibit}


async def _investor_agreement(conn, deal, principal, agreement_id):
    """An agreement this investor may read: one of the deal's required
    (published) agreements, once they have requested access."""
    access = await R.live_access(conn, deal["id"], principal.user_id)
    if access is None or access["status"] not in ("requested", "signed_awaiting", "granted", "resign_required"):
        raise HTTPException(status_code=409, detail="Request access and pass the eligibility check first.")
    a = next((x for x in await R.required_agreements(conn, deal) if str(x["id"]) == str(agreement_id)), None)
    if a is None:
        raise HTTPException(status_code=404, detail="Agreement not found")
    return access, a


async def _exhibit_info(conn, v) -> dict | None:
    """Exhibit A (e.g. the joinder's Underlying NDA PDF) — shown before signing."""
    if not v or not v.get("exhibit_document_id"):
        return None
    _, data = await R.read_document_bytes(conn, v["exhibit_document_id"])
    if v.get("exhibit_sha256") and R.sha256_bytes(data) != v["exhibit_sha256"]:
        raise R.DealRoomError(409, "Exhibit A no longer matches the hash recorded on this version.")
    return {"document_id": str(v["exhibit_document_id"]), "sha256": R.sha256_bytes(data),
            "page_count": R.pdf_page_count(data)}


@router.post("/deal-room/deals/{deal_id}/agreements/{agreement_id}/sign", status_code=201)
async def sign_agreement(request: Request, deal_id: UUID, agreement_id: UUID, body: SignRequest):
    require_permission(request, "indicate_interest")
    pool = await get_pool()
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or \
        (request.client.host if request.client else None)
    outbox = C.Outbox()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
            _refuse_advisor(rights)
            try:
                caller = principal
                out = await R.sign(conn, principal=caller, deal=deal, agreement_id=agreement_id,
                                   typed_name=body.typed_name, consent=body.consent,
                                   shown_sha256=body.file_sha256 or body.rendered_text_sha256, ip=ip,
                                   user_agent=request.headers.get("user-agent"), today=date.today())
            except R.DealRoomError as exc:
                raise _http(exc) from exc
            await C.publisher_notify(conn, outbox, deal, "signature", "deal_room_agreement_signed",
                                     "Agreement signed", f"An investor signed an agreement for {deal['name']}.",
                                     {"complete": out["complete"]})
    await outbox.publish(pool)
    access = out["access"] or {}
    if out["complete"]:
        if access.get("status") == "granted":
            await _notify(pool, deal, [principal.user_id], "deal_access_granted", "Deal room open",
                          f"Your access to {deal['name']} is open.", channels=C.CHANNELS)
    return {"signature_id": str(out["signature"]["id"]), "record_sha256": out["signature"]["record_sha256"],
            "signed_at": out["signature"]["signed_at"].isoformat(), "complete": out["complete"],
            "version": out["version"], "document_sha256": out["signature"]["document_sha256"],
            "status": access.get("status"), "level_granted": access.get("level_granted")}


async def _notify(pool, deal, user_ids, event, title, body, channels=None) -> None:
    try:
        from services.notifications import notification_bus

        await notification_bus.publish(pool, deal["org_id"], event, title, body, [str(u) for u in user_ids],
                                       resource_type="deal", resource_id=deal["id"], channels=channels)
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
        ctx = await _signature_context(conn, sig)
        title, status, entity = ctx["title"], ctx["template_status"], ctx["entity_name"]
    esc = html.escape
    draft = '<p class="draft">Template marked DRAFT — pending counsel review.</p>' if status == "draft" else ""
    page = (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title or 'Signed agreement')}</title>"
        f"<style>{SIGNED_COPY_STYLE}</style></head><body><main><h1>{esc(title or '')}</h1>{draft}"
        f"<pre>{esc(sig['rendered_text'])}</pre><div class='sig'>"
        f"<p>Signed electronically by <strong>{esc(sig['typed_name'])}</strong> for {esc(entity or '')} "
        f"on {esc(sig['signed_at'].isoformat())}, having agreed to sign electronically.</p>"
        f"<p>{esc(str(title or ''))} — version {esc(str(ctx['version'] or sig['template_version'] or ''))} · "
        f"signature {esc(str(sig['id']))}</p>"
        f"<p>Document SHA-256 <code>{esc(sig.get('document_sha256') or sig['rendered_text_sha256'])}</code></p>"
        f"<p>Rendered text SHA-256 <code>{esc(sig['rendered_text_sha256'])}</code><br>"
        f"Record SHA-256 <code>{esc(sig['record_sha256'])}</code><br>"
        f"Chain {esc(sig['chain'])} #{sig['chain_seq']}, previous <code>{esc(sig['prev_signature_sha256'] or '—')}"
        f"</code></p></div></main></body></html>")
    return HTMLResponse(page)


async def _signature_context(conn, sig) -> dict:
    """What a signed copy names besides the row itself: the agreement's title
    and version (from the immutable version, or the template for a Phase 1
    row), the signer, the investing entity, the deal."""
    v = await R.get_version(conn, sig["agreement_version_id"]) if sig.get("agreement_version_id") else None
    t = await conn.fetchrow("SELECT key, title, status FROM agreement_templates WHERE id = $1",
                            sig["template_id"]) if sig.get("template_id") else None
    signer = await conn.fetchrow("SELECT full_name, email FROM users WHERE id = $1", sig["user_id"])
    entity = await conn.fetchval("SELECT COALESCE(legal_name, display_name) FROM entities WHERE id = $1 "
                                 "AND valid_to IS NULL AND system_to IS NULL", sig["entity_id"])
    deal_name = await conn.fetchval("SELECT name FROM deals WHERE id = $1 AND valid_to IS NULL AND system_to IS NULL",
                                    sig["deal_id"])
    return {
        "title": (v["title"] if v else None) or (t["title"] if t else None),
        "template_status": v["template_status"] if v else (t["status"] if t else None),
        "template_key": (v["template_key"] if v else None) or (t["key"] if t else None),
        "version": v["version"] if v else None, "kind": v["kind"] if v else "template",
        "version_row": v, "exhibit_sha256": v["exhibit_sha256"] if v else None,
        "signer_name": signer["full_name"] if signer else None, "signer_email": signer["email"] if signer else None,
        "entity_name": entity, "deal_name": deal_name,
        "verify_ref": (f"GET /api/v1/deal-room/signatures/verify?chain={sig['chain']} (Super Admin) recomputes the "
                       f"{sig['chain']} chain; position #{sig['chain_seq']} must carry record SHA-256 "
                       f"{sig['record_sha256']}. Signature id {sig['id']}."),
    }


@router.get("/deal-room/signatures/{signature_id}/pdf")
async def signed_copy_pdf(request: Request, signature_id: UUID):
    """The signed copy as a PDF — the agreement (rendered text, or the uploaded
    PDF itself), Exhibit A when there is one, then a certificate page. Built on
    demand from the signature row (never stored); same gate as the HTML copy."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        _, sig = await _require_signature_reader(request, conn, signature_id)
        ctx = await _signature_context(conn, sig)
        v = ctx.pop("version_row")
        doc_pdf = exhibit_pdf = None
        try:
            if v is not None and v["kind"] == "upload":
                _, doc_pdf = await R.read_document_bytes(conn, v["document_id"])
                if R.sha256_bytes(doc_pdf) != sig["document_sha256"]:
                    raise R.DealRoomError(409, "The stored file no longer matches the signed document's hash.")
            if v is not None and v.get("exhibit_document_id"):
                _, exhibit_pdf = await R.read_document_bytes(conn, v["exhibit_document_id"])
                if v.get("exhibit_sha256") and R.sha256_bytes(exhibit_pdf) != v["exhibit_sha256"]:
                    raise R.DealRoomError(409, "The stored Exhibit A no longer matches its recorded hash.")
        except R.DealRoomError as exc:
            raise _http(exc) from exc
    pdf = await run_in_threadpool(P.build, sig, ctx, document_pdf=doc_pdf, exhibit_pdf=exhibit_pdf)
    name = f"signed-{str(sig['id'])[:8]}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


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
                   e.display_name AS entity_name, a.access_ends_on, a.paused_reason
            FROM deal_access a
            JOIN deals d ON d.id = a.deal_id AND d.valid_to IS NULL AND d.system_to IS NULL
            LEFT JOIN entities e ON e.id = a.entity_id AND e.valid_to IS NULL AND e.system_to IS NULL
            WHERE a.user_id = $1 AND a.valid_to IS NULL
            ORDER BY a.valid_from DESC
            """, UUID(str(principal.user_id)))
        sigs = await conn.fetch(
            """
            SELECT s.id, s.deal_id, s.signed_at, s.typed_name, COALESCE(v.title, t.title) AS title,
                   COALESCE(v.version, 1) AS version, t.version AS template_version, v.kind,
                   COALESCE(s.document_sha256, s.rendered_text_sha256) AS document_sha256,
                   e.display_name AS entity_name
            FROM deal_signatures s
            LEFT JOIN agreement_versions v ON v.id = s.agreement_version_id
            LEFT JOIN agreement_templates t ON t.id = s.template_id
            LEFT JOIN entities e ON e.id = s.entity_id AND e.valid_to IS NULL AND e.system_to IS NULL
            WHERE s.user_id = $1 ORDER BY s.signed_at
            """, UUID(str(principal.user_id)))
        to_sign: dict = {}
        for r in rows:
            if r["status"] != "resign_required":
                continue
            deal = await R.fetch_deal(conn, r["deal_id"])
            if deal is None:
                continue
            reqs, state = await R.access_state(conn, dict(deal), principal.user_id)
            to_sign[r["deal_id"]] = [{"agreement_id": str(a["id"]), "title": a["title"],
                                      "version": a["current_version"]} for a in reqs if not state[a["id"]]["ok"]]
        # DEALROOM3: the investor's own advisors and return-or-destroy notices;
        # and the deals the caller advises someone on.
        advisors = {r["deal_id"]: await C.advisors_of(conn, r["deal_id"], principal.user_id, lab) for r in rows}
        notices = {r["deal_id"]: await C.live_notice(conn, r["deal_id"], principal.user_id) for r in rows}
        advising = await conn.fetch(
            """
            SELECT ad.deal_id, d.name AS deal_name, ad.status, ad.role, COALESCE(u.full_name, u.email) AS investor_name
            FROM deal_room_advisors ad
            JOIN deals d ON d.id = ad.deal_id AND d.valid_to IS NULL AND d.system_to IS NULL
            JOIN users u ON u.id = ad.access_user_id
            WHERE ad.advisor_user_id = $1 AND ad.valid_to IS NULL AND ad.status IN ('invited', 'active')
            ORDER BY ad.valid_from DESC
            """, UUID(str(principal.user_id)))
    by_deal: dict = {}
    for s in sigs:
        by_deal.setdefault(s["deal_id"], []).append({
            "signature_id": str(s["id"]), "title": s["title"], "version": s["version"], "kind": s["kind"] or "template",
            "entity_name": s["entity_name"], "document_sha256": s["document_sha256"],
            "signed_at": s["signed_at"].isoformat(), "copy_href": f"/api/deal-room/signatures/{s['id']}/copy",
            "pdf_href": f"/api/deal-room/signatures/{s['id']}/pdf"})
    return {"rows": [{
        "deal_id": str(r["deal_id"]), "deal_name": r["deal_name"], "status": r["status"],
        "status_label": lab.get(f"deal_room_status_{r['status']}"), "level_granted": r["level_granted"],
        "level_label": lab.get(f"deal_room_level_{r['level_granted']}") if r["level_granted"] else None,
        "entity_name": r["entity_name"], "since": r["valid_from"].isoformat(),
        "to_sign": to_sign.get(r["deal_id"], []),
        "access_ends_on": r["access_ends_on"].isoformat() if r["access_ends_on"] else None,
        "paused_reason": r["paused_reason"],
        "advisors": advisors.get(r["deal_id"], []),
        "return_notice": _notice_out(notices.get(r["deal_id"])),
        "signatures": by_deal.get(r["deal_id"], [])} for r in rows],
        "advising": [{"deal_id": str(a["deal_id"]), "deal_name": a["deal_name"], "status": a["status"],
                      "status_label": lab.get(f"deal_room_advisor_status_{a['status']}"),
                      "role_label": lab.get(f"deal_room_advisor_role_{a['role']}"),
                      "investor_name": a["investor_name"], "needs_ack": a["status"] == "invited"} for a in advising]}


def _notice_out(n) -> dict | None:
    if n is None:
        return None
    return {"text": n["notice_text"], "sha256": n["notice_sha256"], "sent_at": n["sent_at"].isoformat(),
            "acknowledged_at": n["acknowledged_at"].isoformat() if n["acknowledged_at"] else None}


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
                   a.prior_level, a.resign_after, a.access_ends_on, a.paused_reason,
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
        # per deal: its requirements (own agreements + a covering master NDA);
        # per row: what satisfies each, by version (signature_state — the same
        # rule signing and approval use)
        reqs_by_deal: dict = {}
        states: dict = {}
        for r in rows:
            if r["deal_id"] not in reqs_by_deal:
                deal_row = await R.fetch_deal(conn, r["deal_id"])
                reqs_by_deal[r["deal_id"]] = (dict(deal_row), await R.required_agreements(conn, dict(deal_row))) \
                    if deal_row else (None, [])
            deal_row, reqs = reqs_by_deal[r["deal_id"]]
            states[r["id"]] = await R.signature_state(conn, deal_row, reqs, r["user_id"], r["entity_id"],
                                                      r["resign_after"]) if deal_row else {}
        lab = await R.labels(conn, principal.org_id)
        # DEALROOM3: each investor's advisors (under the investor) and any
        # return-or-destroy notice on the row's deal.
        pairs = [(r["deal_id"], r["user_id"]) for r in rows]
        adv_by: dict = {}
        notice_by: dict = {}
        if pairs:
            deal_ids = list({d for d, _ in pairs})
            user_ids = list({u for _, u in pairs})
            for a in await conn.fetch(
                    "SELECT deal_id, access_user_id, advisor_user_id, name, firm, role, status, acknowledged_at, "
                    "revoked_reason FROM deal_room_advisors WHERE deal_id = ANY($1::uuid[]) AND access_user_id = "
                    "ANY($2::uuid[]) AND valid_to IS NULL ORDER BY valid_from", deal_ids, user_ids):
                adv_by.setdefault((a["deal_id"], a["access_user_id"]), []).append({
                    "advisor_user_id": str(a["advisor_user_id"]), "name": a["name"], "firm": a["firm"],
                    "role": a["role"], "role_label": lab.get(f"deal_room_advisor_role_{a['role']}"),
                    "status": a["status"], "status_label": lab.get(f"deal_room_advisor_status_{a['status']}"),
                    "acknowledged_at": a["acknowledged_at"].isoformat() if a["acknowledged_at"] else None,
                    "revoked_reason": a["revoked_reason"]})
            for n in await conn.fetch(
                    "SELECT deal_id, user_id, sent_at, acknowledged_at FROM deal_room_return_notices WHERE deal_id = "
                    "ANY($1::uuid[]) AND user_id = ANY($2::uuid[]) AND valid_to IS NULL ORDER BY sent_at",
                    deal_ids, user_ids):
                notice_by[(n["deal_id"], n["user_id"])] = {
                    "sent_at": n["sent_at"].isoformat(),
                    "acknowledged_at": n["acknowledged_at"].isoformat() if n["acknowledged_at"] else None}

    def col_key(a):
        return "master_nda" if a.get("is_master") else (a["template_key"] or f"upload:{a['id']}")

    columns: list[dict] = []
    for _, reqs in reqs_by_deal.values():
        for a in reqs:
            if col_key(a) not in {c["key"] for c in columns}:
                columns.append({"key": col_key(a), "title": "Master NDA" if a.get("is_master") else a["title"]})
    out_rows = []
    for r in rows:
        reqs = reqs_by_deal[r["deal_id"]][1]
        st = states[r["id"]]
        by_key = {col_key(a): a for a in reqs}
        cells, detail = {}, {}
        for c in columns:
            a = by_key.get(c["key"])
            if a is None:
                cells[c["key"]] = "n/a"
                continue
            x = st[a["id"]]
            cells[c["key"]] = x["signed_at"].isoformat() if x["ok"] else None
            if x["covered"]:
                label = f"covered by master NDA signed {x['signed_at'].date().isoformat()}"
            elif x["ok"]:
                label = f"v{x['version']} · {x['signed_at'].date().isoformat()}"
            elif x["stale"]:
                label = f"signed v{x['last_version']} — v{a['current_version']} needed"
            else:
                label = None
            detail[c["key"]] = {"signed_at": x["signed_at"].isoformat() if x["ok"] else None,
                                "version": x["version"], "current_version": a["current_version"],
                                "covered_by_master": x["covered"], "stale": x["stale"], "label": label}
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
            "agreements": cells, "agreement_detail": detail,
            "covered_by_master": next((d["label"] for d in detail.values() if d["covered_by_master"]), None),
            "level_granted": r["level_granted"],
            "level_label": lab.get(f"deal_room_level_{r['level_granted']}") if r["level_granted"] else None,
            "prior_level": r["prior_level"],
            "status": r["status"], "status_label": lab.get(f"deal_room_status_{r['status']}"),
            "decided_by": r["decided_by_name"], "decided_at": r["decided_at"].isoformat() if r["decided_at"] else None,
            "reason": r["reason"], "group": r["group_label"],
            "access_ends_on": r["access_ends_on"].isoformat() if r["access_ends_on"] else None,
            "paused_reason": r["paused_reason"],
            "advisors": adv_by.get((r["deal_id"], r["user_id"]), []),
            "return_notice": notice_by.get((r["deal_id"], r["user_id"]))})
    return {
        "rows": out_rows, "total": total, "page": page, "page_size": page_size, "scope": scope,
        "group_by": group_by, "agreement_columns": columns,
        "permissions": {"can_read": True, "can_write": True, "is_super_admin": bool(principal.is_super_admin),
                        "read_permission": "deal publisher / delegate / manage_deals / manage_org_settings",
                        "write_permission": "deal publisher / delegate / manage_deals / manage_org_settings"},
        "vocabularies": {**R.vocabularies(lab), "group_by": list(GROUPS), "actions": list(R.DECIDE_ACTIONS),
                         "extendable_statuses": ["granted", "expired"],
                         "grant_levels": [v for v in R.vocabularies(lab)["levels"] if v["key"] in R.GRANT_LEVELS],
                         "editable": ["status"], "inline_editable": []},
    }


@router.post("/deal-room/access/decide")
async def decide_access(request: Request, body: Decision):
    """approve (choose level) / decline (reason) / revoke / require_resign — single or bulk.
    Every row is re-checked against the caller's scope; every change is
    audited in its own transaction with the change."""
    pool = await get_pool()
    results = []
    if body.send_return_notice and body.action != "revoke":
        raise HTTPException(status_code=422, detail="A return-or-destroy notice goes with a revoke.")
    async with pool.acquire() as conn:
        principal, scope, ids = await _require_grid_scope(request, conn)
        for access_id in body.access_ids:
            try:
                notice = None
                async with conn.transaction():
                    row = await conn.fetchrow("SELECT deal_id, org_id, status FROM deal_access WHERE id = $1 "
                                              "AND valid_to IS NULL", access_id)
                    if row is None:
                        raise R.DealRoomError(404, "Access request not found (it may have changed — reload).")
                    deal = await R.fetch_deal(conn, row["deal_id"])
                    if deal is None or not (await R.deal_rights(conn, principal, dict(deal))).can_manage:
                        raise R.DealRoomError(404, "Access request not found (it may have changed — reload).")
                    new = await R.decide(conn, actor=principal.user_id, access_id=access_id, action=body.action,
                                         level=body.level, reason=body.reason, ends_on=body.ends_on)
                    if body.action == "revoke" and body.send_return_notice:
                        notice = await C.send_return_notice(conn, dict(deal), new, actor=principal.user_id)
                results.append({"id": str(access_id), "ok": True, "new_id": str(new["id"]), "status": new["status"],
                                "access_ends_on": new["access_ends_on"].isoformat() if new.get("access_ends_on")
                                else None, "return_notice_id": str(notice["id"]) if notice else None})
                if notice is not None:
                    await _notify(pool, dict(deal), [new["user_id"]], "deal_room_return_notice",
                                  "Return or destroy deal materials",
                                  f"Your access to {deal['name']} was withdrawn. Please return or destroy the "
                                  "materials you received and acknowledge the notice in My deal access.",
                                  channels=C.CHANNELS)
                if body.action == "extend":
                    await _notify(pool, dict(deal), [new["user_id"]], "deal_access_extended", "Deal room access extended",
                                  f"Your access to {deal['name']} now runs until {new['access_ends_on'].isoformat()}.",
                                  channels=C.CHANNELS)
                elif new["status"] == "granted":
                    await _notify(pool, dict(deal), [new["user_id"]], "deal_access_granted", "Deal room open",
                                  f"Your access to {deal['name']} is open.", channels=C.CHANNELS)
                elif new["status"] == "resign_required":
                    await _notify(pool, dict(deal), [new["user_id"]], "deal_access_resign_required",
                                  "Re-signing required", f"Sign the agreements for {deal['name']} again to reopen "
                                                         "its data room.")
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


# ── DEALROOM2: uploaded agreements, versions, the page viewer ──────────────
async def _store_agreement_pdf(conn, deal, file: UploadFile, data: bytes, actor) -> dict:
    """An uploaded agreement PDF: validated (PDF only), stored in R2 under the
    deal, recorded as a deal document outside every folder (it is read only
    through the agreement), its SHA-256 computed from the bytes received."""
    if (file.filename or "").lower().endswith((".docx", ".doc")):
        raise R.DealRoomError(415, "Only PDF agreements can be uploaded: there is no pure-Python DOCX to PDF "
                                   "conversion that keeps the document's layout. Save it as PDF first.")
    pages = R.pdf_page_count(data)
    bucket = os.environ.get("R2_BUCKET_NAME", storage.DEFAULT_BUCKET)
    key = f"deals/{deal['id']}/agreements/{uuid.uuid4()}_{file.filename or 'agreement.pdf'}"
    await run_in_threadpool(storage.upload_bytes, key, data, "application/pdf", bucket)
    doc_id = await conn.fetchval(
        """
        INSERT INTO deal_documents (org_id, deal_id, folder_id, file_name, file_type, file_size_bytes, document_type,
            r2_key, r2_bucket, processing_status, uploaded_by, status, reviewed_by, reviewed_at, visible_to_members)
        VALUES ($1, $2, NULL, $3, 'application/pdf', $4, 'deal_agreement', $5, $6, 'pending', $7, 'approved', $7,
                now(), false)
        RETURNING id
        """, deal["org_id"], deal["id"], file.filename or "agreement.pdf", len(data), key, bucket,
        UUID(str(actor)))
    return {"document_id": doc_id, "file_sha256": R.sha256_bytes(data), "page_count": pages,
            "file_name": file.filename}


@router.post("/deal-room/deals/{deal_id}/agreements/upload", status_code=201)
async def upload_agreement(request: Request, deal_id: UUID, file: UploadFile = File(...),
                           title: str = Form(...), sort_order: int | None = Form(None)):
    """The publisher's own agreement as a PDF (draft until published). No
    fill-in fields: the investor signs the document as uploaded."""
    pool = await get_pool()
    data = await file.read()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            if not title.strip():
                raise HTTPException(status_code=422, detail="Give the agreement a title.")
            try:
                stored = await _store_agreement_pdf(conn, deal, file, data, principal.user_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
            order = sort_order if sort_order is not None else int(await conn.fetchval(
                "SELECT COALESCE(max(sort_order), 0) + 1 FROM deal_agreements WHERE deal_id = $1 "
                "AND system_to IS NULL AND status <> 'withdrawn'", deal_id))
            aid = await conn.fetchval(
                "INSERT INTO deal_agreements (org_id, deal_id, template_id, sort_order, kind, title, document_id, "
                "file_sha256, created_by) VALUES ($1, $2, NULL, $3, 'upload', $4, $5, $6, $7) RETURNING id",
                deal["org_id"], deal_id, order, title.strip(), stored["document_id"], stored["file_sha256"],
                UUID(str(principal.user_id)))
            await R.audit(conn, org_id=deal["org_id"], actor=principal.user_id, action="deal_room.agreement_upload",
                          resource_id=aid, payload={"file_sha256": stored["file_sha256"],
                                                    "page_count": stored["page_count"],
                                                    "file_name": stored["file_name"]})
    return {"id": str(aid), "kind": "upload", "sort_order": order, "file_sha256": stored["file_sha256"],
            "page_count": stored["page_count"], "fill_in_fields": False}


async def _after_version(pool, deal, out) -> dict:
    users = list({a["user_id"] for a in out["affected"]})
    if users:
        await _notify(pool, deal, users, "deal_access_resign_required", "Re-signing required",
                      f"An agreement for {deal['name']} changed. Sign the new version to reopen the data room.")
    v = out["version"]
    return {"version": v["version"], "version_id": str(v["id"]), "policy": out["policy"],
            "signatures": out["signatures"], "file_sha256": v["file_sha256"],
            "affected": [{"deal_id": str(a["deal_id"]), "user_id": str(a["user_id"])} for a in out["affected"]]}


@router.post("/deal-room/deals/{deal_id}/agreements/{agreement_id}/versions", status_code=201)
async def new_agreement_version(request: Request, deal_id: UUID, agreement_id: UUID, body: NewVersion):
    """A new version of a PUBLISHED template agreement (changed field values or
    Exhibit A). Once anyone has signed, resign_policy is required: keep or
    resign."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            if body.exhibit_document_id and not await conn.fetchval(
                    "SELECT 1 FROM deal_documents WHERE id = $1 AND deal_id = $2 AND deleted_at IS NULL",
                    body.exhibit_document_id, deal_id):
                raise HTTPException(status_code=404, detail="Exhibit document not found on this deal")
            try:
                out = await R.new_version(conn, deal, agreement_id, actor=principal.user_id,
                                          policy=body.resign_policy, field_values=body.field_values,
                                          exhibit_document_id=body.exhibit_document_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
    return await _after_version(pool, deal, out)


@router.post("/deal-room/deals/{deal_id}/agreements/{agreement_id}/versions/file", status_code=201)
async def replace_agreement_file(request: Request, deal_id: UUID, agreement_id: UUID, file: UploadFile = File(...),
                                 resign_policy: str | None = Form(None)):
    """Replace an uploaded agreement's PDF. A draft is replaced in place; a
    published one gets a new version (keep / resign once signed)."""
    pool = await get_pool()
    data = await file.read()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            a = await _agreement(conn, deal_id, agreement_id)
            if a["kind"] != "upload":
                raise HTTPException(status_code=422, detail="Only an uploaded agreement takes a replacement file.")
            if a["status"] == "published" and resign_policy not in R.RESIGN_POLICIES:
                nsig = int(await conn.fetchval("SELECT count(*) FROM deal_signatures WHERE deal_agreement_id = $1",
                                               a["id"]))
                if nsig:  # refuse BEFORE anything is stored
                    raise HTTPException(status_code=422, detail={
                        "message": f"This agreement has {nsig} signature(s). Choose 'keep' (existing signers stay) "
                                   "or 'resign' (they must sign the new version).",
                        "signatures": nsig, "choices": list(R.RESIGN_POLICIES)})
            try:
                stored = await _store_agreement_pdf(conn, deal, file, data, principal.user_id)
                if a["status"] == "draft":
                    await R.archive_agreement(conn, agreement_id)
                    await conn.execute("UPDATE deal_agreements SET document_id = $2, file_sha256 = $3, "
                                       "system_from = now() WHERE id = $1", agreement_id, stored["document_id"],
                                       stored["file_sha256"])
                    return {"version": None, "status": "draft", "file_sha256": stored["file_sha256"],
                            "page_count": stored["page_count"], "affected": []}
                out = await R.new_version(conn, deal, agreement_id, actor=principal.user_id, policy=resign_policy,
                                          new_file=stored)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
    return await _after_version(pool, deal, out)


@router.get("/deal-room/deals/{deal_id}/agreements/{agreement_id}/versions")
async def list_agreement_versions(request: Request, deal_id: UUID, agreement_id: UUID):
    """Every version of an agreement and how many signatures each carries."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        _, deal, _ = await _require_deal_manager(request, conn, deal_id)
        a = await _agreement(conn, deal_id, agreement_id)
        counts = {r["v"]: int(r["n"]) for r in await conn.fetch(
            "SELECT COALESCE(agreement_version_id::text, 'v1-legacy') AS v, count(*) AS n FROM deal_signatures "
            "WHERE deal_agreement_id = $1 GROUP BY 1", a["id"])}
        rows = await R.versions_of(conn, a["id"])
    return {"agreement_id": str(a["id"]), "title": a["title"], "kind": a["kind"], "rows": [
        {"id": str(v["id"]), "version": v["version"], "resign_policy": v["resign_policy"],
         "created_at": v["created_at"].isoformat(), "file_sha256": v["file_sha256"], "file_name": v["file_name"],
         "page_count": v["page_count"], "template_key": v["template_key"], "template_version": v["template_version"],
         "field_values": v["field_values"], "exhibit_sha256": v["exhibit_sha256"],
         "signatures": counts.get(str(v["id"]), 0) + (counts.get("v1-legacy", 0) if v["version"] == 1 else 0)}
        for v in rows]}


@router.get("/deal-room/deals/{deal_id}/agreements/{agreement_id}/pages/{page}")
async def agreement_page(request: Request, deal_id: UUID, agreement_id: UUID, page: int,
                         part: str = Query("document")):
    """One page of an uploaded agreement (or of Exhibit A) as PNG, for the
    in-app viewer: the investor reads every page before Sign enables. Managers
    may preview drafts; investors see only the current published version."""
    if part not in ("document", "exhibit") or page < 1:
        raise HTTPException(status_code=422, detail="part must be document or exhibit; pages start at 1")
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
        if rights.can_manage:
            a = await R.agreement_by_id(conn, agreement_id)
            if a is None or str(a["deal_id"]) != str(deal_id):
                a = next((x for x in await R.required_agreements(conn, deal) if str(x["id"]) == str(agreement_id)),
                         None)
            if a is None:
                raise HTTPException(status_code=404, detail="Agreement not found")
        else:
            _, a = await _investor_agreement(conn, deal, principal, agreement_id)
        v = await R.get_version(conn, a["version_id"]) if a.get("version_id") else None
        doc_id = (v or a).get("exhibit_document_id") if part == "exhibit" else (v or a).get("document_id")
        if not doc_id:
            raise HTTPException(status_code=404, detail="No such document on this agreement")
        try:
            _, data = await R.read_document_bytes(conn, doc_id)
            want = (v or {}).get("exhibit_sha256") if part == "exhibit" else (v or a).get("file_sha256")
            if want and R.sha256_bytes(data) != want:
                raise R.DealRoomError(409, "The stored file no longer matches its recorded hash.")
        except R.DealRoomError as exc:
            raise _http(exc) from exc
    try:
        png = await run_in_threadpool(P.page_png, data, page - 1)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail="No such page") from exc
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


# ── DEALROOM2: the master NDA ──────────────────────────────────────────────
def _master_out(m) -> dict | None:
    if m is None:
        return None
    return {**_jsonable({k: m[k] for k in ("id", "publisher_entity_id", "deal_agreement_id", "scope",
                                            "accepted_min_version", "created_at")}),
            "deal_ids": [str(d) for d in m["deal_ids"]]}


@router.get("/deal-room/publishers/{entity_id}/master-nda")
async def get_master_nda(request: Request, entity_id: UUID):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _require_publisher_manager(request, conn, entity_id)
        return {"master_nda": _master_out(await R.live_master(conn, entity_id))}


@router.put("/deal-room/publishers/{entity_id}/master-nda")
async def put_master_nda(request: Request, entity_id: UUID, body: MasterNdaSet):
    """Mark one published agreement as the publisher's master NDA, covering all
    their deals or selected ones. Replaces any previous master (history kept)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, org = await _require_publisher_manager(request, conn, entity_id)
            try:
                m = await R.set_master(conn, org_id=org, publisher_entity_id=entity_id,
                                       deal_agreement_id=body.deal_agreement_id, scope=body.scope,
                                       deal_ids=body.deal_ids, accepted_min_version=body.accepted_min_version,
                                       actor=principal.user_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
            await R.audit(conn, org_id=org, actor=principal.user_id, action="deal_room.master_nda_set",
                          resource_id=m["id"], payload=body.model_dump())
    return {"master_nda": _master_out(m)}


@router.delete("/deal-room/publishers/{entity_id}/master-nda")
async def delete_master_nda(request: Request, entity_id: UUID):
    """Stop using a master NDA for future requests. Signatures and access
    already granted are untouched."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, org = await _require_publisher_manager(request, conn, entity_id)
            if not await R.revoke_master(conn, entity_id, actor=principal.user_id):
                raise HTTPException(status_code=404, detail="No master NDA is set")
            await R.audit(conn, org_id=org, actor=principal.user_id, action="deal_room.master_nda_revoke",
                          resource_id=entity_id, payload={})
    return {"revoked": True}


# ── DEALROOM2: the firm template library ───────────────────────────────────
def _library_row(t: dict, principal) -> dict:
    platform = t["org_id"] is None
    editable = t["status"] == "draft" and not int(t.get("in_use") or 0)
    can = principal.is_super_admin if platform else True
    return {**L.jsonable({k: t[k] for k in ("id", "key", "version", "title", "body", "fields", "scope", "status",
                                            "based_on_id", "created_at", "published_at")}),
            "in_use": int(t.get("in_use") or 0), "can_edit": bool(can and editable),
            "can_publish": bool(can and t["status"] == "draft"),
            "can_add_version": bool(can and t["status"] != "draft"), "can_copy": bool(platform)}


@router.get("/deal-room/library")
async def get_library(request: Request):
    """The firm's library (its own templates, every version) beside the
    platform templates. Org admins (manage_org_settings) and Super Admin."""
    principal = require_permission(request, "manage_org_settings")
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await L.library(conn, principal.org_id)
        lab = await R.labels(conn, principal.org_id)
    return {"rows": [_library_row(t, principal) for t in rows],
            "permissions": {"can_read": True, "can_write": True, "is_super_admin": bool(principal.is_super_admin),
                            "can_write_platform": bool(principal.is_super_admin),
                            "read_permission": "manage_org_settings", "write_permission": "manage_org_settings"},
            "vocabularies": {"editable": ["title", "body", "fields"], "inline_editable": [],
                             "sources": list(L.SOURCES), "types": list(L.TYPES),
                             "agreement_kinds": R.vocabularies(lab)["agreement_kinds"]}}


@router.post("/deal-room/library", status_code=201)
async def create_library_template(request: Request, body: LibraryCreate):
    """Copy a platform template into the firm's library, or write a new one
    (draft, version 1). scope 'platform' is Super Admin only."""
    principal = require_permission(request, "manage_org_settings")
    if body.scope not in ("org", "platform"):
        raise HTTPException(status_code=422, detail="scope must be org or platform")
    if body.scope == "platform":
        require_super_admin(request)
    org_id = None if body.scope == "platform" else get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                if body.source_template_id:
                    if org_id is None:
                        raise R.DealRoomError(422, "Copying is into a firm library (scope org).")
                    t = await L.copy_to_firm(conn, org_id=org_id, source_id=body.source_template_id, key=body.key,
                                             actor=principal.user_id)
                else:
                    t = await L.create(conn, org_id=org_id, key=body.key or "", title=body.title or "",
                                       body=body.body or "", fields=body.fields or [], actor=principal.user_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
            await R.audit(conn, org_id=principal.org_id, actor=principal.user_id, action="deal_room.template_create",
                          resource_id=t["id"], payload={"scope": body.scope, "key": t["key"],
                                                        "based_on": body.source_template_id})
    return _library_row({**t, "in_use": 0}, principal)


@router.patch("/deal-room/library/{template_id}")
async def edit_library_template(request: Request, template_id: UUID, body: LibraryEdit):
    changes = body.model_dump(exclude_unset=True)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, t = await _library_target(request, conn, template_id)
            try:
                t = await L.edit(conn, t, changes)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
            await R.audit(conn, org_id=principal.org_id, actor=principal.user_id, action="deal_room.template_edit",
                          resource_id=t["id"], payload={"changed": sorted(changes)})
    return _library_row({**t, "in_use": 0}, principal)


@router.post("/deal-room/library/{template_id}/versions", status_code=201)
async def add_library_version(request: Request, template_id: UUID):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, t = await _library_target(request, conn, template_id)
            try:
                t = await L.add_version(conn, t, actor=principal.user_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
            await R.audit(conn, org_id=principal.org_id, actor=principal.user_id, action="deal_room.template_version",
                          resource_id=t["id"], payload={"key": t["key"], "version": t["version"]})
    return _library_row({**t, "in_use": 0}, principal)


@router.post("/deal-room/library/{template_id}/publish")
async def publish_library_template(request: Request, template_id: UUID):
    """draft → active; the firm's previous active version of the key retires
    (kept forever — deals already using it keep their own snapshot)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, t = await _library_target(request, conn, template_id)
            try:
                t = await L.publish(conn, t, actor=principal.user_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
            await R.audit(conn, org_id=principal.org_id, actor=principal.user_id, action="deal_room.template_publish",
                          resource_id=t["id"], payload={"key": t["key"], "version": t["version"]})
            n = await L.in_use(conn, t["id"])
    return _library_row({**t, "in_use": n}, principal)



# ── DEALROOM3: Q&A ─────────────────────────────────────────────────────────
@router.get("/deal-room/deals/{deal_id}/questions")
async def list_questions(request: Request, deal_id: UUID):
    """Managers: the queue (every live question, with its asker). Investors and
    advisors: their own questions plus every ANSWERED question their access
    reaches — never another investor's identity. Reading answers is logged."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
        if rights.role == "advisor" and rights.advisor_status != "active":
            raise HTTPException(status_code=404, detail="Deal not found")
        lab = await R.labels(conn, deal["org_id"])
        rows = await C.questions_for(conn, deal, manage=rights.can_manage, user_id=principal.user_id, lab=lab)
        reader = None
        if not rights.can_manage:
            reader = await C.reader_context(conn, deal, principal, rights)
            seen = {r["detail"] for r in await conn.fetch(
                "SELECT detail->>'question_id' AS detail FROM deal_room_activity WHERE deal_id = $1 AND user_id = $2 "
                "AND action = 'answer_read'", deal["id"], UUID(str(principal.user_id)))}
            for q in rows:
                if q["status"] == "answered" and q["id"] not in seen:
                    await C.log_activity(conn, deal, reader, "answer_read", folder_id=q["folder_id"],
                                         detail={"question_id": q["id"]})
        can_ask = bool(not rights.can_manage and rights.role != "advisor" and reader is not None
                       and reader["access"] is not None and reader["access"]["status"] == "granted")
    return {"rows": rows,
            "permissions": {"can_read": True, "can_write": rights.can_manage, "can_ask": can_ask,
                            "is_super_admin": bool(principal.is_super_admin), "read_permission": "view_deals",
                            "write_permission": "deal publisher / delegate / manage_deals"},
            "vocabularies": {"question_statuses": R.vocab(lab, "deal_room_question_status_", R.QUESTION_STATUSES),
                             "editable": ["answer", "withdraw", "merge"] if rights.can_manage else [],
                             "inline_editable": []}}


@router.post("/deal-room/deals/{deal_id}/questions", status_code=201)
async def ask_question(request: Request, deal_id: UUID, body: QuestionCreate):
    """An investor with granted access asks — generally, or about a folder they can open."""
    pool = await get_pool()
    outbox = C.Outbox()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
            _refuse_advisor(rights)
            if rights.can_manage:
                raise HTTPException(status_code=409, detail="Managers answer questions; investors ask them.")
            reader = await C.reader_context(conn, deal, principal, rights)
            try:
                q = await C.ask(conn, deal, reader, body=body.body, folder_id=body.folder_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
            await C.publisher_notify(conn, outbox, deal, "question", "deal_room_question_asked", "New question",
                                     f"An investor asked a question about {deal['name']}.",
                                     {"question_id": str(q["id"])})
    await outbox.publish(pool)
    return {"id": str(q["id"]), "status": q["status"], "folder_id": str(q["folder_id"]) if q["folder_id"] else None}


@router.post("/deal-room/deals/{deal_id}/questions/{question_id}/answer")
async def answer_question(request: Request, deal_id: UUID, question_id: UUID, body: AnswerBody):
    """Publish an answer to every investor whose access reaches the question's folder (general →
    after_agreements); each of them is notified, the asker included."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            try:
                ans, audience = await C.answer(conn, deal, question_id, body=body.body, actor=principal.user_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
    outbox = C.Outbox()
    outbox.add(deal, audience, "deal_room_question_answered", "A question was answered",
               f"A question about {deal['name']} has an answer in the data room's Q&A.",
               {"question_id": str(question_id)})
    await outbox.publish(pool)
    return {"question_id": str(question_id), "answer_id": str(ans["id"]), "recipients": len(audience)}


@router.post("/deal-room/deals/{deal_id}/questions/{question_id}/withdraw")
async def withdraw_question(request: Request, deal_id: UUID, question_id: UUID, body: ReasonBody):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            try:
                q = await C.withdraw(conn, deal, question_id, reason=body.reason, actor=principal.user_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
    return {"id": str(q["id"]), "status": q["status"]}


@router.post("/deal-room/deals/{deal_id}/questions/{question_id}/merge")
async def merge_question(request: Request, deal_id: UUID, question_id: UUID, body: MergeBody):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
            try:
                q = await C.merge(conn, deal, question_id, into_id=body.into_id, reason=body.reason,
                                  actor=principal.user_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
    return {"id": str(q["id"]), "status": q["status"], "merged_into": str(q["merged_into"])}


# ── DEALROOM3: advisors ────────────────────────────────────────────────────
@router.post("/deal-room/deals/{deal_id}/advisors", status_code=201)
async def invite_room_advisor(request: Request, deal_id: UUID, body: AdvisorInvite):
    """An investor with granted access names an advisor (an existing user of the firm is linked;
    otherwise invited through the usermgmt1 path). The advisor sees nothing until they acknowledge."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
            _refuse_advisor(rights)
            if rights.can_manage:
                raise HTTPException(status_code=409, detail="Advisors are named by investors with access.")
            reader = await C.reader_context(conn, deal, principal, rights)
            try:
                adv = await C.invite_advisor(conn, deal, reader, name=body.name, firm=body.firm, role=body.role,
                                             email=body.email)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
    await _notify(pool, deal, [adv["advisor_user_id"]], "deal_room_advisor_invited", "Advisor invitation",
                  f"You have been asked to review {deal['name']} as an advisor. Sign the acknowledgement to begin.",
                  channels=C.CHANNELS)
    return {"advisor_user_id": str(adv["advisor_user_id"]), "status": adv["status"], "how": adv["how"]}


@router.get("/deal-room/deals/{deal_id}/advisors")
async def list_room_advisors(request: Request, deal_id: UUID):
    """Managers: every advisor on the deal, under their investor. An investor: their own."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
        _refuse_advisor(rights)
        lab = await R.labels(conn, deal["org_id"])
        rows = await C.advisors_of(conn, deal_id, None if rights.can_manage else principal.user_id, lab)
    return {"rows": rows, "permissions": {"can_read": True, "can_write": rights.can_manage,
                                          "is_super_admin": bool(principal.is_super_admin),
                                          "read_permission": "view_deals",
                                          "write_permission": "deal publisher / delegate / manage_deals"},
            "vocabularies": {"advisor_roles": R.vocab(lab, "deal_room_advisor_role_", R.ADVISOR_ROLES),
                             "editable": ["remove"] if rights.can_manage else [], "inline_editable": []}}


@router.post("/deal-room/deals/{deal_id}/advisors/{advisor_user_id}/remove")
async def remove_room_advisor(request: Request, deal_id: UUID, advisor_user_id: UUID, body: ReasonBody):
    """The investor removes their own advisor; a manager removes any advisor (reason recorded)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
            _refuse_advisor(rights)
            try:
                row = await C.remove_advisor(conn, deal, advisor_user_id, actor=principal.user_id, reason=body.reason,
                                             by_investor=None if rights.can_manage else principal.user_id)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
    return {"advisor_user_id": str(advisor_user_id), "status": row["status"]}


@router.get("/deal-room/deals/{deal_id}/advisor-acknowledgement")
async def get_advisor_ack(request: Request, deal_id: UUID):
    """The acknowledgement text an invited advisor signs (and its hash)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
        if rights.role != "advisor":
            raise HTTPException(status_code=404, detail="No advisor invitation for you on this deal.")
        adv = await R.advisor_row(conn, deal_id, principal.user_id)
        text = await C.advisor_ack_text(conn, deal, adv)
    return {"text": text, "text_sha256": C.sha256_text(text), "status": adv["status"]}


@router.post("/deal-room/deals/{deal_id}/advisor-acknowledgement")
async def sign_advisor_ack(request: Request, deal_id: UUID, body: AdvisorAck):
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, rights = await _require_deal_viewer(request, conn, deal_id)
            if rights.role != "advisor":
                raise HTTPException(status_code=404, detail="No advisor invitation for you on this deal.")
            try:
                row = await C.acknowledge(conn, deal, principal, typed_name=body.typed_name, consent=body.consent,
                                          text_sha256=body.text_sha256)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
    return {"status": row["status"], "acknowledged_at": row["acknowledged_at"].isoformat()}


# ── DEALROOM3: the return-or-destroy notice ────────────────────────────────
@router.post("/deal-room/deals/{deal_id}/return-notice/acknowledge")
async def acknowledge_return_notice(request: Request, deal_id: UUID, body: NoticeAck):
    """The investor confirms they returned or destroyed the materials (typed name)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            principal, deal, _ = await _require_deal_party(request, conn, deal_id)
            try:
                row = await C.acknowledge_notice(conn, deal, principal, typed_name=body.typed_name)
            except R.DealRoomError as exc:
                raise _http(exc) from exc
    return {"acknowledged_at": row["acknowledged_at"].isoformat()}


# ── DEALROOM3: activity (managers only) and the watermark lookup ───────────
@router.get("/deal-room/deals/{deal_id}/activity")
async def deal_activity(request: Request, deal_id: UUID, view: str = Query("investor"),
                        format: str = Query("json")):
    """Per investor (first / last open, documents viewed / downloaded, pages), per document, or every
    event — JSON or CSV. Managers only: an investor never sees anyone's activity. Kept after access
    ends and after the deal closes (append-only; no delete path)."""
    if view not in C.ACTIVITY_VIEWS or format not in ("json", "csv"):
        raise HTTPException(status_code=422, detail="view must be investor, document or events; format json or csv")
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal, deal, _ = await _require_deal_manager(request, conn, deal_id)
        rows = await C.activity_rows(conn, deal_id, view)
        lab = await R.labels(conn, deal["org_id"])
    if format == "csv":
        return PlainTextResponse(C.activity_csv(rows), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="deal-activity-{view}.csv"', "Cache-Control": "no-store"})
    return {"view": view, "rows": _jsonable(rows),
            "permissions": {"can_read": True, "can_write": False, "is_super_admin": bool(principal.is_super_admin),
                            "read_permission": "deal publisher / delegate / manage_deals", "write_permission": None},
            "vocabularies": {"views": list(C.ACTIVITY_VIEWS),
                             "activity_actions": R.vocab(lab, "deal_room_activity_", R.ACTIVITY_ACTIONS),
                             "editable": [], "inline_editable": []}}


@router.get("/deal-room/deals/{deal_id}/watermarks/{watermark_id}")
async def lookup_watermark(request: Request, deal_id: UUID, watermark_id: UUID):
    """A watermark id printed on a leaked page → the person, entity, document and time it was made for."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _require_deal_manager(request, conn, deal_id)
        w = await C.lookup_watermark(conn, watermark_id)
    if w is None or str(w["deal_id"]) != str(deal_id):
        raise HTTPException(status_code=404, detail="Watermark not found")
    return _jsonable(w)


# ── DEALROOM3: CRM relationship date (the DRAFT 506(b) rule's input) ───────
@router.put("/deal-room/relationship-dates/{entity_id}")
async def put_relationship_date(request: Request, entity_id: UUID, body: RelationshipDate):
    """Staff record when the firm's relationship with a CRM entity began (history kept)."""
    principal = require_any_permission(request, PUBLISHER_ADMIN_PERMS)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if not await conn.fetchval("SELECT 1 FROM entities WHERE id = $1 AND org_id = $2 AND valid_to IS NULL "
                                       "AND system_to IS NULL", entity_id, UUID(str(org_id))):
                raise HTTPException(status_code=404, detail="Entity not found")
            row = await C.set_relationship_date(conn, org_id, entity_id, established_on=body.established_on,
                                                basis=body.basis, actor=principal.user_id)
            await R.audit(conn, org_id=org_id, actor=principal.user_id, action="deal_room.relationship_date",
                          resource_id=entity_id, payload=body.model_dump())
    return _jsonable(row)
