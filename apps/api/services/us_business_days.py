"""CASH03 — US business days for Treasury maturities (pure).

No holiday calendar existed anywhere in the app (services/authority.CALENDAR_NOTE and services/fee_calc both record
the absence), so this is the ONE calendar: the U.S. federal holidays the Federal Reserve and the Treasury observe
(5 U.S.C. 6103) — the days a Treasury security does not pay and a rung cannot mature.

    New Year's Day 1 Jan · Martin Luther King Jr. Day 3rd Mon Jan · Washington's Birthday 3rd Mon Feb ·
    Memorial Day last Mon May · Juneteenth 19 Jun (from 2021) · Independence Day 4 Jul · Labor Day 1st Mon Sep ·
    Columbus Day 2nd Mon Oct · Veterans Day 11 Nov · Thanksgiving 4th Thu Nov · Christmas 25 Dec

A fixed-date holiday on a Saturday is observed the Friday before, on a Sunday the Monday after — EXCEPT New Year's Day
on a Saturday, which the Federal Reserve does not move back into December (no observed day). Weekends are never
business days. Pure functions; the year range is unbounded (computed, never a table).
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

HOLIDAY_NAMES = ("new_years_day", "mlk_day", "washingtons_birthday", "memorial_day", "juneteenth", "independence_day",
                 "labor_day", "columbus_day", "veterans_day", "thanksgiving", "christmas")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th ``weekday`` (Mon = 0) of the month; n = -1 is the last."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date, *, new_year: bool = False) -> date | None:
    if d.weekday() == 5:                      # Saturday → Friday, except New Year's Day (not observed)
        return None if new_year else d - timedelta(days=1)
    if d.weekday() == 6:                      # Sunday → Monday
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=256)
def holidays(year: int) -> dict[date, str]:
    """{observed date: holiday name} for one calendar year."""
    out: dict[date, str] = {}
    fixed = [("new_years_day", date(year, 1, 1)), ("independence_day", date(year, 7, 4)),
             ("veterans_day", date(year, 11, 11)), ("christmas", date(year, 12, 25))]
    if year >= 2021:
        fixed.append(("juneteenth", date(year, 6, 19)))
    for name, d in fixed:
        obs = _observed(d, new_year=name == "new_years_day")
        if obs is not None:
            out[obs] = name
    # a 1 Jan of NEXT year falling on a Saturday is not observed on 31 Dec (the Federal Reserve rule)
    out[_nth_weekday(year, 1, 0, 3)] = "mlk_day"
    out[_nth_weekday(year, 2, 0, 3)] = "washingtons_birthday"
    out[_nth_weekday(year, 5, 0, -1)] = "memorial_day"
    out[_nth_weekday(year, 9, 0, 1)] = "labor_day"
    out[_nth_weekday(year, 10, 0, 2)] = "columbus_day"
    out[_nth_weekday(year, 11, 3, 4)] = "thanksgiving"
    return out


def holiday_name(d: date) -> str | None:
    return holidays(d.year).get(d)


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def prior_business_day(d: date) -> date:
    """``d`` itself when it is a business day, else the nearest EARLIER business day (a rung must mature on or
    before the obligation it funds)."""
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d


def next_business_day(d: date) -> date:
    while not is_business_day(d):
        d += timedelta(days=1)
    return d


def adjustment_note(target: date, adjusted: date) -> str | None:
    """Why a date moved, in words — None when it did not."""
    if target == adjusted:
        return None
    why = holiday_name(target)
    reason = (why.replace("_", " ") if why else ("a Saturday" if target.weekday() == 5 else "a Sunday"))
    return f"{target.isoformat()} is {reason if not why else 'a holiday (' + reason + ')'} — moved to the prior " \
           f"business day {adjusted.isoformat()}"
