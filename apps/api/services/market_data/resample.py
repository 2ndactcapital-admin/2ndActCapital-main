"""Frequencies and resampling for the market data read API (mkt03). Pure.

"Frequency" in a request means AT MOST THIS FINE. Each returned point is the
LAST observation in its calendar period (ISO week, month, quarter), reported
with its ACTUAL obs_date. A series whose native frequency is coarser than the
request is returned natively — never upsampled, never interpolated.

Order, fine to coarse: daily, weekly, monthly, quarterly, semiannual.
per_meeting and irregular are treated as monthly. semiannual sorts coarsest;
correlations refuse it ('unsupported_frequency').
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Sequence

FREQUENCY_RANK = {"daily": 0, "weekly": 1, "monthly": 2, "quarterly": 3, "semiannual": 4}
_TREATED_AS = {"per_meeting": "monthly", "irregular": "monthly"}

# What a caller may ask for. 'native' means "no resampling".
REQUEST_FREQUENCIES = ("native", "daily", "weekly", "monthly", "quarterly")
# Grid rows: daily is allowed only for a short window (see read_service).
GRID_FREQUENCIES = ("daily", "weekly", "monthly", "quarterly")
# Frequencies a correlation pair may run at.
CORRELATION_FREQUENCIES = ("daily", "weekly", "monthly", "quarterly")

Point = tuple[date, object]  # (obs_date, Decimal value)


def effective_frequency(native: str) -> str:
    """The frequency a series is ranked at: per_meeting/irregular → monthly."""
    return _TREATED_AS.get(native, native)


def coarser_frequency(a: str, b: str) -> str:
    """The coarser of two frequencies (after per_meeting/irregular → monthly)."""
    ea, eb = effective_frequency(a), effective_frequency(b)
    return ea if FREQUENCY_RANK[ea] >= FREQUENCY_RANK[eb] else eb


def period_index(d: date, frequency: str) -> int:
    """An integer that is consecutive for consecutive periods.

    daily → the date's ordinal; weekly → the ISO week (Monday-anchored);
    monthly → y*12+m; quarterly → y*4+q; semiannual → y*2+h.
    """
    if frequency == "daily":
        return d.toordinal()
    if frequency == "weekly":
        return (d - timedelta(days=d.isoweekday() - 1)).toordinal() // 7
    if frequency == "monthly":
        return d.year * 12 + (d.month - 1)
    if frequency == "quarterly":
        return d.year * 4 + (d.month - 1) // 3
    if frequency == "semiannual":
        return d.year * 2 + (d.month - 1) // 6
    raise ValueError(f"unknown frequency {frequency!r}")


def resample(points: Sequence[Point], frequency: str) -> list[Point]:
    """Last observation per period, with its true obs_date. ``points`` must be
    ascending by date. daily is the identity (one observation per date)."""
    if frequency == "daily":
        return list(points)
    out: list[Point] = []
    last_key = None
    for d, v in points:
        key = period_index(d, frequency)
        if out and key == last_key:
            out[-1] = (d, v)
        else:
            out.append((d, v))
        last_key = key
    return out


def returned_frequency(native: str, requested: str) -> str:
    """What the series endpoint actually returns for ``requested``."""
    if requested == "native":
        return native
    if FREQUENCY_RANK[effective_frequency(native)] > FREQUENCY_RANK[requested]:
        return native  # coarser natively: never upsample
    return requested


def resample_for_request(points: Sequence[Point], native: str, requested: str) -> tuple[str, list[Point]]:
    """(returned frequency, points). Natively-coarser series come back as-is."""
    freq = returned_frequency(native, requested)
    if freq == native:
        return freq, list(points)
    return freq, resample(points, freq)


def period_end(d: date, frequency: str) -> date:
    """The last calendar day of the period containing ``d`` (ISO week ends Sunday)."""
    if frequency == "daily":
        return d
    if frequency == "weekly":
        return d + timedelta(days=7 - d.isoweekday())
    if frequency == "monthly":
        return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
    if frequency == "quarterly":
        last_month = ((d.month - 1) // 3) * 3 + 3
        return date(d.year, last_month, calendar.monthrange(d.year, last_month)[1])
    raise ValueError(f"unsupported grid frequency {frequency!r}")


def is_period_end(d: date, frequency: str) -> bool:
    if frequency == "daily":
        return d.isoweekday() <= 5
    return period_end(d, frequency) == d


def period_ends(start: date, end: date, frequency: str) -> list[date]:
    """Every period end p with start <= p <= end, ascending. For daily, every
    weekday (Mon–Fri) in the window — weekend rows would only repeat Friday."""
    out: list[date] = []
    if end < start:
        return out
    if frequency == "daily":
        d = start
        while d <= end:
            if d.isoweekday() <= 5:
                out.append(d)
            d += timedelta(days=1)
        return out
    p = period_end(start, frequency)
    while p <= end:
        out.append(p)
        if p == date.max:
            break
        p = period_end(p + timedelta(days=1), frequency)
    return out


def certainly_more_periods_than(start: date, end: date, frequency: str, cap: int) -> bool:
    """True only when the window CERTAINLY has more than ``cap`` periods —
    decided without building an unbounded list for an absurd window. The
    estimate never over-counts (days/31 for months, days/92 for quarters, ...),
    so past 2*cap the real count is past cap. False means "count it exactly"."""
    if end < start:
        return False
    days = (end - start).days
    per = {"daily": 1.4, "weekly": 7, "monthly": 31, "quarterly": 92}[frequency]
    return days / per > 2 * cap


def add_months(d: date, months: int) -> date:
    """Calendar month arithmetic, clamping the day (Mar 31 − 1 month = Feb 28/29).
    Clamps at the ends of the representable calendar instead of raising."""
    idx = d.year * 12 + (d.month - 1) + months
    if idx < 12:  # before year 1
        return date.min
    y, m = idx // 12, idx % 12 + 1
    if y > 9999:
        return date.max
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))
