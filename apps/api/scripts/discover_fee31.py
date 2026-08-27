"""Sprint fee31 Task 1 — read the deployed shape of the account layer.

Reports only. Writes nothing. Everything downstream in this sprint is written
against this output rather than against the sprint prompt's description of the
tables, because column-name drift between prompt and deployment is the repeat
offender in this codebase.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _db_connect import admin_dsn, connect  # noqa: E402

TABLES = [
    "accounts",
    "account_owners",
    "account_balances_daily",
    "account_flows",
    "account_import_batches",
]
SEED_ORG = "00000000-0000-0000-0000-000000000001"


async def main() -> int:
    dsn, provenance = await admin_dsn()
    if not dsn:
        print(f"BLOCKED — {provenance}")
        return 1
    print(f"[db] connected via {provenance}")
    conn = await connect(dsn)
    try:
        print(f"[db] current_user={await conn.fetchval('select current_user')}")

        print("\n=== EXISTENCE ===")
        rows = await conn.fetch(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = ANY($1::text[])
            ORDER BY c.relname
            """,
            TABLES,
        )
        found = {r["relname"] for r in rows}
        for r in rows:
            print(
                f"  {r['relname']:<26} rls={r['relrowsecurity']} "
                f"force={r['relforcerowsecurity']} policies={r['policies']}"
            )
        for missing in sorted(set(TABLES) - found):
            print(f"  {missing:<26} *** NOT DEPLOYED ***")

        print("\n=== COLUMNS ===")
        for table in TABLES:
            if table not in found:
                continue
            cols = await conn.fetch(
                """
                SELECT column_name, data_type, udt_name, is_nullable, column_default,
                       numeric_precision, numeric_scale, character_maximum_length
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                ORDER BY ordinal_position
                """,
                table,
            )
            print(f"\n-- {table} ({len(cols)} cols)")
            for c in cols:
                t = c["data_type"]
                if t == "USER-DEFINED":
                    t = f"enum:{c['udt_name']}"
                elif t == "numeric" and c["numeric_precision"]:
                    t = f"numeric({c['numeric_precision']},{c['numeric_scale']})"
                elif c["character_maximum_length"]:
                    t = f"{t}({c['character_maximum_length']})"
                null = "" if c["is_nullable"] == "YES" else " NOT NULL"
                default = f" DEFAULT {c['column_default']}" if c["column_default"] else ""
                print(f"   {c['column_name']:<30} {t}{null}{default}")

        print("\n=== CONSTRAINTS + INDEXES ===")
        for table in TABLES:
            if table not in found:
                continue
            cons = await conn.fetch(
                """
                SELECT conname, contype, pg_get_constraintdef(oid) AS def
                FROM pg_constraint
                WHERE conrelid = ($1::regclass)
                ORDER BY contype, conname
                """,
                f"public.{table}",
            )
            idx = await conn.fetch(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname='public' AND tablename=$1 ORDER BY indexname",
                table,
            )
            print(f"\n-- {table}")
            for c in cons:
                print(f"   [{c['contype']}] {c['conname']}: {c['def']}")
            for i in idx:
                print(f"   [i] {i['indexname']}: {i['indexdef']}")

        print("\n=== RLS POLICIES ===")
        pols = await conn.fetch(
            """
            SELECT tablename, policyname, permissive, roles, cmd, qual, with_check
            FROM pg_policies
            WHERE schemaname = 'public' AND tablename = ANY($1::text[])
            ORDER BY tablename, policyname
            """,
            TABLES,
        )
        for p in pols:
            print(f"\n-- {p['tablename']}.{p['policyname']} [{p['cmd']}] {p['permissive']}")
            print(f"   roles={list(p['roles'])}")
            print(f"   using={p['qual']}")
            print(f"   check={p['with_check']}")
        if not pols:
            print("   (none)")

        print("\n=== ENUMS REFERENCED ===")
        enums = await conn.fetch(
            """
            SELECT DISTINCT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder) AS labels
            FROM pg_type t
            JOIN pg_enum e ON e.enumtypid = t.oid
            WHERE t.oid IN (
                SELECT a.atttypid FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname='public' AND c.relname = ANY($1::text[]) AND a.attnum > 0
            )
            GROUP BY t.typname ORDER BY t.typname
            """,
            TABLES,
        )
        for e in enums:
            print(f"   {e['typname']}: {list(e['labels'])}")
        if not enums:
            print("   (none)")

        print("\n=== ROW COUNTS (all orgs, superuser view) ===")
        for table in TABLES:
            if table in found:
                n = await conn.fetchval(f"SELECT count(*) FROM public.{table}")
                print(f"   {table:<26} {n}")

        print("\n=== entity_holdings (seed org) ===")
        eh = await conn.fetchrow(
            """
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE org_id = $1) AS n_seed,
                   min(as_of_date) FILTER (WHERE org_id = $1) AS min_d,
                   max(as_of_date) FILTER (WHERE org_id = $1) AS max_d
            FROM public.entity_holdings
            """,
            SEED_ORG,
        )
        print(f"   total={eh['n']} seed_org={eh['n_seed']} range={eh['min_d']}..{eh['max_d']}")

        print("\n=== households ===")
        hh = await conn.fetch(
            "SELECT org_id, count(*) AS n FROM public.households GROUP BY org_id ORDER BY n DESC"
        )
        for h in hh:
            print(f"   {h['org_id']}  {h['n']}")
        sample = await conn.fetch(
            "SELECT id, name FROM public.households WHERE org_id=$1 ORDER BY created_at LIMIT 5",
            SEED_ORG,
        )
        for s in sample:
            print(f"   sample: {s['id']}  {s['name']}")

        print("\n=== entities (seed org, count by type) ===")
        ent = await conn.fetch(
            "SELECT entity_type::text AS t, count(*) AS n FROM public.entities "
            "WHERE org_id=$1 GROUP BY 1 ORDER BY n DESC",
            SEED_ORG,
        )
        for e in ent:
            print(f"   {e['t']:<24} {e['n']}")

        print("\n=== org_settings keys (custody / fee / salt relevant) ===")
        os_cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='org_settings' ORDER BY ordinal_position"
        )
        print("   columns:", [c["column_name"] for c in os_cols])
        keys = await conn.fetch(
            "SELECT DISTINCT setting_key FROM public.org_settings "
            "WHERE setting_key ILIKE '%custod%' OR setting_key ILIKE '%fee%' "
            "   OR setting_key ILIKE '%salt%' OR setting_key ILIKE '%account%' "
            "ORDER BY 1"
        )
        print("   matching keys:", [k["setting_key"] for k in keys] or "(none)")

        print("\n=== organizations ===")
        orgs = await conn.fetch("SELECT id, name, slug FROM public.organizations ORDER BY name")
        for o in orgs:
            print(f"   {o['id']}  {o['slug']:<20} {o['name']}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
