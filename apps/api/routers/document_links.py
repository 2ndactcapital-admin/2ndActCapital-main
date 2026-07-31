"""Chancery Phase 5 — document linkage endpoints.

Thin HTTP layer over ``services.document_linkage``. Auth + org-scoping follow the
same pattern as the other Chancery routers (``routers.documents``): a valid JWT is
enforced by the global middleware in ``main.py``; ``org_id`` comes from the JWT
claims via ``routers.entities.get_org_id`` and is NEVER read from the request body;
the acting user is resolved with ``services.users.ensure_user`` (used as
``created_by`` / ``reviewed_by``). Every DB call runs on the RLS-scoped pool.

Routes (mounted under ``/api/v1``):
  POST   /documents/{document_id}/entity-links            link to one+ entities
  DELETE /documents/{document_id}/entity-links/{entity_id} unlink an entity
  POST   /documents/{document_id}/record-links            link to a generic record
  GET    /documents/{document_id}/links                   all links for a document
  POST   /documents/{document_id}/auto-link-k1            run K-1 auto-linkage
  GET    /entities/{entity_id}/documents                  documents linked to entity
  GET    /document-link-proposals                         pending proposals (org)
  POST   /document-link-proposals/{proposal_id}/approve   approve → create + link
  POST   /document-link-proposals/{proposal_id}/reject    reject
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from routers.entities import get_org_id
from schemas.entities import EntityType
from services.database import get_pool
from services.users import ensure_user
from services import document_linkage as dl

router = APIRouter(tags=["document-links"])


# ── request bodies ───────────────────────────────────────────────────────────
class EntityLinkCreate(BaseModel):
    entity_ids: list[UUID] = Field(..., min_length=1)
    link_role: str | None = None


class RecordLinkCreate(BaseModel):
    record_type: str = Field(..., min_length=1)
    record_id: UUID


class ProposalApprove(BaseModel):
    # Optional: reviewer may point at an existing entity to link to. When omitted,
    # approval routes through the picker find-or-create path (Task 1a).
    entity_id: UUID | None = None
    entity_type: EntityType = EntityType.individual


def _raise(err: "dl.LinkageError"):
    raise HTTPException(status_code=err.status_code, detail=err.detail)


# ── Task 2 — manual linkage ─────────────────────────────────────────────────
@router.post("/documents/{document_id}/entity-links", status_code=201)
async def link_entities(document_id: UUID, body: EntityLinkCreate, request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        try:
            results = await dl.link_document_to_entities(
                conn, org_id, document_id, body.entity_ids,
                created_by=user_id, link_role=body.link_role,
            )
        except dl.LinkageError as err:
            _raise(err)
    return {"document_id": str(document_id), "links": results}


@router.delete("/documents/{document_id}/entity-links/{entity_id}")
async def unlink_entity(document_id: UUID, entity_id: UUID, request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await dl.unlink_document_from_entity(
            conn, org_id, document_id, entity_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Link not found")
    return {"document_id": str(document_id), "entity_id": str(entity_id),
            "unlinked": True}


@router.post("/documents/{document_id}/record-links", status_code=201)
async def link_record(document_id: UUID, body: RecordLinkCreate, request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        try:
            result = await dl.link_document_to_record(
                conn, org_id, document_id, body.record_type, body.record_id,
                created_by=user_id,
            )
        except dl.LinkageError as err:
            _raise(err)
    return {"document_id": str(document_id), "link": result}


@router.get("/documents/{document_id}/links")
async def get_document_links(document_id: UUID, request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await dl.list_document_links(conn, org_id, document_id)
        except dl.LinkageError as err:
            _raise(err)


@router.get("/entities/{entity_id}/documents")
async def get_entity_documents(entity_id: UUID, request: Request):
    """Documents linked to an entity — powers Phase-9 contextual surfacing.

    NOTE (Phase-9 discovery): this exact path is ALSO declared by
    ``routers.entity_documents`` (the Sprint-17 CRM R2 upload subsystem), which is
    mounted FIRST in ``main.py`` and therefore wins the match — so this handler is
    effectively shadowed at the HTTP layer. The Phase-9 panel deliberately does NOT
    rely on it; it calls the collision-free generic route below
    (``/records/{record_type}/{record_id}/documents`` with record_type='entity').
    Kept for API back-compat and because the underlying service function is the one
    the panel/verify exercise directly."""
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            documents = await dl.list_documents_for_entity(conn, org_id, entity_id)
        except dl.LinkageError as err:
            _raise(err)
    return {"entity_id": str(entity_id), "documents": documents}


# ── Phase 9 — the reusable Documents panel + basic search ────────────────────
@router.get("/records/{record_type}/{record_id}/documents")
async def get_record_documents(record_type: str, record_id: UUID, request: Request):
    """Documents linked to ANY record — the single, collision-free query behind the
    Phase-9 reusable Documents panel. ``record_type='entity'`` uses Phase-5's
    entity-link query; every other type (spv / deal / transaction / …) uses the
    generic ``document_record_links`` query. A record with no linked documents
    returns an empty list (clean empty state), not an error."""
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            documents = await dl.list_documents_for_panel(
                conn, org_id, record_type, record_id)
        except dl.LinkageError as err:
            _raise(err)
    return {
        "record_type": record_type,
        "record_id": str(record_id),
        "documents": documents,
    }


@router.get("/document-search")
async def search_documents(request: Request, q: str = "", limit: int = 50):
    """Basic (NON-semantic) document search — filename, category (doc_family), and
    an ILIKE substring match against extracted text. This is NOT the Phase-11
    semantic/vector search; it is plain metadata/text substring matching."""
    org_id = get_org_id(request)
    limit = max(1, min(int(limit or 50), 200))
    pool = await get_pool()
    async with pool.acquire() as conn:
        results = await dl.search_documents(conn, org_id, q, limit=limit)
    return {"query": q, "count": len(results), "results": results}


# ── Task 3 — K-1 auto-linkage trigger ───────────────────────────────────────
@router.post("/documents/{document_id}/auto-link-k1")
async def auto_link_k1(document_id: UUID, request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await dl.auto_link_k1_document(conn, org_id, document_id)
        except dl.LinkageError as err:
            _raise(err)


# ── Task 4 — proposal review ─────────────────────────────────────────────────
@router.get("/document-link-proposals")
async def list_proposals(request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        proposals = await dl.list_pending_proposals(conn, org_id)
    return {"proposals": proposals}


@router.post("/document-link-proposals/{proposal_id}/approve")
async def approve_proposal(proposal_id: UUID, request: Request,
                           body: ProposalApprove | None = None):
    org_id = get_org_id(request)
    body = body or ProposalApprove()
    pool = await get_pool()
    async with pool.acquire() as conn:
        reviewer = await ensure_user(conn, request)
        try:
            return await dl.approve_proposal(
                conn, org_id, proposal_id,
                reviewed_by=reviewer,
                entity_id=body.entity_id,
                entity_type=body.entity_type.value,
            )
        except dl.LinkageError as err:
            _raise(err)


@router.post("/document-link-proposals/{proposal_id}/reject")
async def reject_proposal(proposal_id: UUID, request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        reviewer = await ensure_user(conn, request)
        try:
            return await dl.reject_proposal(
                conn, org_id, proposal_id, reviewed_by=reviewer)
        except dl.LinkageError as err:
            _raise(err)
