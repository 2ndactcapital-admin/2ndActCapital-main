"""Chancery document intake — Phase 1 (batch ROUTE + native EXTRACT).

POST /api/v1/documents accepts ONE OR MORE files in a single multipart request
and drives each through the Phase-1 pipeline (route → native extract) via
``services.chancery_intake.process_document``.

Batch semantics (deliberate Phase-1 simplicity/safety choices):
  * One ``document_drops`` row per request (org_id from the authenticated
    request — NEVER from the body), ``file_count`` = number of files submitted.
  * Each file gets its own ``documents`` row (``drop_id`` = the drop,
    ``sequence_in_drop`` = 1-based position in the order received).
  * Files are processed STRICTLY SEQUENTIALLY — a later file never starts until
    the previous one has finished (success or failure). Not a perf optimisation
    to bypass.
  * One file failing (corrupt PDF, extraction error) does NOT stop the rest —
    its failure is recorded on ITS OWN documents row and the batch continues.
  * When all files are done the drop is marked ``completed`` and the response
    lists EACH document's own outcome (not one overall pass/fail).

The multipart/file-handling mechanics reuse the same pattern as the Sprint-17
entity-document upload (``fastapi.UploadFile`` + ``ensure_user``). Phase 1 does
NOT store bytes to R2 (STORE is a future phase) — bytes are read into memory,
handed to ``process_document``, and discarded.
"""

import uuid as _uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from services.chancery_intake import process_document
from services.database import get_pool
from services.users import ensure_user
from services import vdr_analysis

router = APIRouter(tags=["documents"])

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"

ORG_ID_CLAIMS = (
    "org_id",
    "https://2ndactcapital.com/org_id",
    "https://api.2ndactcapital.com/org_id",
)


def _get_org_id(request: Request) -> str:
    """Caller's org_id from JWT claims, or the default org. Never from the body."""
    claims = getattr(request.state, "user", None) or {}
    for key in ORG_ID_CLAIMS:
        value = claims.get(key)
        if value:
            return value
    return DEFAULT_ORG_ID


async def _document_outcome(conn, doc_id, org_id) -> dict:
    """Assemble a single document's outcome for the batch response."""
    row = await conn.fetchrow(
        """
        SELECT d.id, d.sequence_in_drop, d.original_filename, d.status,
               d.doc_family, d.storage_key,
               e.has_native_text_layer, e.extraction_method, e.page_count
        FROM documents d
        LEFT JOIN document_extractions e ON e.document_id = d.id
        WHERE d.id = $1 AND d.org_id = $2
        """,
        doc_id, org_id,
    )
    status = row["status"] if row else "failed"
    # Post-extraction pipeline states (Phase 2): extraction is no longer terminal
    # — STORE ('stored') and SORT ('sorted' / 'pending_review') advance it.
    extracted_or_beyond = ("extracted", "stored", "sorted", "pending_review")
    # Phase 4: routing now reaches a decision for non-PDF types too, including a
    # clear 'unsupported_format' terminal for a recognised-but-unhandled file.
    routed_states = ("routed", "needs_ocr", "unsupported_format") + extracted_or_beyond
    return {
        "id": str(doc_id),
        "sequence_in_drop": row["sequence_in_drop"] if row else None,
        "original_filename": row["original_filename"] if row else None,
        "status": status,
        # routing produced a decision (not stuck at the initial 'dropped', not 'failed')
        "routed": status in routed_states,
        "extracted": status in extracted_or_beyond,
        "stored": status in ("stored", "sorted", "pending_review"),
        "sorted": status in ("sorted", "pending_review"),
        "doc_family": row["doc_family"] if row else None,
        "storage_key": row["storage_key"] if row else None,
        "has_text_layer": row["has_native_text_layer"] if row else None,
        "extraction_method": row["extraction_method"] if row else None,
        "page_count": row["page_count"] if row else None,
    }


@router.post("/documents", status_code=201)
async def intake_documents(
    request: Request,
    files: list[UploadFile] = File(...),
    is_vdr: bool = Form(False),
):
    """Batch-capable Chancery intake: one drop, N files, processed in order.

    Phase 10: when ``is_vdr`` is true, the caller is marking this drop as a
    Virtual Data Room for a NEW deal. After the drop completes, the aggregate
    VDR analysis runs (``services.vdr_analysis.analyze_drop``) and — only if the
    documents confidently describe a single deal — a pending
    ``vdr_deal_proposals`` row is created. This NEVER auto-creates a deal.
    Reuses the existing drop mechanism entirely; ``is_vdr`` is just a flag on the
    same upload path (no separate VDR upload endpoint).
    """
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")

    org_id = _get_org_id(request)
    pool = await get_pool()

    # 1. One document_drops row for the whole request.
    async with pool.acquire() as conn:
        uploader = await ensure_user(conn, request)
        drop_id = await conn.fetchval(
            """
            INSERT INTO document_drops (org_id, source, file_count, status, created_by)
            VALUES ($1, 'upload', $2, 'processing', $3)
            RETURNING id
            """,
            org_id, len(files), uploader,
        )

    # 2. For each file, IN ORDER: create its documents row, then process it
    #    SEQUENTIALLY (await before moving to the next). A failure on one file
    #    is recorded on its own row and does not stop the batch.
    outcomes: list[dict] = []
    for index, upload in enumerate(files, start=1):
        contents = await upload.read()
        doc_id = _uuid.uuid4()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO documents (
                    id, org_id, original_filename, source, mime_type,
                    status, drop_id, sequence_in_drop, created_by
                ) VALUES ($1, $2, $3, 'upload', $4, 'dropped', $5, $6, $7)
                """,
                doc_id, org_id, upload.filename or f"file-{index}",
                upload.content_type, drop_id, index, uploader,
            )

        try:
            await process_document(doc_id, org_id, contents)
        except Exception as exc:  # noqa: BLE001 — one file's failure must not stop the batch
            print(f"[chancery] intake: process_document crashed for "
                  f"{doc_id} (seq {index}): {type(exc).__name__}: {exc}")
            # Best-effort: record the failure on this document's own row.
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE documents SET status = 'failed', updated_at = now() "
                        "WHERE id = $1 AND org_id = $2",
                        doc_id, org_id,
                    )
            except Exception as inner:  # noqa: BLE001
                print(f"[chancery] intake: failed to mark {doc_id} failed: {inner}")

        async with pool.acquire() as conn:
            outcomes.append(await _document_outcome(conn, doc_id, org_id))

    # 3. All files processed → complete the drop.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE document_drops SET status = 'completed', completed_at = now() "
            "WHERE id = $1 AND org_id = $2",
            drop_id, org_id,
        )

    response = {
        "drop_id": str(drop_id),
        "file_count": len(files),
        "status": "completed",
        "documents": outcomes,
    }

    # 4. VDR intake: aggregate-analyze the whole batch → maybe a deal proposal.
    #    Best-effort: a failure here never fails the (already-completed) upload.
    if is_vdr:
        try:
            response["vdr_analysis"] = await vdr_analysis.analyze_drop(
                pool, org_id, drop_id, created_by=uploader)
        except Exception as exc:  # noqa: BLE001
            print(f"[chancery] VDR analysis failed for drop {drop_id}: "
                  f"{type(exc).__name__}: {exc}")
            response["vdr_analysis"] = {
                "proposal_created": False,
                "proposal_id": None,
                "reason": f"VDR analysis error: {type(exc).__name__}",
            }

    return response
