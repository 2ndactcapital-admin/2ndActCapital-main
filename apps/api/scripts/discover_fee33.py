"""Sprint fee33 — Task 1 discovery. Reports the DEPLOYED shape, never assumes.

Read-only. Nothing here writes.

WHY THIS SCRIPT MATTERS MORE THAN discover_fee32's DID
──────────────────────────────────────────────────────────────────────────────
The fee33 prompt asserts that Part 1 (``billing_groups``,
``billing_group_members``) "is already applied by Joe directly via Supabase MCP
— confirm it live before writing any code, do not re-create it."

It was not. Neither table exists in any schema, no enum backing ``group_type``
exists, and the prompt does not carry the DDL text either. This script is the
reproducible proof of that finding, so a reviewer can re-run it rather than
take the sprint log's word for it.

Run it BEFORE the migration to see the absence; run it after to see what the
sprint applied in its place.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, app_service_dsn, connect  # noqa: E402


COLUMNS_SQL = """
SELECT c.column_name, c.data_type, c.is_nullable, c.column_default
FROM information_schema.columns c
WHERE c.table_schema = $1 AND c.table_name = $2
ORDER BY c.ordinal_position
"""

CONSTRAINTS_SQL = """
SELECT con.conname, pg_get_constraintdef(con.oid) AS def, con.contype
FROM pg_constraint con
JOIN pg_class rel ON rel.oid = con.conrelid
JOIN pg_namespace ns ON ns.oid = rel.relnamespace
WHERE ns.nspname = $1 AND rel.relname = $2
ORDER BY con.contype, con.conname
"""

INDEX_SQL = """
SELECT indexname, indexdef FROM pg_indexes
WHERE schemaname = $1 AND tablename = $2 ORDER BY indexname
"""

POLICY_SQL = """
SELECT polname, pg_get_expr(polqual, polrelid) AS using_expr,
       pg_get_expr(polwithcheck, polrelid) AS check_expr
FROM pg_policy pol
JOIN pg_class rel ON rel.oid = pol.polrelid
JOIN pg_namespace ns ON ns.oid = rel.relnamespace
WHERE ns.nspname = $1 AND rel.relname = $2
ORDER BY polname
"""

GRANT_SQL = """
SELECT grantee, string_agg(privilege_type, ',' ORDER BY privilege_type) AS privs
FROM information_schema.role_table_grants
WHERE table_schema = $1 AND table_name = $2
GROUP BY grantee ORDER BY grantee
"""


async def describe(conn, schema: str, table: str, *, full: bool = True) -> bool:
    """Print the deployed shape. Returns whether the table exists at all."""
    cols = await conn.fetch(COLUMNS_SQL, schema, table)
    if not cols:
        print(f"\n### {schema}.{table} — NOT DEPLOYED")
        return False
    print(f"\n### {schema}.{table}")
    for c in cols:
        null = "NULL" if c["is_nullable"] == "YES" else "NOT NULL"
        dflt = f" DEFAULT {c['column_default']}" if c["column_default"] else ""
        print(f"    {c['column_name']:<32} {c['data_type']:<28} {null}{dflt}")
    if not full:
        return True
    for row in await conn.fetch(CONSTRAINTS_SQL, schema, table):
        print(f"    [con {row['contype']}] {row['conname']}: {row['def']}")
    for row in await conn.fetch(INDEX_SQL, schema, table):
        print(f"    [idx] {row['indexdef']}")
    rls = await conn.fetchval(
        "SELECT rel.relrowsecurity FROM pg_class rel "
        "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
        "WHERE ns.nspname = $1 AND rel.relname = $2",
        schema, table,
    )
    print(f"    [rls enabled] {rls}")
    for row in await conn.fetch(POLICY_SQL, schema, table):
        print(f"    [pol] {row['polname']} USING {row['using_expr']}")
        print(f"          WITH CHECK {row['check_expr']}")
    for row in await conn.fetch(GRANT_SQL, schema, table):
        print(f"    [grant] {row['grantee']}: {row['privs']}")
    return True


async def main() -> int:
    dsn, prov = await admin_dsn()
    if dsn is None:
        print(f"BLOCKED: no admin DSN — {prov}")
        return 2
    print(f"admin dsn provenance: {prov}")
    app_dsn, app_prov = await app_service_dsn()
    print(f"app_service dsn provenance: {app_prov}")

    conn = await connect(dsn)
    try:
        print("\n" + "=" * 78)
        print("PART 1 TABLES — the prompt says these are already applied")
        print("=" * 78)
        groups_live = await describe(conn, "public", "billing_groups")
        members_live = await describe(conn, "public", "billing_group_members")

        print("\n### any table anywhere matching %billing% or %fee%")
        rows = await conn.fetch(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_name ILIKE '%billing%' OR table_name ILIKE '%fee%' "
            "ORDER BY 1, 2"
        )
        print(f"    {[dict(r) for r in rows] or 'NONE'}")

        print("\n### any enum backing a group_type vocabulary")
        rows = await conn.fetch(
            "SELECT n.nspname, t.typname, "
            "       array_agg(e.enumlabel ORDER BY e.enumsortorder) AS labels "
            "FROM pg_type t "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "LEFT JOIN pg_enum e ON e.enumtypid = t.oid "
            "WHERE t.typname ILIKE ANY (ARRAY['%billing%','%breakpoint%','%payer%','%fee%']) "
            "GROUP BY 1, 2"
        )
        print(f"    {[dict(r) for r in rows] or 'NONE'}")

        if not (groups_live and members_live):
            print(
                "\n    [FIND] The prompt's premise is FALSE. Part 1 was never\n"
                "           applied, and the prompt does not carry the DDL. The\n"
                "           sprint authors it — see migrations/fee33_billing_groups.sql."
            )

        print("\n" + "=" * 78)
        print("CONVENTIONS THE NEW TABLES MUST MATCH — fee31/fee32's deployed shape")
        print("=" * 78)
        await describe(conn, "public", "accounts")
        await describe(conn, "public", "account_owners", full=False)
        await describe(conn, "public", "households")
        await describe(conn, "public", "household_memberships", full=False)

        print("\n" + "=" * 78)
        print("IS THERE ALREADY A NATURAL DEFAULT BILLING GROUP PER HOUSEHOLD?")
        print("=" * 78)

        print("\n### entities.primary_household_id — the STRICT (at-most-one) grouping")
        row = await conn.fetchrow(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'entities' "
            "  AND column_name = 'primary_household_id'"
        )
        print(f"    {dict(row) if row else 'ABSENT'}")
        print(
            "    services/households.py calls this the non-overlapping partition\n"
            "    'used for net-worth / billing'. household_memberships is the\n"
            "    many-to-many one and OVERLAPS by design — summing over it\n"
            "    double-counts, which is exactly what corrupts a breakpoint."
        )

        print("\n### accounts.household_id — nullable? defaulted? populated?")
        row = await conn.fetchrow(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'accounts' "
            "  AND column_name = 'household_id'"
        )
        print(f"    {dict(row) if row else 'ABSENT'}")

        print("\n### per-account billing attributes that already exist")
        rows = await conn.fetch(
            "SELECT column_name, data_type, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'accounts' "
            "  AND column_name IN ('is_billable','service_model','is_discretionary') "
            "ORDER BY column_name"
        )
        for r in rows:
            print(f"    {dict(r)}")

        print("\n### live row counts (teardown risk surface)")
        for schema, table in (
            ("public", "households"),
            ("public", "household_memberships"),
            ("public", "accounts"),
            ("public", "account_owners"),
            ("public", "entities"),
            ("public", "organizations"),
        ):
            n = await conn.fetchval(f"SELECT count(*) FROM {schema}.{table}")
            print(f"    {schema}.{table}: {n}")

        print("\n### accounts grouped by household — would a default group be well-defined?")
        row = await conn.fetchrow(
            "SELECT count(*) FILTER (WHERE household_id IS NOT NULL) AS with_hh, "
            "       count(*) FILTER (WHERE household_id IS NULL)     AS without_hh "
            "FROM public.accounts WHERE system_to IS NULL"
        )
        print(f"    {dict(row)}")

        print("\n### app_service must NOT bypass RLS, or every isolation check is vacuous")
        for r in await conn.fetch(
            "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles "
            "WHERE rolname IN ('app_service','postgres') ORDER BY rolname"
        ):
            print(f"    {dict(r)}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
