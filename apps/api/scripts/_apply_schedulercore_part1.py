"""Apply docs/schedulercore_part1.sql to the deployed DB. Idempotent."""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

import asyncpg  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
SQL = REPO / "docs" / "schedulercore_part1.sql"


async def main():
    dsn = await bootstrap_async()
    if not dsn:
        sys.exit("no working DATABASE_URL")
    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        await conn.execute(SQL.read_text())
        print("[apply] part1 executed")
        print("\n===== workflow_triggers columns =====")
        for r in await conn.fetch(
            """SELECT column_name, data_type, is_nullable, column_default
               FROM information_schema.columns
               WHERE table_name = 'workflow_triggers' AND table_schema = 'public'
               ORDER BY ordinal_position"""
        ):
            print(f"  {r['column_name']:<24} {r['data_type']:<28} "
                  f"null={r['is_nullable']} default={r['column_default']}")
        print("\n===== constraints =====")
        for r in await conn.fetch(
            """SELECT conname, pg_get_constraintdef(oid) AS d
               FROM pg_constraint WHERE conrelid = 'public.workflow_triggers'::regclass
               ORDER BY conname"""
        ):
            print(f"  {r['conname']}: {r['d']}")
        print("\n===== indexes =====")
        for r in await conn.fetch(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'workflow_triggers'"
        ):
            print(f"  {r['indexdef']}")
    finally:
        await conn.close()


asyncio.run(main())
