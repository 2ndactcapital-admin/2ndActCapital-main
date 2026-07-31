"""Chancery Phase 6 — document review / confirm endpoints.

Thin HTTP layer over ``services.document_review``. Auth + org-scoping follow the
SAME pattern as every other Chancery router (``routers.documents`` /
``routers.document_links``): a valid JWT is enforced by the global middleware in
``main.py``; ``org_id`` comes from the JWT claims via
``routers.entities.get_org_id`` and is NEVER read from the request body; the
acting user is resolved with ``services.users.ensure_user`` (used as
``corrected_by`` / ``confirmed_by``). Every DB call runs on the RLS-scoped pool.

Routes (mounted under ``/api/v1``):
  GET  /documents/{document_id}/review        one-call review payload
  POST /documents/{document_id}/corrections   correct one mapped field
  POST /documents/{document_id}/classification-correction  correct doc_category
  POST /documents/{document_id}/confirm       mark reviewed/confirmed
  GET  /documents/{document_id}/download       presigned R2 URL ("see attached")
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from routers.entities import get_org_id
from services import chancery_workflow_bridge
from services import document_review as review
from services import storage
from services.database import get_pool
from services.users import ensure_user

router = APIRouter(tags=["document-review"])


class FieldCorrection(BaseModel):
    field_name: str = Field(..., min_length=1)
    # A string keeps monetary values EXACT (no float coercion). The frontend
    # always sends the edited text verbatim.
    corrected_value: str
    notes: str | None = None


class ClassificationCorrection(BaseModel):
    # The doc_category code the classifier assigned (what is being corrected);
    # optional because a document may never have been classified.
    original_category: str | None = None
    corrected_category: str = Field(..., min_length=1)
    notes: str | None = None


def _raise(err: "review.ReviewError"):
    raise HTTPException(status_code=err.status_code, detail=err.detail)


@router.get("/documents/{document_id}/review")
async def get_review(document_id: UUID, request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await review.get_review_payload(conn, org_id, document_id)
        except review.ReviewError as err:
            _raise(err)


@router.post("/documents/{document_id}/corrections", status_code=201)
async def submit_correction(document_id: UUID, body: FieldCorrection, request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        try:
            return await review.submit_field_correction(
                conn, org_id, document_id,
                field_name=body.field_name,
                corrected_value=body.corrected_value,
                corrected_by=user_id,
                notes=body.notes,
            )
        except review.ReviewError as err:
            _raise(err)


@router.post("/documents/{document_id}/classification-correction", status_code=201)
async def submit_classification_correction(
    document_id: UUID, body: ClassificationCorrection, request: Request
):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        try:
            return await review.submit_classification_correction(
                conn, org_id, document_id,
                original_category=body.original_category,
                corrected_category=body.corrected_category,
                corrected_by=user_id,
                notes=body.notes,
            )
        except review.ReviewError as err:
            _raise(err)


@router.post("/documents/{document_id}/confirm")
async def confirm(document_id: UUID, request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        try:
            result = await review.confirm_document(
                conn, org_id, document_id, confirmed_by=user_id)
        except review.ReviewError as err:
            _raise(err)
            return  # unreachable — _raise always raises; keeps type-checkers happy

    # Chancery Phase 7 — fire 'document_confirmed' event triggers AFTER the
    # status is successfully set to 'confirmed' (a failed confirm above never
    # reaches here, so a failed confirm can never start a workflow). The bridge
    # is a graceful no-op when no trigger is configured and never raises, so it
    # cannot break an already-successful confirm. It automates only WHICH runs
    # start — every started run still honours each step's autonomy tier.
    await chancery_workflow_bridge.fire_document_confirmed_triggers(
        pool, org_id, document_id, started_by=user_id)
    return result


@router.get("/documents/{document_id}/download")
async def download(document_id: UUID, request: Request,
                   expires: int = Query(3600, ge=60, le=86400)):
    """Presigned R2 URL for the stored original — the "see attached document"
    fallback when no on-page coordinates exist. 404 when the document has no
    stored file (e.g. Phase-1-only intake that never reached STORE)."""
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT storage_key FROM documents WHERE id = $1 AND org_id = $2",
            document_id, org_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    if not row["storage_key"]:
        raise HTTPException(status_code=404, detail="Document has no stored file")
    url = await run_in_threadpool(storage.get_signed_url, row["storage_key"], expires)
    return {"url": url, "expires_in": expires}
