"""One-shot applier for docs/notetermsrouting_part1.sql. Not part of the sprint
deliverable — a local convenience because there is no human to paste the SQL
into the Supabase console. Idempotent: every statement in that file is
IF NOT EXISTS / DROP-then-CREATE.
"""
import asyncio
import os
import sys

ROOT = "/mnt/c/Users/Joe/2ndActCapital"
sys.path.insert(0, os.path.join(ROOT, "apps/api/venv/lib/python3.12/site-packages"))
sys.path.insert(0, os.path.join(ROOT, "apps/api"))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, "apps/api/.env"), override=False)


async def main():
    sql = open(os.path.join(ROOT, "docs/notetermsrouting_part1.sql")).read()
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    print("current_user:", await conn.fetchval("SELECT current_user"))
    await conn.execute(sql)
    print("APPLIED OK")
    print("table:", await conn.fetchval("SELECT to_regclass('portfolio.note_terms_stp_policy')"))
    cols = await conn.fetch(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='portfolio' AND table_name='securities_global_note_terms' "
        "AND column_name IN ('routing_decision','routed_at')"
    )
    print("new cols:", [dict(c) for c in cols])
    total = await conn.fetchval("SELECT count(*) FROM portfolio.securities_global_note_terms")
    nulls = await conn.fetchval(
        "SELECT count(*) FROM portfolio.securities_global_note_terms WHERE routing_decision IS NULL"
    )
    print(f"routing_decision IS NULL: {nulls} of {total}")
    pol = await conn.fetch(
        "SELECT polname, polcmd FROM pg_policy "
        "WHERE polrelid='portfolio.note_terms_stp_policy'::regclass ORDER BY polname"
    )
    print("policies:", [
        (r["polname"], r["polcmd"].decode() if isinstance(r["polcmd"], bytes) else r["polcmd"])
        for r in pol
    ])
    print("rls enabled:", await conn.fetchval(
        "SELECT relrowsecurity FROM pg_class WHERE oid='portfolio.note_terms_stp_policy'::regclass"
    ))
    await conn.close()


asyncio.run(main())
