"""UDF tags — Task 2e of udf01a.

A separate module, not a section of ``portfolio_udf.py``, because a tag has a
lifecycle a scalar value does not: a vocabulary that is minted once and then
ASSIGNED many times, merged, and renamed. One row per assignment in
``portfolio.udf_tag_assignments`` (join table, not an array on the target) is
what makes merge/rename a single UPDATE across every assignment rather than a
rewrite of every record that happens to carry the tag.

WHO MAY MINT A NEW TAG, AND WHY IT IS SEPARATE FROM ASSIGNING ONE
──────────────────────────────────────────────────────────────────────────────
Assigning an EXISTING vocabulary entry to a record is a normal write, gated the
same way any UDF value write is. Minting — adding a code that has never existed
in this definition's vocabulary before — is gated on ``create_tags``
(``tags/create`` in ``public.permissions``), checked BEFORE the mint is
attempted.

The sprint prompt asked for a ``tag.create`` permission while also saying
"do not invent new permission strings". Those conflicted: ``public.permissions``
had 28 rows and no tags resource, and the nearest existing grant
(``manage_portfolio``) would have made minting and assigning the same gate —
at which point "mint without the permission is rejected, with it succeeds"
could not be tested, because BOTH callers would already hold whatever gates
assigning. Approved: add the one real row (see
``migrations/udf01a_tags_permission.sql``) and gate minting on it specifically.

NORMALIZATION
──────────────────────────────────────────────────────────────────────────────
``normalized_code`` is trim + casefold, computed on write and never trusted from
the caller. ``tag_code`` preserves the FIRST-ENTERED casing — "Prospect" minted
first stays "Prospect" even though a later caller assigns "PROSPECT"; both
resolve to the same ``normalized_code`` and the same vocabulary entry.

DUAL-WRITE INTO udf_values (TEMPORARY — see TODO(udf-1d) below)
──────────────────────────────────────────────────────────────────────────────
``services.fee_run_inputs._load_positions`` reads position tags directly out of
``portfolio.udf_values.value_text`` (fee35 finding [2]) and is the ONLY
production consumer of UDF data in this codebase. It does not know about
``udf_tag_assignments`` and will not be taught about it in this sprint — that is
Sprint 1b+ scope. So a tag minted through this module against a ``position``
target ALSO writes (or removes) a plain ``value_text`` row through
``portfolio_udf.record_udf_value``-shaped SQL, keeping the legacy read path
byte-identical to what it saw before this sprint existed. Every other
``target_type`` skips the dual-write — there is no other consumer to protect.
"""

from __future__ import annotations

from typing import Any

from services.org_settings import get_setting
from services.portfolio_assets import _OrgWrite, _require_org
from services.portfolio_udf import (
    TABLE_UDF_DEFINITIONS,
    TABLE_UDF_TAG_ASSIGNMENTS,
    TABLE_UDF_VALUES,
    UdfError,
    _current,
)

TAG_CREATE_PERMISSION = "create_tags"

#: fee_run_inputs only ever reads target_type='position' tags out of
#: udf_values.value_text. Dual-write is scoped to exactly that — see the
#: module docstring.
_DUAL_WRITE_TARGET_TYPES = frozenset({"position"})


class TagPermissionError(UdfError):
    """The caller may not mint a NEW tag (missing ``create_tags``)."""


class TagCapError(UdfError):
    """An org_settings-configured cap was exceeded."""


def normalize_tag(raw: Any) -> tuple[str, str]:
    """Return ``(tag_code, normalized_code)`` for a raw caller-supplied tag.

    ``tag_code`` is the trimmed ORIGINAL casing (what gets stored the first
    time a code is minted); ``normalized_code`` is casefolded for comparison
    and is what every uniqueness/lookup query actually keys on.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise UdfError("tag must be a non-empty string")
    trimmed = raw.strip()
    return trimmed, trimmed.casefold()


async def _get_tag_definition(conn, *, definition_id: str) -> dict:
    row = await conn.fetchrow(
        f"SELECT id::text AS id, org_id::text AS org_id, applies_to, data_type "
        f"FROM {TABLE_UDF_DEFINITIONS} d "
        f"WHERE d.id = $1::uuid AND d.deleted_at IS NULL AND {_current('d')}",
        str(definition_id),
    )
    if row is None:
        raise UdfError(f"definition {definition_id} is not a current, readable definition")
    if row["data_type"] != "tags":
        raise UdfError(
            f"definition {definition_id} has data_type={row['data_type']!r}, "
            f"not 'tags' — tag assignment only applies to a tags-typed field"
        )
    return dict(row)


async def _vocabulary_codes(conn, *, definition_id: str) -> set[str]:
    rows = await conn.fetch(
        f"SELECT DISTINCT normalized_code FROM {TABLE_UDF_TAG_ASSIGNMENTS} "
        f"WHERE definition_id = $1::uuid AND system_to IS NULL",
        str(definition_id),
    )
    return {r["normalized_code"] for r in rows}


async def _dual_write_position_tags(
    c, *, org_id: str, definition_id: str, target_id: str
) -> None:
    """Refresh the legacy ``value_text`` row from the CURRENT assignment set.

    TODO(udf-1d): remove once fee_run_inputs reads udf_tag_assignments
    directly. Until then this keeps that query's result byte-identical to a
    tag minted through the OLD ``record_udf_value`` path — that identity is
    asserted directly in ``verify_udf01a.py``.

    Mirrors ``portfolio_udf``'s own close-then-insert convention on the system
    axis rather than importing ``record_udf_value`` — that function validates
    against the definition's ``data_type``, which for a tags field is
    ``'tags'`` and deliberately refuses direct value writes (see
    ``portfolio_udf.coerce_value``). This is the one sanctioned bypass of that
    refusal, scoped to exactly the legacy shape the old path produced: one
    ``value_text`` row per assigned code is not what fee_run_inputs expects —
    it expects tag membership as a joined array read back out per position — so
    what is dual-written is the SET of current codes, space-joined, matching
    what ``fee35``'s original position-tagging flow stored.
    """
    codes = await c.fetch(
        f"SELECT tag_code FROM {TABLE_UDF_TAG_ASSIGNMENTS} "
        f"WHERE definition_id = $1::uuid AND target_id = $2::uuid "
        f"AND system_to IS NULL ORDER BY tag_code",
        str(definition_id), str(target_id),
    )
    await c.execute(
        f"UPDATE {TABLE_UDF_VALUES} SET system_to = now() "
        f"WHERE org_id = $1::uuid AND definition_id = $2::uuid "
        f"AND target_type = 'position' AND target_id = $3::uuid "
        f"AND system_to IS NULL AND valid_to IS NULL",
        org_id, str(definition_id), str(target_id),
    )
    if codes:
        joined = " ".join(r["tag_code"] for r in codes)
        await c.execute(
            f"INSERT INTO {TABLE_UDF_VALUES} "
            f"(org_id, definition_id, target_type, target_id, value_text) "
            f"VALUES ($1::uuid, $2::uuid, 'position', $3::uuid, $4)",
            org_id, str(definition_id), str(target_id), joined,
        )


async def assign_tags(
    conn,
    *,
    org_id: str,
    definition_id: str,
    target_id: str,
    codes: list[str],
    assigned_by: str | None,
    can_create_tags: bool,
) -> list[dict]:
    """Set the tags on one target to exactly ``codes`` (mint any new ones).

    Refuses BEFORE writing anything if ``codes`` would mint a tag the caller
    is not allowed to mint (``can_create_tags=False``) — the router resolves
    that flag from :data:`TAG_CREATE_PERMISSION` via ``rbac.has_permission``
    and passes it in, so this function stays testable without a pool.

    Enforces ``crm.udf.max_tags_per_record`` and ``crm.udf.max_tag_vocabulary``
    from ``org_settings`` — never a module constant, so an org_admin can raise
    either cap without a deploy.
    """
    org_id = _require_org(org_id)
    definition = await _get_tag_definition(conn, definition_id=definition_id)
    if definition["org_id"] not in (None, org_id):
        raise UdfError(f"definition {definition_id} does not belong to org {org_id}")
    if not target_id:
        raise UdfError("target_id is required")

    normalized = []
    seen = set()
    for raw in codes:
        tag_code, normalized_code = normalize_tag(raw)
        if normalized_code in seen:
            continue
        seen.add(normalized_code)
        normalized.append((tag_code, normalized_code))

    max_per_record = int(await get_setting(conn, org_id, "crm.udf.max_tags_per_record"))
    if len(normalized) > max_per_record:
        raise TagCapError(
            f"{len(normalized)} tags exceeds crm.udf.max_tags_per_record="
            f"{max_per_record} for this org"
        )

    async with _OrgWrite(conn, org_id) as c:
        existing_vocab = await _vocabulary_codes(c, definition_id=definition_id)
        # Preserve first-entered casing for a code already in the vocabulary —
        # the caller's casing never overrides it.
        first_casing = {}
        for row in await c.fetch(
            f"SELECT normalized_code, tag_code FROM {TABLE_UDF_TAG_ASSIGNMENTS} "
            f"WHERE definition_id = $1::uuid ORDER BY created_at",
            str(definition_id),
        ):
            first_casing.setdefault(row["normalized_code"], row["tag_code"])

        minted = [nc for _, nc in normalized if nc not in existing_vocab]
        if minted and not can_create_tags:
            raise TagPermissionError(
                f"tag(s) {sorted(set(minted))} do not exist in this field's "
                f"vocabulary yet. Minting a NEW tag requires "
                f"{TAG_CREATE_PERMISSION!r} — assigning an EXISTING tag does "
                f"not."
            )
        if minted:
            max_vocab = int(await get_setting(conn, org_id, "crm.udf.max_tag_vocabulary"))
            new_vocab_size = len(existing_vocab | set(minted))
            if new_vocab_size > max_vocab:
                raise TagCapError(
                    f"minting {sorted(set(minted))} would grow this field's "
                    f"vocabulary to {new_vocab_size}, exceeding "
                    f"crm.udf.max_tag_vocabulary={max_vocab}"
                )

        await c.execute(
            f"UPDATE {TABLE_UDF_TAG_ASSIGNMENTS} SET system_to = now() "
            f"WHERE org_id = $1::uuid AND definition_id = $2::uuid "
            f"AND target_id = $3::uuid AND system_to IS NULL",
            org_id, str(definition_id), str(target_id),
        )
        for tag_code, normalized_code in normalized:
            stored_code = first_casing.get(normalized_code, tag_code)
            await c.execute(
                f"INSERT INTO {TABLE_UDF_TAG_ASSIGNMENTS} "
                f"(org_id, definition_id, target_type, target_id, "
                f" tag_code, normalized_code, created_by) "
                f"VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6, $7::uuid)",
                org_id, str(definition_id), definition["applies_to"], str(target_id),
                stored_code, normalized_code,
                str(assigned_by) if assigned_by else None,
            )

        if definition["applies_to"] in _DUAL_WRITE_TARGET_TYPES:
            await _dual_write_position_tags(
                c, org_id=org_id, definition_id=definition_id, target_id=target_id
            )

        return await _current_assignments(c, definition_id=definition_id, target_id=target_id)


async def _current_assignments(conn, *, definition_id: str, target_id: str) -> list[dict]:
    rows = await conn.fetch(
        f"SELECT id::text AS id, tag_code, normalized_code, created_at "
        f"FROM {TABLE_UDF_TAG_ASSIGNMENTS} "
        f"WHERE definition_id = $1::uuid AND target_id = $2::uuid "
        f"AND system_to IS NULL ORDER BY tag_code",
        str(definition_id), str(target_id),
    )
    return [dict(r) for r in rows]


async def get_vocabulary(conn, *, definition_id: str) -> list[dict]:
    """Every distinct tag code ever minted for this definition, with its
    current assignment count. Deleted-then-reassigned history is excluded —
    only ``system_to IS NULL`` rows count toward "current"."""
    rows = await conn.fetch(
        f"""
        SELECT tag_code, normalized_code, count(*) AS n
        FROM {TABLE_UDF_TAG_ASSIGNMENTS}
        WHERE definition_id = $1::uuid AND system_to IS NULL
        GROUP BY normalized_code, tag_code
        ORDER BY tag_code
        """,
        str(definition_id),
    )
    return [dict(r) for r in rows]


async def merge_tags(
    conn, *, org_id: str, definition_id: str, from_code: str, into_code: str,
    changed_by: str | None,
) -> int:
    """Repoint every assignment of ``from_code`` onto ``into_code``.

    Both codes are normalized before comparison. Repointing is a
    close-then-insert per affected assignment (system axis, same convention as
    everywhere else in this layer) rather than an UPDATE of ``tag_code`` in
    place, so the assignment's own history is preserved rather than rewritten.
    Audited into ``udf_definition_audit`` against the DEFINITION (there is no
    per-tag audit table) with the merge recorded in ``after_state``.
    """
    org_id = _require_org(org_id)
    definition = await _get_tag_definition(conn, definition_id=definition_id)
    _, from_norm = normalize_tag(from_code)
    into_stored, into_norm = normalize_tag(into_code)
    if from_norm == into_norm:
        raise UdfError("from_code and into_code normalize to the same tag")

    async with _OrgWrite(conn, org_id) as c:
        rows = await c.fetch(
            f"SELECT target_id::text AS target_id FROM {TABLE_UDF_TAG_ASSIGNMENTS} "
            f"WHERE definition_id = $1::uuid AND normalized_code = $2 "
            f"AND system_to IS NULL",
            str(definition_id), from_norm,
        )
        for row in rows:
            target_id = row["target_id"]
            already = await c.fetchval(
                f"SELECT 1 FROM {TABLE_UDF_TAG_ASSIGNMENTS} "
                f"WHERE definition_id = $1::uuid AND target_id = $2::uuid "
                f"AND normalized_code = $3 AND system_to IS NULL",
                str(definition_id), target_id, into_norm,
            )
            await c.execute(
                f"UPDATE {TABLE_UDF_TAG_ASSIGNMENTS} SET system_to = now() "
                f"WHERE definition_id = $1::uuid AND target_id = $2::uuid "
                f"AND normalized_code = $3 AND system_to IS NULL",
                str(definition_id), target_id, from_norm,
            )
            if not already:
                await c.execute(
                    f"INSERT INTO {TABLE_UDF_TAG_ASSIGNMENTS} "
                    f"(org_id, definition_id, target_type, target_id, "
                    f" tag_code, normalized_code, created_by) "
                    f"VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6, $7::uuid)",
                    org_id, str(definition_id), definition["applies_to"], target_id,
                    into_stored, into_norm,
                    str(changed_by) if changed_by else None,
                )
            if definition["applies_to"] in _DUAL_WRITE_TARGET_TYPES:
                await _dual_write_position_tags(
                    c, org_id=org_id, definition_id=definition_id, target_id=target_id
                )

        await c.execute(
            f"""INSERT INTO portfolio.udf_definition_audit
                (definition_id, org_id, changed_by, change_kind, before_state, after_state)
            VALUES ($1::uuid, $2::uuid, $3::uuid, 'update', $4::jsonb, $5::jsonb)""",
            str(definition_id), org_id, str(changed_by) if changed_by else None,
            f'{{"tag_merge_from": "{from_norm}"}}',
            f'{{"tag_merge_into": "{into_norm}", "targets_repointed": {len(rows)}}}',
        )
        return len(rows)


async def rename_tag(
    conn, *, org_id: str, definition_id: str, code: str, new_label: str,
    changed_by: str | None,
) -> int:
    """Change the DISPLAY casing/spelling of a tag without changing its
    identity. Unlike :func:`merge_tags`, this does not require ``new_label``
    to already exist in the vocabulary — it becomes the vocabulary entry.
    Every CURRENT assignment's ``tag_code`` is updated in place (not
    close-then-insert: this is a display correction, not a new fact about the
    target), and the change is audited the same way a merge is.
    """
    org_id = _require_org(org_id)
    await _get_tag_definition(conn, definition_id=definition_id)
    _, norm = normalize_tag(code)
    new_stored, new_norm = normalize_tag(new_label)

    async with _OrgWrite(conn, org_id) as c:
        result = await c.execute(
            f"UPDATE {TABLE_UDF_TAG_ASSIGNMENTS} "
            f"SET tag_code = $3, normalized_code = $4 "
            f"WHERE definition_id = $1::uuid AND normalized_code = $2 "
            f"AND system_to IS NULL",
            str(definition_id), norm, new_stored, new_norm,
        )
        n = int(result.split()[-1]) if result else 0
        await c.execute(
            f"""INSERT INTO portfolio.udf_definition_audit
                (definition_id, org_id, changed_by, change_kind, before_state, after_state)
            VALUES ($1::uuid, $2::uuid, $3::uuid, 'update', $4::jsonb, $5::jsonb)""",
            str(definition_id), org_id, str(changed_by) if changed_by else None,
            f'{{"tag_rename_from": "{norm}"}}',
            f'{{"tag_rename_to": "{new_norm}", "assignments_updated": {n}}}',
        )
        return n
