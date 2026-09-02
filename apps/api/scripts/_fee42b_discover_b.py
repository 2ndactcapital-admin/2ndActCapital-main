"""fee42b Task 1, pass B — the v_capital_accounts gap, and the fixture surface."""

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


def h(t):
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


async def main():
    dsn, prov = await admin_dsn()
    conn = await connect(dsn)
    try:
        h("journal_lines — the view's base, and what dim_member_series_id points at")
        for c in await conn.fetch(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name='journal_lines'"
            " ORDER BY ordinal_position"
        ):
            print(f"    {c['column_name']:<28} {c['data_type']} "
                  f"{'' if c['is_nullable']=='YES' else 'NOT NULL'}")
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid='public.journal_lines'::regclass ORDER BY contype, conname"
        ):
            print(f"    [{r['conname']}] {r['def']}")
        print(f"  journal_lines rows: {await conn.fetchval('SELECT count(*) FROM journal_lines')}")
        print("  non-null dim_member_series_id: "
              f"{await conn.fetchval('SELECT count(*) FROM journal_lines WHERE dim_member_series_id IS NOT NULL')}")
        print("  capital accounts in COA: ")
        for r in await conn.fetch(
            "SELECT code, name, is_capital_account, tax_character_code FROM chart_of_accounts"
            " WHERE system_to IS NULL ORDER BY code"
        ):
            print(f"    {dict(r)}")

        h("Any table at all whose name suggests the member-series dimension")
        for r in await conn.fetch(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema='public' AND (table_name ILIKE '%member_series%'"
            "   OR table_name ILIKE 'dim_%') ORDER BY 1"
        ):
            print(f"    {r['table_name']}")
        print("  (empty above == the dimension the view groups by has no table)")

        h("spvs — vehicle_type / master_entity_id: is there a grain below the SPV?")
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid='public.spvs'::regclass AND contype='c'"
        ):
            print(f"    {r['conname']}: {r['def']}")
        for r in await conn.fetch(
            "SELECT id, name, deal_id, vehicle_type, master_entity_id, class_label FROM spvs"
        ):
            print(f"    {dict(r)}")
        print("  spv_transactions columns referencing an investment/position: ")
        for c in await conn.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name='spv_transactions'"
            "   AND (column_name ILIKE '%invest%' OR column_name ILIKE '%position%'"
            "        OR column_name ILIKE '%asset%' OR column_name ILIKE '%security%')"
        ):
            print(f"    {c['column_name']}")
        print("  (empty above == an SPV transaction is not attributable to a sub-investment)")

        h("spv_subscriptions + entities for the org — real fixture surface")
        for r in await conn.fetch(
            "SELECT s.id, s.spv_id, s.entity_id, s.commitment_amount, s.funded_amount,"
            " s.ownership_pct, s.subscription_status, s.valid_to, e.display_name"
            " FROM spv_subscriptions s LEFT JOIN entities e ON e.id = s.entity_id"
            " ORDER BY s.created_at"
        ):
            print(f"    {dict(r)}")

        h("workflow_definitions / versions in the DEFAULT org")
        for r in await conn.fetch(
            "SELECT d.id, d.org_id, d.name, d.is_active,"
            " (SELECT count(*) FROM workflow_versions v WHERE v.workflow_definition_id=d.id) AS versions,"
            " (SELECT count(*) FROM workflow_versions v WHERE v.workflow_definition_id=d.id AND v.is_current) AS current"
            " FROM workflow_definitions d ORDER BY d.org_id, d.created_at"
        ):
            print(f"    {dict(r)}")

        h("workflow_runs — context readability")
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid='public.workflow_runs'::regclass AND contype='c'"
        ):
            print(f"    {r['conname']}: {r['def']}")
        print(f"  rows: {await conn.fetchval('SELECT count(*) FROM workflow_runs')}")

        h("domain_event_deliveries shape")
        for c in await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name='domain_event_deliveries'"
            " ORDER BY ordinal_position"
        ):
            print(f"    {c['column_name']:<28} {c['data_type']}")

        h("users available as maker/checker in the default org")
        for r in await conn.fetch(
            "SELECT id, email, org_id, is_active FROM users"
            " WHERE org_id = '00000000-0000-0000-0000-000000000001'"
            " ORDER BY created_at LIMIT 12"
        ):
            print(f"    {dict(r)}")

        h("app_service role — rolbypassrls")
        for r in await conn.fetch(
            "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles"
            " WHERE rolname IN ('app_service','postgres')"
        ):
            print(f"    {dict(r)}")

        h("organizations available for the cross-org test")
        for r in await conn.fetch("SELECT id, name FROM organizations ORDER BY created_at"):
            print(f"    {dict(r)}")
    finally:
        await conn.close()


asyncio.run(main())
