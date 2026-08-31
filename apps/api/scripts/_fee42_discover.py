"""fee42 Task 1 — live discovery. Read-only. Reports, never assumes.

Answers, against the deployed database and not the sprint prompt:

  A. spv_fee_terms / spv_fee_side_letters exactly as deployed — columns,
     CHECKs, indexes (especially the active-per-class uniqueness and whether
     it is NULLS NOT DISTINCT), FKs, RLS + policies.
  B. What fee36's SPV_MGMT_FEE_OFFSET credit-basis resolution ACTUALLY reads.
  C. Which SPVs are active/billing vs historical, and what flat fee scalars
     they carry.
  D. spv_transaction_allocations' real shape, and which mgmt_fee_basis values
     (COMMITTED / FUNDED / NAV / INVESTED_COST) are computable today.
  E. fee_credits' deployed shape + whether an application write path exists.
"""

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

NEW_TABLES = ["spv_fee_terms", "spv_fee_side_letters"]
CONTEXT_TABLES = [
    "spvs", "spv_subscriptions", "spv_transactions",
    "spv_transaction_allocations", "fee_credits",
]


def h(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


async def describe(conn, table: str, *, full: bool) -> bool:
    h(f"TABLE public.{table}")
    if await conn.fetchval("SELECT to_regclass($1)", f"public.{table}") is None:
        print("  !! NOT DEPLOYED")
        return False

    if full:
        cols = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable, column_default,
                   numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=$1
            ORDER BY ordinal_position
            """, table)
        for c in cols:
            t = c["data_type"]
            if t == "numeric" and c["numeric_precision"]:
                t += f"({c['numeric_precision']},{c['numeric_scale']})"
            nn = "" if c["is_nullable"] == "YES" else " NOT NULL"
            d = f" DEFAULT {c['column_default']}" if c["column_default"] else ""
            print(f"  {c['column_name']:<28} {t}{nn}{d}")

    cons = await conn.fetch(
        "SELECT conname, contype, pg_get_constraintdef(oid) AS def "
        "FROM pg_constraint WHERE conrelid = $1::regclass ORDER BY contype, conname",
        f"public.{table}")
    print("  -- constraints --")
    for c in cons:
        print(f"    [{c['contype']}] {c['conname']}: {c['def']}")

    idx = await conn.fetch(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname='public' AND tablename=$1 ORDER BY indexname", table)
    print("  -- indexes --")
    for i in idx:
        print(f"    {i['indexname']}: {i['indexdef']}")

    rls = await conn.fetchrow(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relname=$1", table)
    print(f"  -- RLS: enabled={rls['relrowsecurity']} forced={rls['relforcerowsecurity']}")
    pols = await conn.fetch(
        "SELECT polname, polcmd, pg_get_expr(polqual, polrelid) AS using_expr, "
        "       pg_get_expr(polwithcheck, polrelid) AS check_expr, "
        "       (SELECT array_agg(rolname) FROM pg_roles WHERE oid = ANY(polroles)) AS roles "
        "FROM pg_policy WHERE polrelid = $1::regclass ORDER BY polname",
        f"public.{table}")
    for p in pols:
        print(f"    POLICY {p['polname']} cmd={p['polcmd']} roles={p['roles']}")
        print(f"      USING      {p['using_expr']}")
        print(f"      WITH CHECK {p['check_expr']}")
    if not pols:
        print("    (no policies)")

    n = await conn.fetchval(f"SELECT count(*) FROM public.{table}")
    print(f"  -- row count: {n}")
    return True


async def main() -> int:
    dsn, prov = await admin_dsn()
    if dsn is None:
        print(f"FATAL: {prov}")
        return 1
    print(f"connected via {prov}")
    conn = await connect(dsn)
    try:
        # ── A. the two new tables ───────────────────────────────────────────
        for t in NEW_TABLES:
            await describe(conn, t, full=True)
        for t in CONTEXT_TABLES:
            await describe(conn, t, full=False)

        # ── C. SPV population ───────────────────────────────────────────────
        h("C. spv_status distribution (all orgs)")
        for r in await conn.fetch(
            "SELECT spv_status, count(*) AS n, "
            "count(mgmt_fee_pct) AS with_mgmt, count(carry_pct) AS with_carry "
            "FROM public.spvs GROUP BY spv_status ORDER BY spv_status"
        ):
            print(f"  {r['spv_status']:<16} n={r['n']:<4} "
                  f"mgmt_fee_pct set={r['with_mgmt']:<4} carry_pct set={r['with_carry']}")

        h("C2. spv_status CHECK / vocabulary source")
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid='public.spvs'::regclass AND contype='c'"
        ):
            print(f"  {r['conname']}: {r['def']}")
        print("  -- distinct spv_status values ever recorded (spv_status_history) --")
        for r in await conn.fetch(
            "SELECT to_status, count(*) n FROM public.spv_status_history "
            "GROUP BY to_status ORDER BY to_status"
        ):
            print(f"    {r['to_status']}: {r['n']}")

        h("C3. every SPV, with what is genuinely known about its economics")
        rows = await conn.fetch(
            """
            SELECT s.id::text AS id, s.org_id::text AS org_id, s.name,
                   s.spv_status, s.class_label, s.vehicle_type,
                   s.mgmt_fee_pct, s.carry_pct, s.close_date, s.currency,
                   (SELECT count(*) FROM public.spv_subscriptions sub
                     WHERE sub.spv_id = s.id AND sub.valid_to IS NULL) AS subs,
                   (SELECT count(*) FROM public.spv_transactions t
                     WHERE t.spv_id = s.id AND t.txn_type = 'call_mgmt_fee') AS mgmt_calls,
                   (SELECT count(*) FROM public.spv_fee_terms ft
                     WHERE ft.spv_id = s.id AND ft.valid_to IS NULL
                       AND ft.system_to IS NULL) AS terms_rows
            FROM public.spvs s
            ORDER BY s.spv_status, s.name
            """)
        for r in rows:
            print(f"  {r['name'][:34]:<34} status={r['spv_status']:<12} "
                  f"class={str(r['class_label']):<6} mgmt={r['mgmt_fee_pct']} "
                  f"carry={r['carry_pct']} subs={r['subs']} "
                  f"mgmt_calls={r['mgmt_calls']} terms={r['terms_rows']}")
            print(f"       id={r['id']} org={r['org_id']} vehicle_type={r['vehicle_type']} "
                  f"close_date={r['close_date']}")

        # ── D. what each mgmt_fee_basis needs, and whether it exists ────────
        h("D. mgmt_fee_basis computability")
        checks = {
            "COMMITTED": ("spv_subscriptions.commitment_amount",
                          "SELECT count(*) FROM public.spv_subscriptions "
                          "WHERE commitment_amount IS NOT NULL AND valid_to IS NULL"),
            "FUNDED": ("spv_subscriptions.funded_amount",
                       "SELECT count(*) FROM public.spv_subscriptions "
                       "WHERE funded_amount IS NOT NULL AND valid_to IS NULL"),
            "INVESTED_COST": ("spv_transactions call/investment rows",
                              "SELECT count(*) FROM public.spv_transactions "
                              "WHERE txn_type ILIKE '%invest%' OR txn_type ILIKE '%call%'"),
        }
        for label, (src, sql) in checks.items():
            print(f"  {label:<14} source={src:<44} rows={await conn.fetchval(sql)}")
        print("  NAV            source=? — searched below")
        for r in await conn.fetch(
            "SELECT table_schema, table_name, column_name FROM information_schema.columns "
            "WHERE column_name ILIKE '%nav%' OR column_name ILIKE '%net_asset%' "
            "ORDER BY table_schema, table_name, column_name"
        ):
            print(f"    NAV-ish column: {r['table_schema']}.{r['table_name']}.{r['column_name']}")

        h("D2. spv_transactions txn_type vocabulary actually in use")
        for r in await conn.fetch(
            "SELECT txn_type, status, count(*) n FROM public.spv_transactions "
            "GROUP BY txn_type, status ORDER BY txn_type, status"
        ):
            print(f"  {r['txn_type']:<24} status={r['status']:<12} n={r['n']}")

        h("D3. spv_transaction_allocations population")
        for r in await conn.fetch(
            "SELECT a.status, count(*) n, sum(a.allocated_amount) total "
            "FROM public.spv_transaction_allocations a GROUP BY a.status ORDER BY a.status"
        ):
            print(f"  status={r['status']:<12} n={r['n']} total={r['total']}")

        # ── E. fee_credits reality ─────────────────────────────────────────
        h("E. fee_credits population by credit_source")
        for r in await conn.fetch(
            "SELECT credit_source, scope_type, count(*) n FROM public.fee_credits "
            "GROUP BY credit_source, scope_type ORDER BY credit_source"
        ):
            print(f"  {r['credit_source']:<24} scope={r['scope_type']:<12} n={r['n']}")

        h("F. accounts/households scope targets for a credit")
        for r in await conn.fetch(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='accounts' "
            "ORDER BY ordinal_position"
        ):
            print(f"  accounts.{r['column_name']:<28} {r['data_type']} "
                  f"null={r['is_nullable']}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
