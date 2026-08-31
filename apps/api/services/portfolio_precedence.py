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

   **fee32 adds ONE more specific level ahead of it**, and nothing else: an
   active row in ``public.portfolio_precedence_household_overrides`` for the
   household the holding belongs to. Resolution order is now household override
   → org setting → ``DEFAULT_SETTINGS``, and the second and third are untouched.
   A household with no override row resolves through exactly the code path it
   resolved through before — the override lookup returns ``None`` and
   :func:`get_source_order` runs unchanged. That is the property the sprint is
   held to, so it is stated here rather than left to be inferred.

   Why a household and not an account or an entity: one family reports through
   one set of feeds. "We trust Addepar for the Hollis household because their
   custodian's feed is six weeks behind" is a real statement an operator makes,
   and it is true of every account and every entity under that household at
   once. Per-account overrides would multiply the same decision by the number
   of accounts and let them drift apart silently.

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

import json
from dataclasses import dataclass, replace
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

#: fee32's household override table. NOTE THE SCHEMA: the table is named
#: ``portfolio_precedence_household_overrides`` but it lives in ``public``, not
#: in the ``portfolio`` schema. Introspected from the deployed database, not
#: inferred from the name — ``portfolio`` is not on any application role's
#: search_path, so qualifying this as ``portfolio.`` would fail at runtime and
#: the name is exactly the kind of thing that invites the wrong guess.
TABLE_HOUSEHOLD_OVERRIDES = "public.portfolio_precedence_household_overrides"

TABLE_ACCOUNTS = "public.accounts"
TABLE_ENTITIES = "public.entities"

#: Where an order came from. Reported rather than inferred, for the same reason
#: ``SourceOrder.is_default`` is: a reconciliation screen that cannot say WHY a
#: source won is not a reconciliation screen.
ORIGIN_HOUSEHOLD = "household_override"
ORIGIN_ORG_SETTING = "org_setting"
ORIGIN_DEFAULT = "platform_default"

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
    #: fee32. One of ``ORIGIN_*``. Defaulted so every existing construction site
    #: and every existing reader keeps working unchanged — a caller that only
    #: knows about ``is_default`` still gets the same two-valued answer it did
    #: before, and ``origin`` is strictly extra information.
    origin: str = ORIGIN_DEFAULT
    #: The household whose override supplied this order, when ``origin`` is
    #: ``ORIGIN_HOUSEHOLD``. ``None`` otherwise.
    household_id: str | None = None
    #: Why no household override applied, when one might have been expected.
    #: ``None`` when an override DID apply or when the question never arose.
    household_reason: str | None = None

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
        return SourceOrder(
            DEFAULT_SOURCE_ORDER, is_default=True, origin=ORIGIN_DEFAULT
        )
    try:
        return SourceOrder(
            validate_source_order(raw), is_default=False, origin=ORIGIN_ORG_SETTING
        )
    except PrecedenceConfigError as exc:
        return SourceOrder(
            DEFAULT_SOURCE_ORDER, is_default=True, invalid_reason=str(exc),
            origin=ORIGIN_DEFAULT,
        )


# ── The household override — fee32 ──────────────────────────────────────────


class HouseholdOverrideError(PortfolioError):
    """A household override write was refused for a reason the caller can fix."""


async def get_household_override(
    conn, org_id: str, household_id: str
) -> dict[str, Any] | None:
    """The household's ACTIVE override row, or ``None``. Read-only.

    "Active" is ``system_to IS NULL AND valid_to IS NULL`` — both axes, matching
    the partial unique index the table actually carries
    (``… WHERE system_to IS NULL``) plus the valid axis the table's own
    ``valid_from``/``valid_to`` columns exist for. A row closed on either axis
    is history and must not resolve anything.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        row = await c.fetchrow(
            f"""
            SELECT h.id::text AS id, h.household_id::text AS household_id,
                   h.source_order, h.reason,
                   h.approved_by::text AS approved_by,
                   h.created_at, h.valid_from
            FROM {TABLE_HOUSEHOLD_OVERRIDES} h
            WHERE h.org_id = $1::uuid AND h.household_id = $2::uuid
              AND h.system_to IS NULL AND h.valid_to IS NULL
            """,
            org_id, str(household_id),
        )
    if row is None:
        return None
    raw = row["source_order"]
    return {
        **dict(row),
        "source_order": json.loads(raw) if isinstance(raw, str) else raw,
        "created_at": row["created_at"].isoformat(),
        "valid_from": row["valid_from"].isoformat(),
    }


async def get_household_source_order(
    conn, org_id: str, household_id: str | None
) -> SourceOrder | None:
    """The household's override as a :class:`SourceOrder`, or ``None``.

    ``None`` means "this household has not overridden anything" and is the
    signal to fall through to the org setting — NOT an error and NOT the
    default order. Returning ``DEFAULT_SOURCE_ORDER`` here instead would silently
    stop the org's own configured order from ever applying, which is the exact
    failure mode this sprint must not introduce.

    A stored override that no longer validates falls through to the org level
    the same way an invalid org setting falls through to the default, and for
    the same reason: resolution is on the ingestion path, and failing an import
    because a settings row went stale turns a configuration problem into a data
    problem. The ``invalid_reason`` rides along so a caller can surface it.
    """
    if not household_id:
        return None
    override = await get_household_override(conn, org_id, str(household_id))
    if override is None:
        return None
    try:
        return SourceOrder(
            validate_source_order(override["source_order"]),
            is_default=False,
            origin=ORIGIN_HOUSEHOLD,
            household_id=str(household_id),
        )
    except PrecedenceConfigError as exc:
        # Deliberately NOT a SourceOrder: an unusable override must fall
        # through to the org level, and returning one here would apply a
        # half-broken order. The reason is carried to the caller by
        # `_resolve_source_order`, which re-reads it.
        return SourceOrder(
            (), is_default=False, origin=ORIGIN_HOUSEHOLD,
            household_id=str(household_id), invalid_reason=str(exc),
        )


async def household_for_position(
    conn, org_id: str, *, account_id: str | None, owner_entity_id: str
) -> str | None:
    """Which household a position belongs to. ``None`` when it belongs to none.

    The RFC settles the precedence between the two available routes:

    * ``account_id`` → ``accounts.household_id``, when the position carries an
      account. The account is the more specific fact: a position reported on a
      statement belongs to whatever household that statement's account is
      filed under, even if the owning entity's own ``primary_household_id``
      says something else (a trust whose primary household is the grantor's
      while the account sits under the beneficiary's).
    * otherwise ``owner_entity_id`` → ``entities.primary_household_id``.

    Note that an account whose ``household_id`` is NULL does NOT fall back to
    the entity route. The account is present and it says "no household" — that
    is an answer, not an absence, and falling through would let a position's
    household depend on a column the operator deliberately left empty.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        if account_id:
            return await c.fetchval(
                f"""
                SELECT a.household_id::text FROM {TABLE_ACCOUNTS} a
                WHERE a.id = $1::uuid AND a.org_id = $2::uuid
                  AND {_current('a')}
                """,
                str(account_id), org_id,
            )
        return await c.fetchval(
            f"""
            SELECT e.primary_household_id::text FROM {TABLE_ENTITIES} e
            WHERE e.id = $1::uuid AND e.org_id = $2::uuid AND {_current('e')}
            """,
            str(owner_entity_id), org_id,
        )


async def _household_for_candidates(
    conn, org_id: str, rows: Sequence[Mapping[str, Any]]
) -> tuple[str | None, str | None]:
    """The ONE household these candidates share, and why there isn't one.

    Returns ``(household_id, reason)``. Exactly one of them is ever non-None.

    Every candidate shares an ``owner_entity_id`` — :func:`resolve_precedence`
    has already refused the set otherwise — but they need NOT share an
    ``account_id``: the normal shape is one row from a custodial feed carrying
    an account and one manual row carrying none. Each candidate is mapped to a
    household independently and the distinct answers are compared:

    * exactly one distinct household → that household's override applies;
    * none → no household, org level, no reason to report;
    * more than one → **no override is applied** and the reason says so.

    Refusing to guess in the ambiguous case is the point. Picking "the first
    one" would make which household's policy governs a holding depend on row
    insertion order, and the resulting winner would flip on re-resolution
    without any setting having changed.
    """
    households: dict[str, None] = {}
    for row in rows:
        household = await household_for_position(
            conn, org_id,
            account_id=row.get("account_id"),
            owner_entity_id=row["owner_entity_id"],
        )
        if household:
            households[household] = None

    if not households:
        return None, None
    if len(households) == 1:
        return next(iter(households)), None
    return None, (
        f"the candidate positions map to {len(households)} different "
        f"households ({sorted(households)}), so no household override was "
        f"applied — resolution fell through to the org level. Which household "
        f"governs a holding must not depend on which row was written first."
    )


async def resolve_source_order_for_household(
    conn, org_id: str, household_id: str | None, *, ambiguity: str | None = None
) -> SourceOrder:
    """The order that governs a HOUSEHOLD. Household override → org → default.

    fee32 expressed this fall-through inline inside :func:`_resolve_source_order`,
    which derives its household from a set of position candidates. fee41 needs
    the identical decision for a household that may own no positions at all — a
    fee narrative describes the arrangement, and an arrangement exists before the
    first holding lands.

    So the three-level fall-through lives HERE and
    :func:`_resolve_source_order` calls it. Copying it would have been three
    lines; it would also have been a second place for the precedence order of a
    firm's billing prose to drift away from the precedence order its portfolio
    actually resolves under, with nothing failing when they disagreed.

    ``ambiguity`` is the caller's explanation for why ``household_id`` is
    ``None`` when one might have been expected. It rides through onto
    ``household_reason`` exactly as before.
    """
    household_order = await get_household_source_order(conn, org_id, household_id)
    if household_order is not None and household_order.order:
        return household_order

    org_order = await get_source_order(conn, org_id)
    reason = ambiguity
    if household_order is not None and household_order.invalid_reason:
        reason = (
            f"household {household_id} has an override that is not usable and "
            f"was ignored: {household_order.invalid_reason}"
        )
    return replace(org_order, household_id=household_id, household_reason=reason)


async def _resolve_source_order(
    conn, org_id: str, rows: Sequence[Mapping[str, Any]]
) -> SourceOrder:
    """The order that governs THIS set of candidates. Household → org → default.

    The ONLY new decision fee32 makes. When it returns without a household
    override — because there is no household, or no override row for it, or the
    override is unusable, or the candidates span several households — it returns
    exactly what ``get_source_order(conn, org_id)`` returned before this sprint
    existed, and the rest of resolution is byte-identical.

    fee41 moved the fall-through itself into
    :func:`resolve_source_order_for_household`; deriving the household from the
    candidate rows is still this function's own job and is unchanged.
    """
    household_id, ambiguity = await _household_for_candidates(conn, org_id, rows)
    return await resolve_source_order_for_household(
        conn, org_id, household_id, ambiguity=ambiguity
    )


# ── Managing an override ────────────────────────────────────────────────────


async def set_household_source_order(
    conn,
    org_id: str,
    *,
    household_id: str,
    source_order: Any,
    reason: str,
    approved_by: str,
) -> str:
    """Create or replace a household's override. Returns the ACTIVE row's id.

    Bi-temporal on the SYSTEM axis, matching the deployed partial unique index
    (``(org_id, household_id) WHERE system_to IS NULL``): the existing active
    row is closed with ``system_to`` and a new row inserted. Nothing points at
    this table's ``id`` by foreign key, so the valid axis would have worked too
    — the system axis is chosen because a changed override is a NEW POLICY
    DECISION with its own approver and reason, not a correction of what the
    previous decision was. The superseded row keeps its own reason and
    ``approved_by`` intact, which is the whole audit value of the table.

    ``reason`` and ``approved_by`` are NOT NULL in the deployed schema and are
    required here rather than defaulted. An override that overrules the firm's
    own configured precedence for one family, with no recorded reason and no
    named approver, is the thing an auditor asks about.

    The order is validated BEFORE anything is written — the same
    :func:`validate_source_order` the org-level setting uses, so a household
    cannot save an order the org level would have refused.
    """
    org_id = _require_org(org_id)
    if not str(reason or "").strip():
        raise HouseholdOverrideError(
            "reason is required — an override with no recorded reason cannot be "
            "reviewed, only discovered"
        )
    if not approved_by:
        raise HouseholdOverrideError(
            "approved_by is required — an override approved by nobody is not a "
            "policy decision"
        )
    order = validate_source_order(source_order)

    async with _OrgWrite(conn, org_id) as c:
        household_exists = await c.fetchval(
            "SELECT 1 FROM public.households "
            "WHERE id = $1::uuid AND org_id = $2::uuid",
            str(household_id), org_id,
        )
        if not household_exists:
            raise HouseholdOverrideError(
                f"household {household_id} is not a household in org {org_id}. "
                f"The foreign key on this table references households(id) with "
                f"no org predicate, so this check is what keeps an override "
                f"from being filed against another tenant's household."
            )
        await c.execute(
            f"""
            UPDATE {TABLE_HOUSEHOLD_OVERRIDES} h
            SET system_to = now()
            WHERE h.org_id = $1::uuid AND h.household_id = $2::uuid
              AND h.system_to IS NULL
            """,
            org_id, str(household_id),
        )
        return await c.fetchval(
            f"""
            INSERT INTO {TABLE_HOUSEHOLD_OVERRIDES}
                (org_id, household_id, source_order, reason, approved_by)
            VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, $5::uuid)
            RETURNING id::text
            """,
            org_id, str(household_id), json.dumps(list(order)),
            str(reason).strip(), str(approved_by),
        )


async def clear_household_source_order(
    conn, org_id: str, *, household_id: str
) -> bool:
    """Retire a household's override. ``False`` if it had none.

    Closes the active row on the system axis rather than deleting it: the
    decision was made, and a policy that vanishes leaves a reconciliation
    screen unable to explain why last quarter's winner was what it was.

    After this the household resolves through the org setting again —
    identically to a household that never had an override, which is what makes
    "added, changed, removed" a real three-state test rather than two.
    """
    org_id = _require_org(org_id)
    async with _OrgWrite(conn, org_id) as c:
        closed = await c.fetchval(
            f"""
            WITH upd AS (
                UPDATE {TABLE_HOUSEHOLD_OVERRIDES} h
                SET system_to = now()
                WHERE h.org_id = $1::uuid AND h.household_id = $2::uuid
                  AND h.system_to IS NULL
                RETURNING 1
            ) SELECT count(*) FROM upd
            """,
            org_id, str(household_id),
        )
    return bool(closed)


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
    #: fee32. Where the winning order came from (one of ``ORIGIN_*``), the
    #: household it was derived for, and — when a household override could have
    #: applied but did not — why. All three default so every existing
    #: construction site and reader is unaffected.
    order_origin: str = ORIGIN_DEFAULT
    household_id: str | None = None
    household_reason: str | None = None

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
    "superseded_by_source, system_from, "
    # fee32. Read here rather than passed in for the same reason every other
    # field is: a caller that handed in the account would be trusting the
    # pipeline to have remembered what it wrote, and precedence exists because
    # pipelines disagree.
    "account_id::text AS account_id"
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

    **fee32** — the order these candidates are ranked by is now resolved by
    :func:`_resolve_source_order`: a household override first, then the org
    setting, then the platform default. The household is derived from the
    candidates themselves (each row's ``account_id`` → ``accounts.household_id``
    when it has one, else its ``owner_entity_id`` →
    ``entities.primary_household_id``) and the outcome reports which of the
    three actually governed, via ``order_origin`` / ``household_id``. A holding
    whose household has no override is ranked by the identical order it was
    ranked by before, through the identical call.

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

        # fee32: moved to AFTER the fetch. The order is no longer a property of
        # the org alone — it can be a property of the household these specific
        # candidates belong to, and the household is derived from the rows. For
        # a holding with no household, or a household with no override, this
        # returns exactly what `get_source_order(conn, org_id)` returned when it
        # was called before the fetch, and everything below is unchanged.
        source_order = await _resolve_source_order(c, org_id, rows)
        order = source_order.order

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
        order_origin=source_order.origin,
        household_id=source_order.household_id,
        household_reason=source_order.household_reason,
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
