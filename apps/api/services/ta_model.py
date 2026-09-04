"""ta_model.py — Takahashi-Alexander private-equity cash-flow projection model.

TA MODEL SPRINT 1. Per docs/TA_MODEL_INTEGRATION_BRIEF.md: this module is a
PURE FUNCTION — no DB, no config import, no I/O of any kind. Every input is
supplied explicitly by the caller (ta_config.params_for_strategy resolves an
org's parameters and hands this module a plain TAParams; this module never
reads org_settings, never imports asyncpg, never imports anything under
routers/). That boundary is what makes the model testable with nothing but
Decimal arithmetic and safe to call from a preview endpoint with no
commitment_id at all.

Decimal throughout. A ``float`` anywhere in this module's inputs is refused,
never silently coerced — see ``_reject_float``. This mirrors
``services.portfolio_assets._money``: ``Decimal(0.1)`` is not ``0.1``, and the
error has to happen at the boundary, not three arithmetic steps downstream
where the wrong number just looks like a rounding difference.

WHAT THE MODEL COMPUTES
──────────────────────────────────────────────────────────────────────────────
Given a commitment's known-to-date state (committed capital, cumulative
called, cumulative distributed, current NAV) and a set of strategy
parameters, projects forward period-by-period:

  contribution(t) = RC * uncalled(t-1)
      RC = rate_of_contribution — the fraction of REMAINING uncalled capital
      drawn each period. Uncalled capital shrinks toward zero asymptotically,
      which is the real shape of a capital-call schedule (a fund calls
      fastest early, more slowly as commitments are exhausted).

  distribution(t) = RD * bow(t) * nav(t-1)
      RD = rate_of_distribution at full bow. bow(t) ramps linearly from 0 at
      t=0 to bow_factor at the end of fund_life_years, so distributions are
      near-zero in the fund's early years and heaviest near harvest — the
      defining J-curve shape. bow_factor > 1 concentrates distributions later
      than a flat rate would; 1.0 would be a straight linear ramp.

  nav(t) = nav(t-1) + contribution(t) - distribution(t) + nav(t-1) * G
      G = growth_rate per period, applied to the PRIOR period's NAV (a
      contribution made mid-period has not yet had a period to grow).

PROJECTED CASH FLOWS ARE NEVER PERSISTED. This function's return value is
computed at read time only and is not written back to the database anywhere
in this codebase — the same rule that governs SPV-derived capital calls.
Callers may persist the ``TAParams`` that produced a projection (in
``portfolio.ta_model_params``, bi-temporally) and calibration RESULTS (in
``portfolio.ta_calibration_results``), never the period-by-period output.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

TWO_PLACES = Decimal("0.01")
RATE_PLACES = Decimal("0.000001")


class TAModelError(ValueError):
    """Bad input to the projection. Never a DB or I/O failure — there is none."""


def _fixed(value: Decimal) -> str:
    """Fixed-point string for a Decimal — NEVER scientific notation.

    A Decimal read back from a database ``numeric`` column for a round value
    (e.g. ``fund_life_years = 10``) can carry a POSITIVE internal exponent —
    Postgres's digit-group representation round-trips into ``Decimal('1E+1')``
    rather than ``Decimal('10')``, equal in value but not in display. Plain
    ``str(value)`` on such a Decimal always renders scientific notation
    (Python's decimal spec: any positive exponent forces it), which would
    silently put "1E+1" into a jsonb column or an API response for a value
    that looks like an integer everywhere else in the system. ``format(value,
    'f')`` forces plain fixed-point regardless of the internal exponent.
    """
    return format(value, "f")


def _reject_float(value: object, name: str) -> None:
    if isinstance(value, float):
        raise TAModelError(
            f"{name} must be a Decimal, int or str — got float. Binary floats "
            f"cannot represent decimal money/rates exactly; the fix is at the "
            f"caller (parse to str/Decimal), not here."
        )


def _require_decimal(value: object, name: str) -> Decimal:
    _reject_float(value, name)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TAModelError(f"{name} must be a Decimal, not bool")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except Exception as exc:  # noqa: BLE001 - re-raised as TAModelError below
            raise TAModelError(f"{name}={value!r} is not numeric") from exc
    raise TAModelError(f"{name} must be a Decimal, int or str — got {type(value).__name__}")


@dataclass(frozen=True)
class TAParams:
    """One strategy's calibratable parameters, always Decimal, always explicit.

    ``periods_per_year`` is carried on the params themselves (not passed
    separately to ``project_cash_flows``) because ``fund_life_years`` and the
    bow curve are only meaningful relative to a stated frequency — a params
    set calibrated at quarterly frequency is not interchangeable with one
    calibrated annually without also converting the frequency.
    """

    rate_of_contribution: Decimal
    rate_of_distribution: Decimal
    growth_rate: Decimal
    bow_factor: Decimal
    fund_life_years: Decimal
    periods_per_year: int

    def __post_init__(self) -> None:
        for name in (
            "rate_of_contribution", "rate_of_distribution", "growth_rate",
            "bow_factor", "fund_life_years",
        ):
            value = getattr(self, name)
            _reject_float(value, name)
            if not isinstance(value, Decimal):
                raise TAModelError(f"{name} must be a Decimal, got {type(value).__name__}")
        if isinstance(self.periods_per_year, bool) or not isinstance(self.periods_per_year, int):
            raise TAModelError("periods_per_year must be an int")
        if self.periods_per_year < 1:
            raise TAModelError("periods_per_year must be >= 1")
        if not (Decimal(0) <= self.rate_of_contribution <= Decimal(1)):
            raise TAModelError("rate_of_contribution must be between 0 and 1")
        if not (Decimal(0) <= self.rate_of_distribution <= Decimal(1)):
            raise TAModelError("rate_of_distribution must be between 0 and 1")
        if self.bow_factor < 0:
            raise TAModelError("bow_factor must be >= 0")
        if self.fund_life_years <= 0:
            raise TAModelError("fund_life_years must be positive")

    @classmethod
    def from_raw(cls, raw: dict, *, periods_per_year: int) -> "TAParams":
        """Build from a dict of str/Decimal values (e.g. decoded jsonb)."""
        return cls(
            rate_of_contribution=_require_decimal(raw["rate_of_contribution"], "rate_of_contribution"),
            rate_of_distribution=_require_decimal(raw["rate_of_distribution"], "rate_of_distribution"),
            growth_rate=_require_decimal(raw["growth_rate"], "growth_rate"),
            bow_factor=_require_decimal(raw["bow_factor"], "bow_factor"),
            fund_life_years=_require_decimal(raw["fund_life_years"], "fund_life_years"),
            periods_per_year=periods_per_year,
        )

    def to_json(self) -> dict:
        """Decimals become FIXED-POINT STRINGS — never scientific notation
        (see :func:`_fixed`) — jsonb decimals stored/returned as strings."""
        return {
            "rate_of_contribution": _fixed(self.rate_of_contribution),
            "rate_of_distribution": _fixed(self.rate_of_distribution),
            "growth_rate": _fixed(self.growth_rate),
            "bow_factor": _fixed(self.bow_factor),
            "fund_life_years": _fixed(self.fund_life_years),
            "periods_per_year": self.periods_per_year,
        }


@dataclass(frozen=True)
class TAPeriod:
    """One projected period. Every monetary field is a Decimal."""

    period: int
    contribution: Decimal
    distribution: Decimal
    nav: Decimal
    cumulative_paid_in: Decimal
    cumulative_distributed: Decimal

    def to_json(self) -> dict:
        return {
            "period": self.period,
            "contribution": _fixed(self.contribution),
            "distribution": _fixed(self.distribution),
            "nav": _fixed(self.nav),
            "cumulative_paid_in": _fixed(self.cumulative_paid_in),
            "cumulative_distributed": _fixed(self.cumulative_distributed),
        }


def contributions_between(periods: list[TAPeriod], period_start: int, period_end: int) -> Decimal:
    """Sum of ``contribution`` across periods whose ``period`` index falls in
    ``[period_start, period_end]``, inclusive. TA MODEL SPRINT 4, TASK 2 — this
    is the read-time primitive the obligation ledger consumes: it operates on
    a projection's already-computed ``TAPeriod`` list (the same list a caller
    already got back from :func:`project_cash_flows`), never recomputes a
    projection itself. Nothing here touches the database.
    """
    if isinstance(period_start, bool) or not isinstance(period_start, int):
        raise TAModelError("period_start must be an int")
    if isinstance(period_end, bool) or not isinstance(period_end, int):
        raise TAModelError("period_end must be an int")
    if period_start < 1 or period_end < period_start:
        raise TAModelError("period_start must be >= 1 and period_end must be >= period_start")
    return sum(
        (p.contribution for p in periods if period_start <= p.period <= period_end),
        Decimal(0),
    )


def contributions_in_years(
    periods: list[TAPeriod], start_year: int, end_year: int, periods_per_year: int,
) -> Decimal:
    """Sum of ``contribution`` across whole years ``[start_year, end_year)``
    (0-based, relative to the projection's own period 1), converted to a
    period-index range at ``periods_per_year`` and delegated to
    :func:`contributions_between`. A "36-month visibility horizon" is
    ``contributions_in_years(periods, 0, 3, periods_per_year)``.
    """
    if isinstance(periods_per_year, bool) or not isinstance(periods_per_year, int) or periods_per_year < 1:
        raise TAModelError("periods_per_year must be a positive int")
    if isinstance(start_year, bool) or not isinstance(start_year, int):
        raise TAModelError("start_year must be an int")
    if isinstance(end_year, bool) or not isinstance(end_year, int) or end_year <= start_year:
        raise TAModelError("end_year must be an int greater than start_year")
    period_start = start_year * periods_per_year + 1
    period_end = end_year * periods_per_year
    return contributions_between(periods, period_start, period_end)


def project_cash_flows(
    *,
    committed_capital: Decimal,
    called_to_date: Decimal,
    distributed_to_date: Decimal,
    current_nav: Decimal,
    params: TAParams,
    horizon_periods: int,
) -> list[TAPeriod]:
    """Project ``horizon_periods`` periods forward. Returns a NEW list every
    call — nothing here is memoized or cached, by design (see module docstring:
    projected cash flows are never persisted, and a cache would be exactly
    that in disguise).
    """
    committed_capital = _require_decimal(committed_capital, "committed_capital")
    called_to_date = _require_decimal(called_to_date, "called_to_date")
    distributed_to_date = _require_decimal(distributed_to_date, "distributed_to_date")
    current_nav = _require_decimal(current_nav, "current_nav")

    if not isinstance(params, TAParams):
        raise TAModelError("params must be a TAParams instance")
    if isinstance(horizon_periods, bool) or not isinstance(horizon_periods, int):
        raise TAModelError("horizon_periods must be an int")
    if horizon_periods < 1:
        raise TAModelError("horizon_periods must be >= 1")
    if horizon_periods > 400:
        raise TAModelError("horizon_periods > 400 is almost certainly a units bug (periods, not years)")

    if committed_capital < 0:
        raise TAModelError("committed_capital must be >= 0")
    if called_to_date < 0:
        raise TAModelError("called_to_date must be >= 0")
    if distributed_to_date < 0:
        raise TAModelError("distributed_to_date must be >= 0")
    if current_nav < 0:
        raise TAModelError("current_nav must be >= 0")

    life_periods = params.fund_life_years * params.periods_per_year

    paid_in = called_to_date
    distributed = distributed_to_date
    nav = current_nav
    periods: list[TAPeriod] = []

    for t in range(1, horizon_periods + 1):
        uncalled = committed_capital - paid_in
        if uncalled < 0:
            uncalled = Decimal(0)
        contribution = (params.rate_of_contribution * uncalled).quantize(RATE_PLACES)

        elapsed_fraction = Decimal(t) / life_periods if life_periods > 0 else Decimal(1)
        if elapsed_fraction > 1:
            elapsed_fraction = Decimal(1)
        bow_multiplier = elapsed_fraction * params.bow_factor
        distribution = (params.rate_of_distribution * bow_multiplier * nav).quantize(RATE_PLACES)
        # A period cannot distribute more than it has available (prior NAV
        # plus this period's own call) — an unclamped model can go NAV-negative
        # on a strategy with an aggressive bow_factor near the end of life.
        available = nav + contribution
        if distribution > available:
            distribution = available

        nav = nav + contribution - distribution + (nav * params.growth_rate)
        if nav < 0:
            nav = Decimal(0)

        paid_in = paid_in + contribution
        distributed = distributed + distribution

        periods.append(
            TAPeriod(
                period=t,
                contribution=contribution.quantize(TWO_PLACES),
                distribution=distribution.quantize(TWO_PLACES),
                nav=nav.quantize(TWO_PLACES),
                cumulative_paid_in=paid_in.quantize(TWO_PLACES),
                cumulative_distributed=distributed.quantize(TWO_PLACES),
            )
        )

    return periods
