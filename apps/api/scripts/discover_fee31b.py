"""Sprint fee31 Task 1, part 2 — supporting shapes the adapter/import needs."""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402

SEED_ORG = "00000000-0000-0000-0000-000000000001"


async def cols(conn, table: str) -> None:
    rows = await conn.fetch(
        """
        SELECT column_name, data_type, udt_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position
        """,
        table,
    )
    if not rows:
        print(f"-- {table}: NOT DEPLOYED")
        return
    print(f"-- {table}")
    for c in rows:
        t = f"enum:{c['udt_name']}" if c["data_type"] == "USER-DEFINED" else c["data_type"]
        null = "" if c["is_nullable"] == "YES" else " NOT NULL"
        d = f" DEFAULT {c['column_default']}" if c["column_default"] else ""
        print(f"   {c['column_name']:<28} {t}{null}{d}")


async def main() -> int:
    dsn, prov = await admin_dsn()
    if not dsn:
        print(f"BLOCKED — {prov}")
        return 1
    conn = await connect(dsn)
    try:
        print("=== SUPPORTING TABLE SHAPES ===")
        for t in ("org_settings", "households", "entities", "users", "entity_holdings"):
            await cols(conn, t)
            print()

        print("=== candidate exception-holding tables ===")
        rows = await conn.fetch(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public'
              AND (table_name ILIKE '%exception%' OR table_name ILIKE '%import%'
                   OR table_name ILIKE '%unmatched%')
            ORDER BY table_name
            """
        )
        print("   ", [r["table_name"] for r in rows] or "(none)")

        print("\n=== org_settings sample rows (seed org) ===")
        rows = await conn.fetch(
            "SELECT setting_key, category, is_public, "
            "left(setting_value::text, 60) AS val "
            "FROM public.org_settings WHERE org_id=$1 ORDER BY category, setting_key LIMIT 40",
            SEED_ORG,
        )
        for r in rows:
            print(f"   {r['category'] or '-':<18} {r['setting_key']:<34} {r['val']}")
        print(f"   ({len(rows)} shown)")

        print("\n=== org_settings unique constraints ===")
        rows = await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid='public.org_settings'::regclass ORDER BY conname"
        )
        for r in rows:
            print(f"   {r['conname']}: {r['def']}")
        rows = await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname='public' AND tablename='org_settings'"
        )
        for r in rows:
            print(f"   [i] {r['indexname']}: {r['indexdef']}")

        print("\n=== entities NOT NULL columns (for fixture inserts) ===")
        rows = await conn.fetch(
            "SELECT column_name, data_type, column_default FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='entities' AND is_nullable='NO' "
            "ORDER BY ordinal_position"
        )
        for r in rows:
            print(f"   {r['column_name']:<26} {r['data_type']}  default={r['column_default']}")

        print("\n=== households NOT NULL columns ===")
        rows = await conn.fetch(
            "SELECT column_name, data_type, column_default FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='households' AND is_nullable='NO' "
            "ORDER BY ordinal_position"
        )
        for r in rows:
            print(f"   {r['column_name']:<26} {r['data_type']}  default={r['column_default']}")

        print("\n=== organizations NOT NULL columns ===")
        rows = await conn.fetch(
            "SELECT column_name, data_type, column_default FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='organizations' AND is_nullable='NO' "
            "ORDER BY ordinal_position"
        )
        for r in rows:
            print(f"   {r['column_name']:<26} {r['data_type']}  default={r['column_default']}")

        print("\n=== grants on the new tables (app_service must have DML) ===")
        rows = await conn.fetch(
            """
            SELECT table_name, grantee, string_agg(privilege_type, ',' ORDER BY privilege_type) AS privs
            FROM information_schema.role_table_grants
            WHERE table_schema='public' AND table_name IN
              ('accounts','account_owners','account_balances_daily','account_flows','account_import_batches')
            GROUP BY table_name, grantee ORDER BY table_name, grantee
            """
        )
        for r in rows:
            print(f"   {r['table_name']:<26} {r['grantee']:<16} {r['privs']}")
    finally:
        await conn.close()

    print("\n=== app_service connectivity ===")
    dsn2, prov2 = await app_service_dsn()
    if not dsn2:
        print(f"   UNAVAILABLE — {prov2}")
    else:
        c2 = await connect(dsn2)
        try:
            print(f"   connected via {prov2}; current_user={await c2.fetchval('select current_user')}")
            print(f"   is superuser={await c2.fetchval('select usesuper from pg_user where usename=current_user')}")
            n = await c2.fetchval("select count(*) from public.accounts")
            print(f"   accounts visible with NO org GUC set: {n} (expect 0)")
        finally:
            await c2.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
