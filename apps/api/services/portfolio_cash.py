"""Portfolio Phase D — cash, modelled as an asset like everything else.

THE WHOLE POINT: THERE IS NO SPECIAL CASE IN HERE
──────────────────────────────────────────────────────────────────────────────
Cash is a position. Not a column on an entity, not a ``cash_balances`` table,
not a branch in the rollup. A dollar held at a custodian is a thing you own, and
the object model already has a shape for a thing you own — ``assets`` +
``positions``, joined by the same edge every other holding uses.

So every function below is a thin composition of A2's two writers:

    ensure_cash_asset   → portfolio_assets.create_asset
    record_cash_balance → ensure_cash_asset + portfolio_assets.create_position

There is no ``INSERT INTO portfolio.assets`` and no ``INSERT INTO
portfolio.positions`` anywhere in this file, deliberately. ``create_position``
is the ONLY thing in the codebase enforcing the ownership-basis contract (there
is no CHECK constraint behind it — A2 module docstring point 2), so a cash
writer that inserted directly would be the one holding type whose basis nobody
validated. ``verify_portfoliod.py`` AST-checks this file for a bare INSERT for
that reason.

A BANK ACCOUNT IS NOT A NEW CONCEPT EITHER
──────────────────────────────────────────────────────────────────────────────
"Chase checking holds $50,000" is an ``entity_type='account'`` entity as the
``owner_entity_id`` of a cash position. That is the SAME call as "the family
trust holds $50,000 directly", with a different owner. Nothing here inspects the
owner's ``entity_type``, defaults to an account, or requires one — §5 of the
design says the account node is optional, and this module is one of the places
that has to actually be true.

WHY ``value`` BASIS AND ``amortized_cost``
──────────────────────────────────────────────────────────────────────────────
``ownership_basis='value'`` (A2 §3.2): cash has no unit and no percentage; the
amount IS the fact, with nothing to derive it from. ``create_position`` will
refuse a ``quantity`` or an ``ownership_pct`` on it, which is correct — "1000
units of USD at a price of 1" is a listed-security shape imposed on something
that is not one, and it invites a rollup to recompute a value that was never
computed in the first place.

``valuation_method='amortized_cost'``: cash is carried at face. This is also
load-bearing, not decorative — A2's ``record_transaction`` derives an asset's
market from ``valuation_method``, and ``amortized_cost`` maps to ``both``, so
public-market types (``buy``, ``dividend``) and private-market types
(``call_investment``, ``dist_roc``) are BOTH legal against a cash asset. They
have to be: cash is the settlement leg of every transaction in either book.
``market_price`` would have made every capital call against cash illegal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg

from services.portfolio_assets import (
    AMORTIZED_COST,
    VALUE,
    PortfolioError,
    TABLE_ASSETS,
    TABLE_POSITIONS,
    _OrgWrite,
    _money,
    _require_org,
    create_asset,
    create_position,
)

#: ``assets.asset_type`` is NOT NULL with no CHECK, so this is a convention.
#: It is also the key half of ``assets_cash_active_uniq``, which means changing
#: this string orphans every cash asset already created under the old one.
CASH_ASSET_TYPE = "cash"

#: Default when a caller does not say. ``fx_rates`` and A2 both treat USD as the
#: base; a cash asset with no currency is meaningless, so there is a default
#: rather than a NULL.
DEFAULT_CURRENCY = "USD"


@dataclass(frozen=True)
class CashPosition:
    """What :func:`record_cash_balance` wrote — both halves of the pair."""

    asset_id: str
    position_id: str
    org_id: str
    owner_entity_id: str
    currency_code: str
    as_of_date: date
    amount: Decimal


def _normalize_currency(currency_code: Any) -> str:
    """Fold to the stored form. Applied symmetrically by writes AND lookups.

    Same discipline as A2's ``normalize_identifier_value``: ``'usd'`` and
    ``'USD'`` must not become two cash assets, and folding on only one side
    would make the uniqueness index unable to see the collision.
    """
    value = (currency_code or "").strip().upper()
    if not value:
        raise PortfolioError(
            "currency_code is required. assets.currency_code is NULLABLE, so "
            "assets_cash_active_uniq — which is a partial unique index over "
            "(org_id, currency_code) — cannot constrain a NULL: two NULL-currency "
            "cash assets would both be accepted, because NULL is not equal to "
            "itself in a unique index. The refusal here is what closes that hole."
        )
    return value


def cash_asset_name(currency_code: str) -> str:
    """The display name for an org's cash asset in one currency."""
    return f"Cash ({_normalize_currency(currency_code)})"


async def ensure_cash_asset(
    conn, *, org_id: str, currency_code: str = DEFAULT_CURRENCY
) -> str:
    """Find-or-create the org's cash asset for one currency. Returns its id.

    ONE per ``(org_id, currency_code)``. USD cash and EUR cash are two different
    assets — they have different values and cannot be summed without an FX rate
    — but the same org asking twice for USD cash gets the same row back.

    Idempotent in two layers, because a SELECT-then-INSERT in Python is not:

      * the ``SELECT`` below, which is the normal path; and
      * ``assets_cash_active_uniq`` (docs/portfoliod_part1.sql) behind it, so
        two concurrent callers that both miss the SELECT do not both insert.
        The loser catches the unique violation and re-reads the winner's row.

    Without the index this would be idempotent only when nothing raced, and the
    failure is silent: two cash assets, each with its own positions, and a
    portfolio that shows the org's cash split across two lines that neither
    sums nor reconciles.
    """
    org_id = _require_org(org_id)
    currency = _normalize_currency(currency_code)

    async with _OrgWrite(conn, org_id) as c:
        existing = await c.fetchval(
            f"""
            SELECT id::text FROM {TABLE_ASSETS}
            WHERE org_id = $1::uuid AND asset_type = $2 AND currency_code = $3
              AND valid_to IS NULL AND system_to IS NULL
            """,
            org_id, CASH_ASSET_TYPE, currency,
        )
        if existing:
            return existing

    try:
        return await create_asset(
            conn,
            org_id=org_id,
            name=cash_asset_name(currency),
            asset_type=CASH_ASSET_TYPE,
            asset_class="financial",
            ownership_basis=VALUE,
            valuation_method=AMORTIZED_COST,
            currency_code=currency,
            # Cash is a holding and belongs in the denominator. Excluding it
            # would make every allocation percentage in the app wrong by
            # however much cash the member happens to be sitting on.
            include_in_performance=True,
        )
    except asyncpg.exceptions.UniqueViolationError:
        async with _OrgWrite(conn, org_id) as c:
            won = await c.fetchval(
                f"""
                SELECT id::text FROM {TABLE_ASSETS}
                WHERE org_id = $1::uuid AND asset_type = $2 AND currency_code = $3
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                org_id, CASH_ASSET_TYPE, currency,
            )
        if won:
            return won
        raise


async def record_cash_balance(
    conn,
    *,
    org_id: str,
    owner_entity_id: str,
    amount: Decimal | int | str,
    as_of_date: date,
    currency_code: str = DEFAULT_CURRENCY,
    authority: str = "stated",
    source_system: str = "manual",
    cost_basis: Decimal | int | str | None = None,
    accrued_income: Decimal | int | str | None = None,
    taxonomy_key: str | None = None,
    is_reconciled: bool = False,
    superseded_by_source: str | None = None,
) -> CashPosition:
    """Record a cash balance as a position. Returns both ids.

    ``owner_entity_id`` may be ANY entity — an ``account`` node for a bank or
    brokerage balance, or a trust / LLC / individual holding cash directly. This
    function does not look at ``entity_type`` and does not care.

    ``amount`` goes through A2's ``_money``, which REFUSES ``float`` rather than
    converting it. That matters more for cash than for anything else: a cash
    balance is the one number a user will read to the cent and compare against a
    bank statement, and ``Decimal(0.1)`` preserves the binary error silently all
    the way to the screen.

    A negative amount is permitted — an overdrawn account and a margin debit are
    real, and refusing them here would push someone toward modelling a debit as
    a positive balance somewhere else.

    A balance is a NEW position row per ``as_of_date``, not an update. Positions
    are bi-temporal (CLAUDE.md Rule 3); yesterday's balance did not stop being
    what it was, it stopped being CURRENT. Two balances on the SAME date from
    two sources are resolved by Phase B's ``portfolio_precedence``, which is
    already the mechanism for that and is not re-implemented here.
    """
    org_id = _require_org(org_id)
    currency = _normalize_currency(currency_code)
    value = _money(amount, "amount")
    if not isinstance(as_of_date, date):
        raise PortfolioError(
            f"as_of_date must be a datetime.date — got {type(as_of_date).__name__}"
        )

    asset_id = await ensure_cash_asset(conn, org_id=org_id, currency_code=currency)

    position_id = await create_position(
        conn,
        org_id=org_id,
        owner_entity_id=str(owner_entity_id),
        asset_id=asset_id,
        as_of_date=as_of_date,
        # Explicit rather than inherited from the asset. They are the same value
        # today; stating it means a future edit to the cash asset's default
        # cannot silently change the basis of every cash position ever written.
        ownership_basis=VALUE,
        market_value=value,
        # market_value_native stays NULL: this IS the native amount. A rate of
        # 1.0 against itself is not a conversion, and writing one would make an
        # unconverted figure look converted.
        cost_basis=cost_basis,
        accrued_income=accrued_income,
        authority=authority,
        source_system=source_system,
        taxonomy_key=taxonomy_key,
        is_reconciled=is_reconciled,
        superseded_by_source=superseded_by_source,
    )

    return CashPosition(
        asset_id=asset_id,
        position_id=position_id,
        org_id=org_id,
        owner_entity_id=str(owner_entity_id),
        currency_code=currency,
        as_of_date=as_of_date,
        amount=value,
    )


async def get_cash_balance(
    conn,
    *,
    org_id: str,
    owner_entity_id: str,
    currency_code: str = DEFAULT_CURRENCY,
    as_of: date | None = None,
) -> Decimal | None:
    """The current cash balance for one owner in one currency, or ``None``.

    ``None`` means "no cash position exists", and is NOT ``Decimal(0)`` — same
    rule as A2's ``AssetValue``. "This entity has never had a cash position
    recorded" and "this entity's cash balance is zero" are different facts, and
    only one of them should make a reconciliation stop.

    Positions superseded by source precedence (``superseded_by_source`` set) are
    excluded: Phase B writes that column precisely to mark a losing row, and
    summing it back in here would undo the resolution.
    """
    org_id = _require_org(org_id)
    currency = _normalize_currency(currency_code)

    async with _OrgWrite(conn, org_id) as c:
        row = await c.fetchrow(
            f"""
            SELECT p.market_value
            FROM {TABLE_POSITIONS} p
            JOIN {TABLE_ASSETS} a
              ON a.id = p.asset_id AND a.org_id = p.org_id
            WHERE p.org_id = $1::uuid
              AND p.owner_entity_id = $2::uuid
              AND a.asset_type = $3
              AND a.currency_code = $4
              AND p.superseded_by_source IS NULL
              AND p.valid_to IS NULL AND p.system_to IS NULL
              AND a.valid_to IS NULL AND a.system_to IS NULL
              AND ($5::date IS NULL OR p.as_of_date <= $5::date)
            ORDER BY p.as_of_date DESC, p.system_from DESC
            LIMIT 1
            """,
            org_id, str(owner_entity_id), CASH_ASSET_TYPE, currency, as_of,
        )
    if row is None or row["market_value"] is None:
        return None
    return Decimal(str(row["market_value"]))
