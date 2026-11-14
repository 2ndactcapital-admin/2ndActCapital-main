"""UDFUI1 — the reads the custom-fields builder needs and the visible-only reads hide.

``GET /udf/definitions`` and ``GET /udf/tabs`` return only what is ACTIVE, non-deleted and visible to the caller —
right for the record page, wrong for an admin who must see (and reverse) a deactivated or deleted field. These are
read-only helpers; every write still goes through the existing lifecycle functions in ``services.portfolio_udf`` /
``services.portfolio_udf_tabs`` / ``services.portfolio_udf_layouts``. Nothing here decides access: the preview calls
the EXISTING ``resolve_tab_visibility`` / ``resolve_field_access_bulk`` for the chosen person.
"""

from __future__ import annotations

import json
from typing import Any

from services.portfolio_assets import _require_org
from services.portfolio_udf import (
    APPLIES_TO,
    TABLE_UDF_DEFINITION_AUDIT,
    TABLE_UDF_DEFINITIONS,
    TABLE_UDF_TAG_ASSIGNMENTS,
    TABLE_UDF_VALUES,
    TYPE_PARAM_OPTIONAL,
    TYPE_PARAM_REQUIRED,
    UdfError,
    _DEF_SELECT,
    _check_choice,
    _current,
    _definition_row,
    resolve_visible_definitions,
)
from services.portfolio_udf_field_permissions import resolve_field_access_bulk
from services.portfolio_udf_tabs import (
    TABLE_UDF_LAYOUT_ITEMS,
    TABLE_UDF_LAYOUT_SECTIONS,
    TABLE_UDF_LAYOUTS,
    TABLE_UDF_TAB_PERMISSIONS,
    TABLE_UDF_TABS,
    list_active_tabs,
    resolve_tab_visibility,
)

#: The definition's lifecycle state, derived from the row (never stored).
STATUS_ACTIVE, STATUS_DEACTIVATED, STATUS_DELETED = "active", "deactivated", "deleted"


def lifecycle_status(row: dict) -> str:
    if row.get("deleted_at") is not None:
        return STATUS_DELETED
    return STATUS_ACTIVE if row.get("is_active") else STATUS_DEACTIVATED


def type_param_rules() -> dict[str, dict[str, list[str]]]:
    """Per data type, the type_params it requires / may carry — the engine's own tables, published so the form shows
    exactly the options the schema supports (Rule 1: no client-side copy)."""
    return {t: {"required": list(TYPE_PARAM_REQUIRED[t]), "optional": list(TYPE_PARAM_OPTIONAL.get(t, ()))}
            for t in sorted(TYPE_PARAM_REQUIRED)}


async def list_admin_definitions(conn, *, org_id: str, target_type: str | None) -> list[dict]:
    """Every CURRENT definition of this org (any scope) plus every platform definition — active, deactivated and
    soft-deleted — with ``status``, ``used_by`` (distinct records holding a current value or a tag) and
    ``placements`` (tab / section). Org filter repeated in SQL (a Super-Admin connection bypasses RLS)."""
    org_id = _require_org(org_id)
    params: list[Any] = [org_id]
    where = f"{_current('d')} AND (d.owner_scope = 'platform' OR d.org_id = $1::uuid)"
    if target_type:
        params.append(_check_choice(target_type, APPLIES_TO, "target_type"))
        where += " AND d.applies_to = $2"
    rows = await conn.fetch(
        f"""SELECT {_DEF_SELECT},
                   (SELECT count(DISTINCT v.target_id) FROM {TABLE_UDF_VALUES} v
                     WHERE v.definition_id = d.id AND v.org_id = $1::uuid
                       AND v.valid_to IS NULL AND v.system_to IS NULL) AS value_targets,
                   (SELECT count(DISTINCT t.target_id) FROM {TABLE_UDF_TAG_ASSIGNMENTS} t
                     WHERE t.definition_id = d.id AND t.org_id = $1::uuid) AS tag_targets,
                   (SELECT count(*) FROM {TABLE_UDF_VALUES} v WHERE v.definition_id = d.id) AS value_rows
            FROM {TABLE_UDF_DEFINITIONS} d
            WHERE {where}
            ORDER BY d.applies_to, d.display_order, d.label, d.id""",
        *params,
    )
    defs = []
    for r in rows:
        row = dict(r)
        value_targets, tag_targets, value_rows = (row.pop("value_targets"), row.pop("tag_targets"),
                                                  row.pop("value_rows"))
        row = _definition_row(row)
        row["status"] = lifecycle_status(row)
        row["used_by"] = int(max(value_targets or 0, tag_targets or 0))
        row["value_rows"] = int(value_rows or 0)
        row["placements"] = []
        defs.append(row)
    if not defs:
        return defs
    placed = await conn.fetch(
        f"""SELECT i.definition_id::text AS definition_id, i.id::text AS item_id,
                   s.id::text AS section_id, s.title AS section_title,
                   t.id::text AS tab_id, t.label AS tab_label
            FROM {TABLE_UDF_LAYOUT_ITEMS} i
            JOIN {TABLE_UDF_LAYOUT_SECTIONS} s ON s.id = i.section_id
            JOIN {TABLE_UDF_LAYOUTS} l ON l.id = s.layout_id
            JOIN {TABLE_UDF_TABS} t ON t.id = l.tab_id
            WHERE i.definition_id = ANY($1::uuid[]) AND l.org_id = $2::uuid
            ORDER BY t.display_order, t.label, s.display_order""",
        [d["id"] for d in defs], org_id,
    )
    by_id = {d["id"]: d for d in defs}
    for p in placed:
        by_id[p["definition_id"]]["placements"].append(
            {k: p[k] for k in ("tab_id", "tab_label", "section_id", "section_title", "item_id")})
    return defs


async def list_value_sets(conn, *, org_id: str) -> list[dict]:
    """The value sets (``public.reference_data_lists``) a select / multiselect may point at: the org's own and the
    platform's (org_id NULL), active only, with their active codes."""
    org_id = _require_org(org_id)
    rows = await conn.fetch(
        """SELECT l.id::text AS id, l.list_key, l.label, l.org_id::text AS org_id,
                  COALESCE((SELECT json_agg(json_build_object('code', d.code, 'label', d.label)
                                            ORDER BY d.display_order, d.code)
                            FROM public.reference_data d WHERE d.list_id = l.id AND d.is_active), '[]') AS items
           FROM public.reference_data_lists l
           WHERE l.is_active AND (l.org_id IS NULL OR l.org_id = $1::uuid)
           ORDER BY l.label, l.list_key""",
        org_id,
    )
    out = []
    for r in rows:
        row = dict(r)
        if isinstance(row["items"], str):
            row["items"] = json.loads(row["items"])
        out.append(row)
    return out


async def list_definition_audit(conn, *, org_id: str, definition_id: str) -> list[dict]:
    """The definition's audit rows, newest first, with the changer's name and the keys that changed."""
    org_id = _require_org(org_id)
    rows = await conn.fetch(
        f"""SELECT a.id::text AS id, a.definition_id::text AS definition_id, a.change_kind, a.changed_at,
                   a.changed_by::text AS changed_by, u.full_name AS changed_by_name, u.email AS changed_by_email,
                   a.before_state, a.after_state
            FROM {TABLE_UDF_DEFINITION_AUDIT} a
            LEFT JOIN public.users u ON u.id = a.changed_by
            WHERE a.definition_id = $1::uuid AND (a.org_id = $2::uuid OR a.org_id IS NULL)
            ORDER BY a.changed_at DESC, a.id""",
        str(definition_id), org_id,
    )
    out = []
    for r in rows:
        row = dict(r)
        before = row.pop("before_state")
        after = row.pop("after_state")
        before = json.loads(before) if isinstance(before, str) else (before or {})
        after = json.loads(after) if isinstance(after, str) else (after or {})
        skip = {"updated_at"}
        row["changed_fields"] = sorted(k for k in set(before) | set(after)
                                       if k not in skip and before.get(k) != after.get(k))
        row["changes"] = {k: {"before": before.get(k), "after": after.get(k)} for k in row["changed_fields"]}
        out.append(row)
    return out


async def list_admin_tabs(conn, *, org_id: str, applies_to: str | None) -> list[dict]:
    """Every tab of this org (active, deactivated, deleted) with ``status`` and its section / item counts."""
    org_id = _require_org(org_id)
    params: list[Any] = [org_id]
    where = "t.org_id = $1::uuid"
    if applies_to:
        params.append(_check_choice(applies_to, APPLIES_TO, "applies_to"))
        where += " AND t.applies_to = $2"
    rows = await conn.fetch(
        f"""SELECT t.id::text AS id, t.org_id::text AS org_id, t.applies_to, t.label, t.api_name, t.display_order,
                   t.is_active, t.created_at, t.created_by::text AS created_by, t.deleted_at,
                   t.deleted_by::text AS deleted_by,
                   (SELECT count(*) FROM {TABLE_UDF_LAYOUT_SECTIONS} s JOIN {TABLE_UDF_LAYOUTS} l
                      ON l.id = s.layout_id WHERE l.tab_id = t.id) AS section_count,
                   (SELECT count(*) FROM {TABLE_UDF_LAYOUT_ITEMS} i JOIN {TABLE_UDF_LAYOUT_SECTIONS} s
                      ON s.id = i.section_id JOIN {TABLE_UDF_LAYOUTS} l ON l.id = s.layout_id
                     WHERE l.tab_id = t.id) AS item_count
            FROM {TABLE_UDF_TABS} t
            WHERE {where}
            ORDER BY t.applies_to, t.display_order, t.label, t.id""",
        *params,
    )
    out = []
    for r in rows:
        row = dict(r)
        row["status"] = lifecycle_status(row)
        row["section_count"] = int(row["section_count"] or 0)
        row["item_count"] = int(row["item_count"] or 0)
        out.append(row)
    return out


async def list_tab_grants(conn, *, tab_id: str) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT id::text AS id, tab_id::text AS tab_id, profile_id::text AS profile_id,
                   permission_set_id::text AS permission_set_id, is_visible
            FROM {TABLE_UDF_TAB_PERMISSIONS} WHERE tab_id = $1::uuid ORDER BY id""",
        str(tab_id),
    )
    return [dict(r) for r in rows]


async def preview_access(conn, *, org_id: str, user_id: str, target_type: str) -> dict:
    """What ``user_id`` would see on a ``target_type`` record — tabs visible / hidden, and each field hidden / read /
    edit, both on its own (no tab: the values / definitions endpoints) and on each tab it is placed on (the record
    page). Exactly the existing resolvers; nothing is recomputed here."""
    org_id = _require_org(org_id)
    target_type = _check_choice(target_type, APPLIES_TO, "target_type")
    member = await conn.fetchval(
        "SELECT 1 FROM public.users WHERE id = $1::uuid AND org_id = $2::uuid", str(user_id), org_id)
    if not member:
        raise UdfError(f"user {user_id} is not a person of this org")
    tabs = await list_active_tabs(conn, org_id=org_id, applies_to=target_type)
    tab_out = []
    for t in tabs:
        visible = await resolve_tab_visibility(conn, tab_id=t["id"], org_id=org_id, user_id=user_id)
        tab_out.append({"tab_id": t["id"], "label": t["label"], "visible": bool(visible)})
    defs = await resolve_visible_definitions(conn, org_id=org_id, user_id=user_id, applies_to=target_type)
    def_ids = [d["id"] for d in defs]
    no_tab = await resolve_field_access_bulk(conn, definition_ids=def_ids, tab_id=None, org_id=org_id,
                                             user_id=user_id)
    placed = await conn.fetch(
        f"""SELECT DISTINCT l.tab_id::text AS tab_id, i.definition_id::text AS definition_id
            FROM {TABLE_UDF_LAYOUT_ITEMS} i
            JOIN {TABLE_UDF_LAYOUT_SECTIONS} s ON s.id = i.section_id
            JOIN {TABLE_UDF_LAYOUTS} l ON l.id = s.layout_id
            WHERE l.org_id = $1::uuid AND i.definition_id = ANY($2::uuid[])""",
        org_id, def_ids,
    )
    by_tab: dict[str, list[str]] = {}
    for p in placed:
        by_tab.setdefault(p["tab_id"], []).append(p["definition_id"])
    on_tab: dict[str, dict[str, str]] = {}
    active_tab_ids = {t["tab_id"] for t in tab_out}
    for tab_id, ids in by_tab.items():
        if tab_id not in active_tab_ids:
            continue
        on_tab[tab_id] = await resolve_field_access_bulk(conn, definition_ids=ids, tab_id=tab_id, org_id=org_id,
                                                         user_id=user_id)
    fields = []
    for d in defs:
        fields.append({
            "definition_id": d["id"], "label": d["label"], "data_type": d["data_type"],
            "owner_scope": d["owner_scope"], "access": no_tab.get(d["id"]),
            "on_tabs": [{"tab_id": tid, "access": m[d["id"]]} for tid, m in on_tab.items() if d["id"] in m],
        })
    return {"user_id": str(user_id), "target_type": target_type, "tabs": tab_out, "fields": fields,
            "resolution": ["resolve_tab_visibility", "resolve_field_access_bulk"]}
