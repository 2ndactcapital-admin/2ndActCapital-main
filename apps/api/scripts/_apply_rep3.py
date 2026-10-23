"""REP3 — apply the model-management schema, then prove it landed.

    python3 apps/api/scripts/_apply_rep3.py --schema   # docs/rep3_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep3.py --verify   # read it back from the catalog

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. ``--verify`` reads columns, constraints,
partial unique indexes, RLS flags, every policy, triggers, grants, the
SECURITY DEFINER helpers and the permission grants back and exits non-zero on
any gap. Never prints a secret.
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

SCHEMA_SQL = REPO / "docs" / "rep3_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

ORG_TABLES = ("models", "model_uploads", "model_column_mappings", "model_versions", "model_version_positions",
              "model_sleeves", "model_upload_events", "model_subscriptions", "model_events")
SHELF_TABLES = ("model_library_entries",)
SYSTEM_AXIS = ("models", "model_versions", "model_library_entries", "model_subscriptions")
CARVE_OUT = {"models": "model_is_platform_published", "model_versions": "model_version_is_platform_readable",
             "model_version_positions": "model_version_is_platform_readable",
             "model_sleeves": "model_version_is_platform_readable"}
GUARDS = ("models_guard_trg", "model_versions_guard_trg", "model_version_positions_guard_trg",
          "model_sleeves_guard_trg", "model_uploads_immutable_trg", "model_upload_events_immutable_trg",
          "model_events_immutable_trg", "model_library_entries_guard_trg", "model_subscriptions_guard_trg",
          "model_column_mappings_guard_trg")
PARTIAL_UNIQUES = ("models_live_name_uq", "model_uploads_stored_uq", "model_column_mappings_live_uq",
                   "model_column_mappings_version_uq", "model_versions_approved_live_uq",
                   "model_versions_open_live_uq", "model_versions_no_uq", "model_version_positions_row_uq",
                   "model_version_positions_security_uq", "model_version_positions_cash_uq", "model_sleeves_live_uq",
                   "model_library_entries_model_live_uq", "model_subscriptions_model_live_uq")
CHECKS = ("models_reserved_name_chk", "models_source_chk", "models_status_chk", "models_library_chk",
          "model_uploads_outcome_chk", "model_uploads_duplicate_chk", "model_uploads_sha_chk",
          "model_versions_status_chk", "model_versions_maker_checker_chk", "model_versions_approved_chk",
          "model_versions_submitted_chk", "model_version_positions_weight_chk", "model_version_positions_band_chk",
          "model_version_positions_state_chk", "model_sleeves_weight_chk", "model_sleeves_follow_chk",
          "model_upload_events_event_chk", "model_library_entries_shelf_chk", "model_library_entries_cadence_chk",
          "model_events_action_chk", "model_events_human_chk", "models_library_entry_fk")
DEFINERS = ("model_is_platform_published", "model_version_is_platform_readable", "model_library_usage")
PERMS = ("view_models", "manage_models", "approve_models")


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
        for t in ORG_TABLES + SHELF_TABLES:
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'portfolio' "
                "AND table_name = $1 ORDER BY ordinal_position", t)]
            need(bool(cols), f"portfolio.{t} exists", f"{len(cols)} columns")
            need(all(c in cols for c in ("valid_from", "valid_to", "system_from", "system_to")),
                 f"portfolio.{t} is bi-temporal")
            if t in SYSTEM_AXIS:
                need("archived_from_id" in cols, f"portfolio.{t} archives on the system axis (archived_from_id)")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"portfolio.{t}")
            need(rls is True, f"portfolio.{t} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = 'portfolio' "
                                   "AND tablename = $1", t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"portfolio.{t}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = 'portfolio' "
                "AND table_name = $1 AND grantee = 'app_service'", t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on portfolio.{t}")
            quals = {r["cmd"]: (r["qual"] or "") + (r["with_check"] or "") for r in pol}
            if t in SHELF_TABLES:
                need("'platform'" in quals.get("SELECT", "") and all(
                    "is_super_admin" in quals.get(c, "") and "'org'" in quals.get(c, "")
                    for c in ("INSERT", "UPDATE", "DELETE")),
                     f"portfolio.{t}: platform shelf read by all, written by super admin; org shelf by its org")
            else:
                need(all("NULLIF(current_setting('app.current_org_id'" in q for q in quals.values())
                     and len(quals) == 4, f"portfolio.{t}: org isolation with NULLIF on every operation")
                if t in CARVE_OUT:
                    need(CARVE_OUT[t] in quals.get("SELECT", "")
                         and all(CARVE_OUT[t] not in quals.get(c, "") for c in ("INSERT", "UPDATE", "DELETE")),
                         f"portfolio.{t}: platform-content carve-out on SELECT only ({CARVE_OUT[t]})")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'portfolio' AND indexname LIKE 'model%'")}
        for name in PARTIAL_UNIQUES:
            d = idx.get(name, "")
            need("UNIQUE" in d and "WHERE" in d, f"partial unique index {name}", d[:150])
        cons = {r["conname"] for r in await conn.fetch(
            "SELECT conname FROM pg_constraint WHERE connamespace = 'portfolio'::regnamespace AND conname LIKE 'model%'")}
        for name in CHECKS:
            need(name in cons, f"constraint {name}")
        trg = {r["tgname"] for r in await conn.fetch(
            "SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND c.relnamespace = 'portfolio'::regnamespace")}
        for name in GUARDS:
            need(name in trg, f"trigger {name}")
        fns = {r["proname"]: r for r in await conn.fetch(
            "SELECT proname, prosecdef, proconfig FROM pg_proc WHERE pronamespace = 'portfolio'::regnamespace "
            "AND proname = ANY($1::text[])", list(DEFINERS))}
        for name in DEFINERS:
            f = fns.get(name)
            need(f is not None and f["prosecdef"] and any("search_path" in c for c in (f["proconfig"] or [])),
                 f"SECURITY DEFINER helper {name} with a pinned search_path")
        perms = {r["name"] for r in await conn.fetch("SELECT name FROM permissions WHERE name = ANY($1::text[])",
                                                     list(PERMS))}
        need(perms == set(PERMS), "three model permissions", f"{sorted(perms)}")
        holders = {r["name"] for r in await conn.fetch(
            "SELECT DISTINCT r.name FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
            "JOIN permissions p ON p.id = rp.permission_id WHERE p.name = ANY($1::text[])", list(PERMS))}
        need(holders == {"org_admin"}, "granted to org_admin and no other role", f"{sorted(holders)}")
        missing = await conn.fetchval(
            "SELECT count(*) FROM roles r WHERE r.name = 'org_admin' AND (SELECT count(*) FROM role_permissions rp "
            "JOIN permissions p ON p.id = rp.permission_id WHERE rp.role_id = r.id AND p.name = ANY($1::text[])) < 3",
            list(PERMS))
        need(missing == 0, "every org_admin role holds all three", f"{missing} missing")
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
