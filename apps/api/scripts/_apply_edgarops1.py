"""EDGAROPS1 — apply docs/edgarops1_part1.sql and prove it landed.

    python3 apps/api/scripts/_apply_edgarops1.py --schema     # apply, one transaction
    python3 apps/api/scripts/_apply_edgarops1.py --verify     # read the index back from the catalog
    python3 apps/api/scripts/_apply_edgarops1.py --all        # apply, verify

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner). Idempotent (CREATE UNIQUE INDEX IF NOT
EXISTS). Never prints a secret. Writes NO business rows.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

SCHEMA_SQL = REPO / "docs" / "edgarops1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
INDEX = "edgar_pipeline_runs_auto_download_day_uq"
LOADED_INDEX = "edgar_index_filings_loaded_at_idx"


async def _conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
    return conn


async def apply(conn) -> None:
    async with conn.transaction():
        await conn.execute(SCHEMA_SQL.read_text())
    print("[INFO] docs/edgarops1_part1.sql applied")


async def verify(conn) -> bool:
    d = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE schemaname = 'portfolio' AND indexname = $1", INDEX)
    ok = bool(d) and "UNIQUE" in d and "auto_download" in d and "bulk_fetch" in d and "schedule" in d
    print(f"[{'PASS' if ok else 'FAIL'}] {INDEX} exists, unique, partial on scheduled bulk_fetch runs: {d}")
    d2 = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE schemaname = 'portfolio' AND indexname = $1",
                             LOADED_INDEX)
    ok2 = bool(d2) and "(loaded_at)" in d2
    print(f"[{'PASS' if ok2 else 'FAIL'}] {LOADED_INDEX} exists: {d2}")
    return ok and ok2


async def main(argv) -> int:
    mode = (argv[1:] or ["--verify"])[0]
    conn = await _conn()
    try:
        if mode in ("--schema", "--all"):
            await apply(conn)
        return 0 if await verify(conn) else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
