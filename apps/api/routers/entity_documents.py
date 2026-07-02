"""Entity documents endpoints — Sprint 17.

Handles upload, versioning, listing, patching status/tags, and R2 signed
download URLs for CRM entity documents.
"""

import os
import uuid as _uuid

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from services.database import get_pool
from services.storage import get_signed_url, upload_bytes
from services.users import ensure_user

router = APIRouter(tags=["entity-documents"])

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_BUCKET = os.environ.get("R2_BUCKET_NAME", "2ndactcapital-docs")

ORG_ID_CLAIMS = (
    "org_id",
    "https://2ndactcapital.com/org_id",
    "https://api.2ndactcapital.com/org_id",
)


def _get_org_id(request: Request) -> str:
    claims = getattr(request.state, "user", None) or {}
    for key in ORG_ID_CLAIMS:
        value = claims.get(key)
        if value:
            return value
    return DEFAULT_ORG_ID


# Deployed column names (storage_key / content_type / file_size — no r2_bucket)
_DOC_FIELDS = (
    "d.id, d.org_id, d.entity_id, d.title, d.doc_category, "
    "d.file_name, d.content_type, d.file_size, d.storage_key, "
    "d.version, d.supersedes_id, d.status, d.uploaded_by, d.created_at, d.updated_at"
)

_DOC_GROUP = (
    "d.id, d.org_id, d.entity_id, d.title, d.doc_category, "
    "d.file_name, d.content_type, d.file_size, d.storage_key, "
    "d.version, d.supersedes_id, d.status, d.uploaded_by, d.created_at, d.updated_at"
)


async def _doc_with_tags(conn, doc_id, org_id) -> dict | None:
    row = await conn.fetchrow(
        f"""
        SELECT {_DOC_FIELDS},
               COALESCE(
                 array_agg(t.tag ORDER BY t.tag) FILTER (WHERE t.tag IS NOT NULL),
                 ARRAY[]::text[]
               ) AS tags
        FROM entity_documents d
        LEFT JOIN entity_document_tags t ON t.document_id = d.id
        WHERE d.id = $1 AND d.org_id = $2
        GROUP BY {_DOC_GROUP}
        """,
        doc_id, org_id,
    )
    return dict(row) if row else None


async def _assert_entity(conn, org_id: str, entity_id):
    found = await conn.fetchval(
        """
        SELECT 1 FROM entities
        WHERE id = $1 AND org_id = $2
          AND valid_to IS NULL AND system_to IS NULL
        """,
        entity_id, org_id,
    )
    if not found:
        raise HTTPException(status_code=404, detail="Entity not found")


# ---------------------------------------------------------------------------
# POST /entities/{entity_id}/documents — upload a new document
# ---------------------------------------------------------------------------
@router.post("/entities/{entity_id}/documents", status_code=201)
async def upload_document(
    request: Request,
    entity_id: _uuid.UUID,
    title: str = Form(...),
    doc_category: str = Form(...),
    tags: list[str] = Form(default=[]),
    file: UploadFile = File(...),
):
    org_id = _get_org_id(request)
    contents = await file.read()
    file_ext = os.path.splitext(file.filename or "")[-1].lower()
    doc_id = _uuid.uuid4()
    storage_key = f"entity-docs/{entity_id}/{doc_id}{file_ext}"

    await run_in_threadpool(upload_bytes, storage_key, contents, file.content_type)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _assert_entity(conn, org_id, entity_id)
        uploader = await ensure_user(conn, request)

        row = await conn.fetchrow(
            """
            INSERT INTO entity_documents (
                id, org_id, entity_id, title, doc_category,
                file_name, content_type, file_size,
                storage_key, version, status, uploaded_by
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 1, 'active', $10)
            RETURNING id, org_id, entity_id, title, doc_category,
                      file_name, content_type, file_size, storage_key,
                      version, supersedes_id, status, uploaded_by, created_at, updated_at
            """,
            doc_id, org_id, entity_id, title, doc_category,
            file.filename, file.content_type, len(contents),
            storage_key, uploader,
        )

        clean_tags = [t.strip() for t in tags if t.strip()]
        if clean_tags:
            await conn.executemany(
                "INSERT INTO entity_document_tags (org_id, document_id, tag, is_fixed) "
                "VALUES ($1, $2, $3, false) ON CONFLICT DO NOTHING",
                [(org_id, doc_id, tag) for tag in clean_tags],
            )

    result = dict(row)
    result["tags"] = clean_tags
    return result


# ---------------------------------------------------------------------------
# POST /entities/{entity_id}/documents/{doc_id}/version — upload new version
# ---------------------------------------------------------------------------
@router.post("/entities/{entity_id}/documents/{doc_id}/version", status_code=201)
async def new_document_version(
    request: Request,
    entity_id: _uuid.UUID,
    doc_id: _uuid.UUID,
    title: str = Form(...),
    doc_category: str = Form(...),
    tags: list[str] = Form(default=[]),
    file: UploadFile = File(...),
):
    org_id = _get_org_id(request)
    contents = await file.read()
    file_ext = os.path.splitext(file.filename or "")[-1].lower()
    new_doc_id = _uuid.uuid4()
    storage_key = f"entity-docs/{entity_id}/{new_doc_id}{file_ext}"

    await run_in_threadpool(upload_bytes, storage_key, contents, file.content_type)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await _assert_entity(conn, org_id, entity_id)

        prior = await conn.fetchrow(
            "SELECT version FROM entity_documents WHERE id=$1 AND org_id=$2 AND entity_id=$3",
            doc_id, org_id, entity_id,
        )
        if not prior:
            raise HTTPException(status_code=404, detail="Document not found")

        uploader = await ensure_user(conn, request)

        # Mark prior version deprecated
        await conn.execute(
            "UPDATE entity_documents SET status='deprecated', updated_at=now() "
            "WHERE id=$1 AND org_id=$2",
            doc_id, org_id,
        )

        row = await conn.fetchrow(
            """
            INSERT INTO entity_documents (
                id, org_id, entity_id, title, doc_category,
                file_name, content_type, file_size,
                storage_key, version, supersedes_id, status, uploaded_by
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'active', $12)
            RETURNING id, org_id, entity_id, title, doc_category,
                      file_name, content_type, file_size, storage_key,
                      version, supersedes_id, status, uploaded_by, created_at, updated_at
            """,
            new_doc_id, org_id, entity_id, title, doc_category,
            file.filename, file.content_type, len(contents),
            storage_key, prior["version"] + 1, doc_id, uploader,
        )

        clean_tags = [t.strip() for t in tags if t.strip()]
        if clean_tags:
            await conn.executemany(
                "INSERT INTO entity_document_tags (org_id, document_id, tag, is_fixed) "
                "VALUES ($1, $2, $3, false) ON CONFLICT DO NOTHING",
                [(org_id, new_doc_id, tag) for tag in clean_tags],
            )

    result = dict(row)
    result["tags"] = clean_tags
    return result


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/documents — list documents with optional filters
# ---------------------------------------------------------------------------
@router.get("/entities/{entity_id}/documents")
async def list_documents(
    request: Request,
    entity_id: _uuid.UUID,
    status: str | None = Query(None),
    category: str | None = Query(None),
    tag: str | None = Query(None),
):
    org_id = _get_org_id(request)
    pool = await get_pool()

    conditions = ["d.entity_id = $1", "d.org_id = $2"]
    params: list = [entity_id, org_id]

    if status:
        params.append(status)
        conditions.append(f"d.status = ${len(params)}")
    if category:
        params.append(category)
        conditions.append(f"d.doc_category = ${len(params)}")
    if tag:
        params.append(tag)
        conditions.append(
            f"EXISTS (SELECT 1 FROM entity_document_tags t2 "
            f"WHERE t2.document_id = d.id AND t2.tag = ${len(params)})"
        )

    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""
        SELECT {_DOC_FIELDS},
               COALESCE(
                 array_agg(t.tag ORDER BY t.tag) FILTER (WHERE t.tag IS NOT NULL),
                 ARRAY[]::text[]
               ) AS tags
        FROM entity_documents d
        LEFT JOIN entity_document_tags t ON t.document_id = d.id
        WHERE {where}
        GROUP BY {_DOC_GROUP}
        ORDER BY d.version DESC, d.created_at DESC
        """,
        *params,
    )

    return {"items": [dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# PATCH /entities/{entity_id}/documents/{doc_id} — update status / tags
# ---------------------------------------------------------------------------
@router.patch("/entities/{entity_id}/documents/{doc_id}")
async def patch_document(
    request: Request,
    entity_id: _uuid.UUID,
    doc_id: _uuid.UUID,
    body: dict,
):
    org_id = _get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM entity_documents WHERE id=$1 AND org_id=$2 AND entity_id=$3",
            doc_id, org_id, entity_id,
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Document not found")

        new_status = body.get("status")
        if new_status:
            await conn.execute(
                "UPDATE entity_documents SET status=$1, updated_at=now() WHERE id=$2",
                new_status, doc_id,
            )

        add_tags = [t.strip() for t in (body.get("add_tags") or []) if t.strip()]
        remove_tags = [t.strip() for t in (body.get("remove_tags") or []) if t.strip()]

        if add_tags:
            await conn.executemany(
                "INSERT INTO entity_document_tags (org_id, document_id, tag, is_fixed) "
                "VALUES ($1, $2, $3, false) ON CONFLICT DO NOTHING",
                [(org_id, doc_id, tag) for tag in add_tags],
            )
        if remove_tags:
            await conn.execute(
                "DELETE FROM entity_document_tags WHERE document_id=$1 AND tag=ANY($2::text[])",
                doc_id, remove_tags,
            )

        result = await _doc_with_tags(conn, doc_id, org_id)

    return result


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/documents/{doc_id}/download — presigned R2 URL
# ---------------------------------------------------------------------------
@router.get("/entities/{entity_id}/documents/{doc_id}/download")
async def download_document(
    request: Request,
    entity_id: _uuid.UUID,
    doc_id: _uuid.UUID,
    expires: int = Query(3600, ge=60, le=86400),
):
    org_id = _get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT storage_key FROM entity_documents "
            "WHERE id=$1 AND org_id=$2 AND entity_id=$3",
            doc_id, org_id, entity_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")

    url = await run_in_threadpool(
        get_signed_url, row["storage_key"], expires, DEFAULT_BUCKET
    )
    return {"url": url, "expires_in": expires}
