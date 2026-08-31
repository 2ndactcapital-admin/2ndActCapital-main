"""Advisor edits to model-proposed fee fields, recorded. fee40 Task 3.3.

Every time an advisor changes a field the model proposed, that edit is the most
valuable signal the fee module produces: it is a labelled example of the model
being wrong about this firm's own arrangements. This module records them. It
does NOT yet change model behaviour from them — the feedback loop that reads
these rows back into the prompt is later work, and building half of it now would
mean a retrieval path nobody has proved against real rows.

WHY ``document_field_corrections`` AND WHAT HAD TO CHANGE
──────────────────────────────────────────────────────────────────────────────
It is the existing field-level correction ledger, already polymorphic
(``target_type``/``target_id``) and already read by
``services/correction_retrieval.py``. Reusing it means one ledger, one retrieval
path. Task 1 measured that reusing it was NOT free, though: two deployed CHECK
constraints made a fee-schedule correction impossible to write, and the prompt's
anticipated blocker (a NOT NULL ``document_id``) was not one of them —
``document_id`` was already nullable.

  1. ``document_field_corrections_target_type_chk`` was a closed allow-list of
     ``('document','note_terms','template_proposal')``. ``'FEE_SCHEDULE_SPEC'``
     was rejected outright.

  2. ``document_field_corrections_document_pairing_chk`` forced
     ``org_id IS NULL`` for EVERY non-document target. That is right for
     ``note_terms`` — a 424B2's terms are a public fact belonging to no tenant —
     and wrong here. A firm's fee negotiations are tenant data. Writing them
     org-NULL would have made them invisible to ``correction_retrieval``, which
     filters ``org_id = $1``, defeating the entire purpose; and it would have
     put them in a globally readable row.

The migration extends both constraints and — the part that matters for tenant
safety — narrows the three ``document_field_corrections_global_*`` RLS policies
from ``target_type <> 'document'`` to an explicit allow-list of the genuinely
global target types. That open-ended ``<>`` would have silently globalised this
new target type, and every future one, without anybody writing a line of code.

So: a FEE_SCHEDULE_SPEC correction carries a REAL ``org_id``, is covered by
``document_field_corrections_org_isolation`` and by nothing else, and needs no
``app.is_super_admin`` escape hatch — unlike the note-terms path, which does.

WHAT ``target_id`` POINTS AT
──────────────────────────────────────────────────────────────────────────────
The conversation, not the schedule. The correction is about the model's
PROPOSAL, and at the moment an advisor fixes a field there is usually no
``fee_schedules`` row yet — a target_id pointing at a schedule would be
unavailable exactly when the signal is generated. When the spec does edit an
existing schedule, that id is recorded in the notes envelope, where it is
additional context rather than the identity of the thing corrected.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Mapping

TABLE = "document_field_corrections"

#: The target_type this module owns. Uppercase, matching the fee module's own
#: vocabularies (ASSET_MANAGEMENT, GRADUATED, ACCOUNT) rather than the lowercase
#: convention of the document-pipeline target types already in the column. The
#: column is a free-text CHECK allow-list, so both live side by side; the case
#: is the tell for which subsystem wrote a row.
TARGET_TYPE = "FEE_SCHEDULE_SPEC"

#: ``notes`` is a text column with no structure, so provenance goes in as a JSON
#: envelope — the same convention ``note_terms_corrections`` established.
SOURCE_ADVISOR_EDIT = "advisor_edit_before_save"


class FeeSpecCorrectionError(ValueError):
    """A correction could not be logged as specified."""

    code = "fee_spec_correction_invalid"


def _as_text(value: Any) -> str | None:
    """Render a field value for the text columns without losing its type.

    JSON rather than ``str()``: ``None`` has to survive as the token ``null``
    and not the string ``"None"``, ``False`` as ``false``, and a Decimal as its
    exact digits. A later reader can ``json.loads`` any of them unambiguously,
    which ``str()`` output cannot promise.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


async def log_fee_spec_correction(
    conn,
    *,
    org_id: str,
    conversation_id: str,
    field_name: str,
    original_value: Any,
    corrected_value: Any,
    corrected_by: str | None = None,
    fee_schedule_id: str | None = None,
    notes: str | None = None,
    source: str = SOURCE_ADVISOR_EDIT,
) -> str | None:
    """Record one advisor edit to a model-proposed field.

    Returns the new correction id, or ``None`` when the edit was a no-op.

    A no-op is not an error and is not stored. An advisor who clicks into a
    field and out again without changing it has taught the model nothing, and
    logging it would put unchanged pairs into the training signal at whatever
    rate the UI happens to fire blur events — quietly diluting exactly the data
    this table exists to collect.

    Raises :class:`FeeSpecCorrectionError` on a missing org_id, conversation_id
    or field name. Typed rather than ``assert``: asserts vanish under ``python
    -O``, and these are the only guards between a caller and a row that cannot
    be attributed to anything.
    """
    if not org_id:
        raise FeeSpecCorrectionError(
            "org_id is required — it comes from the caller's verified session "
            "and is what scopes this correction to one tenant"
        )
    if not conversation_id:
        raise FeeSpecCorrectionError(
            "conversation_id is required — it becomes target_id"
        )
    if not field_name or not str(field_name).strip():
        raise FeeSpecCorrectionError("field_name is required")

    original_text = _as_text(original_value)
    corrected_text = _as_text(corrected_value)

    if original_text == corrected_text:
        return None

    if corrected_text is None:
        # NOT NULL column, and "the advisor cleared this field" is a real, and
        # informative, correction. JSON null round-trips.
        corrected_text = "null"

    envelope: dict[str, Any] = {"source": source, "target_kind": "fee_spec_draft"}
    if fee_schedule_id:
        envelope["fee_schedule_id"] = str(fee_schedule_id)
    if notes:
        envelope["notes"] = notes

    correction_id = await conn.fetchval(
        f"""
        INSERT INTO {TABLE}
            (document_id, org_id, template_extraction_id, target_type, target_id,
             field_name, original_value, corrected_value, notes, corrected_by)
        VALUES (NULL, $1::uuid, NULL, $2, $3::uuid, $4, $5, $6, $7, $8::uuid)
        RETURNING id
        """,
        str(org_id), TARGET_TYPE, str(conversation_id), str(field_name),
        original_text, corrected_text, json.dumps(envelope),
        str(corrected_by) if corrected_by else None,
    )
    return str(correction_id)


async def log_fee_spec_corrections(
    conn,
    *,
    org_id: str,
    conversation_id: str,
    edits: Mapping[str, Mapping[str, Any]],
    corrected_by: str | None = None,
    fee_schedule_id: str | None = None,
) -> list[dict[str, Any]]:
    """Log a whole screenful of edits. ``{field: {original, corrected}}``.

    Returns one entry per field with its outcome, including the no-ops, so a
    caller can tell "nothing was logged because nothing changed" from "nothing
    was logged because the write failed" — two states a bare count conflates.
    """
    out: list[dict[str, Any]] = []
    for field_name, pair in edits.items():
        if not isinstance(pair, Mapping):
            raise FeeSpecCorrectionError(
                f"edit for {field_name!r} must be an object with "
                f"'original' and 'corrected'"
            )
        correction_id = await log_fee_spec_correction(
            conn, org_id=org_id, conversation_id=conversation_id,
            field_name=field_name,
            original_value=pair.get("original"),
            corrected_value=pair.get("corrected"),
            corrected_by=corrected_by, fee_schedule_id=fee_schedule_id,
        )
        out.append({
            "field": field_name,
            "correction_id": correction_id,
            "logged": correction_id is not None,
            "reason": None if correction_id else "value unchanged — nothing to learn from",
        })
    return out


async def list_fee_spec_corrections(
    conn, org_id: str, *, conversation_id: str | None = None, limit: int = 200
) -> list[dict[str, Any]]:
    """Corrections for this org. ``org_id`` is in the WHERE clause, not implied.

    RLS also scopes this, but a query that relied on RLS alone would return
    whatever the connection's GUC happened to hold — including everything, on a
    connection whose role bypasses RLS.
    """
    rows = await conn.fetch(
        f"""
        SELECT id::text AS id, target_id::text AS target_id, field_name,
               original_value, corrected_value, notes,
               corrected_by::text AS corrected_by, corrected_at
        FROM {TABLE}
        WHERE org_id = $1::uuid AND target_type = $2
          AND ($3::uuid IS NULL OR target_id = $3::uuid)
        ORDER BY corrected_at DESC
        LIMIT $4
        """,
        str(org_id), TARGET_TYPE,
        str(conversation_id) if conversation_id else None, limit,
    )
    return [dict(r) for r in rows]


__all__ = [
    "SOURCE_ADVISOR_EDIT",
    "TABLE",
    "TARGET_TYPE",
    "FeeSpecCorrectionError",
    "list_fee_spec_corrections",
    "log_fee_spec_correction",
    "log_fee_spec_corrections",
]
