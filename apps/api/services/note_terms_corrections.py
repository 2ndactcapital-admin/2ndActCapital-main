"""Correction logging for ``portfolio.securities_global_note_terms`` rows.

ONE PLACE, NOT EVERY CALL SITE
──────────────────────────────────────────────────────────────────────────────
Every correction to an extracted note term — whether a human made it in a review
screen, or a later automated pass overrode an extraction — goes through
:func:`log_note_terms_correction`. The INSERT is deliberately not inlined
anywhere else. The polymorphic shape below has three interlocking constraints
(see WHY THE ODD SHAPE) and duplicating it at N call sites means getting it
wrong at N-1 of them.

There is no review UI in this sprint. This module is the logging half only.

WHY THE ODD SHAPE
──────────────────────────────────────────────────────────────────────────────
``document_field_corrections`` was made polymorphic by the corrections sprint so
non-document things could be corrected through the same ledger. For a note-terms
target that means, enforced by ``document_field_corrections_document_pairing_chk``:

    target_type = 'note_terms'   target_id = the note_terms row id
    document_id IS NULL          org_id    IS NULL

``org_id`` is NULL because ``securities_global_note_terms`` is a GLOBAL table.
The terms of a Morgan Stanley 424B2 are a public fact; they are not any tenant's
data, and attributing a correction to one tenant would both misrepresent it and
leak that tenant's review activity into a shared row. The NULL is load-bearing,
not laziness — the CHECK constraint rejects a non-document target that carries an
org_id at all.

RLS: the global INSERT policy requires ``app.is_super_admin``, so this function
sets that GUC inside its own transaction (SET LOCAL semantics, PgBouncer-safe),
the same pattern ``services/edgar_fetch.py`` uses for the corpus ingest path.
Reads need nothing: ``document_field_corrections_global_read`` is
``USING (target_type <> 'document')``, so a note-terms correction is readable
under ``app_service`` with no org context at all.

THE TENANT LEARNING LOOP IS NOT POLLUTED
──────────────────────────────────────────────────────────────────────────────
``services/correction_retrieval.py`` filters every query with ``org_id = $1``.
NULL never equals a uuid, so nothing written here is ever pulled into a tenant's
few-shot correction examples. That is what makes it safe to also record machine
observations (see :func:`log_hazard_disagreement`) in this table.
"""

from __future__ import annotations

import json
from typing import Any

TABLE = "document_field_corrections"
TARGET_TYPE = "note_terms"

# Marks a row written by the extraction pipeline rather than by a person. Lives
# in ``notes`` as JSON because there is no dedicated column for provenance and
# adding one is out of this sprint's scope.
SOURCE_HUMAN = "human_review"
SOURCE_HAZARD_ENSEMBLE = "hazard_ensemble_disagreement"


class NoteTermsCorrectionError(ValueError):
    """A correction could not be logged as specified."""


def _as_text(value: Any) -> str | None:
    """Render a term value for the text columns without losing its type.

    JSON, not ``str()``: ``None`` must survive as the token ``null`` rather than
    the string ``"None"``, ``True`` as ``true``, and a Decimal as its exact
    digits. ``corrected_value`` is NOT NULL on the table, so a genuinely null
    corrected value still has to be representable — it becomes ``"null"``, which
    round-trips through ``json.loads`` unambiguously.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


async def log_note_terms_correction(
    conn,
    *,
    note_terms_id: str,
    field_name: str,
    original_value: Any,
    corrected_value: Any,
    notes: str | None = None,
    corrected_by: str | None = None,
    source: str = SOURCE_HUMAN,
) -> str:
    """Record one field-level correction against a note-terms row.

    Args:
        conn: an asyncpg connection. The caller owns it; this function opens its
            own transaction so the ``app.is_super_admin`` GUC it sets is scoped
            to the INSERT and does not leak into the caller's later statements.
        note_terms_id: ``portfolio.securities_global_note_terms.id``. Becomes
            ``target_id``.
        field_name: the term corrected — a ``note_terms_field_registry.field_key``.
        original_value: what extraction produced. May be None.
        corrected_value: the replacement. May be None; stored as JSON ``null``
            because the column is NOT NULL and "corrected to nothing" is a real
            correction.
        notes: free-text rationale. Merged into a JSON envelope with ``source``.
        corrected_by: ``users.id`` when a person did it, None for machine writes.
        source: SOURCE_HUMAN or SOURCE_HAZARD_ENSEMBLE.

    Returns:
        The new ``document_field_corrections.id`` as a string.

    Raises:
        NoteTermsCorrectionError: on a missing target id or field name. Typed,
            not ``assert`` — asserts vanish under ``python -O`` and this is the
            only guard standing between a caller and a silently orphaned row.
    """
    if not note_terms_id:
        raise NoteTermsCorrectionError("note_terms_id is required — it becomes target_id")
    if not field_name or not str(field_name).strip():
        raise NoteTermsCorrectionError("field_name is required")

    envelope = {"source": source}
    if notes:
        envelope["notes"] = notes
    note_payload = json.dumps(envelope)

    original_text = _as_text(original_value)
    corrected_text = _as_text(corrected_value)
    if corrected_text is None:
        corrected_text = "null"  # NOT NULL column; JSON null is the honest value

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        correction_id = await conn.fetchval(
            f"""
            INSERT INTO {TABLE}
                (document_id, org_id, template_extraction_id, target_type, target_id,
                 field_name, original_value, corrected_value, notes, corrected_by)
            VALUES (NULL, NULL, NULL, $1, $2::uuid, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            TARGET_TYPE,
            str(note_terms_id),
            str(field_name),
            original_text,
            corrected_text,
            note_payload,
            corrected_by,
        )

    return str(correction_id)


async def log_hazard_disagreement(
    conn,
    *,
    note_terms_id: str,
    field_name: str,
    primary_value: Any,
    primary_model: str,
    secondary_value: Any,
    secondary_model: str,
) -> str:
    """Record that the two extraction models disagreed on one hazard field.

    This is NOT a correction — nothing has been corrected, and the pipeline
    deliberately does not pick a winner. It is a disagreement, recorded so a
    reviewer can see BOTH answers and which model gave each.

    It lives in ``document_field_corrections`` because that is the only existing
    store that is field-level, note-terms-targeted, and org-NULL.
    ``securities_global_note_terms`` has exactly one jsonb column, ``field_status``,
    and ``models.note_terms.validate_field_status`` rejects any value outside the
    four states — so the ensemble record cannot go on the row itself, and this
    sprint does not alter that table's schema. That is a real gap in the terms
    table, written down here rather than papered over: a proper
    ``extraction_notes jsonb`` column on the terms row is the right long-term fix.

    ``corrected_by`` is NULL and ``notes.source`` is SOURCE_HAZARD_ENSEMBLE, so
    machine observations are distinguishable from human corrections by query.
    """
    detail = json.dumps({
        "primary": {"model": primary_model, "value": primary_value},
        "secondary": {"model": secondary_model, "value": secondary_value},
        "resolution": "unresolved — flagged needs_review, neither answer written as truth",
    }, default=str)

    return await log_note_terms_correction(
        conn,
        note_terms_id=note_terms_id,
        field_name=field_name,
        original_value=primary_value,
        corrected_value=secondary_value,
        notes=detail,
        corrected_by=None,
        source=SOURCE_HAZARD_ENSEMBLE,
    )


async def get_note_terms_corrections(conn, note_terms_id: str) -> list[dict]:
    """Every correction and disagreement recorded against one terms row.

    Readable with no org context by design — the global read policy is
    ``USING (target_type <> 'document')``.
    """
    rows = await conn.fetch(
        f"""
        SELECT id, field_name, original_value, corrected_value, notes,
               corrected_by, corrected_at, target_type, org_id
        FROM {TABLE}
        WHERE target_type = $1 AND target_id = $2::uuid
        ORDER BY corrected_at
        """,
        TARGET_TYPE, str(note_terms_id),
    )
    return [dict(r) for r in rows]
