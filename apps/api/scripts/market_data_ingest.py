#!/usr/bin/env python3
"""Market data (mkt01) — load the indicator registry, validate FRED codes,
backfill full FRED history.

Usage (from the repo root):
    doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_ingest.py all
    ... market_data_ingest.py load|validate|backfill|all [--series fred.dgs10]

  load      upsert docs/market_data/market_indicator_registry_v1.json into
            market_data.indicator_series (definition fields only)
  validate  check every FRED code against FRED; pending → active | invalid_code
  backfill  full observation history for every 'active' series
  all       load, validate, backfill — in that order

Hydrates secrets from Doppler over HTTPS itself (scripts/_doppler_env.py), so
it also works without ``doppler run --``. Never prints a URL, a params dict or
the FRED key: FRED takes the key in the query string.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))  # apps/api, for services.*
sys.path.insert(0, str(HERE.parent))      # scripts/, for _doppler_env

from _doppler_env import hydrate_from_doppler  # noqa: E402
from services.database import platform_scope  # noqa: E402
from services.market_data import ingest, registry  # noqa: E402
from services.market_data.fred import FredClient  # noqa: E402


def _dsn() -> str | None:
    dsn = os.environ.get("DATABASE_URL")
    return dsn.replace("postgresql+asyncpg://", "postgresql://") if dsn else None


async def run_load(conn, series_key: str | None) -> None:
    seed = registry.load_seed_file()
    if series_key:
        seed = [s for s in seed if s["series_key"] == series_key]
        if not seed:
            print(f"load: {series_key} is not in the seed file")
            return
    async with platform_scope(conn):
        result = await registry.load_registry(conn, seed)
    print(f"load: {len(seed)} seed rows — inserted {result.inserted}, "
          f"updated {result.updated}, unchanged {result.unchanged}")


async def run_validate(conn, client: FredClient, keys: list[str] | None) -> list:
    outcomes = await ingest.validate(conn, client, keys)
    print(f"\nvalidate: {len(outcomes)} FRED series")
    print(f"  {'series_key':<28} {'code':<18} {'status':<13} detail")
    for o in outcomes:
        detail = o.units if o.outcome == "active" else (o.error or "")
        print(f"  {o.series_key:<28} {str(o.source_code):<18} {o.status_after:<13} {detail}")
    for o in outcomes:
        for find in o.finds:
            print(f"[FIND] {find}")
    tally: dict[str, int] = {}
    for o in outcomes:
        tally[o.outcome] = tally.get(o.outcome, 0) + 1
    print("validate totals: " + ", ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    return outcomes


async def run_backfill(conn, client: FredClient, keys: list[str] | None) -> list:
    outcomes = await ingest.backfill(conn, client, keys)
    print(f"\nbackfill: {len(outcomes)} active series")
    print(f"  {'series_key':<28} {'rows':>7} {'first':<10} {'last':<10} {'status':<8} "
          f"{'ins':>7} {'rev':>5} {'skip':>5} {'rej':>4}")
    for o in outcomes:
        print(f"  {o.series_key:<28} {o.active_rows:>7} {str(o.first_date or '-'):<10} "
              f"{str(o.last_date or '-'):<10} {o.status:<8} {o.counts.inserted:>7} "
              f"{o.counts.revised:>5} {o.skipped:>5} {o.rejected:>4}"
              + (f"  {o.error}" if o.error else ""))
    by_status: dict[str, int] = {}
    for o in outcomes:
        by_status[o.status] = by_status.get(o.status, 0) + 1
    print(f"backfill totals: rows inserted {sum(o.counts.inserted for o in outcomes)}, "
          f"revised {sum(o.counts.revised for o in outcomes)}, "
          f"unchanged {sum(o.counts.unchanged for o in outcomes)}, "
          f"skipped '.' {sum(o.skipped for o in outcomes)}, "
          f"rejected {sum(o.rejected for o in outcomes)}; "
          + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())))
    return outcomes


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["load", "validate", "backfill", "all"])
    parser.add_argument("--series", help="limit to one series_key")
    args = parser.parse_args()

    loaded, doppler_err = hydrate_from_doppler()
    if loaded:
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS")
    elif doppler_err:
        print(f"[INFO] Doppler hydration skipped: {doppler_err} — using the ambient environment")

    needs_fred = args.command in ("validate", "backfill", "all")
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if needs_fred and not api_key:
        print("FRED_API_KEY is not set in the environment (add it to Doppler) — nothing was run.")
        return 1
    dsn = _dsn()
    if not dsn:
        print("DATABASE_URL is not set — nothing was run.")
        return 1

    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    client = FredClient(api_key) if needs_fred else None
    try:
        who = await conn.fetchrow(
            "SELECT current_user AS u, "
            "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypass")
        print(f"Connected as {who['u']} (bypasses RLS: {who['bypass']})")
        keys = [args.series] if args.series else None
        if args.command in ("load", "all"):
            await run_load(conn, args.series)
        if args.command in ("validate", "all"):
            await run_validate(conn, client, keys)
        if args.command in ("backfill", "all"):
            await run_backfill(conn, client, keys)
    finally:
        if client is not None:
            await client.aclose()
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
