"""Staleness report — which active series have not had a new observation for
longer than their frequency would explain. A REPORT, never a failure: FRED
publishes some series weeks late, and a stale series is still correct history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping

# FIRST-PASS VALUES — tune them after the first live staleness report. A
# series is stale when today - last_observation_date is MORE than the limit.
# The slack over the nominal period covers publication lag (monthly CPI lands
# ~2 weeks after month end; quarterly GDP ~1 month after quarter end) and
# FRED dating a period on its first day.
STALE_DAILY_DAYS = 10
STALE_WEEKLY_DAYS = 21
STALE_MONTHLY_DAYS = 120
STALE_QUARTERLY_DAYS = 200
STALE_SEMIANNUAL_DAYS = 400

STALENESS_LIMIT_DAYS = {
    "daily": STALE_DAILY_DAYS,
    "weekly": STALE_WEEKLY_DAYS,
    "monthly": STALE_MONTHLY_DAYS,
    "quarterly": STALE_QUARTERLY_DAYS,
    "semiannual": STALE_SEMIANNUAL_DAYS,
}
# No regular cadence, so no honest limit. Never flagged on age.
NEVER_STALE = frozenset({"per_meeting", "irregular"})


@dataclass(frozen=True)
class StaleSeries:
    series_key: str
    frequency: str
    last_observation_date: date | None
    age_days: int | None
    limit_days: int | None
    reason: str


def stale_series(rows: Iterable[Mapping[str, Any]], today: date) -> list[StaleSeries]:
    """Rows need series_key, frequency, last_observation_date; an
    ``ingest_status`` key, if present, limits the report to 'active' rows.

    A NULL last_observation_date on an active series is ALWAYS flagged —
    whatever its frequency — because it means the series has no data at all.
    """
    out: list[StaleSeries] = []
    for row in rows:
        try:
            status = row["ingest_status"]
        except (KeyError, LookupError):  # dicts raise KeyError; asyncpg Records a LookupError
            status = "active"
        if status != "active":
            continue
        key, freq, last = row["series_key"], row["frequency"], row["last_observation_date"]
        if last is None:
            out.append(StaleSeries(key, freq, None, None, STALENESS_LIMIT_DAYS.get(freq),
                                   "no observations at all"))
            continue
        if freq in NEVER_STALE or freq not in STALENESS_LIMIT_DAYS:
            continue
        age = (today - last).days
        limit = STALENESS_LIMIT_DAYS[freq]
        if age > limit:
            out.append(StaleSeries(key, freq, last, age, limit, f"{age} days old, limit {limit}"))
    return out
