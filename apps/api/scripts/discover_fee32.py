"""Sprint fee32 — Task 1 discovery. Reports the DEPLOYED shape, never assumes.

Read-only. Prints what is actually in the database for the four things fee32
touches: portfolio.positions.account_id, public.accounts / account_owners /
households, and portfolio_precedence_household_overrides. Nothing here writes.
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


async def describe(conn, schema: str, table: str, *, full: bool = True) -> None:
    cols = await conn.fetch(COLUMNS_SQL, schema, table)
    if not cols:
        print(f"\n### {schema}.{table} — NOT DEPLOYED")
        return
    print(f"\n### {schema}.{table}")
    for c in cols:
        null = "NULL" if c["is_nullable"] == "YES" else "NOT NULL"
        dflt = f" DEFAULT {c['column_default']}" if c["column_default"] else ""
        print(f"    {c['column_name']:<32} {c['data_type']:<28} {null}{dflt}")
    if not full:
        return
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
        await describe(conn, "portfolio", "positions")
        await describe(conn, "public", "portfolio_precedence_household_overrides")
        await describe(conn, "public", "accounts", full=False)
        await describe(conn, "public", "account_owners")
        await describe(conn, "public", "households", full=False)
        await describe(conn, "public", "household_memberships", full=False)
        await describe(conn, "public", "account_import_exceptions")

        print("\n### entities.primary_household_id presence")
        row = await conn.fetchrow(
            COLUMNS_SQL.replace("ORDER BY c.ordinal_position", "")
            + " AND c.column_name = 'primary_household_id'",
            "public", "entities",
        )
        print(f"    {dict(row) if row else 'ABSENT'}")

        print("\n### live row counts")
        for schema, table in (
            ("portfolio", "positions"),
            ("public", "accounts"),
            ("public", "account_owners"),
            ("public", "households"),
            ("public", "portfolio_precedence_household_overrides"),
            ("public", "account_import_exceptions"),
        ):
            n = await conn.fetchval(f"SELECT count(*) FROM {schema}.{table}")
            print(f"    {schema}.{table}: {n}")

        print("\n### positions.account_id populated?")
        n = await conn.fetchval(
            "SELECT count(*) FROM portfolio.positions WHERE account_id IS NOT NULL"
        )
        print(f"    non-null account_id: {n}")

        print("\n### org_settings rows for the precedence key")
        rows = await conn.fetch(
            "SELECT org_id::text, setting_key, setting_value FROM public.org_settings "
            "WHERE setting_key LIKE 'portfolio.precedence%'"
        )
        print(f"    {[dict(r) for r in rows] or 'none — every org is on the default'}")

        print("\n### app_service rolbypassrls")
        print("    ", dict(await conn.fetchrow(
            "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles "
            "WHERE rolname IN ('app_service','postgres') ORDER BY rolname"
        )))
        for r in await conn.fetch(
            "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles "
            "WHERE rolname IN ('app_service','postgres') ORDER BY rolname"
        ):
            print("    ", dict(r))
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
