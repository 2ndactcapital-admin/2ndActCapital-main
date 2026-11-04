"""REP12 — apply the note-check schema and prove it landed.

    python3 apps/api/scripts/_apply_rep12.py --schema    # docs/rep12_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep12.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_rep12.py --all       # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. Never prints a secret.

THE MIGRATION WRITES NO ROWS. ``--verify`` also proves every existing
note_replica_comparisons row is 'person' with a computed_by (the new CHECK is
added VALID, so the ALTER itself would have failed otherwise); live counts are
printed, never asserted against a frozen number.
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

SCHEMA_SQL = REPO / "docs" / "rep12_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

NEW_TABLES = ("public.note_checks", "public.note_check_settings")
PARTIAL_UNIQUES = ("note_checks_live_uq", "note_check_settings_live_uq")
CHECKS = {
    "public.note_checks": ("note_checks_trigger_chk", "note_checks_issued_basis_chk", "note_checks_verdict_chk",
                           "note_checks_mapping_chk", "note_checks_underlying_chk", "note_checks_outcome_chk",
                           "note_checks_pricing_chk", "note_checks_status_chk", "note_checks_review_chk",
                           "note_checks_dismiss_note_chk", "note_checks_json_chk", "note_checks_key_chk",
                           "note_checks_archive_chk"),
    "public.note_check_settings": ("note_check_settings_threshold_chk", "note_check_settings_archetypes_chk",
                                   "note_check_settings_lists_chk"),
    "public.note_replica_comparisons": ("note_replica_comparisons_source_chk", "note_replica_comparisons_priced_chk",
                                        "note_replica_comparisons_fee_chk"),
}
GUARDS = {"public.note_checks": "note_checks_guard_trg",
          "public.note_check_settings": "note_check_settings_guard_trg",
          "public.note_replica_comparisons": "note_replica_comparisons_guard_trg"}


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
            quals = [(r["qual"] or "") + (r["with_check"] or "") for r in pol]
            need(len(quals) == 4 and all("NULLIF(current_setting('app.current_org_id'" in q for q in quals),
                 f"{full}: org isolation with NULLIF on every operation")
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
        valid = await conn.fetchval("SELECT convalidated FROM pg_constraint WHERE conrelid = "
                                    "'public.note_replica_comparisons'::regclass AND conname = "
                                    "'note_replica_comparisons_source_chk'")
        need(valid is True, "note_replica_comparisons_source_chk is VALIDATED over every existing row")
        nn = await conn.fetchval("SELECT is_nullable FROM information_schema.columns WHERE table_schema = 'public' "
                                 "AND table_name = 'note_replica_comparisons' AND column_name = 'computed_by'")
        need(nn == "YES", "note_replica_comparisons.computed_by is nullable (an automatic check has no person)")
        trg = {(r["rel"], r["tgname"]) for r in await conn.fetch(
            "SELECT n.nspname || '.' || c.relname AS rel, t.tgname FROM pg_trigger t JOIN pg_class c "
            "ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE NOT t.tgisinternal")}
        for rel, name in GUARDS.items():
            need((rel, name) in trg, f"trigger {name} on {rel}")
        fks = await conn.fetchval("SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conrelid = "
                                  "ANY($1::regclass[])", list(NEW_TABLES))
        need(int(fks) >= 11, "foreign keys on the new tables", f"{fks}")
        print(f"[INFO] live checks: {await conn.fetchval('SELECT count(*) FROM public.note_checks')}"
              f"; settings rows: {await conn.fetchval('SELECT count(*) FROM public.note_check_settings')}"
              f"; comparisons: {await conn.fetchval('SELECT count(*) FROM public.note_replica_comparisons')}")
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
