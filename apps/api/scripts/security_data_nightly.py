#!/usr/bin/env python3
"""Nightly security-data run (marketdata1) — the cron entrypoint. SEPARATE
from scripts/market_data_nightly.py (the mkt02 indicator nightly).

    Render (rootDir apps/api):  python scripts/security_data_nightly.py
    Locally (repo root):        doppler run -- apps/api/venv/bin/python apps/api/scripts/security_data_nightly.py

Refreshes the ranked network providers within their daily call budgets,
computes quality events (platform series; org data for non-test orgs),
opens to-dos for stale / disagreeing items that feed open proposals, writes
one portfolio.security_data_runs summary row.

Configuration from os.environ only (the Render service's own Doppler sync).
Exit 0 = success, 1 = partial / failed / crashed, 2 = DATABASE_URL missing.
Never prints a secret: every error goes through scrub_error.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))  # apps/api, for services.*

from services.market_data.adapters import scrub_error  # noqa: E402


async def _main(trigger: str) -> int:
    from services.database import close_pool, get_pool
    from services.security_data import nightly

    pool = await get_pool()
    try:
        res = await nightly.run_nightly(pool, trigger=trigger)
    finally:
        await close_pool()
    print(f"security data nightly: run {res.run_id} trigger={res.trigger} status={res.status}")
    print(f"  calls={res.calls} fetched={res.fetched} budget_exhausted={res.budget_exhausted}")
    print(f"  quality_events={res.quality_events} todos={res.todos} orgs_checked={res.orgs_checked} "
          f"test_orgs_skipped={res.orgs_skipped_test}")
    for e in res.errors[:20]:
        print(f"  error: {e}")
    return 0 if res.status == "success" else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trigger", default="scheduled", choices=("scheduled", "manual"))
    args = ap.parse_args()
    if not (os.environ.get("DATABASE_URL") or "").strip():
        print("missing environment variable: DATABASE_URL")
        return 2
    try:
        return asyncio.run(_main(args.trigger))
    except Exception as exc:  # noqa: BLE001
        print(f"security data nightly crashed: {scrub_error(exc)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
