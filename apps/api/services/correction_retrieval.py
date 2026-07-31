"""Chancery Phase 8 — correction-retrieval (retrieval-augmented correction loop).

NOT fine-tuning. The mechanism is a correction LOG (Phase 6's
``document_field_corrections``, written by ``document_review.submit_field_correction``
and, for classifications, ``submit_classification_correction``) read back at
inference time: before the AI classifies a NEW document — or before the
deterministic K-1 mapper commits its fields — we look up the org's relevant PAST
corrections and let them influence the result. A single human correction takes
effect on the very next matching document, with no batch retraining and no delay.

Two shapes, because the two call sites are fundamentally different (Task 1c):

  * CLASSIFICATION is an AI call (``document_classifier.classify_document``).
    Corrections become FEW-SHOT prompt content — "a document was previously
    corrected from category A to category B" — injected into the system prompt.

  * K-1 EXTRACTION is DETERMINISTIC regex (``textract_extraction.map_k1_fields``),
    NOT an AI call. There is no prompt to inject into, so corrections are applied
    as a deterministic post-map override: when the mapper reproduces a value a
    human previously corrected for this org+template+field, we swap in the
    corrected value. Same recurring extraction error → auto-fixed next time.

DATA-ISOLATION (Task 1a / verify requirement): every query filters by ``org_id``
in the QUERY ITSELF — never relying only on RLS. One org's corrections must never
surface for another org's retrieval.

SCHEMA (Task 1a): no schema change was needed. Extraction corrections carry a
``template_extraction_id`` → ``document_template_extractions.template_type`` join
that yields the template_type ('k1'). Classification corrections are encoded with
the reserved ``field_name = CLASSIFICATION_FIELD`` and a NULL
``template_extraction_id`` (original_value = wrong code, corrected_value = right
code) — the table's nullable columns already support this, no ALTER required.
"""

# Reserved field_name marking a document-classification (doc_category) correction
# rather than a template-field correction. See submit_classification_correction.
CLASSIFICATION_FIELD = "doc_category"

# Few-shot cap — this becomes prompt content, not a full history dump.
DEFAULT_LIMIT = 5


async def get_relevant_corrections(
    conn,
    org_id,
    context: dict,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Return the most-recent relevant past corrections for ``org_id``.

    ``context`` selects the retrieval shape:

      * ``{"kind": "classification"}`` — doc-category corrections for this org
        (reserved ``field_name = CLASSIFICATION_FIELD``). Returns rows shaped
        ``{"field_name", "original_value", "corrected_value", "excerpt"}`` where
        ``excerpt`` is a short slice of the corrected document's extracted text
        (best-effort; ``None`` when unavailable) so the few-shot can describe the
        document, not just the label swap.

      * ``{"kind": "extraction", "template_type": <str>, "field_name": <str|None>}``
        — field-level corrections joined to ``document_template_extractions`` and
        filtered to that ``template_type`` (e.g. 'k1'). When ``field_name`` is
        given, only that field's corrections are returned; otherwise all fields
        for the template. Rows shaped
        ``{"field_name", "original_value", "corrected_value", "template_type"}``.

    Always ``org_id``-scoped in the SQL itself (data-isolation requirement).
    Returns ``[]`` — never raises — when there is no relevant history (the common
    early-on case), so callers can wire this in without a behaviour change when
    the log is empty.
    """
    if org_id is None:
        return []
    kind = (context or {}).get("kind")
    capped = max(1, min(int(limit or DEFAULT_LIMIT), 50))

    if kind == "classification":
        rows = await conn.fetch(
            """
            SELECT c.field_name,
                   c.original_value,
                   c.corrected_value,
                   left(coalesce(e.extracted_text, ''), 240) AS excerpt
            FROM document_field_corrections c
            LEFT JOIN LATERAL (
                SELECT extracted_text
                FROM document_extractions
                WHERE document_id = c.document_id
                  AND org_id = c.org_id
                ORDER BY created_at DESC
                LIMIT 1
            ) e ON true
            WHERE c.org_id = $1
              AND c.field_name = $2
              AND c.corrected_value IS NOT NULL
            ORDER BY c.corrected_at DESC
            LIMIT $3
            """,
            org_id, CLASSIFICATION_FIELD, capped,
        )
        out = []
        for r in rows:
            excerpt = (r["excerpt"] or "").strip() or None
            out.append({
                "field_name": r["field_name"],
                "original_value": r["original_value"],
                "corrected_value": r["corrected_value"],
                "excerpt": excerpt,
            })
        return out

    if kind == "extraction":
        template_type = (context or {}).get("template_type")
        if not template_type:
            return []
        field_name = (context or {}).get("field_name")
        rows = await conn.fetch(
            """
            SELECT c.field_name,
                   c.original_value,
                   c.corrected_value,
                   dte.template_type
            FROM document_field_corrections c
            JOIN document_template_extractions dte
              ON dte.id = c.template_extraction_id
             AND dte.org_id = c.org_id
            WHERE c.org_id = $1
              AND dte.template_type = $2
              AND ($3::text IS NULL OR c.field_name = $3)
              AND c.corrected_value IS NOT NULL
            ORDER BY c.corrected_at DESC
            LIMIT $4
            """,
            org_id, template_type, field_name, capped,
        )
        return [
            {
                "field_name": r["field_name"],
                "original_value": r["original_value"],
                "corrected_value": r["corrected_value"],
                "template_type": r["template_type"],
            }
            for r in rows
        ]

    return []


def format_classification_examples(corrections: list[dict]) -> str:
    """Render classification corrections as a few-shot prompt block, or ``""``.

    Empty input → empty string, so the caller appends nothing and the normal
    prompt is completely unchanged when there is no relevant history.
    """
    if not corrections:
        return ""
    lines = [
        "",
        "PAST CORRECTIONS (this organization) — a human previously RE-classified "
        "documents like these. Treat each as a strong hint: if the new document "
        "closely matches one of these patterns, prefer the corrected category.",
    ]
    for c in corrections:
        orig = c.get("original_value") or "(none)"
        corrected = c.get("corrected_value")
        excerpt = (c.get("excerpt") or "").replace("\n", " ").strip()
        if excerpt:
            snippet = excerpt[:200]
            lines.append(
                f'  - A document containing "{snippet}" was corrected '
                f'from category "{orig}" to "{corrected}".'
            )
        else:
            lines.append(
                f'  - A document was corrected from category "{orig}" '
                f'to "{corrected}".'
            )
    return "\n".join(lines)


def apply_field_corrections(
    mapped_fields: dict,
    corrections: list[dict],
) -> tuple[dict, list[dict]]:
    """Apply deterministic field corrections to a freshly-mapped K-1 field dict.

    For each field the mapper produced, if the most-recent past correction for
    that ``field_name`` has an ``original_value`` equal to the mapped value, the
    field is replaced with ``corrected_value`` — i.e. the same recurring
    extraction error a human already fixed is auto-fixed here. Values are compared
    as strings (K-1 monetary fields are stored as exact decimal strings).

    Returns ``(new_mapped, applied)`` where ``applied`` is a list of
    ``{"field_name", "from", "to"}`` records (empty when nothing matched).
    ``corrections`` empty (the common case) → the input is returned unchanged and
    ``applied`` is ``[]``, so the normal extraction path is never degraded.
    """
    if not mapped_fields or not corrections:
        return dict(mapped_fields or {}), []

    # Most-recent correction wins per field (get_relevant_corrections is already
    # ordered corrected_at DESC, so the FIRST seen per field is the newest).
    latest_by_field: dict = {}
    for c in corrections:
        fn = c.get("field_name")
        if fn and fn not in latest_by_field:
            latest_by_field[fn] = c

    new_mapped = dict(mapped_fields)
    applied: list[dict] = []
    for field_name, value in list(mapped_fields.items()):
        corr = latest_by_field.get(field_name)
        if not corr:
            continue
        original = corr.get("original_value")
        corrected = corr.get("corrected_value")
        if corrected is None or original is None:
            continue
        if str(value) == str(original) and str(value) != str(corrected):
            new_mapped[field_name] = corrected
            applied.append({
                "field_name": field_name,
                "from": str(value),
                "to": str(corrected),
            })
    return new_mapped, applied
