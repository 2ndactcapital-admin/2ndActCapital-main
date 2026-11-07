"""REP13 — apply the long/short schema and prove it landed.

    python3 apps/api/scripts/_apply_rep13.py --schema    # docs/rep13_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep13.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_rep13.py --all       # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table
owner). Idempotent. Never prints a secret.

THE ONLY ROWS THE MIGRATION WRITES: the platform capability records of stock_borrow / margin_initial_pct /
margin_maintenance_pct for 'altruist' and 'manual', each 'unknown' (not declared). --verify proves every live
capability row of margin / short_selling / stock_borrow is still NOT available (no custodian declares shorting).
Live counts are printed, never asserted against a frozen number.
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

SCHEMA_SQL = REPO / "docs" / "rep13_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

ORG_TABLES = ("public.suitability_records", "public.long_short_settings", "public.long_short_proposals")
NEW_TABLES = ORG_TABLES + ("public.borrow_rates",)
PARTIAL_UNIQUES = ("suitability_records_live_uq", "long_short_settings_live_uq", "long_short_settings_version_uq",
                   "borrow_rates_live_uq", "long_short_proposals_live_uq")
CHECKS = {
    "public.custodian_capabilities": ("custodian_capabilities_key_chk", "custodian_capabilities_value_chk"),
    "public.suitability_records": ("suitability_records_subject_chk", "suitability_records_kind_chk",
                                   "suitability_records_status_chk", "suitability_records_pending_chk",
                                   "suitability_records_approved_chk", "suitability_records_maker_checker_chk",
                                   "suitability_records_rejected_chk", "suitability_records_removed_chk",
                                   "suitability_records_archive_chk"),
    "portfolio.models": ("models_kind_chk", "models_long_short_chk", "models_reserved_name_chk",
                         "models_long_short_name_chk"),
    "portfolio.model_version_positions": ("model_version_positions_weight_chk", "model_version_positions_band_chk"),
    "public.long_short_settings": ("long_short_settings_status_chk", "long_short_settings_saved_chk",
                                   "long_short_settings_extension_chk", "long_short_settings_te_chk",
                                   "long_short_settings_max_short_chk", "long_short_settings_ceiling_chk",
                                   "long_short_settings_exclusions_chk"),
    "public.borrow_rates": ("borrow_rates_scope_chk", "borrow_rates_rate_chk", "borrow_rates_rebate_chk",
                            "borrow_rates_source_chk"),
    "public.long_short_proposals": ("long_short_proposals_json_chk", "long_short_proposals_ack_chk",
                                    "long_short_proposals_summary_chk"),
    "public.rebalance_proposal_trades": ("rebalance_proposal_trades_side_chk",
                                         "rebalance_proposal_trades_short_leg_chk"),
    "public.execution_instructions": ("execution_instructions_side_chk", "execution_instructions_short_terms_chk"),
    "public.trade_blocks": ("trade_blocks_side_chk",),
}
GUARDS = {"public.suitability_records": "suitability_records_guard_trg",
          "public.long_short_settings": "long_short_settings_guard_trg",
          "public.borrow_rates": "borrow_rates_guard_trg",
          "public.long_short_proposals": "long_short_proposals_guard_trg",
          "portfolio.model_version_positions": "model_version_positions_short_guard_trg"}


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
            need(all(c in cols for c in ("valid_from", "valid_to", "system_from", "system_to")),
                 f"{full} carries both temporal axes")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", full)
            need(rls is True, f"{full} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = $1 "
                                   "AND tablename = $2", schema, t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"{full}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            quals = [(r["qual"] or "") + (r["with_check"] or "") for r in pol]
            if full in ORG_TABLES:
                need(len(quals) == 4 and all("NULLIF(current_setting('app.current_org_id'" in q for q in quals),
                     f"{full}: org isolation with NULLIF on every operation")
            else:
                need(all("NULLIF(current_setting('app.is_super_admin'" in q for q in quals if q != ""),
                     f"{full}: every policy names the super-admin GUC with NULLIF")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = $1 "
                "AND table_name = $2 AND grantee = 'app_service'", schema, t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on {full}")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])", list(PARTIAL_UNIQUES))}
        for name in PARTIAL_UNIQUES:
            d = idx.get(name, "")
            need("UNIQUE" in d and "WHERE" in d, f"partial unique index {name}", d[:140])
        for table, names in CHECKS.items():
            have = {r["conname"] for r in await conn.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid = $1::regclass AND contype = 'c'", table)}
            for n in names:
                need(n in have, f"constraint {table}.{n}")
        for table in ("portfolio.models", "public.rebalance_proposal_trades", "public.execution_instructions"):
            for n in [r["conname"] for r in await conn.fetch(
                    "SELECT conname FROM pg_constraint WHERE conrelid = $1::regclass AND contype = 'c' "
                    "AND NOT convalidated", table)]:
                need(False, f"{table}.{n} is VALIDATED over every existing row")
        trg = {(r["rel"], r["tgname"]) for r in await conn.fetch(
            "SELECT n.nspname || '.' || c.relname AS rel, t.tgname FROM pg_trigger t JOIN pg_class c "
            "ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE NOT t.tgisinternal")}
        for rel, name in GUARDS.items():
            need((rel, name) in trg, f"trigger {name} on {rel}")
        for col, table in (("kind", "models"), ("gross_long", "models"), ("gross_short", "models"),
                           ("signal_source", "models"), ("signal_as_of", "models"), ("short_exclusions", "models")):
            have = await conn.fetchval("SELECT 1 FROM information_schema.columns WHERE table_schema = 'portfolio' "
                                       "AND table_name = $1 AND column_name = $2", table, col)
            need(have == 1, f"portfolio.{table}.{col}")
        for table, col in (("rebalance_proposal_trades", "short_leg"), ("execution_instructions", "short_terms")):
            have = await conn.fetchval("SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                                       "AND table_name = $1 AND column_name = $2", table, col)
            need(have == 1, f"public.{table}.{col}")
        caps = await conn.fetch(
            "SELECT custodian_code, capability_key, value FROM public.custodian_capabilities WHERE valid_to IS NULL "
            "AND system_to IS NULL AND capability_key IN ('margin', 'short_selling', 'stock_borrow', "
            "'margin_initial_pct', 'margin_maintenance_pct') ORDER BY 1, 2")
        for c in ("altruist", "manual"):
            got = {r["capability_key"] for r in caps if r["custodian_code"] == c}
            need({"stock_borrow", "margin_initial_pct", "margin_maintenance_pct"} <= got,
                 f"{c}: the three new capability keys are recorded", f"{sorted(got)}")
        declared = [dict(r) for r in caps if r["custodian_code"] in ("altruist", "manual")
                    and r["capability_key"] in ("margin", "short_selling", "stock_borrow") and r["value"] == "yes"]
        need(not declared, "no stored custodian declares margin / short selling / stock borrow", f"{declared}")
        n_ls = await conn.fetchval("SELECT count(*) FROM portfolio.models WHERE kind = 'long_short'")
        print(f"[INFO] long/short models: {n_ls}"
              f"; borrow rates: {await conn.fetchval('SELECT count(*) FROM public.borrow_rates')}"
              f"; suitability records: {await conn.fetchval('SELECT count(*) FROM public.suitability_records')}")
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
