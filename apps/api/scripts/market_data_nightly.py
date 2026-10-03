#!/usr/bin/env python3
"""Nightly market data refresh (mkt02) — the Render cron entrypoint.

    Render (rootDir apps/api):  python scripts/market_data_nightly.py
    Locally (repo root):        doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_nightly.py

Re-pulls the FULL history of every 'active' series through the adapter
registry, writes only what changed, records one batch in
market_data.indicator_ingest_runs, then prints a staleness report.

Configuration comes from os.environ ONLY. On Render the values arrive
through the service's own Doppler sync; this script deliberately does no
Doppler hydration of its own.

Exit codes (Render marks a run failed on any non-zero exit):
  0  every attempted series succeeded (skipped "no adapter" series are fine)
  1  at least one series failed, or the batch itself crashed
  2  a required environment variable is missing (names printed, never values)

Never prints a URL, a params dict, or a secret: every error is scrubbed
(services/market_data/adapters.py scrub_error) before it is printed or stored.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

import asyncpg

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))  # apps/api, for services.*

from services.market_data import adapters, nightly, staleness  # noqa: E402
from services.market_data.adapters import scrub_error  # noqa: E402

# Every variable this cron needs. FRED_API_KEY is required because 57 of the
# active series are FRED's; Yahoo needs no key.
REQUIRED_ENV = ("DATABASE_URL", "FRED_API_KEY")


def missing_env(environ: Mapping[str, str]) -> list[str]:
    return [name for name in REQUIRED_ENV if not (environ.get(name) or "").strip()]


def exit_code(result: nightly.NightlyResult | None) -> int:
    """The decision function. None = the batch crashed before it finished."""
    if result is None:
        return 1
    return 1 if result.failed else 0


def summary_lines(result: nightly.NightlyResult) -> list[str]:
    lines = [
        f"market data nightly: batch {result.batch_id} trigger={result.trigger} status={result.status}",
        f"  series attempted={result.attempted} succeeded={result.succeeded} "
        f"(partial={result.partial}) failed={result.failed} "
        f"skipped_no_adapter={result.skipped_no_adapter}",
        f"  rows inserted={result.rows_inserted} revised={result.rows_revised} "
        f"unchanged={result.rows_unchanged}",
    ]
    for key, error in result.failures:
        lines.append(f"[FAILED] {key}: {error}")
    if result.skipped_series:
        lines.append("  skipped (no adapter registered): " + ", ".join(result.skipped_series))
    return lines


def stale_lines(stale: list[staleness.StaleSeries]) -> list[str]:
    lines = [f"staleness: {len(stale)} stale active series (first-pass limits, a report not a failure)"]
    for s in stale:
        lines.append(f"[STALE] {s.series_key} ({s.frequency}) last={s.last_observation_date or '-'} — {s.reason}")
    return lines


async def raise_failure_alert(conn, result: nightly.NightlyResult) -> None:
    """One alert per failed batch through the existing member_todos alert
    mechanism (services/workflow_todos.py) — see docs/MARKET_DATA_DESIGN_V1.md."""
    from services import workflow_todos
    from services.database import close_pool, platform_scope, reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with platform_scope(conn):
            await workflow_todos.create_market_data_batch_failure_alert(
                conn, batch_id=result.batch_id, failures=result.failures)
    finally:
        reset_rls_context(tokens)
        await close_pool()


async def _stale_report(conn) -> list[staleness.StaleSeries]:
    rows = await conn.fetch(
        """SELECT series_key, frequency, last_observation_date, ingest_status
             FROM market_data.indicator_series
            WHERE ingest_status = 'active'
            ORDER BY sort_order, series_key""")
    return staleness.stale_series(rows, datetime.now(timezone.utc).date())


async def run(environ: Mapping[str, str], *, registry: Mapping[str, Any] | None = None,
              series_selection: list[str] | None = None, trigger: str = "nightly",
              alert: Callable[[Any, nightly.NightlyResult], Awaitable[None]] | None = raise_failure_alert,
              ) -> int:
    missing = missing_env(environ)
    if missing:
        print("market data nightly: missing required environment variable(s): "
              + ", ".join(missing) + " — nothing was run.")
        return 2

    dsn = environ["DATABASE_URL"].strip().replace("postgresql+asyncpg://", "postgresql://")
    own_registry = registry is None
    if own_registry:
        registry = adapters.build_registry(environ)
    result: nightly.NightlyResult | None = None
    conn = None
    try:
        try:
            conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
        except Exception as exc:  # noqa: BLE001
            print(f"market data nightly: could not connect to the database ({type(exc).__name__}) — nothing was run.")
            return 1
        try:
            result = await nightly.run_nightly(conn, registry, series_selection, trigger)
        except Exception as exc:  # noqa: BLE001
            print("market data nightly: the batch CRASHED: " + scrub_error(f"{type(exc).__name__}: {exc}"))
            return exit_code(None)

        for line in summary_lines(result):
            print(line)
        if result.failed and alert is not None:
            try:
                await alert(conn, result)
                print(f"  alert raised for batch {result.batch_id}")
            except Exception as exc:  # noqa: BLE001 — the exit code already reports the failure
                print("  alert could NOT be raised: " + scrub_error(f"{type(exc).__name__}: {exc}"))
        try:
            for line in stale_lines(await _stale_report(conn)):
                print(line)
        except Exception as exc:  # noqa: BLE001 — staleness is a report, never a failure
            print("staleness report unavailable: " + scrub_error(f"{type(exc).__name__}: {exc}"))
        return exit_code(result)
    finally:
        if conn is not None:
            await conn.close()
        if own_registry:
            await adapters.close_registry(registry)


def main() -> int:
    parser = argparse.ArgumentParser(description="Nightly market data refresh (mkt02)")
    parser.add_argument("--trigger", choices=["nightly", "manual"], default="nightly",
                        help="run_trigger recorded on every run row (default: nightly)")
    args = parser.parse_args()
    return asyncio.run(run(os.environ, trigger=args.trigger))


if __name__ == "__main__":
    sys.exit(main())
