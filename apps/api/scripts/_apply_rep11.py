"""REP11 — apply the note-replication schema and prove it landed.

    python3 apps/api/scripts/_apply_rep11.py --schema    # docs/rep11_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep11.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_rep11.py --all       # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent. Never prints a secret.

THE MIGRATION WRITES NO ROWS. ``--verify`` also proves every existing adviser
quote still satisfies the widened kind CHECK (re-added without NOT VALID, so
the ALTER itself would have failed otherwise) and that no note-terms row was
touched (the live count is printed, never asserted against a frozen number).
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

SCHEMA_SQL = REPO / "docs" / "rep11_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

PLATFORM_TABLES = ("portfolio.note_replicability",)
ORG_TABLES = ("public.note_replicas", "public.note_replica_legs", "public.note_replica_comparisons")
NEW_TABLES = PLATFORM_TABLES + ORG_TABLES
PARTIAL_UNIQUES = ("note_replicability_live_uq", "note_replicas_open_uq", "note_replicas_approved_uq",
                   "note_replica_legs_live_uq")
CHECKS = {
    "portfolio.note_replicability": ("note_replicability_platform_chk", "note_replicability_verdict_chk",
                                     "note_replicability_mapping_chk", "note_replicability_reasons_chk",
                                     "note_replicability_review_chk", "note_replicability_spec_chk",
                                     "note_replicability_fp_chk"),
    "public.note_replicas": ("note_replicas_verdict_chk", "note_replicas_mapping_chk", "note_replicas_status_chk",
                             "note_replicas_period_chk", "note_replicas_level_chk", "note_replicas_treasury_chk",
                             "note_replicas_treasury_mapping_chk", "note_replicas_style_chk",
                             "note_replicas_settlement_chk", "note_replicas_multiplier_chk", "note_replicas_spread_chk",
                             "note_replicas_maker_checker_chk", "note_replicas_approved_chk",
                             "note_replicas_pending_chk", "note_replicas_archived_reason_chk",
                             "note_replicas_archive_chk"),
    "public.note_replica_legs": ("note_replica_legs_kind_chk", "note_replica_legs_direction_chk",
                                 "note_replica_legs_ratio_chk", "note_replica_legs_shape_chk",
                                 "note_replica_legs_level_chk", "note_replica_legs_contract_chk",
                                 "note_replica_legs_no_chk", "note_replica_legs_purpose_chk"),
    "public.note_replica_comparisons": ("note_replica_comparisons_status_chk", "note_replica_comparisons_priced_chk",
                                        "note_replica_comparisons_fee_chk", "note_replica_comparisons_json_chk",
                                        "note_replica_comparisons_method_chk"),
    "portfolio.adviser_quotes": ("adviser_quotes_kind_chk",),
}
GUARDS = {"portfolio.note_replicability": "note_replicability_guard_trg",
          "public.note_replicas": "note_replicas_guard_trg",
          "public.note_replica_legs": "note_replica_legs_guard_trg",
          "public.note_replica_comparisons": "note_replica_comparisons_guard_trg",
          "portfolio.adviser_quotes": "adviser_quotes_guard_trg"}
NEW_QUOTE_KINDS = ("note_price", "treasury_yield")


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
        kdef = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = "
                                   "'portfolio.adviser_quotes'::regclass AND conname = 'adviser_quotes_kind_chk'")
        need(all(f"'{k}'" in (kdef or "") for k in NEW_QUOTE_KINDS + ("premium", "index_level", "underlying_price",
                                                                       "implied_vol")),
             "adviser_quotes.kind admits note_price and treasury_yield (rep8's four kept)", (kdef or "")[:160])
        gsrc = await conn.fetchval("SELECT prosrc FROM pg_proc WHERE proname = 'adviser_quotes_guard'")
        need("note_price" in (gsrc or "") and "treasury_yield" in (gsrc or ""),
             "adviser_quotes_guard checks the instrument of the two new kinds")
        trg = {(r["rel"], r["tgname"]) for r in await conn.fetch(
            "SELECT n.nspname || '.' || c.relname AS rel, t.tgname FROM pg_trigger t JOIN pg_class c "
            "ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace WHERE NOT t.tgisinternal")}
        for rel, name in GUARDS.items():
            need((rel, name) in trg, f"trigger {name} on {rel}")
        fks = await conn.fetchval("SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND conrelid = "
                                  "ANY($1::regclass[])", list(NEW_TABLES))
        need(int(fks) >= 15, "foreign keys on the new tables", f"{fks}")
        print(f"[INFO] live note-terms rows: {await conn.fetchval(
            'SELECT count(*) FROM portfolio.securities_global_note_terms WHERE valid_to IS NULL AND system_to IS NULL')}"
              f"; verdicts: {await conn.fetchval('SELECT count(*) FROM portfolio.note_replicability')}"
              f"; replicas: {await conn.fetchval('SELECT count(*) FROM public.note_replicas')}"
              f"; quotes: {await conn.fetchval('SELECT count(*) FROM portfolio.adviser_quotes')}")
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
