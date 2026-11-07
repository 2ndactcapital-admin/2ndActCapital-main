"""S28 — apply docs/s28_part1.sql, seed the AI drift monitor platform job (INACTIVE), prove both landed.

    python3 apps/api/scripts/_apply_s28.py --schema     # the migration, one transaction (operator DSN)
    python3 apps/api/scripts/_apply_s28.py --seed-jobs  # "system:AI — drift monitor", trigger OFF
    python3 apps/api/scripts/_apply_s28.py --verify     # read everything back from the catalog / tables
    python3 apps/api/scripts/_apply_s28.py --all        # all three, in order

The job is seeded with its trigger INACTIVE and this script never switches it on: the Super Admin does, on the
Security master screen (Platform jobs — BUGFIX4's audited switch), after the branch is deployed. Runs on the operator's
postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``: DDL needs the table owner). Idempotent. Never prints a
secret. Writes no ai_decision_log row and no alert.
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

SCHEMA_SQL = REPO / "docs" / "s28_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
JOB_KEY = "ai_drift_monitor"
TABLES = ("ai_drift_thresholds", "ai_drift_alerts", "ai_drift_alert_events")


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


async def seed_jobs() -> None:
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


async def verify() -> int:
    from services import ai_drift as AD
    from services import security_master_jobs as J

    conn = await _conn()
    gaps: list[str] = []

    def need(ok: bool, label: str, detail: str = "") -> None:
        print(f"{'[OK]  ' if ok else '[GAP] '}{label}" + (f" — {detail}" if detail else ""))
        if not ok:
            gaps.append(label)

    try:
        for t in TABLES:
            pol = {r["cmd"] for r in await conn.fetch("SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                      "tablename = $1", t)}
            need(pol == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(pol)}")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"public.{t}")
            need(rls is True, f"{t}: RLS enabled")
        for ix in ("ai_drift_thresholds_live_uq", "ai_drift_alerts_open_uq"):
            d = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE indexname = $1", ix) or ""
            need("UNIQUE" in d and "WHERE" in d, f"{ix}: a partial unique index", d or "missing")
        live = {r["rule_key"]: r for r in await conn.fetch(
            "SELECT rule_key, threshold, min_recent, min_baseline, enabled FROM public.ai_drift_thresholds WHERE "
            "task_type IS NULL AND valid_to IS NULL")}
        for k, d in AD.DEFAULT_THRESHOLDS.items():
            r = live.get(k)
            need(bool(r) and r["threshold"] == d["threshold"] and r["min_recent"] == d["min_recent"]
                 and r["min_baseline"] == d["min_baseline"] and r["enabled"] is d["enabled"],
                 f"platform default {k}", f"{dict(r) if r else None}")
        spec = J.JOBS[JOB_KEY]
        row = await conn.fetchrow(
            "SELECT t.is_active, t.schedule_cron, t.timezone FROM workflow_definitions d JOIN workflow_triggers t ON "
            "t.workflow_definition_id = d.id AND t.trigger_type = 'scheduled' WHERE d.org_id = $1 AND d.name = $2",
            J.PLATFORM_ORG_ID, spec.name)
        need(bool(row and row["is_active"] is False and row["schedule_cron"] == spec.cron),
             f"{spec.name} seeded in the platform org, trigger OFF", f"{dict(row) if row else None}")
    finally:
        await conn.close()
    print("SCHEMA OK" if not gaps else f"GAPS: {gaps}")
    return 0 if not gaps else 1


async def main() -> int:
    args = set(sys.argv[1:])
    if not args & {"--schema", "--seed-jobs", "--verify", "--all"}:
        print(__doc__)
        return 2
    if args & {"--schema", "--all"}:
        await apply_schema()
    if args & {"--seed-jobs", "--all"}:
        await seed_jobs()
    if args & {"--verify", "--all"}:
        return await verify()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
