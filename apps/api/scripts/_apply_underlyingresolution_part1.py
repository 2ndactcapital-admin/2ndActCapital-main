"""One-shot applier for docs/underlyingresolution_part1.sql.

Not part of the sprint deliverable — a local convenience because there is no
human to paste the SQL into the Supabase console. Idempotent: every statement in
that file is IF NOT EXISTS / DROP-then-CREATE.
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
    sql = open(os.path.join(ROOT, "docs/underlyingresolution_part1.sql")).read()
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    print("current_user:", await conn.fetchval("SELECT current_user"))
    await conn.execute(sql)
    print("APPLIED OK")

    cols = await conn.fetch(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema='portfolio' AND table_name='securities_global_relationships' "
        "AND column_name IN ('proposed_global_security_id','proposal_confidence',"
        "'proposal_kind','proposal_hint','proposed_at','normalized_underlying_text',"
        "'resolved_by','resolved_at') ORDER BY column_name"
    )
    print("new cols:", [(c["column_name"], c["data_type"]) for c in cols])

    cons = await conn.fetch(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid='portfolio.securities_global_relationships'::regclass "
        "AND conname LIKE 'sec_global_rel_%' ORDER BY conname"
    )
    print("constraints:", [c["conname"] for c in cons])

    print("trigger:", await conn.fetchval(
        "SELECT tgname FROM pg_trigger "
        "WHERE tgrelid='portfolio.securities_global_relationships'::regclass "
        "AND tgname='trg_sec_global_rel_confirm_gate'"
    ))
    print("index-name uq index:", await conn.fetchval(
        "SELECT indexname FROM pg_indexes WHERE schemaname='portfolio' "
        "AND indexname='uq_sec_global_active_index_name'"
    ))

    dist = await conn.fetch(
        "SELECT link_state, count(*) FROM portfolio.securities_global_relationships "
        "GROUP BY 1 ORDER BY 2 DESC"
    )
    print("link_state after migration:", [(r[0], r[1]) for r in dist])
    await conn.close()


asyncio.run(main())
