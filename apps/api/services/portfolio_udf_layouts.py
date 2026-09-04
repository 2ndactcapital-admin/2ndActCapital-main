"""UDF layouts — Sprint udf01b, Task 2d/2e.

THREE-TABLE TREE, ONE LAYOUT PER (TAB, RECORD_TYPE)
──────────────────────────────────────────────────────────────────────────────
``udf_layouts`` (org_id, tab_id, record_type_id) -> ``udf_layout_sections``
(layout_id, column_count IN (1,2)) -> ``udf_layout_items`` (section_id,
definition_id NULLABLE, column_index IN (0,1), col_span IN (1,2)). Only
``udf_layouts`` carries ``org_id`` — sections and items are scoped
transitively through ``layout_id``/``section_id``, exactly what the deployed
RLS policies do (introspected in Task 1:
``udf_layout_sections_org_isolation`` and ``udf_layout_items_org_isolation``
both walk the FK chain back to ``udf_layouts.org_id`` rather than repeating
an ``org_id`` column that does not exist on those tables). Every write here
walks the same chain in Python before issuing anything, so a caller cannot
aim a section/item write at another org's layout by supplying a
looks-plausible id — RLS would refuse the row anyway, but the chain lookup is
what turns that refusal into a clean 4xx instead of a raw asyncpg error.

SPACER ITEMS
──────────────────────────────────────────────────────────────────────────────
``definition_id IS NULL`` is a spacer — a layout item occupying a grid cell
with no field behind it. The partial unique index
``udf_layout_item_def_per_section_uq`` is ``WHERE definition_id IS NOT NULL``,
so any number of spacers may coexist in one section; only a real field may
not appear twice in the same section.

col_span=2 GATE
──────────────────────────────────────────────────────────────────────────────
The deployed CHECK (``udf_layout_items_col_span_check``) only constrains
``col_span IN (1,2)`` — it has no idea what data_type the referenced
definition has, because a CHECK cannot see another table. The
``long_text``/``rich_text``-only rule for ``col_span=2`` is enforced HERE, by
joining ``udf_definitions``, exactly as the sprint prompt requires ("don't
trust the caller"). A spacer (``definition_id IS NULL``) has no definition to
join against and is exempt — it may carry either ``col_span`` value.
"""

from __future__ import annotations

from typing import Any

import asyncpg

from services.portfolio_assets import _OrgWrite, _require_org
from services.portfolio_udf import TABLE_UDF_DEFINITIONS, UdfError, _current
from services.portfolio_udf_field_permissions import ACCESS_HIDDEN, ACCESS_READ, resolve_field_access_bulk
from services.portfolio_udf_tabs import get_tab

TABLE_UDF_LAYOUTS = "portfolio.udf_layouts"
TABLE_UDF_LAYOUT_SECTIONS = "portfolio.udf_layout_sections"
TABLE_UDF_LAYOUT_ITEMS = "portfolio.udf_layout_items"

#: The two-value CHECKs, mirrored verbatim from the deployed constraints.
COLUMN_COUNTS = frozenset({1, 2})
COLUMN_INDEXES = frozenset({0, 1})
COL_SPANS = frozenset({1, 2})

#: The only data_types a col_span=2 item may reference — udf_layout_items has
#: no CHECK that can see this; it is enforced entirely in this module.
WIDE_ELIGIBLE_DATA_TYPES = frozenset({"long_text", "rich_text"})


class LayoutError(UdfError):
    """A layout write was refused for a reason the caller can fix."""


class LayoutScopeError(LayoutError):
    """The referenced layout/section/tab does not belong to the calling org."""


class LayoutDuplicateError(LayoutError):
    """A layout already exists for this (tab_id, record_type_id)."""


class LayoutCapError(LayoutError):
    """``max_sections_per_layout``/``max_items_per_section`` would be exceeded."""


class LayoutColSpanError(LayoutError):
    """``col_span=2`` requested for a definition whose data_type does not
    support it, or a duplicate real field in one section."""


class LayoutSectionReferencedError(LayoutError):
    """Soft delete refused: the section still has items.

    Carries ``references`` — the same shape ``TabReferencedError``/
    ``UdfReferencedError`` use — so the caller sees exactly what would need
    clearing first.
    """

    def __init__(self, message: str, *, references: dict[str, int] | None = None):
        super().__init__(message)
        self.references = references or {}


# ── Ownership-chain helpers ─────────────────────────────────────────────────


async def _layout_org(conn, layout_id: str) -> str | None:
    return await conn.fetchval(
        f"SELECT org_id::text FROM {TABLE_UDF_LAYOUTS} WHERE id = $1::uuid",
        str(layout_id),
    )


async def _section_layout(conn, section_id: str) -> dict | None:
    row = await conn.fetchrow(
        f"""SELECT s.id::text AS section_id, l.id::text AS layout_id,
                   l.org_id::text AS org_id
            FROM {TABLE_UDF_LAYOUT_SECTIONS} s
            JOIN {TABLE_UDF_LAYOUTS} l ON l.id = s.layout_id
            WHERE s.id = $1::uuid""",
        str(section_id),
    )
    return dict(row) if row else None


async def _item_section(conn, item_id: str) -> dict | None:
    row = await conn.fetchrow(
        f"""SELECT i.id::text AS item_id, i.section_id::text AS section_id,
                   i.definition_id::text AS definition_id,
                   s.layout_id::text AS layout_id, l.org_id::text AS org_id
            FROM {TABLE_UDF_LAYOUT_ITEMS} i
            JOIN {TABLE_UDF_LAYOUT_SECTIONS} s ON s.id = i.section_id
            JOIN {TABLE_UDF_LAYOUTS} l ON l.id = s.layout_id
            WHERE i.id = $1::uuid""",
        str(item_id),
    )
    return dict(row) if row else None


# ── Layout ───────────────────────────────────────────────────────────────────


_LAYOUT_SELECT = """
    id::text AS id, org_id::text AS org_id, tab_id::text AS tab_id,
    record_type_id::text AS record_type_id, created_at,
    created_by::text AS created_by
"""


async def get_layout(conn, *, layout_id: str) -> dict | None:
    row = await conn.fetchrow(
        f"SELECT {_LAYOUT_SELECT} FROM {TABLE_UDF_LAYOUTS} WHERE id = $1::uuid",
        str(layout_id),
    )
    return dict(row) if row else None


async def get_layout_by_tab(
    conn, *, tab_id: str, record_type_id: str | None = None
) -> dict | None:
    row = await conn.fetchrow(
        f"""SELECT {_LAYOUT_SELECT} FROM {TABLE_UDF_LAYOUTS}
            WHERE tab_id = $1::uuid
              AND record_type_id IS NOT DISTINCT FROM $2::uuid""",
        str(tab_id), str(record_type_id) if record_type_id else None,
    )
    return dict(row) if row else None


async def create_layout(
    conn, *, org_id: str, tab_id: str, record_type_id: str | None = None,
    created_by: str | None = None,
) -> dict:
    org_id = _require_org(org_id)
    tab = await get_tab(conn, tab_id=tab_id)
    if tab is None:
        raise LayoutError(f"tab {tab_id} does not exist or is deleted")
    if tab["org_id"] != org_id:
        raise LayoutScopeError(f"tab {tab_id} belongs to a different org")
    async with _OrgWrite(conn, org_id) as c:
        try:
            row = await c.fetchrow(
                f"""INSERT INTO {TABLE_UDF_LAYOUTS}
                        (org_id, tab_id, record_type_id, created_by)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid)
                    RETURNING id::text""",
                org_id, str(tab_id),
                str(record_type_id) if record_type_id else None,
                str(created_by) if created_by else None,
            )
        except asyncpg.UniqueViolationError as exc:
            raise LayoutDuplicateError(
                f"a layout already exists for tab {tab_id} "
                f"record_type_id={record_type_id!r} — refused by "
                f"udf_layout_one_per_tab_uq"
            ) from exc
        return await get_layout(c, layout_id=row["id"])


async def get_or_create_layout(
    conn, *, org_id: str, tab_id: str, record_type_id: str | None = None,
    created_by: str | None = None,
) -> dict:
    """The tab's layout, creating it lazily on first use.

    The router's section-add endpoints are nested under ``tab_id``, not a
    layout id — there is no separate "create layout" route in this sprint's
    endpoint list, so the first section added to a tab implicitly creates its
    (single, default) layout. A duplicate-key race between two concurrent
    first-adds is caught the same way :func:`create_layout` already handles
    it: re-fetch on conflict rather than erroring.
    """
    org_id = _require_org(org_id)
    existing = await get_layout_by_tab(conn, tab_id=tab_id, record_type_id=record_type_id)
    if existing is not None:
        return existing
    try:
        return await create_layout(
            conn, org_id=org_id, tab_id=tab_id, record_type_id=record_type_id,
            created_by=created_by,
        )
    except LayoutDuplicateError:
        layout = await get_layout_by_tab(conn, tab_id=tab_id, record_type_id=record_type_id)
        if layout is None:  # pragma: no cover — the race that just lost lost to a delete too
            raise
        return layout


# ── Sections ─────────────────────────────────────────────────────────────────


_SECTION_SELECT = """
    id::text AS id, layout_id::text AS layout_id, title, display_order,
    column_count
"""


async def _count_sections(conn, *, layout_id: str) -> int:
    return await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_LAYOUT_SECTIONS} WHERE layout_id = $1::uuid",
        str(layout_id),
    )


async def add_section(
    conn, *, layout_id: str, org_id: str, title: str | None = None,
    column_count: int = 2, display_order: int = 0,
) -> dict:
    from services.org_settings import get_setting

    org_id = _require_org(org_id)
    layout_org = await _layout_org(conn, layout_id)
    if layout_org is None:
        raise LayoutError(f"layout {layout_id} does not exist")
    if layout_org != org_id:
        raise LayoutScopeError(f"layout {layout_id} belongs to a different org")
    if column_count not in COLUMN_COUNTS:
        raise LayoutError(f"column_count must be one of {sorted(COLUMN_COUNTS)}")

    async with _OrgWrite(conn, org_id) as c:
        cap = int(await get_setting(c, org_id, "crm.udf.max_sections_per_layout"))
        count = await _count_sections(c, layout_id=layout_id)
        if count >= cap:
            raise LayoutCapError(
                f"layout {layout_id} already has {count} section(s); "
                f"crm.udf.max_sections_per_layout={cap}"
            )
        row = await c.fetchrow(
            f"""INSERT INTO {TABLE_UDF_LAYOUT_SECTIONS}
                    (layout_id, title, display_order, column_count)
                VALUES ($1::uuid, $2, $3, $4)
                RETURNING {_SECTION_SELECT}""",
            str(layout_id), title, int(display_order), int(column_count),
        )
        return dict(row)


async def reorder_sections(
    conn, *, layout_id: str, org_id: str, ordered_section_ids: list[str],
) -> list[dict]:
    org_id = _require_org(org_id)
    layout_org = await _layout_org(conn, layout_id)
    if layout_org is None:
        raise LayoutError(f"layout {layout_id} does not exist")
    if layout_org != org_id:
        raise LayoutScopeError(f"layout {layout_id} belongs to a different org")

    existing = await conn.fetch(
        f"SELECT id::text AS id FROM {TABLE_UDF_LAYOUT_SECTIONS} WHERE layout_id = $1::uuid",
        str(layout_id),
    )
    existing_ids = {r["id"] for r in existing}
    requested_ids = {str(i) for i in ordered_section_ids}
    if existing_ids != requested_ids:
        raise LayoutError(
            f"ordered_section_ids must be exactly this layout's section ids — "
            f"got {sorted(requested_ids)}, layout has {sorted(existing_ids)}"
        )
    async with _OrgWrite(conn, org_id) as c:
        for order, section_id in enumerate(ordered_section_ids):
            await c.execute(
                f"UPDATE {TABLE_UDF_LAYOUT_SECTIONS} SET display_order = $2 "
                f"WHERE id = $1::uuid",
                str(section_id), order,
            )
        rows = await c.fetch(
            f"SELECT {_SECTION_SELECT} FROM {TABLE_UDF_LAYOUT_SECTIONS} "
            f"WHERE layout_id = $1::uuid ORDER BY display_order",
            str(layout_id),
        )
        return [dict(r) for r in rows]


async def remove_section(conn, *, section_id: str, org_id: str) -> None:
    """Refused, not cascaded, if the section still has items — mirrors
    ``soft_delete_tab``'s reference-blocking principle so a section can never
    take its items down with it silently."""
    org_id = _require_org(org_id)
    current = await _section_layout(conn, section_id)
    if current is None:
        raise LayoutError(f"section {section_id} does not exist")
    if current["org_id"] != org_id:
        raise LayoutScopeError(f"section {section_id} belongs to a different org")
    count = await _count_items(conn, section_id=section_id)
    if count:
        raise LayoutSectionReferencedError(
            f"section {section_id} has {count} item(s) and cannot be removed: "
            f"udf_layout_items={count}. Remove the items first.",
            references={"udf_layout_items": count},
        )
    async with _OrgWrite(conn, org_id) as c:
        await c.execute(
            f"DELETE FROM {TABLE_UDF_LAYOUT_SECTIONS} WHERE id = $1::uuid", str(section_id)
        )


# ── Items ────────────────────────────────────────────────────────────────────


_ITEM_SELECT = """
    id::text AS id, section_id::text AS section_id,
    definition_id::text AS definition_id, display_order, column_index,
    col_span, is_read_only
"""


async def _count_items(conn, *, section_id: str) -> int:
    return await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_UDF_LAYOUT_ITEMS} WHERE section_id = $1::uuid",
        str(section_id),
    )


async def _validate_col_span(conn, *, definition_id: str | None, col_span: int) -> None:
    if col_span not in COL_SPANS:
        raise LayoutError(f"col_span must be one of {sorted(COL_SPANS)}")
    if definition_id is None or col_span != 2:
        return
    data_type = await conn.fetchval(
        f"SELECT data_type FROM {TABLE_UDF_DEFINITIONS} d "
        f"WHERE d.id = $1::uuid AND {_current('d')} AND d.deleted_at IS NULL",
        str(definition_id),
    )
    if data_type is None:
        raise LayoutError(f"definition {definition_id} does not exist or is deleted")
    if data_type not in WIDE_ELIGIBLE_DATA_TYPES:
        raise LayoutColSpanError(
            f"col_span=2 is only valid for data_type IN "
            f"{sorted(WIDE_ELIGIBLE_DATA_TYPES)} — definition {definition_id} "
            f"has data_type={data_type!r}"
        )


async def add_item(
    conn, *, section_id: str, org_id: str, definition_id: str | None = None,
    display_order: int = 0, column_index: int = 0, col_span: int = 1,
    is_read_only: bool = False,
) -> dict:
    from services.org_settings import get_setting

    org_id = _require_org(org_id)
    section = await _section_layout(conn, section_id)
    if section is None:
        raise LayoutError(f"section {section_id} does not exist")
    if section["org_id"] != org_id:
        raise LayoutScopeError(f"section {section_id} belongs to a different org")
    if column_index not in COLUMN_INDEXES:
        raise LayoutError(f"column_index must be one of {sorted(COLUMN_INDEXES)}")
    await _validate_col_span(conn, definition_id=definition_id, col_span=col_span)

    async with _OrgWrite(conn, org_id) as c:
        cap = int(await get_setting(c, org_id, "crm.udf.max_items_per_section"))
        count = await _count_items(c, section_id=section_id)
        if count >= cap:
            raise LayoutCapError(
                f"section {section_id} already has {count} item(s); "
                f"crm.udf.max_items_per_section={cap}"
            )
        try:
            row = await c.fetchrow(
                f"""INSERT INTO {TABLE_UDF_LAYOUT_ITEMS}
                        (section_id, definition_id, display_order, column_index,
                         col_span, is_read_only)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6)
                    RETURNING {_ITEM_SELECT}""",
                str(section_id), str(definition_id) if definition_id else None,
                int(display_order), int(column_index), int(col_span),
                bool(is_read_only),
            )
        except asyncpg.UniqueViolationError as exc:
            raise LayoutColSpanError(
                f"definition {definition_id} already appears in section "
                f"{section_id} — refused by "
                f"udf_layout_item_def_per_section_uq"
            ) from exc
        return dict(row)


async def move_item(
    conn, *, item_id: str, org_id: str, section_id: str | None = None,
    column_index: int | None = None, col_span: int | None = None,
    display_order: int | None = None,
) -> dict:
    """Change section/column/order. ``definition_id`` never changes here —
    only :func:`add_item`/:func:`remove_item` create or destroy the
    field-to-item binding."""
    org_id = _require_org(org_id)
    current = await _item_section(conn, item_id)
    if current is None:
        raise LayoutError(f"item {item_id} does not exist")
    if current["org_id"] != org_id:
        raise LayoutScopeError(f"item {item_id} belongs to a different org")

    target_section_id = section_id or current["section_id"]
    if section_id is not None and section_id != current["section_id"]:
        new_section = await _section_layout(conn, section_id)
        if new_section is None:
            raise LayoutError(f"section {section_id} does not exist")
        if new_section["org_id"] != org_id:
            raise LayoutScopeError(f"section {section_id} belongs to a different org")
        if new_section["layout_id"] != current["layout_id"]:
            raise LayoutError(
                f"section {section_id} belongs to a different layout than "
                f"item {item_id}'s current section — move_item does not "
                f"relayout an item across layouts"
            )

    effective_col_span = col_span if col_span is not None else await conn.fetchval(
        f"SELECT col_span FROM {TABLE_UDF_LAYOUT_ITEMS} WHERE id = $1::uuid", str(item_id)
    )
    if col_span is not None:
        await _validate_col_span(
            conn, definition_id=current["definition_id"], col_span=effective_col_span
        )
    if column_index is not None and column_index not in COLUMN_INDEXES:
        raise LayoutError(f"column_index must be one of {sorted(COLUMN_INDEXES)}")

    set_clauses = []
    params: list[Any] = [str(item_id)]
    for name, value in (
        ("section_id", target_section_id if section_id is not None else None),
        ("column_index", column_index),
        ("col_span", col_span),
        ("display_order", display_order),
    ):
        if value is None:
            continue
        params.append(value)
        cast = "::uuid" if name == "section_id" else ""
        set_clauses.append(f"{name} = ${len(params)}{cast}")

    async with _OrgWrite(conn, org_id) as c:
        if set_clauses:
            await c.execute(
                f"UPDATE {TABLE_UDF_LAYOUT_ITEMS} SET {', '.join(set_clauses)} "
                f"WHERE id = $1::uuid",
                *params,
            )
        row = await c.fetchrow(
            f"SELECT {_ITEM_SELECT} FROM {TABLE_UDF_LAYOUT_ITEMS} WHERE id = $1::uuid",
            str(item_id),
        )
        return dict(row)


async def remove_item(conn, *, item_id: str, org_id: str) -> None:
    org_id = _require_org(org_id)
    current = await _item_section(conn, item_id)
    if current is None:
        raise LayoutError(f"item {item_id} does not exist")
    if current["org_id"] != org_id:
        raise LayoutScopeError(f"item {item_id} belongs to a different org")
    async with _OrgWrite(conn, org_id) as c:
        await c.execute(
            f"DELETE FROM {TABLE_UDF_LAYOUT_ITEMS} WHERE id = $1::uuid", str(item_id)
        )


# ── Task 2e: resolution for rendering ───────────────────────────────────────


async def get_resolved_layout(
    conn, *, tab_id: str, org_id: str, user_id: str, record_type_id: str | None = None,
) -> dict:
    """The full section -> item tree for one tab, each item enriched with its
    definition's ``label``/``data_type``/``type_params``/``is_required`` so
    the frontend can render without a second round-trip.

    Returns a structure with ``layout_id: None`` and ``sections: []`` when no
    layout has been configured for this tab yet — that is a normal state
    (a brand-new tab has no layout), not an error.

    Sprint udf01c, Task 2e — field-level security applied here, on TOP of
    whatever tab-visibility gate the caller already ran (the router 403s
    before this is even called if the tab itself is hidden). A ``'hidden'``
    field's layout item is dropped from its section's ``items`` list
    entirely — not returned with a null definition, not returned empty. A
    ``'read'`` field's item is flagged ``is_read_only: true`` regardless of
    what the layout itself set, combined with the layout's own
    ``is_read_only`` by most-restrictive-wins (boolean OR — a layout that
    already said read-only stays read-only no matter what FLS says; FLS
    saying read forces it true even over a layout that said false). An
    ``'edit'`` field's item is unchanged. A spacer item (``definition_id IS
    NULL``) has no field to resolve access for and is always kept as-is.
    """
    org_id = _require_org(org_id)
    layout = await get_layout_by_tab(conn, tab_id=tab_id, record_type_id=record_type_id)
    if layout is None:
        return {
            "layout_id": None, "tab_id": str(tab_id),
            "record_type_id": str(record_type_id) if record_type_id else None,
            "sections": [],
        }
    if layout["org_id"] != org_id:
        raise LayoutScopeError(f"layout for tab {tab_id} belongs to a different org")

    section_rows = await conn.fetch(
        f"SELECT {_SECTION_SELECT} FROM {TABLE_UDF_LAYOUT_SECTIONS} "
        f"WHERE layout_id = $1::uuid ORDER BY display_order",
        layout["id"],
    )
    item_rows = await conn.fetch(
        f"""SELECT i.id::text AS id, i.section_id::text AS section_id,
                   i.definition_id::text AS definition_id, i.display_order,
                   i.column_index, i.col_span, i.is_read_only,
                   d.label AS label, d.data_type AS data_type,
                   d.type_params AS type_params, d.is_required AS is_required
            FROM {TABLE_UDF_LAYOUT_ITEMS} i
            LEFT JOIN {TABLE_UDF_DEFINITIONS} d
              ON d.id = i.definition_id AND {_current('d')} AND d.deleted_at IS NULL
            WHERE i.section_id IN (
                SELECT id FROM {TABLE_UDF_LAYOUT_SECTIONS} WHERE layout_id = $1::uuid
            )
            ORDER BY i.column_index, i.display_order""",
        layout["id"],
    )
    def_ids = [r["definition_id"] for r in item_rows if r["definition_id"] is not None]
    access_map = await resolve_field_access_bulk(
        conn, definition_ids=def_ids, tab_id=tab_id, org_id=org_id, user_id=user_id,
    )

    items_by_section: dict[str, list[dict]] = {}
    for r in item_rows:
        row = dict(r)
        tp = row.get("type_params")
        if isinstance(tp, str):
            import json

            row["type_params"] = json.loads(tp)
        definition_id = row["definition_id"]
        if definition_id is not None:
            access = access_map.get(definition_id, ACCESS_HIDDEN)
            if access == ACCESS_HIDDEN:
                continue
            if access == ACCESS_READ:
                row["is_read_only"] = True
        items_by_section.setdefault(row["section_id"], []).append(row)

    sections = []
    for s in section_rows:
        section = dict(s)
        section["items"] = items_by_section.get(section["id"], [])
        sections.append(section)

    return {
        "layout_id": layout["id"], "tab_id": str(tab_id),
        "record_type_id": layout["record_type_id"],
        "sections": sections,
    }
