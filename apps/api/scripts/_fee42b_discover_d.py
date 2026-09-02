"""fee42b Task 1, pass D — the permission key and the profile mechanism."""
from __future__ import annotations

import asyncio
import glob
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _s in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _s not in sys.path:
        sys.path.insert(0, _s)
for _p in (str(HERE), str(API_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _db_connect import admin_dsn, connect  # noqa: E402


async def main():
    dsn, _ = await admin_dsn()
    conn = await connect(dsn)
    try:
        tbl = await conn.fetchval("SELECT to_regclass('public.permissions')")
        print("permissions table:", tbl)
        if tbl:
            cols = [
                c["column_name"] for c in await conn.fetch(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema='public' AND table_name='permissions'"
                    " ORDER BY ordinal_position"
                )
            ]
            print("  cols:", cols)
            for r in await conn.fetch(
                "SELECT * FROM permissions WHERE name ILIKE '%fee%'"
                " OR name ILIKE '%carry%' OR name ILIKE '%spv%' ORDER BY name"
            ):
                print("   ", dict(r))
        print("\nprofile_permissions distinct keys matching fee/carry/spv:")
        for r in await conn.fetch(
            "SELECT DISTINCT permission_key FROM profile_permissions"
            " ORDER BY permission_key"
        ):
            print("   ", r["permission_key"])
        for t in ("permissions_catalog", "permission_catalog", "app_permissions"):
            print(f"  {t}:", await conn.fetchval("SELECT to_regclass($1)", f"public.{t}"))
        print("\nprofiles cols:")
        for c in await conn.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name='profiles'"
            " ORDER BY ordinal_position"
        ):
            print("   ", c["column_name"])
        print("\nprofile_permissions cols:")
        for c in await conn.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name='profile_permissions'"
            " ORDER BY ordinal_position"
        ):
            print("   ", c["column_name"])
        print("\nusers cols (subset):")
        for c in await conn.fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name='users'"
            " ORDER BY ordinal_position"
        ):
            print(f"    {c['column_name']:<26} {'NULL' if c['is_nullable']=='YES' else 'NOT NULL'}")
        print("\nRLS on the tables this sprint writes:")
        for t in ("spv_carry_runs", "spv_carry_run_lines", "assistant_activities",
                  "spv_transaction_allocations", "spv_transactions",
                  "spv_fee_terms", "domain_events"):
            r = await conn.fetchrow(
                "SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n"
                " ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=$1",
                t,
            )
            print(f"    {t:<32} rls={r['relrowsecurity']}")
        print("\napp_service grants on the new tables:")
        for r in await conn.fetch(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants"
            " WHERE grantee='app_service' AND table_name IN"
            " ('spv_carry_runs','spv_carry_run_lines') ORDER BY 1,2"
        ):
            print(f"    {dict(r)}")
        print("\nspv_status_history (teardown dependency):",
              await conn.fetchval("SELECT to_regclass('public.spv_status_history')"))
    finally:
        await conn.close()


asyncio.run(main())
