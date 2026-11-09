"""CASH04 — apply docs/cash04_part1.sql, prove it landed, and (operator only) seed the waterfall job INACTIVE.

    python3 apps/api/scripts/_apply_cash04.py --schema      # docs/cash04_part1.sql, one transaction
    python3 apps/api/scripts/_apply_cash04.py --verify      # read it back from the catalog
    python3 apps/api/scripts/_apply_cash04.py --all         # both (what the sprint ran)
    python3 apps/api/scripts/_apply_cash04.py --seed-jobs   # OPERATOR: the platform job, trigger INACTIVE

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table owner). Idempotent.
Never prints a secret. --schema / --verify write no row. --seed-jobs writes the job's system definition and ONE
scheduled trigger in the Hollisworks platform org, switched OFF (a Super Admin switches it on in Admin → Security
master, BUGFIX4's switch) — the sprint does NOT run it.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()

SCHEMA_SQL = REPO / "docs" / "cash04_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
JOB_KEY = "cash_maturity_waterfall"
CHECKS = {
    "public.rebalance_proposals": {
        "rebalance_proposals_kind_chk": ("'cash'::text", "cash_policy_id", "'cash_waterfall'::text", "period"),
        "rebalance_proposals_trigger_chk": ("'cash_waterfall'::text",),
    },
    "public.rebalance_proposal_trades": {
        "rebalance_proposal_trades_cash_leg_chk": ("sleeve", "price_kind", "limit_price"),
    },
}
INDEX = "rebalance_proposals_cash_waterfall_uq"


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
    bad = []
    try:
        for table, checks in CHECKS.items():
            defs = {r["conname"]: r["d"] for r in await conn.fetch(
                "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conrelid = $1::regclass "
                "AND conname = ANY($2::text[])", table, list(checks))}
            for name, markers in checks.items():
                d = defs.get(name)
                if d is None:
                    bad.append(f"{name}: absent")
                    continue
                miss = [m for m in markers if m not in d]
                if miss:
                    bad.append(f"{name}: lacks {miss}")
        col = await conn.fetchval("SELECT data_type FROM information_schema.columns WHERE table_schema = 'public' AND "
                                  "table_name = 'rebalance_proposal_trades' AND column_name = 'cash_leg'")
        if col != "jsonb":
            bad.append(f"rebalance_proposal_trades.cash_leg: {col!r}")
        idx = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE indexname = $1", INDEX)
        if not idx or "UNIQUE" not in idx or "cash_waterfall" not in idx:
            bad.append(f"{INDEX}: {idx!r}")
        ex = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                                 "'execution_instructions_side_chk'")
        if not ex or "'buy'::text" not in ex:
            bad.append("execution_instructions_side_chk unreadable")
        print(f"[INFO] execution_instructions_side_chk unchanged: {ex}")
    finally:
        await conn.close()
    print("SCHEMA OK" if not bad else f"SCHEMA PROBLEMS: {bad}")
    return 0 if not bad else 1


async def seed_jobs() -> None:
    from services import cash_waterfall  # noqa: F401 — the job spec is registered in security_master_jobs.JOBS
    from services import security_master_jobs as J

    conn = await _conn()
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            created_by = await J.platform_job_owner(conn)
            out = await J.ensure_platform_jobs(conn, org_id=J.PLATFORM_ORG_ID, created_by=created_by,
                                               jobs={JOB_KEY: J.JOBS[JOB_KEY]}, active=False)
        for k, v in out.items():
            print(f"[OK] {k}: definition {v['definition_id']} trigger {v['trigger_id']} "
                  f"({'created, trigger INACTIVE' if v['created'] else 'already present'})")
    finally:
        await conn.close()


async def main(argv: list[str]) -> int:
    if "--schema" in argv or "--all" in argv:
        await apply_schema()
    rc = 0
    if "--verify" in argv or "--all" in argv:
        rc = await verify_schema()
    if "--seed-jobs" in argv:
        await seed_jobs()
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
