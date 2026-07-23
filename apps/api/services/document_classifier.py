"""Open-set document-type classifier (Sprint 25).

Sorts an already-extracted plain-text document into one of the canonical
``reference_data`` ``doc_category`` codes — or, when nothing fits well, PROPOSES
a new category into a review queue rather than force-fitting or silently
inventing one. This mirrors the house "AI proposes, human ratifies into a fixed
versioned schema" pattern (cf. entity_notes extraction suggestions): a human
ratifies a proposal into reference_data; this service never touches the
canonical list.

Out of scope (this is S25): OCR / ingestion (Chancery, S26) — ``text`` is
assumed already extracted; retrieval / embeddings (S26+).

Model selection: resolved per-org from org_settings via
extraction.resolve_model with key ``ai.model.document_classifier`` (Sprint 25),
falling back to ``ai.model.default`` (Haiku) when the org has not overridden it.
The model string is NEVER hardcoded here — only DEFAULT_SETTINGS and the seed
SQL may hold a literal model id.
"""

import json
from decimal import Decimal, InvalidOperation

from services.extraction import (
    DEFAULT_MODEL_KEY,
    DOCUMENT_CLASSIFIER_MODEL_KEY,
    call_claude_json,
    resolve_model,
)


def _decode_jsonb(value):
    """asyncpg returns jsonb as str; decode to the Python value."""
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


async def resolve_classifier_model(conn, org_id) -> str:
    """Which model the classifier calls for ``org_id``.

    Same task-specific-override convention as the 'assistant' task: the classifier
    checks its dedicated ``ai.model.document_classifier`` key FIRST. If this org
    has explicitly set that key, that override wins (an org_admin choosing a
    stronger classifier model). Otherwise it falls back to ``ai.model.default``
    (Haiku) — resolved through resolve_model so an org-wide default override is
    still respected, and the platform default backstops an org with nothing set.

    Note we detect an *explicit* override by looking for a stored row directly
    (get_setting would mask "unset" behind the DEFAULT_SETTINGS value), which is
    what lets the fall-through reach ai.model.default rather than the classifier
    key's own default.
    """
    try:
        row = await conn.fetchrow(
            "SELECT setting_value FROM org_settings "
            "WHERE org_id = $1 AND setting_key = $2",
            org_id, DOCUMENT_CLASSIFIER_MODEL_KEY,
        )
        if row is not None:
            return _decode_jsonb(row["setting_value"])
    except Exception as exc:
        print(f"resolve_classifier_model lookup failed, using default: {exc}")
    # No explicit classifier override on this org → the org's ai.model.default.
    return await resolve_model(org_id, key=DEFAULT_MODEL_KEY)


async def list_candidate_categories(conn, org_id) -> list[dict]:
    """Active doc_category candidates visible to ``org_id``.

    reference_data rows are global when ``org_id IS NULL`` (the seeded 12) or
    org-specific. Both are valid candidates for this org.
    """
    rows = await conn.fetch(
        """
        SELECT code, label
        FROM reference_data
        WHERE list_key = 'doc_category'
          AND is_active = true
          AND (org_id = $1 OR org_id IS NULL)
        ORDER BY display_order, code
        """,
        org_id,
    )
    return [{"code": r["code"], "label": r["label"]} for r in rows]


def _build_system_prompt(candidates: list[dict]) -> str:
    listing = "\n".join(f"  - {c['code']}: {c['label']}" for c in candidates)
    return (
        "You are a document-type classifier for a private investment platform. "
        "Given the text of a single document, sort it into ONE category.\n\n"
        "This is an OPEN-SET task. First try to match the document to one of the "
        "EXISTING categories below. Only if NONE of them fits well should you "
        "propose a NEW category — do not force a bad match, and do not invent a "
        "category when an existing one clearly fits.\n\n"
        "EXISTING categories (code: label):\n"
        f"{listing}\n\n"
        "Return ONLY valid JSON, no other text, in exactly this shape:\n"
        "{\n"
        '  "category_code": "<an existing code above, OR a new snake_case code '
        'if proposing>",\n'
        '  "confidence": <number 0-1>,\n'
        '  "is_new_proposal": <true if none of the existing categories fit and '
        "you are proposing a new one, else false>,\n"
        '  "proposed_label": "<human-readable Title Case label when '
        'is_new_proposal is true, else null>",\n'
        '  "reasoning": "<one sentence on why>"\n'
        "}"
    )


def _to_decimal(value):
    """confidence is a numeric column — asyncpg needs Decimal, not float."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


async def classify_document(
    conn,
    org_id,
    text: str,
    *,
    model: str | None = None,
) -> dict:
    """Classify ``text`` for ``org_id``.

    Returns::

        {"category_code": str | None, "confidence": float | None,
         "is_new_proposal": bool, "reasoning": str | None,
         "proposal_id": str | None}

    When ``is_new_proposal`` is true (nothing fit, or the model returned a code
    that is not a known candidate — which we treat as an implicit proposal so a
    hallucinated code can never masquerade as canonical), a row is INSERTed into
    ``doc_category_proposals`` (status 'pending') for a human to ratify. The
    canonical reference_data list is NEVER modified here.

    Returns is_new_proposal=false with category_code=None when the model is
    unavailable (no ANTHROPIC_API_KEY) — callers should treat None as
    "unclassified", not as a match.
    """
    candidates = await list_candidate_categories(conn, org_id)
    candidate_codes = {c["code"] for c in candidates}

    resolved_model = model or await resolve_classifier_model(conn, org_id)

    parsed = await call_claude_json(
        _build_system_prompt(candidates),
        f"Document text:\n{text}",
        max_tokens=300,
        model=resolved_model,
    )

    if parsed is None:
        # No API key or call failed — do not guess a category.
        return {
            "category_code": None,
            "confidence": None,
            "is_new_proposal": False,
            "reasoning": None,
            "proposal_id": None,
        }

    category_code = (parsed.get("category_code") or "").strip() or None
    confidence = parsed.get("confidence")
    reasoning = parsed.get("reasoning")
    proposed_label = parsed.get("proposed_label")
    is_new = bool(parsed.get("is_new_proposal"))

    # Safety net: a code the model claims is existing but that is not actually a
    # candidate is an invented code. Never return it as canonical — route it to
    # the review queue as a proposal instead.
    if not is_new and category_code not in candidate_codes:
        is_new = True

    proposal_id = None
    if is_new:
        # Fall back to the code itself as a label when the model omitted one.
        label = (proposed_label or "").strip() or (
            category_code or "Uncategorized document"
        )
        row = await conn.fetchrow(
            """
            INSERT INTO doc_category_proposals
                (org_id, proposed_code, proposed_label, reasoning, confidence,
                 source_excerpt, status)
            VALUES ($1, $2, $3, $4, $5, $6, 'pending')
            RETURNING id
            """,
            org_id,
            category_code,
            label,
            reasoning,
            _to_decimal(confidence),
            (text or "")[:500],
        )
        proposal_id = str(row["id"])

    return {
        "category_code": category_code,
        "confidence": confidence,
        "is_new_proposal": is_new,
        "reasoning": reasoning,
        "proposal_id": proposal_id,
    }
