"""fee42b Task 1, pass C — percent scale, and the CHECKs on spv_fee_terms."""
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
for _p in (str(HERE), str(API_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _db_connect import admin_dsn, connect  # noqa: E402


async def main():
    dsn, _ = await admin_dsn()
    conn = await connect(dsn)
    try:
        print("== spv_fee_terms CHECKs ==")
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid='public.spv_fee_terms'::regclass AND contype='c'"
            " ORDER BY conname"
        ):
            print(f"  {r['conname']}: {r['def']}")
        print("\n== spv_fee_terms UNIQUE/indexes ==")
        for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes"
            " WHERE schemaname='public' AND tablename='spv_fee_terms'"
        ):
            print(f"  {r['indexdef']}")
        print("\n== spvs CHECKs + deployed pct values (scale evidence) ==")
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid='public.spvs'::regclass AND contype='c'"
        ):
            print(f"  {r['conname']}: {r['def']}")
        for r in await conn.fetch("SELECT name, carry_pct, mgmt_fee_pct FROM spvs"):
            print(f"  {dict(r)}")
        print("\n== fee_schedule_tiers rate scale (house convention) ==")
        if await conn.fetchval("SELECT to_regclass('public.fee_schedule_tiers')"):
            cols = [
                c["column_name"] for c in await conn.fetch(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema='public' AND table_name='fee_schedule_tiers'"
                    " AND data_type='numeric'"
                )
            ]
            print(f"  numeric columns: {cols}")
            if cols:
                for r in await conn.fetch(
                    f"SELECT {', '.join(cols)} FROM fee_schedule_tiers LIMIT 6"
                ):
                    print(f"  {dict(r)}")
        print("\n== spv_transactions txn_type values in use ==")
        for r in await conn.fetch(
            "SELECT DISTINCT txn_type FROM spv_transactions ORDER BY 1"
        ):
            print(f"  {r['txn_type']}")
        print("\n== spv_transaction_allocations CHECKs ==")
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid='public.spv_transaction_allocations'::regclass"
            " ORDER BY contype, conname"
        ):
            print(f"  {r['conname']}: {r['def']}")
        print("\n== spv_subscriptions CHECKs ==")
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid='public.spv_subscriptions'::regclass AND contype='c'"
        ):
            print(f"  {r['conname']}: {r['def']}")
        print("\n== entities: type of the two subscriber entities ==")
        for r in await conn.fetch(
            "SELECT id, display_name, entity_type, org_id FROM entities"
            " WHERE id IN ('10000000-0000-0000-0000-000000000002',"
            "              '05729ae7-326d-4f3a-a73b-dc31f31e8593')"
        ):
            print(f"  {dict(r)}")
        print("\n== deals available (spvs.deal_id is NOT NULL) ==")
        for r in await conn.fetch(
            "SELECT id, org_id, name FROM deals ORDER BY created_at LIMIT 5"
        ):
            print(f"  {dict(r)}")
    finally:
        await conn.close()


asyncio.run(main())
