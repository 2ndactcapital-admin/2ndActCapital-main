"""REP2 — apply the execution / capabilities / human-task schema, then prove it landed.

    python3 apps/api/scripts/_apply_rep2.py --schema   # docs/rep2_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep2.py --verify   # read it back from the catalog

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. ``--verify`` reads columns, constraints,
indexes, RLS flags, every policy, triggers, grants and the seed rows back and
exits non-zero on any gap. Never prints a secret.
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

SCHEMA_SQL = REPO / "docs" / "rep2_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

PLATFORM_TABLES = ("custodian_capabilities",)
ORG_TABLES = ("execution_instructions", "execution_instruction_lots", "execution_fills",
              "execution_instruction_events")
GUARDS = ("custodian_capabilities_guard_trg", "execution_instructions_guard_trg",
          "execution_instruction_lots_guard_trg", "execution_fills_guard_trg",
          "execution_instruction_events_immutable_trg")
PARTIAL_UNIQUES = ("custodian_capabilities_live_uq", "custodian_capabilities_version_uq",
                   "execution_instructions_task_live_uq", "execution_instruction_lots_live_uq")
CHECKS = ("custodian_capabilities_value_chk", "custodian_capabilities_platform_chk",
          "execution_instructions_maker_checker_chk", "execution_instructions_amount_chk",
          "execution_instructions_price_chk", "execution_instructions_status_chk",
          "execution_instruction_events_human_chk")
CAPABILITY_KEYS = ("fractional_shares", "equity_trading_api", "model_api", "trade_list_upload", "options_level",
                   "flex_options", "index_options", "margin", "short_selling", "in_kind_transfers",
                   "custodian_tax_loss_harvesting", "lot_level_cost_basis_feed", "held_away_aggregation")


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
        col = await conn.fetchrow(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'workflow_steps' AND column_name = 'assigned_permission'")
        need(col is not None and col["is_nullable"] == "YES" and col["column_default"] is None,
             "workflow_steps.assigned_permission: nullable text, no default (existing steps unchanged)",
             f"{dict(col) if col else None}")
        n_set = await conn.fetchval("SELECT count(*) FROM workflow_steps WHERE assigned_permission IS NOT NULL "
                                    "AND workflow_version_id NOT IN (SELECT v.id FROM workflow_versions v JOIN "
                                    "workflow_definitions d ON d.id = v.workflow_definition_id "
                                    "WHERE d.name LIKE 'system:human_task:%')")
        need(n_set == 0, "no pre-existing workflow step carries an assigned_permission", f"{n_set}")
        for t in PLATFORM_TABLES + ORG_TABLES:
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = $1 ORDER BY ordinal_position", t)]
            need(bool(cols), f"public.{t} exists", f"{len(cols)} columns")
            need(all(c in cols for c in ("valid_from", "valid_to", "system_from", "system_to")),
                 f"public.{t} is bi-temporal")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"public.{t}")
            need(rls is True, f"public.{t} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = 'public' "
                                   "AND tablename = $1", t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"public.{t}: a policy for SELECT/INSERT/UPDATE/DELETE")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = 'public' "
                "AND table_name = $1 AND grantee = 'app_service'", t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on public.{t}")
            quals = {r["cmd"]: (r["qual"] or "") + (r["with_check"] or "") for r in pol}
            if t in PLATFORM_TABLES:
                need(quals.get("SELECT") == "true" and all("is_super_admin" in quals.get(c, "")
                                                         for c in ("INSERT", "UPDATE", "DELETE")),
                     f"public.{t}: platform shape (read all, write super admin)")
            else:
                need(all("NULLIF(current_setting('app.current_org_id'" in q for q in quals.values()) and len(quals) == 4,
                     f"public.{t}: org isolation with NULLIF on every operation")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND "
            "(indexname LIKE 'execution_%' OR indexname LIKE 'custodian_%')")}
        for name in PARTIAL_UNIQUES:
            d = idx.get(name, "")
            need("UNIQUE" in d and "WHERE" in d, f"partial unique index {name}", d[:140])
        cons = {r["conname"] for r in await conn.fetch(
            "SELECT conname FROM pg_constraint WHERE conname LIKE 'execution_%' OR conname LIKE 'custodian_%'")}
        for name in CHECKS:
            need(name in cons, f"constraint {name}")
        trg = {r["tgname"] for r in await conn.fetch("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")}
        for name in GUARDS:
            need(name in trg, f"trigger {name}")
        seed = await conn.fetch("SELECT custodian_code, capability_key, value FROM public.custodian_capabilities "
                                "WHERE valid_to IS NULL AND system_to IS NULL")
        by = {(r["custodian_code"], r["capability_key"]): r["value"] for r in seed}
        need(all(by.get(("altruist", k)) == "unknown" for k in CAPABILITY_KEYS),
             "altruist seeded: all 13 capabilities 'unknown'", f"{sum(1 for k in by if k[0] == 'altruist')}")
        need(all(("manual", k) in by for k in CAPABILITY_KEYS)
             and by.get(("manual", "equity_trading_api")) == "no",
             "manual seeded: all 13 capabilities; equity_trading_api 'no'")
        perms = {r["name"] for r in await conn.fetch("SELECT name FROM permissions WHERE name LIKE '%_execution'")}
        need(perms == {"view_execution", "manage_execution", "approve_execution"}, "three execution permissions",
             f"{sorted(perms)}")
        holders = {r["name"] for r in await conn.fetch(
            "SELECT DISTINCT r.name FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
            "JOIN permissions p ON p.id = rp.permission_id WHERE p.name LIKE '%_execution'")}
        need(holders == {"org_admin"}, "granted to org_admin and no other role", f"{sorted(holders)}")
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
