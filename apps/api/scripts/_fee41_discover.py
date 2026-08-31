"""fee41 Task 1 — live discovery. Read-only. Reports, never assumes."""

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

TABLES = [
    "fee_narrative_templates",
    "fee_narratives",
    "fee_schedules",
    "fee_schedule_tiers",
    "fee_exclusions",
    "fee_discounts",
    "fee_credits",
]


async def describe(conn, table: str) -> None:
    print(f"\n{'=' * 78}\nTABLE public.{table}\n{'=' * 78}")
    exists = await conn.fetchval(
        "SELECT to_regclass($1)", f"public.{table}")
    if exists is None:
        print("  !! NOT DEPLOYED")
        return
    cols = await conn.fetch(
        """
        SELECT column_name, data_type, is_nullable, column_default,
               character_maximum_length, numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=$1
        ORDER BY ordinal_position
        """, table)
    for c in cols:
        t = c["data_type"]
        if c["character_maximum_length"]:
            t += f"({c['character_maximum_length']})"
        elif c["numeric_precision"] and t == "numeric":
            t += f"({c['numeric_precision']},{c['numeric_scale']})"
        nn = "" if c["is_nullable"] == "YES" else " NOT NULL"
        d = f" DEFAULT {c['column_default']}" if c["column_default"] else ""
        print(f"  {c['column_name']:<32} {t}{nn}{d}")

    cons = await conn.fetch(
        "SELECT conname, contype, pg_get_constraintdef(oid) AS def "
        "FROM pg_constraint WHERE conrelid = $1::regclass ORDER BY contype, conname",
        f"public.{table}")
    print("  -- constraints --")
    for c in cons:
        print(f"  [{c['contype']}] {c['conname']}: {c['def']}")

    idx = await conn.fetch(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname='public' AND tablename=$1 ORDER BY indexname", table)
    print("  -- indexes --")
    for i in idx:
        print(f"  {i['indexdef']}")

    rls = await conn.fetchrow(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid = $1::regclass", f"public.{table}")
    print(f"  -- RLS enabled={rls['relrowsecurity']} forced={rls['relforcerowsecurity']} --")
    pols = await conn.fetch(
        "SELECT polname, polcmd, pg_get_expr(polqual, polrelid) AS q, "
        "       pg_get_expr(polwithcheck, polrelid) AS w, "
        "       (SELECT array_agg(rolname) FROM pg_roles WHERE oid = ANY(polroles)) AS roles "
        "FROM pg_policy WHERE polrelid = $1::regclass ORDER BY polname",
        f"public.{table}")
    for p in pols:
        print(f"  POLICY {p['polname']} cmd={p['polcmd']} roles={p['roles']}")
        print(f"     USING {p['q']}")
        print(f"     CHECK {p['w']}")

    n = await conn.fetchval(f"SELECT count(*) FROM public.{table}")
    print(f"  -- live row count: {n} --")


async def main() -> int:
    dsn, prov = await admin_dsn()
    if not dsn:
        print(f"FAIL: {prov}")
        return 1
    print(f"admin: {prov}")
    conn = await connect(dsn)
    try:
        for t in TABLES:
            await describe(conn, t)

        print(f"\n{'=' * 78}\nFEE32 PRECEDENCE SURFACE\n{'=' * 78}")
        rows = await conn.fetch(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_name ILIKE '%precedence%' OR table_name ILIKE '%golden%' "
            "ORDER BY 1,2")
        print("tables matching precedence/golden:")
        for r in rows:
            print(f"  {r['table_schema']}.{r['table_name']}")

        fns = await conn.fetch(
            "SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid) AS args, "
            "       pg_get_function_result(p.oid) AS ret "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE p.proname ILIKE '%precedence%' ORDER BY 1,2")
        print("functions matching precedence:")
        for r in fns:
            print(f"  {r['nspname']}.{r['proname']}({r['args']}) -> {r['ret']}")

        print(f"\n{'=' * 78}\nALL fee_* TABLES DEPLOYED\n{'=' * 78}")
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name LIKE 'fee%' ORDER BY 1")
        for r in rows:
            print(f"  {r['table_name']}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
