"""REP12 — Task 1 discovery (READ ONLY). Prints the facts docs/REP12_DISCOVERY.md records.

    python3 apps/api/scripts/_discover_rep12.py

Runs on the operator DSN (catalog + platform tables are readable there) and writes NOTHING. Never prints a secret.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"


async def main() -> None:
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    try:
        print("user:", await conn.fetchval("SELECT current_user"), "via", how)

        async def show(label, sql, *args):
            rows = await conn.fetch(sql, *args)
            print(f"\n== {label} ({len(rows)} rows)")
            for r in rows[:60]:
                print("  ", dict(r))

        for t in ("securities_global_relationships", "securities_global_note_terms", "reference_filings",
                  "note_term_readings", "adviser_quotes"):
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='portfolio' AND table_name=$1 "
                "ORDER BY ordinal_position", t)]
            print(f"\nportfolio.{t}: {cols}")
        for t in ("notifications", "member_todos", "roles", "permissions", "role_permissions", "user_roles",
                  "users", "organizations", "audit_log"):
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name=$1 ORDER BY ordinal_position", t)]
            print(f"\npublic.{t}: {cols}")
        await show("1c edges per state (live underlying_of)",
                   "SELECT link_state, count(*) FROM portfolio.securities_global_relationships "
                   "WHERE relationship_type='underlying_of' AND valid_to IS NULL AND system_to IS NULL "
                   "GROUP BY 1 ORDER BY 1")
        await show("1c ambiguous edges by proposal shape",
                   "SELECT link_state, proposal_confidence, proposal_kind, (proposed_global_security_id IS NOT NULL) "
                   "AS has_target, count(*) FROM portfolio.securities_global_relationships "
                   "WHERE relationship_type='underlying_of' AND valid_to IS NULL AND system_to IS NULL "
                   "AND link_state <> 'resolved' GROUP BY 1,2,3,4 ORDER BY 1,2,3,4")
        await show("1c proposal hints sample",
                   "SELECT raw_underlying_text, proposal_confidence, proposal_kind, left(proposal_hint, 160) AS hint "
                   "FROM portfolio.securities_global_relationships WHERE relationship_type='underlying_of' "
                   "AND valid_to IS NULL AND system_to IS NULL AND link_state <> 'resolved' "
                   "ORDER BY proposal_confidence NULLS LAST, raw_underlying_text LIMIT 25")
        await show("1c notes by edge count",
                   "SELECT n, count(*) FROM (SELECT from_global_security_id, count(*) n FROM "
                   "portfolio.securities_global_relationships WHERE relationship_type='underlying_of' AND valid_to "
                   "IS NULL AND system_to IS NULL GROUP BY 1) x GROUP BY n ORDER BY n")
        await show("1c CHECKs on relationships", "SELECT conname, pg_get_constraintdef(oid) d FROM pg_constraint "
                   "WHERE conrelid='portfolio.securities_global_relationships'::regclass AND contype='c'")
        await show("1c triggers on relationships", "SELECT tgname FROM pg_trigger WHERE tgrelid="
                   "'portfolio.securities_global_relationships'::regclass AND NOT tgisinternal")
        await show("1b live terms / verdicts",
                   "SELECT (SELECT count(*) FROM portfolio.securities_global_note_terms WHERE valid_to IS NULL AND "
                   "system_to IS NULL) terms, (SELECT count(*) FROM portfolio.note_replicability WHERE valid_to IS "
                   "NULL AND system_to IS NULL) verdicts_live, (SELECT count(*) FROM portfolio.note_replicability) "
                   "verdicts_all, (SELECT count(*) FROM public.note_replicas) replicas, (SELECT count(*) FROM "
                   "public.note_replica_comparisons) comparisons")
        await show("1b verdicts", "SELECT verdict, needs_review, count(*) FROM portfolio.note_replicability WHERE "
                   "valid_to IS NULL AND system_to IS NULL GROUP BY 1,2 ORDER BY 1,2")
        await show("1d dates on terms", "SELECT t.terms_status, min(f.filing_date) min_filed, max(f.filing_date) "
                   "max_filed, count(t.initial_valuation_date) n_pricing, min(t.system_from) first_written, "
                   "max(t.system_from) last_written, count(*) FROM portfolio.securities_global_note_terms t LEFT JOIN "
                   "portfolio.reference_filings f ON f.id=t.reference_filing_id WHERE t.valid_to IS NULL AND "
                   "t.system_to IS NULL GROUP BY 1")
        await show("1d terms rows per note", "SELECT n, count(*) FROM (SELECT global_security_id, count(*) n FROM "
                   "portfolio.securities_global_note_terms WHERE valid_to IS NULL AND system_to IS NULL GROUP BY 1) x "
                   "GROUP BY 1")
        await show("1e replication permissions by role", "SELECT r.name, o.is_test, p.name perm, count(*) FROM "
                   "public.role_permissions rp JOIN public.roles r ON r.id=rp.role_id JOIN public.permissions p ON "
                   "p.id=rp.permission_id LEFT JOIN public.organizations o ON o.id=r.org_id WHERE p.name LIKE "
                   "'%replication%' GROUP BY 1,2,3 ORDER BY 1,2,3")
        await show("1e member_todos sources", "SELECT source, related_type, count(*) FROM public.member_todos "
                   "GROUP BY 1,2 ORDER BY 3 DESC LIMIT 20")
        await show("1e notifications by type", "SELECT * FROM public.notifications LIMIT 0")
        await show("name collision", "SELECT table_schema, table_name FROM information_schema.tables WHERE "
                   "table_name LIKE 'note_check%'")
        await show("orgs", "SELECT is_test, count(*) FROM public.organizations GROUP BY 1")
        await show("teardown fn", "SELECT proname FROM pg_proc WHERE proname IN ('execution_teardown_allowed')")
    finally:
        await conn.close()


async def catalog() -> None:
    """--catalog: the NOT NULL columns, CHECKs and UNIQUE indexes the verify's fixture seeds rely on."""
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    conn = await connect(dsn)
    try:
        for t in ("portfolio.reference_filings", "portfolio.note_terms_stp_policy", "portfolio.securities_global",
                  "portfolio.securities_global_note_terms", "portfolio.securities_global_relationships"):
            s_, n_ = t.split(".")
            cols = await conn.fetch("SELECT column_name, data_type, column_default FROM information_schema.columns "
                                    "WHERE table_schema = $1 AND table_name = $2 AND is_nullable = 'NO' ORDER BY "
                                    "ordinal_position", s_, n_)
            print(f"\n{t} NOT NULL: {[(c['column_name'], c['data_type'], (c['column_default'] or '')[:30]) for c in cols]}")
            for r in await conn.fetch("SELECT conname, pg_get_constraintdef(oid) d FROM pg_constraint WHERE conrelid = "
                                      "$1::regclass AND contype IN ('c', 'u')", t):
                print(f"   {r['conname']}: {r['d'][:220]}")
            for r in await conn.fetch("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = $1 AND "
                                      "tablename = $2 AND indexdef LIKE '%UNIQUE%'", s_, n_):
                print(f"   idx {r['indexname']}: {r['indexdef'][-160:]}")
            trg = [r["tgname"] for r in await conn.fetch("SELECT tgname FROM pg_trigger WHERE tgrelid = $1::regclass "
                                                         "AND NOT tgisinternal", t)]
            print(f"   triggers: {trg}")
        print("\nfixture prefixes:", await conn.fetchval(
            "SELECT count(*) FROM portfolio.reference_filings WHERE accession_number LIKE 'ZZR12%' OR cik LIKE 'ZZR12%'"),
              await conn.fetchval("SELECT count(*) FROM portfolio.securities_global WHERE name LIKE 'REP12 VERIFY%'"),
              await conn.fetchval("SELECT count(*) FROM public.organizations WHERE id::text LIKE '99000000-0000-0000-0000-0000001c%'"))
        print("rls on reference_filings / stp policy:", [(r["tablename"], r["cmd"]) for r in await conn.fetch(
            "SELECT tablename, cmd FROM pg_policies WHERE tablename IN ('reference_filings', 'note_terms_stp_policy')")])
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(catalog() if "--catalog" in sys.argv else main())
