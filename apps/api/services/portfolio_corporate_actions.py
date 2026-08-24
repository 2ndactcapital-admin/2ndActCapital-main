"""Corporate actions — Portfolio Phase F.

RECORDING AND APPLYING ARE TWO DIFFERENT OPERATIONS AT TWO DIFFERENT SCOPES
──────────────────────────────────────────────────────────────────────────────
A 2-for-1 split of one security is **one real-world event about one security**.
It is not a fact that becomes truer, or different, because a second tenant
happens to hold that security. So the record of it lives in
``portfolio.securities_global_corporate_actions``, which has **no ``org_id``** —
the same scope decision A1 already made for prices and identifiers, for the same
reason. (The design's original §10 sketch keyed corporate actions to
``asset_id``, which is tenant-scoped; that was corrected in this sprint's Part 1
SQL. See ``docs/PROJECT_STATUS.md`` §7n.)

**Applying** it is the opposite. Each org's own ``assets`` and ``positions`` are
tenant rows, and each org restates its own holdings from the same recorded event,
independently. One org applying a split has no effect whatsoever on another org
that holds the same security — that is asserted directly in
``verify_portfoliof.py`` against the real ``app_service`` connection, not
inferred from the existence of an RLS policy.

Hence: :func:`record_corporate_action` is Super-Admin-gated and global.
:func:`apply_split` and :func:`apply_spinoff` take an ``org_id`` and are gated by
org isolation. Nothing here does both at once.

CONSUMED, NOT COMPUTED
──────────────────────────────────────────────────────────────────────────────
``terms`` is **published data**. A split ratio and a spinoff's cost-basis
allocation come from the custodian feed or the market-data provider that
published them. This module reads them; it never derives them. There is no code
here that infers a ratio from a price discontinuity, and there should never be:
a ratio guessed from a 49.6% price move is indistinguishable, downstream, from
one the issuer actually declared, and by the time anyone notices the position has
been restated.

That is also why :func:`record_corporate_action` validates that ``terms`` is
present and is a non-empty JSON object and **nothing further**. The keys this
module happens to read (:data:`TERMS_RATIO`, :data:`TERMS_DISTRIBUTION_RATIO`,
:data:`TERMS_COST_BASIS_PCT`) are read at APPLY time, by the apply function that
needs them, and a caller recording a ``merger`` or a ``tender`` for future use
must not be forced to invent a ``ratio`` key to get the row stored.

WHY ``adjustment``, AND WHY THE FLAG IS NOT DERIVED
──────────────────────────────────────────────────────────────────────────────
Every write here goes through A2's real :func:`~services.portfolio_assets.
create_position` and :func:`~services.portfolio_assets.record_transaction`. That
is load-bearing rather than tidy: ``create_position`` is the only code in the
codebase enforcing the ownership-basis contract (``portfolio.positions`` has no
CHECK covering it), and ``record_transaction`` is the only thing checking a
transaction type's ``market`` against the asset's.

The type is ``adjustment``, chosen by reading the deployed
``public.transaction_types``, not by assumption. It is the ONLY one of the
sixteen rows that is simultaneously ``direction='none'``,
``performance_impact='none'``, ``affects_paid_in=0``, ``affects_unfunded=0``,
``affects_nav=0`` and ``market='both'`` — so it can attach to a listed equity and
a private fund interest alike, and it registers as neither a gain, an income
item, a contribution nor a distribution. ``sell`` carries
``performance_impact='gain'``; using it would make every split show up in
realized gains. One honest mismatch, reported rather than papered over:
``adjustment.amount_basis`` is ``'currency'`` while a split adjustment carries a
*unit* delta. There is no units-based, performance-neutral type in the deployed
vocabulary, and inventing one is a schema change this sprint did not ask for.

``transactions.is_corporate_action_adjustment`` is set explicitly and is NOT
derived from ``corporate_action_id IS NOT NULL``. A report must be able to write
``WHERE is_corporate_action_adjustment = false`` and get a correct realized-gain
population **without knowing this module exists**. Deriving the flag would also
collapse the one case where the two legitimately differ: a cash-in-lieu *sale*
cites a corporate action and genuinely IS a realized gain.

BI-TEMPORAL RESTATEMENT, NOT AN UPDATE
──────────────────────────────────────────────────────────────────────────────
A split changes a position's quantity. Per CLAUDE.md Rule 3 that is never an
in-place update: the current row is closed (``valid_to = now()``) and a new row
is inserted through ``create_position``. The only UPDATE this module issues is
that close — it touches ``valid_to`` and nothing else, and it never rewrites a
measure.

Two consequences worth stating, because both are silent if you get them wrong:

* **The position id changes.** The adjustment transaction is attached to the NEW
  row, which is the one a reader looking at "this holding today" will find.
  Idempotency therefore keys on ``(org_id, corporate_action_id)`` and never on a
  position id, which would not survive the restatement it is trying to detect.

* **``as_of_date`` is preserved, deliberately.** It is the position's stated
  as-of, and the split does not move it; the bi-temporal axis records *when the
  restatement happened*. Advancing it to the ex-date would mint a second holding
  under a different natural key ``(owner_entity_id, asset_id, as_of_date)`` —
  which is exactly what ``portfolio_precedence.resolve_precedence`` resolves on,
  and it would then see two holdings where there is one.

ATOMICITY
──────────────────────────────────────────────────────────────────────────────
Each apply function opens **one** transaction and calls A2's writers inside it.
A2's ``_OrgWrite`` nests as a SAVEPOINT when the connection is already in a
transaction, so every close, every ``create_position``, every ``create_asset``
and every ``record_transaction`` for one apply call commits together or not at
all. Without the outer transaction, an org could end up holding the spinoff
shares and not the reduced parent basis — one side of an event that has no one
side.

WHAT IS DELIBERATELY NOT HERE
──────────────────────────────────────────────────────────────────────────────
No UDFs (Phase G). No reconciliation engine, performance calculation or
cross-client analysis (Phase H). No merger / tender / delisting application
logic — those ``action_type`` values exist in the deployed CHECK constraint and
can be RECORDED, and :func:`apply_corporate_action` refuses them by name rather
than doing something approximate. No cash movement for
``cash_in_lieu_per_share``: cash is Phase D's ``portfolio_cash``, the fractional-
share cash a spinoff pays is a real cash event and manufacturing one here from a
per-share rate would be computing rather than consuming. The value is preserved
verbatim in ``terms`` and reported in :attr:`ApplyOutcome.unapplied_terms`.

**``securities_global_corporate_actions.applied_at`` is never written by an apply
function.** It is a column on the GLOBAL row, and "applied" is a per-org fact.
Stamping it when the first org applies would tell the second org the event had
already been handled. Whether an org has applied an action is answered by
:func:`already_applied_transactions`, which reads that org's own transactions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from services.portfolio_assets import (
    AUTHORITIES,
    SOURCE_SYSTEMS,
    TABLE_ASSETS,
    TABLE_POSITIONS,
    TABLE_TRANSACTIONS,
    UNITS,
    PortfolioError,
    _OrgWrite,
    _require_org,
    create_asset,
    create_position,
    record_transaction,
)
from services.securities_global import (
    TABLE_SEC,
    SecuritiesGlobalError,
    SecuritiesGlobalPermissionError,
    _require_super_admin,
    _SuperAdminWrite,
)

# ── Schema-qualified, always. `portfolio` is NOT on app_service's search_path.
TABLE_CORP_ACTIONS = "portfolio.securities_global_corporate_actions"


# ── Vocabularies, mirrored verbatim from the deployed CHECK constraint ───────
# corp_actions_type_chk. Duplicated in Python for the same reason A1 and A2
# duplicate theirs: a 23514 names a constraint and not the value that was wrong.
SPLIT = "split"
REVERSE_SPLIT = "reverse_split"
SPINOFF = "spinoff"
ACTION_TYPES = frozenset({
    SPLIT, REVERSE_SPLIT, SPINOFF, "merger", "name_change", "cusip_change",
    "tender", "delisting",
})

# The subset this sprint knows how to APPLY. The rest can be recorded — the fact
# is still one fact, and recording it is what makes it available to Phase G/H —
# but there is no application logic, and `apply_corporate_action` says so by name
# rather than silently doing nothing, which is indistinguishable from "this org
# holds none of it".
APPLICABLE_ACTION_TYPES = frozenset({SPLIT, REVERSE_SPLIT, SPINOFF})

# Action types that cannot be applied without knowing what the holder ends up
# with, so the resulting security is required at RECORD time rather than
# discovered to be missing at apply time.
_REQUIRES_RESULTING_SECURITY = frozenset({SPINOFF})

# The `terms` keys read at APPLY time. Recording does not require any of them.
TERMS_RATIO = "ratio"
TERMS_DISTRIBUTION_RATIO = "distribution_ratio"
TERMS_COST_BASIS_PCT = "cost_basis_allocation_pct_original"
TERMS_CASH_IN_LIEU = "cash_in_lieu_per_share"

# Terms keys this module reads but deliberately does not act on. Reported back on
# the outcome so a caller is told what was ignored instead of assuming the whole
# of `terms` was honoured.
_UNAPPLIED_TERMS_KEYS = (TERMS_CASH_IN_LIEU,)

# Defaults for the adjustment transaction. `internal` because the adjustment was
# computed here from published terms — the custodian did not state it, so
# claiming `custodial` would manufacture provenance. `manual` because the apply
# is operator-triggered; there is no `corporate_action` token in the deployed
# `positions_source_chk` vocabulary and adding one is a schema change this sprint
# did not ask for.
ADJUSTMENT_TYPE_CODE = "adjustment"
DEFAULT_AUTHORITY = "internal"
DEFAULT_SOURCE_SYSTEM = "manual"


def _current(alias: str) -> str:
    """The "this row is the current truth" predicate, alias-qualified on BOTH
    temporal columns — identical to A1's and A2's, and for the same reason: every
    query below joins at least two temporal tables."""
    return f"{alias}.valid_to IS NULL AND {alias}.system_to IS NULL"


class CorporateActionError(PortfolioError):
    """A corporate-action write or apply was refused for a fixable reason.

    Subclasses A2's :class:`~services.portfolio_assets.PortfolioError` (itself a
    ``ValueError``) so an ingestion loop already catching portfolio errors keeps
    catching these, rather than crashing on a new sibling exception type.
    """


class UnapplicableActionError(CorporateActionError):
    """The action was recorded successfully but has no application logic here.

    Its own class so a batch applier can skip-and-count mergers and tenders while
    still failing hard on a malformed split — the same distinction
    ``StructuredNotePricingError`` draws in A1.
    """


# ── Ratios ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Ratio:
    """A published ratio, read as **NEW : OLD**.

    ``"2:1"`` on a split is two post-split shares for each one held. ``"1:10"``
    on a reverse split is one for ten. ``"1:4"`` as a spinoff distribution is one
    spinco share per four parent shares. One reading, applied to all three.

    Numerator and denominator are kept SEPARATE rather than pre-divided.
    ``Decimal(1) / Decimal(3)`` is ``0.3333333333333333333333333333`` and a
    quantity multiplied by it then divided back out does not return to where it
    started; ``qty * 1 / 3`` and ``qty * 3 / 1`` do. Cost basis is the reason
    this matters — the whole assertion of a split is that TOTAL cost basis is
    unchanged, and a pre-divided multiplier loses that in the last digit.
    """

    numerator: Decimal
    denominator: Decimal

    def apply(self, value: Decimal) -> Decimal:
        """new = value × numerator ÷ denominator."""
        return value * self.numerator / self.denominator

    def unapply(self, value: Decimal) -> Decimal:
        """The inverse. What a per-unit cost does when a quantity is multiplied."""
        return value * self.denominator / self.numerator

    def __str__(self) -> str:
        return f"{_plain(self.numerator)}:{_plain(self.denominator)}"


def _plain(value: Decimal) -> str:
    """Render a Decimal without exponent notation, for messages only."""
    return format(value.normalize(), "f")


def parse_ratio(value: Any, field_name: str = TERMS_RATIO) -> Ratio:
    """Parse a published ratio. Accepts ``"2:1"``, ``"2"``, ``2``, ``Decimal``.

    ``float`` is refused, exactly as A1's and A2's ``_money`` refuse it and for
    the identical reason: ``Decimal(1.1)`` is not ``Decimal("1.1")``, nothing
    raises, and the quantity is simply wrong from then on. A ratio is not money
    but it multiplies money.

    Zero and negative components are refused. A ``0:1`` split is not a split; if
    a feed emits one it is a parse failure upstream, and applying it would zero
    every holder's quantity silently.
    """
    if isinstance(value, Ratio):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise CorporateActionError(
            f"{field_name} must be a string like '2:1', an int, or a Decimal — "
            f"got {type(value).__name__}. Binary floats cannot represent a "
            f"decimal ratio exactly and Decimal(float) silently preserves the "
            f"error into every restated quantity."
        )
    if value is None:
        raise CorporateActionError(
            f"{field_name} is required in terms and was not supplied. This "
            f"module consumes published terms and never derives a ratio."
        )

    if isinstance(value, (int, Decimal)):
        num, den = Decimal(value), Decimal(1)
    else:
        text = str(value).strip()
        if not text:
            raise CorporateActionError(f"{field_name} is empty")
        parts = [p.strip() for p in text.replace("-for-", ":").split(":")]
        if len(parts) == 1:
            parts.append("1")
        if len(parts) != 2:
            raise CorporateActionError(
                f"{field_name}={value!r} is not a ratio. Expected 'NEW:OLD' "
                f"(e.g. '2:1' for a two-for-one split, '1:10' for a "
                f"one-for-ten reverse split) or a bare multiplier."
            )
        try:
            num, den = Decimal(parts[0]), Decimal(parts[1])
        except InvalidOperation as exc:
            raise CorporateActionError(
                f"{field_name}={value!r} has a non-numeric component"
            ) from exc

    if num <= 0 or den <= 0:
        raise CorporateActionError(
            f"{field_name}={value!r} resolves to {_plain(num)}:{_plain(den)}. "
            f"Both components must be positive — a zero or negative ratio would "
            f"zero out or invert every holder's quantity, and no issuer declares "
            f"one."
        )
    return Ratio(numerator=num, denominator=den)


def _percent(value: Any, field_name: str) -> Decimal:
    """Parse a published percentage, 0 < pct <= 100. Refuses float."""
    if isinstance(value, bool) or isinstance(value, float):
        raise CorporateActionError(
            f"{field_name} must be a Decimal, int or str — got "
            f"{type(value).__name__}."
        )
    try:
        pct = Decimal(value) if not isinstance(value, str) else Decimal(value.strip())
    except (InvalidOperation, TypeError) as exc:
        raise CorporateActionError(f"{field_name}={value!r} is not a number") from exc
    if not (Decimal(0) < pct <= Decimal(100)):
        raise CorporateActionError(
            f"{field_name}={_plain(pct)} is outside (0, 100]. It is the "
            f"percentage of the ORIGINAL cost basis that stays with the original "
            f"security, as published by the issuer's Form 8937."
        )
    return pct


def _coerce_terms(terms: Any) -> dict[str, Any]:
    """Validate ``terms`` is present and is a JSON object. Nothing more.

    See the module docstring: the internal shape is deliberately unvalidated,
    because the keys that matter differ per ``action_type`` and are read by the
    apply function that needs them. A ``merger`` recorded for future use must not
    have to invent a ``ratio``.

    A JSON *string* is accepted and parsed, so a caller holding a raw feed
    payload does not have to round-trip it — but it is parsed rather than passed
    through, so a malformed payload fails here instead of as a 22P02 from the
    ``::jsonb`` cast naming neither the column nor the caller.
    """
    if terms is None:
        raise CorporateActionError(
            "terms is required (NOT NULL in the schema). A corporate action with "
            "no published terms cannot be applied, and recording one would "
            "create a row that looks actionable and is not."
        )
    if isinstance(terms, str):
        try:
            terms = json.loads(terms)
        except json.JSONDecodeError as exc:
            raise CorporateActionError(f"terms is not valid JSON: {exc}") from exc
    if not isinstance(terms, Mapping):
        raise CorporateActionError(
            f"terms must be a JSON object — got {type(terms).__name__}. The "
            f"published terms of an action are keyed values "
            f"(e.g. {{'ratio': '2:1'}}), not a scalar or a list."
        )
    if not terms:
        raise CorporateActionError(
            "terms is empty. See above: an empty object is indistinguishable "
            "from 'we never received the terms'."
        )
    return dict(terms)


# ── Results ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AdjustedPosition:
    """One position's before/after, and the transaction that records the change.

    Both sides are carried because "the position now says 200" does not prove an
    adjustment happened — that is also what you get if it always said 200. The
    verification asserts on the pair.
    """

    asset_id: str
    owner_entity_id: str
    original_position_id: str
    position_id: str
    quantity_before: Decimal | None
    quantity_after: Decimal | None
    cost_basis_before: Decimal | None
    cost_basis_after: Decimal | None
    transaction_id: str
    restated: bool
    resulting_position_id: str | None = None
    resulting_asset_id: str | None = None
    resulting_quantity: Decimal | None = None
    resulting_cost_basis: Decimal | None = None
    resulting_transaction_id: str | None = None


@dataclass(frozen=True)
class SkippedPosition:
    """A current position on an affected asset that was NOT adjusted, and why.

    Reported rather than silently dropped: "12 positions affected" out of a
    holding of 14 is a number somebody has to be able to reconcile.
    """

    position_id: str
    asset_id: str
    ownership_basis: str
    reason: str


@dataclass(frozen=True)
class ApplyOutcome:
    """What one org's apply call did.

    ``already_applied`` is NOT an error state and NOT the same as
    ``positions_affected == 0``. An org that holds none of the security reports
    zero affected and ``already_applied=False``; an org that applied it an hour
    ago reports zero affected and ``already_applied=True``, plus the transaction
    ids that prove it. A caller that could not tell those apart would re-run the
    first case forever and skip the second case's audit trail.
    """

    corporate_action_id: str
    org_id: str
    action_type: str
    positions_affected: int
    already_applied: bool
    assets_matched: tuple[str, ...] = ()
    adjusted: tuple[AdjustedPosition, ...] = ()
    skipped: tuple[SkippedPosition, ...] = ()
    prior_transaction_ids: tuple[str, ...] = ()
    resulting_asset_id: str | None = None
    resulting_asset_created: bool = False
    unapplied_terms: Mapping[str, Any] = field(default_factory=dict)

    @property
    def applied(self) -> bool:
        """True when THIS call changed something."""
        return self.positions_affected > 0


# ── Record — global, Super-Admin-gated ──────────────────────────────────────


async def record_corporate_action(
    conn,
    *,
    global_security_id: str,
    action_type: str,
    ex_date: date,
    terms: Mapping[str, Any] | str,
    resulting_global_security_id: str | None = None,
    record_date: date | None = None,
    pay_date: date | None = None,
    source_system: str | None = None,
    is_super_admin: bool = False,
) -> str:
    """Record one corporate action, globally. Returns its id.

    Composes A1's conventions rather than inventing new ones: the same
    :func:`~services.securities_global._require_super_admin` app-layer gate for a
    legible refusal, the same :class:`~services.securities_global.
    _SuperAdminWrite` transaction-local elevation so RLS is the real gate, and
    the same merge-chain resolution ``add_price`` uses — an action aimed at a
    security that was later merged away is recorded against the survivor, because
    that is the security every holder's asset will resolve to.

    ``source_system`` is free text here and is NOT checked against A2's
    ``SOURCE_SYSTEMS``. That vocabulary is the deployed ``positions_source_chk``,
    which enumerates *ingestion integrations this tenant platform has*; the
    publisher of a corporate action is a market-data vendor, and there is no
    CHECK on this column.
    """
    _require_super_admin(is_super_admin, "record_corporate_action")

    if action_type not in ACTION_TYPES:
        raise CorporateActionError(
            f"action_type={action_type!r} is not one of {sorted(ACTION_TYPES)} "
            f"(deployed constraint corp_actions_type_chk)"
        )
    if not isinstance(ex_date, date):
        raise CorporateActionError(
            f"ex_date must be a datetime.date — got {type(ex_date).__name__}"
        )
    for name, value in (("record_date", record_date), ("pay_date", pay_date)):
        if value is not None and not isinstance(value, date):
            raise CorporateActionError(
                f"{name} must be a datetime.date or None — got "
                f"{type(value).__name__}"
            )
    payload = _coerce_terms(terms)

    if action_type in _REQUIRES_RESULTING_SECURITY and not resulting_global_security_id:
        raise CorporateActionError(
            f"action_type={action_type!r} requires resulting_global_security_id: "
            f"it creates a holding in a DIFFERENT security, and an apply that "
            f"discovered the referent was missing would already have restated "
            f"the original position by then."
        )

    async with _SuperAdminWrite(conn) as c:
        target = await _resolve_canonical(c, global_security_id, "global_security_id")
        resulting = None
        if resulting_global_security_id:
            resulting = await _resolve_canonical(
                c, resulting_global_security_id, "resulting_global_security_id"
            )
            if resulting == target:
                raise CorporateActionError(
                    f"resulting_global_security_id resolves to the same security "
                    f"as global_security_id ({target}). A spinoff that resulted "
                    f"in itself would have every holder create a duplicate "
                    f"position in the security they already hold."
                )

        return await c.fetchval(
            f"""
            INSERT INTO {TABLE_CORP_ACTIONS}
                (global_security_id, resulting_global_security_id, action_type,
                 ex_date, record_date, pay_date, terms, source_system)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::jsonb, $8)
            RETURNING id::text
            """,
            target, resulting, action_type, ex_date, record_date, pay_date,
            json.dumps(payload), source_system,
        )


async def _resolve_canonical(conn, global_security_id: str, field_name: str) -> str:
    """Forward a security id through the merge chain, as ``add_price`` does."""
    resolved = await conn.fetchval(
        f"""
        SELECT COALESCE(s.canonical_id, s.id)::text
        FROM {TABLE_SEC} s
        WHERE s.id = $1::uuid
        """,
        str(global_security_id),
    )
    if resolved is None:
        raise CorporateActionError(
            f"{field_name}: security {global_security_id} does not exist"
        )
    return resolved


async def get_corporate_action(conn, corporate_action_id: str) -> dict[str, Any] | None:
    """Read one recorded action. Unconditional — the table is global-read.

    ``terms`` is returned as a parsed ``dict``: asyncpg hands back ``jsonb`` as a
    string, and a caller doing ``row['terms']['ratio']`` on that gets a
    ``TypeError`` about string indices rather than anything about JSON.
    """
    row = await conn.fetchrow(
        f"""
        SELECT id::text AS id,
               global_security_id::text AS global_security_id,
               resulting_global_security_id::text AS resulting_global_security_id,
               action_type, ex_date, record_date, pay_date, terms,
               source_system, applied_at
        FROM {TABLE_CORP_ACTIONS} ca
        WHERE ca.id = $1::uuid AND {_current('ca')}
        """,
        str(corporate_action_id),
    )
    if row is None:
        return None
    out = dict(row)
    if isinstance(out["terms"], str):
        out["terms"] = json.loads(out["terms"])
    return out


# ── Apply — tenant-scoped ───────────────────────────────────────────────────


async def find_affected_assets(
    conn, org_id: str, global_security_id: str
) -> list[dict[str, Any]]:
    """Every CURRENT asset in this org tied to a global security. Task 1d.

    The join is on ``COALESCE(s.canonical_id, s.id)``, not on
    ``a.global_security_id = $2`` directly. An org whose asset still points at a
    duplicate that A1 later merged away holds the same real security, and a
    split of it splits their shares too. Matching on the raw id would leave
    exactly those orgs un-adjusted, silently, with no row anywhere recording that
    they were skipped.

    Read under the caller's org context by :func:`_apply`, so RLS is what limits
    it to one tenant; the explicit ``a.org_id = $1`` is the second lock, not the
    first.
    """
    rows = await conn.fetch(
        f"""
        SELECT a.id::text AS id, a.name, a.ownership_basis, a.valuation_method,
               a.currency_code, a.asset_type, a.asset_class,
               a.default_taxonomy_key
        FROM {TABLE_ASSETS} a
        JOIN {TABLE_SEC} s ON s.id = a.global_security_id
        WHERE a.org_id = $1::uuid
          AND COALESCE(s.canonical_id, s.id) = $2::uuid
          AND {_current('a')}
        ORDER BY a.id
        """,
        str(org_id), str(global_security_id),
    )
    return [dict(r) for r in rows]


async def already_applied_transactions(
    conn, org_id: str, corporate_action_id: str
) -> list[str]:
    """The adjustment transactions THIS org already has for this action.

    This is the idempotency key, and it is deliberately
    ``(org_id, corporate_action_id)`` rather than anything involving a position
    id: applying a split closes the position row and mints a new one, so a
    position-keyed check would be looking for a marker on a row that no longer
    exists and would happily double-adjust.

    ``is_corporate_action_adjustment`` is required in the predicate as well as
    ``corporate_action_id``. A cash-in-lieu sale citing the same action is a real
    trade, not this module's marker, and counting it would make a second apply
    look already-done.
    """
    rows = await conn.fetch(
        f"""
        SELECT t.id::text AS id
        FROM {TABLE_TRANSACTIONS} t
        WHERE t.org_id = $1::uuid
          AND t.corporate_action_id = $2::uuid
          AND t.is_corporate_action_adjustment
          AND {_current('t')}
        ORDER BY t.id
        """,
        str(org_id), str(corporate_action_id),
    )
    return [r["id"] for r in rows]


async def _current_positions(conn, org_id: str, asset_ids: Sequence[str]):
    """Every current position in this org on any of these assets.

    Includes rows carrying a ``superseded_by_source``. A precedence loser is
    still a current row, and leaving it at its pre-split quantity means the day
    the org re-orders its sources — or the winning feed is corrected away —
    ``resolve_precedence`` promotes a number that is wrong by the split ratio.
    """
    if not asset_ids:
        return []
    return await conn.fetch(
        f"""
        SELECT p.id::text AS id, p.owner_entity_id::text AS owner_entity_id,
               p.asset_id::text AS asset_id, p.as_of_date, p.ownership_basis,
               p.quantity, p.ownership_pct, p.cost_basis, p.market_value,
               p.market_value_native, p.fx_rate_id::text AS fx_rate_id,
               p.accrued_income, p.authority, p.source_system, p.taxonomy_key,
               p.is_reconciled, p.superseded_by_source
        FROM {TABLE_POSITIONS} p
        WHERE p.org_id = $1::uuid
          AND p.asset_id = ANY($2::uuid[])
          AND {_current('p')}
        ORDER BY p.id
        """,
        str(org_id), [str(a) for a in asset_ids],
    )


async def _close_position(conn, org_id: str, position_id: str) -> None:
    """Bi-temporal close — CLAUDE.md Rule 3, step 1.

    The ONLY ``UPDATE`` in this module. It writes ``valid_to`` and nothing else:
    a measure is never rewritten in place, it is superseded by the row
    ``create_position`` inserts next.

    ``AND valid_to IS NULL`` in the predicate is not decoration. Without it, a
    concurrent apply that already closed the row would push ``valid_to`` forward
    a second time and the two restatements would overlap in valid time.
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
        raise CorporateActionError(
            f"position {position_id} was not a current row in org {org_id} at "
            f"close time — it was closed or corrected concurrently. The apply is "
            f"rolled back rather than restating a superseded row."
        )


async def _restate_position(
    conn,
    org_id: str,
    row: Mapping[str, Any],
    *,
    quantity: Decimal | None,
    cost_basis: Decimal | None,
) -> str:
    """Close the current position row and insert its restated successor.

    Everything not named by the corporate action is carried across verbatim —
    ``authority``, ``source_system``, ``taxonomy_key``, ``superseded_by_source``,
    ``as_of_date``, ``accrued_income``, ``fx_rate_id``. The restatement says what
    the split changed, and a field silently reset to a default here would be
    indistinguishable, afterwards, from the source having stopped reporting it.

    ``market_value`` and ``market_value_native`` are carried across UNCHANGED,
    which is the correct answer and not an omission: a split multiplies the share
    count and divides the price, and the holding is worth exactly what it was
    worth a moment earlier. Recomputing it from a price series would introduce a
    number the corporate action did not publish.
    """
    await _close_position(conn, org_id, row["id"])
    return await create_position(
        conn,
        org_id=org_id,
        owner_entity_id=row["owner_entity_id"],
        asset_id=row["asset_id"],
        as_of_date=row["as_of_date"],
        authority=row["authority"],
        source_system=row["source_system"],
        ownership_basis=row["ownership_basis"],
        quantity=quantity,
        ownership_pct=row["ownership_pct"],
        market_value=row["market_value"],
        market_value_native=row["market_value_native"],
        cost_basis=cost_basis,
        accrued_income=row["accrued_income"],
        fx_rate_id=row["fx_rate_id"],
        taxonomy_key=row["taxonomy_key"],
        is_reconciled=bool(row["is_reconciled"]),
        superseded_by_source=row["superseded_by_source"],
    )


async def _record_adjustment(
    conn,
    org_id: str,
    *,
    position_id: str,
    corporate_action_id: str,
    trade_date: date,
    quantity_delta: Decimal | None,
    authority: str,
    source_system: str,
    note: str,
) -> str:
    """Write the adjustment transaction through A2's real ``record_transaction``.

    ``price``, ``gross_amount``, ``net_amount``, ``fees`` and ``taxes`` are all
    left NULL rather than set to zero. No cash moved, and a stored ``0.00`` is
    indistinguishable from a genuine zero-dollar trade once it has been summed —
    the same reason A2's ``AssetValue.value`` is ``None`` and never
    ``Decimal(0)`` for a missing mark.
    """
    return await record_transaction(
        conn,
        org_id=org_id,
        position_id=position_id,
        transaction_type_code=ADJUSTMENT_TYPE_CODE,
        trade_date=trade_date,
        authority=authority,
        source_system=source_system,
        quantity=quantity_delta,
        external_ref=f"corporate_action:{corporate_action_id}:{note}",
        corporate_action_id=corporate_action_id,
        is_corporate_action_adjustment=True,
    )


def _partition(assets, positions) -> tuple[list, list[SkippedPosition]]:
    """Split current positions into "a split acts on this" and "it does not".

    Only a ``units`` position has a quantity for a ratio to multiply. A
    ``percent`` position's authoritative measure is an ownership percentage,
    which a share split does not change — the holder owns the same fraction of
    the same company before and after. A ``value`` position's authoritative
    measure is a currency amount, likewise unchanged.

    Both are SKIPPED, not failed and not silently adjusted, and each one comes
    back in :attr:`ApplyOutcome.skipped` with its reason.
    """
    by_asset = {a["id"]: a for a in assets}
    actionable, skipped = [], []
    for p in positions:
        if p["ownership_basis"] != UNITS:
            skipped.append(SkippedPosition(
                position_id=p["id"], asset_id=p["asset_id"],
                ownership_basis=p["ownership_basis"],
                reason=(
                    f"ownership_basis={p['ownership_basis']!r}: the authoritative "
                    f"measure is not a share count, and a share ratio does not "
                    f"change a percentage of ownership or a stated value"
                ),
            ))
        elif p["quantity"] is None:
            # `create_position` cannot produce this, but an ingestion path that
            # wrote SQL directly could, and multiplying None raises a TypeError
            # eight frames away from the row that caused it.
            skipped.append(SkippedPosition(
                position_id=p["id"], asset_id=p["asset_id"],
                ownership_basis=p["ownership_basis"],
                reason="ownership_basis='units' but quantity IS NULL",
            ))
        else:
            actionable.append((p, by_asset[p["asset_id"]]))
    return actionable, skipped


async def _load_for_apply(conn, corporate_action_id: str, expected):
    """Shared preamble: read the action, check it is the expected kind."""
    action = await get_corporate_action(conn, corporate_action_id)
    if action is None:
        raise CorporateActionError(
            f"corporate action {corporate_action_id} does not exist"
        )
    if action["action_type"] not in expected:
        raise CorporateActionError(
            f"corporate action {corporate_action_id} is a "
            f"{action['action_type']!r}, not one of {sorted(expected)}. Use "
            f"apply_corporate_action() to dispatch on the recorded type rather "
            f"than assuming it."
        )
    return action


async def apply_split(
    conn,
    org_id: str,
    corporate_action_id: str,
    *,
    authority: str = DEFAULT_AUTHORITY,
    source_system: str = DEFAULT_SOURCE_SYSTEM,
) -> ApplyOutcome:
    """Apply a recorded split or reverse split to ONE org's own positions.

    Quantity is multiplied by the published ratio. **Total cost basis is left
    exactly as it was**, which IS the "unit cost divided by the ratio" the design
    asks for: ``portfolio.positions.cost_basis`` is the total, so 100 shares at a
    $5,000 basis becoming 200 shares at a $5,000 basis is precisely a per-unit
    cost of $50 becoming $25. Recomputing it as ``new_quantity × (old_unit_cost ÷
    ratio)`` would produce the same number in the 2:1 case and a number ending in
    a rounding artefact for every three-way split — and the invariant that
    matters, the one an accountant checks, is that the total did not move.

    Idempotent on ``(org_id, corporate_action_id)``. An org that holds none of
    the security reports zero positions affected and does not raise.
    """
    return await _apply(
        conn, org_id, corporate_action_id,
        expected=frozenset({SPLIT, REVERSE_SPLIT}),
        authority=authority, source_system=source_system,
    )


async def apply_spinoff(
    conn,
    org_id: str,
    corporate_action_id: str,
    *,
    authority: str = DEFAULT_AUTHORITY,
    source_system: str = DEFAULT_SOURCE_SYSTEM,
) -> ApplyOutcome:
    """Apply a recorded spinoff to ONE org's own positions.

    Two sides, one transaction:

    * the ORIGINAL position keeps its share count — a spinoff does not change the
      parent share count — and has its cost basis reduced to the published
      allocation, ``terms['cost_basis_allocation_pct_original']`` (the issuer's
      Form 8937 number). If the terms do not carry an allocation, the original
      basis is left ALONE and the resulting position's basis is ``NULL``, not
      zero: an unpublished allocation is unknown, and a zero-basis lot makes the
      entire proceeds a gain the first time it is sold;
    * a NEW position in ``resulting_global_security_id`` for the SAME
      ``owner_entity_id``, quantity = parent quantity × ``distribution_ratio``,
      created on a tenant asset that is minted for this org if it does not
      already have one referencing that global security.

    Both sides commit together — see the module docstring on atomicity. An org
    holding the spinoff shares with the parent's basis untouched has been told
    its portfolio grew by the value of the spinoff, which it did not.

    ``cash_in_lieu_per_share`` is read, reported in
    :attr:`ApplyOutcome.unapplied_terms`, and deliberately NOT turned into a cash
    movement; see the module docstring.
    """
    return await _apply(
        conn, org_id, corporate_action_id,
        expected=frozenset({SPINOFF}),
        authority=authority, source_system=source_system,
    )


async def apply_corporate_action(
    conn,
    org_id: str,
    corporate_action_id: str,
    *,
    authority: str = DEFAULT_AUTHORITY,
    source_system: str = DEFAULT_SOURCE_SYSTEM,
) -> ApplyOutcome:
    """Dispatch on the RECORDED ``action_type``.

    Raises :class:`UnapplicableActionError` — by name, with the type in the
    message — for ``merger``, ``tender``, ``delisting``, ``name_change`` and
    ``cusip_change``. Those are recordable and out of this sprint's scope, and
    returning a clean zero-affected outcome for them would be
    indistinguishable from "this org holds none of it", which is the one thing a
    caller most needs to be able to tell apart.
    """
    action = await get_corporate_action(conn, corporate_action_id)
    if action is None:
        raise CorporateActionError(
            f"corporate action {corporate_action_id} does not exist"
        )
    action_type = action["action_type"]
    if action_type not in APPLICABLE_ACTION_TYPES:
        raise UnapplicableActionError(
            f"corporate action {corporate_action_id} is a {action_type!r}. It is "
            f"recorded, and reading it is supported, but Phase F implements "
            f"application for {sorted(APPLICABLE_ACTION_TYPES)} only. Applying "
            f"a {action_type!r} needs terms this module would have to invent."
        )
    fn = apply_spinoff if action_type == SPINOFF else apply_split
    return await fn(
        conn, org_id, corporate_action_id,
        authority=authority, source_system=source_system,
    )


async def _apply(
    conn,
    org_id: str,
    corporate_action_id: str,
    *,
    expected: frozenset[str],
    authority: str,
    source_system: str,
) -> ApplyOutcome:
    """The one apply path. ONE transaction, org context raised once.

    ``org_id`` comes from the caller (a router reads it from JWT claims, never
    from a request body — A2's ``_require_org`` says so and this reuses it). The
    outer transaction is opened HERE rather than left to A2's ``_OrgWrite``,
    because ``_OrgWrite`` commits per call: a spinoff running through it would
    commit the parent's restated basis and then, if the resulting-side insert
    failed, leave the org with half an event and no error visible in the data.
    Inside an outer transaction those nest as savepoints and the whole apply is
    one commit.
    """
    org_id = _require_org(org_id)
    if authority not in AUTHORITIES:
        raise CorporateActionError(
            f"authority={authority!r} is not one of {sorted(AUTHORITIES)}"
        )
    if source_system not in SOURCE_SYSTEMS:
        raise CorporateActionError(
            f"source_system={source_system!r} is not one of "
            f"{sorted(SOURCE_SYSTEMS)}"
        )

    async with _OrgWrite(conn, org_id) as c:
        action = await _load_for_apply(c, corporate_action_id, expected)
        action_type = action["action_type"]
        terms = action["terms"]
        unapplied = {
            k: terms[k] for k in _UNAPPLIED_TERMS_KEYS if k in terms
        }

        prior = await already_applied_transactions(c, org_id, corporate_action_id)
        if prior:
            # Deliberately BEFORE any read of positions and any parse of terms.
            # A second apply must be cheap and must not be able to fail on terms
            # that the first apply already consumed successfully.
            return ApplyOutcome(
                corporate_action_id=corporate_action_id, org_id=org_id,
                action_type=action_type, positions_affected=0,
                already_applied=True, prior_transaction_ids=tuple(prior),
                unapplied_terms=unapplied,
            )

        assets = await find_affected_assets(c, org_id, action["global_security_id"])
        positions = await _current_positions(c, org_id, [a["id"] for a in assets])
        actionable, skipped = _partition(assets, positions)

        if not actionable:
            # Zero affected, cleanly. NOT an error, and NOT already_applied —
            # see ApplyOutcome's docstring on why the caller must be able to tell
            # this case from the one above.
            return ApplyOutcome(
                corporate_action_id=corporate_action_id, org_id=org_id,
                action_type=action_type, positions_affected=0,
                already_applied=False,
                assets_matched=tuple(a["id"] for a in assets),
                skipped=tuple(skipped), unapplied_terms=unapplied,
            )

        if action_type == SPINOFF:
            adjusted, resulting_asset_id, created = await _apply_spinoff_rows(
                c, org_id, action, actionable,
                authority=authority, source_system=source_system,
            )
        else:
            adjusted = await _apply_split_rows(
                c, org_id, action, actionable,
                authority=authority, source_system=source_system,
            )
            resulting_asset_id, created = None, False

        return ApplyOutcome(
            corporate_action_id=corporate_action_id, org_id=org_id,
            action_type=action_type, positions_affected=len(adjusted),
            already_applied=False,
            assets_matched=tuple(a["id"] for a in assets),
            adjusted=tuple(adjusted), skipped=tuple(skipped),
            resulting_asset_id=resulting_asset_id,
            resulting_asset_created=created,
            unapplied_terms=unapplied,
        )


async def _apply_split_rows(conn, org_id, action, actionable, *, authority, source_system):
    """Restate each actionable position for a split / reverse split."""
    ratio = parse_ratio(action["terms"].get(TERMS_RATIO), TERMS_RATIO)
    ex_date = action["ex_date"]
    out: list[AdjustedPosition] = []

    for row, _asset in actionable:
        qty_before = row["quantity"]
        qty_after = ratio.apply(qty_before)
        cost = row["cost_basis"]  # Unchanged. See apply_split's docstring.

        new_position_id = await _restate_position(
            conn, org_id, row, quantity=qty_after, cost_basis=cost,
        )
        txn_id = await _record_adjustment(
            conn, org_id,
            position_id=new_position_id,
            corporate_action_id=action["id"],
            trade_date=ex_date,
            quantity_delta=qty_after - qty_before,
            authority=authority, source_system=source_system,
            note=f"{action['action_type']}:{ratio}",
        )
        out.append(AdjustedPosition(
            asset_id=row["asset_id"], owner_entity_id=row["owner_entity_id"],
            original_position_id=row["id"], position_id=new_position_id,
            quantity_before=qty_before, quantity_after=qty_after,
            cost_basis_before=cost, cost_basis_after=cost,
            transaction_id=txn_id, restated=True,
        ))
    return out


async def _apply_spinoff_rows(conn, org_id, action, actionable, *, authority, source_system):
    """Restate each parent position and create its resulting-security side."""
    terms = action["terms"]
    dist = parse_ratio(terms.get(TERMS_DISTRIBUTION_RATIO), TERMS_DISTRIBUTION_RATIO)
    retained_pct = (
        _percent(terms[TERMS_COST_BASIS_PCT], TERMS_COST_BASIS_PCT)
        if terms.get(TERMS_COST_BASIS_PCT) is not None
        else None
    )
    ex_date = action["ex_date"]
    resulting_security_id = action["resulting_global_security_id"]
    if not resulting_security_id:
        raise CorporateActionError(
            f"corporate action {action['id']} is a spinoff with no "
            f"resulting_global_security_id. It cannot be applied — there is "
            f"nothing to create a position in."
        )

    resulting_asset_id, created = await _ensure_resulting_asset(
        conn, org_id, resulting_security_id, actionable[0][1],
    )

    out: list[AdjustedPosition] = []
    for row, _asset in actionable:
        qty = row["quantity"]
        cost_before = row["cost_basis"]

        # A spinoff does not change the parent share count. Only basis moves,
        # and only if the issuer published an allocation.
        if retained_pct is not None and cost_before is not None:
            cost_after = cost_before * retained_pct / Decimal(100)
            resulting_cost = cost_before - cost_after
            parent_position_id = await _restate_position(
                conn, org_id, row, quantity=qty, cost_basis=cost_after,
            )
            restated = True
        else:
            # Nothing about the parent row changed, so it is NOT closed and
            # re-inserted: a bi-temporal restatement that restates nothing is a
            # lie about when the holding last changed.
            cost_after = cost_before
            resulting_cost = None
            parent_position_id = row["id"]
            restated = False

        parent_txn_id = await _record_adjustment(
            conn, org_id,
            position_id=parent_position_id,
            corporate_action_id=action["id"],
            trade_date=ex_date,
            # The parent's share count is unchanged, so the delta is NULL rather
            # than 0 — "no units moved on this leg", not "zero units traded".
            quantity_delta=None,
            authority=authority, source_system=source_system,
            note="spinoff:original",
        )

        resulting_qty = dist.apply(qty)
        resulting_position_id = await create_position(
            conn,
            org_id=org_id,
            owner_entity_id=row["owner_entity_id"],
            asset_id=resulting_asset_id,
            as_of_date=row["as_of_date"],
            authority=authority,
            source_system=source_system,
            ownership_basis=UNITS,
            quantity=resulting_qty,
            cost_basis=resulting_cost,
            # market_value is left NULL, never 0: the spinoff publishes a share
            # count and a basis allocation, not a price. A zero here would be
            # summed into every rollup as a real zero-value holding.
            taxonomy_key=row["taxonomy_key"],
        )
        resulting_txn_id = await _record_adjustment(
            conn, org_id,
            position_id=resulting_position_id,
            corporate_action_id=action["id"],
            trade_date=ex_date,
            quantity_delta=resulting_qty,
            authority=authority, source_system=source_system,
            note=f"spinoff:resulting:{dist}",
        )

        out.append(AdjustedPosition(
            asset_id=row["asset_id"], owner_entity_id=row["owner_entity_id"],
            original_position_id=row["id"], position_id=parent_position_id,
            quantity_before=qty, quantity_after=qty,
            cost_basis_before=cost_before, cost_basis_after=cost_after,
            transaction_id=parent_txn_id, restated=restated,
            resulting_position_id=resulting_position_id,
            resulting_asset_id=resulting_asset_id,
            resulting_quantity=resulting_qty,
            resulting_cost_basis=resulting_cost,
            resulting_transaction_id=resulting_txn_id,
        ))
    return out, resulting_asset_id, created


async def _ensure_resulting_asset(
    conn, org_id: str, resulting_security_id: str, template: Mapping[str, Any]
) -> tuple[str, bool]:
    """Find or create THIS org's tenant asset for the resulting security.

    Returns ``(asset_id, created)``. Found once per apply call and reused across
    every position, so an org with six holders of the parent gets ONE spinco
    asset and six positions on it — not six assets.

    When it has to be created, the shape is taken from the global security (its
    name, its ``security_type`` as the open-text ``asset_type``, its currency)
    and the ``valuation_method`` / ``asset_class`` from the PARENT asset. That
    last part is the deliberate one: a spinoff out of a listed equity is a listed
    equity, and defaulting it to ``market_price`` when the parent was carried at
    ``nav`` would flip the new asset into the public-market bucket and make
    ``record_transaction`` reject the very adjustment we are about to write.
    """
    existing = await find_affected_assets(conn, org_id, resulting_security_id)
    if existing:
        return existing[0]["id"], False

    sec = await conn.fetchrow(
        f"""
        SELECT s.name, s.security_type, s.currency_code
        FROM {TABLE_SEC} s
        WHERE s.id = $1::uuid
        """,
        str(resulting_security_id),
    )
    if sec is None:
        raise CorporateActionError(
            f"resulting security {resulting_security_id} does not exist"
        )

    asset_id = await create_asset(
        conn,
        org_id=org_id,
        name=sec["name"],
        asset_type=(sec["security_type"] or template["asset_type"]),
        asset_class=template["asset_class"],
        ownership_basis=UNITS,
        valuation_method=template["valuation_method"],
        global_security_id=resulting_security_id,
        currency_code=sec["currency_code"] or template["currency_code"],
        default_taxonomy_key=template["default_taxonomy_key"],
    )
    return asset_id, True


# Re-exported so a caller catching permission failures does not have to import
# from A1 to name the exception this module's Super-Admin gate raises.
__all__ = [
    "ACTION_TYPES", "APPLICABLE_ACTION_TYPES", "ADJUSTMENT_TYPE_CODE",
    "TABLE_CORP_ACTIONS", "SPLIT", "REVERSE_SPLIT", "SPINOFF",
    "TERMS_RATIO", "TERMS_DISTRIBUTION_RATIO", "TERMS_COST_BASIS_PCT",
    "TERMS_CASH_IN_LIEU",
    "Ratio", "parse_ratio",
    "AdjustedPosition", "SkippedPosition", "ApplyOutcome",
    "CorporateActionError", "UnapplicableActionError",
    "SecuritiesGlobalError", "SecuritiesGlobalPermissionError",
    "record_corporate_action", "get_corporate_action",
    "find_affected_assets", "already_applied_transactions",
    "apply_split", "apply_spinoff", "apply_corporate_action",
]
