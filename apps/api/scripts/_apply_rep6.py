"""REP6 — apply the tax-engine schema, then prove it landed.

    python3 apps/api/scripts/_apply_rep6.py --schema   # docs/rep6_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep6.py --verify   # read it back from the catalog

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. ``--verify`` reads columns, CHECKs, partial
unique indexes, RLS flags, every policy, triggers, grants and the permission
rows back — plus the rep5 changes (the harvest trigger, wash-sale results on
trades, the harvest settings) — and exits non-zero on any gap. Never prints a secret.
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

SCHEMA_SQL = REPO / "docs" / "rep6_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

TABLES = ("tax_profiles", "loss_carryforwards", "substitution_sets", "substitution_set_candidates",
          "substitution_denials", "declared_outside_trades", "loss_bank_entries", "harvest_runs", "harvest_candidates")
PARTIAL_UNIQUES = ("tax_profiles_approved_uq", "tax_profiles_pending_uq", "tax_profiles_version_uq", "tax_profiles_task_uq",
                   "loss_carryforwards_approved_uq", "loss_carryforwards_pending_uq", "loss_carryforwards_version_uq",
                   "loss_carryforwards_task_uq", "substitution_sets_approved_uq", "substitution_sets_pending_uq",
                   "substitution_sets_version_uq", "substitution_sets_task_uq", "substitution_set_candidates_rank_uq",
                   "substitution_set_candidates_security_uq", "substitution_denials_live_uq",
                   "substitution_denials_task_uq", "loss_bank_entries_source_uq", "harvest_runs_day_uq")
CHECKS = ("tax_profiles_subject_chk", "tax_profiles_filing_chk", "tax_profiles_rates_chk", "tax_profiles_source_chk",
          "tax_profiles_status_chk", "tax_profiles_maker_checker_chk", "tax_profiles_decided_chk",
          "tax_profiles_rejected_chk", "loss_carryforwards_amount_chk", "loss_carryforwards_source_chk",
          "loss_carryforwards_maker_checker_chk", "substitution_sets_maker_checker_chk",
          "substitution_set_candidates_text_chk", "substitution_set_candidates_source_chk",
          "substitution_denials_pair_chk", "substitution_denials_rule_chk", "substitution_denials_maker_chk",
          "substitution_denials_maker_checker_chk", "substitution_denials_user_active_chk",
          "declared_outside_trades_side_chk", "declared_outside_trades_loss_chk", "declared_outside_trades_amount_chk",
          "loss_bank_entries_character_chk", "loss_bank_entries_source_chk", "loss_bank_entries_cf_sign_chk",
          "harvest_runs_outcome_chk", "harvest_runs_proposed_chk", "harvest_runs_custodian_chk",
          "harvest_candidates_outcome_chk", "harvest_candidates_skip_chk", "harvest_candidates_reason_chk",
          "harvest_candidates_value_chk", "rebalance_proposals_trigger_chk", "rebalance_proposal_trades_wash_chk",
          "rebalance_proposal_trades_wash_route_chk", "account_model_links_harvest_chk")
GUARDS = {"tax_profiles": "tax_profiles_guard_trg", "loss_carryforwards": "loss_carryforwards_guard_trg",
          "substitution_sets": "substitution_sets_guard_trg",
          "substitution_set_candidates": "substitution_set_candidates_guard_trg",
          "substitution_denials": "substitution_denials_guard_trg",
          "declared_outside_trades": "declared_outside_trades_guard_trg",
          "loss_bank_entries": "loss_bank_entries_guard_trg", "harvest_runs": "harvest_runs_guard_trg",
          "harvest_candidates": "harvest_candidates_guard_trg"}
PERMISSIONS = ("view_tax", "manage_tax", "approve_tax")
NEW_COLUMNS = {"rebalance_proposal_trades": ("wash_sale",),
               "account_model_links": ("harvest_loss_pct", "harvest_loss_min", "harvest_to_cash", "harvest_swap_back")}


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
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = $1 ORDER BY ordinal_position", t)]
            need(bool(cols), f"public.{t} exists", f"{len(cols)} columns")
            need(all(c in cols for c in ("org_id", "valid_from", "valid_to", "system_from", "system_to")),
                 f"public.{t} is org-scoped and bi-temporal")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"public.{t}")
            need(rls is True, f"public.{t} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = 'public' "
                                   "AND tablename = $1", t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"public.{t}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            quals = [(r["qual"] or "") + (r["with_check"] or "") for r in pol]
            need(len(quals) == 4 and all("NULLIF(current_setting('app.current_org_id'" in q for q in quals),
                 f"public.{t}: org isolation with NULLIF on every operation")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = 'public' "
                "AND table_name = $1 AND grantee = 'app_service'", t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on public.{t}")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY($1::text[])",
            list(PARTIAL_UNIQUES))}
        for name in PARTIAL_UNIQUES:
            d = idx.get(name, "")
            need("UNIQUE" in d and "WHERE" in d, f"partial unique index {name}", d[:160])
        cons = {r["conname"] for r in await conn.fetch(
            "SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace")}
        for name in CHECKS:
            need(name in cons, f"constraint {name}")
        trg = {(r["rel"], r["tgname"]) for r in await conn.fetch(
            "SELECT c.relname AS rel, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace")}
        for rel, name in GUARDS.items():
            need((rel, name) in trg, f"trigger {name} on {rel}")
        trig = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                   "WHERE conname = 'rebalance_proposals_trigger_chk'")
        need("'harvest'" in (trig or ""), "rebalance_proposals.trigger admits 'harvest'", (trig or "")[:120])
        for t, cols in NEW_COLUMNS.items():
            have = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = $1",
                t)}
            need(set(cols) <= have, f"public.{t} gains {', '.join(cols)}")
        perms = {r["name"] for r in await conn.fetch("SELECT name FROM permissions WHERE name = ANY($1::text[])",
                                                     list(PERMISSIONS))}
        need(perms == set(PERMISSIONS), "permissions view/manage/approve_tax exist", f"{sorted(perms)}")
        missing = await conn.fetchval(
            """SELECT count(*) FROM roles r CROSS JOIN permissions p
               WHERE r.name = 'org_admin' AND p.name = ANY($1::text[])
                 AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)""",
            list(PERMISSIONS))
        need(int(missing) == 0, "every org_admin role holds the three tax permissions", f"{missing} missing")
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
