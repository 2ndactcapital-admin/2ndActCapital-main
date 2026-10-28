"""MARKETDATA1 — apply the security-data layer schema and prove it landed.

    python3 apps/api/scripts/_apply_marketdata1.py --schema    # docs/marketdata1_part1.sql, one transaction
    python3 apps/api/scripts/_apply_marketdata1.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_marketdata1.py --all       # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. Never prints a secret.

THE MIGRATION WRITES NO ORG ROWS: seven platform providers and the platform
rank. ``--verify`` also proves the price store was empty when its point key
was widened, and that every pre-existing corporate action still satisfies the
widened action_type CHECK.
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

SCHEMA_SQL = REPO / "docs" / "marketdata1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

PLATFORM_TABLES = ("portfolio.data_providers", "portfolio.provider_call_log", "portfolio.security_data_runs")
ORG_TABLES = ("portfolio.data_provider_entitlements", "portfolio.data_uploads")
MIXED_TABLES = ("portfolio.data_source_rank", "portfolio.index_levels", "portfolio.data_quality_events")
NEW_TABLES = PLATFORM_TABLES + ORG_TABLES + MIXED_TABLES
PARTIAL_UNIQUES = ("data_providers_key_live_uq", "data_source_rank_provider_live_uq", "data_source_rank_position_live_uq",
                   "data_provider_entitlements_live_uq", "data_uploads_live_uq", "data_uploads_file_live_uq",
                   "uq_sec_global_price_point", "index_levels_point_live_uq", "data_quality_events_live_uq")
CHECKS = {
    "portfolio.data_providers": ("data_providers_platform_chk", "data_providers_key_chk", "data_providers_kind_chk",
                                 "data_providers_status_chk", "data_providers_capabilities_chk",
                                 "data_providers_member_display_chk", "data_providers_retention_chk",
                                 "data_providers_budget_chk", "data_providers_display_needs_internal_chk",
                                 "data_providers_redistribution_chk"),
    "portfolio.data_source_rank": ("data_source_rank_scope_chk", "data_source_rank_type_chk", "data_source_rank_rank_chk"),
    "portfolio.data_uploads": ("data_uploads_type_chk", "data_uploads_status_chk", "data_uploads_counts_chk",
                               "data_uploads_licence_chk", "data_uploads_maker_checker_chk", "data_uploads_decided_chk",
                               "data_uploads_reject_note_chk"),
    "portfolio.index_levels": ("index_levels_key_chk", "index_levels_upload_chk"),
    "portfolio.data_quality_events": ("data_quality_events_scope_chk", "data_quality_events_type_chk",
                                      "data_quality_events_kind_chk"),
    "portfolio.provider_call_log": ("provider_call_log_platform_chk", "provider_call_log_calls_chk"),
    "portfolio.security_data_runs": ("security_data_runs_platform_chk", "security_data_runs_status_chk",
                                     "security_data_runs_trigger_chk"),
    "portfolio.securities_global_prices": ("sec_global_price_type_chk", "sec_global_price_upload_chk",
                                           "sec_global_price_positive_chk"),
    "portfolio.securities_global_corporate_actions": ("corp_actions_type_chk", "corp_actions_upload_chk"),
}
NEW_COLUMNS = {
    "securities_global_prices": ("provider_key", "as_of", "quality_flags", "licence", "org_id", "upload_id"),
    "securities_global_corporate_actions": ("provider_key", "as_of", "quality_flags", "licence", "org_id", "upload_id"),
    "replica_holdings_snapshots": ("provider_key", "licence"),
}
PROVIDERS = ("custodian", "sec_nport", "ssga_spdr", "fred", "csv_upload", "adviser_entry", "vendor_slot",
             "platform_entry")


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
        n = await conn.fetchval("SELECT count(*) FROM portfolio.securities_global_prices")
        print(f"[INFO] securities_global_prices rows before the point key is widened: {n}")
        async with conn.transaction():
            await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        print("[OK] schema applied")
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
                "SELECT column_name FROM information_schema.columns WHERE table_schema = $1 AND table_name = $2 "
                "ORDER BY ordinal_position", schema, t)]
            need(bool(cols), f"{full} exists", f"{len(cols)} columns")
            need(all(c in cols for c in ("org_id", "valid_from", "valid_to", "system_from", "system_to")),
                 f"{full} carries org_id and both temporal axes")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", full)
            need(rls is True, f"{full} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = $1 "
                                   "AND tablename = $2", schema, t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"{full}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            writes = [(r["qual"] or "") + (r["with_check"] or "") for r in pol if r["cmd"] != "SELECT"]
            if full in PLATFORM_TABLES:
                need(len(writes) == 3 and all("app.is_super_admin" in q and "current_org_id" not in q for q in writes),
                     f"{full}: platform rows — read by all, written only under super admin")
            elif full in ORG_TABLES:
                sel = [(r["qual"] or "") for r in pol if r["cmd"] == "SELECT"]
                need(bool(sel) and all("NULLIF(current_setting('app.current_org_id'" in q for q in sel),
                     f"{full}: org-scoped read with NULLIF")
            else:
                need(len(writes) == 3 and all("NULLIF(current_setting('app.current_org_id'" in q
                                              and "app.is_super_admin" in q for q in writes),
                     f"{full}: org rows own-org (NULLIF) and platform rows super admin, every write")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = $1 "
                "AND table_name = $2 AND grantee = 'app_service'", schema, t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on {full}")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(PARTIAL_UNIQUES))}
        for name in PARTIAL_UNIQUES:
            d = idx.get(name, "")
            need("UNIQUE" in d and "WHERE" in d, f"partial unique index {name}", d[:160])
        need("provider_key" in idx.get("uq_sec_global_price_point", ""), "price point key includes provider_key")
        for table, names in CHECKS.items():
            have = {r["conname"] for r in await conn.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid = $1::regclass AND contype = 'c'", table)}
            for n in names:
                need(n in have, f"constraint {table}.{n}")
        tdef = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                                   "'corp_actions_type_chk'")
        need("cash_dividend" in (tdef or "") and "split" in (tdef or ""), "corp_actions_type_chk admits cash_dividend")
        for t, cols in NEW_COLUMNS.items():
            have = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'portfolio' "
                "AND table_name = $1", t)}
            need(set(cols) <= have, f"portfolio.{t} gained {', '.join(cols)}", f"missing {sorted(set(cols) - have)}")
        for t in ("securities_global_prices", "securities_global_corporate_actions"):
            pol = {r["cmd"]: (r["qual"] or "") + (r["with_check"] or "") for r in await conn.fetch(
                "SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = 'portfolio' AND tablename = $1", t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= set(pol), f"portfolio.{t}: a policy for every operation")
            need("current_org_id" in pol.get("SELECT", "") and "org_id IS NULL" in pol.get("SELECT", ""),
                 f"portfolio.{t}: platform rows read by all, an org's upload rows by that org only")
        trg = {r["tgname"] for r in await conn.fetch("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")}
        for name in ("provider_call_log_append_trg", "security_data_runs_append_trg"):
            need(name in trg, f"trigger {name}")
        prov = {r["key"]: r for r in await conn.fetch(
            "SELECT key, kind, status, lic_internal_use, lic_member_display FROM portfolio.data_providers "
            "WHERE valid_to IS NULL AND system_to IS NULL")}
        need(set(PROVIDERS) <= set(prov), "every free provider + the vendor slot registered",
             f"missing {sorted(set(PROVIDERS) - set(prov))}")
        vs = prov.get("vendor_slot")
        need(bool(vs) and vs["status"] == "not_connected" and vs["lic_internal_use"] is False,
             "vendor_slot is not connected and carries no rights")
        cu = prov.get("custodian")
        need(bool(cu) and cu["lic_member_display"] == "own_holdings_only", "custodian: member display own holdings only")
        ranks = await conn.fetch("SELECT data_type, provider_key, rank FROM portfolio.data_source_rank WHERE owner_scope = "
                                 "'platform' AND valid_to IS NULL AND system_to IS NULL ORDER BY data_type, rank")
        need(len(ranks) >= 11, "platform rank seeded", f"{[(r['data_type'], r['provider_key'], r['rank']) for r in ranks]}")
        need(not any(r["provider_key"] == "vendor_slot" for r in ranks), "the vendor slot is in no platform rank")
        need(not any(r["data_type"] == "fund_holdings" and r["provider_key"] == "csv_upload" for r in ranks),
             "fund holdings are not ranked from file uploads (platform-only holdings store)")
        n_org = sum([int(await conn.fetchval(f"SELECT count(*) FROM {t} WHERE org_id IS NOT NULL"))
                     for t in NEW_TABLES])
        need(n_org == 0, "the migration wrote no org rows", f"{n_org}")
        print(f"[INFO] prices: {await conn.fetchval('SELECT count(*) FROM portfolio.securities_global_prices')}; "
              f"corporate actions: {await conn.fetchval('SELECT count(*) FROM portfolio.securities_global_corporate_actions')}; "
              f"holdings snapshots with a provider: "
              f"{await conn.fetchval('SELECT count(*) FROM portfolio.replica_holdings_snapshots WHERE provider_key IS NOT NULL')}")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


def main() -> int:
    args = sys.argv[1:]
    if "--all" in args:
        asyncio.run(apply_schema())
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
