"""Chancery Phase 3 — real K-1 template extraction (TABULAR family).

This is the piece the original Phase 3 never built: after Phase 4 confirmed live
AWS Textract access (the ``textractgate`` sprint), this module turns a K-1 that
Phase 2's SORT classified as ``category='k1'`` into structured template fields,
then hands off to Phase 5's REAL linkage engine (``services.document_linkage``).

Two extraction sources, chosen by COST DISCIPLINE (never call Textract when the
native text layer already carries the content):

  * NATIVE   — a text-native K-1 (Phase 1 pdfplumber already produced
               ``extracted_text`` / ``extracted_tables`` on the
               ``document_extractions`` row). We map straight from that stored
               output — NO Textract call.
  * TEXTRACT — a scanned / needs_ocr K-1 (no usable native text). We call
               ``services.textract.analyze_document`` with FeatureTypes
               ``TABLES`` + ``FORMS`` on the original bytes.

Both sources normalise to the SAME shape — text lines + optional (key→value) form
pairs + tables — so a single mapper produces ``mapped_fields`` regardless of
source. Every monetary figure is stored as an EXACT decimal STRING (never a
float): the values are parsed through :class:`decimal.Decimal` and stored via
``str(Decimal(...))`` so precision and scale survive verbatim in the JSON.

Party-name contract (Phase 5, ``document_linkage.K1_PARTY_NAME_KEYS``): the
party field key is chosen by the K-1's actual form type — a Form 1065 K-1 uses
``partner_name``, a 1120-S uses ``shareholder_name``, a 1041 uses
``beneficiary_name`` — always the FIRST key in the contract's priority order that
genuinely applies to that form.
"""

import json
import re
from decimal import Decimal, InvalidOperation

from services import textract
from services.database import get_pool, set_rls_context, reset_rls_context
from services.document_linkage import auto_link_k1_document
from starlette.concurrency import run_in_threadpool

K1_TEMPLATE_TYPE = "k1"

SOURCE_NATIVE = "native"
SOURCE_TEXTRACT = "textract"

# document_extractions.extraction_method values that carry a real native text
# layer (so Textract must NOT be re-run — cost discipline).
_NATIVE_METHODS = frozenset({
    "native_pdfplumber", "native_docx", "native_xlsx", "native_pptx",
    "native_text", "textract_image",  # a standalone image already OCR'd upstream
})

# ---------------------------------------------------------------------------
# Form-type → party field key (Phase-5 contract, "first applicable" priority).
# Ordered most-specific first so "1120-s" is matched before a bare "1120".
# ---------------------------------------------------------------------------
_FORM_TYPES = (
    ("1120-s", "shareholder_name", ("shareholder",)),
    ("1120s", "shareholder_name", ("shareholder",)),
    ("1041", "beneficiary_name", ("beneficiary",)),
    ("1065", "partner_name", ("partner", "member")),
)
# Fallback when the form number is not stated: a partnership K-1 is the common
# case, so the party is a partner (contract's first key).
_DEFAULT_PARTY_KEY = "partner_name"
_DEFAULT_ROLE_WORDS = ("partner", "member", "shareholder", "beneficiary", "recipient")

# ---------------------------------------------------------------------------
# K-1 box fields we map — a real, representative subset (not every line item).
# Each is (mapped_fields key, ordered label phrases to look for). Phrases are
# matched case-insensitively; the FIRST field whose phrase appears on a line
# claims that line's trailing monetary token.
# ---------------------------------------------------------------------------
_K1_BOXES = (
    ("ordinary_business_income", ("ordinary business income",)),
    ("net_rental_real_estate_income",
     ("net rental real estate income", "rental real estate income")),
    ("interest_income", ("interest income",)),
    ("ordinary_dividends", ("ordinary dividends",)),
    ("net_long_term_capital_gain",
     ("net long-term capital gain", "net long term capital gain",
      "long-term capital gain", "long term capital gain")),
)

# A monetary token: optional parens (negative), optional $, digits w/ commas,
# optional decimals. Kept deliberately strict so a stray box NUMBER ("1", "6a")
# is not mistaken for a value.
_MONEY_RE = re.compile(r"\(?\$?\s*-?\d[\d,]*(?:\.\d+)?\s*\)?")


def money_to_string(raw) -> str | None:
    """Coerce a raw monetary token to an EXACT decimal string, or None.

    ``"12,345.67"`` → ``"12345.67"``; ``"(1,000.00)"`` → ``"-1000.00"``;
    ``"$50.25"`` → ``"50.25"``. Uses :class:`Decimal` so scale/precision are
    preserved verbatim — never a float. A non-numeric token returns None.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("$", "").replace(",", "").replace(" ", "")
    if not s or s in ("-", "."):
        return None
    try:
        dec = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if negative:
        dec = -dec
    return str(dec)


def _last_money_on_line(line: str) -> str | None:
    """Return the last monetary token on a line as an exact decimal string."""
    tokens = _MONEY_RE.findall(line or "")
    for tok in reversed(tokens):
        val = money_to_string(tok)
        if val is not None:
            return val
    return None


def _detect_form(text: str) -> tuple[str, tuple[str, ...]]:
    """Return (party_field_key, role_words) for the K-1's form type."""
    low = (text or "").lower()
    for needle, key, role_words in _FORM_TYPES:
        if needle in low:
            return key, role_words
    return _DEFAULT_PARTY_KEY, _DEFAULT_ROLE_WORDS


def _party_from_lines(lines, role_words) -> str | None:
    """Find the party name on a '<role>'s name: VALUE' line."""
    for line in lines:
        low = line.lower()
        if "name" not in low:
            continue
        if not any(rw in low for rw in role_words):
            continue
        if ":" in line:
            value = line.split(":", 1)[1].strip()
            if value:
                return value
    return None


def _party_from_forms(forms, role_words) -> str | None:
    """Find the party name among Textract FORMS key→value pairs."""
    for key, value in (forms or {}).items():
        low = key.lower()
        if "name" in low and any(rw in low for rw in role_words) and value.strip():
            return value.strip()
    return None


def map_k1_fields(text: str, lines, forms) -> dict:
    """Map normalised extraction output to K-1 ``mapped_fields``.

    ``text`` is the full extracted text (used for form-type detection); ``lines``
    is the per-line list; ``forms`` is the (key→value) dict (empty for native).
    Returns a dict with the party-name field (correct contract key) plus any box
    fields found, all monetary values as exact decimal STRINGS.
    """
    party_key, role_words = _detect_form(text)
    mapped: dict = {}

    party = _party_from_forms(forms, role_words) or _party_from_lines(lines, role_words)
    if party:
        mapped[party_key] = party

    # Box values — line text first (works for native AND Textract), then any
    # FORMS pair whose key names the same box.
    for field_key, phrases in _K1_BOXES:
        value = None
        for line in lines:
            low = line.lower()
            if any(p in low for p in phrases):
                value = _last_money_on_line(line)
                if value is not None:
                    break
        if value is None and forms:
            for fk, fv in forms.items():
                if any(p in fk.lower() for p in phrases):
                    value = money_to_string(fv) or _last_money_on_line(fv)
                    if value is not None:
                        break
        if value is not None:
            mapped[field_key] = value
    return mapped


def _decode_jsonb(value):
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None
    return value


def _lines_from_text(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _rows_of(entry):
    """Yield row-lists from one extracted_tables entry, across the shapes the
    Phase-1/4 extractors emit: native PDF ``{"page", "tables": [[rows]]}`` (a list
    of tables, each a list of rows), docx/xlsx/pptx ``{"rows": [...]}``, or a bare
    list of rows."""
    if isinstance(entry, dict):
        if "tables" in entry:  # native PDF: value is a list of tables
            for table in entry.get("tables") or []:
                yield from (table or [])
            return
        entry = entry.get("rows") or []
    yield from (entry or [])


def _lines_from_tables(tables) -> list[str]:
    """Flatten extracted table rows into 'cell cell' lines so the box mapper sees
    a label + value on one line even when the text layer split them."""
    out: list[str] = []
    for entry in tables or []:
        for row in _rows_of(entry):
            cells = [str(c) for c in (row or []) if c not in (None, "")]
            if cells:
                out.append(" ".join(cells))
    return out


async def _load_latest_extraction(pool, org_id, document_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT extraction_method, extracted_text, extracted_tables,
                   has_native_text_layer
            FROM document_extractions
            WHERE document_id = $1 AND org_id = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            document_id, org_id,
        )


async def run_k1_extraction(pool, doc, org_id, file_bytes: bytes) -> dict:
    """Extract a K-1's template fields and persist a
    ``document_template_extractions`` row; set the document to ``status='sorted'``.

    Source is chosen by cost discipline: a native text layer is mapped directly
    (NO Textract); only a scanned / needs_ocr K-1 triggers a Textract
    AnalyzeDocument (TABLES+FORMS) call. Returns an outcome dict including the
    chosen ``source``, the ``mapped_fields``, and the new ``extraction_id``.
    """
    document_id = doc["id"]
    latest = await _load_latest_extraction(pool, org_id, document_id)
    method = latest["extraction_method"] if latest else None
    native_text = (latest["extracted_text"] if latest else None) or ""

    use_native = bool(native_text.strip()) or (method in _NATIVE_METHODS)

    if use_native:
        source = SOURCE_NATIVE
        tables = _decode_jsonb(latest["extracted_tables"]) if latest else None
        lines = _lines_from_text(native_text) + _lines_from_tables(tables)
        forms: dict = {}
        text = native_text
        raw_extraction = {
            "source": SOURCE_NATIVE,
            "extraction_method": method,
            "lines": lines,
            "tables": tables or [],
        }
    else:
        source = SOURCE_TEXTRACT
        result = await run_in_threadpool(
            textract.analyze_document, file_bytes, ("TABLES", "FORMS"))
        lines = result["lines"]
        forms = result["forms"]
        text = result["text"]
        raw_extraction = {
            "source": SOURCE_TEXTRACT,
            "feature_types": result["feature_types"],
            "block_count": result["block_count"],
            "lines": lines,
            "forms": forms,
            "tables": result["tables"],
        }

    mapped_fields = map_k1_fields(text, lines, forms)

    async with pool.acquire() as conn:
        extraction_id = await conn.fetchval(
            """
            INSERT INTO document_template_extractions
                (document_id, org_id, template_type, extraction_source,
                 raw_extraction, mapped_fields)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb)
            RETURNING id
            """,
            document_id, org_id, K1_TEMPLATE_TYPE, source,
            json.dumps(raw_extraction), json.dumps(mapped_fields),
        )
        # Task 3: the document is now genuinely ready for Phase-5 linkage.
        await conn.execute(
            "UPDATE documents SET status = 'sorted', updated_at = now() WHERE id = $1",
            document_id,
        )

    return {
        "source": source,
        "mapped_fields": mapped_fields,
        "extraction_id": str(extraction_id),
    }


async def extract_k1_and_link(pool, doc, org_id, file_bytes: bytes) -> dict:
    """Full K-1 step: extract template fields, then fire Phase-5 REAL auto-link.

    This is what Phase 2's SORT calls when it classifies a document as a K-1
    (``category='k1'``). Sets its own RLS context so it is safe to call both from
    inside ``process_document`` (context already set — nesting is harmless) and
    standalone (a worker / verify driving the pipeline). Returns
    ``{"extraction": {...}, "link": {...}}``.
    """
    tokens = set_rls_context(org_id, False)
    try:
        extraction = await run_k1_extraction(pool, doc, org_id, file_bytes)
        async with pool.acquire() as conn:
            link = await auto_link_k1_document(conn, org_id, doc["id"])
        return {"extraction": extraction, "link": link}
    finally:
        reset_rls_context(tokens)
