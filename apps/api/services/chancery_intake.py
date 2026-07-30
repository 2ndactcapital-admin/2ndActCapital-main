"""Chancery intake — Phase 1: ROUTE + native EXTRACT.

This is Phase 1 of the 6-phase Chancery document pipeline. It covers only:

  ROUTE    A *deterministic* (no AI, no network) check of whether a PDF has a
           real, native text layer, or is an image/scan with no extractable
           text. This runs BEFORE any extraction logic.
  EXTRACT  Text + per-page tables for the text-native case ONLY, via pdfplumber
           (confirmed reliable in Phase-1 discovery — exact text round-trips,
           empty string for a text-less page, clean exception on garbage bytes).

Explicitly OUT OF SCOPE this phase (do NOT add here):
  SORT (S25 document classifier), STORE (R2), OCR/TABULAR/NARRATIVE extraction
  via Textract, INDEX (embeddings/vectors), RETRIEVE. A scanned / image-only PDF
  is DETECTED by ROUTE and marked ``needs_ocr`` for a FUTURE Textract phase — it
  is never fed to native extraction and never silently mishandled.

Bytes are NOT persisted in Phase 1 (STORE/R2 is out of scope), so
``process_document`` receives the file bytes from its caller rather than loading
them from storage — but it still loads the ``documents`` row (to validate the
document exists in-org and to drive its status transitions).
"""

import io
import json

import pdfplumber

from services.database import get_pool, set_rls_context, reset_rls_context

# extraction_method markers written to document_extractions.extraction_method
# (free text — no CHECK constraint on the column, confirmed via introspection).
METHOD_NATIVE = "native_pdfplumber"   # text-native PDF, real text extracted
METHOD_OCR_PENDING = "ocr_pending"    # scan / image-only → future Textract phase
METHOD_FAILED = "failed"              # corrupt / unreadable PDF
METHOD_PENDING = "pending"            # transient: routed, extraction not yet run

# documents.status values used by this phase (also free text, default 'dropped').
STATUS_ROUTED = "routed"
STATUS_EXTRACTED = "extracted"
STATUS_NEEDS_OCR = "needs_ocr"
STATUS_FAILED = "failed"


# ---------------------------------------------------------------------------
# ROUTE — deterministic native-text-layer detection (no AI, no network)
# ---------------------------------------------------------------------------
def route_document(file_bytes: bytes) -> dict:
    """Deterministically decide whether ``file_bytes`` is a text-native PDF.

    Returns::

        {"has_text_layer": bool, "page_count": int,
         "valid_pdf": bool, "error": str | None}

    A PDF is "text-native" when at least one page yields non-whitespace text
    from its embedded text layer. A scanned / image-only PDF opens fine but
    every page's ``extract_text`` is empty → ``has_text_layer=False`` (route it
    to OCR in a future phase). A corrupt / non-PDF input does NOT crash — it
    returns ``valid_pdf=False`` with a clear ``error`` string.
    """
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            page_count = len(pdf.pages)
            has_text_layer = False
            for page in pdf.pages:
                text = page.extract_text() or ""
                if text.strip():
                    has_text_layer = True
                    break
        return {
            "has_text_layer": has_text_layer,
            "page_count": page_count,
            "valid_pdf": True,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — any parse failure is a routing result, not a crash
        return {
            "has_text_layer": False,
            "page_count": 0,
            "valid_pdf": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


# ---------------------------------------------------------------------------
# EXTRACT (native only) — text + per-page tables for a text-native PDF
# ---------------------------------------------------------------------------
def extract_native(file_bytes: bytes) -> dict:
    """Extract full text and per-page tables from a TEXT-NATIVE PDF.

    Returns ``{"extracted_text": str, "extracted_tables": list, "page_count": int}``.
    ``extracted_tables`` is a list of ``{"page": int, "tables": [[...rows...]]}``
    entries, one per page that actually contained a table. Callers must only run
    this on a PDF that ``route_document`` reported as text-native — a scan will
    yield empty text here, which is why routing gates it.
    """
    page_texts: list[str] = []
    tables_by_page: list[dict] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        page_count = len(pdf.pages)
        for index, page in enumerate(pdf.pages, start=1):
            page_texts.append(page.extract_text() or "")
            page_tables = page.extract_tables() or []
            if page_tables:
                tables_by_page.append({"page": index, "tables": page_tables})
    return {
        "extracted_text": "\n\n".join(page_texts).strip(),
        "extracted_tables": tables_by_page,
        "page_count": page_count,
    }


# ---------------------------------------------------------------------------
# ORCHESTRATION — process a single already-persisted documents row
# ---------------------------------------------------------------------------
async def process_document(document_id, org_id, file_bytes: bytes) -> None:
    """Route, then (if text-native) extract, a single ``documents`` row.

    Steps:
      1. Load the ``documents`` row (validates it exists in ``org_id``).
      2. ``route_document`` → write a ``document_extractions`` row recording the
         routing decision; set ``documents.status = 'routed'``.
      3. If text-native → ``extract_native`` and UPDATE that same extraction row
         with the real content; ``documents.status = 'extracted'``.
         If scan (valid PDF, no text) → ``documents.status = 'needs_ocr'``
         (Textract's job, a future phase — native extraction is NOT attempted).
         If corrupt/unreadable → ``documents.status = 'failed'``.

    Never processes bytes for a non-text-native PDF. Returns None. The org RLS
    context is set from the passed ``org_id`` so this is safe to call standalone
    (e.g. from a future worker) as well as from within a request whose middleware
    already established the same context.
    """
    tokens = set_rls_context(org_id, False)
    try:
        pool = await get_pool()

        # 1. Load the documents row (existence + in-org validation).
        async with pool.acquire() as conn:
            doc = await conn.fetchrow(
                "SELECT id, org_id, original_filename, status "
                "FROM documents WHERE id = $1 AND org_id = $2",
                document_id, org_id,
            )
            if doc is None:
                # Nothing to do — the row must exist (the endpoint just created
                # it). Fail loud in logs, but do not raise into the batch loop.
                print(f"[chancery] process_document: document {document_id} "
                      f"not found in org {org_id}")
                return

        # 2. ROUTE (deterministic, in-process — no AI, no network).
        routing = route_document(file_bytes)

        # Record the routing decision on its OWN document_extractions row.
        async with pool.acquire() as conn:
            extraction_id = await conn.fetchval(
                """
                INSERT INTO document_extractions (
                    document_id, org_id, extraction_method,
                    has_native_text_layer, page_count
                ) VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                document_id, org_id, METHOD_PENDING,
                routing["has_text_layer"],
                routing["page_count"] or None,
            )
            await conn.execute(
                "UPDATE documents SET status = $1, updated_at = now() WHERE id = $2",
                STATUS_ROUTED, document_id,
            )

        # 3. Branch on the routing decision.
        if not routing["valid_pdf"]:
            # Corrupt / unreadable — do NOT attempt extraction. Record failure.
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE document_extractions SET extraction_method = $1 WHERE id = $2",
                    METHOD_FAILED, extraction_id,
                )
                await conn.execute(
                    "UPDATE documents SET status = $1, updated_at = now() WHERE id = $2",
                    STATUS_FAILED, document_id,
                )
            return

        if routing["has_text_layer"]:
            # Text-native → native extraction.
            try:
                native = extract_native(file_bytes)
            except Exception as exc:  # noqa: BLE001 — extraction failure ≠ batch failure
                print(f"[chancery] extract_native failed for {document_id}: "
                      f"{type(exc).__name__}: {exc}")
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE document_extractions SET extraction_method = $1 WHERE id = $2",
                        METHOD_FAILED, extraction_id,
                    )
                    await conn.execute(
                        "UPDATE documents SET status = $1, updated_at = now() WHERE id = $2",
                        STATUS_FAILED, document_id,
                    )
                return

            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE document_extractions
                    SET extraction_method = $1,
                        extracted_text = $2,
                        extracted_tables = $3::jsonb,
                        page_count = $4
                    WHERE id = $5
                    """,
                    METHOD_NATIVE,
                    native["extracted_text"],
                    json.dumps(native["extracted_tables"]),
                    native["page_count"],
                    extraction_id,
                )
                await conn.execute(
                    "UPDATE documents SET status = $1, updated_at = now() WHERE id = $2",
                    STATUS_EXTRACTED, document_id,
                )
            return

        # Valid PDF but no text layer → scan/image-only → future Textract phase.
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE document_extractions SET extraction_method = $1 WHERE id = $2",
                METHOD_OCR_PENDING, extraction_id,
            )
            await conn.execute(
                "UPDATE documents SET status = $1, updated_at = now() WHERE id = $2",
                STATUS_NEEDS_OCR, document_id,
            )
    finally:
        reset_rls_context(tokens)
