"""SECMASTER1 — apply the security-master schema, seed the platform jobs, prove both landed.

    python3 apps/api/scripts/_apply_secmaster1.py --schema     # docs/secmaster1_part1.sql, one transaction
    python3 apps/api/scripts/_apply_secmaster1.py --seed-jobs  # the four platform system workflows + triggers
    python3 apps/api/scripts/_apply_secmaster1.py --verify     # read both back from the catalog / tables
    python3 apps/api/scripts/_apply_secmaster1.py --all        # all three, in order
    python3 apps/api/scripts/_apply_secmaster1.py --activate-jobs   # OPERATOR, AFTER the branch is deployed

THE TRIGGERS ARE SEEDED INACTIVE. The Render scheduler (2ndactcapital-workflow-scheduler) runs the deployed code;
until this branch is merged and deployed its action registry has no security_master.* verbs, and the engine marks
a Service Task whose action does not resolve as COMPLETED having invoked nothing — a scheduled job would look green
every night while doing nothing. --activate-jobs (after deploy) sets the four triggers active; --verify reports
their state.

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table
owner; the Supabase MCP cancels migration text containing DROP). Idempotent. Never prints a secret.

THE MIGRATION WRITES NO SECURITY, IDENTIFIER OR ORG ROW. --seed-jobs writes, in the Hollisworks platform org only,
four ``system:security_master:*`` / ``system:security_data:*`` workflow definitions (version 1, service-task steps)
and one scheduled trigger each, created by that org's Super Admin (so a held run alerts a Super Admin).
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

SCHEMA_SQL = REPO / "docs" / "secmaster1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

NEW_TABLES = ("portfolio.security_reference_attributes", "portfolio.security_master_source_files",
              "portfolio.sec_13f_list", "portfolio.security_master_review", "portfolio.openfigi_mappings",
              "portfolio.security_master_checkpoints")
READ_BY_ALL = ("portfolio.security_reference_attributes", "portfolio.security_master_source_files",
               "portfolio.sec_13f_list")
PARTIAL_UNIQUES = ("security_reference_attributes_live_uq", "security_master_source_files_complete_uq",
                   "sec_13f_list_live_uq", "security_master_review_open_uq", "security_master_checkpoints_uq")
TRGM = ("idx_sec_global_name_trgm", "idx_sec_global_short_name_trgm", "idx_sec_global_ident_value_trgm")
PROVIDERS = ("nasdaq_trader", "sec_tickers", "openfigi", "sec_13f_list", "finance_database")


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
            out = await J.ensure_platform_jobs(conn, org_id=J.PLATFORM_ORG_ID, created_by=created_by, active=False)
        for k, v in out.items():
            print(f"[OK] {k}: definition {v['definition_id']} trigger {v['trigger_id']} "
                  f"({'created' if v['created'] else 'already present'})")
    finally:
        await conn.close()


def SECMASTER1_JOBS(J):
    """SECMASTER2 — the four jobs THIS script seeds and activates. J.JOBS also holds later jobs (the mutual-fund job,
    seeded inactive by _apply_secmaster2.py) which the operator switches on separately, on the Security master
    screen — --activate-jobs must never switch them on as a side effect."""
    return [j for k, j in J.JOBS.items() if k in ("security_master_nightly", "security_master_13f",
                                                  "security_master_attributes", "security_data_nightly")]


async def activate_jobs() -> None:
    from services import security_master_jobs as J

    conn = await _conn()
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            n = await conn.execute(
                "UPDATE workflow_triggers t SET is_active = true FROM workflow_definitions d WHERE "
                "d.id = t.workflow_definition_id AND d.org_id = $1 AND d.name = ANY($2::text[]) AND "
                "t.trigger_type = 'scheduled' AND NOT t.is_active", J.PLATFORM_ORG_ID, [j.name for j in SECMASTER1_JOBS(J)])
        print(f"[OK] activated: {n}")
    finally:
        await conn.close()


async def verify() -> int:
    from services import security_master_jobs as J

    conn = await _conn()
    gaps: list[str] = []

    def need(ok: bool, label: str, detail: str = "") -> None:
        print(f"{'[OK]  ' if ok else '[GAP] '}{label}" + (f" — {detail}" if detail else ""))
        if not ok:
            gaps.append(label)

    try:
        for full in NEW_TABLES:
            schema, t = full.split(".")
            cols = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = $1 AND table_name = $2",
                schema, t)]
            need(bool(cols), f"{full} exists", f"{len(cols)} columns")
            need(all(c in cols for c in ("org_id", "owner_scope", "valid_from", "valid_to", "system_from",
                                         "system_to")), f"{full} carries owner_scope/org_id and both temporal axes")
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", full)
            need(rls is True, f"{full} has RLS enabled")
            pol = await conn.fetch("SELECT cmd, qual, with_check FROM pg_policies WHERE schemaname = $1 "
                                   "AND tablename = $2", schema, t)
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= {r["cmd"] for r in pol},
                 f"{full}: a policy for SELECT/INSERT/UPDATE/DELETE", f"{sorted(r['cmd'] for r in pol)}")
            writes = [(r["qual"] or "") + (r["with_check"] or "") for r in pol if r["cmd"] != "SELECT"]
            need(len(writes) == 3 and all("app.is_super_admin" in w and "NULLIF" in w for w in writes),
                 f"{full}: every write policy requires app.is_super_admin (NULLIF)")
            sel = " ".join((r["qual"] or "") for r in pol if r["cmd"] == "SELECT")
            if full in READ_BY_ALL:
                need("org_id IS NULL" in sel, f"{full}: platform rows readable by every org", sel[:120])
            else:
                need("app.is_super_admin" in sel and "org_id IS NULL" not in sel,
                     f"{full}: read only under app.is_super_admin", sel[:120])
            grants = {r["privilege_type"] for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants WHERE table_schema = $1 "
                "AND table_name = $2 AND grantee = 'app_service'", schema, t)}
            need({"SELECT", "INSERT", "UPDATE", "DELETE"} <= grants, f"app_service grants on {full}")
        idx = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1::text[])",
            list(PARTIAL_UNIQUES + TRGM))}
        for name in PARTIAL_UNIQUES:
            d = idx.get(name, "")
            need("UNIQUE" in d and "WHERE" in d, f"partial unique index {name}", d[:140])
        for name in TRGM:
            need("gin_trgm_ops" in idx.get(name, ""), f"trigram index {name}")
        chk = await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = "
                                  "'data_source_rank_type_chk'")
        need("reference" in (chk or "") and "factor_score" in (chk or ""),
             "data_source_rank_type_chk admits 'reference' and keeps every earlier type")
        rows = {r["key"]: dict(r) for r in await conn.fetch(
            "SELECT * FROM portfolio.data_providers WHERE key = ANY($1::text[]) AND valid_to IS NULL "
            "AND system_to IS NULL", list(PROVIDERS))}
        for k in PROVIDERS:
            p = rows.get(k)
            need(p is not None and p["capabilities"] == ["reference"] and p["terms_note"] and p["confirmed_on"]
                 and p["attribution"], f"provider {k} with licence facts")
        need(all(not rows[k]["lic_redistribution"] for k in ("finance_database", "sec_13f_list", "openfigi")
                 if k in rows), "FinanceDatabase / 13(f) CUSIPs / OpenFIGI: no redistribution right")
        need(rows.get("sec_13f_list", {}).get("lic_member_display") == "own_holdings_only",
             "13(f) CUSIPs shown to members only for their own holdings")
        rank = [r["provider_key"] for r in await conn.fetch(
            "SELECT provider_key FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = "
            "'reference' AND valid_to IS NULL AND system_to IS NULL ORDER BY rank")]
        need(rank == list(PROVIDERS), "platform rank for 'reference'", f"{rank}")
        for key, spec in J.JOBS.items():
            row = await conn.fetchrow(
                "SELECT d.id, d.name, v.id AS vid, t.schedule_cron, t.timezone, t.is_active, t.created_by "
                "FROM workflow_definitions d JOIN workflow_versions v ON v.workflow_definition_id = d.id "
                "AND v.is_current LEFT JOIN workflow_triggers t ON t.workflow_definition_id = d.id AND "
                "t.trigger_type = 'scheduled' WHERE d.org_id = $1 AND d.name = $2", J.PLATFORM_ORG_ID, spec.name)
            need(row is not None and row["schedule_cron"] == spec.cron and row["timezone"] == spec.timezone,
                 f"platform job {spec.name} with its schedule ({spec.cron} {spec.timezone})",
                 "trigger ACTIVE" if row is not None and row["is_active"] else
                 "trigger inactive until --activate-jobs (after deploy)")
            if row is not None:
                steps = [r["action_registry_key"] for r in await conn.fetch(
                    "SELECT action_registry_key FROM workflow_steps WHERE workflow_version_id = $1 ORDER BY step_key",
                    row["vid"])]
                need(sorted(steps) == sorted(s.action for s in spec.steps), f"{spec.name} steps", f"{steps}")
        live = await conn.fetchval("SELECT count(*) FROM portfolio.securities_global WHERE valid_to IS NULL AND "
                                   "system_to IS NULL")
        print(f"[INFO] live securities now: {live} (the migration wrote none; the operator's load fills them)")
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
    if "--activate-jobs" in args:
        asyncio.run(activate_jobs())
        return asyncio.run(verify())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
