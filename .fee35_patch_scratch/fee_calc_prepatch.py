"""The fee calculation engine. Sprint fee35.

Takes a rule (a ``fee_schedules`` row and its tiers), a set of facts (balances,
flows, positions) and the adjustments the caller has already scope-resolved
(exclusions, discounts, credits), and returns a number plus the reasoning that
produced it. It writes nothing. ``fee_runs``/``fee_run_lines`` is fee36.

``verify_fee35.py`` check [V1] asserts that mechanically rather than asking
you to trust this paragraph, and does it three ways: no database token appears
in either module's CODE (docstrings and comments stripped first, so prose about
the check does not satisfy the check); importing ``services.fee_calc`` in a
clean subprocess interpreter loads zero database drivers anywhere in its
transitive import graph; and no function in either module takes a
connection-shaped parameter.


CALC_DETAIL IS AN OUTPUT, NOT A LOG
──────────────────────────────────────────────────────────────────────────────
Every stage appends one entry to ``calc_detail['steps']``. The entries carry
the inputs the stage read, the arithmetic it did and the amount it handed on,
as decimal STRINGS — ``json.dumps(result.calc_detail)`` works with no encoder,
which is the requirement for fee36 to store it in a ``jsonb`` column.

The test for whether this is enough is: an operator holding one row of
``fee_run_lines`` and nothing else can answer "why is this fee this number"
without re-running the engine and without the schedule in front of them. So a
tier step lists every slice with its base, its rate and its amount — not just
the total — and a flow the engine DECLINED to weight appears with the reason it
was declined, because "the engine did not see it" and "the engine saw it and
skipped it" are different bugs and only one of them is a bug.


THE ORDER COMES FROM THE SCHEDULE
──────────────────────────────────────────────────────────────────────────────
``ordering_policy`` is walked, not assumed. :func:`_run_policy` iterates the
schedule's own list and dispatches on each name; the default sequence appears
in this module only as fee34's ``ORDERING_STEPS`` constant, used to validate
that a policy is a permutation. Golden cases 6 and 7 are the same schedule and
the same balance with two different policies and two different answers.

Two positions in the policy are constrained rather than free, and the engine
says so instead of quietly re-sorting:

  * ``EXCLUSIONS`` must precede ``TIERS``. Exclusions change the billable
    VALUE; tiers turn a value into money. A policy that tiers first has
    nothing coherent for the exclusions step to do afterwards, and picking one
    of the plausible re-interpretations would mean an operator's typo silently
    changes what a client is charged. :class:`OrderingNotSupportedError`.

  * A ``NET_OF_CREDITS`` discount requires ``CREDITS`` before ``DISCOUNTS``.
    ``applies_to`` is not the same knob as ``ordering_policy`` — the policy
    says WHEN the discount runs, ``applies_to`` says what a percentage of it is
    a percentage OF. Asking for a percentage of a net figure that has not been
    computed yet is a contradiction, not a default.

``PRORATION`` is deliberately NOT a policy step, because it is not one in the
column: the deployed default has six entries and proration is not among them.
It is applied immediately after ``TIERS``, on the period gross, and appears in
the trace in that position.


ANNUAL RATES, PER-PERIOD AMOUNTS
──────────────────────────────────────────────────────────────────────────────
The schema stores ``rate_bps`` and ``flat_amount`` on ``fee_schedule_tiers``
with no unit anywhere. This engine reads BOTH as ANNUAL and divides the tiered
total by ``PERIODS_PER_YEAR[billing_frequency]``.

``minimum_fee``, ``maximum_fee``, ``fee_discounts.value`` (for DOLLAR_CREDIT)
and ``fee_exclusions.flat_amount`` are read as PER-PERIOD amounts — a $900
quarterly minimum is entered as 900, not 3600.

Neither reading is derivable from the schema. Both are emitted into
``calc_detail['assumptions']`` on every run that depends on them, so the
assumption travels with the number rather than living only here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dc_field
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Sequence

from services.fee_calc_inputs import (
    PERIODS_PER_YEAR,
    AccountCalcRequest,
    AccountPeriodInput,
    BillingPeriod,
    CreditInput,
    DailyBalanceInput,
    DiscountInput,
    ExclusionInput,
    FeeCalcError,
    FeeCalcInputError,
    FeeScheduleInput,
    FeeTierInput,
    FlowInput,
    PositionInput,
    money,
)
from services.fee_validation import raise_if_invalid, validate_tiers

#: Bumped when a change to this module could change a number. Stamped into
#: every ``calc_detail`` so a fee_run_line recomputed under a later engine can
#: be told apart from one that was always wrong.
ENGINE_VERSION = "fee35.1"

ZERO = Decimal(0)
ONE = Decimal(1)
CENTS = Decimal("0.01")
_BPS = Decimal(10000)
_HUNDRED = Decimal(100)


# ── The assumptions, named ───────────────────────────────────────────────────
#
# Each of these is a place the deployed schema does not say enough and the
# engine had to choose. They are constants rather than prose so a golden case
# can assert one is present, and so fee36 can surface them next to the invoice.

A_BLENDED = (
    "tier_method='BLENDED_PUBLISHED' was calculated as GRADUATED. The design "
    "doc does not specify its mechanics and the deployed CHECK constraint "
    "admits the value without defining it; GRADUATED was chosen because it is "
    "the only reading under which a published blended rate card and a "
    "graduated one agree at every tier boundary. Revisit before billing a real "
    "BLENDED_PUBLISHED schedule."
)
A_BUSINESS_DAYS = (
    "proration_method='BUSINESS_DAYS' used a plain Monday-Friday calendar. "
    "This codebase has no holiday calendar table — a market holiday falling in "
    "the period is therefore counted as a business day, which overstates the "
    "denominator and understates a partial-period fee slightly."
)
A_ANNUAL_RATES = (
    "rate_bps and tier flat_amount were read as ANNUAL and divided by "
    f"periods-per-year. The schema records no unit for either."
)
A_PERIOD_AMOUNTS = (
    "minimum_fee, maximum_fee, DOLLAR_CREDIT discount value and exclusion "
    "flat_amount were read as PER-BILLING-PERIOD amounts, not annual."
)
A_PCT_SCALE = (
    "fee_discounts.value for PCT_OFF was read as a PERCENT in [0, 100] "
    "(20 means 20%). Note the contrast with fee_credits.offset_pct, which the "
    "deployed CHECK constraint fee_credits_offset_pct_range confines to [0, 1] "
    "— two adjacent tables express a proportion on two different scales and "
    "nothing in the schema says so."
)
A_VALUATION_BASIS = (
    "Valuation used account_balances_daily.total_market_value alone. "
    "accrued_income is a separate column and was NOT added to the billable "
    "base."
)
A_ALT_FREQUENCY = (
    "A REDUCED_RATE carve-out was tiered on its alt schedule's own tiers but "
    "converted to a period amount using the PRIMARY schedule's "
    "billing_frequency, because the line is billed on the primary's cycle."
)
A_MARGIN_MAGNITUDE = (
    "margin_treatment='REDUCE_BILLABLE' subtracted the absolute value of "
    "margin_balance. The column carries no sign convention and both feeds seen "
    "so far report a debit as a positive number."
)


# ═══════════════════════════════════════════════════════════════════════════
# Errors
# ═══════════════════════════════════════════════════════════════════════════


class OrderingNotSupportedError(FeeCalcError):
    """The policy is a valid permutation but asks for something incoherent."""

    code = "fee_ordering_not_supported"


class ValuationUnavailableError(FeeCalcError):
    """No balance row the chosen valuation method can use.

    Its own class because the remedy is an import, not a schedule edit — fee36
    needs to tell these apart to decide whether a run is retryable.
    """

    code = "fee_valuation_unavailable"


class AmbiguousBalanceError(FeeCalcError):
    """Two source systems disagree about the same day and neither is flagged.

    ``account_balances_daily``'s primary key is
    ``(org_id, account_id, as_of_date, source_system)``, so this is a shape the
    table invites. Averaging them would produce a billable value no custodian
    statement matches; picking one alphabetically would make the fee depend on
    a feed's name. So: refuse, and name both values.
    """

    code = "fee_balance_ambiguous"


class AltScheduleMissingError(FeeCalcError):
    """A REDUCED_RATE exclusion points at a schedule the caller did not load."""

    code = "fee_alt_schedule_missing"


class DiscountNotCalculableError(FeeCalcError):
    """A discount this engine deliberately refuses to interpret."""

    code = "fee_discount_not_calculable"


class GroupScopeMissingError(FeeCalcError):
    """A group-scoped minimum on an account with no household/billing group."""

    code = "fee_group_scope_missing"


# ═══════════════════════════════════════════════════════════════════════════
# Serialisation helpers
# ═══════════════════════════════════════════════════════════════════════════


def _d(value: Decimal | None) -> str | None:
    """A Decimal as a string. Never a float — that is the whole point."""
    return None if value is None else str(value)


def _dt(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# Stage: day counting and proration
# ═══════════════════════════════════════════════════════════════════════════


def count_days(start: date, end: date, method: str) -> int:
    """Inclusive day count between two dates under a proration method.

    ``NONE`` counts calendar days like ``CALENDAR_DAYS``; the difference
    between them is not how days are counted but whether the count is used at
    all (see :func:`proration_factor`).
    """
    if end < start:
        return 0
    if method == "BUSINESS_DAYS":
        total = 0
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                total += 1
            cursor += timedelta(days=1)
        return total
    return (end - start).days + 1


def proration_factor(
    period: BillingPeriod, *, proration_method: str, billing_timing: str
) -> tuple[Decimal, dict[str, Any]]:
    """The multiplier applied to a full-period gross fee, and its trace.

    Three outcomes, and the third is the one that is easy to get wrong:

    * A full period, or ``proration_method='NONE'`` — factor 1.

    * A mid-period INCEPTION — factor is the in-service share of the period.
      Same for ADVANCE and ARREARS: the stub going forward and the stub just
      ended are the same fraction of the same period.

    * A mid-period TERMINATION on a schedule billed in ADVANCE — factor is
      NEGATIVE. The full period was already charged at its start, so the line
      this run produces is a refund of the unearned remainder, not a bill for
      the earned part. Returning ``+earned/total`` here would charge the client
      a second time for days they had already paid for.

      The refund branch is conditioned on the account having been in service at
      period start. An account that both opened AND closed inside one period
      was never billed the full period in advance, so it prorates positively
      like any other stub.
    """
    total = count_days(period.period_start, period.period_end, proration_method)
    window = count_days(period.effective_start, period.effective_end, proration_method)
    detail: dict[str, Any] = {
        "step": "PRORATION",
        "proration_method": proration_method,
        "billing_timing": billing_timing,
        "period_start": _dt(period.period_start),
        "period_end": _dt(period.period_end),
        "service_start": _dt(period.service_start),
        "service_end": _dt(period.service_end),
        "in_service_from": _dt(period.effective_start),
        "in_service_to": _dt(period.effective_end),
        "period_days": total,
        "in_service_days": window,
    }

    if proration_method == "NONE":
        detail["outcome"] = "not_prorated"
        detail["reason"] = (
            "proration_method='NONE' — a partial period is billed in full"
        )
        detail["factor"] = _d(ONE)
        return ONE, detail

    if total == 0:
        detail["outcome"] = "zero_denominator"
        detail["reason"] = (
            "the period contains no countable days under this method"
        )
        detail["factor"] = _d(ZERO)
        return ZERO, detail

    if not period.is_partial:
        detail["outcome"] = "full_period"
        detail["factor"] = _d(ONE)
        return ONE, detail

    started_before_period = (
        period.service_start is None or period.service_start <= period.period_start
    )
    if period.is_termination and billing_timing == "ADVANCE" and started_before_period:
        unearned = total - window
        factor = -(Decimal(unearned) / Decimal(total))
        detail["outcome"] = "termination_refund"
        detail["unearned_days"] = unearned
        detail["reason"] = (
            f"billed in ADVANCE and terminated on "
            f"{_dt(period.effective_end)}; {unearned} of {total} days were paid "
            f"for and not earned, so this line is a refund"
        )
        detail["factor"] = _d(factor)
        return factor, detail

    factor = Decimal(window) / Decimal(total)
    detail["outcome"] = (
        "termination_partial" if period.is_termination else "inception_partial"
    )
    detail["factor"] = _d(factor)
    return factor, detail


# ═══════════════════════════════════════════════════════════════════════════
# Stage: valuation
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Valuation:
    """What the account was worth, under one valuation method."""

    method: str
    total_market_value: Decimal
    cash_value: Decimal
    margin_balance: Decimal
    as_of: date | None
    dates_used: tuple[date, ...]


def _is_month_end(day: date) -> bool:
    return (day + timedelta(days=1)).day == 1


def _pick_one(rows: Sequence[DailyBalanceInput], day: date) -> DailyBalanceInput:
    """One balance for one day, or refuse. See :class:`AmbiguousBalanceError`."""
    same = sorted(
        (r for r in rows if r.as_of_date == day), key=lambda r: r.source_system
    )
    if not same:
        raise ValuationUnavailableError(
            f"no balance row for {day.isoformat()}", as_of_date=day.isoformat()
        )
    values = {r.total_market_value for r in same}
    if len(values) > 1:
        raise AmbiguousBalanceError(
            f"{len(same)} source systems report different total_market_value for "
            f"{day.isoformat()} and none is flagged is_billing_source: "
            + ", ".join(f"{r.source_system}={r.total_market_value}" for r in same),
            as_of_date=day.isoformat(),
            sources={r.source_system: str(r.total_market_value) for r in same},
        )
    return same[0]


def resolve_valuation(
    balances: Iterable[DailyBalanceInput], period: BillingPeriod, method: str
) -> tuple[Valuation, dict[str, Any]]:
    """Turn a balance history into one value, under the schedule's method.

    Two filters run before any method-specific logic, in this order:

    1. **Billing source.** If ANY row in the period is flagged
       ``is_billing_source``, only flagged rows are considered. That flag exists
       to settle which custodian feed is authoritative for billing, and honouring
       it only when it is convenient would make it decorative.
    2. **The period.** Rows outside ``[period_start, period_end]`` are dropped
       here rather than trusted to have been filtered upstream — a query that
       returned last quarter too would otherwise move the average with nothing
       in the trace to show it.
    """
    rows = list(balances)
    detail: dict[str, Any] = {
        "step": "VALUATION",
        "valuation_method": method,
        "rows_supplied": len(rows),
    }

    flagged = [r for r in rows if r.is_billing_source]
    if flagged:
        rows = flagged
        detail["billing_source_filter"] = (
            f"{len(flagged)} of {detail['rows_supplied']} rows are flagged "
            f"is_billing_source; only those were used"
        )
    else:
        detail["billing_source_filter"] = (
            "no row is flagged is_billing_source; all rows were considered"
        )

    if method == "PERIOD_START":
        at_or_before = [r for r in rows if r.as_of_date <= period.period_start]
        if at_or_before:
            day = max(r.as_of_date for r in at_or_before)
            in_scope = at_or_before
            if day != period.period_start:
                detail["substitution"] = (
                    f"no balance on {_dt(period.period_start)}; used the latest "
                    f"earlier one, {day.isoformat()}"
                )
        else:
            in_period = [
                r for r in rows
                if period.period_start <= r.as_of_date <= period.period_end
            ]
            if not in_period:
                raise ValuationUnavailableError(
                    f"PERIOD_START valuation has no balance on or before "
                    f"{_dt(period.period_end)}",
                    account_id=None, period_start=_dt(period.period_start),
                )
            day = min(r.as_of_date for r in in_period)
            in_scope = in_period
            detail["substitution"] = (
                f"no balance on or before {_dt(period.period_start)}; used the "
                f"earliest in-period one, {day.isoformat()}"
            )
        row = _pick_one(in_scope, day)
        detail.update({
            "as_of": _dt(day),
            "source_system": row.source_system,
            "total_market_value": _d(row.total_market_value),
            "cash_value": _d(row.cash_value),
            "margin_balance": _d(row.margin_balance),
        })
        return (
            Valuation(method, row.total_market_value, row.cash_value,
                      row.margin_balance, day, (day,)),
            detail,
        )

    if method == "PERIOD_END":
        at_or_before = [r for r in rows if r.as_of_date <= period.period_end]
        if not at_or_before:
            raise ValuationUnavailableError(
                f"PERIOD_END valuation has no balance on or before "
                f"{_dt(period.period_end)}",
                period_end=_dt(period.period_end),
            )
        day = max(r.as_of_date for r in at_or_before)
        if day != period.period_end:
            detail["substitution"] = (
                f"no balance on {_dt(period.period_end)}; used the latest "
                f"earlier one, {day.isoformat()}"
            )
        row = _pick_one(at_or_before, day)
        detail.update({
            "as_of": _dt(day),
            "source_system": row.source_system,
            "total_market_value": _d(row.total_market_value),
            "cash_value": _d(row.cash_value),
            "margin_balance": _d(row.margin_balance),
        })
        return (
            Valuation(method, row.total_market_value, row.cash_value,
                      row.margin_balance, day, (day,)),
            detail,
        )

    in_period = [
        r for r in rows if period.period_start <= r.as_of_date <= period.period_end
    ]
    if method == "AVG_MONTH_END":
        in_period = [r for r in in_period if _is_month_end(r.as_of_date)]
        detail["filter"] = "month-end dates only"
    if not in_period:
        raise ValuationUnavailableError(
            f"{method} valuation has no usable balance row inside "
            f"{_dt(period.period_start)}..{_dt(period.period_end)}",
            valuation_method=method,
        )

    days = sorted({r.as_of_date for r in in_period})
    picked = [_pick_one(in_period, day) for day in days]
    n = Decimal(len(picked))
    total = sum((r.total_market_value for r in picked), ZERO) / n
    cash = sum((r.cash_value for r in picked), ZERO) / n
    margin = sum((r.margin_balance for r in picked), ZERO) / n
    detail.update({
        "days_averaged": len(days),
        "first_date": _dt(days[0]),
        "last_date": _dt(days[-1]),
        "total_market_value": _d(total),
        "cash_value": _d(cash),
        "margin_balance": _d(margin),
    })
    return (
        Valuation(method, total, cash, margin, days[-1], tuple(days)),
        detail,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Stage: day-weighted flows
# ═══════════════════════════════════════════════════════════════════════════


def flow_adjustment(
    flows: Iterable[FlowInput], period: BillingPeriod, schedule: FeeScheduleInput
) -> tuple[Decimal, dict[str, Any]]:
    """The correction a period-end style valuation needs for mid-period flows.

    A $30,000 deposit on day 47 of 91 is already sitting in the day-91 market
    value at its full size, but it was only in the account for 45 of the 91
    days. The adjustment REMOVES the part that was not there:

        adjustment = -amount * days_absent / period_days

    ``days_remaining`` counts the flow date itself, matching the inclusive
    counting used everywhere else in this module: a deposit on the last day of
    the period was present for one day, not zero.

    ``day_weight_threshold`` is a floor on the flow's SIZE, compared on the
    absolute amount so a $30,000 withdrawal is weighted exactly as a $30,000
    deposit is. A flow under the threshold is not weighted at all — it is not
    weighted by a small factor, it is left entirely alone — and it still gets a
    trace entry saying so.

    Calendar days are used here regardless of ``proration_method``: a deposit
    sits in the account over a weekend, so counting business days would say a
    Friday deposit was absent for two days it was in fact present for. Deliberate
    and separate from proration, which measures a service relationship rather
    than the presence of money.
    """
    rows = sorted(
        flows, key=lambda f: (f.flow_date, str(f.id or ""), str(f.amount))
    )
    period_days = period.calendar_days
    detail: dict[str, Any] = {
        "step": "FLOWS",
        "day_weight_flows": schedule.day_weight_flows,
        "day_weight_threshold": _d(schedule.day_weight_threshold),
        "period_days": period_days,
        "day_count_basis": "CALENDAR_DAYS (always, independent of proration_method)",
        "flows": [],
        "flows_supplied": len(rows),
    }

    if not schedule.day_weight_flows:
        for f in rows:
            detail["flows"].append({
                "flow_date": _dt(f.flow_date),
                "amount": _d(f.amount),
                "flow_type": f.flow_type,
                "outcome": "not_weighted",
                "reason": "schedule.day_weight_flows is false",
                "adjustment": _d(ZERO),
            })
        detail["total_adjustment"] = _d(ZERO)
        return ZERO, detail

    total = ZERO
    threshold = schedule.day_weight_threshold
    for f in rows:
        entry: dict[str, Any] = {
            "flow_date": _dt(f.flow_date),
            "amount": _d(f.amount),
            "flow_type": f.flow_type,
        }
        if not (period.period_start <= f.flow_date <= period.period_end):
            entry.update(outcome="skipped", reason="flow_date is outside the period",
                         adjustment=_d(ZERO))
            detail["flows"].append(entry)
            continue
        if not f.is_billable_flow:
            entry.update(outcome="skipped", reason="is_billable_flow is false",
                         adjustment=_d(ZERO))
            detail["flows"].append(entry)
            continue
        if threshold is not None and abs(f.amount) < threshold:
            entry.update(
                outcome="ignored",
                reason=(f"|{f.amount}| is below day_weight_threshold "
                        f"{threshold}; not weighted at all"),
                adjustment=_d(ZERO),
            )
            detail["flows"].append(entry)
            continue

        days_present = (period.period_end - f.flow_date).days + 1
        days_absent = period_days - days_present
        adjustment = -(f.amount * Decimal(days_absent)) / Decimal(period_days)
        total += adjustment
        entry.update(
            outcome="weighted",
            days_present=days_present,
            days_absent=days_absent,
            weight=_d(Decimal(days_present) / Decimal(period_days)),
            adjustment=_d(adjustment),
            reason=(f"present {days_present} of {period_days} days; the "
                    f"{days_absent}-day absent share of the flow is removed "
                    f"from the billable value"),
        )
        detail["flows"].append(entry)

    detail["total_adjustment"] = _d(total)
    return total, detail


# ═══════════════════════════════════════════════════════════════════════════
# Stage: exclusions and billable value
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CarveOut:
    """Value removed from the primary schedule to be billed on another one."""

    alt_fee_schedule_id: str
    amount: Decimal
    exclusion_id: str | None
    basis_type: str
    basis_value: str | None


def _effective(row: Any, period: BillingPeriod) -> bool:
    """Does this exclusion/discount/credit's window overlap the period?

    The caller resolved SCOPE; dates are still checked here. A row that expired
    last month is scope-correct and must not bill, and the cheapest place to
    catch it is the one place that knows the period.
    """
    frm = getattr(row, "effective_from", None)
    to = getattr(row, "effective_to", None)
    if frm is not None and frm > period.period_end:
        return False
    if to is not None and to < period.period_start:
        return False
    return True


def _positions_for(
    positions: Iterable[PositionInput], account_id: str, period: BillingPeriod
) -> list[PositionInput]:
    """The account's positions as of the period, one row per asset.

    ``portfolio.positions`` is bi-temporal and a caller can legitimately hand
    over several vintages of the same holding. Taking the latest
    ``as_of_date`` per asset — deterministically, breaking ties on the row id —
    is what stops a stale row from either double-counting a carve-out or
    shadowing the current one.
    """
    latest: dict[str, PositionInput] = {}
    candidates = [
        p for p in positions
        if (p.account_id is None or p.account_id == account_id)
        and (p.as_of_date is None or p.as_of_date <= period.period_end)
    ]
    for p in sorted(
        candidates,
        key=lambda p: (p.as_of_date or date.min, str(p.id or ""), p.asset_id),
    ):
        latest[p.asset_id] = p
    return [latest[k] for k in sorted(latest)]


#: The three taxonomy key shapes, per CLAUDE.md Rule 4. Parsed rather than
#: string-matched — see :func:`taxonomy_covers`.
_TAXONOMY_SHAPES = (
    re.compile(r"^taxonomy_sc_(\d+)$"),
    re.compile(r"^taxonomy_mc_(\d+)_(\d+)$"),
    re.compile(r"^taxonomy_sub_(\d+)_(\d+)_(\d+)$"),
)


def _taxonomy_parts(key: str) -> tuple[int, ...] | None:
    """``taxonomy_mc_3_2`` -> ``(3, 2)``. ``None`` if it is not a taxonomy key."""
    for pattern in _TAXONOMY_SHAPES:
        m = pattern.match(key)
        if m:
            return tuple(int(g) for g in m.groups())
    return None


def taxonomy_covers(ancestor: str, candidate: str) -> bool:
    """Does an ASSET_CLASS exclusion at ``ancestor`` catch ``candidate``?

    An exclusion written at the super-class level must catch the major classes
    and sub-categories beneath it, and a string ``startswith`` does NOT do that
    for this project's key scheme:

        taxonomy_sc_3  vs  taxonomy_mc_3_2      children, NOT a string prefix
        taxonomy_mc_3_2 vs taxonomy_sub_3_2_1   children, NOT a string prefix
        taxonomy_sc_3  vs  taxonomy_sc_30       a string prefix, NOT children

    The first two are exclusions that would silently catch nothing; the third
    is an exclusion that would silently catch an unrelated super-class. So the
    keys are PARSED into their numeric components (Rule 4's three shapes) and
    compared component-wise.

    A key matching none of the three shapes falls back to exact equality
    rather than to prefix matching — an unrecognised key catching things by
    accident is the failure mode this whole function exists to avoid.
    """
    a = _taxonomy_parts(ancestor)
    c = _taxonomy_parts(candidate)
    if a is None or c is None:
        return ancestor == candidate
    return c[: len(a)] == a


def _matched_positions(
    exclusion: ExclusionInput, positions: Sequence[PositionInput]
) -> list[PositionInput]:
    basis = exclusion.basis_value
    if basis is None:
        return []
    if exclusion.basis_type == "SECURITY":
        return [p for p in positions if p.asset_id == basis]
    if exclusion.basis_type == "ASSET_CLASS":
        return [
            p for p in positions
            if p.taxonomy_key is not None and taxonomy_covers(basis, p.taxonomy_key)
        ]
    if exclusion.basis_type == "POSITION_TAG":
        return [p for p in positions if basis in p.tags]
    return []


def apply_exclusions(
    *,
    valuation: Valuation,
    flow_adjust: Decimal,
    account: Any,
    positions: Sequence[PositionInput],
    exclusions: Sequence[ExclusionInput],
    schedule: FeeScheduleInput,
    period: BillingPeriod,
) -> tuple[Decimal, list[CarveOut], Decimal, dict[str, Any], list[str]]:
    """Total value in, billable value out, with every deduction named.

    Returns ``(billable, carve_outs, flat_additions, detail, assumptions)``.

    Order inside the stage: schedule-level ``cash_treatment`` and
    ``margin_treatment`` first, then the exclusion rows. The percentage base
    for ``EXCLUDE_ABOVE_PCT`` is the account's TOTAL value, not the running
    billable — otherwise a concentrated-stock carve-out processed first would
    shrink the base and quietly excuse more cash than the schedule says.
    """
    assumptions: list[str] = [A_VALUATION_BASIS]
    account_value = valuation.total_market_value
    running = account_value + flow_adjust
    detail: dict[str, Any] = {
        "step": "EXCLUSIONS",
        "account_value": _d(account_value),
        "flow_adjustment": _d(flow_adjust),
        "value_after_flow_adjustment": _d(running),
        "cash_treatment": schedule.cash_treatment,
        "margin_treatment": schedule.margin_treatment,
        "deductions": [],
    }
    deductions: list[dict[str, Any]] = detail["deductions"]

    cash_excluded = ZERO
    if schedule.cash_treatment == "EXCLUDE":
        cash_excluded = valuation.cash_value
        running -= cash_excluded
        deductions.append({
            "source": "schedule.cash_treatment",
            "rule": "EXCLUDE",
            "cash_value": _d(valuation.cash_value),
            "amount": _d(-cash_excluded),
            "running_billable": _d(running),
        })
    elif schedule.cash_treatment == "EXCLUDE_ABOVE_PCT":
        allowance = account_value * (schedule.cash_exclusion_pct or ZERO)
        excess = valuation.cash_value - allowance
        cash_excluded = excess if excess > ZERO else ZERO
        running -= cash_excluded
        deductions.append({
            "source": "schedule.cash_treatment",
            "rule": "EXCLUDE_ABOVE_PCT",
            "cash_value": _d(valuation.cash_value),
            "cash_exclusion_pct": _d(schedule.cash_exclusion_pct),
            "allowance": _d(allowance),
            "amount": _d(-cash_excluded),
            "running_billable": _d(running),
            "note": "the allowance is a percentage of the account's total "
                    "value, measured before any other deduction",
        })

    if schedule.margin_treatment == "REDUCE_BILLABLE":
        margin = abs(valuation.margin_balance)
        running -= margin
        assumptions.append(A_MARGIN_MAGNITUDE)
        deductions.append({
            "source": "schedule.margin_treatment",
            "rule": "REDUCE_BILLABLE",
            "margin_balance": _d(valuation.margin_balance),
            "amount": _d(-margin),
            "running_billable": _d(running),
        })

    carve_outs: list[CarveOut] = []
    flat_additions = ZERO

    ordered = sorted(
        exclusions,
        key=lambda e: (e.basis_type, str(e.basis_value or ""), str(e.id or "")),
    )
    for ex in ordered:
        entry: dict[str, Any] = {
            "source": "fee_exclusions",
            "exclusion_id": ex.id,
            "basis_type": ex.basis_type,
            "basis_value": ex.basis_value,
            "treatment": ex.treatment,
            "scope_type": ex.scope_type,
        }
        if not _effective(ex, period):
            entry.update(
                outcome="skipped", amount=_d(ZERO),
                reason=(f"effective {_dt(ex.effective_from)}..{_dt(ex.effective_to)} "
                        f"does not overlap the period"),
                running_billable=_d(running),
            )
            deductions.append(entry)
            continue

        if ex.basis_type == "ACCOUNT":
            amount = running if running > ZERO else ZERO
            entry["matched"] = "the whole account"
        elif ex.basis_type == "HELD_AWAY":
            if account.is_held_away:
                amount = running if running > ZERO else ZERO
                entry["matched"] = "the whole account (is_held_away is true)"
            else:
                amount = ZERO
                entry["matched"] = "nothing — accounts.is_held_away is false"
        elif ex.basis_type == "CASH":
            if schedule.cash_treatment == "EXCLUDE":
                amount = ZERO
                entry["matched"] = (
                    "nothing — schedule.cash_treatment already excluded cash; "
                    "counting it twice would deduct it twice"
                )
            else:
                amount = valuation.cash_value - cash_excluded
                if amount < ZERO:
                    amount = ZERO
                entry["matched"] = f"cash not already excluded ({amount})"
        else:
            matched = _matched_positions(ex, positions)
            valued = [p for p in matched if p.market_value is not None]
            unvalued = [p for p in matched if p.market_value is None]
            amount = sum((p.market_value for p in valued), ZERO)
            entry["matched"] = (
                f"{len(valued)} position(s)"
                + (f", {len(unvalued)} with a NULL market_value contributing "
                   f"nothing" if unvalued else "")
            )
            entry["matched_positions"] = [
                {"asset_id": p.asset_id, "taxonomy_key": p.taxonomy_key,
                 "market_value": _d(p.market_value)}
                for p in matched
            ]

        if ex.treatment == "EXCLUDE":
            running -= amount
            entry.update(outcome="excluded", amount=_d(-amount))
        elif ex.treatment == "REDUCED_RATE":
            running -= amount
            carve_outs.append(CarveOut(
                alt_fee_schedule_id=ex.alt_fee_schedule_id,  # type: ignore[arg-type]
                amount=amount,
                exclusion_id=ex.id,
                basis_type=ex.basis_type,
                basis_value=ex.basis_value,
            ))
            entry.update(
                outcome="carved_out", amount=_d(-amount),
                alt_fee_schedule_id=ex.alt_fee_schedule_id,
                note="removed from the primary billable value and billed on "
                     "alt_fee_schedule_id at the TIERS step",
            )
        else:  # FLAT
            running -= amount
            flat_additions += ex.flat_amount or ZERO
            entry.update(
                outcome="flat", amount=_d(-amount),
                flat_amount=_d(ex.flat_amount),
                note="removed from the billable value; flat_amount is added to "
                     "the period fee at the TIERS step",
            )
        entry["running_billable"] = _d(running)
        deductions.append(entry)

    if running < ZERO:
        detail["clamped_from"] = _d(running)
        detail["clamp_note"] = (
            "deductions exceeded the account's value; billable floored at zero "
            "rather than allowed to produce a negative fee"
        )
        running = ZERO

    detail["billable_value"] = _d(running)
    detail["carve_out_total"] = _d(sum((c.amount for c in carve_outs), ZERO))
    detail["flat_additions"] = _d(flat_additions)
    return running, carve_outs, flat_additions, detail, assumptions


# ═══════════════════════════════════════════════════════════════════════════
# Stage: tiering
# ═══════════════════════════════════════════════════════════════════════════


def apply_tiers(
    billable: Decimal, tiers: Sequence[FeeTierInput], method: str
) -> tuple[Decimal, list[dict[str, Any]], list[str]]:
    """An ANNUAL amount and one trace entry per tier.

    ``fee_validation.validate_tiers`` runs first and raises. It is fee34's
    function, called rather than re-implemented, because a tier set with a gap
    would otherwise produce a number here — a plausible, wrong, quietly
    under-billed number — instead of an error.

    Every tier gets a slice entry, including tiers the balance never reached,
    with ``base`` of zero. An operator reading the trace can then see the whole
    rate card and where the balance stopped, which is the actual question
    behind "why is this fee this number".
    """
    raise_if_invalid(validate_tiers(tiers))
    ordered = sorted(tiers, key=lambda t: t.tier_seq)
    assumptions = [A_ANNUAL_RATES]
    if method == "BLENDED_PUBLISHED":
        assumptions.append(A_BLENDED)

    slices: list[dict[str, Any]] = []
    total = ZERO

    if method == "CLIFF":
        reached: FeeTierInput | None = None
        for t in ordered:
            if billable >= t.lower_bound and (
                t.upper_bound is None or billable < t.upper_bound
            ):
                reached = t
                break
        for t in ordered:
            hit = reached is not None and t.tier_seq == reached.tier_seq
            base = billable if hit else ZERO
            amount = _tier_amount(t, base) if hit else ZERO
            total += amount
            slices.append(_slice_entry(t, base, amount, "CLIFF", hit))
        if reached is None:
            slices.append({
                "tier_seq": None,
                "note": (f"billable {billable} falls below the first tier's "
                         f"lower_bound {ordered[0].lower_bound if ordered else None}; "
                         f"no tier rate applies"),
                "amount_annual": _d(ZERO),
            })
        return total, slices, assumptions

    # GRADUATED, and BLENDED_PUBLISHED by the documented assumption.
    for t in ordered:
        upper = t.upper_bound
        top = billable if upper is None else min(billable, upper)
        base = top - t.lower_bound
        if base < ZERO:
            base = ZERO
        amount = _tier_amount(t, base) if base > ZERO else ZERO
        total += amount
        slices.append(_slice_entry(t, base, amount, "GRADUATED", base > ZERO))
    return total, slices, assumptions


def _tier_amount(tier: FeeTierInput, base: Decimal) -> Decimal:
    rate = tier.annual_rate
    if rate is not None:
        return base * rate
    return tier.flat_amount or ZERO


def _slice_entry(
    tier: FeeTierInput, base: Decimal, amount: Decimal, method: str, hit: bool
) -> dict[str, Any]:
    return {
        "tier_seq": tier.tier_seq,
        "lower_bound": _d(tier.lower_bound),
        "upper_bound": _d(tier.upper_bound),
        "rate_bps": _d(tier.rate_bps),
        "flat_amount": _d(tier.flat_amount),
        "method": method,
        "base": _d(base),
        "reached": hit,
        "amount_annual": _d(amount),
    }


# ═══════════════════════════════════════════════════════════════════════════
# The pipeline
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class _Run:
    """Mutable bookkeeping for one account's walk through the policy."""

    steps: list[dict[str, Any]] = dc_field(default_factory=list)
    assumptions: list[str] = dc_field(default_factory=list)
    amount: Decimal = ZERO
    billable: Decimal = ZERO
    account_value: Decimal = ZERO
    gross_period: Decimal = ZERO
    minimum_deferred: bool = False

    def note(self, assumption: str) -> None:
        if assumption not in self.assumptions:
            self.assumptions.append(assumption)


@dataclass(frozen=True)
class FeeCalcResult:
    """One account, one period, one number, and why.

    ``amount`` is quantized to cents; ``amount_unrounded`` is not. Both are
    returned because fee36 will store the cent figure on the invoice line and
    a group-level minimum has to allocate against the unrounded one — rounding
    twice, once per account and once for the group, is how allocations stop
    summing to their own total.
    """

    account_id: str
    schedule_id: str
    schedule_code: str
    currency: str
    period_start: date
    period_end: date
    account_value: Decimal
    billable_value: Decimal
    gross_fee: Decimal
    amount: Decimal
    amount_unrounded: Decimal
    is_refund: bool
    minimum_deferred_to_group: bool
    assumptions: tuple[str, ...]
    calc_detail: dict[str, Any]

    def as_json(self) -> str:
        """``calc_detail`` as JSON. Raises if anything in it is not encodable."""
        return json.dumps(self.calc_detail)


def calculate_account_fee(
    request: AccountCalcRequest, *, group_minimum_uplift: Decimal | None = None
) -> FeeCalcResult:
    """One account's fee for one period. Pure: same inputs, same bytes out.

    ``group_minimum_uplift`` is how :func:`calculate_group_fees` completes a
    HOUSEHOLD- or BILLING_GROUP-scoped minimum. It is injected rather than
    computed here because this account cannot see its siblings, and it is
    applied at the MINIMUM step's position in the policy rather than bolted on
    at the end, so a MAXIMUM that follows MINIMUM still sees the uplifted
    figure.
    """
    data = request.data
    schedule = request.schedule
    period = data.period
    account = data.account
    run = _Run()

    header: dict[str, Any] = {
        "engine_version": ENGINE_VERSION,
        "account_id": account.id,
        "schedule": {
            "id": schedule.id,
            "code": schedule.code,
            "version": schedule.version,
            "product_type": schedule.product_type,
            "rate_type": schedule.rate_type,
            "tier_method": schedule.effective_tier_method,
            "billing_frequency": schedule.billing_frequency,
            "billing_timing": schedule.billing_timing,
            "valuation_method": schedule.valuation_method,
            "proration_method": schedule.proration_method,
            "periods_per_year": schedule.periods_per_year,
            "currency": schedule.currency,
            "status": schedule.status,
        },
        "period": {
            "start": _dt(period.period_start),
            "end": _dt(period.period_end),
            "calendar_days": period.calendar_days,
            "service_start": _dt(period.service_start),
            "service_end": _dt(period.service_end),
            "is_partial": period.is_partial,
        },
        "ordering_policy": list(schedule.ordering_policy),
        "steps": run.steps,
    }

    if not account.is_billable:
        run.steps.append({
            "step": "SHORT_CIRCUIT",
            "reason": "accounts.is_billable is false",
            "amount": _d(ZERO),
        })
        return _finish(request, run, header, ZERO, ZERO, ZERO, ZERO)

    _check_policy(schedule, request.discounts)

    valuation, val_detail = resolve_valuation(
        data.balances, period, schedule.valuation_method
    )
    run.steps.append(val_detail)
    run.account_value = valuation.total_market_value

    adjust, flow_detail = flow_adjustment(data.flows, period, schedule)
    run.steps.append(flow_detail)

    positions = _positions_for(data.positions, account.id, period)
    factor, prorate_detail = proration_factor(
        period,
        proration_method=schedule.proration_method,
        billing_timing=schedule.billing_timing,
    )
    if schedule.proration_method == "BUSINESS_DAYS":
        run.note(A_BUSINESS_DAYS)

    carve_outs: list[CarveOut] = []
    flat_additions = ZERO
    tiered_done = False

    for step in schedule.ordering_policy:
        if step == "EXCLUSIONS":
            billable, carve_outs, flat_additions, ex_detail, ex_assumptions = (
                apply_exclusions(
                    valuation=valuation,
                    flow_adjust=adjust,
                    account=account,
                    positions=positions,
                    exclusions=request.exclusions,
                    schedule=schedule,
                    period=period,
                )
            )
            for a in ex_assumptions:
                run.note(a)
            run.billable = billable
            run.steps.append(ex_detail)

        elif step == "TIERS":
            _tier_step(request, run, carve_outs, flat_additions)
            run.steps.append(prorate_detail)
            run.gross_period = run.amount
            run.amount = run.amount * factor
            prorate_detail["gross_before_proration"] = _d(run.gross_period)
            prorate_detail["amount_after"] = _d(run.amount)
            tiered_done = True

        elif step == "DISCOUNTS":
            _discount_step(request, run)

        elif step == "CREDITS":
            _credit_step(request, run)

        elif step == "MINIMUM":
            _minimum_step(request, run, group_minimum_uplift)

        elif step == "MAXIMUM":
            _maximum_step(request, run)

    if not tiered_done:  # pragma: no cover - _ordering_policy makes this impossible
        raise OrderingNotSupportedError(
            "ordering_policy did not contain TIERS", policy=list(schedule.ordering_policy)
        )

    return _finish(
        request, run, header, run.account_value, run.billable,
        run.gross_period, run.amount,
    )


def _check_policy(
    schedule: FeeScheduleInput, discounts: Sequence[DiscountInput]
) -> None:
    """The two policy orderings the engine refuses rather than reinterprets."""
    policy = list(schedule.ordering_policy)
    if policy.index("EXCLUSIONS") > policy.index("TIERS"):
        raise OrderingNotSupportedError(
            "ordering_policy places EXCLUSIONS after TIERS. Exclusions change "
            "the billable VALUE and tiers turn a value into money, so there is "
            "no coherent reading of running them in that order. Refusing "
            "rather than re-sorting: an operator's typo must not silently "
            "change what a client is charged",
            policy=policy,
        )
    if policy.index("CREDITS") > policy.index("DISCOUNTS"):
        offenders = [
            d.id for d in discounts
            if d.applies_to == "NET_OF_CREDITS" and _wants_base(d)
        ]
        if offenders:
            raise OrderingNotSupportedError(
                "a discount is applies_to='NET_OF_CREDITS' but ordering_policy "
                "runs DISCOUNTS before CREDITS, so the net figure it is a "
                "percentage of does not exist yet. applies_to and "
                "ordering_policy are two different knobs and this combination "
                "sets them against each other",
                policy=policy, discount_ids=offenders,
            )


def _wants_base(discount: DiscountInput) -> bool:
    """True for discounts whose size depends on what they are applied to."""
    return discount.discount_type in ("PCT_OFF", "BPS_OFF")


def _tier_step(
    request: AccountCalcRequest,
    run: _Run,
    carve_outs: Sequence[CarveOut],
    flat_additions: Decimal,
) -> None:
    schedule = request.schedule
    ppy = Decimal(schedule.periods_per_year)

    annual, slices, assumptions = apply_tiers(
        run.billable, request.tiers, schedule.effective_tier_method
    )
    for a in assumptions:
        run.note(a)

    detail: dict[str, Any] = {
        "step": "TIERS",
        "tier_method": schedule.effective_tier_method,
        "billable_value": _d(run.billable),
        "slices": slices,
        "primary_annual": _d(annual),
        "periods_per_year": schedule.periods_per_year,
        "carve_outs": [],
    }

    minimum_billable = schedule.minimum_billable_value
    total_billable = run.billable + sum((c.amount for c in carve_outs), ZERO)
    if minimum_billable is not None and total_billable < minimum_billable:
        detail.update({
            "outcome": "below_minimum_billable_value",
            "total_billable_value": _d(total_billable),
            "minimum_billable_value": _d(minimum_billable),
            "reason": ("the account's billable value is under the schedule's "
                       "minimum_billable_value, so no fee is charged"),
            "amount_period": _d(ZERO),
        })
        run.amount = ZERO
        run.steps.append(detail)
        return

    carve_annual = ZERO
    for carve in sorted(carve_outs, key=lambda c: (c.alt_fee_schedule_id, str(c.exclusion_id or ""))):
        pair = (request.alt_schedules or {}).get(carve.alt_fee_schedule_id)
        if pair is None:
            raise AltScheduleMissingError(
                f"REDUCED_RATE exclusion {carve.exclusion_id} carves out "
                f"{carve.amount} to alt_fee_schedule_id "
                f"{carve.alt_fee_schedule_id}, which was not supplied in "
                f"alt_schedules. Billing the carve-out at zero would have been "
                f"the silent failure",
                alt_fee_schedule_id=carve.alt_fee_schedule_id,
                exclusion_id=carve.exclusion_id,
            )
        alt_schedule, alt_tiers = pair
        alt_annual, alt_slices, alt_assumptions = apply_tiers(
            carve.amount, alt_tiers, alt_schedule.effective_tier_method
        )
        for a in alt_assumptions:
            run.note(a)
        run.note(A_ALT_FREQUENCY)
        carve_annual += alt_annual
        detail["carve_outs"].append({
            "exclusion_id": carve.exclusion_id,
            "basis_type": carve.basis_type,
            "basis_value": carve.basis_value,
            "alt_fee_schedule_id": carve.alt_fee_schedule_id,
            "alt_schedule_code": alt_schedule.code,
            "alt_tier_method": alt_schedule.effective_tier_method,
            "carved_value": _d(carve.amount),
            "slices": alt_slices,
            "amount_annual": _d(alt_annual),
            "amount_period": _d(alt_annual / ppy),
        })

    period_amount = (annual + carve_annual) / ppy + flat_additions
    if flat_additions != ZERO:
        run.note(A_PERIOD_AMOUNTS)
    detail.update({
        "carve_out_annual": _d(carve_annual),
        "total_annual": _d(annual + carve_annual),
        "primary_amount_period": _d(annual / ppy),
        "flat_exclusion_additions_period": _d(flat_additions),
        "amount_period": _d(period_amount),
    })
    run.amount = period_amount
    run.steps.append(detail)


def _discount_step(request: AccountCalcRequest, run: _Run) -> None:
    schedule = request.schedule
    period = request.data.period
    base_gross = run.amount
    detail: dict[str, Any] = {
        "step": "DISCOUNTS",
        "amount_before": _d(run.amount),
        "discounts": [],
    }
    for d in sorted(
        request.discounts, key=lambda d: (d.discount_type, str(d.id or ""))
    ):
        entry: dict[str, Any] = {
            "discount_id": d.id,
            "discount_type": d.discount_type,
            "applies_to": d.applies_to,
            "value": _d(d.value),
            "scope_type": d.scope_type,
        }
        if not _effective(d, period):
            entry.update(outcome="skipped", amount=_d(ZERO),
                         reason=(f"effective {_dt(d.effective_from)}.."
                                 f"{_dt(d.effective_to)} does not overlap the "
                                 f"period"),
                         running=_d(run.amount))
            detail["discounts"].append(entry)
            continue

        if d.discount_type == "SCHEDULE_OVERRIDE":
            raise DiscountNotCalculableError(
                f"discount {d.id} is SCHEDULE_OVERRIDE. That is a resolution "
                f"decision — it says a different schedule applies — and "
                f"resolving which schedule applies is explicitly not this "
                f"engine's job. Resolve it before calling, and pass the "
                f"overriding schedule as the schedule",
                discount_id=d.id,
            )

        if d.discount_type == "FEE_HOLIDAY":
            amount = -run.amount
            entry["note"] = "the whole period fee is waived"
        elif d.discount_type == "DOLLAR_CREDIT":
            run.note(A_PERIOD_AMOUNTS)
            amount = -(d.value or ZERO)
            entry["note"] = "value is a per-period dollar amount"
        elif d.discount_type == "PCT_OFF":
            run.note(A_PCT_SCALE)
            pct = d.value or ZERO
            if not (ZERO <= pct <= _HUNDRED):
                raise FeeCalcInputError(
                    f"PCT_OFF discount {d.id} has value {pct}, outside [0, 100]. "
                    f"This engine reads PCT_OFF as a percent; a fraction such "
                    f"as 0.20 would silently become a 0.2% discount instead of "
                    f"20%, so out-of-range values are refused rather than "
                    f"guessed at",
                    field="discount.value",
                )
            applied_to = base_gross if d.applies_to == "GROSS" else run.amount
            amount = -(applied_to * pct / _HUNDRED)
            entry["applied_to_amount"] = _d(applied_to)
            entry["note"] = (
                f"{pct}% of the "
                + ("gross at the start of this step"
                   if d.applies_to == "GROSS" else "running net-of-credits amount")
            )
        elif d.discount_type == "BPS_OFF":
            bps = d.value or ZERO
            annual_reduction = run.billable * bps / _BPS
            amount = -(annual_reduction / Decimal(schedule.periods_per_year))
            entry["applied_to_amount"] = _d(run.billable)
            entry["note"] = (
                f"{bps} bps off the annual rate, charged on the billable value "
                f"and divided by {schedule.periods_per_year} periods"
            )
        else:  # pragma: no cover - DISCOUNT_TYPES is exhausted above
            raise DiscountNotCalculableError(
                f"unhandled discount_type {d.discount_type}", discount_id=d.id
            )

        run.amount += amount
        entry.update(outcome="applied", amount=_d(amount), running=_d(run.amount))
        detail["discounts"].append(entry)

    detail["amount_after"] = _d(run.amount)
    run.steps.append(detail)


def _credit_step(request: AccountCalcRequest, run: _Run) -> None:
    period = request.data.period
    detail: dict[str, Any] = {
        "step": "CREDITS",
        "amount_before": _d(run.amount),
        "credits": [],
        "note": ("fee_credits has no amount column — offset_pct is multiplied "
                 "by a basis_amount the caller supplied, not by anything the "
                 "table stores"),
    }
    for c in sorted(
        request.credits, key=lambda c: (c.credit_source, str(c.id or ""))
    ):
        entry: dict[str, Any] = {
            "credit_id": c.id,
            "credit_source": c.credit_source,
            "offset_pct": _d(c.offset_pct),
            "basis_amount": _d(c.basis_amount),
            "basis_origin": "caller-supplied (no column on fee_credits)",
            "scope_type": c.scope_type,
        }
        if not _effective(c, period):
            entry.update(outcome="skipped", amount=_d(ZERO),
                         reason=(f"effective {_dt(c.effective_from)}.."
                                 f"{_dt(c.effective_to)} does not overlap the "
                                 f"period"),
                         running=_d(run.amount))
            detail["credits"].append(entry)
            continue
        amount = -(c.basis_amount * c.offset_pct)
        run.amount += amount
        entry.update(outcome="applied", amount=_d(amount), running=_d(run.amount))
        detail["credits"].append(entry)

    detail["amount_after"] = _d(run.amount)
    run.steps.append(detail)


def _minimum_step(
    request: AccountCalcRequest, run: _Run, uplift: Decimal | None
) -> None:
    schedule = request.schedule
    account = request.data.account
    detail: dict[str, Any] = {
        "step": "MINIMUM",
        "minimum_fee": _d(schedule.minimum_fee),
        "minimum_fee_scope": schedule.minimum_fee_scope,
        "amount_before": _d(run.amount),
    }

    if schedule.minimum_fee is None:
        detail.update(outcome="not_configured", amount_after=_d(run.amount))
        run.steps.append(detail)
        return

    run.note(A_PERIOD_AMOUNTS)

    if run.amount < ZERO:
        detail.update(
            outcome="skipped",
            reason=("the running amount is negative — this line is a refund of "
                    "a fee billed in advance. Applying a minimum here would "
                    "turn a refund into a charge"),
            amount_after=_d(run.amount),
        )
        run.steps.append(detail)
        return

    if schedule.minimum_fee_scope == "ACCOUNT":
        if run.amount < schedule.minimum_fee:
            uplift_amount = schedule.minimum_fee - run.amount
            run.amount = schedule.minimum_fee
            detail.update(outcome="applied", uplift=_d(uplift_amount),
                          amount_after=_d(run.amount))
        else:
            detail.update(outcome="not_reached", uplift=_d(ZERO),
                          amount_after=_d(run.amount))
        run.steps.append(detail)
        return

    # HOUSEHOLD or BILLING_GROUP.
    scope_id = (
        account.household_id if schedule.minimum_fee_scope == "HOUSEHOLD"
        else account.billing_group_id
    )
    if scope_id is None:
        raise GroupScopeMissingError(
            f"minimum_fee_scope is {schedule.minimum_fee_scope} but the account "
            f"has no "
            + ("household_id" if schedule.minimum_fee_scope == "HOUSEHOLD"
               else "billing_group_id (resolved from billing_group_members by "
                    "the caller)")
            + ". Falling back to an account-scoped minimum would apply the "
              "minimum once per account instead of once per group",
            account_id=account.id, minimum_fee_scope=schedule.minimum_fee_scope,
        )
    detail["scope_id"] = scope_id
    if uplift is None:
        run.minimum_deferred = True
        detail.update(
            outcome="deferred_to_group",
            reason=("a group-scoped minimum is compared against the sum of the "
                    "group's accounts, which this account cannot see. "
                    "calculate_group_fees supplies this account's share"),
            amount_after=_d(run.amount),
        )
    else:
        run.amount += uplift
        detail.update(
            outcome="group_share_applied", uplift=_d(uplift),
            amount_after=_d(run.amount),
            reason=("this account's pro-rata share of the group's shortfall "
                    "against the group minimum"),
        )
    run.steps.append(detail)


def _maximum_step(request: AccountCalcRequest, run: _Run) -> None:
    schedule = request.schedule
    detail: dict[str, Any] = {
        "step": "MAXIMUM",
        "maximum_fee": _d(schedule.maximum_fee),
        "amount_before": _d(run.amount),
    }
    if schedule.maximum_fee is None:
        detail.update(outcome="not_configured", amount_after=_d(run.amount))
    elif run.amount < ZERO:
        detail.update(
            outcome="skipped",
            reason="the running amount is negative; a cap on a refund is not a cap",
            amount_after=_d(run.amount),
        )
    elif run.amount > schedule.maximum_fee:
        run.note(A_PERIOD_AMOUNTS)
        reduction = run.amount - schedule.maximum_fee
        run.amount = schedule.maximum_fee
        detail.update(outcome="applied", reduction=_d(reduction),
                      amount_after=_d(run.amount))
    else:
        detail.update(outcome="not_reached", amount_after=_d(run.amount))
    run.steps.append(detail)


def _finish(
    request: AccountCalcRequest,
    run: _Run,
    header: dict[str, Any],
    account_value: Decimal,
    billable: Decimal,
    gross: Decimal,
    amount: Decimal,
) -> FeeCalcResult:
    quantized = amount.quantize(CENTS, rounding=ROUND_HALF_UP)
    header["assumptions"] = list(run.assumptions)
    header["result"] = {
        "account_value": _d(account_value),
        "billable_value": _d(billable),
        "gross_fee_period": _d(gross),
        "amount_unrounded": _d(amount),
        "amount": _d(quantized),
        "currency": request.schedule.currency,
        "is_refund": quantized < ZERO,
        "minimum_deferred_to_group": run.minimum_deferred,
        "rounding": "ROUND_HALF_UP to 2 decimal places, once, at the end",
    }
    return FeeCalcResult(
        account_id=request.data.account.id,
        schedule_id=request.schedule.id,
        schedule_code=request.schedule.code,
        currency=request.schedule.currency,
        period_start=request.data.period.period_start,
        period_end=request.data.period.period_end,
        account_value=account_value,
        billable_value=billable,
        gross_fee=gross,
        amount=quantized,
        amount_unrounded=amount,
        is_refund=quantized < ZERO,
        minimum_deferred_to_group=run.minimum_deferred,
        assumptions=tuple(run.assumptions),
        calc_detail=header,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Group-scoped minimums
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class GroupFeeResult:
    """Every account in one call, plus what the group-level pass did."""

    results: tuple[FeeCalcResult, ...]
    group_detail: dict[str, Any]

    def by_account(self) -> dict[str, FeeCalcResult]:
        return {r.account_id: r for r in self.results}

    @property
    def total(self) -> Decimal:
        return sum((r.amount for r in self.results), ZERO)


def calculate_group_fees(
    requests: Sequence[AccountCalcRequest]
) -> GroupFeeResult:
    """Calculate several accounts together so a group minimum can see them all.

    ``minimum_fee_scope`` is a real column with three real values and the
    account-only reading is wrong for two of them. A HOUSEHOLD minimum of
    $6,000 across two accounts billing $1,000 and $2,000 is a $3,000 shortfall
    charged ONCE, split between them — not $6,000 charged to each, which is
    what an account-scoped implementation that ignored the column would do, and
    which would look perfectly plausible on either account's statement alone.

    Two passes, both of the same pure function:

    1. Each account is calculated with its group minimum DEFERRED — the MINIMUM
       step records that it is waiting rather than silently doing nothing.
    2. The shortfall is allocated pro-rata by pass-1 amount, quantized to cents
       with the residual going to the largest fractional remainders so the
       allocation sums EXACTLY to the shortfall, and each account is
       recalculated with its share injected at the MINIMUM step's own position
       in its policy.

    An account whose minimum is ACCOUNT-scoped, or which has no minimum at all,
    is finished after pass 1 and its pass-1 result is returned unchanged.
    """
    pass1 = [calculate_account_fee(r) for r in requests]
    group_detail: dict[str, Any] = {
        "engine_version": ENGINE_VERSION,
        "accounts": len(requests),
        "groups": [],
    }

    buckets: dict[tuple[str, str], list[int]] = {}
    for i, (req, res) in enumerate(zip(requests, pass1)):
        if not res.minimum_deferred_to_group:
            continue
        scope = req.schedule.minimum_fee_scope
        account = req.data.account
        scope_id = (
            account.household_id if scope == "HOUSEHOLD" else account.billing_group_id
        )
        buckets.setdefault((scope, str(scope_id)), []).append(i)

    uplifts: dict[int, Decimal] = {}
    for (scope, scope_id), members in sorted(buckets.items()):
        minimums = [
            requests[i].schedule.minimum_fee for i in members
            if requests[i].schedule.minimum_fee is not None
        ]
        minimum = max(minimums)
        subtotal = sum((pass1[i].amount_unrounded for i in members), ZERO)
        entry: dict[str, Any] = {
            "scope": scope,
            "scope_id": scope_id,
            "account_ids": [requests[i].data.account.id for i in members],
            "minimum_fee": _d(minimum),
            "minimum_source": (
                "the highest minimum_fee among the group's schedules"
                if len(set(minimums)) > 1 else "the group's shared minimum_fee"
            ),
            "group_subtotal_before_minimum": _d(subtotal),
        }
        if subtotal >= minimum:
            entry.update(outcome="not_reached", shortfall=_d(ZERO), allocations=[])
            group_detail["groups"].append(entry)
            continue

        shortfall = minimum - subtotal
        shares = _allocate(shortfall, [pass1[i].amount_unrounded for i in members])
        for i, share in zip(members, shares):
            uplifts[i] = share
        entry.update(
            outcome="applied",
            shortfall=_d(shortfall),
            allocation_basis=("pro-rata by each account's pre-minimum amount; "
                              "equal shares when every amount is zero"),
            allocations=[
                {"account_id": requests[i].data.account.id,
                 "amount_before": _d(pass1[i].amount_unrounded),
                 "share": _d(share)}
                for i, share in zip(members, shares)
            ],
        )
        group_detail["groups"].append(entry)

    if not uplifts:
        return GroupFeeResult(tuple(pass1), group_detail)

    final = [
        calculate_account_fee(req, group_minimum_uplift=uplifts[i])
        if i in uplifts else pass1[i]
        for i, req in enumerate(requests)
    ]
    return GroupFeeResult(tuple(final), group_detail)


def _allocate(shortfall: Decimal, weights: Sequence[Decimal]) -> list[Decimal]:
    """Split ``shortfall`` across ``weights``, to the cent, summing exactly.

    Largest-remainder. Quantizing each share independently would leave a
    residual cent unallocated and the group's accounts would sum to a penny
    less than the minimum the group was told it was paying — small, and exactly
    the kind of thing an audit finds instead of us.

    Ties in the remainder break on position, which is why the caller's order
    must be deterministic. It is: :func:`calculate_group_fees` builds its
    buckets in request order.
    """
    positive = [w if w > ZERO else ZERO for w in weights]
    total = sum(positive, ZERO)
    if total == ZERO:
        n = len(weights)
        exact = [shortfall / Decimal(n)] * n
    else:
        exact = [shortfall * w / total for w in positive]

    floors = [e.quantize(CENTS, rounding=ROUND_DOWN) for e in exact]
    residual = shortfall.quantize(CENTS, rounding=ROUND_HALF_UP) - sum(floors, ZERO)
    cents = int((residual / CENTS).to_integral_value())
    if cents > 0:
        order = sorted(
            range(len(exact)), key=lambda i: (-(exact[i] - floors[i]), i)
        )
        for i in order[:cents]:
            floors[i] += CENTS
    return floors


__all__ = [
    "ENGINE_VERSION",
    "AltScheduleMissingError",
    "AmbiguousBalanceError",
    "CarveOut",
    "DiscountNotCalculableError",
    "FeeCalcResult",
    "GroupFeeResult",
    "GroupScopeMissingError",
    "OrderingNotSupportedError",
    "Valuation",
    "ValuationUnavailableError",
    "apply_exclusions",
    "apply_tiers",
    "calculate_account_fee",
    "calculate_group_fees",
    "count_days",
    "flow_adjustment",
    "proration_factor",
    "resolve_valuation",
    "taxonomy_covers",
]
