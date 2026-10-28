"""LOTMETHOD — apply the lot-method schema (docs/lotmethod_part1.sql) and prove
it landed.

    python3 apps/api/scripts/_apply_lotmethod.py --schema    # one transaction
    python3 apps/api/scripts/_apply_lotmethod.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_lotmethod.py --all       # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. Prints no secret and no account number.

THE MIGRATION WRITES NO SETTINGS ROWS: an account with no approved method row
keeps today's rule. The only data change is B6's backfill of
``deals.version_of_deal_id`` on history copies whose (org, created_at,
created_by) matches exactly ONE live deal; ``--verify`` prints how many were
filled and how many were left NULL as ambiguous (those fall back to the old
match at read time, logged).
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

SCHEMA_SQL = REPO / "docs" / "lotmethod_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

TABLES = ("household_lot_method", "account_lot_method_settings")
PARTIAL_UNIQUES = ("household_lot_method_live_uq", "household_lot_method_pending_uq", "household_lot_method_task_uq",
                   "account_lot_method_settings_live_uq", "account_lot_method_settings_pending_uq",
                   "account_lot_method_settings_task_uq")
CHECKS = ("household_lot_method_method_chk", "household_lot_method_pref_chk", "household_lot_method_target_chk",
          "household_lot_method_status_chk", "household_lot_method_reason_chk",
          "household_lot_method_maker_checker_chk", "household_lot_method_pending_chk",
          "household_lot_method_decided_chk", "household_lot_method_rejected_chk", "household_lot_method_closed_chk",
          "household_lot_method_archive_chk", "account_lot_method_settings_method_chk",
          "account_lot_method_settings_pref_chk", "account_lot_method_settings_target_chk",
          "account_lot_method_settings_custodian_chk", "account_lot_method_settings_source_chk",
          "account_lot_method_settings_status_chk", "account_lot_method_settings_reason_chk",
          "account_lot_method_settings_maker_checker_chk", "account_lot_method_settings_pending_chk",
          "account_lot_method_settings_decided_chk", "account_lot_method_settings_rejected_chk",
          "account_lot_method_settings_closed_chk", "account_lot_method_settings_archive_chk",
          "rebalance_proposals_lot_method_chk", "rebalance_proposals_custodian_method_chk",
          "rebalance_proposal_trades_lot_method_chk")
GUARDS = {"household_lot_method": "household_lot_method_guard_trg",
          "account_lot_method_settings": "account_lot_method_settings_guard_trg"}
PROPOSAL_COLS = ("lot_method", "lot_method_source", "gain_preference", "realize_gains_target", "lot_picks",
                 "custodian_cost_basis_method")


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
            rows = await conn.fetchval(f"SELECT count(*) FROM public.{t}")
            print(f"[INFO] public.{t}: {rows} row(s) (the migration writes none)")
        pcols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'rebalance_proposals'")}
        need(set(PROPOSAL_COLS) <= pcols, "rebalance_proposals lot-method columns", f"{sorted(set(PROPOSAL_COLS) - pcols)}")
        tcols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'rebalance_proposal_trades'")}
        need("lot_method" in tcols, "rebalance_proposal_trades.lot_method")
        dcols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'deals'")}
        need("version_of_deal_id" in dcols, "deals.version_of_deal_id (B6)")
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
        legacy = await conn.fetchval("SELECT count(*) FROM public.rebalance_proposals WHERE lot_method IS NULL")
        print(f"[INFO] proposals with no recorded lot method: {legacy} (made before this sprint: today's rule)")
        filled = await conn.fetchval("SELECT count(*) FROM public.deals WHERE system_to IS NOT NULL "
                                     "AND version_of_deal_id IS NOT NULL")
        left = await conn.fetchval("SELECT count(*) FROM public.deals WHERE system_to IS NOT NULL "
                                   "AND version_of_deal_id IS NULL")
        print(f"[INFO] B6 backfill: {filled} deal history row(s) carry their deal id; {left} left NULL "
              "(ambiguous or unmatched — the read falls back to the old created_at + creator match, logged)")
        wrong = await conn.fetchval(
            "SELECT count(*) FROM public.deals h JOIN public.deals l ON l.id = h.version_of_deal_id "
            "WHERE h.system_to IS NOT NULL AND (l.org_id <> h.org_id OR l.created_at <> h.created_at)")
        need(int(wrong) == 0, "every backfilled deal id names a deal of the same org and creation time", f"{wrong}")
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
