"""CRMDETAIL1 — the CRM record's Relationships panel, typed detail fields and Liabilities (+ net worth).

Routes (all under /api/v1):
  GET   /crm/vocabularies                          — every CRMDETAIL1 vocabulary (internal)
  GET   /entities/{entity_id}/crm-relationships    — governed relationships, both directions + history (internal)
  POST  /entities/{entity_id}/crm-relationships    — add one (internal + manage_members)
  POST  /crm-relationships/{rel_id}/end            — end one: valid_to, never deleted (internal + manage_members)
  GET   /entities/{entity_id}/details              — typed detail fields with labels / types (internal)
  PUT   /entities/{entity_id}/details              — save typed detail fields (internal + manage_members)
  GET   /entities/{entity_id}/liabilities          — staff any record; a member only their own family's
  POST  /entities/{entity_id}/liabilities          — add (internal + manage_members)
  PATCH /liabilities/{liability_id}                — a new version (internal + manage_members)
  POST  /liabilities/{liability_id}/end            — end it, history kept (internal + manage_members)
  GET   /entities/{entity_id}/net-worth            — gross / liabilities / net; staff any, a member own family

GATES. Reads of staff data: ``require_internal`` (STAFFROLES1 — any internal firm role). Writes: an internal firm
role holding ``manage_members`` (``require_internal_permission`` — Super Admin first, the unchanged staff bypass, a
client role never), the same key every other CRM write uses. The two member-scoped reads admit a client only for an
entity of their own family (``require_entity_access`` → 404 otherwise, existence not disclosed) and only with
``view_portfolio``. org_id is always the caller's verified org (``get_org_id``); no request model declares it
(``extra='forbid'``).

ENVELOPE. Every read returns ``rows`` + ``permissions`` (can_read / can_write / is_super_admin / read_permission /
write_permission) + ``vocabularies`` (``editable`` / ``inline_editable`` are EMPTY for a view-only caller — never
omitted). Labels never live in the page.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import crm_config as C
from services import crm_relationships as R
from services import crm_vocab as V
from services import entity_details as D
from services import liabilities as L
from services.authz import (
    principal_of,
    require_active,
    require_entity_access,
    require_internal,
    require_internal_permission,
)
from services.database import get_pool

router = APIRouter(tags=["crm_detail"])

WRITE_PERMISSION = "manage_members"
MEMBER_READ_PERMISSION = "view_portfolio"


class RelationshipAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    relationship_type: str
    other_entity_id: UUID
    direction: str = "outgoing"
    notes: str | None = Field(default=None, max_length=2000)
    effective_date: date | None = None


class RelationshipEnd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=500)


class DetailsSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any]


class LiabilityBody(BaseModel):
    """Amounts are strings (Decimal end to end; a float is refused by services.liabilities)."""
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    kind: str | None = None
    lender_entity_id: UUID | None = None
    lender_name: str | None = None
    original_amount: Any = None
    current_balance: Any = None
    currency_code: str | None = None
    as_of: date | None = None
    rate_type: str | None = None
    rate_text: str | None = None
    maturity: date | None = None
    collateral_account_id: UUID | None = None
    collateral_asset_id: UUID | None = None
    collateral_text: str | None = None
    notes: str | None = None


class LiabilityRevise(LiabilityBody):
    reason: str | None = Field(default=None, max_length=500)


class LiabilityEnd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=500)


def _can_write(request: Request) -> bool:
    p = principal_of(request)
    return bool(p is not None and p.admitted and (p.is_super_admin or p.is_staff
                                                  or (p.is_internal and WRITE_PERMISSION in p.permissions)))


def _permissions(request: Request, read_permission: str = "internal") -> dict:
    p = principal_of(request)
    return {"can_read": True, "can_write": _can_write(request), "is_super_admin": bool(p and p.is_super_admin),
            "read_permission": read_permission, "write_permission": WRITE_PERMISSION}


def _vocab(request: Request, vocab: dict, editable: list[str]) -> dict:
    can = _can_write(request)
    return {**vocab, "editable": list(editable) if can else [], "inline_editable": []}


def _actor(request: Request):
    p = principal_of(request)
    return p.user_id if p is not None else None


async def _member_scoped_entity(request: Request, pool, entity_id: UUID):
    """Staff / internal: any record of the org; a client: only their own family's record, with view_portfolio."""
    p = require_active(request)
    if not (p.is_super_admin or p.is_internal) and MEMBER_READ_PERMISSION not in p.permissions:
        raise HTTPException(status_code=403, detail=f"Permission required: {MEMBER_READ_PERMISSION}")
    return await require_entity_access(request, pool, entity_id)


# ── Vocabularies ────────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/crm/vocabularies")
async def crm_vocabularies(request: Request):
    require_internal(request)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # PORTENTRY1 (GAP-CRM-01..03) — statuses, marital statuses and sub-types from the org's config table
        org_vocab = await C.crm_vocab(conn, org_id)
    from services import record_permissions as RP

    # BUGFIX3 — the CRM list's create controls from the exact rule of each create route (no client default)
    perms = {**_permissions(request), "can": {"new_entity": RP.allowed(principal_of(request), "new_entity")}}
    return {"rows": [], "permissions": perms, "vocabularies": _vocab(
        request, {**V.all_vocab(), **org_vocab}, ["relationships", "details", "liabilities"])}


# ── Relationships ───────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/entities/{entity_id}/crm-relationships")
async def list_crm_relationships(request: Request, entity_id: UUID):
    require_internal(request)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            rows = await R.list_for_entity(conn, org_id, entity_id)
        except R.RelationshipNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"rows": rows, "permissions": _permissions(request),
            "vocabularies": _vocab(request, {**V.relationship_vocab(), "entity_type_labels": V.ENTITY_TYPE_LABELS},
                                   ["add", "end"])}


@router.post("/entities/{entity_id}/crm-relationships", status_code=201)
async def add_crm_relationship(request: Request, entity_id: UUID, body: RelationshipAdd):
    p = require_internal_permission(request, "manage_members")
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await R.create(conn, org_id=org_id, actor_id=p.user_id, entity_id=entity_id,
                                  relationship_type=body.relationship_type, other_entity_id=body.other_entity_id,
                                  direction=body.direction, notes=body.notes, effective_date=body.effective_date)
        except R.RelationshipNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except R.RelationshipConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except R.RelationshipError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/crm-relationships/{rel_id}/end")
async def end_crm_relationship(request: Request, rel_id: UUID, body: RelationshipEnd):
    require_internal_permission(request, "manage_members")
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await R.end(conn, org_id=org_id, rel_id=rel_id, reason=body.reason)
        except R.RelationshipNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── Typed detail fields ─────────────────────────────────────────────────────────────────────────────────────────
@router.get("/entities/{entity_id}/details")
async def get_entity_details(request: Request, entity_id: UUID):
    require_internal(request)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            rows = await D.get_details(conn, org_id, entity_id)
        except D.DetailNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    editable = [f["key"] for f in rows["fields"] if f["editable_here"]]
    return {"rows": rows, "permissions": _permissions(request),
            "vocabularies": _vocab(request, {"field_types": list(V.FIELD_TYPES)}, editable)}


@router.put("/entities/{entity_id}/details")
async def save_entity_details(request: Request, entity_id: UUID, body: DetailsSave):
    p = require_internal_permission(request, "manage_members")
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await D.save_details(conn, org_id, p.user_id, entity_id, body.values)
        except D.DetailNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except D.DetailError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── Liabilities + net worth ─────────────────────────────────────────────────────────────────────────────────────
def _nw_vocab() -> dict:
    from services import networth as NW

    return NW.vocabularies()


@router.get("/entities/{entity_id}/liabilities")
async def list_liabilities(request: Request, entity_id: UUID):
    # BUGFIX2 — authz: member-scoped (staff any record; a member only their own family's, else 404)
    pool = await get_pool()
    await _member_scoped_entity(request, pool, entity_id)
    org_id = get_org_id(request)
    async with pool.acquire() as conn:
        try:
            rows = await L.list_for_entity(conn, org_id, entity_id)
        except L.LiabilityNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        nw = await L.entity_net_worth(conn, org_id, entity_id)
    return {"rows": rows, "net_worth": nw, "permissions": _permissions(request, "internal or own family"),
            # ROLLUP2 — the Unclassified label and split lines come from the envelope (services.networth)
            "vocabularies": _vocab(request, {**V.liability_vocab(), **_nw_vocab()}, ["add", "edit", "end"])}


@router.post("/entities/{entity_id}/liabilities", status_code=201)
async def add_liability(request: Request, entity_id: UUID, body: LiabilityBody):
    p = require_internal_permission(request, "manage_members")
    org_id = get_org_id(request)
    pool = await get_pool()
    data = body.model_dump(exclude_unset=True)
    async with pool.acquire() as conn:
        try:
            out = await L.create(conn, org_id=org_id, actor_id=p.user_id, entity_id=entity_id, data=data)
        except L.LiabilityNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except L.LiabilityError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _refresh(pool, org_id, [entity_id], "liability added", p.user_id)
    return out


async def _refresh(pool, org_id, entity_ids, reason, actor_id):
    """PORTENTRY1 (GAP-PORT-05) — liabilities are read live, but the family's snapshot (and its as-of) is refreshed
    on every liability write too, so the three numbers always come from one moment."""
    from services import portfolio_refresh as PR

    await PR.after_write(pool, org_id, [e for e in entity_ids if e], reason=reason, actor_id=actor_id)


@router.patch("/liabilities/{liability_id}")
async def revise_liability(request: Request, liability_id: UUID, body: LiabilityRevise):
    p = require_internal_permission(request, "manage_members")
    org_id = get_org_id(request)
    pool = await get_pool()
    changes = body.model_dump(exclude_unset=True)
    reason = changes.pop("reason", None)
    async with pool.acquire() as conn:
        try:
            out = await L.revise(conn, org_id=org_id, actor_id=p.user_id, liability_id=liability_id,
                                 changes=changes, reason=reason)
        except L.LiabilityNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except L.LiabilityError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _refresh(pool, org_id, [(out or {}).get("owner_entity_id")], "liability revised", p.user_id)
    return out


@router.post("/liabilities/{liability_id}/end")
async def end_liability(request: Request, liability_id: UUID, body: LiabilityEnd):
    p = require_internal_permission(request, "manage_members")
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            out = await L.end(conn, org_id=org_id, liability_id=liability_id, reason=body.reason)
        except L.LiabilityNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _refresh(pool, org_id, [(out or {}).get("owner_entity_id")], "liability ended", p.user_id)
    return out


@router.get("/entities/{entity_id}/net-worth")
async def entity_net_worth(request: Request, entity_id: UUID):
    # BUGFIX2 — authz: member-scoped (staff any record; a member only their own family's, else 404)
    pool = await get_pool()
    await _member_scoped_entity(request, pool, entity_id)
    org_id = get_org_id(request)
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM entities WHERE id = $1 AND org_id = $2::uuid AND valid_to IS NULL "
                                     "AND system_to IS NULL", entity_id, UUID(str(org_id)))
        if not exists:
            raise HTTPException(status_code=404, detail="Entity not found")
        nw = await L.entity_net_worth(conn, org_id, entity_id)
        # PORTENTRY1 (GAP-PORT-05) — the family rollup that last refreshed this record (status / when / as-of)
        from services import portfolio_refresh as PR

        nw["last_rollup"] = await PR.last_run(conn, org_id, entity_id)
    return {"rows": nw, "permissions": _permissions(request, "internal or own family"),
            "vocabularies": _vocab(request, {"net_worth_lines": V.liability_vocab()["net_worth_lines"],
                                             **_nw_vocab()}, [])}


# ── PORTENTRY2 (MUSE-OLIVO-PERS-01) — what the caller may change on this record ─────────────────────────────────
@router.get("/entities/{entity_id}/permissions")
async def record_permissions(request: Request, entity_id: UUID):
    """The CRM record page's write envelope: one flag per write surface, each from the exact rule of its route
    (services.record_permissions). Internal read (a client never opens a CRM record); 404 for another org's id."""
    from services import record_permissions as RP

    require_internal(request)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        found = await conn.fetchval("SELECT 1 FROM entities WHERE id = $1 AND org_id = $2::uuid "
                                    "AND valid_to IS NULL AND system_to IS NULL", entity_id, str(org_id))
    if not found:
        raise HTTPException(status_code=404, detail="Entity not found")
    return {"entity_id": str(entity_id), **RP.envelope(principal_of(request))}
