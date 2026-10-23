"""REP4 — apply the fund-build schema, then prove it landed.

    python3 apps/api/scripts/_apply_rep4.py --schema   # docs/rep4_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep4.py --verify   # read it back from the catalog

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. ``--verify`` reads columns, CHECKs, partial
unique indexes, RLS flags, every policy, triggers and grants back — plus the
rep3 vocabulary CHECKs this sprint widened — and exits non-zero on any gap.
Never prints a secret.
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
sys.path.insert(0, str(API_DIR))

SCHEMA_SQL = REPO / "docs" / "rep4_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

TABLES = ("replica_model_specs", "replica_registry_keys", "replica_viability_reports")
PARTIAL_UNIQUES = ("replica_model_specs_version_uq", "replica_registry_keys_live_uq", "replica_registry_keys_model_uq",
                   "model_events_holdings_monitor_uq", "model_events_terms_monitor_uq")
CHECKS = ("replica_model_specs_method_chk", "replica_model_specs_min_weight_chk", "replica_model_specs_cash_chk",
          "replica_model_specs_policy_chk", "replica_model_specs_thresholds_chk", "replica_model_specs_origin_chk",
          "replica_model_specs_human_chk", "replica_model_specs_custodian_chk", "replica_registry_keys_method_chk",
          "replica_registry_keys_custodian_chk", "replica_viability_reports_answers_chk",
          "replica_viability_reports_overall_chk", "replica_viability_reports_size_chk",
          "replica_viability_reports_method_chk", "models_disclosure_chk")
GUARDS = ("replica_model_specs_immutable_trg", "replica_viability_reports_immutable_trg",
          "replica_registry_keys_guard_trg")
# rep3 CHECKs widened here: the values that must now be in each definition
WIDENED = {"model_versions_origin_chk": ("fund_build", "fund_monitor", "composite_follow"),
           "model_events_action_chk": ("fund_build_joined", "holdings_monitor", "terms_monitor", "terms_review_done",
                                       "follow_skipped"),
           "model_events_human_chk": ("holdings_monitor", "terms_monitor", "subscription_draft")}


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
        for t in TABLES:
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'portfolio' "
                "AND table_name = $1 ORDER BY ordinal_position", t)]
            need(bool(cols), f"portfolio.{t} exists", f"{len(cols)} columns")
            need(all(c in cols for c in ("org_id", "valid_from", "valid_to", "system_from", "system_to")),
                 f"portfolio.{t} is org-scoped and bi-temporal")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"portfolio.{t}")
            need(rls is True, f"portfolio.{t} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = 'portfolio' "
                                   "AND tablename = $1", t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"portfolio.{t}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            quals = [(r["qual"] or "") + (r["with_check"] or "") for r in pol]
            need(len(quals) == 4 and all("NULLIF(current_setting('app.current_org_id'" in q for q in quals),
                 f"portfolio.{t}: org isolation with NULLIF on every operation")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = 'portfolio' "
                "AND table_name = $1 AND grantee = 'app_service'", t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on portfolio.{t}")
        mcols = [r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'portfolio' "
            "AND table_name = 'models'")]
        need("disclosure" in mcols, "portfolio.models.disclosure exists")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'portfolio' AND indexname = ANY($1::text[])",
            list(PARTIAL_UNIQUES))}
        for name in PARTIAL_UNIQUES:
            d = idx.get(name, "")
            need("UNIQUE" in d and "WHERE" in d, f"partial unique index {name}", d[:160])
        cons = {r["conname"]: r["d"] for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE connamespace = 'portfolio'::regnamespace")}
        for name in CHECKS:
            need(name in cons, f"constraint {name}")
        for name, values in WIDENED.items():
            d = cons.get(name, "")
            need(all(f"'{v}'" in d for v in values), f"{name} carries {', '.join(values)}")
        fks = {r["c"] for r in await conn.fetch(
            "SELECT conrelid::regclass::text || '→' || confrelid::regclass::text AS c FROM pg_constraint "
            "WHERE contype = 'f' AND conrelid::regclass::text = ANY($1::text[])", [f"portfolio.{t}" for t in TABLES])}
        for fk in ("portfolio.replica_model_specs→portfolio.model_versions",
                   "portfolio.replica_model_specs→portfolio.replica_holdings_snapshots",
                   "portfolio.replica_model_specs→portfolio.replica_terms_versions",
                   "portfolio.replica_registry_keys→portfolio.models",
                   "portfolio.replica_viability_reports→portfolio.replica_securities"):
            need(fk in fks, f"FK {fk}")
        trg = {r["tgname"] for r in await conn.fetch(
            "SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND c.relnamespace = 'portfolio'::regnamespace")}
        for name in GUARDS:
            need(name in trg, f"trigger {name}")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


def main() -> int:
    args = sys.argv[1:]
    if "--schema" in args:
        asyncio.run(apply_schema())
        return 0
    if "--verify" in args:
        return asyncio.run(verify_schema())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
