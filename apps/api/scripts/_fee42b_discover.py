"""fee42b Task 1 — live discovery. Read-only. Reports, never assumes.

Answers, against the deployed database and not the sprint prompt:

  A. spv_carry_runs / spv_carry_run_lines exactly as deployed — columns,
     CHECKs, FKs, indexes, RLS + policies, and the two immutability triggers
     Part 1 claims to have applied.
  B. v_capital_accounts' real definition and real contents: what cumulative
     figures it can supply PER INVESTOR PER SPV, and whether its grain
     (dim_member_series_id) can even be joined to an SPV investor entity.
  C. Whether HARD/SOFT hurdle convention is documented anywhere in this repo.
  D. The real event->workflow mechanism: workflow_versions.is_current, the
     context shape publish_event writes, and whether any subscriber to
     event_type='spv_realization' exists today.
  E. The real fixture surface: transaction_types for dist_gain, SPVs with
     subscriptions, existing spv_fee_terms carrying carry economics.
  F. What WHOLE_FUND carry_basis would need vs what is actually derivable.
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

NEW_TABLES = ["spv_carry_runs", "spv_carry_run_lines"]


def h(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


async def describe(conn, table: str) -> bool:
    h(f"TABLE public.{table}")
    if await conn.fetchval("SELECT to_regclass($1)", f"public.{table}") is None:
        print("  !! NOT DEPLOYED")
        return False

    cols = await conn.fetch(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position
        """,
        table,
    )
    print("  COLUMNS")
    for c in cols:
        nn = "" if c["is_nullable"] == "YES" else " NOT NULL"
        dflt = f" DEFAULT {c['column_default']}" if c["column_default"] else ""
        print(f"    {c['column_name']:<34} {c['data_type']}{nn}{dflt}")

    cons = await conn.fetch(
        """
        SELECT c.conname, c.contype, pg_get_constraintdef(c.oid) AS def
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public' AND t.relname = $1
        ORDER BY c.contype, c.conname
        """,
        table,
    )
    print("  CONSTRAINTS")
    for c in cons:
        print(f"    [{c['contype']}] {c['conname']}: {c['def']}")
    if not cons:
        print("    (none)")

    idx = await conn.fetch(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname='public' AND tablename=$1 ORDER BY indexname",
        table,
    )
    print("  INDEXES")
    for i in idx:
        print(f"    {i['indexname']}: {i['indexdef']}")

    rls = await conn.fetchrow(
        """
        SELECT c.relrowsecurity, c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname='public' AND c.relname=$1
        """,
        table,
    )
    print(f"  RLS enabled={rls['relrowsecurity']} forced={rls['relforcerowsecurity']}")
    pol = await conn.fetch(
        """
        SELECT polname, polcmd,
               pg_get_expr(polqual, polrelid)      AS using_expr,
               pg_get_expr(polwithcheck, polrelid) AS check_expr
        FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname='public' AND c.relname=$1
        ORDER BY polname
        """,
        table,
    )
    print("  POLICIES")
    for p in pol:
        print(f"    {p['polname']} [{p['polcmd']}]")
        print(f"       USING       {p['using_expr']}")
        print(f"       WITH CHECK  {p['check_expr']}")
    if not pol:
        print("    (none)")

    trg = await conn.fetch(
        """
        SELECT t.tgname, pg_get_triggerdef(t.oid) AS def
        FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname='public' AND c.relname=$1 AND NOT t.tgisinternal
        ORDER BY t.tgname
        """,
        table,
    )
    print("  TRIGGERS")
    for t in trg:
        print(f"    {t['tgname']}: {t['def']}")
        fn = await conn.fetchval(
            """
            SELECT pg_get_functiondef(p.oid) FROM pg_proc p
            JOIN pg_trigger tg ON tg.tgfoid = p.oid
            WHERE tg.tgname = $1 LIMIT 1
            """,
            t["tgname"],
        )
        if fn:
            for line in fn.splitlines():
                print(f"       | {line}")
    if not trg:
        print("    (none)")

    n = await conn.fetchval(f"SELECT count(*) FROM public.{table}")
    print(f"  ROWS: {n}")
    return True


async def main() -> None:
    dsn, prov = await admin_dsn()
    if dsn is None:
        print(f"FATAL: no working admin DSN — {prov}")
        sys.exit(1)
    print(f"admin: {prov}")
    conn = await connect(dsn)
    try:
        # ── A
        for t in NEW_TABLES:
            await describe(conn, t)

        # ── B. v_capital_accounts
        h("VIEW v_capital_accounts — definition and grain")
        vdef = await conn.fetchval(
            "SELECT pg_get_viewdef('public.v_capital_accounts'::regclass, true)"
        )
        print(vdef)
        opts = await conn.fetchval(
            "SELECT reloptions FROM pg_class WHERE oid='public.v_capital_accounts'::regclass"
        )
        print(f"\n  reloptions: {opts}")
        n = await conn.fetchval("SELECT count(*) FROM v_capital_accounts")
        print(f"  ROWS: {n}")
        if n:
            for r in await conn.fetch("SELECT * FROM v_capital_accounts LIMIT 15"):
                print(f"    {dict(r)}")
            print("\n  distinct account_code / tax_character_code:")
            for r in await conn.fetch(
                "SELECT account_code, account_name, tax_character_code, count(*) AS n,"
                " sum(balance) AS total FROM v_capital_accounts"
                " GROUP BY 1,2,3 ORDER BY 1"
            ):
                print(f"    {dict(r)}")

        h("dim_member_series — can it reach an SPV investor entity?")
        if await conn.fetchval("SELECT to_regclass('public.dim_member_series')") is None:
            print("  !! public.dim_member_series NOT DEPLOYED")
        else:
            for c in await conn.fetch(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
                " WHERE table_schema='public' AND table_name='dim_member_series'"
                " ORDER BY ordinal_position"
            ):
                print(f"    {c['column_name']:<30} {c['data_type']} "
                      f"{'NULL' if c['is_nullable']=='YES' else 'NOT NULL'}")
            print(f"  ROWS: {await conn.fetchval('SELECT count(*) FROM dim_member_series')}")
            for r in await conn.fetch("SELECT * FROM dim_member_series LIMIT 10"):
                print(f"    {dict(r)}")

        h("GL base tables behind the view")
        for t in ("journal_entries", "journal_entry_lines", "chart_of_accounts",
                  "posting_templates", "posting_template_lines"):
            reg = await conn.fetchval("SELECT to_regclass($1)", f"public.{t}")
            if reg is None:
                print(f"  {t}: NOT DEPLOYED")
                continue
            n = await conn.fetchval(f"SELECT count(*) FROM public.{t}")
            print(f"  {t}: {n} rows")

        # ── D. event -> workflow mechanism
        h("workflow_versions / triggers — the deployed event mechanism")
        for c in await conn.fetch(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
            " WHERE table_schema='public' AND table_name='workflow_versions'"
            " ORDER BY ordinal_position"
        ):
            print(f"    workflow_versions.{c['column_name']:<26} {c['data_type']}")
        print("\n  trigger_type CHECK:")
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid='public.workflow_triggers'::regclass AND contype='c'"
        ):
            print(f"    {r['conname']}: {r['def']}")
        print("\n  existing event triggers:")
        for r in await conn.fetch(
            "SELECT t.id, t.org_id, t.event_type, t.is_active, d.name AS defn,"
            " (SELECT count(*) FROM workflow_versions v"
            "   WHERE v.workflow_definition_id = d.id AND v.is_current) AS current_versions"
            " FROM workflow_triggers t JOIN workflow_definitions d"
            "   ON d.id = t.workflow_definition_id"
            " WHERE t.trigger_type = 'event' ORDER BY t.created_at"
        ):
            print(f"    {dict(r)}")
        print(f"\n  domain_events rows: "
              f"{await conn.fetchval('SELECT count(*) FROM domain_events')}")
        for r in await conn.fetch(
            "SELECT event_type, count(*) FROM domain_events GROUP BY 1 ORDER BY 1"
        ):
            print(f"    {dict(r)}")

        # ── E. fixture surface
        h("transaction_types — realization predicate")
        for r in await conn.fetch(
            "SELECT id, code, category, performance_impact, affects_nav"
            " FROM public.transaction_types"
            " WHERE category='distribution' OR performance_impact='gain'"
            " ORDER BY code"
        ):
            print(f"    {dict(r)}")

        h("SPVs with subscriptions + deployed spv_fee_terms carry economics")
        for r in await conn.fetch(
            """
            SELECT s.id, s.org_id, s.name, s.class_label, s.spv_status,
                   s.vehicle_entity_id,
                   (SELECT count(*) FROM spv_subscriptions x
                      WHERE x.spv_id = s.id AND x.valid_to IS NULL) AS subs,
                   (SELECT count(*) FROM spv_transactions x WHERE x.spv_id = s.id) AS txns
            FROM spvs s ORDER BY s.created_at
            """
        ):
            print(f"    {dict(r)}")
        print("\n  spv_fee_terms (active):")
        for r in await conn.fetch(
            "SELECT id, spv_id, class_label, carry_pct, hurdle_pct, hurdle_type,"
            " catchup_pct, carry_basis, clawback_applies, effective_from, effective_to"
            " FROM spv_fee_terms WHERE system_to IS NULL ORDER BY created_at"
        ):
            print(f"    {dict(r)}")

        # ── F. WHOLE_FUND feasibility
        h("WHOLE_FUND feasibility — is there cumulative realized-gain history?")
        for r in await conn.fetch(
            """
            SELECT t.spv_id, tt.code, t.status, count(*) AS n, sum(t.amount) AS total
            FROM spv_transactions t
            LEFT JOIN public.transaction_types tt ON tt.id = t.transaction_type_id
            GROUP BY 1,2,3 ORDER BY 1,2,3
            """
        ):
            print(f"    {dict(r)}")
        print("\n  spv_transaction_allocations by status:")
        for r in await conn.fetch(
            "SELECT status, count(*) AS n, sum(allocated_amount) AS total"
            " FROM spv_transaction_allocations GROUP BY 1 ORDER BY 1"
        ):
            print(f"    {dict(r)}")
        print("\n  contribution/capital-call types available:")
        for r in await conn.fetch(
            "SELECT id, code, category, performance_impact FROM public.transaction_types"
            " WHERE category IN ('contribution','capital_call') OR code ILIKE '%contrib%'"
            " OR code ILIKE '%call%' ORDER BY code"
        ):
            print(f"    {dict(r)}")

        h("assistant_activities — maker-checker CHECK + related_type vocabulary")
        for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint"
            " WHERE conrelid='public.assistant_activities'::regclass AND contype='c'"
        ):
            print(f"    {r['conname']}: {r['def']}")
        for r in await conn.fetch(
            "SELECT related_type, count(*) FROM assistant_activities"
            " GROUP BY 1 ORDER BY 1"
        ):
            print(f"    {dict(r)}")
    finally:
        await conn.close()


asyncio.run(main())
