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
import os
import re

import pdfplumber
from starlette.concurrency import run_in_threadpool

from services import storage
from services.database import get_pool, set_rls_context, reset_rls_context
from services.document_classifier import classify_document

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
# Phase 2 statuses (STORE + SORT). Extraction is NO LONGER terminal — STORE and
# SORT advance a successfully-extracted document further.
STATUS_STORED = "stored"                  # original bytes persisted to R2
STATUS_SORTED = "sorted"                  # classified into an existing category
STATUS_PENDING_REVIEW = "pending_review"  # classifier proposed a NEW category

# Any of these means "native extraction succeeded" (the doc has real text).
EXTRACTED_OR_BEYOND = frozenset({
    STATUS_EXTRACTED, STATUS_STORED, STATUS_SORTED, STATUS_PENDING_REVIEW,
})


# ---------------------------------------------------------------------------
# SORT — map a classifier doc_category code → Chancery doc_family
# ---------------------------------------------------------------------------
# The Chancery design splits documents into two downstream extraction families:
#   TABULAR    — value lives in tables / form fields (K-1s, tax returns,
#                financial statements, subscription & accreditation forms, IDs).
#   NARRATIVE  — value lives in prose legal instruments (formation docs, trusts,
#                wills, estate plans, operating agreements).
# There is NO reference_data 'doc_family' list in the deployed DB (confirmed by
# live introspection 2026-07-30: reference_data has the 12 canonical
# 'doc_category' rows and ZERO 'doc_family' rows), so this mapping is the
# code-level source of truth, keyed by those 12 codes. A code we do not
# recognise (e.g. a newly-ratified category) falls through to None rather than
# being force-fitted into a family.
FAMILY_TABULAR = "tabular"
FAMILY_NARRATIVE = "narrative"
FAMILY_OTHER = "other"

_TABULAR_CATEGORIES = frozenset({
    "k1", "tax_return", "financial_statement", "subscription_doc",
    "accreditation", "id_document",
})
_NARRATIVE_CATEGORIES = frozenset({
    "llc_formation", "trust_instrument", "will", "estate_plan",
    "operating_agreement",
})


def doc_family_for_category(category_code: str | None) -> str | None:
    """Map a canonical doc_category code to its Chancery doc_family.

    Returns 'tabular' / 'narrative' for the two extraction families, 'other' for
    the explicit catch-all category, or None for an unknown/None code (so a code
    we cannot place is never guessed into a family).
    """
    if not category_code:
        return None
    if category_code in _TABULAR_CATEGORIES:
        return FAMILY_TABULAR
    if category_code in _NARRATIVE_CATEGORIES:
        return FAMILY_NARRATIVE
    if category_code == "other":
        return FAMILY_OTHER
    return None


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
# STORE — persist original bytes to R2, versioned (reuses the S17 mechanism)
# ---------------------------------------------------------------------------
def _slugify(value: str) -> str:
    """Filesystem/URL-safe slug for a storage-key path segment."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value or "").strip("-").lower()
    return slug[:80] or "file"


async def store_document(
    pool,
    document_id,
    org_id,
    file_bytes: bytes,
    *,
    original_filename: str | None,
    mime_type: str | None,
    entity_id=None,
) -> str | None:
    """Persist the ORIGINAL bytes to R2 (versioned) and record storage_key.

    Reuses the Sprint-17 R2 mechanism verbatim (``services.storage.upload_bytes``
    → the ``2ndactcapital-docs`` bucket) — NOT a second integration. The object
    key embeds ``document_id`` (unique per documents row), so a re-upload can
    NEVER overwrite a prior stored file. A human-meaningful, 1-based version
    number — counted over the (org, entity, filename) natural key across prior
    STORED rows — is embedded in the key path, mirroring ``entity_documents``'
    own version handling (each version is a distinct object; the prior is
    retained, never clobbered).

    Sets ``documents.storage_key`` and ``status = 'stored'``. Returns the
    storage_key, or None when R2 is not configured (unattended / CI) — in which
    case the document keeps its pre-store status rather than falsely claiming to
    be stored.
    """
    if not os.environ.get("R2_ACCOUNT_ID"):
        print(f"[chancery] store_document: R2 not configured — skipping STORE "
              f"for {document_id}")
        return None

    async with pool.acquire() as conn:
        version = await conn.fetchval(
            """
            SELECT count(*) + 1 FROM documents
            WHERE org_id = $1
              AND original_filename = $2
              AND coalesce(entity_id::text, '') = coalesce($3::text, '')
              AND storage_key IS NOT NULL
              AND id <> $4
            """,
            org_id, original_filename, entity_id, document_id,
        )

    stem, ext = os.path.splitext(original_filename or "")
    entity_seg = str(entity_id) if entity_id else "unfiled"
    storage_key = (
        f"chancery/{org_id}/{entity_seg}/{_slugify(stem)}/v{version}/"
        f"{document_id}{ext.lower()}"
    )

    # boto3 is synchronous — never block the event loop.
    await run_in_threadpool(storage.upload_bytes, storage_key, file_bytes, mime_type)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE documents SET storage_key = $1, status = $2, updated_at = now() "
            "WHERE id = $3",
            storage_key, STATUS_STORED, document_id,
        )
    return storage_key


# ---------------------------------------------------------------------------
# SORT — classify an extracted document, set doc_family / status
# ---------------------------------------------------------------------------
async def sort_document(pool, document_id, org_id, extracted_text: str) -> dict:
    """Classify an extracted document and set ``doc_family`` / ``status``.

    Calls the Sprint-25 open-set classifier (``document_classifier.
    classify_document``):
      * MATCH to an existing category → ``doc_family`` from
        ``doc_family_for_category``; ``status = 'sorted'``.
      * NEW-category proposal → classify_document has ALREADY queued the proposal
        into ``doc_category_proposals`` (the house 'AI proposes, human ratifies'
        review queue). We do NOT invent a doc_family: ``doc_family`` stays NULL
        and ``status = 'pending_review'``.
      * model unavailable (no API key) → classify returns a null category; we
        leave the document's status/doc_family untouched (still 'stored' /
        'extracted') rather than claiming a sort.

    Returns the classifier result dict, augmented with the resolved ``doc_family``.
    """
    async with pool.acquire() as conn:
        result = await classify_document(conn, org_id, extracted_text)

        category = result.get("category_code")
        is_new = result.get("is_new_proposal")

        if is_new:
            await conn.execute(
                "UPDATE documents SET status = $1, updated_at = now() WHERE id = $2",
                STATUS_PENDING_REVIEW, document_id,
            )
            result["doc_family"] = None
        elif category:
            family = doc_family_for_category(category)
            await conn.execute(
                "UPDATE documents SET doc_family = $1, status = $2, "
                "updated_at = now() WHERE id = $3",
                family, STATUS_SORTED, document_id,
            )
            result["doc_family"] = family
        else:
            # No category (model unavailable / failed) — do not claim a sort.
            result["doc_family"] = None
    return result


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
                "SELECT id, org_id, original_filename, mime_type, entity_id, status "
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

            # --- Phase 2: STORE (versioned R2) then SORT (classify) ---
            # STORE first so the durable bytes are never lost, and so the
            # TERMINAL status reflects the SORT outcome: a 'pending_review'
            # proposal (human-review gate) must not be clobbered by a later
            # 'stored' write. Each step degrades independently — a STORE or SORT
            # failure logs and leaves the document at its last good status
            # rather than losing the extraction.
            try:
                await store_document(
                    pool, document_id, org_id, file_bytes,
                    original_filename=doc["original_filename"],
                    mime_type=doc["mime_type"],
                    entity_id=doc["entity_id"],
                )
            except Exception as exc:  # noqa: BLE001 — STORE failure ≠ pipeline failure
                print(f"[chancery] store_document failed for {document_id}: "
                      f"{type(exc).__name__}: {exc}")

            try:
                await sort_document(pool, document_id, org_id,
                                    native["extracted_text"])
            except Exception as exc:  # noqa: BLE001 — SORT failure ≠ pipeline failure
                print(f"[chancery] sort_document failed for {document_id}: "
                      f"{type(exc).__name__}: {exc}")
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
