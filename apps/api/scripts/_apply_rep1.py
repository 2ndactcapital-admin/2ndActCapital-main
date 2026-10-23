"""REP1 — apply the Replication Studio part 1 schema, then prove it landed.

    python3 apps/api/scripts/_apply_rep1.py --schema   # docs/rep1_part1.sql, one transaction
    python3 apps/api/scripts/_apply_rep1.py --verify   # read it back from the catalog

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels migration
text containing DROP). Idempotent: every statement is IF NOT EXISTS, DROP ...
IF EXISTS + CREATE, or ON CONFLICT. ``--verify`` reads columns, constraints,
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

SCHEMA_SQL = REPO / "docs" / "rep1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

PLATFORM_TABLES = (
    "replica_instrument_classes", "replica_holdings_sources", "replica_holdings_snapshots",
    "replica_holdings_rows", "replica_source_documents", "replica_terms_field_registry",
    "replica_term_readings", "replica_refresh_events",
)
ORG_TABLES = ("replica_securities", "replica_terms_versions", "replica_terms_actions")
IMMUTABLE = ("replica_holdings_snapshots", "replica_holdings_rows", "replica_source_documents",
             "replica_term_readings", "replica_terms_actions", "replica_refresh_events")
PARTIAL_UNIQUES = (
    "replica_holdings_sources_key_live_uq", "replica_securities_live_uq",
    "replica_terms_field_registry_key_live_uq", "replica_terms_versions_one_approved_uq",
    "replica_terms_versions_one_draft_uq", "replica_terms_versions_no_uq", "replica_refresh_events_day_uq",
)


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
        for t in PLATFORM_TABLES + ORG_TABLES:
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'portfolio' "
                "AND table_name = $1 ORDER BY ordinal_position", t)]
            need(bool(cols), f"portfolio.{t} exists", f"{len(cols)} columns")
            need(all(c in cols for c in ("valid_from", "valid_to", "system_from", "system_to")),
                 f"portfolio.{t} is bi-temporal (valid_* and system_*)")
            rls = await conn.fetchrow("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass",
                                      f"portfolio.{t}")
            need(bool(rls and rls["relrowsecurity"]), f"portfolio.{t} has RLS enabled")
            cmds = {r["cmd"] for r in await conn.fetch(
                "SELECT cmd FROM pg_policies WHERE schemaname = 'portfolio' AND tablename = $1", t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= cmds,
                 f"portfolio.{t} has a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(cmds)}")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = 'portfolio' "
                "AND table_name = $1 AND grantee = 'app_service'", t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on portfolio.{t}",
                 f"{sorted(grants)}")
            if t in PLATFORM_TABLES:
                quals = {r["cmd"]: (r["qual"] or "") + (r["with_check"] or "") for r in await conn.fetch(
                    "SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = 'portfolio' AND tablename = $1",
                    t)}
                need(quals.get("SELECT") == "true" and all("is_super_admin" in quals.get(c, "")
                                                         for c in ("INSERT", "UPDATE", "DELETE")),
                     f"portfolio.{t}: platform shape (read all, write super admin)")
            else:
                quals = [(r["qual"] or "") + (r["with_check"] or "") for r in await conn.fetch(
                    "SELECT qual, with_check FROM pg_policies WHERE schemaname = 'portfolio' AND tablename = $1", t)]
                need(len(quals) == 4 and all("NULLIF(current_setting('app.current_org_id'" in q for q in quals),
                     f"portfolio.{t}: org isolation with NULLIF on every operation")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'portfolio' AND indexname LIKE 'replica_%'")}
        for name in PARTIAL_UNIQUES:
            d = idx.get(name, "")
            need("UNIQUE" in d and "WHERE" in d, f"partial unique index {name}", d[:140])
        cons = {r["conname"]: r["d"] for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(c.oid) AS d FROM pg_constraint c JOIN pg_namespace n "
            "ON n.oid = c.connamespace WHERE n.nspname = 'portfolio' AND conname LIKE 'replica_%'")}
        for name in ("replica_holdings_snapshots_file_uq", "replica_holdings_rows_queued_chk",
                     "replica_holdings_rows_resolved_chk", "replica_terms_versions_maker_checker_chk",
                     "replica_securities_display_name_chk", "replica_terms_actions_reason_chk"):
            need(name in cons, f"constraint {name}", cons.get(name, "")[:140])
        trg = {r["tgname"] for r in await conn.fetch(
            "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'replica_%'")}
        for t in IMMUTABLE:
            need(f"{t}_immutable_trg" in trg, f"immutability trigger on portfolio.{t}")
        need("replica_terms_versions_guard_trg" in trg, "approved-version guard trigger")
        dfc = {r["conname"]: r["d"] for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conrelid = 'public.document_field_corrections'::regclass AND contype = 'c'")}
        need("replica_terms" in dfc.get("document_field_corrections_target_type_chk", "")
             and "replica_terms" in dfc.get("document_field_corrections_document_pairing_chk", ""),
             "document_field_corrections accepts target_type 'replica_terms' (org-scoped, no document)")
        perms = {r["name"] for r in await conn.fetch(
            "SELECT name FROM permissions WHERE name LIKE '%_replication'")}
        need(perms == {"view_replication", "manage_replication", "approve_replication"},
             "three replication permissions", f"{sorted(perms)}")
        holders = await conn.fetch(
            "SELECT DISTINCT r.name FROM role_permissions rp JOIN roles r ON r.id = rp.role_id "
            "JOIN permissions p ON p.id = rp.permission_id WHERE p.name LIKE '%_replication'")
        need({r["name"] for r in holders} == {"org_admin"}, "granted to org_admin and no other role",
             f"{[r['name'] for r in holders]}")
        n_fields = await conn.fetchval("SELECT count(*) FROM portfolio.replica_terms_field_registry "
                                       "WHERE valid_to IS NULL AND system_to IS NULL")
        crit = {r["field_key"] for r in await conn.fetch(
            "SELECT field_key FROM portfolio.replica_terms_field_registry WHERE is_critical")}
        need(n_fields >= 19 and crit == {"expense_ratio_gross_pct", "expense_ratio_net_pct", "index_name",
                                         "weighting_method", "rebalance_frequency"},
             "field registry seeded; critical = expense ratios, index name, weighting, rebalance",
             f"{n_fields} fields, critical {sorted(crit)}")
        srcs = {r["key"]: r["active"] for r in await conn.fetch(
            "SELECT key, active FROM portfolio.replica_holdings_sources WHERE valid_to IS NULL AND system_to IS NULL")}
        need(srcs.get("ssga_spdr") is True and srcs.get("sec_nport") is True
             and srcs.get("invesco") is False and srcs.get("ishares") is False,
             "holdings sources seeded (ssga + N-PORT active; Invesco/iShares inactive pending confirmation)",
             f"{srcs}")
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
