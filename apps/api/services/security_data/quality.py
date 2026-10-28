"""Data-quality rules — pure functions over (date, value) series, so both
edges of every rule are provable without a database.

  stale         age > limit (settings.is_stale; exactly AT the limit is fresh)
  gap           more than ``max_weekdays`` weekdays with no point between two points
  outlier       |daily move| > multiple × median |daily move| (needs ≥ min_moves moves)
  disagreement  two sources' same-day values differ by more than pct of the first
  budget        a provider's calls today reached its daily budget (writes.budget_left == 0)
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from statistics import median


def weekdays_between(a: date, b: date) -> int:
    """Weekdays strictly between a and b."""
    n, cur = 0, a + timedelta(days=1)
    while cur < b:
        n += cur.weekday() < 5
        cur += timedelta(days=1)
    return n


def gaps(series: list[tuple[date, Decimal]], max_weekdays: int) -> list[dict]:
    pts = sorted(series)
    out = []
    for (a, _), (b, _) in zip(pts, pts[1:]):
        missing = weekdays_between(a, b)
        if missing > max_weekdays:
            out.append({"after": a.isoformat(), "before": b.isoformat(), "weekdays_missing": missing,
                        "limit": max_weekdays})
    return out


def outliers(series: list[tuple[date, Decimal]], multiple: Decimal, min_moves: int) -> list[dict]:
    pts = sorted(series)
    moves = [(b_d, (b - a) / a) for (_, a), (b_d, b) in zip(pts, pts[1:]) if a]
    if len(moves) < min_moves:
        return []
    med = Decimal(str(median(abs(m) for _, m in moves)))
    if med == 0:
        return []
    return [{"date": dt.isoformat(), "move": format(m, "f"), "median_abs_move": format(med, "f"),
             "multiple": format(multiple, "f")} for dt, m in moves if abs(m) > multiple * med]


def disagreement(winner: Decimal, other: Decimal, pct: Decimal) -> bool:
    if not winner:
        return False
    return abs(other - winner) / abs(winner) > pct
