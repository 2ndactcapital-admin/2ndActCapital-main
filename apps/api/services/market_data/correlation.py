"""Pairwise correlation of CHANGES for the market data read API (mkt03). Pure.

Canonical method (docs/MARKET_DATA_DESIGN_V1.md, "Correlation method"), for
focus F and candidate C:

  1. Pair frequency = the COARSER of the two effective frequencies.
  2. Reduce both to it (last observation per period), keep [anchor, end].
  3. Lag: shift C by lag_months, so a positive lag means C LEADS F (C at
     period t is paired with F at period t + lag).
  4. Changes, per series: ln(v_t / v_{t-1}) when every value in the window is
     > 0, else v_t - v_{t-1}. v_{t-1} is the previous point of the reduced,
     windowed series.
  5. Keep periods where both changes exist; n = their count. n < min_periods
     → r = null, 'insufficient_overlap'.
  6. r = Pearson over the paired changes, float64, rounded to 4 dp, returned
     as a string. Zero variance in either → null, 'zero_variance'.

Changes are computed in Decimal (ln included); only the coefficient itself is
float64, which is the one documented exception to the Decimal-only rule. The
coefficient is never stored.

Lag in calendar months has to land on whole periods. Two rules make that true:
  - a daily or weekly pair with a non-zero lag runs MONTHLY instead (warning
    'promoted_to_monthly_for_lag');
  - a quarterly pair needs a lag that is a multiple of 3, else null with
    'lag_not_multiple_of_period'.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Context, Decimal
from typing import Sequence

from services.market_data.resample import (
    CORRELATION_FREQUENCIES,
    coarser_frequency,
    effective_frequency,
    period_index,
    resample,
)

_CTX = Context(prec=34)
MONTHS_PER_PERIOD = {"monthly": 1, "quarterly": 3}

Point = tuple[date, Decimal]


@dataclass
class Changes:
    method: str  # 'log' | 'diff'
    # period index → (F-side date, change)
    by_period: dict[int, tuple[date, float]]


def changes(points: Sequence[Point], frequency: str) -> Changes:
    """Changes over a reduced, windowed, ascending series.

    method is 'log' only when EVERY value is > 0; otherwise 'diff'. The change
    is computed in Decimal, then converted for the float64 coefficient.
    """
    method = "log" if points and all(v > 0 for _, v in points) else "diff"
    out: dict[int, tuple[date, float]] = {}
    for (_d0, v0), (d1, v1) in zip(points, points[1:]):
        if method == "log":
            ch = _CTX.ln(_CTX.divide(v1, v0))
        else:
            ch = _CTX.subtract(v1, v0)
        out[period_index(d1, frequency)] = (d1, float(ch))
    return Changes(method, out)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson r, float64. None when either side has zero variance."""
    n = len(xs)
    if n < 2:
        return None
    mx = math.fsum(xs) / n
    my = math.fsum(ys) / n
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    sxx = math.fsum(a * a for a in dx)
    syy = math.fsum(b * b for b in dy)
    if sxx == 0 or syy == 0:
        return None
    r = math.fsum(a * b for a, b in zip(dx, dy)) / math.sqrt(sxx * syy)
    return max(-1.0, min(1.0, r))


def r_text(r: float) -> str:
    """4 dp, as a string. '-0.0000' is normalised to '0.0000'."""
    text = f"{round(r, 4):.4f}"
    return "0.0000" if text == "-0.0000" else text


@dataclass
class PairResult:
    r: str | None
    r_float: float | None
    n: int
    frequency: str | None
    overlap_from: date | None
    overlap_to: date | None
    unavailable_reason: str | None
    change_method: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _window(points: Sequence[Point], frequency: str, anchor: date, end: date) -> list[Point]:
    return [(d, v) for d, v in resample(points, frequency) if anchor <= d <= end]


def correlate_pair(
    focus_points: Sequence[Point],
    focus_native: str,
    cand_points: Sequence[Point],
    cand_native: str,
    *,
    anchor: date,
    end: date,
    lag_months: int,
    min_periods: int,
) -> PairResult:
    ef, ec = effective_frequency(focus_native), effective_frequency(cand_native)
    if ef not in CORRELATION_FREQUENCIES or ec not in CORRELATION_FREQUENCIES:
        return PairResult(None, None, 0, None, None, None, "unsupported_frequency")

    freq = coarser_frequency(ef, ec)
    warnings: list[str] = []
    if lag_months and freq in ("daily", "weekly"):
        freq = "monthly"
        warnings.append("promoted_to_monthly_for_lag")
    per = MONTHS_PER_PERIOD.get(freq, 1)
    if lag_months % per:
        return PairResult(None, None, 0, freq, None, None, "lag_not_multiple_of_period", warnings=warnings)
    shift = lag_months // per

    f_ch = changes(_window(focus_points, freq, anchor, end), freq)
    c_ch = changes(_window(cand_points, freq, anchor, end), freq)
    methods = {"focus": f_ch.method, "candidate": c_ch.method}

    xs: list[float] = []
    ys: list[float] = []
    dates: list[date] = []
    for c_idx, (_c_date, c_val) in sorted(c_ch.by_period.items()):
        f = f_ch.by_period.get(c_idx + shift)
        if f is None:
            continue
        dates.append(f[0])
        xs.append(f[1])
        ys.append(c_val)

    n = len(xs)
    lo = min(dates) if dates else None
    hi = max(dates) if dates else None
    if n < min_periods:
        return PairResult(None, None, n, freq, lo, hi, "insufficient_overlap", methods, warnings)
    r = pearson(xs, ys)
    if r is None:
        return PairResult(None, None, n, freq, lo, hi, "zero_variance", methods, warnings)
    return PairResult(r_text(r), r, n, freq, lo, hi, None, methods, warnings)


def sort_results(results: list[tuple[str, PairResult]]) -> list[tuple[str, PairResult]]:
    """|r| descending, nulls last, then series_key for a stable order."""
    return sorted(
        results,
        key=lambda kv: (kv[1].r is None, -abs(float(kv[1].r)) if kv[1].r is not None else 0.0, kv[0]),
    )
