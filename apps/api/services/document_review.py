"""Chancery Phase 6 — document review / confirm.

The service layer behind ``routers.document_review``. It assembles the one-call
REVIEW PAYLOAD for a single document, records a human field CORRECTION (both a
``document_field_corrections`` audit row AND the corrected value on the real
``document_template_extractions.mapped_fields`` so the correction becomes the
system of record), and CONFIRMS a reviewed document.

SOURCE-LOCATION HONESTY (Task 1 findings — verified against the REAL code, not
the libraries' theoretical capability):

  * TEXTRACT path (``services.textract.parse_analyze_blocks``): AWS AnalyzeDocument
    returns ``Geometry.BoundingBox`` on every block BY DEFAULT, but the parser
    keeps ONLY ``Text`` (LINE text, KEY/VALUE text, table CELL text) — the
    Geometry is discarded BEFORE ``raw_extraction`` is built and stored. So no
    per-field coordinates survive in the DB.
  * NATIVE path (``services.chancery_intake.extract_native``): pdfplumber's
    ``page.extract_text()`` / ``page.extract_tables()`` return plain strings only.
    pdfplumber CAN expose char/word bounding boxes (``page.chars``), but Phase 1
    never captured them. Only page-level granularity exists (per-table ``page``
    index + ``page_count``), and mapped fields are not tied to a page.

Conclusion: real source coordinates are available for NEITHER path. The payload
therefore reports ``coordinates_available: false`` and degrades to a page
reference ("see attached document") — it NEVER fabricates a highlight overlay.

CONFIDENCE HONESTY: ``map_k1_fields`` produces a flat ``{field: value_string}``
map with NO per-field confidence score, and the classifier does not attach one to
mapped_fields. The payload reports ``confidence_available: false`` and every
field's ``confidence`` is ``None`` — no invented score.

Every monetary value stays an EXACT string end-to-end: mapped_fields already
holds decimal strings, corrections are stored as text (``corrected_value`` is a
text column) and written back with ``to_jsonb($text)`` so no float ever appears.
"""

import json

from services import document_linkage as dl


class ReviewError(Exception):
    """A review/correction/confirm operation failed with an HTTP-shaped reason."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


CONFIRMED_STATUS = "confirmed"


def _decode_jsonb(value):
    """asyncpg returns jsonb as a JSON TEXT string (no codec is registered on the
    pool). Decode to a Python object; pass through anything already decoded."""
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


async def _document_row(conn, org_id, document_id):
    return await conn.fetchrow(
        """
        SELECT id, org_id, entity_id, original_filename, source, mime_type,
               storage_key, status, doc_family, retention_classification,
               confirmed_by, confirmed_at, created_at, updated_at
        FROM documents
        WHERE id = $1 AND org_id = $2
        """,
        document_id, org_id,
    )


async def _latest_extraction(conn, org_id, document_id):
    return await conn.fetchrow(
        """
        SELECT id, extraction_method, has_native_text_layer, extracted_text,
               extracted_tables, page_count, created_at
        FROM document_extractions
        WHERE document_id = $1 AND org_id = $2
        ORDER BY created_at DESC
        LIMIT 1
        """,
        document_id, org_id,
    )


async def _latest_template_extraction(conn, org_id, document_id):
    return await conn.fetchrow(
        """
        SELECT id, template_type, extraction_source, mapped_fields,
               reviewed_by, reviewed_at, created_at
        FROM document_template_extractions
        WHERE document_id = $1 AND org_id = $2
        ORDER BY created_at DESC
        LIMIT 1
        """,
        document_id, org_id,
    )


def _field_source_location(page_count):
    """Per-field source location — HONEST: no coordinates exist for either
    extraction path, so we degrade to a page reference the UI can show as
    "see attached document" instead of a fabricated highlight."""
    return {
        "coordinates_available": False,
        "mode": "page_reference",
        "page": None,  # per-field page is not captured by either path
        "note": "See attached document — no on-page coordinates were captured "
                "during extraction.",
        "page_count": page_count,
    }


def _build_fields(mapped_fields, page_count):
    """Turn the flat mapped_fields map into review rows. No confidence exists, so
    ``confidence`` is None and ``confidence_available`` is False — never invented."""
    fields = []
    for name, value in (mapped_fields or {}).items():
        fields.append({
            "field_name": name,
            "value": value,
            "confidence": None,
            "confidence_available": False,
            "source_location": _field_source_location(page_count),
        })
    fields.sort(key=lambda f: f["field_name"])
    return fields


async def get_review_payload(conn, org_id, document_id) -> dict:
    """Everything needed to review ONE document, in one call.

    Bundles: document metadata, the native extraction (text + tables), the
    template extraction (mapped_fields), a normalised per-field list with honest
    confidence + source-location, and the document's current entity/record links
    (reusing Phase 5's ``list_document_links`` verbatim).
    """
    doc = await _document_row(conn, org_id, document_id)
    if not doc:
        raise ReviewError(404, "Document not found")

    extraction = await _latest_extraction(conn, org_id, document_id)
    template = await _latest_template_extraction(conn, org_id, document_id)
    links = await dl.list_document_links(conn, org_id, document_id)

    page_count = extraction["page_count"] if extraction else None
    mapped_fields = _decode_jsonb(template["mapped_fields"]) if template else None

    extraction_out = None
    if extraction:
        extraction_out = {
            "extraction_id": str(extraction["id"]),
            "extraction_method": extraction["extraction_method"],
            "has_native_text_layer": extraction["has_native_text_layer"],
            "page_count": extraction["page_count"],
            "extracted_text": extraction["extracted_text"],
            "extracted_tables": _decode_jsonb(extraction["extracted_tables"]) or [],
        }

    template_out = None
    if template:
        template_out = {
            "template_extraction_id": str(template["id"]),
            "template_type": template["template_type"],
            "extraction_source": template["extraction_source"],
            "mapped_fields": mapped_fields or {},
            "reviewed_by": str(template["reviewed_by"]) if template["reviewed_by"] else None,
            "reviewed_at": template["reviewed_at"].isoformat() if template["reviewed_at"] else None,
        }

    return {
        "document": {
            "id": str(doc["id"]),
            "original_filename": doc["original_filename"],
            "source": doc["source"],
            "mime_type": doc["mime_type"],
            "status": doc["status"],
            "doc_family": doc["doc_family"],
            "entity_id": str(doc["entity_id"]) if doc["entity_id"] else None,
            "has_stored_file": bool(doc["storage_key"]),
            "confirmed_by": str(doc["confirmed_by"]) if doc["confirmed_by"] else None,
            "confirmed_at": doc["confirmed_at"].isoformat() if doc["confirmed_at"] else None,
            "created_at": doc["created_at"].isoformat() if doc["created_at"] else None,
        },
        "extraction": extraction_out,
        "template_extraction": template_out,
        "fields": _build_fields(mapped_fields, page_count),
        "confidence_available": False,
        "source_location": {
            "coordinates_available": False,
            "mode": "page_reference",
            "page_count": page_count,
            "textract_note": "Textract returns Geometry.BoundingBox by default but "
                             "parse_analyze_blocks stores only text — no coordinates persisted.",
            "native_note": "pdfplumber extract_native stored plain text/tables only "
                           "(page-level granularity), no char/word bounding boxes.",
        },
        "links": links,
    }


async def submit_field_correction(
    conn, org_id, document_id, *, field_name, corrected_value, corrected_by,
    notes=None,
) -> dict:
    """Record a human correction to one mapped field.

    Writes BOTH an audit row in ``document_field_corrections`` (original_value =
    what was there before, corrected_value = the human's new value) AND updates
    the live ``mapped_fields`` on the document's latest
    ``document_template_extractions`` row so the corrected value is the system of
    record going forward. Runs in a single transaction so the audit row and the
    live value never diverge.
    """
    if not field_name or not str(field_name).strip():
        raise ReviewError(422, "field_name is required")
    if corrected_value is None:
        raise ReviewError(422, "corrected_value is required")

    async with conn.transaction():
        doc = await conn.fetchrow(
            "SELECT id FROM documents WHERE id = $1 AND org_id = $2",
            document_id, org_id,
        )
        if not doc:
            raise ReviewError(404, "Document not found")

        template = await conn.fetchrow(
            """
            SELECT id, mapped_fields
            FROM document_template_extractions
            WHERE document_id = $1 AND org_id = $2
            ORDER BY created_at DESC
            LIMIT 1
            FOR UPDATE
            """,
            document_id, org_id,
        )
        if not template:
            raise ReviewError(
                404, "No template extraction to correct for this document")

        mapped = _decode_jsonb(template["mapped_fields"]) or {}
        prior = mapped.get(field_name)
        original_value = None if prior is None else str(prior)

        correction_id = await conn.fetchval(
            """
            INSERT INTO document_field_corrections
                (document_id, org_id, template_extraction_id, field_name,
                 original_value, corrected_value, notes, corrected_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            document_id, org_id, template["id"], field_name,
            original_value, str(corrected_value), notes, corrected_by,
        )

        # Update the LIVE mapped_fields value. to_jsonb($text) keeps it a JSON
        # string — exact decimal precision preserved, never a float. Guard the
        # NULL/'{}' case so jsonb_set always has an object to write into.
        await conn.execute(
            """
            UPDATE document_template_extractions
            SET mapped_fields =
                jsonb_set(COALESCE(mapped_fields, '{}'::jsonb),
                          ARRAY[$2::text], to_jsonb($3::text), true)
            WHERE id = $1
            """,
            template["id"], field_name, str(corrected_value),
        )

    return {
        "correction_id": str(correction_id),
        "document_id": str(document_id),
        "template_extraction_id": str(template["id"]),
        "field_name": field_name,
        "original_value": original_value,
        "corrected_value": str(corrected_value),
    }


async def confirm_document(conn, org_id, document_id, *, confirmed_by) -> dict:
    """Mark a reviewed document confirmed: set ``status='confirmed'`` and stamp
    who confirmed it and when. ``status`` stays free-text (existing convention);
    'confirmed' is a new terminal value in that convention."""
    row = await conn.fetchrow(
        """
        UPDATE documents
        SET status = $3, confirmed_by = $4, confirmed_at = now(), updated_at = now()
        WHERE id = $1 AND org_id = $2
        RETURNING status, confirmed_by, confirmed_at
        """,
        document_id, org_id, CONFIRMED_STATUS, confirmed_by,
    )
    if not row:
        raise ReviewError(404, "Document not found")
    return {
        "document_id": str(document_id),
        "status": row["status"],
        "confirmed_by": str(row["confirmed_by"]) if row["confirmed_by"] else None,
        "confirmed_at": row["confirmed_at"].isoformat() if row["confirmed_at"] else None,
    }
