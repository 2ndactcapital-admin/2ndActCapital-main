"""Portfolio rollup — Phase C, design V6 §14.

Turns ``portfolio.positions`` into ``public.entity_holdings`` buckets: one row
per ``(entity_id, taxonomy_key)`` for a given ``as_of_date``, written under
``source = 'portfolio'``.

WHY THIS FILE EXISTS AT ALL
──────────────────────────────────────────────────────────────────────────────
``services.allocation_lens`` — the Sprint 21 sunburst — reads ``entity_holdings``
and nothing else. That table has had no writer since S21 shipped, so the
sunburst has been rendering an empty tree against real targets for its entire
life. This is the writer. Nothing in ``allocation_lens`` changes; the grain
below was read off its actual query rather than guessed at.

FOUR THINGS THIS MODULE IS DELIBERATE ABOUT
──────────────────────────────────────────────────────────────────────────────

1. **Look-through, not direct ownership.** A position's value is attributed to
   the direct owner AND to every entity above it in the ownership graph, each at
   its own compounded percentage. An individual who holds everything through a
   trust owns nothing at all by ``owner_entity_id``, and a rollup keyed to that
   column would render them an empty sunburst that is not wrong so much as
   meaningless. The percentages come from ``entity_graph.get_lookthrough`` —
   the same BFS the Ownership Tree Graph and ``resolve_entity_set`` use. This
   module does NOT compute ownership percentages; it inverts the direction the
   existing engine is called in and multiplies.

2. **Callable, not trigger-fired.** Positions arrive in batches: a file import
   writes hundreds of rows one at a time, and an eventual Altruist sync will do
   the same. A row-level trigger would rebuild the buckets after every single
   one of those writes, and every intermediate state is a real, readable,
   WRONG number — a member refreshing mid-import sees a portfolio that is
   missing half its holdings. Rolling up is a thing you do when a batch is
   *finished*, which is a fact only the caller knows. So: an explicit call,
   made by the importer at the end of its run and available on a schedule.

3. **The write is an UPSERT on the real constraint, and it also DELETES.**
   ``entity_holdings_bucket_key`` on
   ``(org_id, entity_id, taxonomy_key, as_of_date, source)`` is what makes a
   re-run update rather than duplicate. But an upsert alone is not idempotent
   in the way that matters: if a position is superseded or removed between two
   runs, its bucket is no longer in the computed set, the upsert never touches
   it, and a stale figure survives forever under the current date. So every run
   also deletes the ``source = 'portfolio'`` rows for that ``(org, as_of_date)``
   that the new computation did not produce. Scoped to this source — a manual
   or imported holding row from another track is never touched.

4. **A position with no mark is skipped and COUNTED, never zeroed.** Same rule
   ``resolve_current_value`` already enforces: a zero for "we have no valuation"
   is indistinguishable from a genuine zero the instant it is summed, and the
   fact that it was never measured is gone. The result object reports how many
   positions were dropped for want of a value and for want of a taxonomy key,
   so "the sunburst looks light" has an answer.

A KNOWN INTERACTION WITH ``allocation_lens``, REPORTED NOT PAPERED OVER
──────────────────────────────────────────────────────────────────────────────
``aggregate_allocation`` accepts two selector shapes and weights them
differently:

  * ``{"type": "entity", "id": E}`` — E alone, at weight 1.0. With look-through
    buckets this is exactly right: E's row already carries its compounded share
    of everything held beneath it.
  * ``{"type": "subtree", "root_id": R}`` — R at 1.0 PLUS every descendant at
    its ``effective_pct``. Against look-through buckets that double counts: R's
    own row already contains the descendants' compounded value, and the lens
    then adds a weighted copy of each descendant's row on top.

Phase C's mandate is the look-through bucket, and ``allocation_lens`` is
explicitly out of scope for this sprint, so the buckets are written as
specified and the interaction is recorded here and in ``docs/PROJECT_STATUS.md``
rather than silently absorbed. The fix belongs in the lens (a ``subtree``
selector should stop re-weighting descendants once holdings are themselves
look-through) and is a one-line change there, not a compromise here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from services.entity_graph import get_lookthrough
from services.portfolio_assets import (
    PERCENT,
    TABLE_ASSETS,
    TABLE_POSITIONS,
    PortfolioError,
    _OrgWrite,
    _current,
    _require_org,
    resolve_current_value,
)

# `public` is on the search_path and `portfolio` is NOT (confirmed in A1, A2 and
# Phase B). Qualified anyway, on both, for the same reason portfolio_assets
# qualifies its public tables: a reader should not have to know which schema
# each name happens to resolve in.
TABLE_HOLDINGS = "public.entity_holdings"
TABLE_ENTITY_RELS = "public.entity_relationships"

# The `source` value this module owns. Every row it writes carries it, and it is
# the ONLY source value it will ever delete. A hand-entered holding under
# 'manual' and an S21-era row under any other source are another track's data.
ROLLUP_SOURCE = "portfolio"

# `positions.market_value` is the base-currency amount — `market_value_native`
# plus `fx_rate_id` is where a native figure lives (Phase B). `entity_holdings`
# carries one currency per row and `allocation_lens` sums `market_value` without
# looking at it, so a rollup that emitted mixed currencies would be adding
# euros to dollars silently. One base currency, stated.
BASE_CURRENCY = "USD"

# Money is stored to the cent. Quantized once, at the point of write, after all
# fractional-ownership multiplication has happened at full Decimal precision —
# rounding each attribution step would drift a large tree by real money.
_CENTS = Decimal("0.01")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")

# The permission a router must require before calling `rollup_entity_holdings`.
# Reuses Phase B's — a rollup is a portfolio write, and inventing a second
# permission name for it would mean an org that granted `manage_portfolio`
# still could not refresh its own numbers.
ROLLUP_PERMISSION = "manage_portfolio"


class RollupError(PortfolioError):
    """A rollup was refused for a reason the caller can fix."""


@dataclass(frozen=True)
class SkippedPosition:
    """One position that produced no bucket, and why."""

    position_id: str
    owner_entity_id: str
    asset_id: str
    reason: str


@dataclass
class RollupResult:
    """What a rollup did. Every drop is counted; nothing fails silently."""

    org_id: str
    as_of_date: date
    positions_considered: int = 0
    positions_valued: int = 0
    buckets_written: int = 0
    buckets_removed: int = 0
    entities_covered: int = 0
    total_value: Decimal = _ZERO
    skipped: list[SkippedPosition] = field(default_factory=list)

    @property
    def positions_skipped(self) -> int:
        return len(self.skipped)

    def as_dict(self) -> dict:
        return {
            "org_id": self.org_id,
            "as_of_date": self.as_of_date.isoformat(),
            "positions_considered": self.positions_considered,
            "positions_valued": self.positions_valued,
            "positions_skipped": self.positions_skipped,
            "buckets_written": self.buckets_written,
            "buckets_removed": self.buckets_removed,
            "entities_covered": self.entities_covered,
            "total_value": str(self.total_value),
            "currency_code": BASE_CURRENCY,
            "source": ROLLUP_SOURCE,
            "skipped": [
                {
                    "position_id": s.position_id,
                    "owner_entity_id": s.owner_entity_id,
                    "asset_id": s.asset_id,
                    "reason": s.reason,
                }
                for s in self.skipped
            ],
        }


class _ConnAsPool:
    """Present one live connection with the ``pool.acquire()`` interface.

    ``entity_graph`` takes a pool because it is called from routers that have
    one. This module takes a ``conn`` because it is called from inside somebody
    else's transaction — an importer that has just written a batch and has not
    committed it. Handing ``get_lookthrough`` a real pool would give it a
    DIFFERENT connection, outside that transaction, which would not see the
    ownership edges the caller just wrote and, worse, would not carry the
    ``app.current_org_id`` GUC this module raised: every RLS-protected read in
    the BFS would come back empty and the rollup would quietly attribute
    nothing to anybody.

    So the graph engine is reused verbatim, on this connection, in this
    transaction. No second ownership calculation exists.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Ctx()


def _money(value) -> Decimal:
    """numeric → Decimal without ever passing through float."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


async def _current_positions(conn, org_id: str, as_of_date: date) -> list:
    """The positions that ARE the portfolio on ``as_of_date``.

    Three filters, each load-bearing:

    * ``valid_to IS NULL AND system_to IS NULL`` — the row is current on both
      temporal axes (CLAUDE.md Rule 3's read side).
    * ``superseded_by_source IS NULL`` — Phase B's precedence resolution left
      the LOSING rows in place, current and queryable, annotated with the
      source that beat them. They are not history and the temporal predicate
      does not exclude them; counting them would double every holding that
      arrived from two sources. Only the winner is the portfolio's answer.
    * ``DISTINCT ON (owner_entity_id, asset_id)`` with the latest
      ``as_of_date <= $2`` — a holding restated on a later date supersedes the
      earlier statement of the same holding. Without this, a position stated at
      Q1 and restated at Q2 would both be summed into a Q2 rollup.
    """
    return await conn.fetch(
        f"""
        SELECT DISTINCT ON (p.owner_entity_id, p.asset_id)
               p.id::text               AS position_id,
               p.owner_entity_id::text  AS owner_entity_id,
               p.asset_id::text         AS asset_id,
               p.as_of_date,
               p.ownership_basis,
               p.quantity,
               p.ownership_pct,
               p.market_value,
               p.source_system,
               COALESCE(p.taxonomy_key, a.default_taxonomy_key) AS taxonomy_key
        FROM {TABLE_POSITIONS} p
        JOIN {TABLE_ASSETS} a
          ON a.id = p.asset_id
         AND a.org_id = p.org_id
         AND {_current('a')}
        WHERE p.org_id = $1::uuid
          AND p.as_of_date <= $2::date
          AND p.superseded_by_source IS NULL
          AND {_current('p')}
        ORDER BY p.owner_entity_id, p.asset_id, p.as_of_date DESC,
                 p.system_from DESC
        """,
        org_id,
        as_of_date,
    )


async def _position_value(conn, org_id: str, row, as_of_date: date):
    """The base-currency value of one position, or ``(None, reason)``.

    ``ownership_basis`` selects which measure is AUTHORITATIVE (A2's contract,
    enforced by ``portfolio_assets._validate_basis``), and this is the reader
    that has to honour it:

    * ``percent`` — the position IS a fraction of an asset, and
      ``ownership_pct`` is the fact. Its value is that fraction of the asset's
      own resolved valuation, NOT the stored ``market_value``: on a percent
      position ``market_value`` is a convenience copy that a revaluation of the
      underlying asset does not update, so trusting it would freeze an LLC
      interest at whatever it was worth the day somebody typed it in. The
      stored figure is used only when no valuation resolves at all, which is
      better than dropping the holding entirely.
    * ``units`` — ``market_value`` when present, otherwise
      ``quantity × per-unit valuation`` via ``resolve_current_value``, which
      already knows how to refuse a per-unit mark with no quantity to multiply.
    * ``value`` — ``market_value`` is the fact and there is nothing to derive
      it from.
    """
    basis = row["ownership_basis"]
    stored_mv = None if row["market_value"] is None else _money(row["market_value"])

    if basis == PERCENT:
        pct = row["ownership_pct"]
        if pct is None:
            # Unreachable through create_position (_validate_basis requires it),
            # reachable through a direct INSERT. Named rather than assumed away.
            return None, "ownership_basis='percent' with a NULL ownership_pct"
        asset_value = await resolve_current_value(
            conn, org_id=org_id, asset_id=row["asset_id"], as_of=as_of_date
        )
        if asset_value.found:
            return (_money(pct) / _HUNDRED) * asset_value.value, None
        if stored_mv is not None:
            return stored_mv, None
        return None, (
            f"ownership_basis='percent' and no valuation resolved for asset "
            f"{row['asset_id']}: {asset_value.reason}"
        )

    if stored_mv is not None:
        return stored_mv, None

    resolved = await resolve_current_value(
        conn,
        org_id=org_id,
        asset_id=row["asset_id"],
        as_of=as_of_date,
        quantity=row["quantity"],
    )
    if resolved.found:
        return resolved.value, None
    return None, f"no market_value and none derivable: {resolved.reason}"


async def _ownership_weights(conn, org_id: str, owner_ids: set[str]) -> dict:
    """``{ancestor_entity_id: {owned_entity_id: compounded_fraction}}``.

    Built by calling ``entity_graph.get_lookthrough`` — the REAL engine, the one
    ``resolve_entity_set`` and the Ownership Tree Graph use — once per entity
    that owns anything, and keeping the descendants that actually hold a
    position. ``get_lookthrough`` already returns ``effective_pct`` as a
    compounded 0–1 fraction across the whole chain: 50% of a trust that owns 60%
    of an LLC comes back as ``0.300000`` for the LLC, which is precisely the
    number Task 3 needs and precisely the number this module refuses to
    recompute.

    Candidate ancestors are the entities that appear on the FROM side of a
    current ownership edge — an entity that owns nothing cannot be above
    anything, and running a BFS from every entity in the org to discover that
    would be quadratic for no information.

    ``ownership_pct IS NOT NULL`` matches ``get_lookthrough``'s own traversal
    predicate exactly. An ownership edge with no percentage is not a fractional
    claim the engine can compound, and seeding a BFS from an owner whose only
    edges the BFS itself ignores would just cost a round trip.
    """
    if not owner_ids:
        return {}

    candidates = await conn.fetch(
        f"""
        SELECT DISTINCT er.from_entity_id::text AS entity_id
        FROM {TABLE_ENTITY_RELS} er
        WHERE er.org_id = $1::uuid
          AND er.relationship_type = 'ownership'
          AND er.ownership_pct IS NOT NULL
          AND er.valid_to IS NULL
          AND er.system_to IS NULL
        """,
        org_id,
    )

    pool = _ConnAsPool(conn)
    weights: dict[str, dict[str, Decimal]] = {}
    for cand in candidates:
        ancestor = cand["entity_id"]
        try:
            descendants = await get_lookthrough(pool, org_id, ancestor)
        except ValueError:
            # get_lookthrough raises when the root entity row is not visible.
            # An ownership edge pointing at an entity this connection cannot
            # see is a data or RLS-context problem, not a rollup failure — the
            # ancestor simply contributes nothing.
            continue
        held = {
            d["entity_id"]: Decimal(d["effective_pct"])
            for d in descendants
            if d["entity_id"] in owner_ids
        }
        if held:
            weights[ancestor] = held
    return weights


async def rollup_entity_holdings(
    conn,
    *,
    org_id: str,
    as_of_date: date,
) -> RollupResult:
    """Rebuild ``entity_holdings`` for one org and one date. Returns what it did.

    ``org_id`` comes from the caller's JWT claims, never from a request body —
    ``_require_org`` is the same guard every A2/Phase-B write uses.

    The whole rollup runs inside ONE ``_OrgWrite``: read the positions, walk the
    graph, delete the stale buckets and upsert the new ones, commit or roll back
    together. A rollup that committed its deletes and then failed its inserts
    would leave the sunburst empty, which is the one state worse than stale.
    """
    org_id = _require_org(org_id)
    if not isinstance(as_of_date, date):
        raise RollupError(
            f"as_of_date must be a datetime.date — got {type(as_of_date).__name__}"
        )

    result = RollupResult(org_id=org_id, as_of_date=as_of_date)

    async with _OrgWrite(conn, org_id) as c:
        positions = await _current_positions(c, org_id, as_of_date)
        result.positions_considered = len(positions)

        # ── Value every position, dropping (and naming) the ones that cannot
        #    be valued or classified. ─────────────────────────────────────────
        valued: list[tuple[str, str, Decimal]] = []  # (owner, taxonomy_key, value)
        owner_ids: set[str] = set()
        for row in positions:
            taxonomy_key = row["taxonomy_key"]
            if not taxonomy_key:
                result.skipped.append(SkippedPosition(
                    position_id=row["position_id"],
                    owner_entity_id=row["owner_entity_id"],
                    asset_id=row["asset_id"],
                    reason=(
                        "no taxonomy_key on the position and no "
                        "default_taxonomy_key on the asset — there is no bucket "
                        "to put it in, and inventing one would misreport it"
                    ),
                ))
                continue

            value, reason = await _position_value(c, org_id, row, as_of_date)
            if value is None:
                result.skipped.append(SkippedPosition(
                    position_id=row["position_id"],
                    owner_entity_id=row["owner_entity_id"],
                    asset_id=row["asset_id"],
                    reason=reason or "unvalued",
                ))
                continue

            valued.append((row["owner_entity_id"], taxonomy_key, value))
            owner_ids.add(row["owner_entity_id"])

        result.positions_valued = len(valued)

        # ── Attribute each value to the direct owner AND to every ancestor,
        #    at the graph's own compounded percentage. ─────────────────────────
        weights = await _ownership_weights(c, org_id, owner_ids)
        buckets: dict[tuple[str, str], Decimal] = {}

        for owner, taxonomy_key, value in valued:
            # The direct owner holds the whole thing. Weight 1, not a lookup:
            # an entity does not appear in its own look-through.
            key = (owner, taxonomy_key)
            buckets[key] = buckets.get(key, _ZERO) + value

        for ancestor, held in weights.items():
            for owner, taxonomy_key, value in valued:
                fraction = held.get(owner)
                if fraction is None:
                    continue
                key = (ancestor, taxonomy_key)
                buckets[key] = buckets.get(key, _ZERO) + (value * fraction)

        rows = [
            (entity_id, taxonomy_key,
             amount.quantize(_CENTS, rounding=ROUND_HALF_UP))
            for (entity_id, taxonomy_key), amount in sorted(buckets.items())
        ]

        # ── Remove the buckets this run did NOT produce, then upsert the ones
        #    it did. Deletion first so a bucket that moved taxonomy keys does
        #    not briefly exist under both. ──────────────────────────────────────
        deleted = await c.fetch(
            f"""
            DELETE FROM {TABLE_HOLDINGS} h
            WHERE h.org_id = $1::uuid
              AND h.as_of_date = $2::date
              AND h.source = $3
              AND NOT EXISTS (
                  SELECT 1
                  FROM unnest($4::uuid[], $5::text[]) AS keep(entity_id, taxonomy_key)
                  WHERE keep.entity_id = h.entity_id
                    AND keep.taxonomy_key = h.taxonomy_key
              )
            RETURNING h.id
            """,
            org_id,
            as_of_date,
            ROLLUP_SOURCE,
            [r[0] for r in rows],
            [r[1] for r in rows],
        )
        result.buckets_removed = len(deleted)

        for entity_id, taxonomy_key, amount in rows:
            # ON CONFLICT infers `entity_holdings_bucket_key` from its exact
            # column list — the constraint Phase C's Part 1 SQL added, and the
            # only reason a second run updates instead of raising 23505.
            await c.execute(
                f"""
                INSERT INTO {TABLE_HOLDINGS}
                    (org_id, entity_id, taxonomy_key, market_value,
                     currency_code, as_of_date, source)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::date, $7)
                ON CONFLICT (org_id, entity_id, taxonomy_key, as_of_date, source)
                DO UPDATE SET market_value  = EXCLUDED.market_value,
                              currency_code = EXCLUDED.currency_code,
                              updated_at    = now()
                """,
                org_id, entity_id, taxonomy_key, amount,
                BASE_CURRENCY, as_of_date, ROLLUP_SOURCE,
            )

        result.buckets_written = len(rows)
        result.entities_covered = len({r[0] for r in rows})
        result.total_value = sum((r[2] for r in rows), _ZERO)

    return result
