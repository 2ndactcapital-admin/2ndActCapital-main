"""CASH01 — apply docs/cash01_part1.sql and prove it landed.

    python3 apps/api/scripts/_apply_cash01.py --schema    # docs/cash01_part1.sql, one transaction
    python3 apps/api/scripts/_apply_cash01.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_cash01.py --all       # both

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table owner). Idempotent.
Never prints a secret. DDL only — writes no row.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

SCHEMA_SQL = REPO / "docs" / "cash01_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TABLES = ("cash_obligations", "cash_coverage_minimums")
CHECKS = {
    "cash_obligations": ("cash_obligations_source_type_chk", "cash_obligations_certainty_chk",
                         "cash_obligations_amount_chk", "cash_obligations_currency_chk", "cash_obligations_end_chk",
                         "cash_obligations_rrule_chk", "cash_obligations_ref_chk", "cash_obligations_debt_ref_chk",
                         "cash_obligations_notice_ref_chk", "cash_obligations_invoice_ref_chk",
                         "cash_obligations_name_chk", "cash_obligations_end_reason_chk"),
    "cash_coverage_minimums": ("cash_coverage_minimums_scope_chk", "cash_coverage_minimums_months_chk"),
}
UNIQUES = ("cash_obligations_live_uq", "cash_obligations_external_key_uq", "cash_coverage_minimums_entity_uq",
           "cash_coverage_minimums_household_uq")


async def _conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
    return conn


async def apply_schema() -> None:
    conn = await _conn()
    try:
        async with conn.transaction():
            await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        print("[OK] schema applied")
    finally:
        await conn.close()


async def verify_schema() -> int:
    conn = await _conn()
    bad = []
    try:
        for t in TABLES:
            if await conn.fetchval("SELECT to_regclass($1)", f"public.{t}") is None:
                bad.append(f"{t}: absent")
                continue
            if not await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"public.{t}"):
                bad.append(f"{t}: RLS off")
            ops = {r["cmd"] for r in await conn.fetch("SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                     "tablename = $1", t)}
            if ops != {"SELECT", "INSERT", "UPDATE", "DELETE"}:
                bad.append(f"{t}: policies {sorted(ops)}")
            have = {r["conname"] for r in await conn.fetch("SELECT conname FROM pg_constraint WHERE conrelid = "
                                                          "$1::regclass AND contype = 'c'", f"public.{t}")}
            miss = set(CHECKS[t]) - have
            if miss:
                bad.append(f"{t}: missing checks {sorted(miss)}")
            if not await conn.fetchval("SELECT has_table_privilege('app_service', $1, 'INSERT')", f"public.{t}"):
                bad.append(f"{t}: app_service lacks INSERT")
        idx = {r["indexname"] for r in await conn.fetch("SELECT indexname FROM pg_indexes WHERE indexname = "
                                                        "ANY($1::text[])", list(UNIQUES))}
        if idx != set(UNIQUES):
            bad.append(f"unique indexes missing {sorted(set(UNIQUES) - idx)}")
    finally:
        await conn.close()
    print("SCHEMA OK" if not bad else f"SCHEMA PROBLEMS: {bad}")
    return 0 if not bad else 1


async def main(argv: list[str]) -> int:
    if "--schema" in argv or "--all" in argv:
        await apply_schema()
    if "--verify" in argv or "--all" in argv:
        return await verify_schema()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
