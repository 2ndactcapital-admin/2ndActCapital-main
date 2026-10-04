#!/usr/bin/env python3
"""Market data (mkt02c) — splice older history from Yahoo into a series,
before the series' own first observation. Built for fred.sp500 ← ^GSPC.

Usage (from the repo root):
    doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_splice_history.py --dry-run
    doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_splice_history.py
    ... market_data_splice_history.py [--series fred.sp500] [--yahoo-symbol ^GSPC] [--dry-run]

The rules (boundary F0, overlap gate, insert-only before F0, own-source rows
never touched) live in services/market_data/splice.py. A dry run still runs
the gate and reports every count; it writes nothing.

Exit codes: 0 success; 1 the overlap gate refused or the fetch failed (nothing
written); 2 configuration error (no DATABASE_URL, unknown series, no
own-source rows to splice onto).

Hydrates secrets from Doppler over HTTPS itself (scripts/_doppler_env.py).
Never prints a URL or a secret.
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
from services.market_data import adapters, splice  # noqa: E402

DEFAULT_SERIES = "fred.sp500"
DEFAULT_SYMBOL = "^GSPC"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--series", default=DEFAULT_SERIES, help=f"series_key (default {DEFAULT_SERIES})")
    parser.add_argument("--yahoo-symbol", default=DEFAULT_SYMBOL, help=f"Yahoo symbol (default {DEFAULT_SYMBOL})")
    parser.add_argument("--dry-run", action="store_true", help="run the gate and report counts; write nothing")
    return parser


def print_result(res: splice.SpliceResult) -> int:
    """Print the report; return the exit code."""
    if res.status == "fetch_failed":
        print(f"fetch failed for Yahoo {res.symbol}: {res.error} — nothing written")
        return 1
    for line in splice.format_report(res):
        print(line)
    if res.status == "gate_failed":
        print(f"OVERLAP GATE FAILED: {res.gate.reason} — nothing written")
        for d, own, got, diff in res.gate.worst_days:
            print(f"[FIND] {d}: series {own} vs Yahoo {got} (difference {diff})")
        if res.dry_run:
            print("DRY RUN — nothing written")
        return 1
    if res.dry_run:
        print("DRY RUN — nothing written")
    return 0


async def run(conn, adapter, series_key: str, symbol: str, dry_run: bool) -> int:
    try:
        res = await splice.splice_history(conn, adapter, series_key, symbol, dry_run=dry_run)
    except splice.SpliceConfigError as exc:
        print(f"configuration error: {exc} — nothing written")
        return 2
    return print_result(res)


async def main() -> int:
    args = build_parser().parse_args()

    loaded, doppler_err = hydrate_from_doppler()
    if loaded:
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS")
    elif doppler_err:
        print(f"[INFO] Doppler hydration skipped: {doppler_err} — using the ambient environment")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set — nothing was run.")
        return 2
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    adapter = adapters.build_adapter("yahoo", os.environ)
    try:
        conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    except Exception as exc:  # noqa: BLE001
        print(f"database connection failed ({type(exc).__name__}) — nothing was run.")
        await adapter.aclose()
        return 2
    try:
        who = await conn.fetchrow(
            "SELECT current_user AS u, "
            "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypass")
        print(f"Connected as {who['u']} (bypasses RLS: {who['bypass']})")
        return await run(conn, adapter, args.series, args.yahoo_symbol, args.dry_run)
    finally:
        await adapter.aclose()
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
