"""Transforms for the market data read API (mkt03). Pure: no database.

Canonical definitions (docs/MARKET_DATA_DESIGN_V1.md, "Transform definitions"):

- Observations of a series: its active rows, ascending by obs_date.
- as_of(series, date): the value of the last observation with obs_date <= date;
  none if date is before the first observation.
- Anchor value v0 = as_of(series, anchor). If none, v0 = the first observation
  and the series is floating = true.
- index: 100 * v / v0. Requires v0 > 0, otherwise 'non_positive_anchor'. A
  series whose default_transform is 'level' still indexes when v0 > 0 but
  carries the warning 'rate_like_series_indexed'.
- sigma: (v - v0) / sd, sd = stddev_samp over ALL active observations (full
  history, independent of anchor and window). sd null or 0 → 'zero_variance'.
- level: v unchanged.
- default: the series' own default_transform: rebase_100 → index; level →
  level; yoy_pct → 100 * (v / v12 - 1), v12 = as_of(date minus 12 months),
  null when none; mom_pct → the same with 1 month.
- Every transformed value is Decimal, quantized to 6 dp ROUND_HALF_EVEN, and
  crosses the API as a string.

Decimal only. Nothing here ever touches float.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal
from typing import Sequence

from services.market_data.resample import add_months

MODES = ("index", "sigma", "level", "default")
DEFAULT_TRANSFORMS = ("rebase_100", "level", "yoy_pct", "mom_pct")
# default_transform → the transform 'default' mode applies.
DEFAULT_MODE_MAP = {"rebase_100": "index", "level": "level", "yoy_pct": "yoy", "mom_pct": "mom"}

Q6 = Decimal("0.000001")
HUNDRED = Decimal(100)
# Wide enough that quantizing a large value to 6 dp can never raise.
_CTX = Context(prec=60)

Point = tuple[date, Decimal]


def quantize6(x: Decimal) -> Decimal:
    return x.quantize(Q6, rounding=ROUND_HALF_EVEN, context=_CTX)


def decimal_text(x: Decimal | None) -> str | None:
    """Exact Decimal text, never exponent notation ('1E+2' → '100')."""
    if x is None:
        return None
    return format(x, "f")


def to_decimal(raw) -> Decimal:
    """Decimal(str(...)) at every parse boundary — never Decimal(float)."""
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, float):
        raise TypeError("float reached a Decimal-only boundary")
    return Decimal(str(raw))


class Series:
    """A series' active observations, ascending, with O(log n) as-of lookup."""

    __slots__ = ("dates", "values")

    def __init__(self, points: Sequence[Point]):
        self.dates = [d for d, _ in points]
        self.values = [to_decimal(v) for _, v in points]

    def __len__(self) -> int:
        return len(self.dates)

    def as_of(self, d: date) -> Decimal | None:
        i = bisect.bisect_right(self.dates, d)
        return self.values[i - 1] if i else None

    def as_of_point(self, d: date) -> Point | None:
        i = bisect.bisect_right(self.dates, d)
        return (self.dates[i - 1], self.values[i - 1]) if i else None

    def first(self) -> Point | None:
        return (self.dates[0], self.values[0]) if self.dates else None


def as_of(points: Sequence[Point], d: date) -> Decimal | None:
    return Series(points).as_of(d)


@dataclass
class Anchor:
    value: Decimal | None
    obs_date: date | None
    floating: bool


def anchor_of(series: Series, anchor: date, first_observation_date: date | None = None) -> Anchor:
    """v0 = as_of(anchor); else the first observation, floating.

    ``first_observation_date`` is the series' first ACTIVE observation over its
    whole history. It decides ``floating`` even when the caller only loaded a
    window of points.
    """
    floating = first_observation_date is not None and anchor < first_observation_date
    pt = series.as_of_point(anchor)
    if pt is None:
        pt = series.first()
        floating = True if pt is not None else floating
    if pt is None:
        return Anchor(None, None, floating)
    return Anchor(pt[1], pt[0], floating)


def index_value(v: Decimal, v0: Decimal) -> Decimal:
    return quantize6(_CTX.divide(_CTX.multiply(HUNDRED, v), v0))


def sigma_value(v: Decimal, v0: Decimal, sd: Decimal) -> Decimal:
    return quantize6(_CTX.divide(_CTX.subtract(v, v0), sd))


def pct_change_value(v: Decimal, earlier: Decimal) -> Decimal:
    """100 * (v / earlier - 1)."""
    return quantize6(_CTX.multiply(HUNDRED, _CTX.subtract(_CTX.divide(v, earlier), Decimal(1))))


def level_value(v: Decimal) -> Decimal:
    return quantize6(v)


def _pct_vs(series: "Series", d: date, months: int) -> Decimal | None:
    v = series.as_of(d)
    earlier = series.as_of(add_months(d, -months))
    if v is None or earlier is None or earlier == 0:
        return None
    return pct_change_value(v, earlier)


def yoy(series: "Series", d: date) -> Decimal | None:
    """100 * (v / v12 - 1); null when there is no observation 12 months earlier."""
    return _pct_vs(series, d, 12)


def mom(series: "Series", d: date) -> Decimal | None:
    """100 * (v / v1 - 1); null when there is no observation 1 month earlier."""
    return _pct_vs(series, d, 1)


def resolve_mode(mode: str, default_transform: str) -> str:
    """'default' → the series' own transform: index | level | yoy | mom."""
    if mode == "default":
        return DEFAULT_MODE_MAP[default_transform]
    return mode


@dataclass
class Column:
    """One series' transformed values at a list of dates."""

    applied: str
    values: list[Decimal | None]
    floating: bool
    anchor_date: date | None
    anchor_value: Decimal | None
    warnings: list[str] = field(default_factory=list)
    unavailable_reason: str | None = None


def transform_column(
    series: Series,
    dates: Sequence[date],
    *,
    mode: str,
    default_transform: str,
    anchor: date,
    first_observation_date: date | None,
    sd: Decimal | None,
) -> Column:
    applied = resolve_mode(mode, default_transform)
    a = anchor_of(series, anchor, first_observation_date)
    col = Column(applied, [None] * len(dates), a.floating, a.obs_date, a.value)
    if a.value is None:
        col.unavailable_reason = "no_observations"
        return col

    if applied == "index":
        if a.value <= 0:
            col.unavailable_reason = "non_positive_anchor"
            return col
        if default_transform == "level":
            col.warnings.append("rate_like_series_indexed")
    elif applied == "sigma":
        if sd is None or sd == 0:
            col.unavailable_reason = "zero_variance"
            return col

    for i, d in enumerate(dates):
        v = series.as_of(d)
        if v is None:
            continue
        if applied == "index":
            col.values[i] = index_value(v, a.value)
        elif applied == "sigma":
            col.values[i] = sigma_value(v, a.value, sd)
        elif applied == "level":
            col.values[i] = level_value(v)
        elif applied == "yoy":
            col.values[i] = yoy(series, d)
        elif applied == "mom":
            col.values[i] = mom(series, d)
        else:  # pragma: no cover — guarded by request validation
            raise ValueError(f"unknown transform {applied!r}")
    return col
