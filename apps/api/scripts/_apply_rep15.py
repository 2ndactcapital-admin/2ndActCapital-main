"""REP15 — apply the platform-published models schema and prove it landed.

    python3 apps/api/scripts/_apply_rep15.py --schema    # docs/rep15_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep15.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_rep15.py --all       # both, in order
    python3 apps/api/scripts/_apply_rep15.py --fix1      # docs/rep15_fix1.sql (provided_model_guard), then --verify

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table
owner). Idempotent. Never prints a secret. THE MIGRATION WRITES NO ROWS: no gate row exists afterwards (no row = the
gate is OFF), --verify asserts it for the real Hollisworks platform org.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

SCHEMA_SQL = REPO / "docs" / "rep15_part1.sql"
FIX1_SQL = REPO / "docs" / "rep15_fix1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
PLATFORM_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

NEW_TABLES = ("portfolio.model_providers", "portfolio.model_publishing_gates", "portfolio.catalog_models",
              "portfolio.catalog_versions", "portfolio.provider_subscriptions", "portfolio.provider_fee_accruals",
              "portfolio.provider_fee_invoices", "portfolio.provider_fee_invoice_lines",
              "portfolio.model_provider_events")
GUARDS = {"portfolio.model_providers": "model_providers_guard_trg",
          "portfolio.model_publishing_gates": "model_publishing_gates_guard_trg",
          "portfolio.catalog_models": "catalog_models_guard_trg",
          "portfolio.catalog_versions": "catalog_versions_guard_trg",
          "portfolio.provider_subscriptions": "provider_subscriptions_guard_trg",
          "portfolio.provider_fee_accruals": "provider_fee_accruals_guard_trg",
          "portfolio.provider_fee_invoices": "provider_fee_invoices_guard_trg",
          "portfolio.provider_fee_invoice_lines": "provider_fee_invoice_lines_guard_trg",
          "portfolio.model_provider_events": "model_provider_events_guard_trg",
          "portfolio.model_versions": "model_versions_provided_trg",
          "portfolio.model_version_positions": "model_version_positions_provided_trg",
          "portfolio.model_sleeves": "model_sleeves_provided_trg",
          "portfolio.models": "models_provided_refs_trg"}
UNIQUES = ("model_providers_live_uq", "model_publishing_gates_live_uq", "catalog_models_model_live_uq",
           "catalog_versions_model_version_uq", "catalog_versions_no_uq", "catalog_versions_pending_uq",
           "provider_subscriptions_open_uq", "provider_fee_accruals_live_uq", "provider_fee_invoices_reversal_uq",
           "provider_fee_invoices_journal_uq", "models_provided_live_uq")
CHECKS = {
    "portfolio.model_providers": ("model_providers_status_chk", "model_providers_sec_chk", "model_providers_crd_chk",
                                  "model_providers_registered_chk", "model_providers_adv_key_chk"),
    "portfolio.model_publishing_gates": ("model_publishing_gates_open_chk", "model_publishing_gates_reason_chk"),
    "portfolio.catalog_versions": ("catalog_versions_name_chk", "catalog_versions_description_chk",
                                   "catalog_versions_maker_checker_chk", "catalog_versions_published_chk",
                                   "catalog_versions_fee_chk"),
    "portfolio.provider_subscriptions": ("provider_subscriptions_maker_checker_chk",
                                         "provider_subscriptions_active_chk", "provider_subscriptions_orgs_chk"),
    "portfolio.provider_fee_accruals": ("provider_fee_accruals_formula_chk",),
    "portfolio.provider_fee_invoice_lines": ("provider_fee_invoice_lines_label_chk",),
    "portfolio.models": ("models_source_chk", "models_reserved_name_chk", "models_provided_chk"),
    "public.disclosure_acknowledgements": ("disclosure_ack_subject_chk",),
}
FUNCTIONS = ("model_provider_org_allowed", "provider_tenant_allowed", "model_provider_complete",
             "model_publishing_open", "catalog_visible", "provider_subscription_status", "provider_model_usage",
             "catalog_model_provider")


async def _conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
    return conn


async def apply_schema() -> None:
    conn = await _conn()
    try:
        async with conn.transaction():
            await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        print("[OK] schema applied")
    finally:
        await conn.close()


async def apply_fix1() -> None:
    conn = await _conn()
    try:
        async with conn.transaction():
            await conn.execute(FIX1_SQL.read_text(encoding="utf-8"))
        print("[OK] rep15 fix 1 applied (portfolio.provided_model_guard)")
    finally:
        await conn.close()


async def verify_schema() -> int:
    conn = await _conn()
    gaps: list[str] = []

    def need(ok: bool, label: str, detail: str = "") -> None:
        print(f"{'[OK]  ' if ok else '[GAP] '}{label}" + (f" — {detail}" if detail else ""))
        if not ok:
            gaps.append(label)

    try:
        for full in NEW_TABLES:
            schema, t = full.split(".")
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = $1 AND table_name = $2",
                schema, t)]
            need(bool(cols), f"{full} exists", f"{len(cols)} columns")
            need(all(c in cols for c in ("valid_from", "valid_to", "system_from", "system_to")),
                 f"{full} carries both temporal axes")
            need(await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", full) is True,
                 f"{full} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = $1 "
                                   "AND tablename = $2", schema, t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"{full}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            need(all("NULLIF(current_setting('app.is_super_admin'" in ((r["qual"] or "") + (r["with_check"] or ""))
                     for r in pol), f"{full}: every policy names the super-admin GUC with NULLIF")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = $1 "
                "AND table_name = $2 AND grantee = 'app_service'", schema, t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on {full}")
        for table, trg in GUARDS.items():
            n = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgrelid = $1::regclass AND tgname = $2 "
                                    "AND NOT tgisinternal", table, trg)
            need(n == 1, f"trigger {trg} on {table}")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(UNIQUES))}
        for name in UNIQUES:
            need("UNIQUE" in idx.get(name, ""), f"unique index {name}", idx.get(name, "")[:120])
        for table, names in CHECKS.items():
            have = {r["conname"] for r in await conn.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid = $1::regclass AND contype = 'c'", table)}
            for n in names:
                need(n in have, f"constraint {table}.{n}")
        fns = {r["proname"]: r["prosecdef"] for r in await conn.fetch(
            "SELECT p.proname, p.prosecdef FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'portfolio' AND p.proname = ANY($1::text[])", list(FUNCTIONS))}
        for f in FUNCTIONS:
            need(fns.get(f) is True, f"SECURITY DEFINER portfolio.{f}")
        cyc = await conn.fetchval(
            "SELECT count(*) FROM pg_constraint WHERE conrelid = 'portfolio.models'::regclass AND contype = 'f' "
            "AND conname IN ('models_provided_subscription_id_fkey', 'models_copied_from_catalog_version_id_fkey')")
        need(cyc == 0, "no FK cycle: models' two provided references are trigger-checked, not foreign keys")
        guard = await conn.fetchval("SELECT pg_get_functiondef('public.fn_guard_posted_lines'::regproc)")
        need("ledger.teardown" in guard and "is_test" in guard and "Cannot modify lines of posted entry" in guard,
             "fn_guard_posted_lines: unchanged for real orgs, fixture teardown path for is_test orgs only")
        pmg = await conn.fetchval("SELECT pg_get_functiondef('portfolio.provided_model_guard'::regproc)")
        need("parent_version_id ELSE NEW.version_id" not in pmg and "ELSIF TG_TABLE_NAME = 'model_sleeves'" in pmg,
             "provided_model_guard reads only the firing table's own fields (rep15 fix 1 — no 42703 on positions)")
        need(await conn.fetchval("SELECT portfolio.model_publishing_open($1::uuid)", PLATFORM_ORG_ID) is False,
             "the REAL platform org's publishing gate is OFF")
        for full in NEW_TABLES:
            print(f"[INFO] {full}: {await conn.fetchval(f'SELECT count(*) FROM {full}')} row(s)")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


def main() -> int:
    args = sys.argv[1:]
    if "--all" in args:
        asyncio.run(apply_schema())
        return asyncio.run(verify_schema())
    if "--fix1" in args:
        asyncio.run(apply_fix1())
        return asyncio.run(verify_schema())
    if "--schema" in args:
        asyncio.run(apply_schema())
        return 0
    if "--verify" in args:
        return asyncio.run(verify_schema())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
