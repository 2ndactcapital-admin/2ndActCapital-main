"""AIBUDGET1 — apply the budget / usage / daily-run schema and prove it landed.

    python3 apps/api/scripts/_apply_aibudget1.py --schema    # docs/aibudget1_part1.sql, one transaction
    python3 apps/api/scripts/_apply_aibudget1.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_aibudget1.py --all       # pre-check, apply, verify

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner). Idempotent. Never prints a secret.

Before applying (--all), the pre-check lists every view / function that reads
ai_ensemble_configs, because adding columns to a table with a trigger has broken
a live function once in this project (SPRINT_WORKFLOW_STANDARD, Part 1). The
new columns are NOT NULL only with a DEFAULT, and the retire-only trigger
compares to_jsonb rows, so it covers them unchanged.

Writes NO business rows.
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

SCHEMA_SQL = REPO / "docs" / "aibudget1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

BUDGET_COLUMNS = {
    "model_1_budget_kind": ("text", "NO"), "model_1_budget_value": ("numeric", "YES"),
    "model_2_budget_kind": ("text", "NO"), "model_2_budget_value": ("numeric", "YES"),
}
CONFIG_CHECKS = ("ai_ensemble_configs_budget_kind_chk", "ai_ensemble_configs_budget_value_null_chk",
                 "ai_ensemble_configs_budget_value_positive_chk", "ai_ensemble_configs_budget_tokens_whole_chk")
TABLES = ("ai_daily_usage", "ai_daily_runs")
TABLE_CHECKS = {
    "ai_daily_usage": ("ai_daily_usage_deployment_chk", "ai_daily_usage_nonneg_chk"),
    "ai_daily_runs": ("ai_daily_runs_task_chk", "ai_daily_runs_trigger_chk", "ai_daily_runs_status_chk",
                      "ai_daily_runs_finished_chk", "ai_daily_runs_budget_stop_chk", "ai_daily_runs_stop_kind_chk",
                      "ai_daily_runs_counts_chk", "ai_daily_runs_manual_by_chk"),
}
PARTIAL_UNIQUES = ("ai_daily_runs_one_scheduled_per_day", "ai_daily_runs_one_running_per_task")


async def _conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
    return conn


async def precheck() -> None:
    conn = await _conn()
    try:
        views = await conn.fetch(
            "SELECT DISTINCT v.oid::regclass::text AS v FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid "
            "JOIN pg_class v ON v.oid = r.ev_class WHERE d.refobjid = 'public.ai_ensemble_configs'::regclass "
            "AND v.oid <> 'public.ai_ensemble_configs'::regclass")
        print(f"[INFO] views over ai_ensemble_configs: {[r['v'] for r in views] or 'none'}")
        fns = await conn.fetch(
            "SELECT p.oid::regprocedure::text AS f FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND p.prosrc ILIKE '%ai_ensemble_configs%'")
        print(f"[INFO] functions naming ai_ensemble_configs: {[r['f'] for r in fns] or 'none'}")
        trg = await conn.fetch(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'public.ai_ensemble_configs'::regclass AND NOT tgisinternal")
        print(f"[INFO] triggers on ai_ensemble_configs: {[r['tgname'] for r in trg]}")
        print(f"[INFO] ai_ensemble_configs rows: {await conn.fetchval('SELECT count(*) FROM ai_ensemble_configs')}")
    finally:
        await conn.close()


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
        cols = {r["column_name"]: (r["data_type"], r["is_nullable"], r["column_default"]) for r in await conn.fetch(
            "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'ai_ensemble_configs'")}
        for c, (dt, nullable) in BUDGET_COLUMNS.items():
            got = cols.get(c)
            need(got is not None and got[0] == dt and got[1] == nullable,
                 f"ai_ensemble_configs.{c} {dt} nullable={nullable}", f"{got}")
        need((cols.get("model_1_budget_kind") or (None, None, ""))[2] is not None
             and "unlimited" in str(cols["model_1_budget_kind"][2]), "model_1_budget_kind defaults to 'unlimited'")
        cons = {r["conname"]: r["d"] for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint "
            "WHERE conrelid = 'public.ai_ensemble_configs'::regclass")}
        for c in CONFIG_CHECKS:
            need(c in cons, f"CHECK {c}", cons.get(c, "absent")[:160])
        fn = await conn.fetchval("SELECT pg_get_functiondef('public.ai_ensemble_configs_retire_only()'::regprocedure)")
        need(fn is not None and "to_jsonb(NEW)" in fn and "'is_active'" in fn and "'retired_at'" in fn,
             "the retire-only trigger still compares whole rows (to_jsonb minus is_active / retired_at) — the "
             "budget columns are immutable with it")
        trg = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgrelid = 'public.ai_ensemble_configs'"
                                  "::regclass AND tgname = 'ai_ensemble_configs_retire_only' AND tgenabled <> 'D'")
        need(int(trg) == 1, "trigger ai_ensemble_configs_retire_only present and enabled")

        for t in TABLES:
            tcols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
                "AND table_name = $1 ORDER BY ordinal_position", t)]
            need(bool(tcols), f"public.{t} exists", f"{len(tcols)} columns")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"public.{t}")
            need(rls is True, f"public.{t} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = 'public' "
                                   "AND tablename = $1", t)
            need({r["cmd"] for r in pol} == {"SELECT", "INSERT", "UPDATE", "DELETE"},
                 f"public.{t}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            need(all("is_super_admin" in ((r["qual"] or "") + (r["with_check"] or "")) for r in pol),
                 f"public.{t}: every policy requires app.is_super_admin")
            tcons = {r["conname"] for r in await conn.fetch(
                "SELECT conname FROM pg_constraint WHERE conrelid = $1::regclass", f"public.{t}")}
            for c in TABLE_CHECKS[t]:
                need(c in tcons, f"CHECK {c}")
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = 'public' "
                "AND table_name = $1 AND grantee = 'app_service'", t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service has S/I/U/D on public.{t}",
                 f"{sorted(grants)}")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'ai_daily_runs'")}
        for i in PARTIAL_UNIQUES:
            need(i in idx and "UNIQUE" in idx[i] and "WHERE" in idx[i], f"partial unique index {i}",
                 idx.get(i, "absent"))
        fks = {r["conname"] for r in await conn.fetch(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'public.ai_daily_runs'::regclass AND contype = 'f'")}
        need({"ai_daily_runs_ensemble_config_id_fkey", "ai_daily_runs_started_by_fkey"} <= fks,
             "ai_daily_runs FKs (ensemble config, started_by)", f"{sorted(fks)}")
        pk = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                                 "'ai_daily_usage_pkey'")
        need(pk == "PRIMARY KEY (budget_day, deployment)", "ai_daily_usage keyed by (budget_day, deployment)", f"{pk}")
    finally:
        await conn.close()
    print("SCHEMA OK" if not gaps else f"SCHEMA GAPS: {len(gaps)}")
    return 0 if not gaps else 1


async def main(argv: list[str]) -> int:
    if "--all" in argv:
        await precheck()
        await apply_schema()
        return await verify_schema()
    if "--schema" in argv:
        await apply_schema()
        return 0
    if "--precheck" in argv:
        await precheck()
        return 0
    return await verify_schema()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
