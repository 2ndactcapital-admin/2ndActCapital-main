"""ta_calibrate.py — fit TAParams to a commitment's realized cash-flow history.

TA MODEL SPRINT 1. Pure function module: same no-DB/no-I/O discipline as
``ta_model.py`` — the caller supplies the realized periods already queried
from ``portfolio.transactions`` (see ``services.ta_params.realized_periods_
from_transactions`` for the real query); this module never touches the
database and never imports asyncpg.

TASK 3 — THE FREQUENCY-AWARE CALIBRATION FLOOR
──────────────────────────────────────────────────────────────────────────────
A flat floor of "3 periods" treats 3 QUARTERS the same as 3 YEARS. Those are
not equivalent evidence: 3 quarters is 9 months of a fund whose life is
typically 6-12 years, while 3 years is a real, multi-cycle slice of it. Per
the brief's own explicit instruction, the floor here is expressed as a
MINIMUM NUMBER OF YEARS of realized history (:data:`MIN_CALIBRATION_YEARS`,
default 3) and converted to a period count at the series' own frequency —
:func:`minimum_realized_periods`. A quarterly series therefore needs
``3 * 4 = 12`` realized periods; an annual series still needs the original
``3 * 1 = 3``. This is not cosmetic: it means a 3-quarter calibration attempt
is refused (9 months < 3 years) while a 3-year one is accepted (the exact
proof Task 5 asks for), and it scales correctly to any other frequency an org
might configure (e.g. monthly would need 36).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from services.ta_model import TAParams

#: Minimum realized history required to calibrate, expressed in YEARS —
#: frequency-independent by construction. Overridable per-org via
#: services.ta_config.TA_CALIBRATION_MIN_YEARS_KEY (resolved by the router,
#: passed in here as ``min_years``).
MIN_CALIBRATION_YEARS = Decimal("3")


class TACalibrationError(ValueError):
    """Calibration was refused — insufficient history or malformed input."""


@dataclass(frozen=True)
class RealizedPeriod:
    """One realized period of actual (not projected) cash-flow activity.

    ``period`` is a 1-based sequence index in chronological order (not a
    calendar year/quarter number) — callers sort and renumber before
    constructing these, so calibration never has to reason about calendars.
    ``nav`` is the period's ENDING NAV.
    """

    period: int
    contribution: Decimal
    distribution: Decimal
    nav: Decimal

    def __post_init__(self) -> None:
        for name in ("contribution", "distribution", "nav"):
            value = getattr(self, name)
            if isinstance(value, float):
                raise TACalibrationError(f"{name} must be Decimal, not float")
            if not isinstance(value, Decimal):
                raise TACalibrationError(f"{name} must be a Decimal, got {type(value).__name__}")
            if value < 0:
                raise TACalibrationError(f"{name} must be >= 0")


def minimum_realized_periods(
    periods_per_year: int, *, min_years: Decimal = MIN_CALIBRATION_YEARS
) -> int:
    """The frequency-aware floor, in PERIODS, for one periods_per_year value.

    ``math.ceil`` so a fractional requirement (e.g. 3 years at a hypothetical
    periods_per_year that does not divide evenly) always rounds UP — the floor
    is a minimum, and rounding down would silently admit less than the stated
    number of years.
    """
    if isinstance(periods_per_year, bool) or not isinstance(periods_per_year, int):
        raise TACalibrationError("periods_per_year must be an int")
    if periods_per_year < 1:
        raise TACalibrationError("periods_per_year must be >= 1")
    if not isinstance(min_years, Decimal) or min_years <= 0:
        raise TACalibrationError("min_years must be a positive Decimal")
    return math.ceil(min_years * periods_per_year)


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def calibrate_strategy(
    realized: list[RealizedPeriod],
    *,
    committed_capital: Decimal,
    periods_per_year: int,
    bow_factor: Decimal,
    fund_life_years: Decimal,
    min_years: Decimal = MIN_CALIBRATION_YEARS,
) -> TAParams:
    """Fit ``rate_of_contribution``, ``rate_of_distribution`` and
    ``growth_rate`` to ``realized`` history. ``bow_factor`` and
    ``fund_life_years`` are NOT calibrated (there is no reliable way to infer
    a fund's full-life bow shape or remaining life from a partial realized
    window this small) — they pass through from the strategy's configured
    defaults, exactly as ``ta_config.params_for_strategy`` would resolve them.

    Refuses (:class:`TACalibrationError`) when ``len(realized)`` is below
    :func:`minimum_realized_periods` for ``periods_per_year`` — the
    frequency-aware floor, checked FIRST and before any arithmetic runs.

    Method: per-period implied rates are the same ratios ``ta_model``'s
    forward projection would invert —
    ``RC_t = contribution_t / uncalled_{t-1}``,
    ``RD_t = distribution_t / nav_{t-1}``,
    ``G_t  = (nav_t - (nav_{t-1} + contribution_t - distribution_t)) / (nav_{t-1} + contribution_t - distribution_t)``
    — averaged across all periods where the denominator is meaningful
    (skipping a period whose denominator is zero rather than dividing by it).
    A simple mean, not a fitted regression — defensible substrate for Sprint
    1's floor-enforcement proof; a later sprint may replace this with a
    least-squares fit without changing this function's signature.
    """
    if isinstance(committed_capital, float):
        raise TACalibrationError("committed_capital must be Decimal, not float")
    if not isinstance(committed_capital, Decimal) or committed_capital <= 0:
        raise TACalibrationError("committed_capital must be a positive Decimal")
    if not isinstance(bow_factor, Decimal) or not isinstance(fund_life_years, Decimal):
        raise TACalibrationError("bow_factor and fund_life_years must be Decimal")

    floor = minimum_realized_periods(periods_per_year, min_years=min_years)
    if len(realized) < floor:
        raise TACalibrationError(
            f"{len(realized)} realized period(s) is below the frequency-aware "
            f"calibration floor of {floor} periods for periods_per_year="
            f"{periods_per_year} (requires {min_years} years of history at "
            f"this frequency) — calibration refused"
        )

    ordered = sorted(realized, key=lambda p: p.period)

    cumulative_paid_in = Decimal(0)
    prev_nav = Decimal(0)
    rc_samples: list[Decimal] = []
    rd_samples: list[Decimal] = []
    g_samples: list[Decimal] = []

    for rp in ordered:
        uncalled_before = committed_capital - cumulative_paid_in
        if uncalled_before > 0:
            rc_samples.append(_clamp(rp.contribution / uncalled_before, Decimal(0), Decimal(1)))

        if prev_nav > 0:
            rd_samples.append(_clamp(rp.distribution / prev_nav, Decimal(0), Decimal(1)))

        implied_base = prev_nav + rp.contribution - rp.distribution
        if implied_base > 0:
            g_samples.append((rp.nav - implied_base) / implied_base)

        cumulative_paid_in += rp.contribution
        prev_nav = rp.nav

    def _avg(samples: list[Decimal]) -> Decimal:
        return sum(samples, Decimal(0)) / Decimal(len(samples)) if samples else Decimal(0)

    rc = _clamp(_avg(rc_samples), Decimal(0), Decimal(1)).quantize(Decimal("0.000001"))
    rd = _clamp(_avg(rd_samples), Decimal(0), Decimal(1)).quantize(Decimal("0.000001"))
    g = _avg(g_samples).quantize(Decimal("0.000001"))

    return TAParams(
        rate_of_contribution=rc,
        rate_of_distribution=rd,
        growth_rate=g,
        bow_factor=bow_factor,
        fund_life_years=fund_life_years,
        periods_per_year=periods_per_year,
    )
