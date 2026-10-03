"""Read queries for the market data read API (mkt03).

Every function takes the REQUEST connection (``services.database.get_pool()``
→ ``acquire()``), which already carries the caller's RLS context. The three
tables read here — market_data.indicator_series, indicator_observations and
portfolio.securities_global — all have a global SELECT policy, so no
platform_scope() is needed and none is used: this module must never import it
(verify_mkt03 checks that statically). indicator_ingest_runs is platform-only
and is not read.

Active-row predicate for observations: valid_to IS NULL AND system_to IS NULL.
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

SERIES_COLUMNS = """
    s.id, s.series_key, s.name, s.category, s.region, s.frequency, s.units,
    s.seasonal_adjustment, s.default_transform, s.cost_tier, s.license_class,
    s.source_provider, s.sort_order, s.security_global_id, s.ingest_status
"""


async def make_read_only(conn) -> None:
    """Belt and braces: the rest of this transaction cannot write. Allowed
    after the RLS wrapper's own SELECT — switching TO read-only mid-transaction
    is always permitted (only switching back is not)."""
    await conn.execute("SET LOCAL transaction_read_only = on")


async def active_series(conn) -> list[dict]:
    rows = await conn.fetch(
        f"""SELECT {SERIES_COLUMNS}
              FROM market_data.indicator_series s
             WHERE s.ingest_status = 'active'
             ORDER BY s.sort_order, s.series_key"""
    )
    return [dict(r) for r in rows]


async def series_by_keys(conn, keys: Sequence[str]) -> dict[str, dict]:
    """Registry rows for ``keys`` in ANY status (so the caller can tell an
    unknown key from a non-active one)."""
    rows = await conn.fetch(
        f"""SELECT {SERIES_COLUMNS}
              FROM market_data.indicator_series s
             WHERE s.series_key = ANY($1::text[])""",
        list(keys),
    )
    return {r["series_key"]: dict(r) for r in rows}


async def series_bounds(conn, series_ids: Sequence) -> dict:
    """{series_id: {first, last, count}} over ALL active observations."""
    rows = await conn.fetch(
        """SELECT series_id, min(obs_date) AS first_date, max(obs_date) AS last_date,
                  count(*) AS n
             FROM market_data.indicator_observations
            WHERE series_id = ANY($1::uuid[])
              AND valid_to IS NULL AND system_to IS NULL
            GROUP BY series_id""",
        list(series_ids),
    )
    return {r["series_id"]: {"first": r["first_date"], "last": r["last_date"], "count": r["n"]} for r in rows}


async def series_stats(conn, series_ids: Sequence) -> dict:
    """series_bounds plus stddev_samp(value) — computed in SQL, numeric —
    over ALL active observations (full history, independent of any window)."""
    rows = await conn.fetch(
        """SELECT series_id, min(obs_date) AS first_date, max(obs_date) AS last_date,
                  count(*) AS n, stddev_samp(value) AS sd
             FROM market_data.indicator_observations
            WHERE series_id = ANY($1::uuid[])
              AND valid_to IS NULL AND system_to IS NULL
            GROUP BY series_id""",
        list(series_ids),
    )
    return {
        r["series_id"]: {"first": r["first_date"], "last": r["last_date"], "count": r["n"], "sd": r["sd"]}
        for r in rows
    }


async def observations(
    conn,
    series_ids: Sequence,
    date_from: date | None,
    date_to: date | None,
    *,
    include_prior: bool = False,
) -> dict:
    """{series_id: [(obs_date, Decimal), ...]} ascending, for the window.

    ONE query over series_id = ANY($1), ordered by series_id, obs_date. With
    ``include_prior`` it also returns, per series, the last active observation
    strictly BEFORE ``date_from`` — what as_of() needs when a window opens
    between two observations.
    """
    ids = list(series_ids)
    rows = await conn.fetch(
        """SELECT series_id, obs_date, value FROM (
               SELECT o.series_id, o.obs_date, o.value
                 FROM market_data.indicator_observations o
                WHERE o.series_id = ANY($1::uuid[])
                  AND o.valid_to IS NULL AND o.system_to IS NULL
                  AND ($2::date IS NULL OR o.obs_date >= $2::date)
                  AND ($3::date IS NULL OR o.obs_date <= $3::date)
               UNION ALL
               SELECT p.series_id, p.obs_date, p.value
                 FROM unnest($1::uuid[]) AS ids(id)
                 CROSS JOIN LATERAL (
                     SELECT o.series_id, o.obs_date, o.value
                       FROM market_data.indicator_observations o
                      WHERE o.series_id = ids.id
                        AND o.valid_to IS NULL AND o.system_to IS NULL
                        AND o.obs_date < $2::date
                      ORDER BY o.obs_date DESC
                      LIMIT 1
                 ) p
                WHERE $4::boolean AND $2::date IS NOT NULL
           ) x
           ORDER BY series_id, obs_date""",
        ids, date_from, date_to, include_prior,
    )
    out: dict = {sid: [] for sid in ids}
    for r in rows:
        out[r["series_id"]].append((r["obs_date"], r["value"]))
    return out


async def catalog_securities(conn) -> list[dict]:
    """Active, non-merged security-master rows, with the series that prices
    each one (if any). Read only — this sprint never writes portfolio.*."""
    rows = await conn.fetch(
        """SELECT g.id, g.name, g.short_name, g.security_type, s.series_key
             FROM portfolio.securities_global g
             LEFT JOIN market_data.indicator_series s
               ON s.security_global_id = g.id AND s.ingest_status = 'active'
            WHERE g.valid_to IS NULL AND g.system_to IS NULL
              AND g.merged_into_id IS NULL
            ORDER BY g.security_type, g.name, g.id"""
    )
    return [dict(r) for r in rows]
