"""REP8 — apply the options-sleeve schema and prove it landed.

    python3 apps/api/scripts/_apply_rep8.py --schema    # docs/rep8_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep8.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_rep8.py --all       # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. Never prints a secret.

THE MIGRATION WRITES NO ORG ROWS: two platform instrument classes and the
buffer / income terms fields only. ``--verify`` also proves every existing
proposal / trade / instruction still satisfies the widened CHECKs (they were
re-added without NOT VALID, so the ALTER itself would have failed otherwise).
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

SCHEMA_SQL = REPO / "docs" / "rep8_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

PLATFORM_TABLES = ("portfolio.option_contracts",)
ORG_TABLES = ("portfolio.adviser_quotes", "portfolio.option_packages", "portfolio.option_package_legs",
              "public.options_tax_gate_results", "public.option_settlement_entries")
NEW_TABLES = PLATFORM_TABLES + ORG_TABLES
PARTIAL_UNIQUES = ("option_contracts_terms_uq", "option_contracts_security_uq", "option_packages_live_approved_uq",
                   "option_packages_open_draft_uq", "option_packages_no_uq", "option_packages_roll_uq",
                   "option_package_legs_live_uq", "option_settlement_entries_live_uq")
CHECKS = {
    "portfolio.option_contracts": ("option_contracts_platform_chk", "option_contracts_underlying_kind_chk",
                                   "option_contracts_type_chk", "option_contracts_style_chk",
                                   "option_contracts_settlement_chk", "option_contracts_strike_chk",
                                   "option_contracts_multiplier_chk", "option_contracts_occ_chk",
                                   "option_contracts_broad_chk", "option_contracts_cash_index_chk",
                                   "option_contracts_mini_chk"),
    "portfolio.adviser_quotes": ("adviser_quotes_kind_chk", "adviser_quotes_value_chk", "adviser_quotes_source_chk",
                                 "adviser_quotes_as_of_chk"),
    "portfolio.option_packages": ("option_packages_kind_chk", "option_packages_core_chk", "option_packages_status_chk",
                                  "option_packages_period_chk", "option_packages_maker_checker_chk",
                                  "option_packages_income_terms_chk", "option_packages_approved_priced_chk",
                                  "option_packages_rejected_chk", "option_packages_reason_chk"),
    "portfolio.option_package_legs": ("option_package_legs_role_chk", "option_package_legs_shape_chk",
                                      "option_package_legs_option_chk", "option_package_legs_ratio_chk"),
    "public.options_tax_gate_results": ("options_tax_gate_results_rule_chk", "options_tax_gate_results_outcome_chk",
                                        "options_tax_gate_results_reason_chk", "options_tax_gate_results_role_chk"),
    "public.option_settlement_entries": ("option_settlement_entries_status_chk",
                                         "option_settlement_entries_confirmed_chk"),
    "public.rebalance_proposals": ("rebalance_proposals_kind_chk",),
    "public.rebalance_proposal_trades": ("rebalance_proposal_trades_option_leg_chk",
                                         "rebalance_proposal_trades_price_chk"),
    "public.execution_instructions": ("execution_instructions_option_terms_chk",),
    "portfolio.securities_global": ("securities_global_type_chk",),
}
GUARDS = {"portfolio.option_contracts": "option_contracts_guard_trg",
          "portfolio.adviser_quotes": "adviser_quotes_guard_trg",
          "portfolio.option_packages": "option_packages_guard_trg",
          "portfolio.option_package_legs": "option_package_legs_guard_trg",
          "public.options_tax_gate_results": "options_tax_gate_results_guard_trg",
          "public.option_settlement_entries": "option_settlement_entries_guard_trg"}
NEW_FIELDS = ("underlying_reference", "outcome_period_start", "outcome_period_end", "cap_pct", "buffer_pct",
              "floor_pct", "leg_long_put_strike_pct", "leg_short_put_strike_pct", "leg_short_call_strike_pct",
              "option_exercise_style", "call_strike_rule", "call_strike_pct_otm", "call_delta", "call_tenor_days",
              "call_coverage_pct")


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
            need(all(c in cols for c in ("org_id", "valid_from", "valid_to", "system_from", "system_to")),
                 f"{full} carries org_id and both temporal axes")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", full)
            need(rls is True, f"{full} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = $1 "
                                   "AND tablename = $2", schema, t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"{full}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            if full in ORG_TABLES:
                quals = [(r["qual"] or "") + (r["with_check"] or "") for r in pol]
                need(len(quals) == 4 and all("NULLIF(current_setting('app.current_org_id'" in q for q in quals),
                     f"{full}: org isolation with NULLIF on every operation")
            else:
                writes = [(r["qual"] or "") + (r["with_check"] or "") for r in pol if r["cmd"] != "SELECT"]
                need(len(writes) == 3 and all("app.is_super_admin" in q for q in writes),
                     f"{full}: platform rows — read by all, written only under super admin")
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
        tdef = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                                   "'securities_global_type_chk'")
        need("'option'" in (tdef or ""), "securities_global.security_type admits 'option'")
        trg = {(r["rel"], r["tgname"]) for r in await conn.fetch(
            "SELECT n.nspname || '.' || c.relname AS rel, t.tgname FROM pg_trigger t JOIN pg_class c "
            "ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE NOT t.tgisinternal")}
        for rel, name in GUARDS.items():
            need((rel, name) in trg, f"trigger {name} on {rel}")
        for col, table in (("option_leg", "rebalance_proposal_trades"), ("option_terms", "execution_instructions")):
            need(bool(await conn.fetchval("SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
                                          "AND table_name = $1 AND column_name = $2", table, col)),
                 f"public.{table}.{col} exists")
        need(bool(await conn.fetchval("SELECT 1 FROM information_schema.columns WHERE table_schema = 'portfolio' AND "
                                      "table_name = 'replica_terms_field_registry' AND column_name = "
                                      "'instrument_classes'")), "replica_terms_field_registry.instrument_classes exists")
        classes = {r["key"]: r["enabled"] for r in await conn.fetch(
            "SELECT key, enabled FROM portfolio.replica_instrument_classes WHERE key = ANY($1::text[]) "
            "AND valid_to IS NULL AND system_to IS NULL", ["buffer_etf", "income_etf"])}
        need(classes == {"buffer_etf": True, "income_etf": True}, "instrument classes buffer_etf / income_etf enabled",
             f"{classes}")
        fields = {r["field_key"]: list(r["instrument_classes"] or []) for r in await conn.fetch(
            "SELECT field_key, instrument_classes FROM portfolio.replica_terms_field_registry WHERE field_key = "
            "ANY($1::text[]) AND valid_to IS NULL AND system_to IS NULL", list(NEW_FIELDS))}
        need(set(fields) == set(NEW_FIELDS) and all(fields.values()),
             "every buffer / income terms field registered and class-scoped", f"{sorted(set(NEW_FIELDS) - set(fields))}")
        legacy = await conn.fetchval("SELECT count(*) FROM portfolio.replica_terms_field_registry WHERE "
                                     "instrument_classes IS NOT NULL AND NOT (field_key = ANY($1::text[]))",
                                     list(NEW_FIELDS))
        need(int(legacy) == 0, "every pre-rep8 field stays class-agnostic (NULL = every class)")
        n_opt = await conn.fetchval("SELECT count(*) FROM public.rebalance_proposals WHERE proposal_kind = 'options'")
        print(f"[INFO] options proposals on record: {n_opt}; option contracts: "
              f"{await conn.fetchval('SELECT count(*) FROM portfolio.option_contracts')}; quotes: "
              f"{await conn.fetchval('SELECT count(*) FROM portfolio.adviser_quotes')}")
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
