"""Apply migrations/fee32_position_account_exceptions.sql, then VERIFY it landed.

Never trusts the "success" of an execute() alone — CLAUDE.md's Part 1 rule. The
table's columns, constraints, indexes, RLS flag and app_service grant are all
re-read from the catalogue afterwards and printed.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect  # noqa: E402

MIGRATION = HERE.parent / "migrations" / "fee32_position_account_exceptions.sql"
TABLE = "public.position_account_exceptions"


async def main() -> int:
    dsn, prov = await admin_dsn()
    if dsn is None:
        print(f"BLOCKED: {prov}")
        return 2
    conn = await connect(dsn)
    try:
        await conn.execute(MIGRATION.read_text())
        print(f"executed {MIGRATION.name}")

        cols = await conn.fetch(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='position_account_exceptions' "
            "ORDER BY ordinal_position"
        )
        if not cols:
            print("FAIL: table is not present after the migration")
            return 1
        for r in cols:
            print(f"    col {r['column_name']:<20} {r['data_type']:<26} "
                  f"{'NULL' if r['is_nullable'] == 'YES' else 'NOT NULL'}")
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint "
            f"WHERE conrelid = '{TABLE}'::regclass ORDER BY conname"
        ):
            print(f"    con {r['conname']}: {r['d']}")
        for r in await conn.fetch(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
            "AND tablename='position_account_exceptions' ORDER BY indexname"
        ):
            print(f"    idx {r['indexdef']}")
        print(f"    rls {await conn.fetchval(f'SELECT relrowsecurity FROM pg_class WHERE oid = ' + chr(39) + TABLE + chr(39) + '::regclass')}")
        for r in await conn.fetch(
            "SELECT polname FROM pg_policy "
            f"WHERE polrelid = '{TABLE}'::regclass ORDER BY polname"
        ):
            print(f"    pol {r['polname']}")
        grants = await conn.fetch(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name='position_account_exceptions' AND grantee='app_service' "
            "ORDER BY privilege_type"
        )
        print(f"    app_service grants: {[g['privilege_type'] for g in grants]}")
        if len(grants) != 4:
            print("FAIL: app_service does not hold all four grants")
            return 1
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
