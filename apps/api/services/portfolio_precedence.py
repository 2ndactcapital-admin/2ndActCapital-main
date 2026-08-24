"""Source precedence — Portfolio Phase B, design V6 §1.1.

The same holding arrives from more than one place. A reporting-tool export, a
custodial feed and somebody's manual entry can all describe the same
``(owner_entity_id, asset_id, as_of_date)`` and disagree about the number. This
module answers one question: **which of them is the portfolio's answer**, and
records that answer on the rows that lost.

THREE THINGS THIS MODULE IS DELIBERATE ABOUT
──────────────────────────────────────────────────────────────────────────────

1. **The ordering is DATA, not code.** It lives in ``org_settings`` under
   ``portfolio.precedence.source_order`` (Task 1c's real convention: dotted
   namespace, JSON value, category from prefix, defaulted in
   ``DEFAULT_SETTINGS``). A firm that trusts its custodian over its reporting
   tool changes a setting; nobody deploys. The default order is the design's
   stated one and is what an unconfigured org gets — which is every org today,
   so the default is not a fallback nobody exercises, it is the live path.

2. **Losers are annotated, never deleted.** ``superseded_by_source`` is set on
   the losing row and the row stays exactly where it was, current and
   queryable. The design wants those rows for reconciliation: "Addepar says
   4,200 units and Altruist says 4,150" is the finding, and deleting the loser
   deletes the finding. A reconciliation screen that can only see the winner
   has nothing to reconcile.

3. **Why this is an UPDATE, and why that is not a CLAUDE.md Rule 3 violation.**
   Rule 3 closes a row because its content stopped being true. Nothing here
   stopped being true: the losing row remains, permanently, what that source
   said on that date. What ``superseded_by_source`` records is not a change to
   the fact — it is the RESOLUTION over a set of facts, and it is the only
   column on the row that this module ever writes.

   The Rule-3 shape was considered and is actively wrong here, in two separate
   ways. Closing the loser with ``valid_to`` would drop it out of every
   current-row query in the codebase, which defeats point 2 above — the row
   would be "kept" in exactly the sense that makes it unfindable. Doing it as a
   system-time correction instead (close ``system_to``, re-insert a corrected
   copy) mints a NEW ``id``, and ``portfolio.transactions.position_id`` is an FK
   onto that id: every transaction booked against the position would be left
   pointing at the corrected-away row. So: a narrow, idempotent, single-column
   UPDATE, and the verification asserts every other column of the losing row is
   byte-identical afterwards.

WHAT PRECEDENCE IS NOT
──────────────────────────────────────────────────────────────────────────────
It does not pick between two rows from the SAME source — that is a restatement,
and the later row simply is the answer. It does not merge, average or reconcile
values. It does not look at the numbers at all: a source is trusted or it is
not, and a rule that preferred "whichever number looks more reasonable" would
be unauditable by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from services.org_settings import DEFAULT_SETTINGS, get_setting_with_origin
from services.portfolio_assets import (
    SOURCE_SYSTEMS,
    TABLE_POSITIONS,
    PortfolioError,
    _OrgWrite,
    _current,
    _require_org,
)

# ── The setting ─────────────────────────────────────────────────────────────

#: The org_settings key. Namespaced `portfolio.` so `category_for` files it
#: under a `portfolio` category rather than the catch-all `general`.
PRECEDENCE_SETTING_KEY = "portfolio.precedence.source_order"

#: Design V6 §1.1's stated order, most-trusted first — read from
#: ``DEFAULT_SETTINGS`` rather than re-declared. org_settings' own docstring
#: makes it the one place allowed to hold default DATA, and a precedence order
#: written out twice is an order that drifts: the copy an unconfigured org
#: resolves under and the copy the settings screen displays would diverge with
#: nothing failing.
DEFAULT_SOURCE_ORDER: tuple[str, ...] = tuple(
    DEFAULT_SETTINGS["portfolio.precedence.source_order"]
)

# Every default entry must be a source_system the positions CHECK admits, or the
# default order silently ranks a value no row can ever carry. Asserted at import
# rather than left to a test, because the failure is invisible at runtime: an
# unknown token just never matches anything.
assert set(DEFAULT_SOURCE_ORDER) <= SOURCE_SYSTEMS, (
    "DEFAULT_SOURCE_ORDER contains a source_system that positions_source_chk "
    f"does not admit: {sorted(set(DEFAULT_SOURCE_ORDER) - SOURCE_SYSTEMS)}"
)


class PrecedenceError(PortfolioError):
    """A precedence resolution was refused for a reason the caller can fix."""


class PrecedenceConfigError(PortfolioError):
    """The org's configured precedence order is not usable.

    Raised by :func:`validate_source_order`, which ``org_settings`` calls at
    WRITE time. Rejecting a bad order when it is saved is the whole point — a
    misconfigured order that is only noticed at resolve time has by then been
    silently mis-ranking every ingestion run since it was saved.
    """


def validate_source_order(value: Any) -> tuple[str, ...]:
    """Validate a candidate precedence order and return it normalised.

    Requirements, each because of a specific way the alternative fails:

    * a non-empty list of strings — a scalar or an empty list means "no order",
      which is not the same as the default order and would rank everything
      equally;
    * every entry a real ``source_system`` — a typo'd or aspirational token
      ranks nothing, so the source it was meant to promote quietly stays where
      the tail rule puts it;
    * no duplicates — a duplicate makes the order ambiguous about which
      position the source actually occupies.

    It does NOT require the list to be exhaustive. An org may name only the
    sources it cares about; anything unnamed is ranked after everything named
    (see :func:`_rank`), which is the honest reading of "not configured".
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise PrecedenceConfigError(
            f"{PRECEDENCE_SETTING_KEY} must be a JSON array of source_system "
            f"strings, most-trusted first — got {type(value).__name__}"
        )
    order = list(value)
    if not order:
        raise PrecedenceConfigError(
            f"{PRECEDENCE_SETTING_KEY} may not be an empty array. To use the "
            f"platform default, clear the setting instead of saving [] — an "
            f"empty order ranks every source equally, which is not the same "
            f"thing."
        )
    if not all(isinstance(s, str) for s in order):
        raise PrecedenceConfigError(
            f"{PRECEDENCE_SETTING_KEY} entries must all be strings"
        )
    cleaned = [s.strip() for s in order]
    unknown = [s for s in cleaned if s not in SOURCE_SYSTEMS]
    if unknown:
        raise PrecedenceConfigError(
            f"{PRECEDENCE_SETTING_KEY} names source_system value(s) that no "
            f"position can carry: {unknown}. Allowed: {sorted(SOURCE_SYSTEMS)}"
        )
    dupes = sorted({s for s in cleaned if cleaned.count(s) > 1})
    if dupes:
        raise PrecedenceConfigError(
            f"{PRECEDENCE_SETTING_KEY} lists {dupes} more than once; an order "
            f"must place each source exactly once"
        )
    return tuple(cleaned)


@dataclass(frozen=True)
class SourceOrder:
    """An org's resolved precedence order, and whether it is their own.

    ``is_default`` is carried rather than inferred by comparing to
    ``DEFAULT_SOURCE_ORDER``: an org that explicitly saves an order identical to
    the default has still configured it, and a reconciliation screen that says
    "using the platform default" about a deliberate choice is lying about where
    the number came from.
    """

    order: tuple[str, ...]
    is_default: bool
    invalid_reason: str | None = None

    def rank(self, source_system: str) -> int:
        return _rank(self.order, source_system)


def _rank(order: Sequence[str], source_system: str) -> int:
    """Position in the order; anything unnamed ranks after everything named.

    A source the order does not mention is not an error — see
    :func:`validate_source_order`. It ranks last, which is the conservative
    reading: an unrecognised feed does not get to overrule a configured one.
    """
    try:
        return order.index(source_system)
    except ValueError:
        return len(order)


async def get_source_order(conn, org_id: str) -> SourceOrder:
    """The org's precedence order, falling back to the platform default.

    A stored order that no longer validates (a source_system retired out of the
    CHECK constraint after the org saved it, say) does NOT raise here. Resolution
    is on the ingestion path, and failing an entire import because a settings row
    went stale would turn a configuration problem into a data-loss problem. It
    falls back to the default and reports ``invalid_reason`` so the caller can
    surface it — silently, never.
    """
    org_id = _require_org(org_id)
    raw, is_default = await get_setting_with_origin(
        conn, org_id, PRECEDENCE_SETTING_KEY
    )
    if is_default or raw is None:
        return SourceOrder(DEFAULT_SOURCE_ORDER, is_default=True)
    try:
        return SourceOrder(validate_source_order(raw), is_default=False)
    except PrecedenceConfigError as exc:
        return SourceOrder(
            DEFAULT_SOURCE_ORDER, is_default=True, invalid_reason=str(exc)
        )


# ── Resolution ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PrecedenceCandidate:
    """One position competing to be the answer for a holding key."""

    position_id: str
    source_system: str
    rank: int
    superseded_by_source: str | None


@dataclass(frozen=True)
class PrecedenceOutcome:
    """Which position won, which lost, and under what order."""

    owner_entity_id: str
    asset_id: str
    as_of_date: date
    winner_position_id: str
    winner_source_system: str
    losers: tuple[PrecedenceCandidate, ...] = ()
    order: tuple[str, ...] = ()
    order_is_default: bool = True
    order_invalid_reason: str | None = None
    rows_marked: int = 0
    rows_cleared: int = 0

    @property
    def loser_position_ids(self) -> tuple[str, ...]:
        return tuple(c.position_id for c in self.losers)


# The columns resolution reads. `system_from` and `id` are the tie-break, in
# that order: two rows from the same source are a restatement and the later one
# is the answer; `id` only breaks a tie between rows written in the same
# transaction, and exists so the result is deterministic rather than
# whatever-the-planner-returned.
_CANDIDATE_COLS = (
    "id::text AS id, owner_entity_id::text AS owner_entity_id, "
    "asset_id::text AS asset_id, as_of_date, source_system, "
    "superseded_by_source, system_from"
)


def _sort_key(row: Mapping[str, Any], order: Sequence[str]) -> tuple:
    """Trust rank only.

    Recency is NOT in this key on purpose. The rows are fetched newest-first and
    Python's sort is stable, so recency survives as the within-source tie-break
    without needing a descending component in an otherwise-ascending key.
    """
    return (_rank(order, row["source_system"]),)


async def resolve_precedence(
    conn,
    org_id: str,
    position_candidates: Iterable[Any],
    *,
    apply: bool = True,
) -> PrecedenceOutcome:
    """Decide which of several positions for one holding key is the answer.

    ``position_candidates`` is an iterable of position ids — plain strings,
    ``UUID``s, or anything with an ``id`` / ``position_id`` key or attribute
    (an ``asyncpg.Record`` straight out of a query works). Whatever is passed,
    **only the id is used**: every other field is re-read from the database
    under the org's RLS context. A caller that handed in its own
    ``source_system`` alongside the id would be trusting the ingestion pipeline
    to have remembered what it wrote, and precedence is exactly the mechanism
    that exists because pipelines disagree.

    All candidates must share one ``(owner_entity_id, asset_id, as_of_date)``.
    Resolving across two different holdings is not a thing that has an answer,
    and accepting it silently would mark a perfectly good position as superseded
    by a source describing a different asset.

    With ``apply=False`` it computes the outcome and writes nothing — the read
    a reconciliation screen wants.

    Writes, when applying:

    * every loser's ``superseded_by_source`` ← the winner's ``source_system``
    * the winner's ``superseded_by_source`` ← ``NULL``

    Clearing the winner is not housekeeping. A row that lost a previous
    resolution and wins the next one — because the org re-ordered its sources,
    or because the row that beat it was corrected away — would otherwise stay
    flagged as superseded by a source that no longer outranks it, and every
    downstream reader would skip the actual answer. Both writes are idempotent:
    re-running over an already-resolved set changes nothing and reports
    ``rows_marked=0, rows_cleared=0``.
    """
    org_id = _require_org(org_id)
    ids = [_coerce_id(c) for c in position_candidates]
    if not ids:
        raise PrecedenceError("position_candidates is empty — nothing to resolve")
    # De-dupe, order-preserving. The same id twice is a caller bug, not a tie.
    seen: set[str] = set()
    unique_ids = [i for i in ids if not (i in seen or seen.add(i))]

    source_order = await get_source_order(conn, org_id)
    order = source_order.order

    async with _OrgWrite(conn, org_id) as c:
        rows = await c.fetch(
            f"""
            SELECT {_CANDIDATE_COLS}
            FROM {TABLE_POSITIONS} p
            WHERE p.id = ANY($1::uuid[]) AND p.org_id = $2::uuid
              AND {_current('p')}
            ORDER BY p.system_from DESC, p.id DESC
            """,
            unique_ids, org_id,
        )
        found = {r["id"] for r in rows}
        missing = [i for i in unique_ids if i not in found]
        if missing:
            raise PrecedenceError(
                f"position(s) {missing} are not current rows in org {org_id}. "
                f"A position that is not visible under this org's RLS context "
                f"cannot be resolved against — it may belong to another tenant, "
                f"or have been closed (valid_to / system_to)."
            )

        keys = {(r["owner_entity_id"], r["asset_id"], r["as_of_date"]) for r in rows}
        if len(keys) != 1:
            raise PrecedenceError(
                f"precedence resolves ONE holding key, but the candidates span "
                f"{len(keys)} distinct (owner_entity_id, asset_id, as_of_date) "
                f"combinations: {sorted(str(k) for k in keys)}. Resolving across "
                f"holdings would mark a position superseded by a source that "
                f"describes a different asset."
            )
        owner_entity_id, asset_id, as_of_date = next(iter(keys))

        # `rows` arrives newest-first (system_from DESC, id DESC) and Python's
        # sort is stable, so ranking by trust alone preserves recency as the
        # tie-break within a source. That is the restatement rule: two rows from
        # the same feed for the same date are the same source correcting itself,
        # and the later one is what that source now says.
        ranked = sorted(rows, key=lambda r: _sort_key(r, order))
        winner, losers = ranked[0], ranked[1:]
        winning_source = winner["source_system"]

        rows_marked = 0
        rows_cleared = 0
        if apply:
            stale = [
                r["id"] for r in losers
                if r["superseded_by_source"] != winning_source
            ]
            if stale:
                rows_marked = int(await c.fetchval(
                    f"""
                    WITH upd AS (
                        UPDATE {TABLE_POSITIONS} p
                        SET superseded_by_source = $1
                        WHERE p.id = ANY($2::uuid[]) AND p.org_id = $3::uuid
                        RETURNING 1
                    ) SELECT count(*) FROM upd
                    """,
                    winning_source, stale, org_id,
                ))
            if winner["superseded_by_source"] is not None:
                rows_cleared = int(await c.fetchval(
                    f"""
                    WITH upd AS (
                        UPDATE {TABLE_POSITIONS} p
                        SET superseded_by_source = NULL
                        WHERE p.id = $1::uuid AND p.org_id = $2::uuid
                        RETURNING 1
                    ) SELECT count(*) FROM upd
                    """,
                    winner["id"], org_id,
                ))

    return PrecedenceOutcome(
        owner_entity_id=owner_entity_id,
        asset_id=asset_id,
        as_of_date=as_of_date,
        winner_position_id=winner["id"],
        winner_source_system=winning_source,
        losers=tuple(
            PrecedenceCandidate(
                position_id=r["id"],
                source_system=r["source_system"],
                rank=_rank(order, r["source_system"]),
                # The value AFTER this call, which is what a caller inspecting
                # the outcome means by "what is it marked with".
                superseded_by_source=winning_source if apply
                else r["superseded_by_source"],
            )
            for r in losers
        ),
        order=order,
        order_is_default=source_order.is_default,
        order_invalid_reason=source_order.invalid_reason,
        rows_marked=rows_marked,
        rows_cleared=rows_cleared,
    )


async def resolve_holding(
    conn,
    org_id: str,
    *,
    owner_entity_id: str,
    asset_id: str,
    as_of_date: date,
    apply: bool = True,
) -> PrecedenceOutcome | None:
    """Resolve every current position for one holding key. ``None`` if there are none.

    This is what the ingestion path calls after writing: it gathers the
    candidates itself rather than trusting the importer to have noticed that
    somebody else already had a position on this key. An importer that only
    resolved against the rows IT wrote would never discover the manual entry it
    was supposed to supersede — which is the entire scenario the feature exists
    for.

    A single candidate is not a no-op: it still runs, so a lone survivor whose
    competitor was removed gets its stale ``superseded_by_source`` cleared.
    """
    org_id = _require_org(org_id)
    if not isinstance(as_of_date, date):
        raise PrecedenceError(
            f"as_of_date must be a datetime.date — got {type(as_of_date).__name__}"
        )
    async with _OrgWrite(conn, org_id) as c:
        ids = [
            r["id"] for r in await c.fetch(
                f"""
                SELECT p.id::text AS id FROM {TABLE_POSITIONS} p
                WHERE p.org_id = $1::uuid AND p.owner_entity_id = $2::uuid
                  AND p.asset_id = $3::uuid AND p.as_of_date = $4
                  AND {_current('p')}
                """,
                org_id, str(owner_entity_id), str(asset_id), as_of_date,
            )
        ]
    if not ids:
        return None
    return await resolve_precedence(conn, org_id, ids, apply=apply)


def _coerce_id(candidate: Any) -> str:
    """Pull a position id out of whatever the caller passed."""
    if candidate is None:
        raise PrecedenceError("position_candidates contains None")
    if isinstance(candidate, str):
        return candidate
    for key in ("position_id", "id"):
        if isinstance(candidate, Mapping) and key in candidate:
            return str(candidate[key])
        try:
            if key in candidate:  # asyncpg.Record supports `in`
                return str(candidate[key])
        except TypeError:
            pass
        value = getattr(candidate, key, None)
        if value is not None:
            return str(value)
    return str(candidate)
