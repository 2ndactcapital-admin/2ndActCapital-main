"""DAILYOPS1 — apply docs/dailyops1_part1.sql and prove it landed.

    python3 apps/api/scripts/_apply_dailyops1.py --precheck   # dependents of the column (read only)
    python3 apps/api/scripts/_apply_dailyops1.py --schema     # apply, one transaction
    python3 apps/api/scripts/_apply_dailyops1.py --verify     # read the column back from the catalog
    python3 apps/api/scripts/_apply_dailyops1.py --all        # precheck, apply, verify

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner). Idempotent. Never prints a secret.
Writes NO business rows.
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

SCHEMA_SQL = REPO / "docs" / "dailyops1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
COL_SQL = ("SELECT is_nullable, numeric_precision, numeric_scale FROM information_schema.columns "
           "WHERE table_schema = 'portfolio' AND table_name = 'note_extraction_runs' AND column_name = 'spend_cap_usd'")


async def _conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
    return conn


async def precheck(conn) -> None:
    views = await conn.fetch(
        "SELECT DISTINCT v.oid::regclass::text AS v FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid "
        "JOIN pg_class v ON v.oid = r.ev_class WHERE d.refobjid = 'portfolio.note_extraction_runs'::regclass "
        "AND v.oid <> 'portfolio.note_extraction_runs'::regclass")
    print(f"[INFO] views over note_extraction_runs: {[r['v'] for r in views] or 'none'}")
    fns = await conn.fetch(
        "SELECT p.oid::regprocedure::text AS f FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND p.prosrc ILIKE '%spend_cap_usd%'")
    print(f"[INFO] functions naming spend_cap_usd: {[r['f'] for r in fns] or 'none'}")
    trg = await conn.fetch("SELECT tgname FROM pg_trigger WHERE tgrelid = 'portfolio.note_extraction_runs'::regclass "
                           "AND NOT tgisinternal")
    print(f"[INFO] triggers on note_extraction_runs: {[r['tgname'] for r in trg] or 'none'}")
    print(f"[INFO] column before: {dict(await conn.fetchrow(COL_SQL))}")


async def apply(conn) -> None:
    async with conn.transaction():
        await conn.execute(SCHEMA_SQL.read_text())
    print("[INFO] docs/dailyops1_part1.sql applied")


async def verify(conn) -> bool:
    row = dict(await conn.fetchrow(COL_SQL))
    chk = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'note_extraction_runs_spend_cap_usd_check'")
    ok = row == {"is_nullable": "YES", "numeric_precision": 12, "numeric_scale": 6} and chk is not None \
        and ">= (0)" in chk
    print(f"[{'PASS' if ok else 'FAIL'}] spend_cap_usd is nullable numeric(12,6) with its >= 0 CHECK kept: {row} {chk}")
    return ok


async def main(argv) -> int:
    mode = (argv[1:] or ["--verify"])[0]
    conn = await _conn()
    try:
        if mode in ("--precheck", "--all"):
            await precheck(conn)
        if mode in ("--schema", "--all"):
            await apply(conn)
        if mode in ("--verify", "--all", "--schema"):
            return 0 if await verify(conn) else 1
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
