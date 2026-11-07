"""PRICES1 — apply docs/prices1_part1.sql, seed the prices platform job (INACTIVE), prove both landed.

    python3 apps/api/scripts/_apply_prices1.py --schema     # the migration, one transaction (operator DSN)
    python3 apps/api/scripts/_apply_prices1.py --seed-jobs  # "system:Security data — prices", trigger OFF
    python3 apps/api/scripts/_apply_prices1.py --verify     # read everything back from the catalog / tables
    python3 apps/api/scripts/_apply_prices1.py --all        # all three, in order

The job is seeded with its trigger INACTIVE and this script never switches it on: the Super Admin does, on the
Security master screen (BUGFIX4's audited switch), after the branch is deployed. Runs on the operator's postgres
DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``: DDL needs the table owner). Idempotent. Never prints a secret.
Writes NO price, security or client-org row.
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

SCHEMA_SQL = REPO / "docs" / "prices1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
JOB_KEY = "security_data_prices"
QUALITY_KINDS = ("stale", "gap", "outlier", "disagreement", "budget_exhausted", "invalid_value", "currency_mismatch")


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
    from services import security_master_jobs as J
    from services.security_data.providers import build_providers
    from services.security_data.providers.eod import contract_problems

    conn = await _conn()
    gaps: list[str] = []

    def need(ok: bool, label: str, detail: str = "") -> None:
        print(f"{'[OK]  ' if ok else '[GAP] '}{label}" + (f" — {detail}" if detail else ""))
        if not ok:
            gaps.append(label)

    async def condef(name: str) -> str:
        return await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = $1", name) or ""

    try:
        cols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'portfolio' AND table_name = "
            "'securities_global_prices'")}
        need({"adjustment_factor", "bar"} <= cols, "price store: adjustment_factor + bar columns", f"{sorted(cols)}")
        d = await condef("data_quality_events_kind_chk")
        need(all(f"'{k}'" in d for k in QUALITY_KINDS), "data_quality_events_kind_chk admits every kind (old + new)", d)
        pol = {r["cmd"] for r in await conn.fetch("SELECT cmd FROM pg_policies WHERE schemaname = 'portfolio' AND "
                                                  "tablename = 'security_price_coverage'")}
        need(pol == {"SELECT", "INSERT", "UPDATE", "DELETE"}, "security_price_coverage: a policy for every operation",
             f"{sorted(pol)}")
        idx = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE indexname = 'security_price_coverage_live_uq'")
        need("UNIQUE" in (idx or ""), "one live coverage row per security", idx or "missing")
        code = build_providers()
        for key, status in (("yahoo_chart", "active"), ("tiingo", "not_connected")):
            p = await conn.fetchrow("SELECT * FROM portfolio.data_providers WHERE key = $1 AND valid_to IS NULL AND "
                                    "system_to IS NULL", key)
            spec = code[key].spec
            need(p is not None and p["status"] == status and p["capabilities"] == ["eod_price"]
                 and p["lic_internal_use"] is spec.licence.internal_use
                 and p["lic_member_display"] == spec.licence.member_display
                 and p["lic_redistribution"] is spec.licence.redistribution and p["attribution"] and p["terms_note"]
                 and p["confirmed_on"] and p["daily_call_budget"],
                 f"provider {key} ({status}) with the licence facts its adapter declares")
            need(contract_problems(code[key]) == [], f"{key}: the adapter passes the static contract checks",
                 f"{contract_problems(code[key])}")
        rank = [r["provider_key"] for r in await conn.fetch(
            "SELECT provider_key FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = "
            "'eod_price' AND valid_to IS NULL AND system_to IS NULL ORDER BY rank")]
        need(rank == ["custodian", "csv_upload", "adviser_entry", "platform_entry", "yahoo_chart", "tiingo"],
             "platform rank for eod_price: the four of marketdata1, then yahoo_chart, then tiingo", f"{rank}")
        spec = J.JOBS[JOB_KEY]
        row = await conn.fetchrow(
            "SELECT d.id, v.id AS vid, t.schedule_cron, t.timezone, t.is_active FROM workflow_definitions d JOIN "
            "workflow_versions v ON v.workflow_definition_id = d.id AND v.is_current LEFT JOIN workflow_triggers t ON "
            "t.workflow_definition_id = d.id AND t.trigger_type = 'scheduled' WHERE d.org_id = $1 AND d.name = $2",
            J.PLATFORM_ORG_ID, spec.name)
        need(row is not None and row["schedule_cron"] == spec.cron and row["timezone"] == spec.timezone,
             f"platform job {spec.name} seeded with its schedule ({spec.cron} {spec.timezone})")
        need(row is not None and row["is_active"] is False, f"{spec.name}: trigger OFF (the operator decides)",
             "ACTIVE" if row is not None and row["is_active"] else "inactive")
        if row is not None:
            steps = [r["action_registry_key"] for r in await conn.fetch(
                "SELECT action_registry_key FROM workflow_steps WHERE workflow_version_id = $1", row["vid"])]
            need(steps == ["security_data.prices"], f"{spec.name} steps", f"{steps}")
        n = await conn.fetchval("SELECT count(*) FROM portfolio.securities_global_prices")
        print(f"[INFO] price rows in the store now: {n} (the operator's backfill adds them)")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


def main() -> int:
    args = sys.argv[1:]
    if "--all" in args:
        asyncio.run(apply_schema())
        asyncio.run(seed_jobs())
        return asyncio.run(verify())
    if "--schema" in args:
        asyncio.run(apply_schema())
        return 0
    if "--seed-jobs" in args:
        asyncio.run(seed_jobs())
        return 0
    if "--verify" in args:
        return asyncio.run(verify())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
