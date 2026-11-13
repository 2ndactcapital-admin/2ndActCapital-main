"""DAILYREBAL1 — apply docs/dailyrebal1_part1.sql (the daily rebalancer's day row + the 'scheduled' trigger) and prove
it landed.

    python3 apps/api/scripts/_apply_dailyrebal1.py --schema     # the migration, one transaction (operator DSN)
    python3 apps/api/scripts/_apply_dailyrebal1.py --verify     # read the catalog back
    python3 apps/api/scripts/_apply_dailyrebal1.py --all        # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``: DDL needs the table owner).
Idempotent. Never prints a secret. Writes NO proposal, account, org or setting row.
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

SCHEMA_SQL = REPO / "docs" / "dailyrebal1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TABLE = "rebalance_schedule_days"
TRIGGERS = ("join", "model_change", "drift", "cash_flow", "manual", "harvest", "cash_waterfall", "scheduled")


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
        before = await conn.fetchval("SELECT count(*) FROM public.rebalance_proposals")
        async with conn.transaction():
            await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        after = await conn.fetchval("SELECT count(*) FROM public.rebalance_proposals")
        print(f"[OK] schema applied (rebalance_proposals rows {before} → {after})")
    finally:
        await conn.close()


async def verify() -> int:
    conn = await _conn()
    gaps: list[str] = []

    def need(ok: bool, label: str, detail: str = "") -> None:
        print(f"{'[OK]  ' if ok else '[GAP] '}{label}" + (f" — {detail}" if detail else ""))
        if not ok:
            gaps.append(label)

    try:
        rls = await conn.fetchval("SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = "
                                  "c.relnamespace WHERE n.nspname = 'public' AND c.relname = $1", TABLE)
        need(rls is True, f"public.{TABLE} exists with RLS enabled", f"{rls}")
        pol = {r["cmd"] for r in await conn.fetch(
            "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = $1", TABLE)}
        need(pol == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{TABLE}: a policy for every operation", f"{sorted(pol)}")
        quals = [r["q"] or "" for r in await conn.fetch(
            "SELECT coalesce(qual, with_check) q FROM pg_policies WHERE schemaname = 'public' AND tablename = $1",
            TABLE)]
        need(all("NULLIF" in q and "current_org_id" in q for q in quals), f"{TABLE}: every policy NULLIFs the org GUC")
        priv = await conn.fetchval(
            "SELECT has_table_privilege('app_service', $1, 'SELECT,INSERT,UPDATE,DELETE')", f"public.{TABLE}")
        need(priv is True, f"{TABLE}: app_service holds the four privileges")
        idx = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE indexname = 'rebalance_schedule_days_day_uq'")
        need("UNIQUE" in (idx or "") and "(org_id, run_day)" in (idx or ""), f"{TABLE}: unique (org_id, run_day)",
             idx or "")
        chk = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = "
                                  "'public.rebalance_proposals'::regclass AND conname = 'rebalance_proposals_trigger_chk'")
        need(chk is not None and all(f"'{t}'" in chk for t in TRIGGERS),
             "rebalance_proposals_trigger_chk names every trigger incl. 'scheduled'", chk or "")
        n = await conn.fetchval(f"SELECT count(*) FROM public.{TABLE}")
        print(f"[INFO] public.{TABLE}: {n} rows")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


def main() -> int:
    args = sys.argv[1:]
    if "--all" in args:
        asyncio.run(apply_schema())
        return asyncio.run(verify())
    if "--schema" in args:
        asyncio.run(apply_schema())
        return 0
    if "--verify" in args:
        return asyncio.run(verify())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
