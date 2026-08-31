"""fee42 Task 1c — fixture prerequisites: deals, and what a fixture deal needs."""

from __future__ import annotations

import asyncio
import glob
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn, connect  # noqa: E402


async def main() -> int:
    dsn, prov = await admin_dsn()
    conn = await connect(dsn)
    try:
        print("--- deals per org ---")
        for r in await conn.fetch(
            "SELECT org_id::text AS org_id, count(*) n FROM public.deals GROUP BY org_id"
        ):
            print("  ", dict(r))
        print("\n--- deals NOT NULL columns without a default ---")
        for r in await conn.fetch(
            "SELECT column_name, data_type, column_default FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='deals' AND is_nullable='NO' "
            "ORDER BY ordinal_position"
        ):
            print(f"  {r['column_name']:<28} {r['data_type']:<28} default={r['column_default']}")
        print("\n--- deals CHECK constraints ---")
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) d FROM pg_constraint "
            "WHERE conrelid='public.deals'::regclass AND contype='c'"
        ):
            print(f"  {r['conname']}: {r['d']}")
        print("\n--- entities NOT NULL no-default ---")
        for r in await conn.fetch(
            "SELECT column_name, data_type, column_default FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='entities' AND is_nullable='NO' "
            "AND column_default IS NULL ORDER BY ordinal_position"
        ):
            print(f"  {r['column_name']:<28} {r['data_type']}")
        print("\n--- users NOT NULL no-default ---")
        for r in await conn.fetch(
            "SELECT column_name, data_type, column_default FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='users' AND is_nullable='NO' "
            "AND column_default IS NULL ORDER BY ordinal_position"
        ):
            print(f"  {r['column_name']:<28} {r['data_type']}")
        print("\n--- triggers on the two new tables and on spvs ---")
        for r in await conn.fetch(
            "SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid) d FROM pg_trigger t "
            "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE NOT t.tgisinternal AND n.nspname='public' AND c.relname IN "
            "('spv_fee_terms','spv_fee_side_letters','spvs','fee_credits')"
        ):
            print(f"  {r['relname']}.{r['tgname']}: {r['d']}")
        print("\n--- existing deal ids per org (reusable) ---")
        for r in await conn.fetch(
            "SELECT id::text AS id, org_id::text AS org_id, name FROM public.deals LIMIT 10"
        ):
            print("  ", dict(r))
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
