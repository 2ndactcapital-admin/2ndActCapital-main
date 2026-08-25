"""The read/correct layer behind the Transactions grid — Portfolio UX 2.

WHAT THIS MODULE IS FOR
──────────────────────────────────────────────────────────────────────────────
A2 shipped :func:`~services.portfolio_assets.record_transaction` with no HTTP
surface at all. Its only callers to date are
``services.portfolio_corporate_actions`` and the verify scripts. This module is
the read half a dense grid needs, plus the one mutation the ledger actually
supports — and ``routers.portfolio_transactions`` is the REST surface on top.

It is deliberately the same shape as ``services.portfolio_positions`` (Portfolio
UX 1): same permission constants, same ``total``-before-limit paging, same
decimal-string serialisation, same server-published editable-field lists, same
``_carry_document_links``. Two grids that behaved differently for no reason
would be two grids to learn.

THE ONE PLACE IT DIVERGES, AND WHY — TASK 1d
──────────────────────────────────────────────────────────────────────────────
**A transaction has never been editable.** Not "editable through a different
API" — there is no ``UPDATE`` against ``portfolio.transactions`` anywhere in the
codebase. It is an append-only ledger, and the table's bi-temporal columns have
only ever been written on INSERT.

So what this module adds is a CORRECTION, and it is a bi-temporal supersession
rather than an offsetting reversal. Both were live options; supersession wins on
a fact about the existing readers, not on taste:

* Every current reader of ``portfolio.transactions`` already filters
  ``valid_to IS NULL AND system_to IS NULL`` —
  ``portfolio_positions.transaction_history``, ``portfolio_commitments``'
  ``SUM(amount * tt.affects_paid_in)`` roll-up, and
  ``portfolio_corporate_actions.already_applied_transactions``. Closing a row
  therefore removes it from all three correctly and with no change to any of
  them.

* An offsetting reversal would leave BOTH rows current. The sums would still
  net out only if the reversal carried an inverted sign — and the deployed
  ``public.transaction_types`` vocabulary has no negative counterpart for any of
  its sixteen codes (``sell`` is not the reversal of ``buy``; it carries
  ``performance_impact='gain'``). It would also double ``count(*)``, which
  ``portfolio_commitments`` reads as ``n``, and make
  ``already_applied_transactions`` see two markers for one applied corporate
  action.

The consequence the UI must handle, identical to positions: **a correction
returns a NEW transaction id.** The original still resolves — it is history, and
the pane lists it — but it is no longer current.

THE CORRECTION CHAIN LIVES IN ``related_transaction_id``
──────────────────────────────────────────────────────────────────────────────
The successor points at its predecessor through the deployed
``related_transaction_id`` column, which had ZERO writers and ZERO rows before
this sprint: ``record_transaction`` accepted it as a parameter and nothing ever
passed one. That is what makes the column usable rather than hijacked — but it
also means the meaning is only unambiguous if nothing else writes it, so this
module's REST surface deliberately does NOT expose ``related_transaction_id`` on
create. Within this API the column means exactly one thing: *the row this row
corrects*. A future ingest that wants it for fee-to-trade pairing needs its own
column, and that is a real constraint reported rather than designed around.

WHAT A CORRECTION MAY NOT TOUCH, AND THE FAILURE IT PREVENTS
──────────────────────────────────────────────────────────────────────────────
``corporate_action_id`` and ``is_corporate_action_adjustment`` are NOT
correctable, and neither is settable on create. Together they are Phase F's
idempotency key: ``portfolio_corporate_actions.already_applied_transactions``
asks "has this org applied this action" by looking for a current transaction
carrying BOTH. A hand edit that cleared either would make a second
``apply_corporate_action`` call look not-yet-done and adjust the position a
second time — silently, and by exactly the split ratio.

``position_id`` is not correctable either, for the same reason ``asset_id`` is
not correctable on a position: re-pointing a ledger entry at a different holding
is not a correction of that entry, it is a different entry.

WHAT IS DELIBERATELY NOT HERE
──────────────────────────────────────────────────────────────────────────────
**No correction reason/note is accepted, because the deployed row has nowhere to
put one.** ``portfolio.transactions`` has no ``note``, no ``reason`` and no
``corrected_by`` column. ``document_field_corrections`` was made polymorphic by
the corrections sprint and is the obvious home, but it is field-grained and
guarded by ``document_field_corrections_document_pairing_chk``, whose
``target_type`` vocabulary this sprint has no mandate to extend. Accepting a
reason and dropping it would be worse than not accepting one. Reported as a gap.

No deletion. A ledger entry recorded in error is corrected, not removed.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from services.portfolio_assets import (
    AUTHORITIES,
    SOURCE_SYSTEMS,
    TABLE_ASSETS,
    TABLE_ENTITIES,
    TABLE_POSITIONS,
    TABLE_TRANSACTIONS,
    TABLE_TXN_TYPES,
    PortfolioError,
    TransactionMarketError,
    _check_choice,
    _current,
    _OrgWrite,
    _require_org,
    record_transaction,
)
from services.portfolio_documents import RECORD_TYPE_TRANSACTION
from services.portfolio_positions import TABLE_DOC_RECORD_LINKS

READ_PERMISSION = "view_portfolio"
WRITE_PERMISSION = "manage_portfolio"

#: One grid page. Same reasoning as the Positions grid: the client sorts and
#: filters the LOADED page, so the cap bounds how much of the truth the user is
#: actually sorting, and ``total`` is reported separately so a truncated page
#: reads as truncation rather than as the whole ledger.
DEFAULT_LIMIT = 200
MAX_LIMIT = 1000

#: Fields a correction may name.
#:
#: ``position_id`` is excluded: re-pointing a ledger entry at a different
#: holding is a different entry, not a correction of this one.
#:
#: ``corporate_action_id`` / ``is_corporate_action_adjustment`` are excluded
#: because together they are Phase F's idempotency key — see the module
#: docstring for the double-adjustment this prevents.
CORRECTABLE_FIELDS = frozenset({
    "transaction_type_code", "trade_date", "settle_date", "quantity", "price",
    "gross_amount", "fees", "taxes", "net_amount", "currency_code",
    "fx_rate_id", "authority", "source_system", "external_ref",
})

#: The subset an INLINE grid cell may correct — deliberately tiny.
#:
#: A ledger entry is not a spreadsheet cell. Everything monetary, the trade date
#: and the type code all feed ``record_transaction``'s validations
#: (type existence, ``is_active``, and the Phase E market-compatibility check
#: against the asset), any of which can REFUSE the correction. A refusal that
#: surfaces as a cell silently snapping back is worse than no inline edit at
#: all — the same rule the Positions grid follows.
#:
#: What is left is exactly the two fields ``record_transaction`` does not
#: validate at all: the settle date and the custodian's reference string. Both
#: are routine custodial fix-ups. Everything else goes through the right pane,
#: which has room to show why it was refused.
INLINE_CORRECTABLE_FIELDS = frozenset({"settle_date", "external_ref"})

#: Money/quantity fields, named once, so the router's float refusal and this
#: module's conversion cannot drift apart.
MONEY_FIELDS = ("quantity", "price", "gross_amount", "fees", "taxes",
                "net_amount")


# ── Serialisation ───────────────────────────────────────────────────────────


def _s(value: Any) -> str | None:
    """Decimal → exact string. A float at the JSON boundary is a rounding error
    introduced at the last possible layer, after the figure survived the
    database and the service as an exact Decimal."""
    return None if value is None else str(value)


def _d(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _ts(value) -> str | None:
    return None if value is None else value.isoformat()


# ── Vocabulary (CLAUDE.md Rule 1) ───────────────────────────────────────────


async def transaction_types(conn) -> list[dict[str, Any]]:
    """The deployed transaction-type vocabulary, labels included.

    Read from ``public.transaction_types`` and shipped with the grid page, so
    the type column renders a LABEL and the type filter offers real codes
    without the frontend hardcoding either (Rule 1).

    ``market`` and ``amount_basis`` travel with each type because the grid needs
    both: ``amount_basis`` decides whether a row's headline figure is a unit
    quantity or a currency amount, and ``market`` is what Phase E's
    compatibility check compares against the asset. A UI that guessed either
    would guess differently from ``record_transaction``.

    Retired types are INCLUDED, flagged by ``is_active``. A historical row can
    legitimately carry one, and dropping it from the vocabulary would make that
    row render with a raw code and no label.
    """
    rows = await conn.fetch(
        f"""
        SELECT code, label, category, direction, market, amount_basis,
               performance_impact, is_active, display_order
        FROM {TABLE_TXN_TYPES}
        ORDER BY display_order, code
        """
    )
    return [
        {
            "code": r["code"],
            "label": r["label"],
            "category": r["category"],
            "direction": r["direction"],
            "market": r["market"],
            "amount_basis": r["amount_basis"],
            "performance_impact": r["performance_impact"],
            "is_active": r["is_active"],
        }
        for r in rows
    ]


# ── Listing ─────────────────────────────────────────────────────────────────

_LIST_COLUMNS = f"""
    t.id::text                          AS id,
    t.position_id::text                 AS position_id,
    t.transaction_type_code             AS transaction_type_code,
    t.corporate_action_id::text         AS corporate_action_id,
    t.is_corporate_action_adjustment    AS is_corporate_action_adjustment,
    t.related_transaction_id::text      AS related_transaction_id,
    t.trade_date                        AS trade_date,
    t.settle_date                       AS settle_date,
    t.quantity                          AS quantity,
    t.price                             AS price,
    t.gross_amount                      AS gross_amount,
    t.fees                              AS fees,
    t.taxes                             AS taxes,
    t.net_amount                        AS net_amount,
    t.currency_code                     AS currency_code,
    t.fx_rate_id::text                  AS fx_rate_id,
    t.authority                         AS authority,
    t.source_system                     AS source_system,
    t.external_ref                      AS external_ref,
    t.valid_from                        AS valid_from,
    t.valid_to                          AS valid_to,
    t.system_from                       AS system_from,
    tt.label                            AS transaction_type_label,
    tt.category                         AS transaction_type_category,
    tt.direction                        AS transaction_type_direction,
    tt.market                           AS transaction_type_market,
    tt.amount_basis                     AS transaction_type_amount_basis,
    tt.performance_impact               AS transaction_type_performance_impact,
    tt.is_active                        AS transaction_type_is_active,
    p.owner_entity_id::text             AS owner_entity_id,
    p.asset_id::text                    AS asset_id,
    p.ownership_basis                   AS position_ownership_basis,
    p.as_of_date                        AS position_as_of_date,
    p.valid_to                          AS position_valid_to,
    a.name                              AS asset_name,
    a.short_name                        AS asset_short_name,
    a.asset_type                        AS asset_type,
    a.asset_class                       AS asset_class,
    a.valuation_method                  AS valuation_method,
    a.currency_code                     AS asset_currency_code,
    e.display_name                      AS owner_name,
    e.entity_type::text                 AS owner_entity_type
"""

# The type join is a LEFT JOIN so a transaction whose type was deleted out from
# under it still appears; losing the ledger row entirely would be a worse answer
# than losing its label.
#
# The position join is INNER and carries no `_current` predicate. A transaction
# recorded against a position that was later restated still points at the CLOSED
# position row — `portfolio_corporate_actions` says so explicitly — so filtering
# the join to current positions would make every pre-split transaction vanish.
_LIST_FROM = f"""
    FROM {TABLE_TRANSACTIONS} t
    LEFT JOIN {TABLE_TXN_TYPES} tt ON tt.code = t.transaction_type_code
    JOIN {TABLE_POSITIONS} p
      ON p.id = t.position_id AND p.org_id = t.org_id
    JOIN {TABLE_ASSETS} a
      ON a.id = p.asset_id AND a.org_id = p.org_id
    JOIN {TABLE_ENTITIES} e
      ON e.id = p.owner_entity_id AND e.org_id = p.org_id
     AND {_current('e')}
"""


def _build_filters(
    *,
    org_id: str,
    position_id: str | None,
    asset_id: str | None,
    owner_entity_id: str | None,
    transaction_type_code: str | None,
    transaction_type_category: str | None,
    trade_from: date | None,
    trade_to: date | None,
    is_corporate_action_adjustment: bool | None,
    source_system: str | None,
    authority: str | None,
    include_history: bool,
    search: str | None,
) -> tuple[str, list[Any]]:
    """Assemble the WHERE clause and its positional arguments.

    Every filter is a bound parameter — including ``search``, whose ``%`` is
    appended to the VALUE rather than spliced into the SQL.
    """
    where = ["t.org_id = $1::uuid"]
    args: list[Any] = [str(org_id)]

    def add(clause_template: str, value: Any) -> None:
        args.append(value)
        where.append(clause_template.format(n=len(args)))

    if not include_history:
        # The default. A corrected transaction leaves its predecessor behind,
        # and a grid showing both would show one ledger entry twice — and would
        # double any total a user computed by eye down the net-amount column.
        where.append(_current("t"))

    if position_id:
        add("t.position_id = ${n}::uuid", str(position_id))
    if asset_id:
        add("p.asset_id = ${n}::uuid", str(asset_id))
    if owner_entity_id:
        add("p.owner_entity_id = ${n}::uuid", str(owner_entity_id))
    if transaction_type_code:
        add("t.transaction_type_code = ${n}", transaction_type_code)
    if transaction_type_category:
        add("tt.category = ${n}", transaction_type_category)
    if trade_from:
        add("t.trade_date >= ${n}::date", trade_from)
    if trade_to:
        add("t.trade_date <= ${n}::date", trade_to)
    if is_corporate_action_adjustment is not None:
        # Tri-state on purpose: unset means "both kinds", which is what a
        # ledger view defaults to. `= false` is a real, different question from
        # "no filter" — it is the realized-gain population — so it must be
        # expressible, and the column is NOT NULL so the comparison is total.
        add("t.is_corporate_action_adjustment = ${n}",
            bool(is_corporate_action_adjustment))
    if source_system:
        add("t.source_system = ${n}", source_system)
    if authority:
        add("t.authority = ${n}", authority)
    if search:
        args.append(f"%{search.strip()}%")
        n = len(args)
        where.append(
            f"(a.name ILIKE ${n} OR e.display_name ILIKE ${n} "
            f" OR t.external_ref ILIKE ${n})"
        )

    return " AND ".join(where), args


async def list_transactions(
    conn,
    *,
    org_id: str,
    position_id: str | None = None,
    asset_id: str | None = None,
    owner_entity_id: str | None = None,
    transaction_type_code: str | None = None,
    transaction_type_category: str | None = None,
    trade_from: date | None = None,
    trade_to: date | None = None,
    is_corporate_action_adjustment: bool | None = None,
    source_system: str | None = None,
    authority: str | None = None,
    include_history: bool = False,
    search: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """One grid page. Returns ``{total, limit, offset, returned, transactions}``.

    ``total`` is the count BEFORE the limit, so the UI can say "showing 200 of
    1,431" rather than implying it has the whole ledger.
    """
    org_id = _require_org(org_id)
    if source_system:
        _check_choice(source_system, SOURCE_SYSTEMS, "source_system")
    if authority:
        _check_choice(authority, AUTHORITIES, "authority")
    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))

    where, args = _build_filters(
        org_id=org_id,
        position_id=position_id,
        asset_id=asset_id,
        owner_entity_id=owner_entity_id,
        transaction_type_code=transaction_type_code,
        transaction_type_category=transaction_type_category,
        trade_from=trade_from,
        trade_to=trade_to,
        is_corporate_action_adjustment=is_corporate_action_adjustment,
        source_system=source_system,
        authority=authority,
        include_history=include_history,
        search=search,
    )

    total = await conn.fetchval(f"SELECT count(*) {_LIST_FROM} WHERE {where}", *args)
    rows = await conn.fetch(
        f"""
        SELECT {_LIST_COLUMNS}
        {_LIST_FROM}
        WHERE {where}
        ORDER BY t.trade_date DESC, t.system_from DESC, t.id ASC
        LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}
        """,
        *args, limit, offset,
    )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "returned": len(rows),
        "transactions": [_row_to_json(r) for r in rows],
    }


def _row_to_json(r) -> dict[str, Any]:
    return {
        "id": r["id"],
        "position_id": r["position_id"],
        "transaction_type_code": r["transaction_type_code"],
        # The LABEL is what the grid renders (Rule 1). It falls back to the raw
        # code only when the type row is gone — never the code dressed up as a
        # label, which would make a dangling code look configured.
        "transaction_type_label": (
            r["transaction_type_label"] or r["transaction_type_code"]
        ),
        "transaction_type_category": r["transaction_type_category"],
        "transaction_type_direction": r["transaction_type_direction"],
        "transaction_type_market": r["transaction_type_market"],
        # Which figure is this row's headline: a unit quantity or a currency
        # amount. Server-published so the grid does not re-derive it.
        "amount_basis": r["transaction_type_amount_basis"],
        "performance_impact": r["transaction_type_performance_impact"],
        "transaction_type_is_active": r["transaction_type_is_active"],
        "trade_date": _d(r["trade_date"]),
        "settle_date": _d(r["settle_date"]),
        "quantity": _s(r["quantity"]),
        "price": _s(r["price"]),
        "gross_amount": _s(r["gross_amount"]),
        "fees": _s(r["fees"]),
        "taxes": _s(r["taxes"]),
        "net_amount": _s(r["net_amount"]),
        "currency_code": r["currency_code"],
        "fx_rate_id": r["fx_rate_id"],
        "authority": r["authority"],
        "source_system": r["source_system"],
        "external_ref": r["external_ref"],
        # Phase F. NOT derived from corporate_action_id here either — this is
        # the stored flag, read back verbatim, because a report filtering on it
        # must see the same value the writer set.
        "is_corporate_action_adjustment": r["is_corporate_action_adjustment"],
        "corporate_action_id": r["corporate_action_id"],
        # Within this API this means "the transaction this row corrects".
        "corrects_transaction_id": r["related_transaction_id"],
        "owner_entity_id": r["owner_entity_id"],
        "owner_name": r["owner_name"],
        "owner_entity_type": r["owner_entity_type"],
        "asset_id": r["asset_id"],
        "asset_name": r["asset_name"],
        "asset_short_name": r["asset_short_name"],
        "asset_type": r["asset_type"],
        "asset_class": r["asset_class"],
        "valuation_method": r["valuation_method"],
        "asset_currency_code": r["asset_currency_code"],
        "position_ownership_basis": r["position_ownership_basis"],
        "position_as_of_date": _d(r["position_as_of_date"]),
        # The position this entry hangs off may itself have been restated away.
        "position_is_current": r["position_valid_to"] is None,
        "valid_from": _ts(r["valid_from"]),
        "valid_to": _ts(r["valid_to"]),
        "recorded_at": _ts(r["system_from"]),
        "is_current": r["valid_to"] is None,
        "is_corrected": r["valid_to"] is not None,
    }


# ── One transaction, in full — the right pane ───────────────────────────────


async def get_transaction(
    conn, *, org_id: str, transaction_id: str
) -> dict[str, Any] | None:
    """Everything the detail pane shows. ``None`` if not in this org.

    The transaction, the position it belongs to (with the link target the pane
    needs to click through to the Positions screen), and the full correction
    chain. One call, because the pane opens on a row click and a waterfall would
    render it in pieces.

    A superseded transaction is RETURNED rather than 404'd — it is history, it
    is reachable from the correction chain, and hiding it would make a
    correction look like a deletion.

    A 404 means "not in your org" as well as "does not exist", and deliberately
    does not distinguish them: telling a caller that a transaction id exists
    somewhere else is itself a cross-tenant leak.
    """
    org_id = _require_org(org_id)
    row = await conn.fetchrow(
        f"""
        SELECT {_LIST_COLUMNS}
        {_LIST_FROM}
        WHERE t.id = $1::uuid AND t.org_id = $2::uuid
        """,
        str(transaction_id), org_id,
    )
    if row is None:
        return None

    return {
        "transaction": _row_to_json(row),
        "position": await position_link(
            conn, org_id=org_id, position_id=row["position_id"],
        ),
        "correction_history": await correction_history(
            conn, org_id=org_id, transaction_id=str(transaction_id),
        ),
        # The record_type the Documents panel needs, emitted by the API rather
        # than hardcoded in the component: `document_record_links.record_type`
        # has NO CHECK constraint, so a frontend typo would write a link nothing
        # ever reads back and nothing would raise. Phase D already defines this
        # constant and dispatches on it generically, so the panel works on a
        # transaction today — the same finding UX 1 recorded for positions.
        "document_record_type": RECORD_TYPE_TRANSACTION,
    }


async def position_link(
    conn, *, org_id: str, position_id: str
) -> dict[str, Any] | None:
    """The owning position, plus where the pane should actually link to.

    ``id`` is the row this transaction is attached to. ``current_position_id``
    is the CURRENT row for the same ``(owner_entity_id, asset_id)``, which is
    frequently a different row: a corporate action or a hand correction restates
    a position and mints a new id, while the transactions stay attached to the
    id they were recorded against.

    Both are returned because they answer different questions and the pane needs
    both. Linking only ``id`` would send a user to the Positions grid — which
    hides closed rows by default — and land them on nothing.
    """
    org_id = _require_org(org_id)
    row = await conn.fetchrow(
        f"""
        SELECT p.id::text                 AS id,
               p.owner_entity_id::text    AS owner_entity_id,
               p.asset_id::text           AS asset_id,
               p.as_of_date               AS as_of_date,
               p.ownership_basis          AS ownership_basis,
               p.quantity                 AS quantity,
               p.ownership_pct            AS ownership_pct,
               p.market_value             AS market_value,
               p.taxonomy_key             AS taxonomy_key,
               p.authority                AS authority,
               p.source_system            AS source_system,
               p.valid_to                 AS valid_to,
               a.name                     AS asset_name,
               a.asset_type               AS asset_type,
               a.currency_code            AS currency_code,
               e.display_name             AS owner_name,
               (
                   SELECT c.id::text FROM {TABLE_POSITIONS} c
                   WHERE c.org_id = p.org_id
                     AND c.owner_entity_id = p.owner_entity_id
                     AND c.asset_id = p.asset_id
                     AND {_current('c')}
                   ORDER BY c.valid_from DESC
                   LIMIT 1
               )                          AS current_position_id
        FROM {TABLE_POSITIONS} p
        JOIN {TABLE_ASSETS} a ON a.id = p.asset_id AND a.org_id = p.org_id
        JOIN {TABLE_ENTITIES} e
          ON e.id = p.owner_entity_id AND e.org_id = p.org_id
         AND {_current('e')}
        WHERE p.id = $1::uuid AND p.org_id = $2::uuid
        """,
        str(position_id), org_id,
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "current_position_id": row["current_position_id"],
        "is_current": row["valid_to"] is None,
        "owner_entity_id": row["owner_entity_id"],
        "owner_name": row["owner_name"],
        "asset_id": row["asset_id"],
        "asset_name": row["asset_name"],
        "asset_type": row["asset_type"],
        "currency_code": row["currency_code"],
        "as_of_date": _d(row["as_of_date"]),
        "ownership_basis": row["ownership_basis"],
        "quantity": _s(row["quantity"]),
        "ownership_pct": _s(row["ownership_pct"]),
        "market_value": _s(row["market_value"]),
        "taxonomy_key": row["taxonomy_key"],
        "authority": row["authority"],
        "source_system": row["source_system"],
    }


_CHAIN_COLUMNS = """
    t.id::text                       AS id,
    t.transaction_type_code          AS transaction_type_code,
    t.trade_date                     AS trade_date,
    t.settle_date                    AS settle_date,
    t.quantity                       AS quantity,
    t.price                          AS price,
    t.net_amount                     AS net_amount,
    t.currency_code                  AS currency_code,
    t.authority                      AS authority,
    t.source_system                  AS source_system,
    t.external_ref                   AS external_ref,
    t.related_transaction_id::text   AS related_transaction_id,
    t.valid_from                     AS valid_from,
    t.valid_to                       AS valid_to,
    t.system_from                    AS system_from
"""


def _order_chain(rows: list) -> list:
    """Oldest-first, by following ``related_transaction_id`` from the root.

    The root is the row whose predecessor is not itself in the chain. Rows the
    walk cannot reach — impossible for a chain this module wrote, but possible
    if a service-level caller ever set ``related_transaction_id`` to something
    outside it — are appended rather than dropped, because a version silently
    missing from a correction history is the one failure mode this list exists
    to prevent.
    """
    by_id = {r["id"]: r for r in rows}
    child_of = {r["related_transaction_id"]: r for r in rows
                if r["related_transaction_id"] in by_id}
    roots = [r for r in rows if r["related_transaction_id"] not in by_id]
    ordered: list = []
    seen: set[str] = set()
    for root in sorted(roots, key=lambda r: (r["system_from"], r["id"])):
        node = root
        while node is not None and node["id"] not in seen:
            ordered.append(node)
            seen.add(node["id"])
            node = child_of.get(node["id"])
    ordered.extend(r for r in rows if r["id"] not in seen)
    return ordered


async def correction_history(
    conn, *, org_id: str, transaction_id: str
) -> list[dict[str, Any]]:
    """The whole correction chain this transaction sits in, oldest first.

    Walked in TWO separate recursive CTEs — ancestors, then descendants — rather
    than one clever bidirectional query. A single CTE would have to reference
    the recursive term inside a subquery to follow the parent pointer upwards,
    which Postgres restricts; two plain walks are provably correct and each is
    the textbook shape.

    ``system_to IS NULL`` on both walks: a row retired on the SYSTEM axis was
    never true and is not part of the story. ``valid_to`` is deliberately NOT
    filtered — the closed rows ARE the history, and excluding them would leave
    the chain with one entry.
    """
    org_id = _require_org(org_id)

    ancestors = await conn.fetch(
        f"""
        WITH RECURSIVE up AS (
            SELECT t.id, t.related_transaction_id
            FROM {TABLE_TRANSACTIONS} t
            WHERE t.id = $1::uuid AND t.org_id = $2::uuid AND t.system_to IS NULL
          UNION
            SELECT p.id, p.related_transaction_id
            FROM {TABLE_TRANSACTIONS} p
            JOIN up ON p.id = up.related_transaction_id
            WHERE p.org_id = $2::uuid AND p.system_to IS NULL
        )
        SELECT id::text AS id FROM up
        """,
        str(transaction_id), org_id,
    )
    descendants = await conn.fetch(
        f"""
        WITH RECURSIVE down AS (
            SELECT t.id
            FROM {TABLE_TRANSACTIONS} t
            WHERE t.id = $1::uuid AND t.org_id = $2::uuid AND t.system_to IS NULL
          UNION
            SELECT c.id
            FROM {TABLE_TRANSACTIONS} c
            JOIN down ON c.related_transaction_id = down.id
            WHERE c.org_id = $2::uuid AND c.system_to IS NULL
        )
        SELECT id::text AS id FROM down
        """,
        str(transaction_id), org_id,
    )

    ids = sorted({r["id"] for r in ancestors} | {r["id"] for r in descendants})

    rows = await conn.fetch(
        f"""
        SELECT {_CHAIN_COLUMNS},
               tt.label AS transaction_type_label
        FROM {TABLE_TRANSACTIONS} t
        LEFT JOIN {TABLE_TXN_TYPES} tt ON tt.code = t.transaction_type_code
        WHERE t.org_id = $1::uuid AND t.id = ANY($2::uuid[])
        """,
        org_id, ids,
    )
    # Ordered by walking the POINTERS, not by timestamp. `system_from` is
    # `now()`, which in Postgres is transaction-start time — so an entry created
    # and corrected inside one transaction would carry the SAME stamp on both
    # rows and the chain would order arbitrarily. The links are exact.
    rows = _order_chain(rows)
    return [
        {
            "id": r["id"],
            "transaction_type_code": r["transaction_type_code"],
            "transaction_type_label": (
                r["transaction_type_label"] or r["transaction_type_code"]
            ),
            "trade_date": _d(r["trade_date"]),
            "settle_date": _d(r["settle_date"]),
            "quantity": _s(r["quantity"]),
            "price": _s(r["price"]),
            "net_amount": _s(r["net_amount"]),
            "currency_code": r["currency_code"],
            "authority": r["authority"],
            "source_system": r["source_system"],
            "external_ref": r["external_ref"],
            "corrects_transaction_id": r["related_transaction_id"],
            "valid_from": _ts(r["valid_from"]),
            "valid_to": _ts(r["valid_to"]),
            "recorded_at": _ts(r["system_from"]),
            "is_current": r["valid_to"] is None,
        }
        for r in rows
    ]


# ── Correcting — a bi-temporal supersession, never an UPDATE ────────────────


async def _close_transaction(conn, org_id: str, transaction_id: str) -> None:
    """CLAUDE.md Rule 3, step 1. Writes ``valid_to`` and nothing else.

    The ``AND valid_to IS NULL`` in the predicate is load-bearing: without it a
    concurrent correction that already closed the row would push ``valid_to``
    forward a second time and the two versions would overlap in valid time. The
    count is checked so a lost race RAISES instead of silently branching the
    ledger off a superseded row.
    """
    closed = await conn.fetchval(
        f"""
        WITH upd AS (
            UPDATE {TABLE_TRANSACTIONS} t
            SET valid_to = now()
            WHERE t.id = $1::uuid AND t.org_id = $2::uuid AND {_current('t')}
            RETURNING 1
        ) SELECT count(*) FROM upd
        """,
        str(transaction_id), str(org_id),
    )
    if not closed:
        raise PortfolioError(
            f"transaction {transaction_id} is not a current row in this org — "
            f"it was already corrected, or it never existed here. The "
            f"correction is refused rather than branching the ledger off a "
            f"superseded entry."
        )


async def correct_transaction(
    conn, *, org_id: str, transaction_id: str, changes: dict[str, Any]
) -> str:
    """Correct a transaction. Returns the NEW transaction's id.

    Close the current row (``valid_to = now()``) and record a successor through
    :func:`~services.portfolio_assets.record_transaction`, carrying every field
    the caller did not name and pointing ``related_transaction_id`` at the row
    it replaces. Both steps run inside ONE transaction: a close that committed
    without its successor would delete a ledger entry from every current read,
    which is the one outcome a correction must never produce.

    Going through ``record_transaction`` rather than writing the INSERT here is
    the point of the function. That is the only code checking a type's
    ``market`` against the asset's (Phase E) and the only code refusing a
    retired type — so a correction that re-typed a listed-equity buy as a
    capital call is refused on the CORRECTION, not just on the original insert.

    ``corporate_action_id`` and ``is_corporate_action_adjustment`` are carried
    forward VERBATIM and are not correctable. See the module docstring: they are
    Phase F's idempotency key, and a correction that dropped either would make a
    re-apply of the same corporate action look not-yet-done.

    An explicit ``None`` in ``changes`` CLEARS the field. That is the difference
    between "leave it alone" (key absent) and "this figure was never real" (key
    present, value ``None``) — a fee wrongly recorded as 12.34 has to be
    clearable, not just settable to zero, because zero fees and unrecorded fees
    are different facts.
    """
    org_id = _require_org(org_id)
    unknown = sorted(set(changes) - CORRECTABLE_FIELDS)
    if unknown:
        raise PortfolioError(
            f"not correctable: {unknown}. Correctable fields are "
            f"{sorted(CORRECTABLE_FIELDS)}. `position_id` is excluded because "
            f"re-pointing a ledger entry at a different holding is a different "
            f"entry, not a correction. `corporate_action_id` and "
            f"`is_corporate_action_adjustment` are excluded because together "
            f"they are the idempotency key that stops a corporate action being "
            f"applied twice."
        )
    if not changes:
        raise PortfolioError("no changes supplied")

    if "authority" in changes:
        _check_choice(changes["authority"], AUTHORITIES, "authority")
    if "source_system" in changes:
        _check_choice(changes["source_system"], SOURCE_SYSTEMS, "source_system")
    if "trade_date" in changes and not isinstance(changes["trade_date"], date):
        raise PortfolioError(
            f"trade_date must be a datetime.date — got "
            f"{type(changes['trade_date']).__name__}"
        )
    if changes.get("settle_date") is not None and not isinstance(
        changes["settle_date"], date
    ):
        raise PortfolioError(
            f"settle_date must be a datetime.date or null — got "
            f"{type(changes['settle_date']).__name__}"
        )

    async with _OrgWrite(conn, org_id) as c:
        current = await c.fetchrow(
            f"""
            SELECT t.position_id::text          AS position_id,
                   t.transaction_type_code,
                   t.trade_date, t.settle_date, t.quantity, t.price,
                   t.gross_amount, t.fees, t.taxes, t.net_amount,
                   t.currency_code,
                   t.fx_rate_id::text           AS fx_rate_id,
                   t.authority, t.source_system, t.external_ref,
                   t.corporate_action_id::text  AS corporate_action_id,
                   t.is_corporate_action_adjustment
            FROM {TABLE_TRANSACTIONS} t
            WHERE t.id = $1::uuid AND t.org_id = $2::uuid AND {_current('t')}
            """,
            str(transaction_id), org_id,
        )
        if current is None:
            raise PortfolioError(
                f"transaction {transaction_id} is not a current transaction in "
                f"this org"
            )

        merged = dict(current)
        merged.update(changes)

        await _close_transaction(c, org_id, transaction_id)

        new_id = await record_transaction(
            c,
            org_id=org_id,
            position_id=merged["position_id"],
            transaction_type_code=merged["transaction_type_code"],
            trade_date=merged["trade_date"],
            authority=merged["authority"],
            source_system=merged["source_system"],
            settle_date=merged["settle_date"],
            quantity=merged["quantity"],
            price=merged["price"],
            gross_amount=merged["gross_amount"],
            fees=merged["fees"],
            taxes=merged["taxes"],
            net_amount=merged["net_amount"],
            currency_code=merged["currency_code"],
            fx_rate_id=merged["fx_rate_id"],
            external_ref=merged["external_ref"],
            # THE CHAIN. Within this API this column means exactly one thing.
            related_transaction_id=str(transaction_id),
            # Carried verbatim, never re-derived — Phase F's key.
            corporate_action_id=merged["corporate_action_id"],
            is_corporate_action_adjustment=bool(
                merged["is_corporate_action_adjustment"]
            ),
        )

        await _carry_document_links(c, org_id, str(transaction_id), new_id)
        return new_id


async def _carry_document_links(
    conn, org_id: str, old_transaction_id: str, new_transaction_id: str
) -> int:
    """Re-point this transaction's source-document links at its successor.

    Exactly the bug ``portfolio_positions._carry_document_links`` exists to
    prevent, one table over. Phase D links a document to a transaction by
    ``(record_type='portfolio_transaction', record_id)``, and a correction mints
    a NEW record_id — so the custodial statement or K-1 a ledger entry was read
    out of would stop appearing in the detail pane the first time anyone fixed a
    settle date.

    COPY, not move. The closed row keeps its links so the historical entry stays
    exactly as auditable as it was before the correction.

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
        str(org_id), str(old_transaction_id), str(new_transaction_id),
        RECORD_TYPE_TRANSACTION,
    )
    return int(copied or 0)


# ── Positions, for the create form's picker ─────────────────────────────────


async def list_positions_for_picker(
    conn, *, org_id: str, owner_entity_id: str | None = None,
    search: str | None = None, limit: int = 50,
) -> list[dict]:
    """Current positions, thin, for the "record a transaction against…" picker.

    Deliberately not ``portfolio_positions.list_positions``: that call resolves
    a valuation per asset, which is its expensive part and which a picker does
    not need. Only CURRENT positions are offered — recording a new transaction
    against a closed position row would attach it to history.

    ``valuation_method`` travels with each row because it is what Phase E's
    market check compares the transaction type against, so the form can explain
    a refusal instead of just showing it.
    """
    org_id = _require_org(org_id)
    limit = max(1, min(int(limit), 200))
    args: list[Any] = [org_id]
    clause = ""
    if owner_entity_id:
        args.append(str(owner_entity_id))
        clause += f" AND p.owner_entity_id = ${len(args)}::uuid"
    if search and search.strip():
        args.append(f"%{search.strip()}%")
        clause += (
            f" AND (a.name ILIKE ${len(args)} OR e.display_name ILIKE ${len(args)})"
        )
    rows = await conn.fetch(
        f"""
        SELECT p.id::text              AS id,
               p.owner_entity_id::text AS owner_entity_id,
               p.asset_id::text        AS asset_id,
               p.as_of_date            AS as_of_date,
               p.ownership_basis       AS ownership_basis,
               a.name                  AS asset_name,
               a.asset_type            AS asset_type,
               a.valuation_method      AS valuation_method,
               a.currency_code         AS currency_code,
               e.display_name          AS owner_name
        FROM {TABLE_POSITIONS} p
        JOIN {TABLE_ASSETS} a ON a.id = p.asset_id AND a.org_id = p.org_id
        JOIN {TABLE_ENTITIES} e
          ON e.id = p.owner_entity_id AND e.org_id = p.org_id
         AND {_current('e')}
        WHERE p.org_id = $1::uuid AND {_current('p')}
        {clause}
        ORDER BY a.name, e.display_name
        LIMIT ${len(args) + 1}
        """,
        *args, limit,
    )
    return [
        {
            "id": r["id"],
            "owner_entity_id": r["owner_entity_id"],
            "owner_name": r["owner_name"],
            "asset_id": r["asset_id"],
            "asset_name": r["asset_name"],
            "asset_type": r["asset_type"],
            "valuation_method": r["valuation_method"],
            "currency_code": r["currency_code"],
            "as_of_date": _d(r["as_of_date"]),
            "ownership_basis": r["ownership_basis"],
        }
        for r in rows
    ]


__all__ = [
    "CORRECTABLE_FIELDS",
    "DEFAULT_LIMIT",
    "INLINE_CORRECTABLE_FIELDS",
    "MAX_LIMIT",
    "MONEY_FIELDS",
    "READ_PERMISSION",
    "RECORD_TYPE_TRANSACTION",
    "TABLE_DOC_RECORD_LINKS",
    "WRITE_PERMISSION",
    "PortfolioError",
    "TransactionMarketError",
    "correct_transaction",
    "correction_history",
    "get_transaction",
    "list_positions_for_picker",
    "list_transactions",
    "position_link",
    "transaction_types",
]
