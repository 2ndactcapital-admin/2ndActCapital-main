#!/usr/bin/env python3
"""Market data (mkt03b) — load key dates, regimes and platform preset views.

Usage (from the repo root):
    doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_seed_reference.py load
    ... market_data_seed_reference.py load --dry-run     classify only, write nothing

Reads docs/market_data/market_key_dates_v1.json, market_regimes_v1.json and
market_view_presets_v1.json and upserts them through
``services.market_data.reference_seed.load_reference`` inside platform_scope():
key_dates on slug, regimes on (regime_type, start_date), platform views on
lower(name). Only data fields and updated_at change, and only when a value
differs; is_active and notes are never touched; nothing is ever deleted. A
preset naming a key that is not an ACTIVE series or a selectable security is
skipped with a [FIND]; the rest still load.

Prints inserted / updated / unchanged per table and every [FIND]. Hydrates
secrets from Doppler over HTTPS itself (scripts/_doppler_env.py), so it also
works without ``doppler run --``. Never prints a connection string.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import asyncpg

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))  # apps/api, for services.*
sys.path.insert(0, str(HERE.parent))      # scripts/, for _doppler_env

from _doppler_env import hydrate_from_doppler  # noqa: E402
from services.market_data import reference_seed  # noqa: E402

REPO_ROOT = HERE.parents[3]
SEED_DIR = REPO_ROOT / "docs" / "market_data"
KEY_DATES_FILE = SEED_DIR / "market_key_dates_v1.json"
REGIMES_FILE = SEED_DIR / "market_regimes_v1.json"
VIEWS_FILE = SEED_DIR / "market_view_presets_v1.json"


def read_seed(path: Path, list_key: str) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get(list_key), list):
        raise SystemExit(f"{path.name}: expected {{\"version\": 1, \"{list_key}\": [...]}}")
    return data[list_key]


def read_all_seeds() -> tuple[list[dict], list[dict], list[dict]]:
    return (read_seed(KEY_DATES_FILE, "key_dates"), read_seed(REGIMES_FILE, "regimes"),
            read_seed(VIEWS_FILE, "views"))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["load"])
    parser.add_argument("--dry-run", action="store_true", help="classify every row without writing")
    args = parser.parse_args()

    loaded, doppler_err = hydrate_from_doppler()
    if loaded:
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS")
    elif doppler_err:
        print(f"[INFO] Doppler hydration skipped: {doppler_err} — using the ambient environment")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set — nothing was run.")
        return 1
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    key_dates, regimes, views = read_all_seeds()
    print(f"Seed files: {len(key_dates)} key dates, {len(regimes)} regimes, {len(views)} preset views")

    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        who = await conn.fetchrow(
            "SELECT current_user AS u, "
            "(SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user) AS bypass")
        print(f"Connected as {who['u']} (bypasses RLS: {who['bypass']})")
        result = await reference_seed.load_reference(
            conn, key_dates=key_dates, regimes=regimes, views=views, dry_run=args.dry_run)
    finally:
        await conn.close()
    for line in reference_seed.format_result(result):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
