"""Vendor cost model — providers, rate cards, pass-through policy. fee37.

This is the FIRST sprint to build the cost side of the ledger. Everything
before it (fee31–fee36) computes what a client is charged; nothing computed
what the firm PAYS to earn it. Four tables landed for this sprint
(``cost_providers``, ``cost_schedules``, ``cost_pass_through_policies``,
``cost_events``) and this module is the only code that writes them.

Scope boundaries that matter, because two neighbouring sprints will want to
reach in here:

  * fee38 (Altruist One evaluator) CONSUMES this module's rate card. It is not
    built here. Nothing in this file decides whether a subscription is worth
    buying; it only records what the subscription costs.
  * fee39 (revenue_events, profitability rollup) does not exist yet.
    ``public.revenue_events`` is not a deployed table — measured, not assumed —
    which is why ``cost_events.linked_revenue_event_id`` carries NO foreign
    key. :func:`record_cost_event` therefore returns the implied revenue as a
    NUMBER and writes no revenue row. When fee39 lands it adds the FK and
    back-fills; until then a revenue write here would be a dangling id.
  * Nothing here is Altruist-API-shaped. The rate card is DATA, typed in from
    a document. There is no live connection and no scraper.


THE RATE CARD IS UNVERIFIED. TREAT IT THAT WAY.
──────────────────────────────────────────────────────────────────────────────
Every figure in :data:`ALTRUIST_SCHEDULES` comes from the original design-doc
research, which was itself conducted without live web access. It may have
drifted. A re-check against altruist.com was ATTEMPTED during this sprint and
could not be performed (no outbound web access was available to the sprint
either), so the numbers are exactly as inherited.

``source_verified_on`` is therefore populated with the date this sprint ENTERED
the row, which is the only honest claim available. It does NOT mean anyone
re-read Altruist's pricing page that day. :func:`seed_altruist_profile` takes
``source_verified_on`` as an explicit argument precisely so that a human who
does perform a real re-check can re-stamp it and mean it.

Do not put these rates in front of a client, and do not bill from them, until
someone has re-read the source and moved that date.


THE AMBIGUITIES ARE SEEDED AS AMBIGUITIES, NOT RESOLVED BY GUESSING
──────────────────────────────────────────────────────────────────────────────
Three genuine unknowns survived the original research. The deployed schema can
express two of them as alternate rows, so they are seeded that way rather than
silently collapsed:

1.  **Altruist One subscription: floor or addition?** The card reads "0.01%
    per month per household, minimum $1/month per account". Whether the $1 is
    a floor under the bps (``max(bps, 1)``) or a separate charge on top
    (``bps + 1``) was never settled. Both readings are seeded, under
    ``ALTRUIST_ONE_SUB_FLOOR`` and the pair ``ALTRUIST_ONE_SUB_ADDITIVE_BPS`` /
    ``ALTRUIST_ONE_SUB_ADDITIVE_PER_ACCOUNT``. They are MUTUALLY EXCLUSIVE —
    see :data:`AMBIGUITY_GROUPS` and :func:`ambiguous_cost_codes`, which exist
    so a consumer that naively sums every schedule for the provider can be
    caught doing it. A caller must pick a reading.

    The floor reading also runs into a real expressiveness gap:
    ``cost_schedules.minimum_amount`` sits on a row whose ``applies_scope`` is
    HOUSEHOLD, but the minimum it is meant to carry is PER ACCOUNT. The column
    cannot say "per account minimum on a household-scoped rate". The seeded
    row records the number; the per-account application of it has to live in
    fee38's evaluator, and is flagged rather than faked.

2.  **Model marketplace paid tier is a RANGE**, 10–15 bps, not a number. Seeded
    as two rows (``..._PAID_LOW`` / ``..._PAID_HIGH``) so the band survives.
    Together with the 0 bps included tier that is three rows, where the sprint
    prompt asked for "two, not one" — the included and paid tiers are indeed
    different cost structures, and the paid tier is additionally a range.

    The card's "or up to 15 bps DISCOUNT under Altruist One" is deliberately
    NOT seeded. A negative-rate row in a table named ``cost_schedules`` sums
    destructively against its siblings, and the discount's base (is it off the
    10–15 bps, or off the whole bill?) is unstated. Recording a number nobody
    can interpret is worse than recording the open question.

3.  **Margin spread is tiered**, 6.25% non-subscriber against a 4.00–5.25%
    Altruist One ladder. There is no ``cost_schedule_tiers`` table — measured;
    ``fee_schedule_tiers`` exists but belongs to the revenue side and has a FK
    to ``fee_schedules`` — so the ladder is seeded as its endpoints rather than
    collapsed to one number.

And one modelling question that is NOT seeded at all:

4.  **CASH_SPREAD does not belong in ``cost_schedules``.** It is a yield
    UPLIFT — money the arrangement EARNS, not money the firm pays. The
    deployed schema agrees, in a way worth spelling out: ``cost_events``'
    ``cost_type`` CHECK admits ten values and none of them is a spread or a
    yield. A positive ``SPREAD_ON_BALANCE`` rate filed under "cost" would be
    read as an expense by every downstream sum. It is left for fee38's
    evaluator inputs. See :data:`UNSEEDED_RATE_CARD_ITEMS`.

    ``MARGIN_SPREAD`` has the same problem one step removed and IS seeded,
    because the prompt asked for it: it is charged by the custodian to the
    CLIENT, so it is not a firm expense either, and it likewise has no
    ``cost_type`` it can legally become. The rows are a rate card the evaluator
    can read; they cannot currently produce a ``cost_event``.


PRECEDENCE, AND THE COLUMN THAT ISN'T THERE
──────────────────────────────────────────────────────────────────────────────
``fee_assignments`` stores an integer ``precedence`` and fee34 derives it from
``scope_type`` on insert so a caller cannot invert it.
``cost_pass_through_policies`` has NO such column — measured. So precedence
here is derived in the SELECT itself, from ``scope_type``, and there is
nothing stored for anyone to corrupt. Same ordering, one scope narrower:

    ACCOUNT 10 < BILLING_GROUP 20 < HOUSEHOLD 30 < ORG_DEFAULT 40

``fee_assignments`` also admits ENTITY; ``cost_pass_through_policies`` does
not. Its CHECK is ``ACCOUNT, HOUSEHOLD, BILLING_GROUP, ORG_DEFAULT``. The two
vocabularies are NOT the same and must not be shared —
:data:`SCOPE_PRECEDENCE` is asserted at import against the deployed list so
that a future widening of one table cannot silently apply to the other.


THE MARKUP DISCLOSURE GATE, AND WHY THE DATABASE'S CHECK ISN'T ONE
──────────────────────────────────────────────────────────────────────────────
The deployed constraint is::

    CHECK ((policy <> 'MARKUP') OR (disclosure_required = true))

That forces a FLAG, not an ACKNOWLEDGEMENT. A MARKUP policy inserts perfectly
happily with ``disclosure_required = true`` and both
``disclosure_acknowledged_by`` and ``disclosure_acknowledged_at`` NULL — which
is the exact state the rule is supposed to prevent: marking up a client's
vendor cost with nobody on record as having disclosed it.

So the real gate is :func:`_assert_markup_disclosed`, enforced in
:func:`create_pass_through_policy`. And note what "active" means here, because
it has no direct representation: ``cost_pass_through_policies`` has no
``status`` column. A policy is active by virtue of being a current row
(``valid_to IS NULL AND system_to IS NULL``) inside its effective window.
Creation IS activation. There is no draft state to gate separately, so the
gate lives at insert, and at every path that re-opens or extends an effective
window.


MONEY
──────────────────────────────────────────────────────────────────────────────
``cost_events.amount`` is ``numeric(20,4)``; a bill line is cents. Those are
different precisions and the difference is not cosmetic. A cost is recorded at
four decimal places, matching its column, so the database never silently
rounds something the firm actually owes. The implied revenue is quantized to
cents, once, at the end, because it is a number that becomes a charge.

Passing through a $0.12345 cost at 100% therefore bills $0.12 and leaves
$0.00345 the firm eats. That residual is returned as
:attr:`PassThroughOutcome.residual_absorbed` rather than discarded — it is
small per event and not small across a million of them, and fee39's
profitability rollup needs to see it to reconcile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Sequence
from uuid import UUID

from services.portfolio_assets import _OrgWrite, _require_org

TABLE_PROVIDERS = "public.cost_providers"
TABLE_SCHEDULES = "public.cost_schedules"
TABLE_POLICIES = "public.cost_pass_through_policies"
TABLE_EVENTS = "public.cost_events"
TABLE_ACCOUNTS = "public.accounts"
TABLE_BILLING_GROUP_MEMBERS = "public.billing_group_members"
TABLE_BILLING_GROUPS = "public.billing_groups"

#: Same pair fee33/fee34 settled on. Deciding what a vendor cost does to a
#: client's bill is a billing authority, not a portfolio one.
READ_PERMISSION = "view_portfolio"
WRITE_PERMISSION = "manage_billing"

ZERO = Decimal("0")
ONE = Decimal("1")
CENTS = Decimal("0.01")
#: ``cost_events.amount`` is numeric(20,4). Quantizing to the column's own
#: scale means the database never has to round a number the firm owes.
COST_Q = Decimal("0.0001")


# ═══════════════════════════════════════════════════════════════════════════
# Deployed vocabularies — mirrored from the CHECK constraints, asserted below
# ═══════════════════════════════════════════════════════════════════════════

#: ``cost_providers_type_check``
PROVIDER_TYPES = ("CUSTODIAN", "TAMP", "MODEL_PROVIDER", "TECH", "ADMIN", "ISSUER")

#: ``cost_schedules_basis_check``
SCHEDULE_BASES = (
    "BPS_ON_VALUE",
    "FLAT_PER_ACCOUNT",
    "FLAT_PER_HOUSEHOLD",
    "PER_TRANSACTION",
    "SPREAD_ON_BALANCE",
)

#: ``cost_schedules_frequency_check``
SCHEDULE_FREQUENCIES = ("MONTHLY", "ANNUAL", "PER_EVENT")

#: ``cost_schedules_applies_scope_check``. Note this is NOT the pass-through
#: scope vocabulary — it has POSITION and lacks BILLING_GROUP/ORG_DEFAULT.
SCHEDULE_APPLIES_SCOPES = ("ACCOUNT", "HOUSEHOLD", "POSITION")

#: ``cost_pass_through_policy_check``
POLICIES = ("ABSORB", "PASS_FULL", "PASS_PARTIAL", "MARKUP")

#: ``cost_pass_through_scope_type_check``. Deliberately NOT the same tuple as
#: ``fee_schedules.SCOPE_PRECEDENCE``'s keys — this one has no ENTITY.
POLICY_SCOPE_TYPES = ("ACCOUNT", "HOUSEHOLD", "BILLING_GROUP", "ORG_DEFAULT")

#: ``cost_events_cost_type_check``. Ten values, and none of them is a spread —
#: see the module docstring on CASH_SPREAD/MARGIN_SPREAD.
COST_TYPES = (
    "CUSTODY",
    "MODEL_FEE",
    "DIRECT_INDEXING",
    "SUBSCRIPTION",
    "ADMIN",
    "TECH",
    "ADVISOR_COMP",
    "SERVICE_TIME",
    "OVERHEAD_ALLOC",
    "REFERRAL",
)

#: ``cost_events_allocation_method_check``
ALLOCATION_METHODS = (
    "DIRECT",
    "PRO_RATA_AUM",
    "PER_ACCOUNT",
    "PER_HOUSEHOLD",
    "TIME_BASED",
    "DRIVER",
)

#: Most-specific first, lowest number wins. Gaps of ten so a scope inserted
#: between two existing ones needs a number, not a renumbering. Derived in the
#: SELECT, never stored — the table has no ``precedence`` column to corrupt.
SCOPE_PRECEDENCE: dict[str, int] = {
    "ACCOUNT": 10,
    "BILLING_GROUP": 20,
    "HOUSEHOLD": 30,
    "ORG_DEFAULT": 40,
}

SCOPE_ORG_DEFAULT = "ORG_DEFAULT"

assert set(SCOPE_PRECEDENCE) == set(POLICY_SCOPE_TYPES), (
    "SCOPE_PRECEDENCE and POLICY_SCOPE_TYPES have drifted: "
    f"{set(SCOPE_PRECEDENCE) ^ set(POLICY_SCOPE_TYPES)}"
)


def _current(alias: str) -> str:
    """Current on both temporal axes, matching ``portfolio_assets._current``."""
    return f"{alias}.valid_to IS NULL AND {alias}.system_to IS NULL"


# ═══════════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════════


class CostModelError(ValueError):
    """A cost-model write was refused for a reason the caller can fix."""


class CostModelNotFoundError(CostModelError):
    """The row is not this org's, or is not current.

    Deliberately indistinguishable from "does not exist", for the same reason
    as fee33's ``BillingGroupNotFoundError``: "that id exists but is not
    yours" confirms a row across a tenant boundary.
    """


class DisclosureRequiredError(CostModelError):
    """A MARKUP policy has no disclosure acknowledgement on record.

    The deployed CHECK only forces ``disclosure_required = true``, which is a
    flag saying disclosure is NEEDED — not evidence that it HAPPENED. This is
    the gate that asks for the evidence. Carries ``missing`` so a router can
    name the field rather than the constraint.
    """

    def __init__(self, message: str, *, missing: Sequence[str]) -> None:
        super().__init__(message)
        self.missing = tuple(missing)


class PassThroughRateError(CostModelError):
    """``pass_through_rate`` is outside the band its ``policy`` names.

    ``cost_pass_through_rate_required`` enforces only presence-or-absence: a
    rate must be NULL for ABSORB and non-NULL otherwise. It does not stop a
    PASS_FULL at 0.5, which would pass half a cost through under a label that
    says all of it, or a MARKUP at 0.9, which would mark a cost DOWN.
    """

    def __init__(self, message: str, *, policy: str, rate: Decimal | None) -> None:
        super().__init__(message)
        self.policy = policy
        self.rate = rate


class ScopeIdRequiredError(CostModelError):
    """``scope_id`` present for ORG_DEFAULT, or absent for anything else.

    Mirrors ``cost_pass_through_scope_id_required``, which enforces the shape
    but cannot explain it.
    """

    def __init__(self, message: str, *, scope_type: str) -> None:
        super().__init__(message)
        self.scope_type = scope_type


class AmbiguousRateCardError(CostModelError):
    """Two mutually-exclusive readings of one rate-card line were both used.

    See :data:`AMBIGUITY_GROUPS`. Seeding both readings is deliberate; summing
    both is a double-count.
    """

    def __init__(self, message: str, *, group: str, codes: Sequence[str]) -> None:
        super().__init__(message)
        self.group = group
        self.codes = tuple(codes)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _as_uuid_text(value: Any, *, field_name: str) -> str:
    if value is None:
        raise CostModelError(f"{field_name} is required")
    if isinstance(value, UUID):
        return str(value)
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise CostModelError(f"{field_name}={value!r} is not a valid uuid") from exc


def _opt_uuid_text(value: Any, *, field_name: str) -> str | None:
    return None if value is None else _as_uuid_text(value, field_name=field_name)


def _decimal(value: Any, *, field_name: str) -> Decimal:
    """Refuse floats outright. Standing rule: Decimal everywhere.

    A float that reached a numeric column would arrive already wrong, and the
    error would surface as a penny of drift on a reconciliation months later.
    """
    if isinstance(value, float):
        raise CostModelError(
            f"{field_name} was passed as a float ({value!r}); money and rates "
            "are Decimal here, and a float is already inexact before it "
            "reaches the database"
        )
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise CostModelError(f"{field_name}={value!r} is not a number") from exc


def _require_choice(value: Any, allowed: Sequence[str], *, field_name: str) -> str:
    text = str(value or "").strip().upper()
    if text not in allowed:
        raise CostModelError(
            f"{field_name}={value!r} is not one of {list(allowed)}"
        )
    return text


# ═══════════════════════════════════════════════════════════════════════════
# The Altruist rate card — DATA, unverified. See the module docstring.
# ═══════════════════════════════════════════════════════════════════════════

ALTRUIST_PROVIDER_CODE = "ALTRUIST"
ALTRUIST_PROVIDER_TYPE = "CUSTODIAN"

#: One URL for the whole card. The original research did not record a
#: per-line citation, and inventing one per row would be a more precise claim
#: than the evidence supports.
ALTRUIST_SOURCE_URL = "https://www.altruist.com/pricing"


@dataclass(frozen=True)
class RateCardRow:
    """One seeded ``cost_schedules`` row, plus what it cannot say.

    ``note`` is not decoration. Several of these rows carry a number whose
    APPLICATION is unresolved, and the note is the only place that survives
    into the database's neighbourhood at all — ``cost_schedules`` has no
    comment or metadata column (measured).
    """

    cost_code: str
    basis: str
    frequency: str
    applies_scope: str
    rate: Decimal | None = None
    flat_amount: Decimal | None = None
    minimum_amount: Decimal | None = None
    #: The ``cost_events.cost_type`` a charge under this schedule becomes.
    #: ``None`` means the deployed CHECK has no legal value for it — the row is
    #: a rate card the fee38 evaluator can read, and cannot produce an event.
    cost_type: str | None = None
    note: str = ""


ALTRUIST_SCHEDULES: tuple[RateCardRow, ...] = (
    # ── Altruist One subscription: reading A, the $1 is a FLOOR ──────────────
    RateCardRow(
        cost_code="ALTRUIST_ONE_SUB_FLOOR",
        basis="BPS_ON_VALUE",
        rate=Decimal("0.00010000"),  # 0.01% per month = 12 bps/yr
        minimum_amount=Decimal("1.0000"),
        frequency="MONTHLY",
        applies_scope="HOUSEHOLD",
        cost_type="SUBSCRIPTION",
        note=(
            "Reading A of an ambiguous card line: monthly cost = "
            "max(0.01% x household value, $1 x account count). MUTUALLY "
            "EXCLUSIVE with the ALTRUIST_ONE_SUB_ADDITIVE_* pair. Also note "
            "minimum_amount here is $1 PER ACCOUNT while applies_scope is "
            "HOUSEHOLD — the column cannot express that, so the per-account "
            "multiplication must happen in fee38's evaluator."
        ),
    ),
    # ── Altruist One subscription: reading B, the $1 is ADDITIONAL ───────────
    RateCardRow(
        cost_code="ALTRUIST_ONE_SUB_ADDITIVE_BPS",
        basis="BPS_ON_VALUE",
        rate=Decimal("0.00010000"),
        frequency="MONTHLY",
        applies_scope="HOUSEHOLD",
        cost_type="SUBSCRIPTION",
        note=(
            "Reading B, part 1 of 2: the bps component, charged in ADDITION "
            "to a flat $1/account. Pairs with "
            "ALTRUIST_ONE_SUB_ADDITIVE_PER_ACCOUNT. MUTUALLY EXCLUSIVE with "
            "ALTRUIST_ONE_SUB_FLOOR."
        ),
    ),
    RateCardRow(
        cost_code="ALTRUIST_ONE_SUB_ADDITIVE_PER_ACCOUNT",
        basis="FLAT_PER_ACCOUNT",
        flat_amount=Decimal("1.0000"),
        frequency="MONTHLY",
        applies_scope="ACCOUNT",
        cost_type="SUBSCRIPTION",
        note=(
            "Reading B, part 2 of 2: the flat per-account component. Pairs "
            "with ALTRUIST_ONE_SUB_ADDITIVE_BPS. Reading B is the more "
            "expensive of the two readings whenever the bps component exceeds "
            "zero, so a conservative evaluator should prefer it until the "
            "ambiguity is settled."
        ),
    ),
    # ── Direct indexing ─────────────────────────────────────────────────────
    RateCardRow(
        cost_code="ALTRUIST_DIRECT_INDEXING",
        basis="BPS_ON_VALUE",
        rate=Decimal("0.00120000"),  # 12 bps
        minimum_amount=Decimal("2000.0000"),
        frequency="ANNUAL",
        applies_scope="ACCOUNT",
        cost_type="DIRECT_INDEXING",
        note=(
            "12 bps with a $2,000 minimum, stated as identical across both "
            "tiers — so one row, not two. The one figure the original "
            "research reported without an ambiguity attached."
        ),
    ),
    # ── Model marketplace: included tier ─────────────────────────────────────
    RateCardRow(
        cost_code="ALTRUIST_MODEL_MARKETPLACE_INCLUDED",
        basis="BPS_ON_VALUE",
        rate=Decimal("0.00000000"),
        frequency="ANNUAL",
        applies_scope="ACCOUNT",
        cost_type="MODEL_FEE",
        note=(
            "The 350+ bundled models, 0 bps. A separate row from the paid "
            "tier rather than an average with it: a zero-cost bundled model "
            "and a paid third-party model are different cost structures, and "
            "which one an account is on is a fact about the account."
        ),
    ),
    # ── Model marketplace: paid tier, seeded as a BAND ───────────────────────
    RateCardRow(
        cost_code="ALTRUIST_MODEL_MARKETPLACE_PAID_LOW",
        basis="BPS_ON_VALUE",
        rate=Decimal("0.00100000"),  # 10 bps
        frequency="ANNUAL",
        applies_scope="ACCOUNT",
        cost_type="MODEL_FEE",
        note=(
            "Bottom of the stated 10-15 bps third-party band. Paired with "
            "ALTRUIST_MODEL_MARKETPLACE_PAID_HIGH; the real rate is "
            "per-model and was never enumerated."
        ),
    ),
    RateCardRow(
        cost_code="ALTRUIST_MODEL_MARKETPLACE_PAID_HIGH",
        basis="BPS_ON_VALUE",
        rate=Decimal("0.00150000"),  # 15 bps
        frequency="ANNUAL",
        applies_scope="ACCOUNT",
        cost_type="MODEL_FEE",
        note=(
            "Top of the stated 10-15 bps third-party band. The card's 'up to "
            "15 bps DISCOUNT under Altruist One' is NOT seeded: its base is "
            "unstated and a negative-rate row would sum destructively against "
            "these. See UNSEEDED_RATE_CARD_ITEMS."
        ),
    ),
    # ── Margin spread ladder: endpoints, not a collapsed midpoint ────────────
    RateCardRow(
        cost_code="ALTRUIST_MARGIN_SPREAD_NON_SUBSCRIBER",
        basis="SPREAD_ON_BALANCE",
        rate=Decimal("0.06250000"),  # 6.25%
        frequency="MONTHLY",
        applies_scope="ACCOUNT",
        cost_type=None,
        note=(
            "Non-subscriber margin spread. cost_type is None deliberately: "
            "cost_events' CHECK has no spread value, and this is charged by "
            "the custodian to the CLIENT — it is not a firm expense. Rate "
            "card for fee38 only; cannot produce a cost_event."
        ),
    ),
    RateCardRow(
        cost_code="ALTRUIST_MARGIN_SPREAD_AONE_LOW",
        basis="SPREAD_ON_BALANCE",
        rate=Decimal("0.04000000"),  # 4.00%
        frequency="MONTHLY",
        applies_scope="ACCOUNT",
        cost_type=None,
        note=(
            "Bottom rung of the Altruist One 4.00-5.25% ladder. Seeded as an "
            "endpoint because no cost_schedule_tiers table exists to hold a "
            "ladder, and collapsing a tiered rate to one number is the thing "
            "the sprint explicitly forbade."
        ),
    ),
    RateCardRow(
        cost_code="ALTRUIST_MARGIN_SPREAD_AONE_HIGH",
        basis="SPREAD_ON_BALANCE",
        rate=Decimal("0.05250000"),  # 5.25%
        frequency="MONTHLY",
        applies_scope="ACCOUNT",
        cost_type=None,
        note="Top rung of the Altruist One 4.00-5.25% ladder.",
    ),
)

#: Cost codes that are alternate readings of ONE card line. Exactly one group
#: member set may be used by any consumer; using both double-counts.
AMBIGUITY_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "ALTRUIST_ONE_SUBSCRIPTION": (
        ("ALTRUIST_ONE_SUB_FLOOR",),
        (
            "ALTRUIST_ONE_SUB_ADDITIVE_BPS",
            "ALTRUIST_ONE_SUB_ADDITIVE_PER_ACCOUNT",
        ),
    ),
}

#: Rate-card lines this sprint deliberately did NOT seed, and why. Kept as
#: data rather than prose so fee38 can surface them to whoever runs the
#: evaluator instead of discovering the hole empirically.
UNSEEDED_RATE_CARD_ITEMS: tuple[dict[str, str], ...] = (
    {
        "item": "CASH_SPREAD",
        "reason": (
            "It is a yield UPLIFT — revenue/benefit, not a cost the firm pays. "
            "cost_events' cost_type CHECK has no value for it, and a positive "
            "rate filed under cost_schedules would be read as an expense by "
            "every downstream sum."
        ),
        "belongs_in": (
            "fee38's own evaluator inputs, as a benefit line — not "
            "cost_schedules."
        ),
    },
    {
        "item": "MODEL_MARKETPLACE Altruist One discount (up to 15 bps)",
        "reason": (
            "The base it applies to is unstated (off the 10-15 bps paid rate, "
            "or off the whole bill?), and a negative-rate row would sum "
            "destructively against the PAID_LOW/PAID_HIGH rows."
        ),
        "belongs_in": (
            "a re-verified rate card — this needs a human to read the source "
            "before it can be represented at all."
        ),
    },
)


def ambiguous_cost_codes() -> frozenset[str]:
    """Every cost code that is one reading of an ambiguous card line.

    A consumer summing schedules for a provider should either exclude these or
    pass an explicit reading through :func:`assert_no_ambiguous_overlap`.
    """
    return frozenset(
        code
        for readings in AMBIGUITY_GROUPS.values()
        for reading in readings
        for code in reading
    )


def assert_no_ambiguous_overlap(cost_codes: Sequence[str]) -> None:
    """Refuse a selection that draws from two readings of the same line.

    This is the guard rail that makes seeding both readings safe rather than
    merely honest. Without it, "sum every Altruist schedule" is a plausible
    thing for fee38 to write and would over-state the subscription cost.
    """
    chosen = {str(c).strip().upper() for c in cost_codes}
    for group, readings in AMBIGUITY_GROUPS.items():
        hit = [r for r in readings if chosen & set(r)]
        if len(hit) > 1:
            raise AmbiguousRateCardError(
                f"cost codes draw from {len(hit)} mutually-exclusive readings "
                f"of the {group} rate-card line; exactly one reading may be "
                "used. Summing both double-counts the subscription.",
                group=group,
                codes=sorted(chosen & ambiguous_cost_codes()),
            )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — seed the Altruist provider profile
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SeededProfile:
    provider_id: str
    provider_code: str
    schedule_ids: dict[str, str]
    source_verified_on: date
    created: bool


async def seed_altruist_profile(
    conn,
    org_id: str,
    *,
    source_verified_on: date,
    effective_from: date | None = None,
    source_url: str = ALTRUIST_SOURCE_URL,
) -> SeededProfile:
    """Create the ALTRUIST provider and its rate-card schedules.

    ``source_verified_on`` is REQUIRED and has no default. That is the point:
    a default would let this run and stamp a verification date nobody chose.
    The caller has to say what date they are willing to stand behind. See the
    module docstring — for this sprint's own run it means "entered on", not
    "re-checked against altruist.com on".

    Idempotent by ``(org_id, provider_code)`` and ``(provider, cost_code)``:
    re-running adopts the existing rows rather than inserting a second copy.
    ``cost_providers_code_uq`` is partial on ``system_to IS NULL``, so it does
    not stop a duplicate current row after an archival — the lookup does.
    """
    org_id = _require_org(org_id)
    if not isinstance(source_verified_on, date) or isinstance(
        source_verified_on, datetime
    ):
        raise CostModelError(
            "source_verified_on must be a date (not a datetime, and not "
            "omitted) — it is a claim about when a human last read the source"
        )
    effective_from = effective_from or source_verified_on
    if not source_url:
        raise CostModelError(
            "source_url is required: an unattributed rate card cannot be "
            "re-verified, which is the only thing that makes it usable"
        )

    async with _OrgWrite(conn, org_id) as c:
        provider = await c.fetchrow(
            f"""
            SELECT id::text AS id FROM {TABLE_PROVIDERS} p
            WHERE p.org_id = $1::uuid AND p.provider_code = $2
              AND {_current('p')}
            """,
            org_id,
            ALTRUIST_PROVIDER_CODE,
        )
        created = provider is None
        if provider is None:
            provider = await c.fetchrow(
                f"""
                INSERT INTO {TABLE_PROVIDERS} (org_id, provider_code, provider_type)
                VALUES ($1::uuid, $2, $3)
                RETURNING id::text AS id
                """,
                org_id,
                ALTRUIST_PROVIDER_CODE,
                ALTRUIST_PROVIDER_TYPE,
            )
        provider_id = provider["id"]

        schedule_ids: dict[str, str] = {}
        for row in ALTRUIST_SCHEDULES:
            existing = await c.fetchrow(
                f"""
                SELECT id::text AS id FROM {TABLE_SCHEDULES} s
                WHERE s.org_id = $1::uuid AND s.cost_provider_id = $2::uuid
                  AND s.cost_code = $3 AND {_current('s')}
                """,
                org_id,
                provider_id,
                row.cost_code,
            )
            if existing is not None:
                schedule_ids[row.cost_code] = existing["id"]
                continue
            inserted = await c.fetchrow(
                f"""
                INSERT INTO {TABLE_SCHEDULES}
                    (org_id, cost_provider_id, cost_code, basis, rate,
                     flat_amount, minimum_amount, frequency, applies_scope,
                     effective_from, source_url, source_verified_on)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5::numeric, $6::numeric,
                        $7::numeric, $8, $9, $10::date, $11, $12::date)
                RETURNING id::text AS id
                """,
                org_id,
                provider_id,
                row.cost_code,
                row.basis,
                row.rate,
                row.flat_amount,
                row.minimum_amount,
                row.frequency,
                row.applies_scope,
                effective_from,
                source_url,
                source_verified_on,
            )
            schedule_ids[row.cost_code] = inserted["id"]

    return SeededProfile(
        provider_id=provider_id,
        provider_code=ALTRUIST_PROVIDER_CODE,
        schedule_ids=schedule_ids,
        source_verified_on=source_verified_on,
        created=created,
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3a — pass-through policy writes, with the real MARKUP gate
# ═══════════════════════════════════════════════════════════════════════════


def _assert_markup_disclosed(
    policy: str,
    *,
    disclosure_acknowledged_by: Any,
    disclosure_acknowledged_at: Any,
) -> None:
    """The gate the deployed CHECK is not.

    ``cost_pass_through_markup_requires_disclosure`` asserts
    ``disclosure_required = true``, which records that disclosure is NEEDED.
    Both acknowledgement columns are nullable and unconstrained, so the state
    "MARKUP, disclosure required, nobody acknowledged" inserts cleanly. That
    state is precisely the one that must not exist: it is a markup on a
    client's vendor cost with no one on record as having told them.

    Applied at creation because creation IS activation here — the table has no
    status column, so a policy becomes live the moment a current row exists
    inside its effective window.
    """
    if policy != "MARKUP":
        return
    missing = []
    if disclosure_acknowledged_by in (None, ""):
        missing.append("disclosure_acknowledged_by")
    if disclosure_acknowledged_at is None:
        missing.append("disclosure_acknowledged_at")
    if missing:
        raise DisclosureRequiredError(
            "a MARKUP pass-through policy cannot be made active without a "
            "disclosure acknowledgement on record; missing "
            f"{', '.join(missing)}. The database's own CHECK only requires "
            "disclosure_required=true, which records that disclosure is "
            "needed, not that it happened.",
            missing=missing,
        )


def _validate_rate_band(policy: str, rate: Decimal | None) -> Decimal | None:
    """Constrain the rate to the band its policy label promises.

    ``cost_pass_through_rate_required`` enforces presence only. A PASS_FULL at
    0.5 and a MARKUP at 0.9 both satisfy it, and both mean something other
    than what they say.
    """
    if policy == "ABSORB":
        if rate is not None:
            raise PassThroughRateError(
                "ABSORB passes nothing through, so pass_through_rate must be "
                "NULL — not 0, which would be a PASS_PARTIAL of zero and a "
                "different statement about intent",
                policy=policy,
                rate=rate,
            )
        return None
    if rate is None:
        raise PassThroughRateError(
            f"{policy} requires a pass_through_rate", policy=policy, rate=None
        )
    if policy == "PASS_FULL" and rate != ONE:
        raise PassThroughRateError(
            f"PASS_FULL means the whole cost, so pass_through_rate must be "
            f"exactly 1; got {rate}. Use PASS_PARTIAL for a fraction.",
            policy=policy,
            rate=rate,
        )
    if policy == "PASS_PARTIAL" and not (ZERO < rate < ONE):
        raise PassThroughRateError(
            f"PASS_PARTIAL means a strict fraction of the cost, so "
            f"pass_through_rate must be in (0, 1); got {rate}. Use ABSORB for "
            "0, PASS_FULL for 1, MARKUP for more than 1.",
            policy=policy,
            rate=rate,
        )
    if policy == "MARKUP" and rate <= ONE:
        raise PassThroughRateError(
            f"MARKUP means more than the cost, so pass_through_rate must be "
            f"greater than 1; got {rate}. A rate of 1 or less is not a markup.",
            policy=policy,
            rate=rate,
        )
    return rate


async def create_pass_through_policy(
    conn,
    org_id: str,
    *,
    cost_schedule_id: Any,
    scope_type: str,
    scope_id: Any = None,
    policy: str,
    pass_through_rate: Any = None,
    approved_by: Any,
    reason: str,
    effective_from: date,
    effective_to: date | None = None,
    disclosure_required: bool = True,
    disclosure_acknowledged_by: Any = None,
    disclosure_acknowledged_at: datetime | None = None,
) -> dict[str, Any]:
    """Insert a pass-through policy, i.e. make one active.

    Every gate here is one the database does not have: the rate band, the
    MARKUP acknowledgement, and the schedule's tenancy. ``cost_schedule_id``
    has a real FK, but a FK does not check ``org_id`` — pointing a policy at
    another tenant's schedule would insert cleanly and RLS would not see it,
    because the policy row's OWN org_id is correct.
    """
    org_id = _require_org(org_id)
    cost_schedule_id = _as_uuid_text(cost_schedule_id, field_name="cost_schedule_id")
    scope_type = _require_choice(
        scope_type, POLICY_SCOPE_TYPES, field_name="scope_type"
    )
    policy = _require_choice(policy, POLICIES, field_name="policy")
    approved_by = _as_uuid_text(approved_by, field_name="approved_by")
    if not (reason or "").strip():
        raise CostModelError(
            "reason is required: a pass-through decision that nobody wrote "
            "down cannot be reviewed later"
        )
    if not isinstance(effective_from, date) or isinstance(effective_from, datetime):
        raise CostModelError("effective_from must be a date")

    if scope_type == SCOPE_ORG_DEFAULT:
        if scope_id is not None:
            raise ScopeIdRequiredError(
                "ORG_DEFAULT is the org-wide fallback and applies to "
                "everything, so it has nothing to point at; scope_id must be "
                "NULL",
                scope_type=scope_type,
            )
        scope_id_text = None
    else:
        if scope_id is None:
            raise ScopeIdRequiredError(
                f"{scope_type} names a specific {scope_type.lower()}, so "
                "scope_id is required",
                scope_type=scope_type,
            )
        scope_id_text = _as_uuid_text(scope_id, field_name="scope_id")

    rate = (
        None
        if pass_through_rate is None
        else _decimal(pass_through_rate, field_name="pass_through_rate")
    )
    rate = _validate_rate_band(policy, rate)

    if policy == "MARKUP" and not disclosure_required:
        # Mirrors cost_pass_through_markup_requires_disclosure, with the reason
        # attached rather than a constraint name.
        raise DisclosureRequiredError(
            "a MARKUP policy always requires disclosure; "
            "disclosure_required cannot be set false",
            missing=("disclosure_required",),
        )
    _assert_markup_disclosed(
        policy,
        disclosure_acknowledged_by=disclosure_acknowledged_by,
        disclosure_acknowledged_at=disclosure_acknowledged_at,
    )
    ack_by = _opt_uuid_text(
        disclosure_acknowledged_by, field_name="disclosure_acknowledged_by"
    )

    async with _OrgWrite(conn, org_id) as c:
        schedule = await c.fetchrow(
            f"""
            SELECT id::text AS id FROM {TABLE_SCHEDULES} s
            WHERE s.id = $1::uuid AND s.org_id = $2::uuid AND {_current('s')}
            """,
            cost_schedule_id,
            org_id,
        )
        if schedule is None:
            raise CostModelNotFoundError(
                f"cost_schedule {cost_schedule_id} is not a current schedule "
                "in this org"
            )
        row = await c.fetchrow(
            f"""
            INSERT INTO {TABLE_POLICIES}
                (org_id, cost_schedule_id, scope_type, scope_id, policy,
                 pass_through_rate, disclosure_required,
                 disclosure_acknowledged_by, disclosure_acknowledged_at,
                 approved_by, reason, effective_from, effective_to)
            VALUES ($1::uuid, $2::uuid, $3, $4::uuid, $5, $6::numeric, $7,
                    $8::uuid, $9::timestamptz, $10::uuid, $11, $12::date,
                    $13::date)
            RETURNING id::text AS id, policy, pass_through_rate,
                      scope_type, scope_id::text AS scope_id, effective_from
            """,
            org_id,
            cost_schedule_id,
            scope_type,
            scope_id_text,
            policy,
            rate,
            disclosure_required,
            ack_by,
            disclosure_acknowledged_at,
            approved_by,
            reason.strip(),
            effective_from,
            effective_to,
        )

    return {
        "id": row["id"],
        "policy": row["policy"],
        "pass_through_rate": row["pass_through_rate"],
        "scope_type": row["scope_type"],
        "scope_id": row["scope_id"],
        "precedence": SCOPE_PRECEDENCE[row["scope_type"]],
        "effective_from": row["effective_from"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3b — resolution
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ResolvedPolicy:
    """The winning policy and, deliberately, the ones it beat.

    ``losers`` is carried for the same reason fee34's ``ResolvedAssignment``
    carries it: "why is this account absorbing when the org default says pass
    it through" is a question an operator asks, and answering it from a
    function that returned only the winner means re-deriving the resolution by
    hand.
    """

    policy_id: str
    cost_schedule_id: str
    policy: str
    pass_through_rate: Decimal | None
    scope_type: str
    scope_id: str | None
    precedence: int
    disclosure_required: bool
    disclosure_acknowledged_by: str | None
    disclosure_acknowledged_at: datetime | None
    losers: tuple[dict[str, Any], ...] = ()


#: Derived in the SELECT because the table has no precedence column. Built
#: from SCOPE_PRECEDENCE so the two cannot drift.
_PRECEDENCE_SQL = "CASE p.scope_type " + " ".join(
    f"WHEN '{scope}' THEN {rank}" for scope, rank in SCOPE_PRECEDENCE.items()
) + " ELSE 999 END"


async def resolve_pass_through_policy(
    conn,
    org_id: str,
    cost_schedule_id: Any,
    *,
    account_id: Any = None,
    household_id: Any = None,
    billing_group_id: Any = None,
    as_of: date | None = None,
) -> ResolvedPolicy | None:
    """Which pass-through policy governs this cost on ``as_of``, and why.

    Pass ``account_id`` and the account's own household and active billing
    groups are gathered from it, so an ACCOUNT policy, a BILLING_GROUP policy
    on any group it belongs to, a HOUSEHOLD policy on its household, and the
    ORG_DEFAULT all compete — most specific wins. Pass ``household_id`` or
    ``billing_group_id`` alone to resolve for a scope that has no account in
    hand; that scope and the ORG_DEFAULT compete.

    Returns ``None`` when nothing matches, including no ORG_DEFAULT. It does
    NOT fall back to ABSORB. A cost with no policy is not a cost the firm has
    decided to eat; it is a cost nobody has ruled on, and a caller has to
    decide what that means rather than inherit a silent default that happens
    to be the cheap answer for the client.
    """
    org_id = _require_org(org_id)
    cost_schedule_id = _as_uuid_text(cost_schedule_id, field_name="cost_schedule_id")
    as_of = as_of or date.today()

    account_id = _opt_uuid_text(account_id, field_name="account_id")
    household_id = _opt_uuid_text(household_id, field_name="household_id")
    group_ids: list[str] = []
    if billing_group_id is not None:
        group_ids.append(_as_uuid_text(billing_group_id, field_name="billing_group_id"))

    if account_id is not None:
        account = await conn.fetchrow(
            f"""
            SELECT a.id::text AS id, a.household_id::text AS household_id
            FROM {TABLE_ACCOUNTS} a
            WHERE a.id = $1::uuid AND a.org_id = $2::uuid AND {_current('a')}
            """,
            account_id,
            org_id,
        )
        if account is None:
            raise CostModelNotFoundError(
                f"account {account_id} is not a current account in this org"
            )
        if household_id is None:
            household_id = account["household_id"]
        for r in await conn.fetch(
            f"""
            SELECT m.billing_group_id::text AS id
            FROM {TABLE_BILLING_GROUP_MEMBERS} m
            JOIN {TABLE_BILLING_GROUPS} g
              ON g.id = m.billing_group_id AND g.org_id = m.org_id
             AND {_current('g')}
            WHERE m.account_id = $1::uuid AND m.org_id = $2::uuid
              AND {_current('m')}
            """,
            account_id,
            org_id,
        ):
            if r["id"] not in group_ids:
                group_ids.append(r["id"])

    rows = await conn.fetch(
        f"""
        SELECT p.id::text               AS policy_id,
               p.cost_schedule_id::text AS cost_schedule_id,
               p.policy, p.pass_through_rate,
               p.scope_type, p.scope_id::text AS scope_id,
               {_PRECEDENCE_SQL}        AS precedence,
               p.disclosure_required,
               p.disclosure_acknowledged_by::text AS disclosure_acknowledged_by,
               p.disclosure_acknowledged_at,
               p.effective_from, p.created_at
        FROM {TABLE_POLICIES} p
        WHERE p.org_id = $1::uuid
          AND p.cost_schedule_id = $2::uuid
          AND {_current('p')}
          AND p.effective_from <= $3::date
          AND (p.effective_to IS NULL OR p.effective_to > $3::date)
          AND (
                (p.scope_type = 'ACCOUNT'       AND p.scope_id = $4::uuid)
             OR (p.scope_type = 'BILLING_GROUP' AND p.scope_id = ANY($5::uuid[]))
             OR (p.scope_type = 'HOUSEHOLD'     AND p.scope_id = $6::uuid)
             OR (p.scope_type = 'ORG_DEFAULT'   AND p.scope_id IS NULL)
          )
        ORDER BY precedence ASC, p.effective_from DESC, p.created_at DESC
        """,
        org_id,
        cost_schedule_id,
        as_of,
        account_id,
        group_ids,
        household_id,
    )
    if not rows:
        return None

    w = rows[0]
    return ResolvedPolicy(
        policy_id=w["policy_id"],
        cost_schedule_id=w["cost_schedule_id"],
        policy=w["policy"],
        pass_through_rate=w["pass_through_rate"],
        scope_type=w["scope_type"],
        scope_id=w["scope_id"],
        precedence=int(w["precedence"]),
        disclosure_required=bool(w["disclosure_required"]),
        disclosure_acknowledged_by=w["disclosure_acknowledged_by"],
        disclosure_acknowledged_at=w["disclosure_acknowledged_at"],
        losers=tuple(
            {
                "policy_id": r["policy_id"],
                "policy": r["policy"],
                "scope_type": r["scope_type"],
                "scope_id": r["scope_id"],
                "precedence": int(r["precedence"]),
            }
            for r in rows[1:]
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3c — the computation, and the event
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PassThroughOutcome:
    """What one cost becomes under one policy. Pure — touches no database.

    The cost is ALWAYS the real cost. ``policy`` changes what the client sees,
    never what the firm paid, which is why ``cost_amount`` here is independent
    of every other field.
    """

    policy: str
    pass_through_rate: Decimal | None
    #: The real cost, at cost_events.amount's own scale.
    cost_amount: Decimal
    #: What fee39 will bill, quantized to cents. Zero under ABSORB.
    implied_revenue: Decimal
    #: implied_revenue - cost_amount. Negative under ABSORB/PASS_PARTIAL,
    #: zero-ish under PASS_FULL, positive under MARKUP.
    margin: Decimal
    is_passed_through: bool
    #: Sub-cent remainder the firm eats because a bill line cannot carry it.
    residual_absorbed: Decimal


def compute_pass_through(
    cost_amount: Any,
    policy: str,
    pass_through_rate: Any = None,
) -> PassThroughOutcome:
    """Apply one policy to one cost. One formula, four bands.

    ``implied_revenue = cost x rate``, with ABSORB's NULL rate meaning zero.
    The four policy names are bands on that single rate rather than four
    separate arithmetics, which is why a PASS_FULL at 0.5 has to be refused —
    if the label did not constrain the band, the label would carry no
    information and the four names would be decoration.
    """
    policy = _require_choice(policy, POLICIES, field_name="policy")
    cost = _decimal(cost_amount, field_name="cost_amount").quantize(
        COST_Q, rounding=ROUND_HALF_UP
    )
    rate = (
        None
        if pass_through_rate is None
        else _decimal(pass_through_rate, field_name="pass_through_rate")
    )
    rate = _validate_rate_band(policy, rate)

    if policy == "ABSORB":
        return PassThroughOutcome(
            policy=policy,
            pass_through_rate=None,
            cost_amount=cost,
            implied_revenue=ZERO.quantize(CENTS),
            margin=(-cost).quantize(CENTS, rounding=ROUND_HALF_UP),
            is_passed_through=False,
            residual_absorbed=cost,
        )

    exact = cost * rate
    revenue = exact.quantize(CENTS, rounding=ROUND_HALF_UP)
    return PassThroughOutcome(
        policy=policy,
        pass_through_rate=rate,
        cost_amount=cost,
        implied_revenue=revenue,
        margin=(revenue - cost).quantize(COST_Q, rounding=ROUND_HALF_UP),
        is_passed_through=True,
        # Only the rounding remainder, not the policy's own retained share:
        # a PASS_PARTIAL retains cost*(1-rate) BY DESIGN, and calling that
        # "absorbed" would conflate a decision with a rounding artifact.
        residual_absorbed=(exact - revenue).quantize(
            Decimal("0.00000001"), rounding=ROUND_HALF_UP
        ),
    )


@dataclass(frozen=True)
class RecordedCost:
    cost_event_id: str
    outcome: PassThroughOutcome
    resolved_policy: ResolvedPolicy | None
    #: Always None this sprint. fee39 owns revenue_events; the table does not
    #: exist yet, which is why cost_events.linked_revenue_event_id has no FK.
    revenue_event_id: str | None = None
    warnings: tuple[str, ...] = field(default=())


async def record_cost_event(
    conn,
    org_id: str,
    *,
    amount: Any,
    cost_type: str,
    event_date: date,
    allocation_method: str,
    cost_provider_id: Any = None,
    cost_schedule_id: Any = None,
    resolved_policy: ResolvedPolicy | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    currency: str = "USD",
    allocation_driver: str | None = None,
    account_id: Any = None,
    entity_id: Any = None,
    household_id: Any = None,
    billing_group_id: Any = None,
    advisor_id: Any = None,
    product_type: str | None = None,
    as_of: date | None = None,
) -> RecordedCost:
    """Record the real cost, and return the revenue it implies.

    The cost_event is written UNCONDITIONALLY — under every policy including
    ABSORB. That is the invariant this whole table exists for: the ledger
    records what the firm paid regardless of who ends up paying it, so a
    profitability rollup that only saw passed-through costs would report the
    absorbed ones as free.

    ``resolved_policy`` may be supplied by a caller that already resolved, or
    left None and resolved here from ``cost_schedule_id`` plus whichever scope
    ids were passed. If neither is available the cost is still recorded, with
    ``is_passed_through`` false and a warning — an unruled cost is a real cost.

    No revenue_event is written. ``revenue_event_id`` is always None and
    ``outcome.implied_revenue`` is the number fee39 will consume.
    """
    org_id = _require_org(org_id)
    cost_type = _require_choice(cost_type, COST_TYPES, field_name="cost_type")
    allocation_method = _require_choice(
        allocation_method, ALLOCATION_METHODS, field_name="allocation_method"
    )
    if not isinstance(event_date, date) or isinstance(event_date, datetime):
        raise CostModelError("event_date must be a date")
    if (period_start is None) != (period_end is None):
        raise CostModelError(
            "period_start and period_end are a pair — one without the other "
            "describes a window with no end, which no consumer can bill from"
        )
    if period_start is not None and period_end < period_start:
        raise CostModelError(
            f"period_end {period_end} precedes period_start {period_start}"
        )

    account_id = _opt_uuid_text(account_id, field_name="account_id")
    entity_id = _opt_uuid_text(entity_id, field_name="entity_id")
    household_id = _opt_uuid_text(household_id, field_name="household_id")
    billing_group_id = _opt_uuid_text(billing_group_id, field_name="billing_group_id")
    advisor_id = _opt_uuid_text(advisor_id, field_name="advisor_id")
    provider_id = _opt_uuid_text(cost_provider_id, field_name="cost_provider_id")
    schedule_id = _opt_uuid_text(cost_schedule_id, field_name="cost_schedule_id")

    warnings: list[str] = []
    if resolved_policy is None and schedule_id is not None:
        resolved_policy = await resolve_pass_through_policy(
            conn,
            org_id,
            schedule_id,
            account_id=account_id,
            household_id=household_id,
            billing_group_id=billing_group_id,
            as_of=as_of or event_date,
        )

    if resolved_policy is None:
        warnings.append(
            "no pass_through policy resolved (not even an ORG_DEFAULT); the "
            "cost is recorded and NOT passed through, but nobody has ruled on "
            "it — this is an unruled cost, not a decision to absorb"
        )
        outcome = compute_pass_through(amount, "ABSORB")
    else:
        outcome = compute_pass_through(
            amount, resolved_policy.policy, resolved_policy.pass_through_rate
        )
        if resolved_policy.policy == "MARKUP" and not (
            resolved_policy.disclosure_acknowledged_by
            and resolved_policy.disclosure_acknowledged_at
        ):
            # Belt to create_pass_through_policy's braces. A row that predates
            # this module, or one inserted by raw SQL, can still be in the
            # state the gate exists to prevent.
            raise DisclosureRequiredError(
                f"resolved MARKUP policy {resolved_policy.policy_id} has no "
                "disclosure acknowledgement on record; it must not price a "
                "client charge",
                missing=tuple(
                    n
                    for n, v in (
                        (
                            "disclosure_acknowledged_by",
                            resolved_policy.disclosure_acknowledged_by,
                        ),
                        (
                            "disclosure_acknowledged_at",
                            resolved_policy.disclosure_acknowledged_at,
                        ),
                    )
                    if not v
                ),
            )

    async with _OrgWrite(conn, org_id) as c:
        row = await c.fetchrow(
            f"""
            INSERT INTO {TABLE_EVENTS}
                (org_id, event_date, period_start, period_end, amount,
                 currency, cost_type, cost_provider_id, allocation_method,
                 allocation_driver, is_passed_through, linked_revenue_event_id,
                 account_id, entity_id, household_id, billing_group_id,
                 advisor_id, product_type)
            VALUES ($1::uuid, $2::date, $3::date, $4::date, $5::numeric,
                    $6, $7, $8::uuid, $9, $10, $11, NULL,
                    $12::uuid, $13::uuid, $14::uuid, $15::uuid, $16::uuid, $17)
            RETURNING id::text AS id
            """,
            org_id,
            event_date,
            period_start,
            period_end,
            outcome.cost_amount,
            currency,
            cost_type,
            provider_id,
            allocation_method,
            allocation_driver,
            outcome.is_passed_through,
            account_id,
            entity_id,
            household_id,
            billing_group_id,
            advisor_id,
            product_type,
        )

    return RecordedCost(
        cost_event_id=row["id"],
        outcome=outcome,
        resolved_policy=resolved_policy,
        revenue_event_id=None,
        warnings=tuple(warnings),
    )


__all__ = [
    "ALTRUIST_PROVIDER_CODE",
    "ALTRUIST_SCHEDULES",
    "ALTRUIST_SOURCE_URL",
    "AMBIGUITY_GROUPS",
    "ALLOCATION_METHODS",
    "AmbiguousRateCardError",
    "COST_TYPES",
    "CostModelError",
    "CostModelNotFoundError",
    "DisclosureRequiredError",
    "POLICIES",
    "POLICY_SCOPE_TYPES",
    "PROVIDER_TYPES",
    "PassThroughOutcome",
    "PassThroughRateError",
    "READ_PERMISSION",
    "RecordedCost",
    "ResolvedPolicy",
    "SCHEDULE_APPLIES_SCOPES",
    "SCHEDULE_BASES",
    "SCHEDULE_FREQUENCIES",
    "SCOPE_PRECEDENCE",
    "ScopeIdRequiredError",
    "SeededProfile",
    "UNSEEDED_RATE_CARD_ITEMS",
    "WRITE_PERMISSION",
    "ambiguous_cost_codes",
    "assert_no_ambiguous_overlap",
    "compute_pass_through",
    "create_pass_through_policy",
    "record_cost_event",
    "resolve_pass_through_policy",
    "seed_altruist_profile",
]
