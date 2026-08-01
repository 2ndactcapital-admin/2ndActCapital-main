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
from services.database import get_pool
from services.permissions import is_staff
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

    return {"query": query, "count": len(results), "results": results}
