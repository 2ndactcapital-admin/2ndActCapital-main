"""UDF-backed DataGrid columns and list filters — Sprint udf02a.

READ-ONLY. No new DDL, no write path — CSV import (Sprint 2b) is the only
other read/write surface planned for this layer. Everything here composes
building blocks that already exist and are load-bearing elsewhere:
:func:`~services.portfolio_udf_field_permissions.resolve_field_access_bulk`
for per-field FLS, :func:`~services.portfolio_udf_tabs.resolve_tab_visibility`
for tab gating (called INSIDE ``resolve_field_access_bulk`` already — never
re-called redundantly here), and :func:`~services.portfolio_udf.
resolve_visible_definitions` for the four-namespace (platform/org/team/user)
scope resolution. Nothing in this module re-implements any of those checks.

COLUMN AVAILABILITY — TWO CANDIDATE SOURCES, NOT ONE
──────────────────────────────────────────────────────────────────────────────
``get_available_columns`` mirrors ``portfolio_udf_layouts.get_resolved_layout``
exactly, per the sprint's own instruction, which means it does NOT treat "tab
given" and "no tab given" as the same query with an extra filter bolted on —
they have genuinely different candidate sources:

* **No ``tab_id``** (mirrors ``GET /udf/definitions``): candidates are every
  definition :func:`resolve_visible_definitions` returns for this user and
  ``target_type`` — the four-namespace union, independent of any layout.
* **A ``tab_id``** (mirrors ``GET /udf/layouts/{tab_id}``): candidates are the
  definitions actually PLACED on that tab's layout, via
  ``udf_layout_items``/``udf_layout_sections``/``udf_layouts`` — the same join
  ``get_resolved_layout`` uses. This does NOT additionally intersect with
  ``resolve_visible_definitions``: ``get_resolved_layout`` doesn't either (it
  joins ``udf_layout_items`` to ``udf_definitions`` directly, gated only by
  FLS), and this sprint's instruction was to reuse that behaviour exactly, not
  to add a second scope check nothing else in this layer performs.

``record_type_id`` is always passed as ``NULL`` here, matching the registered
udf01b debt (FIND 1: 0 rows anywhere carry a non-null ``record_type_id`` and
no code reads/writes it) — a grid column list is not record-type-scoped in
practice today, and there is no deployed data to prove a different join
correct.

FILTER SAFETY
──────────────────────────────────────────────────────────────────────────────
Every filter value is bound as a query parameter — never interpolated into
SQL text. ``build_filter_clause`` appends to a single, shared ``params`` list
per request and references each value by its resulting position, so N filters
compose into ONE statement without any renumbering pass.

Numeric filters reuse ``portfolio_udf._numeric`` (refuses ``float``, same as
every other numeric UDF write) but do NOT reuse ``_coerce_numeric_value``
wholesale: that function also enforces the field's ``min``/``max`` bounds,
which are bounds on a STORED value, not on a comparison operand — a `gt`
filter above the field's own declared max is a legitimate query that should
return zero rows, not a validation error. Scale/precision ARE re-checked here
(reject, never round) because a value with more fractional digits than the
column's declared scale cannot mean what the caller thinks it means.

QUERY-COUNT DISCIPLINE
──────────────────────────────────────────────────────────────────────────────
``list_records_with_udf`` issues a query count that does NOT grow with the
number of returned records or the number of available UDF columns: one query
for available-column resolution's own bulk access map (already O(1) in N/M,
per ``resolve_field_access_bulk``'s docstring), one query for the base rows
(all requested filters/sort are joins in that SAME statement), and ONE bulk
query each for udf_values and udf_tag_assignments inlining — keyed by
``target_id = ANY($ids) AND definition_id = ANY($def_ids)`` rather than one
query per row or per column. The verification measures and reports the real
count for a representative (10 records x 5 columns) case rather than
asserting a hardcoded number.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from services.portfolio_assets import _require_org
from services.portfolio_udf import (
    APPLIES_TO,
    CHOICE_TYPES,
    NUMERIC_TYPES,
    TABLE_UDF_DEFINITIONS,
    TABLE_UDF_TAG_ASSIGNMENTS,
    TABLE_UDF_VALUES,
    TEXT_TYPES,
    UdfError,
    _boolean,
    _check_choice,
    _current,
    _date,
    _datetime,
    _numeric,
    _value_set_codes,
    resolve_visible_definitions,
)
from services.portfolio_udf_field_permissions import (
    ACCESS_EDIT,
    ACCESS_HIDDEN,
    resolve_field_access_bulk,
)
from services.portfolio_udf_layouts import (
    TABLE_UDF_LAYOUT_ITEMS,
    TABLE_UDF_LAYOUT_SECTIONS,
    TABLE_UDF_LAYOUTS,
)
from services.portfolio_udf_tags import normalize_tag

#: Every ``applies_to``/``target_type`` maps to exactly one base table — the
#: real, deployed record a UDF value or tag assignment is keyed against.
#: All six are bi-temporal (``valid_from``/``valid_to``/``system_from``/
#: ``system_to``) and carry ``id``/``org_id`` — introspected from
#: docs/schema_snapshot.sql, not assumed.
TARGET_TABLES: dict[str, str] = {
    "asset": "portfolio.assets",
    "position": "portfolio.positions",
    "valuation": "portfolio.valuations",
    "transaction": "portfolio.transactions",
    "commitment": "portfolio.commitments",
    "entity": "public.entities",
}


class RecordListError(UdfError):
    """A list/filter/sort request was refused for a reason the caller can fix."""


class FilterOperatorError(RecordListError):
    """``operator`` is not valid for this field's ``data_type``."""


class FilterValueError(RecordListError):
    """The filter ``value`` fails the field's own type contract."""


class FilterFieldError(RecordListError):
    """``definition_id`` is not an available column for this caller.

    Refusing here — rather than silently ignoring the filter or resolving it
    anyway — matters for the same reason a hidden field never appears in a
    row payload: a filter is itself a side-channel a hidden field's EXISTENCE
    could leak through (a non-matching vs. zero-row response already reveals
    whether the field exists) if this were allowed to run as an ordinary
    filter, so it is refused before any UDF-table query touches it at all.
    """


class SortFieldError(RecordListError):
    """``sort``'s ``definition_id`` is not an available/sortable column."""


# ── Operator vocabulary per data_type ────────────────────────────────────────

_TEXT_OPS = frozenset({"contains", "equals"})
_NUMERIC_OPS = frozenset({"equals", "gt", "lt", "between"})
_DATE_OPS = frozenset({"equals", "before", "after", "between"})
_BOOLEAN_OPS = frozenset({"equals"})
_CHOICE_OPS = frozenset({"in", "not-in"})
_TAGS_OPS = frozenset({"has-tag", "has-any-of", "has-all-of"})

#: ``rich_text`` is deliberately absent from the sprint's own operator table
#: (only text/long_text/email/url/phone got contains/equals) — a rich-text
#: value is markup, not a comparable scalar, so it gets NO valid operator
#: rather than inheriting the plain-text set silently.
OPERATORS_BY_DATA_TYPE: dict[str, frozenset[str]] = {
    "text": _TEXT_OPS, "long_text": _TEXT_OPS, "email": _TEXT_OPS,
    "url": _TEXT_OPS, "phone": _TEXT_OPS,
    "integer": _NUMERIC_OPS, "numeric": _NUMERIC_OPS,
    "currency": _NUMERIC_OPS, "percent": _NUMERIC_OPS,
    "date": _DATE_OPS, "datetime": _DATE_OPS,
    "boolean": _BOOLEAN_OPS,
    "select": _CHOICE_OPS, "multiselect": _CHOICE_OPS,
    "tags": _TAGS_OPS,
    "rich_text": frozenset(),
}

#: data_types with a single-valued column expression a single-column ORDER BY
#: can use. ``multiselect`` (a JSON array) and ``tags`` (multiple assignment
#: rows) have no one value to sort by — sorting on either is refused with a
#: clear reason rather than picking an arbitrary tiebreak silently.
_SORTABLE_TYPES = frozenset(
    {"text", "long_text", "rich_text", "email", "url", "phone",
     "integer", "numeric", "currency", "percent",
     "date", "datetime", "boolean", "select"}
)


def _parse_jsonb(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


# ═══════════════════════════════════════════════════════════════════════════
# Task 2a-1 — column availability
# ═══════════════════════════════════════════════════════════════════════════


_LAYOUT_COLUMN_CANDIDATES = f"""
SELECT d.id::text AS id, d.api_name, d.label, d.data_type, d.options,
       d.type_params, x.min_order AS display_order
FROM (
    SELECT i.definition_id, min(i.display_order) AS min_order
    FROM {TABLE_UDF_LAYOUT_ITEMS} i
    JOIN {TABLE_UDF_LAYOUT_SECTIONS} s ON s.id = i.section_id
    JOIN {TABLE_UDF_LAYOUTS} l ON l.id = s.layout_id
    WHERE l.tab_id = $1::uuid AND l.record_type_id IS NULL
      AND i.definition_id IS NOT NULL
    GROUP BY i.definition_id
) x
JOIN {TABLE_UDF_DEFINITIONS} d ON d.id = x.definition_id
WHERE d.applies_to = $2 AND d.deleted_at IS NULL AND {_current('d')}
ORDER BY x.min_order, d.field_key
"""


async def get_available_columns(
    conn, *, target_type: str, tab_id: str | None, org_id: str, user_id: str,
) -> list[dict]:
    """Every UDF column this caller may see for ``target_type``, ordered.

    Each row: ``{definition_id, api_name, label, data_type, type_params,
    access}``. ``access`` is ``'read'`` or ``'edit'`` — a ``'hidden'`` column
    is never returned at all, matching the row-level convention every other
    UDF read endpoint already uses (a hidden field is an absent key, not a
    null value).

    A hidden TAB (``tab_id`` given and ``resolve_field_access_bulk`` reports
    every candidate as hidden) returns an empty list rather than raising —
    see the module docstring on why this endpoint degrades gracefully instead
    of 403ing the way ``GET /udf/layouts/{tab_id}`` does: a records list still
    has real base-table rows to show even with zero UDF columns available.
    """
    org_id = _require_org(org_id)
    target_type = _check_choice(target_type, APPLIES_TO, "target_type")

    if tab_id is None:
        candidates = await resolve_visible_definitions(
            conn, org_id=org_id, user_id=user_id, applies_to=target_type
        )
    else:
        rows = await conn.fetch(_LAYOUT_COLUMN_CANDIDATES, str(tab_id), target_type)
        candidates = [dict(r) for r in rows]
        for c in candidates:
            c["options"] = _parse_jsonb(c.get("options"))
            c["type_params"] = _parse_jsonb(c.get("type_params"))

    if not candidates:
        return []

    def_ids = [c["id"] for c in candidates]
    access_map = await resolve_field_access_bulk(
        conn, definition_ids=def_ids, tab_id=tab_id, org_id=org_id, user_id=user_id,
    )

    columns = []
    for c in candidates:
        access = access_map.get(c["id"], ACCESS_EDIT)
        if access == ACCESS_HIDDEN:
            continue
        columns.append({
            "definition_id": c["id"],
            "api_name": c.get("api_name"),
            "label": c["label"],
            "data_type": c["data_type"],
            "type_params": c.get("type_params") or {},
            "options": c.get("options"),
            "access": access,
        })
    return columns


# ═══════════════════════════════════════════════════════════════════════════
# Task 2a-2 — filter operators
# ═══════════════════════════════════════════════════════════════════════════


def _check_operator(data_type: str, operator: str) -> None:
    allowed = OPERATORS_BY_DATA_TYPE.get(data_type, frozenset())
    if operator not in allowed:
        raise FilterOperatorError(
            f"operator={operator!r} is not valid for data_type={data_type!r} — "
            f"allowed: {sorted(allowed) or 'none'}"
        )


def _validate_filter_numeric(data_type: str, value: Any, type_params: dict) -> Decimal:
    """Coerce+validate one numeric filter operand.

    Deliberately NOT ``portfolio_udf._coerce_numeric_value`` — see the module
    docstring: min/max are bounds on a stored value, not on a comparison
    operand, and are not enforced here. Scale/precision ARE enforced, and by
    REJECTING a value with too many fractional digits rather than quantizing
    it, because a silently-rounded filter bound would match different rows
    than the caller asked for.
    """
    number = _numeric(value, "filter value")
    if data_type == "integer":
        if number != number.to_integral_value():
            raise FilterValueError(
                f"data_type='integer' filter value must be a whole number — "
                f"got {number}"
            )
        return number.to_integral_value()
    scale = type_params.get("scale")
    if isinstance(scale, int) and not isinstance(scale, bool):
        quantized = number.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
        if quantized != number:
            raise FilterValueError(
                f"filter value {number} has more decimal places than this "
                f"{data_type} field's declared scale={scale} — rejected "
                f"rather than silently rounded"
            )
    precision = type_params.get("precision")
    if isinstance(precision, int) and not isinstance(precision, bool):
        digits = len(number.as_tuple().digits)
        exponent = number.as_tuple().exponent
        used = max(digits, digits + int(exponent) if exponent < 0 else digits)
        if used > precision:
            raise FilterValueError(
                f"filter value {number} needs {used} digits; this "
                f"{data_type} field declares precision={precision}"
            )
    return number


def _as_str_list(value: Any, *, field_name: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        raise FilterValueError(f"{field_name} must be a non-empty list of strings")
    out = []
    for v in value:
        if not isinstance(v, str) or not v.strip():
            raise FilterValueError(f"{field_name} entries must be non-empty strings — got {v!r}")
        out.append(v.strip())
    return out


def build_filter_clause(
    *, definition_id: str, operator: str, value: Any, data_type: str,
    type_params: dict, choices: list[str] | None, alias: str, base_alias: str,
    org_id_param: int, target_type_param: int, params: list,
) -> tuple[str | None, str]:
    """One filter -> ``(join_sql_or_None, where_fragment)``, parameterized.

    ``params`` is the single list shared across the whole request; this
    function only ever APPENDS to it and references the resulting position —
    composing N filters into one statement never requires renumbering.
    ``org_id_param``/``target_type_param`` are the 1-based positions of
    values already appended by the caller (shared across every filter, so
    ``org_id``/``target_type`` are bound once no matter how many filters
    reference them).

    Raises :class:`FilterOperatorError` for an operator invalid for this
    ``data_type`` (checked BEFORE looking at ``value`` at all — an invalid
    operator is refused regardless of what value accompanies it), and
    :class:`FilterValueError` for a value that fails the field's own type
    contract.
    """
    _check_operator(data_type, operator)
    type_params = type_params or {}

    def _bind(v: Any) -> int:
        params.append(v)
        return len(params)

    if data_type == "tags":
        return _build_tag_filter(
            definition_id=definition_id, operator=operator, value=value,
            base_alias=base_alias, org_id_param=org_id_param,
            target_type_param=target_type_param, params=params, _bind=_bind,
        )

    def_idx = _bind(str(definition_id))
    join_sql = (
        f"LEFT JOIN {TABLE_UDF_VALUES} {alias} "
        f"ON {alias}.org_id = ${org_id_param}::uuid "
        f"AND {alias}.definition_id = ${def_idx}::uuid "
        f"AND {alias}.target_type = ${target_type_param} "
        f"AND {alias}.target_id = {base_alias}.id "
        f"AND {alias}.system_to IS NULL AND {alias}.valid_to IS NULL"
    )

    if data_type in TEXT_TYPES:
        if not isinstance(value, str) or not value.strip():
            raise FilterValueError("filter value must be a non-empty string")
        if operator == "contains":
            idx = _bind(value)
            where = f"{alias}.value_text ILIKE '%' || ${idx} || '%'"
        else:  # equals
            idx = _bind(value)
            where = f"{alias}.value_text = ${idx}"
        return join_sql, where

    if data_type in NUMERIC_TYPES:
        if operator == "between":
            lo, hi = value if isinstance(value, (list, tuple)) and len(value) == 2 else (None, None)
            if lo is None or hi is None:
                raise FilterValueError("operator='between' requires a [low, high] pair")
            lo_v = _validate_filter_numeric(data_type, lo, type_params)
            hi_v = _validate_filter_numeric(data_type, hi, type_params)
            idx_lo, idx_hi = _bind(lo_v), _bind(hi_v)
            where = f"{alias}.value_numeric BETWEEN ${idx_lo} AND ${idx_hi}"
        else:
            v = _validate_filter_numeric(data_type, value, type_params)
            idx = _bind(v)
            op_sql = {"equals": "=", "gt": ">", "lt": "<"}[operator]
            where = f"{alias}.value_numeric {op_sql} ${idx}"
        return join_sql, where

    if data_type == "date":
        if operator == "between":
            lo, hi = value if isinstance(value, (list, tuple)) and len(value) == 2 else (None, None)
            if lo is None or hi is None:
                raise FilterValueError("operator='between' requires a [low, high] pair")
            idx_lo, idx_hi = _bind(_date(lo, "filter value")), _bind(_date(hi, "filter value"))
            where = f"{alias}.value_date BETWEEN ${idx_lo}::date AND ${idx_hi}::date"
        else:
            idx = _bind(_date(value, "filter value"))
            op_sql = {"equals": "=", "before": "<", "after": ">"}[operator]
            where = f"{alias}.value_date {op_sql} ${idx}::date"
        return join_sql, where

    if data_type == "datetime":
        expr = f"({alias}.value_json #>> '{{}}')::timestamptz"
        if operator == "between":
            lo, hi = value if isinstance(value, (list, tuple)) and len(value) == 2 else (None, None)
            if lo is None or hi is None:
                raise FilterValueError("operator='between' requires a [low, high] pair")
            idx_lo = _bind(_datetime(lo, "filter value").isoformat())
            idx_hi = _bind(_datetime(hi, "filter value").isoformat())
            where = f"{expr} BETWEEN ${idx_lo}::timestamptz AND ${idx_hi}::timestamptz"
        else:
            idx = _bind(_datetime(value, "filter value").isoformat())
            op_sql = {"equals": "=", "before": "<", "after": ">"}[operator]
            where = f"{expr} {op_sql} ${idx}::timestamptz"
        return join_sql, where

    if data_type == "boolean":
        idx = _bind(_boolean(value, "filter value"))
        where = f"({alias}.value_json #>> '{{}}')::boolean = ${idx}::boolean"
        return join_sql, where

    if data_type == "select":
        codes = _as_str_list(value, field_name="filter value")
        if choices is not None:
            stray = [c for c in codes if c not in choices]
            if stray:
                raise FilterValueError(
                    f"values {stray} are not in this field's value set {choices}"
                )
        idx = _bind(codes)
        if operator == "in":
            where = f"{alias}.value_text = ANY(${idx}::text[])"
        else:  # not-in — absent (no value at all) trivially satisfies "not one of"
            where = f"({alias}.value_text IS NULL OR NOT ({alias}.value_text = ANY(${idx}::text[])))"
        return join_sql, where

    if data_type == "multiselect":
        codes = _as_str_list(value, field_name="filter value")
        if choices is not None:
            stray = [c for c in codes if c not in choices]
            if stray:
                raise FilterValueError(
                    f"values {stray} are not in this field's value set {choices}"
                )
        idx = _bind(codes)
        if operator == "in":
            where = f"{alias}.value_json ?| ${idx}::text[]"
        else:  # not-in
            where = f"({alias}.value_json IS NULL OR NOT ({alias}.value_json ?| ${idx}::text[]))"
        return join_sql, where

    raise FilterOperatorError(f"unsupported data_type={data_type!r} for filtering")  # pragma: no cover


def _build_tag_filter(
    *, definition_id: str, operator: str, value: Any, base_alias: str,
    org_id_param: int, target_type_param: int, params: list, _bind,
) -> tuple[None, str]:
    def_idx = _bind(str(definition_id))
    ta = f"ta_{def_idx}"
    base_predicate = (
        f"{ta}.org_id = ${org_id_param}::uuid AND {ta}.definition_id = ${def_idx}::uuid "
        f"AND {ta}.target_type = ${target_type_param} AND {ta}.target_id = {base_alias}.id "
        f"AND {ta}.system_to IS NULL"
    )
    if operator == "has-tag":
        if not isinstance(value, str) or not value.strip():
            raise FilterValueError("operator='has-tag' requires a single non-empty string")
        _, norm = normalize_tag(value)
        idx = _bind(norm)
        where = (
            f"EXISTS (SELECT 1 FROM {TABLE_UDF_TAG_ASSIGNMENTS} {ta} "
            f"WHERE {base_predicate} AND {ta}.normalized_code = ${idx})"
        )
        return None, where

    codes = _as_str_list(value, field_name="filter value")
    normalized = [normalize_tag(c)[1] for c in codes]
    idx = _bind(normalized)
    if operator == "has-any-of":
        where = (
            f"EXISTS (SELECT 1 FROM {TABLE_UDF_TAG_ASSIGNMENTS} {ta} "
            f"WHERE {base_predicate} AND {ta}.normalized_code = ANY(${idx}::text[]))"
        )
        return None, where
    # has-all-of
    where = (
        f"(SELECT count(DISTINCT {ta}.normalized_code) FROM {TABLE_UDF_TAG_ASSIGNMENTS} {ta} "
        f"WHERE {base_predicate} AND {ta}.normalized_code = ANY(${idx}::text[])) = {len(set(normalized))}"
    )
    return None, where


def _sort_expr(data_type: str, alias: str) -> str:
    if data_type not in _SORTABLE_TYPES:
        raise SortFieldError(
            f"data_type={data_type!r} has no single-valued column to sort by "
            f"— sortable types: {sorted(_SORTABLE_TYPES)}"
        )
    if data_type in NUMERIC_TYPES:
        return f"{alias}.value_numeric"
    if data_type == "date":
        return f"{alias}.value_date"
    if data_type == "datetime":
        return f"({alias}.value_json #>> '{{}}')::timestamptz"
    if data_type == "boolean":
        return f"({alias}.value_json #>> '{{}}')::boolean"
    # text/long_text/rich_text/email/url/phone/select
    return f"{alias}.value_text"


# ═══════════════════════════════════════════════════════════════════════════
# Task 2a-3 — list_records_with_udf
# ═══════════════════════════════════════════════════════════════════════════


async def _resolve_choices(conn, *, data_type: str, type_params: dict, options: Any) -> list[str] | None:
    if data_type not in CHOICE_TYPES:
        return None
    value_set_id = (type_params or {}).get("value_set_id")
    if value_set_id:
        return await _value_set_codes(conn, value_set_id)
    if isinstance(options, str):
        options = json.loads(options)
    if isinstance(options, list):
        return options
    return None


def _json_safe_row(record: dict) -> dict:
    import uuid as _uuid

    out = {}
    for k, v in record.items():
        out[k] = str(v) if isinstance(v, _uuid.UUID) else v
    return out


async def list_records_with_udf(
    conn,
    *,
    target_type: str,
    org_id: str,
    user_id: str,
    tab_id: str | None = None,
    filters: list[dict] | None = None,
    sort: dict | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Rows of ``target_type`` with their available UDF columns inlined.

    ``tab_id`` is accepted here (not only by :func:`get_available_columns`)
    even though the sprint's own Task 2a-3 signature omitted it — the router
    needs the SAME tab-scoped column set for permission-filtering AND for
    resolving which fields ``filters``/``sort`` may reference, and computing
    it twice (once here, once in the router) would let the two calls
    disagree about which columns are visible. One call, one source of truth.

    Returns ``{rows, columns, total_count}``. ``rows`` carry the base
    table's own columns plus ``udf_values: {definition_id: value}`` — ONLY
    for columns with a stored value; an absent key is an absent value, the
    same convention ``portfolio_udf.coerce_value`` documents for storage.
    """
    org_id = _require_org(org_id)
    target_type = _check_choice(target_type, APPLIES_TO, "target_type")
    base_table = TARGET_TABLES[target_type]
    filters = filters or []

    columns = await get_available_columns(
        conn, target_type=target_type, tab_id=tab_id, org_id=org_id, user_id=user_id,
    )
    columns_by_id = {c["definition_id"]: c for c in columns}

    params: list = [org_id, target_type]
    org_id_param, target_type_param = 1, 2

    joins: list[str] = []
    wheres: list[str] = []
    alias_seq = 0

    for f in filters:
        definition_id = f.get("definition_id")
        operator = f.get("operator")
        value = f.get("value")
        column = columns_by_id.get(definition_id)
        if column is None:
            raise FilterFieldError(
                f"definition_id={definition_id!r} is not an available column "
                f"for this caller/target_type/tab — a filter cannot reference "
                f"a field that would not otherwise be visible"
            )
        alias_seq += 1
        alias = f"v{alias_seq}"
        choices = await _resolve_choices(
            conn, data_type=column["data_type"], type_params=column["type_params"],
            options=column.get("options"),
        )
        join_sql, where_sql = build_filter_clause(
            definition_id=definition_id, operator=operator, value=value,
            data_type=column["data_type"], type_params=column["type_params"],
            choices=choices, alias=alias, base_alias="b",
            org_id_param=org_id_param, target_type_param=target_type_param,
            params=params,
        )
        if join_sql:
            joins.append(join_sql)
        wheres.append(where_sql)

    order_sql = "ORDER BY b.id"
    if sort is not None:
        definition_id = sort.get("definition_id")
        direction = "DESC" if sort.get("direction") == "desc" else "ASC"
        column = columns_by_id.get(definition_id)
        if column is None:
            raise SortFieldError(
                f"sort definition_id={definition_id!r} is not an available "
                f"column for this caller/target_type/tab"
            )
        alias_seq += 1
        alias = f"v{alias_seq}"
        def_idx = len(params) + 1
        params.append(str(definition_id))
        joins.append(
            f"LEFT JOIN {TABLE_UDF_VALUES} {alias} "
            f"ON {alias}.org_id = ${org_id_param}::uuid "
            f"AND {alias}.definition_id = ${def_idx}::uuid "
            f"AND {alias}.target_type = ${target_type_param} "
            f"AND {alias}.target_id = b.id "
            f"AND {alias}.system_to IS NULL AND {alias}.valid_to IS NULL"
        )
        sort_expr = _sort_expr(column["data_type"], alias)
        order_sql = f"ORDER BY {sort_expr} {direction} NULLS LAST, b.id"

    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    limit_idx = len(params) + 1
    params.append(limit)
    offset_idx = len(params) + 1
    params.append(offset)

    # target_type_param is only referenced inside filter/sort joins against
    # TABLE_UDF_VALUES — with zero filters and no sort it would otherwise
    # never appear anywhere in the query text, leaving asyncpg unable to
    # infer its type (IndeterminateDatatypeError). This clause is always
    # true (target_type is validated non-null above) and exists solely to
    # give the parameter an explicit type unconditionally.
    where_sql = " AND ".join(
        [f"b.org_id = ${org_id_param}::uuid", f"${target_type_param}::text IS NOT NULL",
         _current("b")] + wheres
    )
    query = (
        f"SELECT b.*, count(*) OVER() AS __total_count "
        f"FROM {base_table} b "
        f"{' '.join(joins)} "
        f"WHERE {where_sql} "
        f"{order_sql} "
        f"LIMIT ${limit_idx} OFFSET ${offset_idx}"
    )
    rows = await conn.fetch(query, *params)

    total_count = rows[0]["__total_count"] if rows else 0
    base_rows = []
    target_ids = []
    for r in rows:
        d = dict(r)
        d.pop("__total_count", None)
        base_rows.append(_json_safe_row(d))
        target_ids.append(str(d["id"]))

    scalar_def_ids = [c["definition_id"] for c in columns if c["data_type"] != "tags"]
    tag_def_ids = [c["definition_id"] for c in columns if c["data_type"] == "tags"]

    values_by_target: dict[str, dict[str, Any]] = {tid: {} for tid in target_ids}
    if target_ids and scalar_def_ids:
        value_rows = await conn.fetch(
            f"""SELECT target_id::text AS target_id, definition_id::text AS definition_id,
                       value_text, value_numeric, value_date, value_json
                FROM {TABLE_UDF_VALUES}
                WHERE org_id = $1::uuid AND target_type = $2
                  AND target_id = ANY($3::uuid[]) AND definition_id = ANY($4::uuid[])
                  AND system_to IS NULL AND valid_to IS NULL""",
            org_id, target_type, target_ids, scalar_def_ids,
        )
        for vr in value_rows:
            for col in ("value_text", "value_numeric", "value_date", "value_json"):
                if vr[col] is not None:
                    val = vr[col]
                    if col == "value_json":
                        val = _parse_jsonb(val)
                    values_by_target[vr["target_id"]][vr["definition_id"]] = val
                    break

    if target_ids and tag_def_ids:
        tag_rows = await conn.fetch(
            f"""SELECT target_id::text AS target_id, definition_id::text AS definition_id,
                       tag_code
                FROM {TABLE_UDF_TAG_ASSIGNMENTS}
                WHERE org_id = $1::uuid AND target_type = $2
                  AND target_id = ANY($3::uuid[]) AND definition_id = ANY($4::uuid[])
                  AND system_to IS NULL
                ORDER BY tag_code""",
            org_id, target_type, target_ids, tag_def_ids,
        )
        for tr in tag_rows:
            bucket = values_by_target[tr["target_id"]].setdefault(tr["definition_id"], [])
            bucket.append(tr["tag_code"])

    for row, tid in zip(base_rows, target_ids):
        row["udf_values"] = values_by_target.get(tid, {})

    return {"rows": base_rows, "columns": columns, "total_count": total_count}
