#!/usr/bin/env python3
"""Market data (mkt01, generalized in mkt02) — load the indicator registry,
validate source codes, backfill full history.

Usage (from the repo root):
    doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_ingest.py all
    ... market_data_ingest.py load|validate|backfill|all [--series fred.dgs10] [--provider yahoo]
    ... market_data_ingest.py load --seed docs/market_data/market_indicator_registry_additions_v2.json

  load      upsert a seed file (default docs/market_data/market_indicator_registry_v1.json;
            --seed picks another) into market_data.indicator_series (definition
            fields only), then set any security link the seed row carries
  validate  check every code of --provider against its source;
            → active | invalid_code (transient failures change nothing)
  backfill  full observation history for every 'active' series of --provider
  all       load, validate, backfill — in that order

--provider defaults to 'fred', so every mkt01 command behaves exactly as
before. `validate --provider yahoo` also picks up the six Yahoo rows seeded
'deferred'; that is how they become active (the nightly only refreshes
'active' series). Every fetch dispatches through the adapter registry
(services/market_data/adapters.py).

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
from services.market_data import adapters, ingest, registry  # noqa: E402


def _dsn() -> str | None:
    dsn = os.environ.get("DATABASE_URL")
    return dsn.replace("postgresql+asyncpg://", "postgresql://") if dsn else None


def resolve_seed_path(raw: str | os.PathLike | None) -> Path:
    """None → the v1 seed. A relative path is taken from the current directory,
    falling back to the repo root (the operator runs from the repo root)."""
    if raw is None:
        return registry.SEED_PATH
    path = Path(raw)
    if not path.is_absolute() and not path.exists():
        candidate = registry.REPO_ROOT / path
        if candidate.exists():
            return candidate
    return path


async def run_load(conn, series_key: str | None, seed_path: str | os.PathLike | None = None):
    path = resolve_seed_path(seed_path)
    seed = registry.load_seed_file(path)
    if series_key:
        seed = [s for s in seed if s["series_key"] == series_key]
        if not seed:
            print(f"load: {series_key} is not in the seed file {path.name}")
            return None
    async with platform_scope(conn):
        result = await registry.load_registry(conn, seed)
    print(f"load: {len(seed)} seed rows from {path.name} — inserted {result.inserted}, "
          f"updated {result.updated}, unchanged {result.unchanged}; security links set "
          f"{result.linked}, already linked {result.link_unchanged}")
    for find in result.finds:
        print(f"[FIND] {find}")
    return result


async def run_validate(conn, client, keys: list[str] | None,
                       provider: str = ingest.DEFAULT_PROVIDER) -> list:
    """``client`` is a provider adapter (or mkt01's FredClient)."""
    outcomes = await ingest.validate(conn, client, keys, provider=provider)
    print(f"\nvalidate: {len(outcomes)} {provider} series")
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


async def run_backfill(conn, client, keys: list[str] | None,
                       provider: str = ingest.DEFAULT_PROVIDER) -> list:
    outcomes = await ingest.backfill(conn, client, keys, provider=provider)
    print(f"\nbackfill: {len(outcomes)} active {provider} series")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["load", "validate", "backfill", "all"])
    parser.add_argument("--series", help="limit to one series_key")
    parser.add_argument("--seed", default=None,
                        help="seed file for `load` (default: docs/market_data/market_indicator_registry_v1.json)")
    parser.add_argument("--provider", default=ingest.DEFAULT_PROVIDER,
                        choices=list(adapters.REAL_PROVIDERS),
                        help="source_provider to validate/backfill (default: fred)")
    return parser


async def main() -> int:
    args = build_parser().parse_args()

    loaded, doppler_err = hydrate_from_doppler()
    if loaded:
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS")
    elif doppler_err:
        print(f"[INFO] Doppler hydration skipped: {doppler_err} — using the ambient environment")

    needs_source = args.command in ("validate", "backfill", "all")
    client = None
    if needs_source:
        try:
            client = adapters.build_adapter(args.provider, os.environ)
        except adapters.MissingConfiguration as exc:
            print(f"{exc} is not set in the environment (add it to Doppler) — nothing was run.")
            return 1
    dsn = _dsn()
    if not dsn:
        print("DATABASE_URL is not set — nothing was run.")
        if client is not None:
            await client.aclose()
        return 1

    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        who = await conn.fetchrow(
            "SELECT current_user AS u, "
            "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypass")
        print(f"Connected as {who['u']} (bypasses RLS: {who['bypass']})")
        keys = [args.series] if args.series else None
        if args.command in ("load", "all"):
            await run_load(conn, args.series, args.seed)
        if args.command in ("validate", "all"):
            await run_validate(conn, client, keys, args.provider)
        if args.command in ("backfill", "all"):
            await run_backfill(conn, client, keys, args.provider)
    finally:
        if client is not None:
            await client.aclose()
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
