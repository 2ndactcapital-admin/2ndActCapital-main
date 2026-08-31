"""fee42 Task 1b — the NAV question, and the fixture landscape. Read-only."""

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
    if dsn is None:
        print(f"FATAL: {prov}")
        return 1
    conn = await connect(dsn)
    try:
        print("portfolio.spv_derived_positions:",
              await conn.fetchval("SELECT to_regclass('portfolio.spv_derived_positions')"))
        try:
            rows = await conn.fetch(
                "SELECT * FROM portfolio.spv_derived_positions LIMIT 5")
            print(f"  derived rows: {len(rows)}")
            for r in rows:
                print("   ", dict(r))
        except Exception as exc:  # noqa: BLE001
            print("  view read failed:", exc)
        print("assets with internal_spv_id:", await conn.fetchval(
            "SELECT count(*) FROM portfolio.assets WHERE internal_spv_id IS NOT NULL"))
        print("portfolio.valuations rows:", await conn.fetchval(
            "SELECT count(*) FROM portfolio.valuations"))

        print("\n--- spv_subscriptions ---")
        for r in await conn.fetch(
            "SELECT id::text AS id, spv_id::text AS spv_id, entity_id::text AS entity_id, "
            "commitment_amount, funded_amount, ownership_pct, subscription_status, valid_to "
            "FROM public.spv_subscriptions"
        ):
            print("  ", dict(r))

        print("\n--- spv_transactions ---")
        for r in await conn.fetch(
            "SELECT id::text AS id, spv_id::text AS spv_id, txn_type, txn_date, amount, "
            "status FROM public.spv_transactions"
        ):
            print("  ", dict(r))

        print("\n--- scale ---")
        for label, sql in [
            ("accounts (current)",
             "SELECT count(*) FROM public.accounts WHERE valid_to IS NULL AND system_to IS NULL"),
            ("households", "SELECT count(*) FROM public.households"),
            ("entities", "SELECT count(*) FROM public.entities"),
            ("deals", "SELECT count(*) FROM public.deals"),
        ]:
            print(f"  {label}: {await conn.fetchval(sql)}")
        print("  orgs:", [dict(r) for r in await conn.fetch(
            "SELECT id::text AS id, name FROM public.organizations ORDER BY name")])

        print("\n--- app_service rolbypassrls ---")
        print(" ", dict(await conn.fetchrow(
            "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles "
            "WHERE rolname IN ('app_service','postgres') ORDER BY rolname")))
        for r in await conn.fetch(
            "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles "
            "WHERE rolname IN ('app_service','postgres') ORDER BY rolname"
        ):
            print("  ", dict(r))

        print("\n--- grants on the two new tables ---")
        for r in await conn.fetch(
            "SELECT table_name, grantee, privilege_type FROM information_schema.role_table_grants "
            "WHERE table_schema='public' AND table_name IN "
            "('spv_fee_terms','spv_fee_side_letters') ORDER BY table_name, grantee, privilege_type"
        ):
            print("  ", dict(r))
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
