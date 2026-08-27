"""The read/edit layer behind the Positions grid — Portfolio UX 1.

WHAT THIS MODULE IS FOR
──────────────────────────────────────────────────────────────────────────────
A2 (``services.portfolio_assets``) shipped the WRITE contract for positions and
said so in its own docstring: "no router and no UI". Everything that has called
``create_position`` since then has been another service or a verify script.
This module is the missing half — the queries a dense, sortable, filterable
grid needs, plus the one mutation an inline edit performs — and
``routers.portfolio_positions`` is the missing REST surface on top of it.

Nothing here re-implements A2. Writes go through ``create_position`` so
``_validate_basis`` — which A2's docstring records as *the only thing enforcing
the ownership-basis contract, because the table has no CHECK for it* — runs on
every edit, not just on the original insert.

FOUR DECISIONS WORTH THE READER'S TIME
──────────────────────────────────────────────────────────────────────────────
1. **An edit is a bi-temporal RESTATEMENT, not an UPDATE** (CLAUDE.md Rule 3).
   :func:`update_position` closes the current row (``valid_to = now()``) and
   inserts a successor carrying every field the caller did not name. It is the
   same two-step ``portfolio_corporate_actions._restate_position`` performs, and
   it is written the same way deliberately: a split and a hand correction are
   both "this position now says something different", and two mechanisms for
   that would eventually disagree about which row is current.

   The consequence the UI must handle: **an edit returns a NEW position id.**
   The old id still resolves — it is history — but it is no longer current.
   The consequence the SERVICE has to handle is
   :func:`_carry_document_links`: Phase D links a source document to a position
   by record id, so without an explicit copy the first taxonomy correction
   would silently orphan the K-1 the holding was built from.

2. **"Current value" is computed by ``portfolio_rollup.position_current_value``,
   not here.** That function honours the basis contract on READ (a ``percent``
   position is a fraction of the asset's resolved valuation, NOT its stored
   ``market_value``, which nothing revalues). It is what the allocation sunburst
   sums. Calling it means the grid and the sunburst cannot disagree.

3. **A missing value is ``None`` with a REASON, never zero.** Inherited from
   ``resolve_current_value`` and preserved all the way to the JSON: the grid
   renders an em-dash with the reason on hover, and a rollup that summed the
   column would not silently absorb an unmeasured holding as a real zero.

4. **Monetary values serialise as STRINGS.** Same rule as
   ``portfolio_commitments.to_json``. A float at the JSON boundary is a rounding
   error introduced at the last possible layer, after the figures survived the
   database, the service and the resolver as exact Decimals.

WHAT IS DELIBERATELY NOT HERE
──────────────────────────────────────────────────────────────────────────────
No staff-visibility narrowing. Org isolation is the gate (RLS + ``org_id`` from
JWT claims), matching every other portfolio read; layering
``get_staff_visible_entity_ids`` on top is a real feature and a separate one,
and half-doing it here would look like it was covered.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from services.portfolio_assets import (
    AUTHORITIES,
    OWNERSHIP_BASES,
    SOURCE_SYSTEMS,
    TABLE_ASSETS,
    TABLE_ENTITIES,
    TABLE_POSITIONS,
    TABLE_TRANSACTIONS,
    TABLE_VALUATIONS,
    OwnershipBasisError,
    PortfolioError,
    _check_choice,
    _current,
    _opt_money,
    _OrgWrite,
    _require_org,
    _validate_basis,
    create_position,
    resolve_current_value,
)
from services.portfolio_documents import RECORD_TYPE_POSITION
from services.portfolio_rollup import position_current_value

# `public` IS on the search_path; qualified anyway for the same reason A2
# qualifies its two public tables — symmetry is what keeps the habit.
TABLE_CONFIG = "public.config"
TABLE_TXN_TYPES = "public.transaction_types"
TABLE_DOC_RECORD_LINKS = "public.document_record_links"
TABLE_ACCOUNTS = "public.accounts"

READ_PERMISSION = "view_portfolio"
WRITE_PERMISSION = "manage_portfolio"

#: How many rows one grid page may ask for. The grid sorts and filters CLIENT
#: side (TanStack Table over the loaded `rowData`), so the cap is not cosmetic:
#: it bounds how much of the truth the user is actually sorting. The endpoint
#: reports `total` separately so a truncated page is visible as truncation
#: rather than looking like the whole portfolio.
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

#: The superseded-state filter vocabulary. `positions.superseded_by_source` is
#: NULL on a row no other source outranked — Phase B writes it, and it is the
#: difference between "this is the number we use" and "this is what a losing
#: feed said". A grid that silently hid one of them would be lying by omission,
#: so the default is `all` and the state is a visible column.
SUPERSEDED_FILTERS = frozenset({"all", "winners", "losers"})

#: Fields an edit may name. Everything else on the row is either identity
#: (`id`, `org_id`), a temporal axis the restatement owns (`valid_*`,
#: `system_*`), or derived (`reconciled_at`).
#:
#: `asset_id` and `owner_entity_id` are NOT editable. Re-pointing a position at
#: a different asset or owner is not a correction of that position, it is a
#: different position — and a restatement chain that changed subject halfway
#: would make the history unreadable.
#: `account_id` (fee32) IS editable, unlike `asset_id` / `owner_entity_id`.
#: Re-pointing a position at a different custodial account is a correction of
#: the same holding — the owner and the asset are unchanged, only the statement
#: it was reported on. An explicit `null` unlinks it, which is what a position
#: reclassified as directly-held needs. Editing it re-runs the account-owner
#: check in `create_position`, so a correction that introduces a mismatch
#: raises its own exception rather than inheriting the old row's clean record.
EDITABLE_FIELDS = frozenset({
    "as_of_date", "ownership_basis", "quantity", "ownership_pct", "cost_basis",
    "market_value", "market_value_native", "accrued_income", "authority",
    "source_system", "taxonomy_key", "is_reconciled", "superseded_by_source",
    "account_id",
})

#: The subset an INLINE grid cell may edit. Everything here is safe to change
#: without ownership-basis validation feedback, which an inline cell has no room
#: to show: a taxonomy reassignment cannot make the basis contract inconsistent,
#: and neither can a reconciliation flag.
#:
#: `quantity` / `ownership_pct` / `market_value` are deliberately EXCLUDED — the
#: basis contract can refuse them, and a refusal that surfaces as a cell
#: silently snapping back is worse than no inline edit at all. Those go through
#: the right pane, which has room for the error.
INLINE_EDITABLE_FIELDS = frozenset({"taxonomy_key", "is_reconciled"})


# ── Serialisation ───────────────────────────────────────────────────────────


def _s(value: Any) -> str | None:
    """Decimal → exact string. See decision 4 in the module docstring."""
    return None if value is None else str(value)


def _d(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


# ── Taxonomy labels (CLAUDE.md Rule 1 / Rule 4) ──────────────────────────────


async def taxonomy_labels(conn, org_id: str) -> dict[str, str]:
    """``{taxonomy_key: label}`` for the org, read from ``config``.

    Positions store taxonomy KEYS (Rule 4). Labels are resolved server-side at
    read time (Rule 1) and are never hardcoded in this module or the frontend.
    Read on the caller's connection rather than through
    ``services.taxonomy.get_taxonomy_index``'s own pool so the lookup shares the
    request's transaction and org context.
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


# ── Listing ─────────────────────────────────────────────────────────────────


_LIST_COLUMNS = f"""
    p.id::text                         AS id,
    p.owner_entity_id::text            AS owner_entity_id,
    p.asset_id::text                   AS asset_id,
    p.as_of_date                       AS as_of_date,
    p.ownership_basis                  AS ownership_basis,
    p.quantity                         AS quantity,
    p.ownership_pct                    AS ownership_pct,
    p.cost_basis                       AS cost_basis,
    p.market_value                     AS market_value,
    p.market_value_native              AS market_value_native,
    p.fx_rate_id::text                 AS fx_rate_id,
    p.accrued_income                   AS accrued_income,
    p.authority                        AS authority,
    p.source_system                    AS source_system,
    p.taxonomy_key                     AS taxonomy_key,
    p.is_reconciled                    AS is_reconciled,
    p.reconciled_at                    AS reconciled_at,
    p.superseded_by_source             AS superseded_by_source,
    p.valid_from                       AS valid_from,
    p.valid_to                         AS valid_to,
    p.account_id::text                 AS account_id,
    acct.account_number_masked         AS account_number_masked,
    acct.household_id::text            AS account_household_id,
    a.name                             AS asset_name,
    a.short_name                       AS asset_short_name,
    a.asset_type                       AS asset_type,
    a.asset_class                      AS asset_class,
    a.valuation_method                 AS valuation_method,
    a.currency_code                    AS asset_currency_code,
    a.default_taxonomy_key             AS asset_default_taxonomy_key,
    e.display_name                     AS owner_name,
    e.entity_type::text                AS owner_entity_type
"""

_LIST_FROM = f"""
    FROM {TABLE_POSITIONS} p
    JOIN {TABLE_ASSETS} a
      ON a.id = p.asset_id AND a.org_id = p.org_id
    JOIN {TABLE_ENTITIES} e
      ON e.id = p.owner_entity_id AND e.org_id = p.org_id
     AND {_current('e')}
    LEFT JOIN {TABLE_ACCOUNTS} acct
      ON acct.id = p.account_id AND acct.org_id = p.org_id
     AND {_current('acct')}
"""


def _build_filters(
    *,
    org_id: str,
    owner_entity_id: str | None,
    asset_id: str | None,
    taxonomy_key: str | None,
    taxonomy_prefix: str | None,
    source_system: str | None,
    authority: str | None,
    ownership_basis: str | None,
    as_of_from: date | None,
    as_of_to: date | None,
    superseded: str,
    include_history: bool,
    search: str | None,
) -> tuple[str, list[Any]]:
    """Assemble the WHERE clause and its positional arguments.

    Every filter is a bound parameter. ``taxonomy_prefix`` is the one that looks
    like it wants string interpolation and does not get it: the ``%`` is
    appended to the VALUE, not spliced into the SQL.
    """
    where = ["p.org_id = $1::uuid"]
    args: list[Any] = [str(org_id)]

    def add(clause_template: str, value: Any) -> None:
        args.append(value)
        where.append(clause_template.format(n=len(args)))

    if not include_history:
        # The default. A restated position leaves its predecessor behind; a grid
        # that showed both would show one holding twice and double every total
        # a user computed by eye.
        where.append(_current("p"))

    if owner_entity_id:
        add("p.owner_entity_id = ${n}::uuid", str(owner_entity_id))
    if asset_id:
        add("p.asset_id = ${n}::uuid", str(asset_id))
    if taxonomy_key:
        add("p.taxonomy_key = ${n}", taxonomy_key)
    if taxonomy_prefix:
        # Rolls a super-class filter up over its major classes and sub
        # categories, whose keys are prefixed by construction (Rule 4:
        # taxonomy_sc_{n} / taxonomy_mc_{sc}_{mc} / taxonomy_sub_{sc}_{mc}_{n}).
        add("p.taxonomy_key LIKE ${n}", f"{taxonomy_prefix}%")
    if source_system:
        add("p.source_system = ${n}", source_system)
    if authority:
        add("p.authority = ${n}", authority)
    if ownership_basis:
        add("p.ownership_basis = ${n}", ownership_basis)
    if as_of_from:
        add("p.as_of_date >= ${n}::date", as_of_from)
    if as_of_to:
        add("p.as_of_date <= ${n}::date", as_of_to)
    if superseded == "winners":
        where.append("p.superseded_by_source IS NULL")
    elif superseded == "losers":
        where.append("p.superseded_by_source IS NOT NULL")
    if search:
        args.append(f"%{search.strip()}%")
        n = len(args)
        where.append(f"(a.name ILIKE ${n} OR e.display_name ILIKE ${n})")

    return " AND ".join(where), args


async def list_positions(
    conn,
    *,
    org_id: str,
    owner_entity_id: str | None = None,
    asset_id: str | None = None,
    taxonomy_key: str | None = None,
    taxonomy_prefix: str | None = None,
    source_system: str | None = None,
    authority: str | None = None,
    ownership_basis: str | None = None,
    as_of_from: date | None = None,
    as_of_to: date | None = None,
    superseded: str = "all",
    include_history: bool = False,
    search: str | None = None,
    resolve_values: bool = True,
    value_as_of: date | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """One grid page. Returns ``{total, limit, offset, positions, taxonomy}``.

    ``total`` is the count BEFORE the limit, so the UI can say "showing 200 of
    1,431" rather than implying it has everything.

    ``resolve_values=False`` skips the per-asset valuation resolution entirely.
    It exists because that resolution is the expensive part of this call and a
    caller filling an entity picker does not need it — not as a performance
    escape hatch for the grid, which does.
    """
    org_id = _require_org(org_id)
    if superseded not in SUPERSEDED_FILTERS:
        raise PortfolioError(
            f"superseded={superseded!r} is not one of {sorted(SUPERSEDED_FILTERS)}"
        )
    if source_system:
        _check_choice(source_system, SOURCE_SYSTEMS, "source_system")
    if authority:
        _check_choice(authority, AUTHORITIES, "authority")
    if ownership_basis:
        _check_choice(ownership_basis, OWNERSHIP_BASES, "ownership_basis")
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))

    where, args = _build_filters(
        org_id=org_id,
        owner_entity_id=owner_entity_id,
        asset_id=asset_id,
        taxonomy_key=taxonomy_key,
        taxonomy_prefix=taxonomy_prefix,
        source_system=source_system,
        authority=authority,
        ownership_basis=ownership_basis,
        as_of_from=as_of_from,
        as_of_to=as_of_to,
        superseded=superseded,
        include_history=include_history,
        search=search,
    )

    total = await conn.fetchval(
        f"SELECT count(*) {_LIST_FROM} WHERE {where}", *args
    )
    rows = await conn.fetch(
        f"""
        SELECT {_LIST_COLUMNS}
        {_LIST_FROM}
        WHERE {where}
        ORDER BY p.as_of_date DESC, a.name ASC, p.id ASC
        LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
        """,
        *args, limit, offset,
    )

    labels = await taxonomy_labels(conn, org_id)
    out = []
    # Memoised across the page: many positions share an asset, and for a
    # `value`-basis row the resolution does not depend on the position at all.
    # Keyed on the arguments that actually change the answer.
    memo: dict[tuple, tuple[Decimal | None, str | None]] = {}
    for r in rows:
        item = _row_to_json(r, labels)
        if resolve_values:
            key = (
                r["asset_id"], r["ownership_basis"],
                str(r["quantity"]), str(r["ownership_pct"]), str(r["market_value"]),
            )
            if key not in memo:
                memo[key] = await position_current_value(
                    conn, org_id, r, value_as_of
                )
            value, reason = memo[key]
            item["current_value"] = _s(value)
            item["current_value_reason"] = reason
        out.append(item)

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(out),
        "value_as_of": _d(value_as_of),
        "positions": out,
    }


def _row_to_json(r, labels: dict[str, str]) -> dict[str, Any]:
    key = r["taxonomy_key"]
    return {
        "id": r["id"],
        "owner_entity_id": r["owner_entity_id"],
        "owner_name": r["owner_name"],
        "owner_entity_type": r["owner_entity_type"],
        "asset_id": r["asset_id"],
        "asset_name": r["asset_name"],
        "asset_short_name": r["asset_short_name"],
        "asset_type": r["asset_type"],
        "asset_class": r["asset_class"],
        "valuation_method": r["valuation_method"],
        "currency_code": r["asset_currency_code"],
        "as_of_date": _d(r["as_of_date"]),
        "ownership_basis": r["ownership_basis"],
        "quantity": _s(r["quantity"]),
        "ownership_pct": _s(r["ownership_pct"]),
        "cost_basis": _s(r["cost_basis"]),
        "market_value": _s(r["market_value"]),
        "market_value_native": _s(r["market_value_native"]),
        "accrued_income": _s(r["accrued_income"]),
        "fx_rate_id": r["fx_rate_id"],
        # fee32. NULL is the normal, correct state for a directly-held asset or
        # an SPV interest — a blank account column is not a missing value.
        # `account_number_masked` is the only account identifier ever published:
        # public.accounts stores no unmasked number, and this read must not be
        # where one appears.
        "account_id": r["account_id"],
        "account_number_masked": r["account_number_masked"],
        "account_household_id": r["account_household_id"],
        "authority": r["authority"],
        "source_system": r["source_system"],
        "taxonomy_key": key,
        # Resolved server-side (Rule 1). `None` when the key has no config row —
        # NOT the key echoed back as its own label, which would make a stale key
        # look like a configured one.
        "taxonomy_label": labels.get(key) if key else None,
        "is_reconciled": r["is_reconciled"],
        "reconciled_at": (
            r["reconciled_at"].isoformat() if r["reconciled_at"] else None
        ),
        "superseded_by_source": r["superseded_by_source"],
        "is_superseded": r["superseded_by_source"] is not None,
        "valid_from": r["valid_from"].isoformat() if r["valid_from"] else None,
        "valid_to": r["valid_to"].isoformat() if r["valid_to"] else None,
        "is_current": r["valid_to"] is None,
    }


# ── One position, in full — the right pane ──────────────────────────────────


async def get_position(
    conn, *, org_id: str, position_id: str, value_as_of: date | None = None
) -> dict[str, Any] | None:
    """Everything the detail pane shows. ``None`` if not in this org.

    Returns the position, its asset, its owner, the RESOLVED current value with
    the governing valuation that produced it, the asset's full valuation
    history, and the position's transaction history.

    All of it in ONE call on purpose. The pane opens on a row click and a
    four-request waterfall would render it in pieces, each arriving after the
    user had already started reading the last.

    A position that is no longer current (superseded by a restatement) is
    returned rather than 404'd — it is history, it is reachable from the history
    list, and hiding it would make an edit look like a deletion.
    """
    org_id = _require_org(org_id)
    row = await conn.fetchrow(
        f"""
        SELECT {_LIST_COLUMNS},
               p.system_from AS system_from,
               p.system_to   AS system_to
        {_LIST_FROM}
        WHERE p.id = $1::uuid AND p.org_id = $2::uuid
        """,
        str(position_id), org_id,
    )
    if row is None:
        return None

    labels = await taxonomy_labels(conn, org_id)
    position = _row_to_json(row, labels)

    value, reason = await position_current_value(conn, org_id, row, value_as_of)
    position["current_value"] = _s(value)
    position["current_value_reason"] = reason

    # The governing valuation — the row the ladder actually picked. Resolved
    # separately from the value because for a `percent` position the value is a
    # FRACTION of it, and showing the number without saying which mark it came
    # from is what makes a portfolio figure unauditable.
    governing = await resolve_current_value(
        conn,
        org_id=org_id,
        asset_id=row["asset_id"],
        as_of=value_as_of,
        quantity=row["quantity"],
    )

    return {
        "position": position,
        "governing_valuation": {
            "valuation_id": governing.valuation_id,
            "valuation_date": _d(governing.valuation_date),
            "status": governing.status,
            "value_basis": governing.value_basis,
            "currency_code": governing.currency_code,
            "is_superseded": governing.is_superseded,
            "asset_value": _s(governing.value),
            "reason": governing.reason,
        },
        "valuation_history": await valuation_history(
            conn, org_id=org_id, asset_id=row["asset_id"]
        ),
        "transactions": await transaction_history(
            conn, org_id=org_id, position_id=str(position_id)
        ),
        "restatement_history": await restatement_history(
            conn, org_id=org_id,
            owner_entity_id=row["owner_entity_id"],
            asset_id=row["asset_id"],
        ),
        # The record_type the Documents panel needs. Emitted by the API rather
        # than hardcoded in the component: `document_record_links.record_type`
        # has NO CHECK constraint, so a frontend typo would link to a record
        # type nothing ever reads back and nothing would raise.
        "document_record_type": RECORD_TYPE_POSITION,
    }


async def valuation_history(conn, *, org_id: str, asset_id: str) -> list[dict]:
    """Every current valuation row for the asset, newest first.

    ``superseded_by`` is computed rather than stored: A2 keeps supersession as a
    FORWARD pointer on the NEW row and never touches the old one, so "was this
    restated away" is only answerable by looking for a row that points at it.
    """
    rows = await conn.fetch(
        f"""
        SELECT v.id::text                        AS id,
               v.valuation_date                  AS valuation_date,
               v.value                           AS value,
               v.value_basis                     AS value_basis,
               v.currency_code                   AS currency_code,
               v.purpose                         AS purpose,
               v.status                          AS status,
               v.valuation_method                AS valuation_method,
               v.valuation_source                AS valuation_source,
               v.supersedes_valuation_id::text   AS supersedes_valuation_id,
               v.system_from                     AS system_from,
               (
                   SELECT s.id::text FROM {TABLE_VALUATIONS} s
                   WHERE s.supersedes_valuation_id = v.id
                     AND s.org_id = v.org_id
                     AND {_current('s')}
                   LIMIT 1
               )                                 AS superseded_by
        FROM {TABLE_VALUATIONS} v
        WHERE v.org_id = $1::uuid
          AND v.asset_id = $2::uuid
          AND {_current('v')}
        ORDER BY v.valuation_date DESC, v.system_from DESC
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
            "recorded_at": r["system_from"].isoformat() if r["system_from"] else None,
        }
        for r in rows
    ]


async def transaction_history(conn, *, org_id: str, position_id: str) -> list[dict]:
    """Every current transaction on the position, newest trade first.

    Joined to ``transaction_types`` for the LABEL — the grid and pane display
    ``label``, never the raw ``transaction_type_code`` (Rule 1). The join is a
    LEFT JOIN so a transaction whose type was deleted out from under it still
    appears; losing the row entirely would be a worse answer than losing its
    label.
    """
    rows = await conn.fetch(
        f"""
        SELECT t.id::text                       AS id,
               t.transaction_type_code          AS transaction_type_code,
               tt.label                         AS transaction_type_label,
               tt.market                        AS transaction_type_market,
               t.trade_date                     AS trade_date,
               t.settle_date                    AS settle_date,
               t.quantity                       AS quantity,
               t.price                          AS price,
               t.gross_amount                   AS gross_amount,
               t.fees                           AS fees,
               t.taxes                          AS taxes,
               t.net_amount                     AS net_amount,
               t.currency_code                  AS currency_code,
               t.authority                      AS authority,
               t.source_system                  AS source_system,
               t.external_ref                   AS external_ref,
               t.is_corporate_action_adjustment AS is_corporate_action_adjustment,
               t.corporate_action_id::text      AS corporate_action_id
        FROM {TABLE_TRANSACTIONS} t
        LEFT JOIN {TABLE_TXN_TYPES} tt ON tt.code = t.transaction_type_code
        WHERE t.org_id = $1::uuid
          AND t.position_id = $2::uuid
          AND {_current('t')}
        ORDER BY t.trade_date DESC, t.system_from DESC
        """,
        str(org_id), str(position_id),
    )
    return [
        {
            "id": r["id"],
            "transaction_type_code": r["transaction_type_code"],
            "transaction_type_label": (
                r["transaction_type_label"] or r["transaction_type_code"]
            ),
            "transaction_type_market": r["transaction_type_market"],
            "trade_date": _d(r["trade_date"]),
            "settle_date": _d(r["settle_date"]),
            "quantity": _s(r["quantity"]),
            "price": _s(r["price"]),
            "gross_amount": _s(r["gross_amount"]),
            "fees": _s(r["fees"]),
            "taxes": _s(r["taxes"]),
            "net_amount": _s(r["net_amount"]),
            "currency_code": r["currency_code"],
            "authority": r["authority"],
            "source_system": r["source_system"],
            "external_ref": r["external_ref"],
            "is_corporate_action_adjustment": r["is_corporate_action_adjustment"],
            "corporate_action_id": r["corporate_action_id"],
        }
        for r in rows
    ]


async def restatement_history(
    conn, *, org_id: str, owner_entity_id: str, asset_id: str
) -> list[dict]:
    """The (owner, asset) position rows over time — current AND closed.

    This is what makes an edit legible after the fact. :func:`update_position`
    closes a row and inserts a successor, so "what did this holding say last
    week" is answered by the CLOSED rows, which every other query in this module
    correctly excludes.
    """
    rows = await conn.fetch(
        f"""
        SELECT p.id::text            AS id,
               p.as_of_date          AS as_of_date,
               p.ownership_basis     AS ownership_basis,
               p.quantity            AS quantity,
               p.ownership_pct       AS ownership_pct,
               p.market_value        AS market_value,
               p.taxonomy_key        AS taxonomy_key,
               p.source_system       AS source_system,
               p.authority           AS authority,
               p.valid_from          AS valid_from,
               p.valid_to            AS valid_to
        FROM {TABLE_POSITIONS} p
        WHERE p.org_id = $1::uuid
          AND p.owner_entity_id = $2::uuid
          AND p.asset_id = $3::uuid
          AND p.system_to IS NULL
        ORDER BY p.valid_from DESC
        LIMIT 50
        """,
        str(org_id), str(owner_entity_id), str(asset_id),
    )
    return [
        {
            "id": r["id"],
            "as_of_date": _d(r["as_of_date"]),
            "ownership_basis": r["ownership_basis"],
            "quantity": _s(r["quantity"]),
            "ownership_pct": _s(r["ownership_pct"]),
            "market_value": _s(r["market_value"]),
            "taxonomy_key": r["taxonomy_key"],
            "source_system": r["source_system"],
            "authority": r["authority"],
            "valid_from": r["valid_from"].isoformat() if r["valid_from"] else None,
            "valid_to": r["valid_to"].isoformat() if r["valid_to"] else None,
            "is_current": r["valid_to"] is None,
        }
        for r in rows
    ]


# ── Editing — a bi-temporal restatement ─────────────────────────────────────


async def _close_position(conn, org_id: str, position_id: str) -> None:
    """CLAUDE.md Rule 3, step 1. Writes ``valid_to`` and nothing else.

    Shaped exactly like ``portfolio_corporate_actions._close_position``,
    including the ``AND valid_to IS NULL`` in the predicate: without it, a
    concurrent edit that already closed the row would push ``valid_to`` forward
    a second time and the two restatements would overlap in valid time. The
    count is checked so a lost race raises instead of silently restating a row
    that was already history.
    """
    closed = await conn.fetchval(
        f"""
        WITH upd AS (
            UPDATE {TABLE_POSITIONS} p
            SET valid_to = now()
            WHERE p.id = $1::uuid AND p.org_id = $2::uuid AND {_current('p')}
            RETURNING 1
        ) SELECT count(*) FROM upd
        """,
        str(position_id), str(org_id),
    )
    if not closed:
        raise PortfolioError(
            f"position {position_id} is not a current row in this org — it was "
            f"already restated, corrected or closed. The edit is refused rather "
            f"than branching history off a superseded row."
        )


async def update_position(
    conn, *, org_id: str, position_id: str, changes: dict[str, Any]
) -> str:
    """Restate a position. Returns the NEW position's id.

    Close the current row, insert a successor through
    :func:`~services.portfolio_assets.create_position` carrying every field the
    caller did not name. Both steps run inside ONE transaction: a close that
    committed without its successor would delete the holding from every current
    read, which is the one outcome an edit must never produce.

    Going through ``create_position`` rather than writing the INSERT here is the
    point of the whole function. ``_validate_basis`` is, in A2's own words, the
    only thing enforcing the ownership-basis contract — there is no CHECK to
    fall through to. An edit that set ``ownership_basis='value'`` while leaving
    the old ``quantity`` in place would otherwise write a row that makes two
    rollups disagree, and nothing would raise.

    An explicit ``None`` in ``changes`` CLEARS the field. That is the difference
    between "leave it alone" (key absent) and "this measure no longer applies"
    (key present, value None) — and switching basis requires the second, because
    the contract demands the outgoing measure be NULL.
    """
    org_id = _require_org(org_id)
    unknown = sorted(set(changes) - EDITABLE_FIELDS)
    if unknown:
        raise PortfolioError(
            f"not editable: {unknown}. Editable fields are "
            f"{sorted(EDITABLE_FIELDS)}. `asset_id` and `owner_entity_id` are "
            f"deliberately excluded — re-pointing a position at a different "
            f"asset or owner is a different position, not a correction."
        )
    if not changes:
        raise PortfolioError("no changes supplied")

    if "authority" in changes:
        _check_choice(changes["authority"], AUTHORITIES, "authority")
    if "source_system" in changes:
        _check_choice(changes["source_system"], SOURCE_SYSTEMS, "source_system")
    if "ownership_basis" in changes:
        _check_choice(changes["ownership_basis"], OWNERSHIP_BASES, "ownership_basis")
    if "as_of_date" in changes and not isinstance(changes["as_of_date"], date):
        raise PortfolioError(
            f"as_of_date must be a datetime.date — got "
            f"{type(changes['as_of_date']).__name__}"
        )

    async with _OrgWrite(conn, org_id) as c:
        current = await c.fetchrow(
            f"""
            SELECT p.owner_entity_id::text AS owner_entity_id,
                   p.asset_id::text        AS asset_id,
                   p.as_of_date, p.ownership_basis, p.quantity, p.ownership_pct,
                   p.cost_basis, p.market_value, p.market_value_native,
                   p.fx_rate_id::text      AS fx_rate_id,
                   p.accrued_income, p.authority, p.source_system,
                   p.taxonomy_key, p.is_reconciled, p.superseded_by_source,
                   p.account_id::text      AS account_id
            FROM {TABLE_POSITIONS} p
            WHERE p.id = $1::uuid AND p.org_id = $2::uuid AND {_current('p')}
            """,
            str(position_id), org_id,
        )
        if current is None:
            raise PortfolioError(
                f"position {position_id} is not a current position in this org"
            )

        merged = dict(current)
        merged.update(changes)

        # Validated BEFORE the close, so a refused edit leaves the existing row
        # open. Closing first and letting create_position raise would roll the
        # transaction back anyway — but only if the caller kept it in one, and
        # this function is the thing guaranteeing that, not assuming it.
        _validate_merged_basis(merged)

        await _close_position(c, org_id, position_id)

        new_id = await create_position(
            c,
            org_id=org_id,
            owner_entity_id=merged["owner_entity_id"],
            asset_id=merged["asset_id"],
            as_of_date=merged["as_of_date"],
            authority=merged["authority"],
            source_system=merged["source_system"],
            ownership_basis=merged["ownership_basis"],
            quantity=merged["quantity"],
            ownership_pct=merged["ownership_pct"],
            market_value=merged["market_value"],
            market_value_native=merged["market_value_native"],
            cost_basis=merged["cost_basis"],
            accrued_income=merged["accrued_income"],
            fx_rate_id=merged["fx_rate_id"],
            taxonomy_key=merged["taxonomy_key"],
            is_reconciled=bool(merged["is_reconciled"]),
            superseded_by_source=merged["superseded_by_source"],
            # Carried, not defaulted. `create_position` writes every column
            # from its arguments, so omitting this would silently NULL the
            # account link on the FIRST edit of any linked position — the
            # holding would keep its numbers and quietly stop being attached to
            # the statement it came from.
            account_id=merged["account_id"],
        )

        await _carry_document_links(c, org_id, position_id, new_id)
        return new_id


async def _carry_document_links(
    conn, org_id: str, old_position_id: str, new_position_id: str
) -> int:
    """Re-point this position's source-document links at its successor.

    Without this, an edit would silently orphan the evidence. Phase D links a
    document to a position by ``(record_type='portfolio_position', record_id)``,
    and a restatement mints a NEW ``record_id`` — so the K-1 or custodial
    statement a holding was created from would stop appearing in the detail
    pane the first time anyone corrected a taxonomy key. The document still
    evidences the same holding; only the row id moved.

    COPY, not move. The closed row keeps its links so the historical position
    remains as auditable as it was before the edit — the pane can be opened on
    it from the restatement list and still show what it was built from.

    ``ON CONFLICT DO NOTHING`` against the deployed
    ``(document_id, record_type, record_id)`` UNIQUE makes a re-run idempotent.
    Returns how many links were carried, so a caller can tell "none existed"
    from "the copy did nothing".
    """
    copied = await conn.fetchval(
        f"""
        WITH ins AS (
            INSERT INTO {TABLE_DOC_RECORD_LINKS}
                (document_id, org_id, record_type, record_id, created_by)
            SELECT l.document_id, l.org_id, l.record_type, $3::uuid, l.created_by
            FROM {TABLE_DOC_RECORD_LINKS} l
            WHERE l.org_id = $1::uuid
              AND l.record_type = $4
              AND l.record_id = $2::uuid
            ON CONFLICT (document_id, record_type, record_id) DO NOTHING
            RETURNING 1
        ) SELECT count(*) FROM ins
        """,
        str(org_id), str(old_position_id), str(new_position_id),
        RECORD_TYPE_POSITION,
    )
    return int(copied or 0)


def _validate_merged_basis(merged: dict[str, Any]) -> None:
    """Run A2's basis contract against the MERGED field set.

    Imported rather than reimplemented, and called on the merge rather than on
    the change set: an edit that only names ``ownership_basis`` is exactly the
    edit the contract exists to refuse, and a check that looked only at what the
    caller supplied would see one legal field and pass it.
    """
    _validate_basis(
        merged["ownership_basis"],
        _opt_money(merged["quantity"], "quantity"),
        _opt_money(merged["ownership_pct"], "ownership_pct"),
        _opt_money(merged["market_value"], "market_value"),
    )


# ── Assets, for the pickers ─────────────────────────────────────────────────


async def list_assets(
    conn, *, org_id: str, search: str | None = None, limit: int = 50
) -> list[dict]:
    """Tenant assets, for the create-position asset picker.

    Deliberately thin. This is not an asset-management screen — it is the
    minimum a picker needs, and the fields it returns (``ownership_basis``,
    ``valuation_method``) are the two the position form must echo so a user can
    see WHY the basis defaulted the way it did.
    """
    org_id = _require_org(org_id)
    limit = max(1, min(int(limit), 200))
    args: list[Any] = [org_id]
    clause = ""
    if search and search.strip():
        args.append(f"%{search.strip()}%")
        clause = f" AND (a.name ILIKE ${len(args)} OR a.short_name ILIKE ${len(args)})"
    rows = await conn.fetch(
        f"""
        SELECT a.id::text AS id, a.name, a.short_name, a.asset_type,
               a.asset_class, a.ownership_basis, a.valuation_method,
               a.currency_code, a.default_taxonomy_key
        FROM {TABLE_ASSETS} a
        WHERE a.org_id = $1::uuid AND a.is_active = true AND {_current('a')}
        {clause}
        ORDER BY a.name
        LIMIT ${len(args) + 1}
        """,
        *args, limit,
    )
    return [dict(r) for r in rows]


__all__ = [
    "RECORD_TYPE_POSITION",
    "TABLE_DOC_RECORD_LINKS",
    "DEFAULT_LIMIT",
    "EDITABLE_FIELDS",
    "INLINE_EDITABLE_FIELDS",
    "MAX_LIMIT",
    "READ_PERMISSION",
    "SUPERSEDED_FILTERS",
    "WRITE_PERMISSION",
    "OwnershipBasisError",
    "PortfolioError",
    "get_position",
    "list_assets",
    "list_positions",
    "restatement_history",
    "taxonomy_labels",
    "transaction_history",
    "update_position",
    "valuation_history",
]
