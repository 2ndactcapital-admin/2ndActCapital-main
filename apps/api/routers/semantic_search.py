"""Chancery Phase 11b — semantic search (RETRIEVE) endpoint.

    GET /api/v1/semantic-search?q=...&limit=20

A DELIBERATELY SEPARATE action from Phase 9's keyword ``/document-search`` — the
Phase-9 layers explicitly label themselves "NOT semantic/vector search (that is
Phase 11)". This endpoint embeds the query via the org's configured embedding
provider (currently always Voyage), ranks documents by pgvector cosine
similarity, and enforces the SAME visibility engines as everything else (staff
visibility OR member resolve_entity_set, then the restricted-access filter)
before returning citations back to the source documents.

``org_id`` and the acting user come from the JWT (never the request body); the
caller's role decides the staff vs member visibility path.
"""

from fastapi import APIRouter, HTTPException, Request

from routers.entities import get_org_id
from services import document_embedding
from services import document_linkage as dl
from services.database import get_pool
from services.authz import is_staff
from services.users import ensure_user

router = APIRouter(tags=["semantic-search"])


@router.get("/semantic-search")
async def semantic_search(request: Request, q: str = "", limit: int = 20):
    query = (q or "").strip()
    if not query:
        return {"query": "", "count": 0, "results": []}

    org_id = get_org_id(request)
    staff = is_staff(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)

    try:
        results = await document_embedding.semantic_search(
            pool, org_id, user_id, query, is_staff=staff, limit=limit
        )
    except document_embedding.EmbeddingProviderNotEnabled as exc:
        # A non-Voyage provider slipped through (should be impossible given the
        # write-time validation) — surface the clean, specific message.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except document_embedding.EmbeddingUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # BUGFIX1 (R2-DOC-03) — say which visible documents matching the words have
    # NO embedding yet, instead of silently leaving them out: every attachment
    # (deal / entity / SPV files are never embedded) and any vault document
    # without a document_embeddings row. Same caller-scoped keyword search, so
    # nothing outside the caller's visibility can appear here either.
    async with pool.acquire() as conn:
        keyword = await dl.search_documents(
            conn, org_id, query, pool=pool, user_id=user_id, is_staff=staff, limit=50)
        vault_ids = [r["document_id"] for r in keyword if r.get("source") == "vault"]
        embedded = {str(r["document_id"]) for r in await conn.fetch(
            "SELECT DISTINCT document_id FROM document_embeddings "
            "WHERE org_id = $1 AND document_id = ANY($2::uuid[])", org_id, vault_ids)} if vault_ids else set()
    not_indexed = [
        {**r, "index_note": NOT_INDEXED_NOTE}
        for r in keyword
        if r.get("source") != "vault" or r["document_id"] not in embedded
    ]
    return {"query": query, "count": len(results), "results": results,
            "not_indexed": not_indexed, "not_indexed_note": NOT_INDEXED_NOTE}


NOT_INDEXED_NOTE = "Not yet indexed for meaning search — found by its words only."
