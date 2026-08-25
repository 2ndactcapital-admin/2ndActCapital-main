"""The read/edit layer behind the Securities & Assets grid — Portfolio UX 3.

WHAT MAKES THIS SCREEN DIFFERENT FROM POSITIONS AND TRANSACTIONS
──────────────────────────────────────────────────────────────────────────────
Portfolio UX 1 and UX 2 each spanned ONE scope. Every row they read and every
row they wrote lived in a tenant table with an ``org_id`` column and a single
``cmd=ALL`` RLS policy, so "can this caller touch this row" had exactly one
answer: org isolation, plus ``manage_portfolio``.

This screen spans TWO.

* ``portfolio.assets`` is **tenant data** (Portfolio A2). It carries ``org_id``,
  it is covered by ``assets_org_isolation`` (``cmd=ALL``, USING and WITH CHECK
  both ``org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
  OR current_setting('app.is_super_admin', true) = 'true'``), and an org admin
  holding ``manage_portfolio`` may correct their own row.

* ``portfolio.securities_global`` and its identifier / price / relationship
  satellites are the **platform master** (Portfolio A1). They have NO
  ``org_id`` — a CUSIP means the same thing to every tenant — and their RLS is
  inverted: FOUR policies per table, ``SELECT USING (true)`` for everyone and
  INSERT / UPDATE / DELETE gated on ``app.is_super_admin``. Introspected from
  the deployed database, not assumed.

So this module carries a SECOND permission boundary that neither prior screen
needed, and the whole design of the module follows from one requirement: an org
admin must never be able to write global data through this screen, **including
indirectly** — not by sending a global field name to the org endpoint, not by
sending a global column name, and not by the UI accidentally rendering a control
for one.

THE FIELD-NAME COLLISION THAT MAKES "INDIRECTLY" A REAL RISK
──────────────────────────────────────────────────────────────────────────────
``name``, ``short_name`` and ``currency_code`` exist on ``portfolio.assets``
**and** on ``portfolio.securities_global``. On the asset they are org-owned and
editable. On the security they are platform data and are not writable from here
at all. A joined row that emitted a bare ``name`` for one and a bare ``name``
for the other would be a screen where the difference between a legal edit and an
illegal one is which of two identically-named boxes the user happened to click.

Every global-sourced value therefore leaves this module under a ``global_``-
prefixed key (:data:`GLOBAL_SOURCED_FIELDS`), and the org-scoped write path
refuses BOTH those prefixed keys and the raw global column names
(:data:`GLOBAL_TABLE_COLUMNS`) with a dedicated exception — never a generic
"unknown field", because the caller needs to be told *where the real door is*.

WHY AN ASSET EDIT KEEPS ITS ID — AND UX 1's DID NOT
──────────────────────────────────────────────────────────────────────────────
:func:`update_position` (UX 1) restates on the VALID axis: close the row, insert
a successor, mint a new id. That is correct for a position, which is a leaf fact
— the only thing pointing at one is ``document_record_links``, and UX 1 copies
those explicitly.

``portfolio.assets`` is not a leaf. Three deployed foreign keys reference
``assets.id``: ``portfolio.asset_identifiers``, ``portfolio.positions`` and
``portfolio.valuations``. A new id on every taxonomy correction would leave
every position and every valuation of that asset pointing at a row that
``_current()`` no longer returns — a screen-level edit that silently detaches a
holding from its instrument.

:func:`update_asset` therefore archives on the **SYSTEM** axis: the outgoing
version is preserved as a NEW row stamped ``system_to = now()`` (history, and
excluded by every ``_current()`` predicate in the portfolio services), and the
live row is updated in place with its id intact. This is Rule 3's guarantee —
the old state stays independently queryable — applied on the axis that a
referenced master row can actually use.

It is also not new. ``routers.entities.update_entity`` has done exactly this
since long before Portfolio A1, for a table with 44 inbound foreign keys, and
``services.securities_global`` already carves out the same exception for
``canonical_id`` for the same stated reason. Two mechanisms for "correct a
referenced master row" would eventually disagree about which row is current.

The pleasant consequence: nothing needs a ``_carry_document_links`` equivalent.
Document links, identifiers, positions and valuations all keep pointing at the
same id, because the id never moved.

AN ABSENT PRICE IS ABSENT, NEVER ZERO
──────────────────────────────────────────────────────────────────────────────
``portfolio.securities_global_prices`` holds ZERO rows today, and A1's
:func:`~services.securities_global.add_price` **refuses structured notes on
purpose** — 54 of the 67 corpus securities can never have one. So "no latest
price" is the common case, not an edge case, and it is carried out as ``None``
with a ``latest_price_reason`` exactly the way ``resolve_current_value`` carries
a missing valuation. A zero here would be summed into a portfolio total and the
fact that nothing was ever measured would be gone.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from services.portfolio_assets import (
    ASSET_CLASSES,
    IDENTIFIER_TYPES,
    OWNERSHIP_BASES,
    TABLE_ASSET_IDENT,
    TABLE_ASSETS,
    TABLE_ENTITIES,
    TABLE_POSITIONS,
    TABLE_VALUATIONS,
    VALUATION_METHODS,
    PortfolioError,
    _check_choice,
    _current,
    _OrgWrite,
    _require_org,
    create_asset,
    resolve_current_value,
)
from services.portfolio_documents import RECORD_TYPE_ASSET
from services.securities_global import (
    PRICE_COVERAGES,
    SECURITY_EDITABLE_FIELDS,
    SECURITY_TYPES,
    TABLE_IDENT as TABLE_SEC_IDENT,
    TABLE_PRICE as TABLE_SEC_PRICE,
    TABLE_REL as TABLE_SEC_REL,
    TABLE_SEC,
)

# `public` IS on app_service's search_path; qualified anyway for the same reason
# A2 qualifies its two public tables — the symmetry is what keeps the habit.
TABLE_CONFIG = "public.config"
TABLE_ORGS = "public.organizations"
TABLE_DOC_RECORD_LINKS = "public.document_record_links"

# ── The TENANT boundary. Same two names UX 1 and UX 2 use. ──────────────────
# Both already exist in `public.permissions`; neither is invented here. Six
# deployed roles carry `view_portfolio` and three of those also carry
# `manage_portfolio`, so "read but not write" is a real, reachable state rather
# than a hypothetical one the tests have to manufacture.
READ_PERMISSION = "view_portfolio"
WRITE_PERMISSION = "manage_portfolio"

#: How many rows one grid page may ask for. Same shape as UX 1/UX 2: the grid
#: sorts and filters CLIENT side over the loaded page, so the cap bounds how
#: much of the truth the user is actually sorting, and `total` is reported
#: separately so a truncated page reads as truncation.
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

#: The link-state filter vocabulary. `global_security_id` is nullable on
#: purpose (A2): a rental property or a painting has no global counterpart and
#: must not be forced to invent one. So "unlinked" is a legitimate, permanent
#: state and gets a first-class filter rather than looking like missing data.
LINK_FILTERS = frozenset({"all", "linked", "unlinked"})


# ═══════════════════════════════════════════════════════════════════════════
# THE TWO FIELD SETS THE WHOLE PERMISSION STORY HANGS ON
# ═══════════════════════════════════════════════════════════════════════════

#: Columns of ``portfolio.assets`` an org write may name. Every one of them is
#: tenant-owned: the row carries ``org_id`` and the RLS policy's WITH CHECK
#: compares it against the connection's org context.
#:
#: NOT here, deliberately:
#:   ``id`` / ``org_id``        — identity.
#:   ``valid_*`` / ``system_*`` — the temporal axes :func:`update_asset` owns.
#:   ``issuer_entity_id`` / ``internal_spv_id`` — structural links, changed by
#:                                the SPV and entity screens, not by this one.
#:   ``global_security_id``     — see :data:`GLOBAL_TABLE_COLUMNS`. Settable at
#:                                CREATE, never on a correction: re-pointing an
#:                                asset at a different instrument is a different
#:                                asset, exactly as UX 1 refuses to re-point a
#:                                position at a different asset.
ORG_EDITABLE_FIELDS = frozenset({
    "name", "short_name", "asset_type", "asset_class", "ownership_basis",
    "valuation_method", "default_taxonomy_key", "currency_code",
    "include_in_performance", "inception_date", "maturity_date", "is_active",
})

#: The subset an INLINE grid cell may edit. Both are single-valued, neither
#: participates in a cross-field contract, and neither can be refused for a
#: reason a cell has no room to explain — the same test UX 1 applied to
#: ``taxonomy_key`` / ``is_reconciled``.
#:
#: ``valuation_method`` is deliberately NOT inline even though it is a plain
#: enum: A2 derives an asset's MARKET from it (``_asset_market``), so changing
#: it silently changes which transaction types the asset will accept in future.
#: That deserves the pane, which has room to say so.
INLINE_EDITABLE_FIELDS = frozenset({
    "default_taxonomy_key", "include_in_performance",
})

#: The JSON keys this module emits that originate in the GLOBAL tables. Every
#: one is read-only on this screen for EVERY caller, super admin included — the
#: global write path is a different endpoint with a different gate, and a field
#: that was writable from two places with two different rules is the bug this
#: whole prefix exists to prevent.
#:
#: The prefix is not decoration. ``name``, ``short_name`` and ``currency_code``
#: exist on BOTH tables (introspected); without it, the joined row would carry
#: two different fields with the same name and opposite permissions.
GLOBAL_SOURCED_FIELDS = frozenset({
    "global_security_id",
    "global_name",
    "global_short_name",
    "global_security_type",
    "global_currency_code",
    "global_price_coverage",
    "global_canonical_id",
    "global_merged_into_id",
    "global_was_merged",
    "global_identifier_type",
    "global_identifier_value",
    "global_identifiers",
    "latest_price",
    "latest_price_date",
    "latest_price_currency",
    "latest_price_type",
    "latest_price_source",
    "latest_price_reason",
})

#: The RAW column names of the global tables. Refused alongside the prefixed
#: keys above, because a caller probing the org endpoint will try the column
#: name before it tries the API's own key, and "unknown field" would be a
#: misleading answer to a request that was refused for a permission reason.
GLOBAL_TABLE_COLUMNS = frozenset({
    "global_security_id", "security_type", "price_coverage", "canonical_id",
    "merged_into_id", "id_type", "id_value", "is_primary", "price",
    "price_date", "price_type", "source",
})


class GlobalFieldError(PortfolioError):
    """An org-scoped write named a field that belongs to the platform master.

    Its own class, and mapped to **403** rather than 422 by the router, because
    the reason is AUTHORITY and not shape. The body was well-formed; the field
    exists; the value may even be legal. What is missing is Super Admin, and a
    422 would send the caller off to fix a request that was never the problem.
    """


# ── Serialisation ───────────────────────────────────────────────────────────


def _s(value: Any) -> str | None:
    """Decimal → exact string. A float at the JSON boundary is a rounding error
    introduced at the last possible layer, after the figures survived the
    database, the service and the resolver as exact Decimals."""
    return None if value is None else str(value)


def _d(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _ts(value) -> str | None:
    return None if value is None else value.isoformat()


# ── Taxonomy labels (CLAUDE.md Rule 1 / Rule 4) ──────────────────────────────


async def taxonomy_labels(conn, org_id: str) -> dict[str, str]:
    """``{taxonomy_key: label}`` for the org, read from ``config``.

    Assets store taxonomy KEYS in ``default_taxonomy_key`` (Rule 4). Labels are
    resolved server-side at read time (Rule 1) and are never hardcoded in this
    module or the frontend.
    """
    rows = await conn.fetch(
        f"""
        SELECT config_key, config_value
        FROM {TABLE_CONFIG}
        WHERE org_id = $1::uuid
          AND category = 'asset_taxonomy'
          AND (is_active IS NULL OR is_active = true)
        """,
        str(org_id),
    )
    return {r["config_key"]: r["config_value"] for r in rows}


# ═══════════════════════════════════════════════════════════════════════════
# The joined read — tenant asset LEFT JOIN platform security
# ═══════════════════════════════════════════════════════════════════════════

# The join resolves through the merge chain in ONE hop, exactly as A1's
# `get_by_identifier` does: `COALESCE(matched.canonical_id, matched.id)`. The
# COALESCE is not defensive styling — A1 records that `canonical_id` was NULL on
# all 67 pre-existing corpus rows, so a bare `canonical_id` join returns nothing
# for the entire live corpus.
#
# Both LATERALs are LEFT and both are LIMIT 1. An asset with no global link, a
# security with no identifier and a security with no price are all normal, and
# each has to come back as one row with NULLs rather than vanishing.
_LIST_FROM = f"""
    FROM {TABLE_ASSETS} a
    LEFT JOIN {TABLE_SEC} sg_matched
           ON sg_matched.id = a.global_security_id
    LEFT JOIN {TABLE_SEC} sg
           ON sg.id = COALESCE(sg_matched.canonical_id, sg_matched.id)
    LEFT JOIN LATERAL (
        SELECT i.id_type, i.id_value
        FROM {TABLE_SEC_IDENT} i
        WHERE i.global_security_id = sg.id
          AND {_current('i')}
        ORDER BY i.is_primary DESC,
                 array_position(
                     ARRAY['cusip','isin','ticker','sedol','figi','lei','internal'],
                     i.id_type
                 ),
                 i.id_value
        LIMIT 1
    ) gident ON true
    LEFT JOIN LATERAL (
        SELECT p.price, p.price_date, p.currency_code, p.price_type, p.source
        FROM {TABLE_SEC_PRICE} p
        WHERE p.global_security_id = sg.id
          AND {_current('p')}
        ORDER BY p.price_date DESC, p.system_from DESC
        LIMIT 1
    ) gprice ON true
    LEFT JOIN LATERAL (
        SELECT ai.id_type, ai.id_value
        FROM {TABLE_ASSET_IDENT} ai
        WHERE ai.asset_id = a.id
          AND ai.org_id = a.org_id
          AND {_current('ai')}
        ORDER BY ai.is_primary DESC, ai.id_value
        LIMIT 1
    ) oident ON true
    LEFT JOIN {TABLE_ORGS} o ON o.id = a.org_id
"""

_LIST_COLUMNS = f"""
    a.id::text                    AS id,
    a.org_id::text                AS org_id,
    o.name                        AS org_name,
    a.name                        AS name,
    a.short_name                  AS short_name,
    a.asset_class                 AS asset_class,
    a.asset_type                  AS asset_type,
    a.ownership_basis             AS ownership_basis,
    a.valuation_method            AS valuation_method,
    a.include_in_performance      AS include_in_performance,
    a.default_taxonomy_key        AS default_taxonomy_key,
    a.currency_code               AS currency_code,
    a.issuer_entity_id::text      AS issuer_entity_id,
    a.internal_spv_id::text       AS internal_spv_id,
    a.inception_date              AS inception_date,
    a.maturity_date               AS maturity_date,
    a.is_active                   AS is_active,
    a.valid_from                  AS valid_from,
    a.valid_to                    AS valid_to,
    a.system_from                 AS system_from,
    a.system_to                   AS system_to,
    a.global_security_id::text    AS raw_global_security_id,
    sg.id::text                   AS global_security_id,
    sg.name                       AS global_name,
    sg.short_name                 AS global_short_name,
    sg.security_type              AS global_security_type,
    sg.currency_code              AS global_currency_code,
    sg.price_coverage             AS global_price_coverage,
    sg.canonical_id::text         AS global_canonical_id,
    sg.merged_into_id::text       AS global_merged_into_id,
    gident.id_type                AS global_identifier_type,
    gident.id_value               AS global_identifier_value,
    gprice.price                  AS latest_price,
    gprice.price_date             AS latest_price_date,
    gprice.currency_code          AS latest_price_currency,
    gprice.price_type             AS latest_price_type,
    gprice.source                 AS latest_price_source,
    oident.id_type                AS own_identifier_type,
    oident.id_value               AS own_identifier_value,
    (
        SELECT count(*) FROM {TABLE_POSITIONS} p
        WHERE p.asset_id = a.id AND p.org_id = a.org_id AND {_current('p')}
    )                             AS position_count
"""


def _price_reason(row) -> str | None:
    """Why there is no latest price. ``None`` when there IS one.

    Three genuinely different absences, kept apart. Collapsing them into one
    em-dash would make "this instrument is never priced, by design" look
    identical to "we have not loaded prices yet", and only one of those is
    something to go fix.
    """
    if row["latest_price"] is not None:
        return None
    if row["global_security_id"] is None:
        return (
            "this asset is not linked to a global security — there is no "
            "platform price series to read. Unlinked is a legitimate permanent "
            "state for a property, a private interest or a collectible."
        )
    if row["global_security_type"] == "structured_note":
        return (
            "structured notes are never priced in the global price series, by "
            "design: a note's secondary marks are sporadic dealer prints, not a "
            "daily series. Its UNDERLYINGS carry the prices."
        )
    if row["global_price_coverage"] != "has_series":
        return (
            f"the linked security's price_coverage is "
            f"{row['global_price_coverage']!r}, not 'has_series' — no usable "
            f"price series has been found for it"
        )
    return (
        "the linked security is marked as having a price series, but no price "
        "row has been loaded for it yet"
    )


def _row_to_json(r, labels: dict[str, str]) -> dict[str, Any]:
    """One joined row → the JSON the grid and the pane both read.

    Every global-sourced value carries the ``global_`` / ``latest_price``
    prefix (:data:`GLOBAL_SOURCED_FIELDS`) and every org-owned value carries the
    bare column name. That is the whole contract the UI honours: a control is
    rendered for a field only when the server published it as editable, and no
    global-sourced key ever appears in that list.
    """
    key = r["default_taxonomy_key"]
    return {
        # ── Org-owned. Editable subject to `manage_portfolio`. ──────────
        "id": r["id"],
        "org_id": r["org_id"],
        "org_name": r["org_name"],
        "name": r["name"],
        "short_name": r["short_name"],
        "asset_class": r["asset_class"],
        "asset_type": r["asset_type"],
        "ownership_basis": r["ownership_basis"],
        "valuation_method": r["valuation_method"],
        "include_in_performance": r["include_in_performance"],
        "default_taxonomy_key": key,
        # Resolved server-side (Rule 1). `None` when the key has no config row —
        # NOT the key echoed back as its own label, which would make a stale key
        # look like a configured one.
        "taxonomy_label": labels.get(key) if key else None,
        "currency_code": r["currency_code"],
        "issuer_entity_id": r["issuer_entity_id"],
        "internal_spv_id": r["internal_spv_id"],
        "inception_date": _d(r["inception_date"]),
        "maturity_date": _d(r["maturity_date"]),
        "is_active": r["is_active"],
        "own_identifier_type": r["own_identifier_type"],
        "own_identifier_value": r["own_identifier_value"],
        "position_count": int(r["position_count"] or 0),
        "valid_from": _ts(r["valid_from"]),
        "valid_to": _ts(r["valid_to"]),
        "system_from": _ts(r["system_from"]),
        "system_to": _ts(r["system_to"]),
        "is_current": r["valid_to"] is None and r["system_to"] is None,
        # ── Platform-sourced. READ-ONLY here for every caller. ──────────
        "global_security_id": r["global_security_id"],
        "global_name": r["global_name"],
        "global_short_name": r["global_short_name"],
        "global_security_type": r["global_security_type"],
        "global_currency_code": r["global_currency_code"],
        "global_price_coverage": r["global_price_coverage"],
        "global_canonical_id": r["global_canonical_id"],
        "global_merged_into_id": r["global_merged_into_id"],
        # True when the asset points at a security that has since been merged
        # away. The join already forwarded the read to the survivor, and saying
        # so is the difference between "these two names disagree" and "you are
        # looking at the row that replaced the one you linked".
        "global_was_merged": (
            r["raw_global_security_id"] is not None
            and r["global_security_id"] is not None
            and r["raw_global_security_id"] != r["global_security_id"]
        ),
        "global_identifier_type": r["global_identifier_type"],
        "global_identifier_value": r["global_identifier_value"],
        "latest_price": _s(r["latest_price"]),
        "latest_price_date": _d(r["latest_price_date"]),
        "latest_price_currency": r["latest_price_currency"],
        "latest_price_type": r["latest_price_type"],
        "latest_price_source": r["latest_price_source"],
        "latest_price_reason": _price_reason(r),
    }


def _build_filters(
    *,
    org_id: str,
    search: str | None,
    asset_type: str | None,
    asset_class: str | None,
    valuation_method: str | None,
    taxonomy_key: str | None,
    taxonomy_prefix: str | None,
    security_type: str | None,
    linked: str,
    include_inactive: bool,
    include_history: bool,
) -> tuple[str, list[Any]]:
    """Assemble the WHERE clause and its positional arguments.

    Every filter is a bound parameter. ``taxonomy_prefix`` is the one that looks
    like it wants string interpolation and does not get it: the ``%`` is
    appended to the VALUE, not spliced into the SQL.
    """
    where = ["a.org_id = $1::uuid"]
    args: list[Any] = [str(org_id)]

    def add(clause_template: str, value: Any) -> None:
        args.append(value)
        where.append(clause_template.format(n=len(args)))

    if not include_history:
        # The default. An edit archives the outgoing version on the system axis;
        # a grid showing both rows would show one instrument twice.
        where.append(_current("a"))
    if not include_inactive:
        where.append("a.is_active = true")

    if asset_type:
        add("a.asset_type = ${n}", asset_type)
    if asset_class:
        add("a.asset_class = ${n}", asset_class)
    if valuation_method:
        add("a.valuation_method = ${n}", valuation_method)
    if taxonomy_key:
        add("a.default_taxonomy_key = ${n}", taxonomy_key)
    if taxonomy_prefix:
        # Rolls a super-class filter up over its major classes and sub
        # categories, whose keys are prefixed by construction (Rule 4).
        add("a.default_taxonomy_key LIKE ${n}", f"{taxonomy_prefix}%")
    if security_type:
        add("sg.security_type = ${n}", security_type)
    if linked == "linked":
        where.append("sg.id IS NOT NULL")
    elif linked == "unlinked":
        where.append("sg.id IS NULL")
    if search:
        args.append(f"%{search.strip()}%")
        n = len(args)
        # Searches the org's own names AND the platform's, plus both identifier
        # values. An operator looking for a CUSIP does not know or care which
        # table it lives in.
        where.append(
            f"(a.name ILIKE ${n} OR a.short_name ILIKE ${n} "
            f" OR sg.name ILIKE ${n} OR gident.id_value ILIKE ${n} "
            f" OR oident.id_value ILIKE ${n})"
        )

    return " AND ".join(where), args


async def list_assets(
    conn,
    *,
    org_id: str,
    search: str | None = None,
    asset_type: str | None = None,
    asset_class: str | None = None,
    valuation_method: str | None = None,
    taxonomy_key: str | None = None,
    taxonomy_prefix: str | None = None,
    security_type: str | None = None,
    linked: str = "all",
    include_inactive: bool = False,
    include_history: bool = False,
    resolve_values: bool = True,
    value_as_of: date | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """One grid page. Returns ``{total, limit, offset, returned, assets}``.

    ``total`` is the count BEFORE the limit, so the UI can say "showing 200 of
    1,431" rather than implying it has everything.

    ``resolve_values=False`` skips the per-asset valuation resolution, which is
    the expensive part of this call. It exists for callers filling a picker, not
    as a performance escape hatch for the grid, which needs the column.
    """
    org_id = _require_org(org_id)
    if linked not in LINK_FILTERS:
        raise PortfolioError(
            f"linked={linked!r} is not one of {sorted(LINK_FILTERS)}"
        )
    if asset_class:
        _check_choice(asset_class, ASSET_CLASSES, "asset_class")
    if valuation_method:
        _check_choice(valuation_method, VALUATION_METHODS, "valuation_method")
    if security_type:
        _check_choice(security_type, SECURITY_TYPES, "security_type")
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))

    where, args = _build_filters(
        org_id=org_id,
        search=search,
        asset_type=asset_type,
        asset_class=asset_class,
        valuation_method=valuation_method,
        taxonomy_key=taxonomy_key,
        taxonomy_prefix=taxonomy_prefix,
        security_type=security_type,
        linked=linked,
        include_inactive=include_inactive,
        include_history=include_history,
    )

    total = await conn.fetchval(f"SELECT count(*) {_LIST_FROM} WHERE {where}", *args)
    rows = await conn.fetch(
        f"""
        SELECT {_LIST_COLUMNS}
        {_LIST_FROM}
        WHERE {where}
        ORDER BY a.name ASC, a.id ASC
        LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
        """,
        *args, limit, offset,
    )

    labels = await taxonomy_labels(conn, org_id)
    out = []
    for r in rows:
        item = _row_to_json(r, labels)
        if resolve_values:
            # A2's real resolver, not a reimplementation. It honours the
            # supersession ladder and returns an ABSENCE with a reason rather
            # than a zero when nothing qualifies.
            resolved = await resolve_current_value(
                conn, org_id=org_id, asset_id=r["id"], as_of=value_as_of
            )
            item["current_value"] = _s(resolved.value)
            item["current_value_reason"] = resolved.reason
            item["current_value_status"] = resolved.status
            item["current_valuation_id"] = resolved.valuation_id
            item["current_valuation_date"] = _d(resolved.valuation_date)
        out.append(item)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(out),
        "value_as_of": _d(value_as_of),
        "assets": out,
    }


# ── One asset, in full — the right pane ─────────────────────────────────────


async def get_asset(
    conn, *, org_id: str, asset_id: str, value_as_of: date | None = None
) -> dict[str, Any] | None:
    """Everything the detail pane shows. ``None`` if not in this org.

    Asset + linked global security + BOTH identifier sets + the global price
    history + the resolved current value with its governing valuation + the
    asset's valuation history + the positions held against it + the asset's own
    version history.

    All of it in ONE call on purpose. The pane opens on a row click, and a
    six-request waterfall would render it in pieces, each arriving after the
    user had already started reading the last.

    An archived version (``system_to`` set) is returned rather than 404'd — it
    is history, it is reachable from the version list, and hiding it would make
    an edit look like a deletion.
    """
    org_id = _require_org(org_id)
    row = await conn.fetchrow(
        f"SELECT {_LIST_COLUMNS} {_LIST_FROM} "
        f"WHERE a.id = $1::uuid AND a.org_id = $2::uuid",
        str(asset_id), org_id,
    )
    if row is None:
        return None

    labels = await taxonomy_labels(conn, org_id)
    asset = _row_to_json(row, labels)

    resolved = await resolve_current_value(
        conn, org_id=org_id, asset_id=row["id"], as_of=value_as_of
    )
    asset["current_value"] = _s(resolved.value)
    asset["current_value_reason"] = resolved.reason
    asset["current_value_status"] = resolved.status
    asset["current_valuation_id"] = resolved.valuation_id
    asset["current_valuation_date"] = _d(resolved.valuation_date)

    return {
        "asset": asset,
        # The governing valuation — the row the ladder actually picked. Showing
        # the number without saying which mark produced it is what makes a
        # portfolio figure unauditable.
        "governing_valuation": {
            "valuation_id": resolved.valuation_id,
            "valuation_date": _d(resolved.valuation_date),
            "status": resolved.status,
            "value_basis": resolved.value_basis,
            "currency_code": resolved.currency_code,
            "is_superseded": resolved.is_superseded,
            "asset_value": _s(resolved.value),
            "reason": resolved.reason,
        },
        "valuation_history": await valuation_history(
            conn, org_id=org_id, asset_id=str(asset_id)
        ),
        "positions": await positions_on_asset(
            conn, org_id=org_id, asset_id=str(asset_id)
        ),
        "own_identifiers": await own_identifiers(
            conn, org_id=org_id, asset_id=str(asset_id)
        ),
        "version_history": await asset_version_history(
            conn, org_id=org_id, asset_id=str(asset_id)
        ),
        # ── Platform-sourced. Read-only on this screen for every caller. ──
        "global_security": (
            await get_global_security(conn, row["global_security_id"])
            if row["global_security_id"] else None
        ),
        # The record_type the Documents panel needs. Emitted by the API rather
        # than hardcoded in the component: `document_record_links.record_type`
        # has NO CHECK constraint, so a frontend typo would link to a record
        # type nothing ever reads back and nothing would raise.
        "document_record_type": RECORD_TYPE_ASSET,
    }


async def valuation_history(conn, *, org_id: str, asset_id: str) -> list[dict]:
    """Every current valuation row for the asset, newest first.

    ``superseded_by`` is computed rather than stored: A2 keeps supersession as a
    FORWARD pointer on the NEW row and never touches the old one, so "was this
    restated away" is only answerable by looking for a row that points at it.
    """
    rows = await conn.fetch(
        f"""
        SELECT v.id::text                      AS id,
               v.valuation_date, v.value, v.value_basis, v.currency_code,
               v.purpose, v.status, v.valuation_method, v.valuation_source,
               v.supersedes_valuation_id::text AS supersedes_valuation_id,
               v.system_from,
               (
                   SELECT s.id::text FROM {TABLE_VALUATIONS} s
                   WHERE s.supersedes_valuation_id = v.id
                     AND s.org_id = v.org_id
                     AND {_current('s')}
                   LIMIT 1
               )                               AS superseded_by
        FROM {TABLE_VALUATIONS} v
        WHERE v.org_id = $1::uuid AND v.asset_id = $2::uuid AND {_current('v')}
        ORDER BY v.valuation_date DESC, v.system_from DESC
        LIMIT 50
        """,
        str(org_id), str(asset_id),
    )
    return [
        {
            "id": r["id"],
            "valuation_date": _d(r["valuation_date"]),
            "value": _s(r["value"]),
            "value_basis": r["value_basis"],
            "currency_code": r["currency_code"],
            "purpose": r["purpose"],
            "status": r["status"],
            "valuation_method": r["valuation_method"],
            "valuation_source": r["valuation_source"],
            "supersedes_valuation_id": r["supersedes_valuation_id"],
            "superseded_by": r["superseded_by"],
            "is_superseded": r["superseded_by"] is not None,
            "recorded_at": _ts(r["system_from"]),
        }
        for r in rows
    ]


async def positions_on_asset(conn, *, org_id: str, asset_id: str) -> list[dict]:
    """Who holds this asset, and how much. Current rows only.

    The pane's answer to "is this row safe to deactivate" — an asset carrying
    live positions is not, and the count belongs next to the toggle rather than
    in a confirmation dialog nobody reads.
    """
    rows = await conn.fetch(
        f"""
        SELECT p.id::text              AS id,
               p.owner_entity_id::text AS owner_entity_id,
               e.display_name          AS owner_name,
               p.as_of_date, p.ownership_basis, p.quantity, p.ownership_pct,
               p.market_value, p.authority, p.source_system,
               p.superseded_by_source
        FROM {TABLE_POSITIONS} p
        JOIN {TABLE_ENTITIES} e
          ON e.id = p.owner_entity_id AND e.org_id = p.org_id AND {_current('e')}
        WHERE p.org_id = $1::uuid AND p.asset_id = $2::uuid AND {_current('p')}
        ORDER BY p.as_of_date DESC, e.display_name
        LIMIT 50
        """,
        str(org_id), str(asset_id),
    )
    return [
        {
            "id": r["id"],
            "owner_entity_id": r["owner_entity_id"],
            "owner_name": r["owner_name"],
            "as_of_date": _d(r["as_of_date"]),
            "ownership_basis": r["ownership_basis"],
            "quantity": _s(r["quantity"]),
            "ownership_pct": _s(r["ownership_pct"]),
            "market_value": _s(r["market_value"]),
            "authority": r["authority"],
            "source_system": r["source_system"],
            "superseded_by_source": r["superseded_by_source"],
        }
        for r in rows
    ]


async def own_identifiers(conn, *, org_id: str, asset_id: str) -> list[dict]:
    """The org's OWN identifiers for its own asset.

    A genuinely different table from the global identifier list, and the pane
    shows them apart. ``portfolio.asset_identifiers``'s CHECK admits ``parcel``
    and ``vin``, which the global constraint does not (introspected) — a tenant
    asset can be a house or a car, and those keys are nobody else's business.
    """
    rows = await conn.fetch(
        f"""
        SELECT ai.id::text AS id, ai.id_type, ai.id_value, ai.is_primary
        FROM {TABLE_ASSET_IDENT} ai
        WHERE ai.asset_id = $1::uuid AND ai.org_id = $2::uuid AND {_current('ai')}
        ORDER BY ai.is_primary DESC, ai.id_type, ai.id_value
        """,
        str(asset_id), str(org_id),
    )
    return [dict(r) for r in rows]


async def asset_version_history(conn, *, org_id: str, asset_id: str) -> list[dict]:
    """This asset's versions over time — the live row AND its system-axis archives.

    What makes an edit legible after the fact. :func:`update_asset` preserves the
    outgoing version as a row stamped ``system_to``, so "what did this instrument
    say last week" is answered by rows every other query here correctly excludes.

    ─────────────────────────────────────────────────────────────────────────
    HOW AN ARCHIVE IS MATCHED BACK TO ITS LIVE ROW, AND WHY IT IS NOT AN id
    ─────────────────────────────────────────────────────────────────────────
    An archive row gets a fresh ``id`` — it has to, since the live row keeps the
    one every foreign key points at. And ``portfolio.assets`` has **no column
    linking an archived version to the row it came from**: introspected against
    the deployed schema, not assumed. (Neither does ``public.entities``, whose
    archive mechanism this one mirrors — nothing has ever read that history
    back, so the gap has never surfaced.) Adding one is a migration, and this
    sprint ships no DDL.

    So the join key is ``(org_id, valid_from)``. That is a real key here, not a
    heuristic: ``valid_from`` is written once by the original INSERT, is copied
    verbatim into every archive by :func:`_archive_asset_version`, and is not a
    member of :data:`ORG_EDITABLE_FIELDS`, so no edit can ever move it. It is a
    microsecond-precision ``timestamptz``, and unlike ``name`` it survives the
    rename that is very often the edit being inspected.

    The honest limit: two assets created in the same org in the same microsecond
    would share a history list. If a future sprint adds an
    ``archived_from uuid`` column, this predicate is the thing to replace.
    """
    rows = await conn.fetch(
        f"""
        WITH live AS (
            SELECT id, org_id, valid_from FROM {TABLE_ASSETS}
            WHERE id = $1::uuid AND org_id = $2::uuid
        )
        SELECT a.id::text AS id, a.name, a.short_name, a.asset_type,
               a.asset_class, a.ownership_basis, a.valuation_method,
               a.default_taxonomy_key, a.currency_code,
               a.include_in_performance, a.is_active,
               a.valid_from, a.valid_to, a.system_from, a.system_to
        FROM {TABLE_ASSETS} a
        JOIN live ON live.org_id = a.org_id AND live.valid_from = a.valid_from
        WHERE a.org_id = $2::uuid
        ORDER BY a.system_to NULLS FIRST, a.system_from DESC
        LIMIT 50
        """,
        str(asset_id), str(org_id),
    )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "short_name": r["short_name"],
            "asset_type": r["asset_type"],
            "asset_class": r["asset_class"],
            "ownership_basis": r["ownership_basis"],
            "valuation_method": r["valuation_method"],
            "default_taxonomy_key": r["default_taxonomy_key"],
            "currency_code": r["currency_code"],
            "include_in_performance": r["include_in_performance"],
            "is_active": r["is_active"],
            "valid_from": _ts(r["valid_from"]),
            "valid_to": _ts(r["valid_to"]),
            "system_from": _ts(r["system_from"]),
            "system_to": _ts(r["system_to"]),
            "is_current": r["valid_to"] is None and r["system_to"] is None,
        }
        for r in rows
    ]


# ═══════════════════════════════════════════════════════════════════════════
# The org-scoped write — and the boundary it enforces
# ═══════════════════════════════════════════════════════════════════════════


def _reject_global_fields(changes: dict[str, Any]) -> None:
    """Refuse any field that belongs to the platform master. **403, not 422.**

    Called FIRST — before the "is this an editable asset column" check — so that
    a caller sending ``security_type`` is told the truth (*this is platform data
    and you are not Super Admin*) instead of the misleading *unknown field*. The
    order matters: ``security_type`` is not a column of ``portfolio.assets``, so
    the generic check below would happily reject it for the wrong reason, and the
    log would record a shape error where a permission refusal belongs.
    """
    offending = sorted(
        set(changes) & (GLOBAL_SOURCED_FIELDS | GLOBAL_TABLE_COLUMNS)
    )
    if not offending:
        return
    if offending == ["global_security_id"]:
        raise GlobalFieldError(
            "global_security_id may be chosen when the asset is CREATED and "
            "never afterwards. Re-pointing an asset at a different instrument "
            "is a different asset, not a correction of this one — the same "
            "reason a position cannot be re-pointed at a different asset."
        )
    raise GlobalFieldError(
        f"{offending} originate in the GLOBAL security master "
        f"(portfolio.securities_global and its identifier/price satellites), "
        f"which is shared by every tenant and is writable only by a Super "
        f"Admin, through POST/PATCH /portfolio/global-securities. There is no "
        f"org-scoped path that writes them — not this one, and not indirectly. "
        f"The org-editable fields on this asset are "
        f"{sorted(ORG_EDITABLE_FIELDS)}."
    )


async def _archive_asset_version(conn, org_id: str, asset_id: str) -> str:
    """Preserve the outgoing version as a SYSTEM-axis row. Returns its id.

    ``id`` is NOT copied. The archive gets a fresh one and the LIVE row keeps
    the id every foreign key points at — which is the entire reason this is a
    system-axis archive and not UX 1's valid-axis restatement. See the module
    docstring.

    ``system_to = now()`` puts the archive outside every ``_current()``
    predicate in the portfolio services, so no existing query starts returning
    two rows per asset as a side effect of the first edit.
    """
    archived = await conn.fetchval(
        f"""
        INSERT INTO {TABLE_ASSETS}
            (org_id, global_security_id, name, short_name, asset_class,
             asset_type, ownership_basis, valuation_method,
             include_in_performance, default_taxonomy_key, currency_code,
             issuer_entity_id, internal_spv_id, inception_date, maturity_date,
             is_active, valid_from, valid_to, system_from, system_to)
        SELECT a.org_id, a.global_security_id, a.name, a.short_name,
               a.asset_class, a.asset_type, a.ownership_basis,
               a.valuation_method, a.include_in_performance,
               a.default_taxonomy_key, a.currency_code, a.issuer_entity_id,
               a.internal_spv_id, a.inception_date, a.maturity_date,
               a.is_active, a.valid_from, a.valid_to, a.system_from, now()
        FROM {TABLE_ASSETS} a
        WHERE a.id = $1::uuid AND a.org_id = $2::uuid AND {_current('a')}
        RETURNING id::text
        """,
        str(asset_id), str(org_id),
    )
    if archived is None:
        raise PortfolioError(
            f"asset {asset_id} is not a current row in this org — it was "
            f"already archived or does not exist. The edit is refused rather "
            f"than branching history off a superseded row."
        )
    return archived


async def update_asset(
    conn, *, org_id: str, asset_id: str, changes: dict[str, Any]
) -> dict[str, Any]:
    """Correct a tenant asset. **The id does NOT change.**

    Two steps in ONE transaction (:class:`_OrgWrite`), mirroring
    ``routers.entities.update_entity``:

    1. the outgoing version is preserved as a new row stamped ``system_to``;
    2. the live row is updated in place, keeping its id.

    An archive that committed without its update — or an update without its
    archive — would each destroy exactly half of what a correction records, so
    both steps share the transaction :class:`_OrgWrite` opens.

    The RLS policy's ``WITH CHECK`` is the real tenant gate; ``_OrgWrite``
    supplies the org context it compares against. A2 records the caveat that
    matters here: ``_OrgWrite`` sets the GUC FROM its argument, so RLS does not
    catch a *wrong* ``org_id`` — only a connection that never set one. That is
    why every statement below ALSO carries ``AND a.org_id = $2`` explicitly, and
    why ``org_id`` reaches this function from JWT claims and never from a body.

    Returns ``{"id", "changed", "archived_version_id"}``. The id is echoed back
    unchanged deliberately: a caller reading UX 1's PATCH contract would expect
    a new one, and being explicit is cheaper than the bug.
    """
    org_id = _require_org(org_id)
    if not changes:
        raise PortfolioError("no changes supplied")

    # FIRST. See the docstring on why the order is load-bearing.
    _reject_global_fields(changes)

    unknown = sorted(set(changes) - ORG_EDITABLE_FIELDS)
    if unknown:
        raise PortfolioError(
            f"not editable: {unknown}. The org-editable fields on an asset are "
            f"{sorted(ORG_EDITABLE_FIELDS)}. `issuer_entity_id` and "
            f"`internal_spv_id` are structural links owned by the entity and "
            f"SPV screens; the temporal columns are owned by this function."
        )

    # Vocabularies validated against A2's frozensets, which mirror the deployed
    # CHECK constraints. Duplicated in Python for the reason A2 gives: a 23514
    # names a constraint and tells the caller nothing about what to pass.
    if "asset_class" in changes:
        _check_choice(changes["asset_class"], ASSET_CLASSES, "asset_class")
    if "ownership_basis" in changes:
        _check_choice(changes["ownership_basis"], OWNERSHIP_BASES, "ownership_basis")
    if "valuation_method" in changes:
        _check_choice(
            changes["valuation_method"], VALUATION_METHODS, "valuation_method"
        )
    # `assets.name` and `assets.asset_type` are both NOT NULL, and `asset_type`
    # has NO CHECK at all (A2, introspected) — it is open text, so an empty
    # string would be accepted by the database. These two are the only backstop.
    for field in ("name", "asset_type"):
        if field in changes and not (changes[field] or "").strip():
            raise PortfolioError(
                f"{field} is required and cannot be blanked (NOT NULL in the "
                f"schema; asset_type additionally has no CHECK, so an empty "
                f"string would be stored without complaint)"
            )
    for field in ("inception_date", "maturity_date"):
        value = changes.get(field)
        if value is not None and field in changes and not isinstance(value, date):
            raise PortfolioError(
                f"{field} must be a datetime.date — got {type(value).__name__}"
            )

    async with _OrgWrite(conn, org_id) as c:
        archived_id = await _archive_asset_version(c, org_id, asset_id)

        ordered = sorted(changes)
        # Column names come from ORG_EDITABLE_FIELDS, a frozenset of literals in
        # this module, and `unknown` above guarantees every key is a member.
        # Nothing caller-supplied is ever interpolated into the SQL text.
        assignments = ", ".join(
            f"{name} = ${i + 3}" for i, name in enumerate(ordered)
        )
        values = [
            changes[name].strip()
            if name in ("name", "short_name", "asset_type")
            and isinstance(changes[name], str)
            else changes[name]
            for name in ordered
        ]
        await c.execute(
            f"UPDATE {TABLE_ASSETS} a SET {assignments} "
            f"WHERE a.id = $1::uuid AND a.org_id = $2::uuid AND {_current('a')}",
            str(asset_id), org_id, *values,
        )

    return {
        "id": str(asset_id),
        "changed": ordered,
        "archived_version_id": archived_id,
    }


async def create_tenant_asset(
    conn,
    *,
    org_id: str,
    name: str,
    asset_type: str,
    asset_class: str = "financial",
    ownership_basis: str = "units",
    valuation_method: str = "market_price",
    short_name: str | None = None,
    global_security_id: str | None = None,
    default_taxonomy_key: str | None = None,
    currency_code: str | None = None,
    inception_date: date | None = None,
    maturity_date: date | None = None,
    include_in_performance: bool = True,
) -> str:
    """Create a tenant asset, optionally LINKED to a global security.

    Delegates the insert to ``portfolio_assets.create_asset`` — A2's function,
    unchanged — so the vocabularies, the ``_OrgWrite`` org context and the
    NOT-NULL/CHECK handling all stay in one place.

    The one thing added here is the link check. ``global_security_id`` is a real
    foreign key, so a bad value would raise a 23503 naming a constraint; that
    tells a user nothing. More usefully, the id is resolved through the merge
    chain first (``COALESCE(canonical_id, id)``, because A1 records that
    ``canonical_id`` was NULL on all 67 corpus rows), so an asset linked to a
    security that has since been merged away is attached to the SURVIVOR at
    creation rather than silently pointing at a superseded row for years.

    ``issuer_entity_id`` and ``internal_spv_id`` are not accepted here at all —
    they are set by the entity and SPV screens, which know what they mean.
    """
    org_id = _require_org(org_id)
    resolved_link: str | None = None
    if global_security_id:
        resolved_link = await conn.fetchval(
            f"""
            SELECT COALESCE(s.canonical_id, s.id)::text
            FROM {TABLE_SEC} s
            WHERE s.id = $1::uuid AND {_current('s')}
            """,
            str(global_security_id),
        )
        if resolved_link is None:
            raise PortfolioError(
                f"global_security_id {global_security_id} is not a current "
                f"security in the platform master. The global master is "
                f"readable by every tenant, so this is a genuinely missing row "
                f"and not a permission problem."
            )

    return await create_asset(
        conn,
        org_id=org_id,
        name=name,
        asset_type=asset_type,
        asset_class=asset_class,
        ownership_basis=ownership_basis,
        valuation_method=valuation_method,
        short_name=short_name,
        global_security_id=resolved_link,
        default_taxonomy_key=default_taxonomy_key,
        currency_code=currency_code,
        inception_date=inception_date,
        maturity_date=maturity_date,
        include_in_performance=include_in_performance,
    )


# ═══════════════════════════════════════════════════════════════════════════
# The GLOBAL master — reads for everyone, writes elsewhere
# ═══════════════════════════════════════════════════════════════════════════
#
# Reads live here because the joined screen needs them and the deployed policy
# is `SELECT USING (true)` — there is nothing to gate. Writes do NOT live here:
# they stay in `services.securities_global`, behind `_require_super_admin` and
# `_SuperAdminWrite`, which is the only sanctioned way in. A convenience wrapper
# in this module would be a second door into the global tables, and the first
# time somebody forgot to thread `is_super_admin=` through it, it would be an
# unguarded one.


async def get_global_security(conn, global_security_id: str) -> dict[str, Any] | None:
    """One global security, its identifiers, its recent prices, its underlyings.

    Read-only, and read by anyone — the RLS is ``USING (true)`` on all four
    tables. Nothing in this function elevates, and nothing in it writes.
    """
    row = await conn.fetchrow(
        f"""
        SELECT canonical.id::text             AS id,
               canonical.name, canonical.short_name, canonical.security_type,
               canonical.currency_code, canonical.price_coverage,
               canonical.canonical_id::text   AS canonical_id,
               canonical.merged_into_id::text AS merged_into_id,
               matched.id::text               AS matched_id,
               (matched.id <> canonical.id)   AS was_merged,
               canonical.system_from
        FROM {TABLE_SEC} matched
        JOIN {TABLE_SEC} canonical
          ON canonical.id = COALESCE(matched.canonical_id, matched.id)
        WHERE matched.id = $1::uuid
        """,
        str(global_security_id),
    )
    if row is None:
        return None

    idents = await conn.fetch(
        f"""
        SELECT i.id::text AS id, i.id_type, i.id_value, i.is_primary
        FROM {TABLE_SEC_IDENT} i
        WHERE i.global_security_id = $1::uuid AND {_current('i')}
        ORDER BY i.is_primary DESC, i.id_type, i.id_value
        """,
        row["id"],
    )
    prices = await conn.fetch(
        f"""
        SELECT p.id::text AS id, p.price_date, p.price, p.currency_code,
               p.price_type, p.source
        FROM {TABLE_SEC_PRICE} p
        WHERE p.global_security_id = $1::uuid AND {_current('p')}
        ORDER BY p.price_date DESC, p.system_from DESC
        LIMIT 30
        """,
        row["id"],
    )
    rels = await conn.fetch(
        f"""
        SELECT r.id::text AS id, r.raw_underlying_text, r.link_state,
               r.relationship_type, r.weight,
               t.name AS target_name, t.price_coverage AS target_price_coverage
        FROM {TABLE_SEC_REL} r
        LEFT JOIN {TABLE_SEC} tm ON tm.id = r.to_global_security_id
        LEFT JOIN {TABLE_SEC} t  ON t.id = COALESCE(tm.canonical_id, tm.id)
        WHERE r.from_global_security_id = $1::uuid AND {_current('r')}
        ORDER BY r.raw_underlying_text
        LIMIT 50
        """,
        row["id"],
    )

    return {
        "id": row["id"],
        "name": row["name"],
        "short_name": row["short_name"],
        "security_type": row["security_type"],
        "currency_code": row["currency_code"],
        "price_coverage": row["price_coverage"],
        "canonical_id": row["canonical_id"],
        "merged_into_id": row["merged_into_id"],
        "matched_id": row["matched_id"],
        "was_merged": row["was_merged"],
        "identifiers": [dict(r) for r in idents],
        "prices": [
            {
                "id": r["id"],
                "price_date": _d(r["price_date"]),
                "price": _s(r["price"]),
                "currency_code": r["currency_code"],
                "price_type": r["price_type"],
                "source": r["source"],
            }
            for r in prices
        ],
        "relationships": [
            {
                "id": r["id"],
                "raw_underlying_text": r["raw_underlying_text"],
                "link_state": r["link_state"],
                "relationship_type": r["relationship_type"],
                "weight": _s(r["weight"]),
                "target_name": r["target_name"],
                "target_price_coverage": r["target_price_coverage"],
            }
            for r in rels
        ],
        # Stated on every response so a client can never conclude "no button
        # was rendered, so anyone may write this". The gate is server-side; this
        # is only the UI's copy of what the server will do.
        "write_requires_super_admin": True,
    }


async def list_global_securities(
    conn,
    *,
    search: str | None = None,
    security_type: str | None = None,
    price_coverage: str | None = None,
    include_merged: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """The platform master, for the link picker and the Super Admin screen.

    NOT org-scoped, and that is correct rather than an oversight: these rows
    have no ``org_id`` column to scope by and the deployed policy is
    ``SELECT USING (true)``. A tenant seeing that a CUSIP exists is not a
    cross-tenant leak — the row belongs to no tenant. What a tenant must not be
    able to do is WRITE one, and that is enforced on the write path.

    Merged-away duplicates are hidden by default: linking a new asset to a row
    that has already been superseded is exactly the mistake
    :func:`create_tenant_asset` then has to silently correct.
    """
    if security_type:
        _check_choice(security_type, SECURITY_TYPES, "security_type")
    if price_coverage:
        _check_choice(price_coverage, PRICE_COVERAGES, "price_coverage")
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    where = [_current("s")]
    args: list[Any] = []

    def add(clause: str, value: Any) -> None:
        args.append(value)
        where.append(clause.format(n=len(args)))

    if not include_merged:
        where.append("s.merged_into_id IS NULL")
    if security_type:
        add("s.security_type = ${n}", security_type)
    if price_coverage:
        add("s.price_coverage = ${n}", price_coverage)
    if search:
        args.append(f"%{search.strip()}%")
        n = len(args)
        where.append(
            f"(s.name ILIKE ${n} OR s.short_name ILIKE ${n} OR EXISTS ("
            f"  SELECT 1 FROM {TABLE_SEC_IDENT} i "
            f"  WHERE i.global_security_id = s.id AND i.id_value ILIKE ${n}"
            f"    AND {_current('i')}))"
        )

    clause = " AND ".join(where)
    total = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE_SEC} s WHERE {clause}", *args
    )
    rows = await conn.fetch(
        f"""
        SELECT s.id::text AS id, s.name, s.short_name, s.security_type,
               s.currency_code, s.price_coverage,
               s.merged_into_id::text AS merged_into_id,
               (
                   SELECT i.id_type || ':' || i.id_value
                   FROM {TABLE_SEC_IDENT} i
                   WHERE i.global_security_id = s.id AND {_current('i')}
                   ORDER BY i.is_primary DESC, i.id_type, i.id_value
                   LIMIT 1
               ) AS primary_identifier
        FROM {TABLE_SEC} s
        WHERE {clause}
        ORDER BY s.name
        LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
        """,
        *args, limit, offset,
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(rows),
        "securities": [dict(r) for r in rows],
        "write_requires_super_admin": True,
    }


__all__ = [
    "DEFAULT_LIMIT",
    "GLOBAL_SOURCED_FIELDS",
    "GLOBAL_TABLE_COLUMNS",
    "INLINE_EDITABLE_FIELDS",
    "LINK_FILTERS",
    "MAX_LIMIT",
    "ORG_EDITABLE_FIELDS",
    "READ_PERMISSION",
    "RECORD_TYPE_ASSET",
    "SECURITY_EDITABLE_FIELDS",
    "TABLE_DOC_RECORD_LINKS",
    "WRITE_PERMISSION",
    "GlobalFieldError",
    "PortfolioError",
    "asset_version_history",
    "create_tenant_asset",
    "get_asset",
    "get_global_security",
    "list_assets",
    "list_global_securities",
    "own_identifiers",
    "positions_on_asset",
    "taxonomy_labels",
    "update_asset",
    "valuation_history",
]
