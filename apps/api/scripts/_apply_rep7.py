"""REP7 — apply the held-away / X-ray schema, then prove it landed.

    python3 apps/api/scripts/_apply_rep7.py --schema   # docs/rep7_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep7.py --verify   # read it back from the catalog

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. ``--verify`` reads columns, CHECKs, partial
unique indexes, RLS flags, every policy, triggers, grants, the loss-bank
source CHECK and the permission rows back, and exits non-zero on any gap.
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

SCHEMA_SQL = REPO / "docs" / "rep7_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

TABLES = ("held_away_sources", "held_away_imports", "held_away_records", "stale_confirmations", "xray_runs",
          "xray_lines")
PARTIAL_UNIQUES = ("held_away_sources_live_uq", "held_away_imports_task_uq", "held_away_records_record_uq",
                   "held_away_records_declared_uq", "stale_confirmations_open_uq", "stale_confirmations_task_uq",
                   "xray_runs_daily_uq")
CHECKS = ("held_away_sources_kind_chk", "held_away_sources_provider_chk", "held_away_sources_status_chk",
          "held_away_sources_connected_chk", "held_away_sources_stale_chk", "held_away_sources_archive_chk",
          "held_away_imports_kind_chk", "held_away_imports_status_chk", "held_away_imports_counts_chk",
          "held_away_imports_json_chk", "held_away_imports_ref_chk", "held_away_imports_maker_checker_chk",
          "held_away_imports_decided_chk", "held_away_imports_rejected_chk", "held_away_imports_archive_chk",
          "held_away_records_type_chk", "held_away_records_match_chk", "held_away_records_closed_chk",
          "stale_confirmations_purpose_chk", "stale_confirmations_proposal_chk", "stale_confirmations_status_chk",
          "stale_confirmations_confirmed_chk", "stale_confirmations_closed_chk", "stale_confirmations_archive_chk",
          "xray_runs_trigger_chk", "xray_runs_maker_chk", "xray_runs_json_chk", "xray_lines_kind_chk",
          "xray_lines_weight_chk", "xray_lines_json_chk", "loss_bank_entries_source_chk")
GUARDS = {"held_away_sources": "held_away_sources_guard_trg", "held_away_imports": "held_away_imports_guard_trg",
          "held_away_records": "held_away_records_guard_trg", "stale_confirmations": "stale_confirmations_guard_trg",
          "xray_runs": "xray_runs_guard_trg", "xray_lines": "xray_lines_guard_trg"}
PERMISSIONS = ("view_held_away", "manage_held_away", "approve_held_away")


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
        src = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                  "WHERE conname = 'loss_bank_entries_source_chk'")
        need("'held_away'" in (src or "") and "'lot_close'" in (src or ""),
             "loss_bank_entries.source admits 'held_away' (and keeps the rep6 sources)", (src or "")[:160])
        perms = {r["name"] for r in await conn.fetch("SELECT name FROM permissions WHERE name = ANY($1::text[])",
                                                     list(PERMISSIONS))}
        need(perms == set(PERMISSIONS), "permissions view/manage/approve_held_away exist", f"{sorted(perms)}")
        missing = await conn.fetchval(
            """SELECT count(*) FROM roles r CROSS JOIN permissions p
               WHERE r.name = 'org_admin' AND p.name = ANY($1::text[])
                 AND NOT EXISTS (SELECT 1 FROM role_permissions rp WHERE rp.role_id = r.id AND rp.permission_id = p.id)""",
            list(PERMISSIONS))
        need(int(missing) == 0, "every org_admin role holds the three held-away permissions", f"{missing} missing")
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
