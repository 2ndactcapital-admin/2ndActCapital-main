"""REP7B Task 1 discovery — read-only. Real accounts (masked), households,
open instructions on held-away accounts, links, catalog facts. Never prints a
secret or a full account number."""
from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()
FIXTURE_ORG_PREFIXES = ("99000000-",)


async def main() -> None:
    dsn, how = await resolve_dsn("DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP")
    conn = await connect(dsn)
    print("[INFO] as", await conn.fetchval("SELECT current_user"), "via", how)
    rows = await conn.fetch("""
        SELECT a.id, a.org_id, a.account_number_masked, a.custodian_code, a.registration_type, a.tax_status,
               a.household_id, a.is_discretionary, a.is_held_away, a.service_model, a.closed_on, o.is_test
        FROM public.accounts a JOIN public.organizations o ON o.id = a.org_id
        WHERE a.valid_to IS NULL AND a.system_to IS NULL ORDER BY o.is_test, a.created_at""")
    real = [r for r in rows if not r["is_test"]]
    print(f"[INFO] live accounts: {len(rows)} total, {len(real)} in non-test orgs")
    for r in real:
        print("  REAL", str(r["id"])[:8], r["org_id"], r["account_number_masked"], r["custodian_code"],
              r["registration_type"], r["tax_status"], "hh" if r["household_id"] else "no-hh",
              "disc" if r["is_discretionary"] else "NONDISC", "HELD_AWAY" if r["is_held_away"] else "integrated",
              repr(r["service_model"]), r["closed_on"])
    print("[INFO] test-org accounts by org:",
          dict((str(k), v) for k, v in __import__("collections").Counter(str(r["org_id"]) for r in rows if r["is_test"]).items()))
    print("[INFO] custodian codes:", [dict(r) for r in await conn.fetch(
        "SELECT custodian_code, is_held_away, is_discretionary, count(*) FROM public.accounts WHERE valid_to IS NULL "
        "AND system_to IS NULL GROUP BY 1,2,3 ORDER BY 1")])
    print("[INFO] instructions on held-away accounts:")
    for r in await conn.fetch("""
        SELECT i.id, i.org_id, i.status, i.executor_type, i.source, i.routed_via, i.maker_id, i.created_at,
               a.account_number_masked, o.is_test
        FROM public.execution_instructions i JOIN public.accounts a ON a.id = i.account_id
        JOIN public.organizations o ON o.id = i.org_id
        WHERE a.is_held_away AND a.valid_to IS NULL AND a.system_to IS NULL AND i.system_to IS NULL"""):
        print("  ", dict(r))
    print("[INFO] instructions (non-test) by status:", [dict(r) for r in await conn.fetch(
        "SELECT i.status, i.executor_type, count(*) FROM public.execution_instructions i JOIN public.organizations o "
        "ON o.id = i.org_id WHERE NOT o.is_test AND i.system_to IS NULL GROUP BY 1,2")])
    print("[INFO] links (non-test):", [dict(r) for r in await conn.fetch(
        "SELECT l.status, l.advice_only, count(*) FROM public.account_model_links l JOIN public.organizations o "
        "ON o.id = l.org_id WHERE NOT o.is_test AND l.valid_to IS NULL AND l.system_to IS NULL GROUP BY 1,2")])
    print("[INFO] proposals (non-test):", [dict(r) for r in await conn.fetch(
        "SELECT p.status, p.trigger, count(*) FROM public.rebalance_proposals p JOIN public.organizations o "
        "ON o.id = p.org_id WHERE NOT o.is_test AND p.system_to IS NULL GROUP BY 1,2")])
    print("[INFO] households (non-test):", await conn.fetchval(
        "SELECT count(*) FROM public.households h JOIN public.organizations o ON o.id = h.org_id WHERE NOT o.is_test"))
    print("[INFO] household columns:", [r[0] for r in await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='households' "
        "ORDER BY ordinal_position")])
    for t in ("execution_instructions", "rebalance_proposals", "accounts", "account_model_links"):
        print(f"[INFO] {t} CHECKs:")
        for r in await conn.fetch("SELECT conname, pg_get_constraintdef(oid) d FROM pg_constraint WHERE "
                                  "conrelid = ('public.' || $1)::regclass AND contype IN ('c','f','u')", t):
            print("    ", r["conname"], "|", r["d"][:220])
        for r in await conn.fetch("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public' AND "
                                  "tablename=$1 AND indexdef ILIKE '%UNIQUE%'", t):
            print("    UQ", r["indexname"], "|", r["indexdef"][:220])
        for r in await conn.fetch("SELECT tgname FROM pg_trigger WHERE tgrelid = ('public.' || $1)::regclass AND NOT "
                                  "tgisinternal", t):
            print("    TRG", r["tgname"])
    print("[INFO] holiday/calendar tables:", [r[0] for r in await conn.fetch(
        "SELECT table_schema || '.' || table_name FROM information_schema.tables WHERE table_name ILIKE ANY "
        "(ARRAY['%holiday%','%calendar%','%business_day%'])")])
    print("[INFO] permissions like authority/held_away:", [r[0] for r in await conn.fetch(
        "SELECT name FROM public.permissions WHERE name ILIKE '%authority%' OR name ILIKE '%held_away%'")])
    print("[INFO] human task types registered in DB?:", [dict(r) for r in await conn.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name ILIKE '%task%'")])
    await conn.close()


asyncio.run(main())
