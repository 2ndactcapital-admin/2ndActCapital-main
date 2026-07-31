"""Chancery Phase 11a — narrative metadata extraction (NARRATIVE family).

The tabular family (K-1s, tax returns, …) is turned into structured
``mapped_fields`` by ``services.textract_extraction`` (deterministic regex over a
native text layer, or Textract for scans). The NARRATIVE family — LLC formation
docs, trust instruments, wills, estate plans, operating agreements — carries its
value in PROSE, not tables, so a regex mapper does not fit. This module extracts
narrative metadata the same way the rest of the platform reads prose: through the
central AI helper (``services.extraction.call_claude_json``), the SINGLE choke
point every AI call routes through (per-org model + fallback chain + decision
log). No new provider, no direct SDK use.

What we extract from a narrative document's ``extracted_text``:
  * a brief plain-language ``summary``;
  * ``key_provisions`` — a structured list of ``{provision_type, description}``;
  * ``key_dates``       — ``{date, description}`` (effective date, term, etc.);
  * ``key_parties``     — ``{name, role}`` where ``role`` is the SPECIFIC
                          relationship the instrument names (trustee, grantor,
                          beneficiary, managing member, …), not a generic label.

Honest-sparsity contract: a short or ambiguous document may not yield much
structure. We NEVER force elaborate structure out of thin content — the prompt
instructs the model to return empty lists rather than invent, and the normaliser
here drops anything blank. A document with no usable text is skipped outright (no
row), not stored with a fabricated summary.

Downstream (Task 3): each extracted party with a name is matched against existing
entities using the SAME picker matcher Phase 5 uses (``find_entity_dupes``) —
match → a system ``document_entity_links`` row carrying the SPECIFIC role;
no match → a ``document_link_proposals`` row (propose, never auto-create). That
linkage lives in ``services.document_linkage.auto_link_narrative_parties``.

Full semantic INDEX/RETRIEVE (embeddings + pgvector) is deliberately a LATER
sprint (11b); nothing in this module builds any vector logic.
"""

from __future__ import annotations

import json

from services.database import get_pool, reset_rls_context, set_rls_context
from services.document_linkage import auto_link_narrative_parties
# Single AI choke point (Sprint 27). Imported by name so a verify run can stub
# this module's ``call_claude_json`` deterministically without a live API key —
# the same leaf-stub pattern the earlier Chancery verifies use.
from services.extraction import call_claude_json

# The confirmed NARRATIVE doc_category codes (Phase 2/4 discovery). Kept here so a
# caller (and the SORT hook) can gate on the same set. Mirrors
# ``chancery_intake._NARRATIVE_CATEGORIES`` — the two are asserted equal in verify.
NARRATIVE_CATEGORIES = frozenset({
    "llc_formation", "trust_instrument", "will", "estate_plan",
    "operating_agreement",
})

# A narrative document can be long; give the model room for the structured JSON.
_MAX_TOKENS = 1500

NARRATIVE_SYSTEM_PROMPT = (
    "You are a paralegal extracting structured metadata from a legal narrative "
    "document (an LLC formation document, trust instrument, will, estate plan, or "
    "operating agreement). Read the document text and return ONLY a JSON object "
    "with exactly these keys:\n"
    '  "summary": a one- to three-sentence plain-language summary of what the '
    "document is and does (a string; empty string if the text is too thin to "
    "summarise).\n"
    '  "key_provisions": a list of {"provision_type": short label, '
    '"description": one sentence} for the substantive provisions actually present '
    "(e.g. distribution terms, successor trustee, management authority, "
    "revocability). \n"
    '  "key_dates": a list of {"date": the date as written, "description": what '
    "the date marks} for dates the document actually states (effective date, "
    "term, execution date).\n"
    '  "key_parties": a list of {"name": the party\'s name exactly as written, '
    '"role": the SPECIFIC relationship the document assigns them}. Use the '
    "precise role the instrument uses — trustee, successor trustee, grantor, "
    "settlor, beneficiary, executor, managing member, member, testator — NOT a "
    "generic word like 'party' or 'related'.\n\n"
    "CRITICAL: extract only what is genuinely in the text. If the document is "
    "short, ambiguous, or not really one of these instruments, return empty lists "
    "and a brief (or empty) summary. Do NOT invent provisions, dates, parties, or "
    "roles to fill the structure. Return JSON only, no prose, no code fences."
)


# ---------------------------------------------------------------------------
# Normalisation — keep only what is genuinely present (no fabricated structure)
# ---------------------------------------------------------------------------
def _as_list(value):
    return value if isinstance(value, list) else []


def _clean_str(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalize_provisions(raw) -> list[dict]:
    out: list[dict] = []
    for item in _as_list(raw):
        if isinstance(item, dict):
            ptype = _clean_str(item.get("provision_type") or item.get("type"))
            desc = _clean_str(item.get("description"))
            if ptype or desc:
                out.append({"provision_type": ptype, "description": desc})
        elif isinstance(item, str) and item.strip():
            out.append({"provision_type": None, "description": item.strip()})
    return out


def _normalize_dates(raw) -> list[dict]:
    out: list[dict] = []
    for item in _as_list(raw):
        if isinstance(item, dict):
            date = _clean_str(item.get("date"))
            desc = _clean_str(item.get("description"))
            if date or desc:
                out.append({"date": date, "description": desc})
        elif isinstance(item, str) and item.strip():
            out.append({"date": item.strip(), "description": None})
    return out


def _normalize_parties(raw) -> list[dict]:
    """Only parties with a genuine NAME survive — a role with no name is not a
    linkable party, and a nameless entry is exactly the kind of fabricated
    structure the honest-sparsity contract forbids."""
    out: list[dict] = []
    for item in _as_list(raw):
        if isinstance(item, dict):
            name = _clean_str(item.get("name"))
            role = _clean_str(item.get("role"))
            if name:
                out.append({"name": name, "role": role})
        elif isinstance(item, str) and item.strip():
            out.append({"name": item.strip(), "role": None})
    return out


def normalize_extraction(parsed: dict) -> dict:
    """Coerce a raw model response into the stored shape, dropping blanks."""
    return {
        "summary": _clean_str((parsed or {}).get("summary")),
        "key_provisions": _normalize_provisions((parsed or {}).get("key_provisions")),
        "key_dates": _normalize_dates((parsed or {}).get("key_dates")),
        "key_parties": _normalize_parties((parsed or {}).get("key_parties")),
    }


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
async def _load_extracted_text(pool, org_id, document_id) -> str | None:
    """Latest ``document_extractions.extracted_text`` for a document (the prose
    the Phase-1/4 extractors already produced)."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT extracted_text
            FROM document_extractions
            WHERE document_id = $1 AND org_id = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            document_id, org_id,
        )


async def run_narrative_extraction(pool, doc, org_id) -> dict:
    """Extract narrative metadata for one document and persist a
    ``document_narrative_extractions`` row.

    Returns an outcome dict. ``outcome='skipped'`` (no row written) when there is
    no extracted text to read or the AI helper is unavailable (no API key / chain
    exhausted). ``outcome='extracted'`` includes the ``extraction_id`` and the
    normalised structure — which may legitimately have empty lists for a sparse or
    ambiguous document (honest sparsity, never fabricated structure).
    """
    document_id = doc["id"]
    text = await _load_extracted_text(pool, org_id, document_id)
    if not text or not text.strip():
        return {"outcome": "skipped", "reason": "no extracted text"}

    parsed = await call_claude_json(
        NARRATIVE_SYSTEM_PROMPT,
        f"Document text:\n{text}",
        max_tokens=_MAX_TOKENS,
        org_id=org_id,
        task_type="narrative_extraction",
    )
    if parsed is None:
        # No API key / model chain exhausted / unparseable — do not store a guess.
        return {"outcome": "skipped", "reason": "AI unavailable"}

    norm = normalize_extraction(parsed)

    async with pool.acquire() as conn:
        extraction_id = await conn.fetchval(
            """
            INSERT INTO document_narrative_extractions
                (document_id, org_id, summary, extracted_provisions,
                 key_dates, key_parties)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb)
            RETURNING id
            """,
            document_id, org_id, norm["summary"],
            json.dumps(norm["key_provisions"]),
            json.dumps(norm["key_dates"]),
            json.dumps(norm["key_parties"]),
        )

    return {
        "outcome": "extracted",
        "extraction_id": str(extraction_id),
        **norm,
    }


async def extract_narrative_and_link(pool, doc, org_id) -> dict:
    """Full narrative step: extract metadata, then link/propose each key party.

    This is what SORT calls when it classifies a document into a narrative
    category. Sets its own RLS context so it is safe to call both from inside
    ``process_document`` (context already set — nesting is harmless) and
    standalone (a worker / verify driving the pipeline). Returns
    ``{"extraction": {...}, "link": {...} | None}``. Linkage runs only on a real
    extraction with at least one named party.
    """
    tokens = set_rls_context(org_id, False)
    try:
        extraction = await run_narrative_extraction(pool, doc, org_id)
        link = None
        parties = extraction.get("key_parties") or []
        if extraction.get("outcome") == "extracted" and parties:
            async with pool.acquire() as conn:
                link = await auto_link_narrative_parties(
                    conn, org_id, doc["id"], parties)
        return {"extraction": extraction, "link": link}
    finally:
        reset_rls_context(tokens)
