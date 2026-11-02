"""REP9 — apply the block-trading schema and prove it landed.

    python3 apps/api/scripts/_apply_rep9.py --schema    # docs/rep9_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep9.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_rep9.py --all       # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. Never prints a secret.

The migration writes NO business rows: there is no allocation policy and no
executing account until a firm writes one (the policy text is the firm's own
words, approved by a second person), so no block can be built before that.
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

SCHEMA_SQL = REPO / "docs" / "rep9_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

TABLES = ("execution_accounts", "allocation_policies", "trade_blocks", "allocation_corrections", "block_allocations",
          "block_fills", "block_charges", "block_events")
PARTIAL_UNIQUES = ("allocation_policies_live_uq", "allocation_policies_pending_uq", "allocation_policies_version_uq",
                   "allocation_policies_task_uq", "trade_blocks_task_live_uq", "allocation_corrections_pending_uq",
                   "allocation_corrections_task_uq", "block_allocations_live_uq",
                   "block_allocations_open_instruction_uq")
CHECKS = (
    "execution_accounts_broker_chk", "execution_accounts_masked_chk", "execution_accounts_status_chk",
    "execution_accounts_archive_chk",
    "allocation_policies_method_chk", "allocation_policies_rounding_chk", "allocation_policies_partial_chk",
    "allocation_policies_minimum_chk", "allocation_policies_text_chk", "allocation_policies_reason_chk",
    "allocation_policies_version_chk", "allocation_policies_status_chk", "allocation_policies_maker_checker_chk",
    "allocation_policies_pending_chk", "allocation_policies_decided_chk", "allocation_policies_rejected_chk",
    "allocation_policies_closed_chk", "allocation_policies_archive_chk",
    "trade_blocks_side_chk", "trade_blocks_quantity_chk", "trade_blocks_executor_chk",
    "trade_blocks_execution_account_chk", "trade_blocks_route_chk", "trade_blocks_manual_reason_chk",
    "trade_blocks_price_chk", "trade_blocks_reference_chk", "trade_blocks_if_not_filled_chk", "trade_blocks_cross_chk",
    "trade_blocks_status_chk", "trade_blocks_makers_chk", "trade_blocks_maker_checker_chk",
    "trade_blocks_approved_chk", "trade_blocks_pending_chk", "trade_blocks_sent_chk", "trade_blocks_filled_chk",
    "trade_blocks_allocated_chk", "trade_blocks_closed_chk", "trade_blocks_cancelled_chk", "trade_blocks_archive_chk",
    "allocation_corrections_changes_chk", "allocation_corrections_reason_chk", "allocation_corrections_status_chk",
    "allocation_corrections_maker_checker_chk", "allocation_corrections_pending_chk",
    "allocation_corrections_decided_chk", "allocation_corrections_rejected_chk", "allocation_corrections_closed_chk",
    "allocation_corrections_archive_chk",
    "block_allocations_requested_chk", "block_allocations_allocated_chk", "block_allocations_unit_chk",
    "block_allocations_final_chk", "block_allocations_zero_chk", "block_allocations_settlement_kind_chk",
    "block_allocations_settlement_status_chk", "block_allocations_settlement_method_chk",
    "block_allocations_failed_chk", "block_allocations_settled_chk", "block_allocations_dvp_chk",
    "block_allocations_status_chk", "block_allocations_allocated_state_chk", "block_allocations_archive_chk",
    "block_fills_quantity_chk", "block_fills_price_chk",
    "block_charges_kind_chk", "block_charges_amount_chk", "block_charges_currency_chk", "block_charges_closed_chk",
    "block_events_action_chk", "block_events_human_chk", "block_events_detail_chk",
)
GUARDS = {"execution_accounts": "execution_accounts_guard_trg", "allocation_policies": "allocation_policies_guard_trg",
          "trade_blocks": "trade_blocks_guard_trg", "allocation_corrections": "allocation_corrections_guard_trg",
          "block_allocations": "block_allocations_guard_trg", "block_fills": "block_fills_guard_trg",
          "block_charges": "block_charges_guard_trg", "block_events": "block_events_immutable_trg"}
PERMISSIONS = ("view_blocks", "manage_blocks", "approve_blocks")


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
        route = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                    "WHERE conname = 'trade_blocks_route_chk'")
        need("'manual'" in (route or "") and "fix" not in (route or ""),
             "the database admits route 'manual' only (no FIX router is connected)", route or "")
        trg = {(r["rel"], r["tgname"]) for r in await conn.fetch(
            "SELECT c.relname AS rel, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace")}
        for rel, name in GUARDS.items():
            need((rel, name) in trg, f"trigger {name} on {rel}")
        perms = {r["name"] for r in await conn.fetch("SELECT name FROM permissions WHERE name = ANY($1::text[])",
                                                     list(PERMISSIONS))}
        need(perms == set(PERMISSIONS), "permissions view/manage/approve_blocks exist", f"{sorted(perms)}")
        missing = await conn.fetchval(
            """SELECT count(*) FROM roles r CROSS JOIN permissions p
               WHERE r.name = 'org_admin' AND p.name = ANY($1::text[])
                 AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)""",
            list(PERMISSIONS))
        need(int(missing) == 0, "every org_admin role holds the three block permissions", f"{missing} missing")
        rows = {t: int(await conn.fetchval(f"SELECT count(*) FROM public.{t}")) for t in TABLES}
        print(f"[INFO] row counts: {rows}")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


async def main() -> int:
    args = set(sys.argv[1:]) or {"--verify"}
    if "--schema" in args or "--all" in args:
        await apply_schema()
    if "--verify" in args or "--all" in args:
        return await verify_schema()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
