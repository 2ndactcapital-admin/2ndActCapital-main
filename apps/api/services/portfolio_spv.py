"""Portfolio Phase D — the SPV derivation layer.

WHAT THIS MODULE IS
──────────────────────────────────────────────────────────────────────────────
An SPV subscription IS a portfolio holding. Before this phase it was invisible
to ``portfolio.positions``: the two subsystems shared exactly one deployed join
(``assets_internal_spv_id_fkey``) and nothing used it, so a member's SPV
interests simply did not appear in their portfolio.

This module makes them appear WITHOUT copying them. ``spv_subscriptions``
remains the book of record; ``portfolio.spv_derived_positions`` is a VIEW that
projects the current ones into position shape, and everything here is a READ
against that view plus the one write it needs to have something to join to.

THE TASK 1b FINDING, WHICH IS THE REASON THIS MODULE LOOKS LIKE THIS
──────────────────────────────────────────────────────────────────────────────
The brief asked where an SPV interest's CURRENT MARKET VALUE lives. Traced
against the deployed database, the honest answer is that it did not live
anywhere, and three of the four plausible homes are dead ends:

  * ``spv_subscriptions.commitment_amount`` / ``funded_amount`` — a commitment
    and a cost. Neither is a mark.
  * ``spvs`` — ``target_raise`` / ``minimum_raise`` / ``hard_cap`` /
    ``min_commitment``. All fundraising parameters. There is NO NAV column.
  * ``member_investments`` — ``amount_committed`` / ``amount_funded``. Same
    shape, same problem.
  * The Sprint-22 GL — ``v_capital_accounts`` is structurally the right idea
    (``sum(credit − debit)`` over ``is_capital_account`` accounts) and is NOT
    CONNECTED. It groups by ``journal_lines.dim_member_series_id``, which has
    **no foreign key**, references a ``member_series`` table that **does not
    exist anywhere in the database**, is written only from a caller-supplied
    ``dims['member_series_id']`` in ``services/ledger/posting.py``, and is NULL
    on every deployed row. The view returns zero rows for every SPV today.
    There is no join from a subscription to a capital account balance.

So Phase D establishes the path rather than discovering it, using the one join
that already existed:

    spv_subscriptions (current)
      → spvs.id
      → portfolio.assets.internal_spv_id      (ONE asset per SPV)
      → portfolio.valuations, resolved by A2's ladder
      × spv_subscriptions.ownership_pct / 100

The ladder is A2's ``resolve_current_value`` transcribed into SQL inside the
view — latest ``valuation_date``, then ``audited > final > preliminary >
estimated > restated``, with any row a current valuation supersedes demoted
below all of them. ``verify_portfoliod.py`` asserts the view's number equals
``resolve_current_value``'s number EXACTLY rather than approximately, because
"close enough" is how a second, subtly different resolver gets shipped.

If the GL ever starts writing a real per-subscriber capital-account dimension,
THAT becomes the better source and this join should be revisited. It is
recorded in ``docs/PROJECT_STATUS.md`` for exactly that reason.

WHAT IS DELIBERATELY NOT HERE: ANY WRITE AGAINST THE VIEW
──────────────────────────────────────────────────────────────────────────────
There is no ``update_derived_position``, no ``correct_spv_position``, and there
must never be one. A correction to an SPV interest goes to
``spv_subscriptions`` — through ``routers/spv.py``, which already implements the
CLAUDE.md Rule 3 close-and-insert supersede for that table. The view is enforced
read-only in three independent places (see ``docs/portfoliod_part1.sql``):
it is not auto-updatable, its write grants are revoked, and its row ids are v5
UUIDs under a namespace of their own so an id that leaks into a write function
is refused by that function's existence check instead of matching something.

The only write in this module is :func:`ensure_spv_asset`, which creates the
tenant asset the view joins THROUGH. It writes ``portfolio.assets`` via A2's
``create_asset`` — not a second insert path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

import asyncpg

from services.portfolio_assets import (
    PERCENT,
    VALUATION_METHODS,
    PortfolioError,
    TABLE_ASSETS,
    _OrgWrite,
    _require_org,
    create_asset,
)

#: A2 names constants for ``market_price`` and ``amortized_cost`` but not for
#: the other three ``assets_valuation_chk`` members, so this one is declared
#: here and checked against A2's frozenset at import. A typo'd literal would
#: otherwise surface as a 23514 naming a constraint, at the first SPV somebody
#: created, in production.
NAV = "nav"
assert NAV in VALUATION_METHODS, f"{NAV!r} is not in assets_valuation_chk"

# ── Schema-qualified names. `portfolio` is NOT on app_service's search_path
#    (A1/A2/B/C all confirmed it); an unqualified reference raises
#    UndefinedTableError under the production role and works fine in psql. ────
TABLE_SPV_POSITIONS = "portfolio.spv_derived_positions"

# `public` IS on the search_path. Qualified anyway, for the same reason A2
# qualifies public.transaction_types: the symmetry is what keeps the rule a
# habit rather than a special case remembered case-by-case.
TABLE_SPVS = "public.spvs"
TABLE_SPV_SUBSCRIPTIONS = "public.spv_subscriptions"


# ── The projection's fixed vocabulary ───────────────────────────────────────

#: ``positions_authority_chk`` allows five values; an SPV subscription is our
#: own book, not a custodian's statement, so it is ``internal``.
SPV_AUTHORITY = "internal"

#: ``positions_source_chk`` ALREADY permitted this token before Phase D existed
#: — A2 wrote it into the constraint in anticipation. Introspected, not assumed.
SPV_SOURCE_SYSTEM = "spv_subscriptions"

#: ``assets.asset_type`` is NOT NULL with no CHECK (A2 module docstring point 3),
#: so this is a convention, not a constraint. Named here so the ensure-function
#: and every query agree on it.
SPV_ASSET_TYPE = "spv_interest"

#: Which subscriptions are a HOLDING. Lifted verbatim from
#: ``services/spv_allocation.py`` — the deployed definition of an active
#: subscription — rather than invented here. A soft-circled commitment is an
#: intention, not something you own.
ACTIVE_SUBSCRIPTION_STATUSES = ("committed", "funded")

#: The namespace the view's row ids are minted under. Must stay byte-identical
#: to the literal in ``docs/portfoliod_part1.sql`` — this constant exists so a
#: caller can compute a derived position's id without querying the view, and it
#: is worthless if the two drift. ``verify_portfoliod.py`` asserts they agree by
#: comparing this function's output to the view's own ``id`` column.
DERIVED_POSITION_NAMESPACE = UUID("0c0df483-d70c-5244-a0fb-3175651a48a9")

#: Read permission a router should require. Same one A2 recorded; not a new
#: name, because a second permission covering the same data is a second thing to
#: forget to grant.
READ_PERMISSION = "view_portfolio"
WRITE_PERMISSION = "manage_portfolio"


def derived_position_id(subscription_id: Any) -> str:
    """The view's ``id`` for a subscription, computed without touching the DB.

    ``uuid_generate_v5(ns, s.id::text)`` in SQL and ``uuid5(ns, str(sub_id))``
    in Python are the same function over the same bytes — verified against the
    deployed server, not assumed from the RFC.

    Deterministic on purpose. A derived position that got a fresh ``uuid4`` on
    every read could not be linked to a document, drilled into, or referred to
    twice, which would make the whole projection useless for anything except a
    one-shot list.
    """
    return str(uuid5(DERIVED_POSITION_NAMESPACE, str(subscription_id)))


# ── Result shape ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DerivedPosition:
    """One projected position, plus the provenance to get back to its source.

    ``market_value`` is ``None`` — never ``Decimal(0)`` — when no valuation
    qualifies, and ``value_reason`` says which case applies. Identical rule to
    A2's ``AssetValue`` and for the identical reason: a zero for "we have no
    mark" is indistinguishable from a genuine zero once it has been summed into
    a rollup, and by then the information that it was never measured is gone.
    """

    id: str
    org_id: str
    owner_entity_id: str
    asset_id: str
    as_of_date: date
    ownership_basis: str
    ownership_pct: Decimal
    cost_basis: Decimal | None
    market_value: Decimal | None
    authority: str
    source_system: str
    taxonomy_key: str | None
    currency_code: str | None
    # Provenance — the book of record, one hop away.
    subscription_id: str
    spv_id: str
    subscription_status: str
    commitment_amount: Decimal | None
    funded_amount: Decimal | None
    valuation_id: str | None
    valuation_date: date | None
    valuation_status: str | None
    spv_total_value: Decimal | None
    value_basis: str | None
    is_superseded: bool
    value_reason: str | None
    valid_from: datetime | None

    @property
    def valued(self) -> bool:
        return self.market_value is not None


def _row_to_position(row) -> DerivedPosition:
    def _dec(v):
        return None if v is None else Decimal(str(v))

    return DerivedPosition(
        id=str(row["id"]),
        org_id=str(row["org_id"]),
        owner_entity_id=str(row["owner_entity_id"]),
        asset_id=str(row["asset_id"]),
        as_of_date=row["as_of_date"],
        ownership_basis=row["ownership_basis"],
        ownership_pct=_dec(row["ownership_pct"]),
        cost_basis=_dec(row["cost_basis"]),
        market_value=_dec(row["market_value"]),
        authority=row["authority"],
        source_system=row["source_system"],
        taxonomy_key=row["taxonomy_key"],
        currency_code=row["currency_code"],
        subscription_id=str(row["subscription_id"]),
        spv_id=str(row["spv_id"]),
        subscription_status=row["subscription_status"],
        commitment_amount=_dec(row["commitment_amount"]),
        funded_amount=_dec(row["funded_amount"]),
        valuation_id=str(row["valuation_id"]) if row["valuation_id"] else None,
        valuation_date=row["valuation_date"],
        valuation_status=row["valuation_status"],
        spv_total_value=_dec(row["spv_total_value"]),
        value_basis=row["value_basis"],
        is_superseded=bool(row["is_superseded"]),
        value_reason=row["value_reason"],
        valid_from=row["valid_from"],
    )


# ── The one write: the asset the view joins THROUGH ─────────────────────────


async def ensure_spv_asset(conn, *, org_id: str, spv_id: str) -> str:
    """Find-or-create the tenant asset standing for one SPV. Returns its id.

    ONE asset per SPV, never one per subscription. Every subscriber holds a
    percentage of the SAME thing, and an asset per subscription would give the
    SPV as many marks as it has members, each of which could disagree.

    Idempotent in two layers, because the Python check alone is not:

      * a ``SELECT`` first, which is the normal path; and
      * ``assets_internal_spv_active_uniq`` behind it, so two concurrent callers
        that both miss the SELECT do not both insert. The loser catches the
        unique violation and re-reads the winner's row.

    The insert goes through A2's :func:`create_asset` — the SAME function every
    other asset type uses, with its vocabulary checks and its ``_OrgWrite`` RLS
    context. There is deliberately no bare ``INSERT INTO portfolio.assets``
    here: a second insert path is a second place for the org context to be
    forgotten.

    ``valuation_method='nav'`` because an SPV is marked by a stated NAV, not by
    a listed price. That choice is load-bearing beyond documentation — A2's
    ``record_transaction`` derives an asset's market from ``valuation_method``,
    and ``nav`` is what makes ``call_investment`` / ``dist_roc`` (private-markets
    types) legal against this asset and ``buy`` / ``dividend`` illegal.
    """
    org_id = _require_org(org_id)
    if not spv_id:
        raise PortfolioError("spv_id is required")
    spv_id = str(spv_id)

    async with _OrgWrite(conn, org_id) as c:
        existing = await c.fetchval(
            f"""
            SELECT id::text FROM {TABLE_ASSETS}
            WHERE org_id = $1::uuid AND internal_spv_id = $2::uuid
              AND valid_to IS NULL AND system_to IS NULL
            """,
            org_id, spv_id,
        )
        if existing:
            return existing

        spv = await c.fetchrow(
            f"SELECT name, currency, vehicle_entity_id::text AS vehicle_entity_id "
            f"FROM {TABLE_SPVS} WHERE id = $1::uuid AND org_id = $2::uuid",
            spv_id, org_id,
        )
        if spv is None:
            # Reported as "in this org", not "does not exist": under RLS a
            # foreign SPV is invisible to this lookup, and saying it exists
            # elsewhere would leak the other tenant's row through the error.
            raise PortfolioError(f"spv {spv_id} does not exist in this org")

    try:
        return await create_asset(
            conn,
            org_id=org_id,
            name=spv["name"],
            asset_type=SPV_ASSET_TYPE,
            asset_class="financial",
            ownership_basis=PERCENT,
            valuation_method=NAV,
            currency_code=spv["currency"],
            internal_spv_id=spv_id,
            # The SPV's own legal vehicle, when one has been created. NULL is
            # the normal state for a forming SPV and is not an error.
            issuer_entity_id=spv["vehicle_entity_id"],
        )
    except asyncpg.exceptions.UniqueViolationError:
        # Lost the race against a concurrent caller. The winner's row is the
        # answer — re-read rather than raise, which is what "idempotent" has to
        # mean under concurrency.
        async with _OrgWrite(conn, org_id) as c:
            won = await c.fetchval(
                f"""
                SELECT id::text FROM {TABLE_ASSETS}
                WHERE org_id = $1::uuid AND internal_spv_id = $2::uuid
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                org_id, spv_id,
            )
        if won:
            return won
        raise


async def ensure_spv_assets_for_org(conn, *, org_id: str) -> dict[str, str]:
    """:func:`ensure_spv_asset` for every SPV in the org. ``{spv_id: asset_id}``.

    The backfill an org runs once so its existing SPVs start projecting. Safe to
    re-run: every call is find-or-create.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        spv_ids = [
            str(r["id"])
            for r in await c.fetch(
                f"SELECT id FROM {TABLE_SPVS} WHERE org_id = $1::uuid ORDER BY created_at",
                org_id,
            )
        ]
    return {sid: await ensure_spv_asset(conn, org_id=org_id, spv_id=sid) for sid in spv_ids}


# ── Reads against the view. There are no other kinds of call in here. ───────


async def list_derived_positions(
    conn,
    *,
    org_id: str,
    spv_id: str | None = None,
    owner_entity_id: str | None = None,
) -> list[DerivedPosition]:
    """Current SPV subscriptions, in position shape.

    Reads the view, which carries ``security_invoker = true`` — so the org
    isolation enforced here is the DATABASE's, evaluated against the querying
    role, not a Python ``WHERE``. The explicit ``org_id`` predicate below is
    belt-and-braces and is not what makes this safe.
    """
    org_id = _require_org(org_id)
    conditions = ["v.org_id = $1::uuid"]
    params: list[Any] = [org_id]
    if spv_id:
        params.append(str(spv_id))
        conditions.append(f"v.spv_id = ${len(params)}::uuid")
    if owner_entity_id:
        params.append(str(owner_entity_id))
        conditions.append(f"v.owner_entity_id = ${len(params)}::uuid")

    async with _OrgWrite(conn, org_id) as c:
        rows = await c.fetch(
            f"SELECT * FROM {TABLE_SPV_POSITIONS} v "
            f"WHERE {' AND '.join(conditions)} "
            f"ORDER BY v.as_of_date DESC, v.subscription_id",
            *params,
        )
    return [_row_to_position(r) for r in rows]


async def get_derived_position(
    conn, *, org_id: str, subscription_id: str
) -> DerivedPosition | None:
    """One subscription's projection, or ``None`` if it does not currently
    project.

    ``None`` covers four genuinely different situations — the subscription is
    superseded (``valid_to`` set), is still soft-circled, has no post-close
    ``ownership_pct``, or its SPV has no asset. :func:`unprojected_subscriptions`
    is what tells them apart; this function deliberately does not guess.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        row = await c.fetchrow(
            f"SELECT * FROM {TABLE_SPV_POSITIONS} v "
            f"WHERE v.org_id = $1::uuid AND v.subscription_id = $2::uuid",
            org_id, str(subscription_id),
        )
    return _row_to_position(row) if row else None


async def unprojected_subscriptions(conn, *, org_id: str) -> list[dict]:
    """Current subscriptions that do NOT project, and why.

    The view INNER-joins the SPV's asset because ``asset_id`` is NOT NULL in the
    shape it projects into. That is correct, and it means a subscription can
    drop out of the portfolio without anything raising — the one failure mode of
    a derived view that a stored table does not have. This function is the
    answer to that: it makes the omission visible and names its cause, so
    "the member's SPV is missing from their portfolio" is a diagnosable
    condition rather than a mystery.

    Ordered most-recent first. Reasons, in the order they are checked:

      ``superseded``       ``valid_to`` is set — the row is history.
      ``status_not_active``  soft / withdrawn / whatever else; not a holding.
      ``no_ownership_pct``  no post-close percentage yet. The most common one,
                            and the reason both currently-deployed rows do not
                            project.
      ``no_spv_asset``      the SPV has no ``portfolio.assets`` row.
                            :func:`ensure_spv_asset` fixes exactly this.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        rows = await c.fetch(
            f"""
            SELECT s.id::text        AS subscription_id,
                   s.spv_id::text    AS spv_id,
                   s.entity_id::text AS entity_id,
                   s.subscription_status,
                   s.ownership_pct,
                   s.valid_to,
                   CASE
                       WHEN s.valid_to IS NOT NULL THEN 'superseded'
                       WHEN s.subscription_status <> ALL($2::text[])
                            THEN 'status_not_active'
                       WHEN s.ownership_pct IS NULL THEN 'no_ownership_pct'
                       ELSE 'no_spv_asset'
                   END AS reason
            FROM {TABLE_SPV_SUBSCRIPTIONS} s
            WHERE s.org_id = $1::uuid
              AND NOT EXISTS (
                  SELECT 1 FROM {TABLE_SPV_POSITIONS} v
                  WHERE v.subscription_id = s.id
              )
            ORDER BY s.valid_from DESC, s.id
            """,
            org_id, list(ACTIVE_SUBSCRIPTION_STATUSES),
        )
    return [
        {
            "subscription_id": r["subscription_id"],
            "spv_id": r["spv_id"],
            "entity_id": r["entity_id"],
            "subscription_status": r["subscription_status"],
            "ownership_pct": (
                None if r["ownership_pct"] is None else Decimal(str(r["ownership_pct"]))
            ),
            "reason": r["reason"],
        }
        for r in rows
    ]
