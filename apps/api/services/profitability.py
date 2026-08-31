"""Sprint fee39 — revenue emission and the profitability roll-up.

Two things live here, and they are two things on purpose:

1. :func:`emit_revenue_for_run` turns a POSTED ``fee_run``'s lines into
   ``revenue_events`` rows. It is the only writer of ``source_type =
   'FEE_RUN_LINE'`` revenue.
2. :func:`profit_and_loss` and :func:`households_by_margin` read
   ``v_profitability_events`` — the UNION of ``revenue_events`` and
   ``cost_events`` — and produce the firm's standard P&L, sliced by any of
   eight cuts.


WHY THERE IS NO AGGREGATE TABLE PER CUT
──────────────────────────────────────────────────────────────────────────────

``revenue_events`` and ``cost_events`` carry the SAME dimensional keys
(``account_id``, ``household_id``, ``billing_group_id``, ``advisor_id``,
``product_type``). That is the whole design: every cut anyone has asked for is
a ``GROUP BY`` against one view, not a bespoke query, and certainly not a
materialised table per cut that can drift from its source. Materialise only
when a measurement says to. Nothing has measured that yet.


THE LINE ORDER IS THE POINT, NOT A PRESENTATION DETAIL
──────────────────────────────────────────────────────────────────────────────

:data:`PNL_LINE_ORDER` is fixed:

    gross revenue
    direct costs
    contribution margin (direct)          ← before ANY allocation
    advisor comp / service cost
    contribution margin (after service)
    allocated overhead
    net profit

A margin with overhead already baked into it invites an argument about the
allocation basis instead of a decision about the client. So the margin BEFORE
allocation is published as its own line, always, and the two are never
collapsed. :class:`ProfitAndLoss` asserts its own ordering on construction, so
a refactor that reorders the fields fails loudly rather than quietly shipping
a differently-shaped P&L.


SIGNS
──────────────────────────────────────────────────────────────────────────────

``cost_events.amount`` is stored POSITIVE (a cost of 100 is ``100``). The view
negates it into ``signed_amount``, so ``SUM(signed_amount)`` over every row of
a cut IS net profit. This module reports each cost LINE as a positive
magnitude — "direct costs: 4,000" reads correctly and subtracts correctly —
and cross-checks that ``net_profit`` still equals the raw
``SUM(signed_amount)``. If the band partition ever stops covering every
deployed ``cost_type``, that cross-check is what catches it.

A REVERSAL run's ``fee_run_lines`` already carry negated ``net_fee`` (fee36
writes them that way). So a reversal needs no branch here at all: the same
emission path produces negative ``revenue_events``, and the original plus its
reversal sum to zero in the view. Special-casing reversals would create a
second code path that could disagree with the first one.


CAVEATS THIS MODULE INHERITS AND REFUSES TO SWALLOW
──────────────────────────────────────────────────────────────────────────────

* **Unverified cost rates (fee37 F6).** Any cost that came through a MARKUP or
  PASS_PARTIAL pass-through policy rests on ``cost_schedules`` rates nobody has
  re-checked. Rather than assume, :func:`profit_and_loss` looks at the rows it
  actually summed — ``cost_events.is_passed_through`` and any
  ``PASS_THROUGH_MARKUP`` revenue — and attaches
  :data:`UNVERIFIED_RATE_CAVEAT` only when such a row is genuinely in the cut.

* **``cost_events`` is not provably duplicate-free (fee37 F4).** Its dedupe
  index is ``(org_id, cost_provider_id, cost_type, account_id, household_id,
  billing_group_id, period_start, period_end) WHERE system_to IS NULL``, and in
  Postgres a UNIQUE index does not constrain rows whose indexed columns include
  a NULL. Every firm-level or provider-level cost has NULL account/household/
  billing_group, so exactly the rows a firm-wide roll-up sums are the rows the
  index does not protect. A duplicate there does not error — it silently
  doubles a cost line. :func:`duplicate_cost_scan` finds them and
  :func:`profit_and_loss` reports them as a warning on the result, because a
  P&L that is quietly wrong is worse than one that says it might be.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from services.fee_calc_inputs import PRODUCT_TYPES, FeeCalcError
from services.fee_runs import IMMUTABLE_STATUSES, get_run, list_lines

T_REVENUE = "public.revenue_events"
T_COSTS = "public.cost_events"
V_PROFIT = "public.v_profitability_events"

ZERO = Decimal(0)


# ═══════════════════════════════════════════════════════════════════════════
# Vocabularies — every one of these mirrors a DEPLOYED CHECK, read live
# ═══════════════════════════════════════════════════════════════════════════

#: ``revenue_events_type_check``.
REVENUE_TYPES = (
    "ADVISORY_FEE",
    "SPV_MGMT_FEE",
    "SPV_CARRY",
    "PLACEMENT_FEE",
    "CLUB_DUES",
    "PLANNING_FEE",
    "PASS_THROUGH_MARKUP",
    "INTEREST_SHARE",
)

#: ``revenue_events_source_type_check``.
SOURCE_TYPES = ("FEE_RUN_LINE", "COST_EVENT_MARKUP", "SPV_TRANSACTION", "MANUAL")

#: ``revenue_events_recognition_check``.
RECOGNITIONS = ("ACCRUAL", "CASH")

#: ``cost_events_cost_type_check``. Duplicated from :mod:`services.cost_model`
#: rather than imported so this module's band partition can be asserted against
#: the vocabulary it actually believes in; :func:`assert_cost_types_agree`
#: proves the two have not drifted.
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

SOURCE_FEE_RUN_LINE = "FEE_RUN_LINE"
RECOGNITION_ACCRUAL = "ACCRUAL"

LINE_KIND_REVENUE = "REVENUE"
LINE_KIND_COST = "COST"


# ═══════════════════════════════════════════════════════════════════════════
# product_type -> revenue_type
# ═══════════════════════════════════════════════════════════════════════════

#: One entry per DEPLOYED ``fee_schedules.product_type``. Note the advisory one
#: is spelled ``ASSET_MANAGEMENT``, not ``ADVISORY`` — recorded as finding F39-B
#: because the sprint prompt named the other five and left this one implied.
#:
#: ``STRUCTURED_INVESTMENT`` and ``TRANSACTION`` both land on ``PLACEMENT_FEE``.
#: That is not laziness and it is not the "collapse everything to ADVISORY_FEE"
#: the prompt warns against: ``revenue_events_type_check`` admits only eight
#: values, of which three cannot come from a fee run at all (``SPV_CARRY`` is
#: deferred and event-driven, ``PASS_THROUGH_MARKUP`` is fee37's cost engine,
#: ``INTEREST_SHARE`` has no fee-run source), leaving five revenue types for six
#: product types. Nothing is lost: ``revenue_events.product_type`` carries the
#: distinction on the row itself, so the product cut still separates them. If
#: the two ever need to differ in the GL, the CHECK needs a new value first.
#: Recorded as finding F39-C.
PRODUCT_TYPE_TO_REVENUE_TYPE: Mapping[str, str] = {
    "ASSET_MANAGEMENT": "ADVISORY_FEE",
    "SPV": "SPV_MGMT_FEE",
    "STRUCTURED_INVESTMENT": "PLACEMENT_FEE",
    "PLANNING": "PLANNING_FEE",
    "CLUB_DUES": "CLUB_DUES",
    "TRANSACTION": "PLACEMENT_FEE",
}

assert set(PRODUCT_TYPE_TO_REVENUE_TYPE) == set(PRODUCT_TYPES), (
    "PRODUCT_TYPE_TO_REVENUE_TYPE has drifted from fee_schedules' deployed "
    f"product_type vocabulary: {set(PRODUCT_TYPE_TO_REVENUE_TYPE) ^ set(PRODUCT_TYPES)}"
)
assert set(PRODUCT_TYPE_TO_REVENUE_TYPE.values()) <= set(REVENUE_TYPES), (
    "PRODUCT_TYPE_TO_REVENUE_TYPE maps to a revenue_type the deployed CHECK "
    f"refuses: {set(PRODUCT_TYPE_TO_REVENUE_TYPE.values()) - set(REVENUE_TYPES)}"
)


# ═══════════════════════════════════════════════════════════════════════════
# cost_type -> P&L band
# ═══════════════════════════════════════════════════════════════════════════

#: Costs attributable to serving the client that are NOT people-time and NOT
#: firm overhead. These are the ones a client-level margin should carry.
DIRECT_COST_TYPES = (
    "CUSTODY",
    "MODEL_FEE",
    "DIRECT_INDEXING",
    "SUBSCRIPTION",
    "ADMIN",
    "TECH",
    "REFERRAL",
)

#: People. Split out because "is this relationship worth the advisor's time" is
#: a different question from "does this relationship cover its vendor bills",
#: and the P&L answers both by showing the margin on either side of it.
SERVICE_COST_TYPES = ("ADVISOR_COMP", "SERVICE_TIME")

#: The allocated band. One value today, kept as a tuple so adding a second
#: allocated cost type is a one-line change that the partition assert checks.
OVERHEAD_COST_TYPES = ("OVERHEAD_ALLOC",)

COST_BANDS: Mapping[str, tuple[str, ...]] = {
    "direct_costs": DIRECT_COST_TYPES,
    "service_costs": SERVICE_COST_TYPES,
    "allocated_overhead": OVERHEAD_COST_TYPES,
}

_banded = [t for band in COST_BANDS.values() for t in band]
assert len(_banded) == len(set(_banded)), (
    f"a cost_type is in two P&L bands at once: "
    f"{sorted({t for t in _banded if _banded.count(t) > 1})}"
)
assert set(_banded) == set(COST_TYPES), (
    "the P&L band partition does not cover cost_events_cost_type_check exactly. "
    f"Unbanded: {sorted(set(COST_TYPES) - set(_banded))}; "
    f"unknown: {sorted(set(_banded) - set(COST_TYPES))}. An unbanded cost_type "
    "would vanish from every margin line while still moving net profit."
)
del _banded


def assert_cost_types_agree() -> None:
    """Fail if :mod:`services.cost_model` and this module disagree on cost_type.

    Imported lazily and called explicitly rather than at import time: the
    verify script wants this as its own named assertion, and a module-level
    circular import between the cost engine and the P&L reader would be a worse
    trade than an explicit call.
    """
    from services.cost_model import COST_TYPES as ENGINE_COST_TYPES

    if set(ENGINE_COST_TYPES) != set(COST_TYPES):
        raise ProfitabilityError(
            "services.cost_model.COST_TYPES and services.profitability.COST_TYPES "
            f"have drifted: {set(ENGINE_COST_TYPES) ^ set(COST_TYPES)}. The P&L "
            "band partition is asserted against this module's copy, so drift "
            "here means a real cost_type is silently unbanded",
            symmetric_difference=sorted(set(ENGINE_COST_TYPES) ^ set(COST_TYPES)),
        )


# ═══════════════════════════════════════════════════════════════════════════
# The standard line order
# ═══════════════════════════════════════════════════════════════════════════

#: (field name, human label). FIXED. See the module docstring — this order is a
#: decision about how the firm argues about client profitability, not styling.
PNL_LINE_ORDER: tuple[tuple[str, str], ...] = (
    ("gross_revenue", "Gross revenue"),
    ("direct_costs", "Direct costs"),
    ("contribution_margin_direct", "Contribution margin (direct)"),
    ("service_costs", "Advisor comp / service cost"),
    ("contribution_margin_after_service", "Contribution margin (after service)"),
    ("allocated_overhead", "Allocated overhead"),
    ("net_profit", "Net profit"),
)

#: Which lines are subtractions, for a renderer that wants to show them as
#: "(4,000)" without re-deriving the arithmetic from the labels.
PNL_COST_LINES = ("direct_costs", "service_costs", "allocated_overhead")


UNVERIFIED_RATE_CAVEAT = (
    "UNVERIFIED RATES: at least one cost in this cut is a pass-through, so its "
    "amount rests on cost_schedules / provider_benefit_schedules rows whose "
    "source_url has not been re-checked against a primary source (fee37 "
    "finding F6). Treat those cost lines as order-of-magnitude, not as a bill."
)


# ═══════════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════════


class ProfitabilityError(FeeCalcError):
    """A ``ValueError`` subclass, matching fee34/35/36/37's error base."""

    code = "profitability_error"


class RunNotPostedError(ProfitabilityError):
    """Revenue was requested for a run that has not reached POSTED."""

    code = "profitability_run_not_posted"


class UnknownProductTypeError(ProfitabilityError):
    """A fee_run_line carries a product_type with no revenue_type mapping."""

    code = "profitability_unknown_product_type"


class InvalidCutError(ProfitabilityError):
    """The requested slice is not one of the eight."""

    code = "profitability_invalid_cut"


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — revenue emission
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EmissionResult:
    """What :func:`emit_revenue_for_run` actually did, per line.

    ``emitted`` and ``skipped`` are separate counts rather than one "processed"
    number because the whole idempotency question is which of the two happened.
    """

    run_id: str
    run_status: str
    lines: int
    emitted: int
    skipped: int
    revenue_event_ids: tuple[str, ...]
    total_amount: Decimal

    @property
    def was_noop(self) -> bool:
        return self.emitted == 0 and self.skipped == self.lines


def revenue_type_for(product_type: str) -> str:
    """Map a ``fee_run_lines.product_type`` onto a legal ``revenue_type``."""
    try:
        return PRODUCT_TYPE_TO_REVENUE_TYPE[product_type]
    except KeyError:
        raise UnknownProductTypeError(
            f"fee_run_line product_type {product_type!r} has no revenue_type "
            f"mapping. Known: {sorted(PRODUCT_TYPE_TO_REVENUE_TYPE)}. Emitting "
            f"it as ADVISORY_FEE would silently misclassify real revenue",
            product_type=product_type,
        ) from None


async def emit_revenue_for_run(conn, org_id: str, run_id: str) -> EmissionResult:
    """One ``revenue_events`` row per ``fee_run_line`` of a POSTED run.

    Idempotent by the deployed partial unique index
    ``revenue_events_source_dedupe_uq (org_id, source_type, source_id) WHERE
    system_to IS NULL AND source_id IS NOT NULL``. The INSERT names that index
    by its columns AND its predicate and takes ``DO NOTHING``, so re-processing
    a run is a counted no-op — ``skipped`` goes up, nothing raises, and no
    caller ever has to read a constraint name out of a database error to work
    out that the work was already done.

    ``event_date`` is the run's ``period_end``, not ``posted_at``. Recognition
    is ACCRUAL: the revenue belongs to the period it was earned in, and posting
    a January run in March should not move January's revenue into March. It
    also keeps the value deterministic — ``posted_at`` is a ``timestamptz``
    whose ``::date`` depends on the session timezone.

    NOT transactional on its own. Call it inside the caller's transaction —
    :func:`services.fee_runs.post_run` does — so that a run cannot reach POSTED
    with its revenue half-written.
    """
    run = await get_run(conn, org_id, run_id)
    if run["status"] not in IMMUTABLE_STATUSES:
        raise RunNotPostedError(
            f"fee_run {run_id} is {run['status']}; revenue is emitted only once "
            f"a run reaches one of {IMMUTABLE_STATUSES}. Emitting from a "
            f"re-previewable run would book revenue that can still change",
            run_id=run_id, status=run["status"],
        )

    lines = await list_lines(conn, org_id, run_id)
    emitted_ids: list[str] = []
    skipped = 0
    total = ZERO

    for line in lines:
        revenue_type = revenue_type_for(line["product_type"])
        new_id = await conn.fetchval(
            f"""INSERT INTO {T_REVENUE}
                  (org_id, event_date, period_start, period_end, amount, currency,
                   revenue_type, recognition, account_id, entity_id, household_id,
                   billing_group_id, advisor_id, product_type, source_type, source_id)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9::uuid, $10::uuid,
                        $11::uuid, $12::uuid, $13::uuid, $14, $15, $16::uuid)
                ON CONFLICT (org_id, source_type, source_id)
                  WHERE system_to IS NULL AND source_id IS NOT NULL
                DO NOTHING
                RETURNING id::text""",
            org_id, run["period_end"], run["period_start"], run["period_end"],
            line["net_fee"], line["currency"], revenue_type, RECOGNITION_ACCRUAL,
            line["account_id"], line["entity_id"], line["household_id"],
            line["billing_group_id"], line["advisor_id"], line["product_type"],
            SOURCE_FEE_RUN_LINE, line["id"],
        )
        if new_id is None:
            skipped += 1
        else:
            emitted_ids.append(new_id)
            total += Decimal(line["net_fee"])

    return EmissionResult(
        run_id=run_id,
        run_status=run["status"],
        lines=len(lines),
        emitted=len(emitted_ids),
        skipped=skipped,
        revenue_event_ids=tuple(emitted_ids),
        total_amount=total,
    )


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 — the eight cuts
# ═══════════════════════════════════════════════════════════════════════════

CUT_ACCOUNT = "ACCOUNT"
CUT_ACCOUNTS = "ACCOUNTS"
CUT_HOUSEHOLD = "HOUSEHOLD"
CUT_HOUSEHOLDS = "HOUSEHOLDS"
CUT_BILLING_GROUP = "BILLING_GROUP"
CUT_ADVISOR = "ADVISOR"
CUT_PRODUCT_TYPE = "PRODUCT_TYPE"
CUT_FIRM = "FIRM"

#: kind -> (view column, takes a list?). ``FIRM`` filters on nothing.
_CUT_COLUMNS: Mapping[str, tuple[str | None, bool]] = {
    CUT_ACCOUNT: ("account_id", False),
    CUT_ACCOUNTS: ("account_id", True),
    CUT_HOUSEHOLD: ("household_id", False),
    CUT_HOUSEHOLDS: ("household_id", True),
    CUT_BILLING_GROUP: ("billing_group_id", False),
    CUT_ADVISOR: ("advisor_id", False),
    CUT_PRODUCT_TYPE: ("product_type", False),
    CUT_FIRM: (None, False),
}

CUT_KINDS = tuple(_CUT_COLUMNS)

#: The columns that are uuid in the view and so need an explicit cast; the
#: product cut's column is text. Getting this wrong is a runtime type error on
#: the first query, not a silent wrong answer, but naming it keeps the SQL
#: builder readable.
_UUID_CUT_COLUMNS = frozenset(
    {"account_id", "household_id", "billing_group_id", "advisor_id"}
)


@dataclass(frozen=True)
class Cut:
    """One of the eight slices. Validated on construction, not at query time."""

    kind: str
    value: str | None = None
    values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in _CUT_COLUMNS:
            raise InvalidCutError(
                f"unknown cut {self.kind!r}. The eight are {list(CUT_KINDS)}",
                kind=self.kind,
            )
        column, is_list = _CUT_COLUMNS[self.kind]
        if column is None:
            if self.value is not None or self.values:
                raise InvalidCutError(
                    "the FIRM cut takes no value — it is the whole firm. Pass "
                    "an account or household cut to narrow it",
                    kind=self.kind,
                )
            return
        if is_list:
            if not self.values:
                raise InvalidCutError(
                    f"cut {self.kind} needs a non-empty `values` list. An empty "
                    f"set would return a zero P&L that looks like a real one",
                    kind=self.kind,
                )
            if self.value is not None:
                raise InvalidCutError(
                    f"cut {self.kind} takes `values`, not `value`",
                    kind=self.kind,
                )
        else:
            if not self.value:
                raise InvalidCutError(
                    f"cut {self.kind} needs a `value`", kind=self.kind
                )
            if self.values:
                raise InvalidCutError(
                    f"cut {self.kind} takes `value`, not `values`", kind=self.kind
                )

    # -- constructors, so callers never hand-build the wrong field ----------

    @classmethod
    def account(cls, account_id: str) -> "Cut":
        return cls(CUT_ACCOUNT, value=account_id)

    @classmethod
    def accounts(cls, account_ids: Iterable[str]) -> "Cut":
        return cls(CUT_ACCOUNTS, values=tuple(account_ids))

    @classmethod
    def household(cls, household_id: str) -> "Cut":
        return cls(CUT_HOUSEHOLD, value=household_id)

    @classmethod
    def households(cls, household_ids: Iterable[str]) -> "Cut":
        return cls(CUT_HOUSEHOLDS, values=tuple(household_ids))

    @classmethod
    def billing_group(cls, billing_group_id: str) -> "Cut":
        return cls(CUT_BILLING_GROUP, value=billing_group_id)

    @classmethod
    def advisor(cls, advisor_id: str) -> "Cut":
        return cls(CUT_ADVISOR, value=advisor_id)

    @classmethod
    def product_type(cls, product_type: str) -> "Cut":
        if product_type not in PRODUCT_TYPES:
            raise InvalidCutError(
                f"unknown product_type {product_type!r}. Deployed: "
                f"{list(PRODUCT_TYPES)}",
                product_type=product_type,
            )
        return cls(CUT_PRODUCT_TYPE, value=product_type)

    @classmethod
    def firm(cls) -> "Cut":
        return cls(CUT_FIRM)

    def describe(self) -> str:
        if self.kind == CUT_FIRM:
            return "the whole firm"
        if self.values:
            return f"{self.kind} in ({len(self.values)} ids)"
        return f"{self.kind} = {self.value}"

    def sql(self, next_param: int, alias: str = "") -> tuple[str, list[Any]]:
        """Return ``(predicate, params)`` for this cut, parameterised.

        ``next_param`` is the 1-based index of the next free ``$n``. ``alias``
        qualifies the column — mandatory wherever the view is joined to
        ``cost_events``, since both carry ``org_id`` and an unqualified
        reference is an ambiguous-column error, not a wrong answer, but only
        on the code path that happens to join. Returns an empty predicate for
        FIRM. Never interpolates a value into SQL text.
        """
        column, is_list = _CUT_COLUMNS[self.kind]
        if column is None:
            return "", []
        col = f"{alias}.{column}" if alias else column
        cast = "::uuid" if column in _UUID_CUT_COLUMNS else ""
        if is_list:
            return f" AND {col} = ANY(${next_param}{cast}[])", [list(self.values)]
        return f" AND {col} = ${next_param}{cast}", [self.value]


# ═══════════════════════════════════════════════════════════════════════════
# The P&L itself
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ProfitAndLoss:
    """The seven standard lines, in the fixed order, plus its own provenance.

    Cost lines are POSITIVE magnitudes. The margins already have them
    subtracted. ``net_profit`` is cross-checked against the raw
    ``SUM(signed_amount)`` of the same rows on construction — see
    :data:`band_check_delta`.
    """

    org_id: str
    cut: Cut
    period_start: date | None
    period_end: date | None

    gross_revenue: Decimal
    direct_costs: Decimal
    contribution_margin_direct: Decimal
    service_costs: Decimal
    contribution_margin_after_service: Decimal
    allocated_overhead: Decimal
    net_profit: Decimal

    revenue_rows: int
    cost_rows: int
    band_check_delta: Decimal
    warnings: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # The line order is load-bearing, so prove it from the dataclass rather
        # than trusting that nobody reordered the fields.
        names = [name for name, _ in PNL_LINE_ORDER]
        fields = [f.name for f in self.__dataclass_fields__.values()]  # type: ignore[attr-defined]
        positions = [fields.index(n) for n in names]
        if positions != sorted(positions):
            raise ProfitabilityError(
                "ProfitAndLoss's fields no longer appear in PNL_LINE_ORDER order. "
                "The order is the deliverable — margin before allocation has to "
                "come before margin after it",
                expected=names,
            )

    @property
    def margin_pct(self) -> Decimal | None:
        """Net profit over gross revenue, or None when there is no revenue.

        None rather than zero: a household with costs and no revenue has an
        undefined margin, and reporting 0% would sort it in with break-even
        clients instead of flagging it.
        """
        if self.gross_revenue == ZERO:
            return None
        return self.net_profit / self.gross_revenue

    def lines(self) -> list[dict[str, Any]]:
        """The seven lines, in order, ready to render."""
        return [
            {
                "key": key,
                "label": label,
                "amount": getattr(self, key),
                "is_cost": key in PNL_COST_LINES,
            }
            for key, label in PNL_LINE_ORDER
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "cut": {
                "kind": self.cut.kind,
                "value": self.cut.value,
                "values": list(self.cut.values),
                "describe": self.cut.describe(),
            },
            "period_start": self.period_start,
            "period_end": self.period_end,
            "lines": self.lines(),
            "margin_pct": self.margin_pct,
            "revenue_rows": self.revenue_rows,
            "cost_rows": self.cost_rows,
            "warnings": list(self.warnings),
            "caveats": list(self.caveats),
        }


def _period_sql(
    next_param: int,
    period_start: date | None,
    period_end: date | None,
    alias: str = "",
) -> tuple[str, list[Any]]:
    """Window on ``event_date``, either bound optional. ``alias`` qualifies it."""
    col = f"{alias}.event_date" if alias else "event_date"
    clauses: list[str] = []
    params: list[Any] = []
    if period_start is not None:
        clauses.append(f" AND {col} >= ${next_param + len(params)}")
        params.append(period_start)
    if period_end is not None:
        clauses.append(f" AND {col} <= ${next_param + len(params)}")
        params.append(period_end)
    return "".join(clauses), params


def _band_filter(alias_types: Sequence[str]) -> str:
    """A literal IN-list of cost types. Safe: every element is a module constant."""
    quoted = ", ".join(f"'{t}'" for t in alias_types)
    return f"category IN ({quoted})"


async def profit_and_loss(
    conn,
    org_id: str,
    cut: Cut,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> ProfitAndLoss:
    """The standard P&L for one cut. One aggregate query, no per-cut SQL.

    ``org_id`` comes from the caller's verified session context and is filtered
    explicitly here as well as by RLS. Belt and braces on purpose: parts of this
    application connect as ``postgres``, which has ``rolbypassrls``, so an
    org filter that existed only in a policy would be inert on those paths.
    """
    params: list[Any] = [org_id]
    cut_sql, cut_params = cut.sql(len(params) + 1, alias="v")
    params.extend(cut_params)
    period_sql, period_params = _period_sql(
        len(params) + 1, period_start, period_end, alias="v"
    )
    params.extend(period_params)
    where = f"v.org_id = $1::uuid{cut_sql}{period_sql}"

    row = await conn.fetchrow(
        f"""
        SELECT
          COALESCE(SUM(v.signed_amount) FILTER (
            WHERE v.line_kind = '{LINE_KIND_REVENUE}'), 0)        AS gross_revenue,
          COALESCE(SUM(-v.signed_amount) FILTER (
            WHERE v.line_kind = '{LINE_KIND_COST}'
              AND v.{_band_filter(DIRECT_COST_TYPES)}), 0)        AS direct_costs,
          COALESCE(SUM(-v.signed_amount) FILTER (
            WHERE v.line_kind = '{LINE_KIND_COST}'
              AND v.{_band_filter(SERVICE_COST_TYPES)}), 0)       AS service_costs,
          COALESCE(SUM(-v.signed_amount) FILTER (
            WHERE v.line_kind = '{LINE_KIND_COST}'
              AND v.{_band_filter(OVERHEAD_COST_TYPES)}), 0)      AS allocated_overhead,
          COALESCE(SUM(v.signed_amount), 0)                       AS raw_net,
          COUNT(*) FILTER (WHERE v.line_kind = '{LINE_KIND_REVENUE}') AS revenue_rows,
          COUNT(*) FILTER (WHERE v.line_kind = '{LINE_KIND_COST}')    AS cost_rows
        FROM {V_PROFIT} v
        WHERE {where}
        """,
        *params,
    )

    gross = Decimal(row["gross_revenue"])
    direct = Decimal(row["direct_costs"])
    service = Decimal(row["service_costs"])
    overhead = Decimal(row["allocated_overhead"])
    cm_direct = gross - direct
    cm_after_service = cm_direct - service
    net = cm_after_service - overhead

    warnings, caveats = await _annotate(
        conn, org_id, cut, period_start, period_end, where, params
    )

    # If the bands stopped covering every deployed cost_type, some cost would
    # move raw_net without appearing on any line. Report it rather than let the
    # P&L silently stop adding up.
    delta = net - Decimal(row["raw_net"])
    if delta != ZERO:
        warnings = warnings + (
            f"BANDS INCOMPLETE: net profit from the seven lines ({net}) does not "
            f"equal SUM(signed_amount) ({Decimal(row['raw_net'])}); difference "
            f"{delta}. A cost_type in the data is not in any P&L band",
        )

    return ProfitAndLoss(
        org_id=org_id,
        cut=cut,
        period_start=period_start,
        period_end=period_end,
        gross_revenue=gross,
        direct_costs=direct,
        contribution_margin_direct=cm_direct,
        service_costs=service,
        contribution_margin_after_service=cm_after_service,
        allocated_overhead=overhead,
        net_profit=net,
        revenue_rows=int(row["revenue_rows"]),
        cost_rows=int(row["cost_rows"]),
        band_check_delta=delta,
        warnings=warnings,
        caveats=caveats,
    )


async def _annotate(
    conn,
    org_id: str,
    cut: Cut,
    period_start: date | None,
    period_end: date | None,
    where: str,
    params: Sequence[Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Warnings and caveats derived from the rows this cut actually summed.

    Both are measured, never assumed. A P&L over a cut with no pass-through
    costs and no duplicate risk gets neither, so a caveat that IS present means
    something.
    """
    warnings: list[str] = []
    caveats: list[str] = []

    # Pass-through exposure: joined back to the base table, since
    # is_passed_through is not projected by the view. ``where`` is v-qualified
    # for exactly this query — cost_events carries org_id/event_date too, so an
    # unqualified predicate here is an ambiguous-column error.
    passthrough = await conn.fetchval(
        f"""SELECT EXISTS (
              SELECT 1 FROM {V_PROFIT} v
              JOIN {T_COSTS} c ON c.id = v.id
              WHERE {where} AND v.line_kind = '{LINE_KIND_COST}'
                AND c.is_passed_through
            ) OR EXISTS (
              SELECT 1 FROM {V_PROFIT} v
              WHERE {where} AND v.line_kind = '{LINE_KIND_REVENUE}'
                AND v.category = 'PASS_THROUGH_MARKUP'
            )""",
        *params,
    )
    if passthrough:
        caveats.append(UNVERIFIED_RATE_CAVEAT)

    duplicates = await duplicate_cost_scan(
        conn, org_id, period_start=period_start, period_end=period_end
    )
    if duplicates:
        total = sum(d["duplicate_amount"] for d in duplicates)
        warnings.append(
            f"POSSIBLE DUPLICATE COSTS: {len(duplicates)} cost_events group(s) "
            f"totalling {total} in surplus share the full dedupe tuple but were "
            f"not caught by cost_events_dedupe_uq, because a NULL in "
            f"account_id/household_id/billing_group_id makes that UNIQUE index "
            f"inapplicable (fee37 finding F4). Cost lines here may be overstated."
        )

    return tuple(warnings), tuple(caveats)


async def duplicate_cost_scan(
    conn,
    org_id: str,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> list[dict[str, Any]]:
    """``cost_events`` groups the dedupe index could not have prevented.

    Groups on the SAME tuple ``cost_events_dedupe_uq`` indexes, but restricted
    to rows where at least one of ``account_id`` / ``household_id`` /
    ``billing_group_id`` is NULL — precisely the rows that index does not
    constrain, since Postgres treats NULLs as distinct in a UNIQUE index. Rows
    with all three populated cannot duplicate; including them would report
    nothing and imply the scan had checked something it had not.

    ``duplicate_amount`` is the SURPLUS (n-1 copies), which is what a roll-up
    is overstated by, not the group total.
    """
    clauses = ""
    params: list[Any] = [org_id]
    if period_start is not None:
        params.append(period_start)
        clauses += f" AND event_date >= ${len(params)}"
    if period_end is not None:
        params.append(period_end)
        clauses += f" AND event_date <= ${len(params)}"

    rows = await conn.fetch(
        f"""SELECT cost_provider_id::text AS cost_provider_id, cost_type,
                   account_id::text AS account_id,
                   household_id::text AS household_id,
                   billing_group_id::text AS billing_group_id,
                   period_start, period_end,
                   COUNT(*) AS copies,
                   SUM(amount) - MIN(amount) AS duplicate_amount
            FROM {T_COSTS}
            WHERE org_id = $1::uuid AND system_to IS NULL
              AND (account_id IS NULL OR household_id IS NULL
                   OR billing_group_id IS NULL){clauses}
            GROUP BY 1,2,3,4,5,6,7
            HAVING COUNT(*) > 1
            ORDER BY 8 DESC, 2""",
        *params,
    )
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# Households ranked by margin, WORST FIRST
# ═══════════════════════════════════════════════════════════════════════════

RANK_NET_PROFIT = "net_profit"
RANK_MARGIN_PCT = "margin_pct"
RANK_KEYS = (RANK_NET_PROFIT, RANK_MARGIN_PCT)


@dataclass(frozen=True)
class HouseholdMargin:
    household_id: str
    household_name: str | None
    gross_revenue: Decimal
    direct_costs: Decimal
    contribution_margin_direct: Decimal
    service_costs: Decimal
    contribution_margin_after_service: Decimal
    allocated_overhead: Decimal
    net_profit: Decimal
    margin_pct: Decimal | None

    def lines(self) -> list[dict[str, Any]]:
        return [
            {"key": k, "label": label, "amount": getattr(self, k),
             "is_cost": k in PNL_COST_LINES}
            for k, label in PNL_LINE_ORDER
        ]


async def households_by_margin(
    conn,
    org_id: str,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    rank_by: str = RANK_NET_PROFIT,
    limit: int | None = None,
    include_unhoused: bool = False,
) -> list[HouseholdMargin]:
    """Every household's P&L, worst margin first. The metric that changes behaviour.

    Defaults to ranking on ``net_profit`` — dollars — rather than percentage.
    A percentage on a household with almost no revenue swings wildly and would
    park a trivial account at the top of a list meant to drive a real
    conversation. ``rank_by='margin_pct'`` is available for the other question.

    Households with revenue but no costs still appear; a household with neither
    does not, because it has no rows in the view. ``include_unhoused`` adds the
    ``household_id IS NULL`` bucket (firm-level costs, unhouseholded accounts)
    as a single row with a NULL id — off by default so the ranking is a list of
    real households, but available because that bucket is where a large
    unallocated cost hides.
    """
    if rank_by not in RANK_KEYS:
        raise InvalidCutError(
            f"rank_by must be one of {list(RANK_KEYS)}, not {rank_by!r}",
            rank_by=rank_by,
        )

    params: list[Any] = [org_id]
    period_sql, period_params = _period_sql(
        len(params) + 1, period_start, period_end, alias="v"
    )
    params.extend(period_params)
    housed = "" if include_unhoused else " AND v.household_id IS NOT NULL"

    rows = await conn.fetch(
        f"""
        WITH agg AS (
          SELECT v.household_id,
            COALESCE(SUM(v.signed_amount) FILTER (
              WHERE v.line_kind = '{LINE_KIND_REVENUE}'), 0)      AS gross_revenue,
            COALESCE(SUM(-v.signed_amount) FILTER (
              WHERE v.line_kind = '{LINE_KIND_COST}'
                AND v.{_band_filter(DIRECT_COST_TYPES)}), 0)      AS direct_costs,
            COALESCE(SUM(-v.signed_amount) FILTER (
              WHERE v.line_kind = '{LINE_KIND_COST}'
                AND v.{_band_filter(SERVICE_COST_TYPES)}), 0)     AS service_costs,
            COALESCE(SUM(-v.signed_amount) FILTER (
              WHERE v.line_kind = '{LINE_KIND_COST}'
                AND v.{_band_filter(OVERHEAD_COST_TYPES)}), 0)    AS allocated_overhead
          FROM {V_PROFIT} v
          WHERE v.org_id = $1::uuid{housed}{period_sql}
          GROUP BY v.household_id
        )
        SELECT agg.household_id::text AS household_id, h.name AS household_name,
               agg.gross_revenue, agg.direct_costs, agg.service_costs,
               agg.allocated_overhead,
               agg.gross_revenue - agg.direct_costs - agg.service_costs
                 - agg.allocated_overhead AS net_profit,
               CASE WHEN agg.gross_revenue = 0 THEN NULL
                    ELSE (agg.gross_revenue - agg.direct_costs - agg.service_costs
                          - agg.allocated_overhead) / agg.gross_revenue
               END AS margin_pct
        FROM agg
        LEFT JOIN public.households h ON h.id = agg.household_id
                                     AND h.org_id = $1::uuid
        ORDER BY {"net_profit" if rank_by == RANK_NET_PROFIT else "margin_pct"}
                 ASC NULLS LAST,
                 net_profit ASC, agg.household_id
        {"LIMIT " + str(int(limit)) if limit is not None else ""}
        """,
        *params,
    )

    out: list[HouseholdMargin] = []
    for r in rows:
        gross = Decimal(r["gross_revenue"])
        direct = Decimal(r["direct_costs"])
        service = Decimal(r["service_costs"])
        overhead = Decimal(r["allocated_overhead"])
        cm_direct = gross - direct
        out.append(
            HouseholdMargin(
                household_id=r["household_id"],
                household_name=r["household_name"],
                gross_revenue=gross,
                direct_costs=direct,
                contribution_margin_direct=cm_direct,
                service_costs=service,
                contribution_margin_after_service=cm_direct - service,
                allocated_overhead=overhead,
                net_profit=Decimal(r["net_profit"]),
                margin_pct=(
                    None if r["margin_pct"] is None else Decimal(r["margin_pct"])
                ),
            )
        )
    return out


__all__ = [
    "COST_BANDS",
    "COST_TYPES",
    "CUT_KINDS",
    "DIRECT_COST_TYPES",
    "OVERHEAD_COST_TYPES",
    "PNL_COST_LINES",
    "PNL_LINE_ORDER",
    "PRODUCT_TYPE_TO_REVENUE_TYPE",
    "RANK_KEYS",
    "RANK_MARGIN_PCT",
    "RANK_NET_PROFIT",
    "RECOGNITIONS",
    "REVENUE_TYPES",
    "SERVICE_COST_TYPES",
    "SOURCE_TYPES",
    "UNVERIFIED_RATE_CAVEAT",
    "Cut",
    "EmissionResult",
    "HouseholdMargin",
    "InvalidCutError",
    "ProfitAndLoss",
    "ProfitabilityError",
    "RunNotPostedError",
    "UnknownProductTypeError",
    "assert_cost_types_agree",
    "duplicate_cost_scan",
    "emit_revenue_for_run",
    "households_by_margin",
    "profit_and_loss",
    "revenue_type_for",
]
