"""SECMASTER2 — apply docs/secmaster2_part1.sql, seed the mutual-fund platform job (INACTIVE), prove both landed.

    python3 apps/api/scripts/_apply_secmaster2.py --schema     # the migration, one transaction (operator DSN)
    python3 apps/api/scripts/_apply_secmaster2.py --seed-jobs  # "system:Security data — mutual funds", trigger OFF
    python3 apps/api/scripts/_apply_secmaster2.py --verify     # read everything back from the catalog / tables
    python3 apps/api/scripts/_apply_secmaster2.py --all        # all three, in order

The job is seeded with its trigger INACTIVE and this script never switches it on: the Super Admin does, on the
Security master screen (BUGFIX4's audited switch), after the branch is deployed. Runs on the operator's postgres
DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``: DDL needs the table owner). Idempotent. Never prints a secret.
Writes NO security, identifier, attribute or client-org row.
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

SCHEMA_SQL = REPO / "docs" / "secmaster2_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
JOB_KEY = "security_master_mutual_funds"
ID_TYPES = ("cusip", "isin", "ticker", "sedol", "figi", "lei", "internal", "sec_series_id", "sec_class_id")
NEW_FIELDS = ("fund_category_group", "fund_structure")


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
    from services import security_master_load as M

    conn = await _conn()
    gaps: list[str] = []

    def need(ok: bool, label: str, detail: str = "") -> None:
        print(f"{'[OK]  ' if ok else '[GAP] '}{label}" + (f" — {detail}" if detail else ""))
        if not ok:
            gaps.append(label)

    async def condef(name: str) -> str:
        return await conn.fetchval("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = $1", name) or ""

    try:
        d = await condef("sec_global_ident_type_chk")
        need(all(f"'{t}'" in d for t in ID_TYPES), "sec_global_ident_type_chk admits every earlier type + SEC ids", d)
        d = await condef("sec_global_ident_sec_shape_chk")
        need("S[0-9]{9}" in d and "C[0-9]{9}" in d, "SEC series / class id shape CHECK", d)
        idx = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_sec_global_ident_sec_class'")
        need("UNIQUE" in (idx or "") and "sec_class_id" in (idx or "") and "valid_to IS NULL" in (idx or ""),
             "partial unique index on the live sec_class_id", idx or "missing")
        d = await condef("security_reference_attributes_field_chk")
        need(all(f"'{f}'" in d for f in M.REFERENCE_FIELDS + ("absent_runs",)),
             "security_reference_attributes_field_chk admits every field the code writes", d[:200])
        d = await condef("security_reference_attributes_structure_chk")
        need(all(f"'{v}'" in d for v in ("mutual_fund", "money_market", "etf")), "fund_structure values CHECK", d)
        p = await conn.fetchrow("SELECT * FROM portfolio.data_providers WHERE key = 'sec_mutual_funds' AND "
                                "valid_to IS NULL AND system_to IS NULL")
        need(p is not None and p["capabilities"] == ["reference"] and p["status"] == "active" and p["lic_internal_use"]
             and p["lic_member_display"] == "allowed" and p["lic_redistribution"] and p["attribution"]
             and p["confirmed_on"] and "User-Agent" in (p["terms_note"] or ""),
             "provider sec_mutual_funds with its licence facts (public record, fair-access rule)")
        rank = [r["provider_key"] for r in await conn.fetch(
            "SELECT provider_key FROM portfolio.data_source_rank WHERE owner_scope = 'platform' AND data_type = "
            "'reference' AND valid_to IS NULL AND system_to IS NULL ORDER BY rank")]
        need(rank[-1:] == ["sec_mutual_funds"] and rank[:5] == ["nasdaq_trader", "sec_tickers", "openfigi",
                                                                "sec_13f_list", "finance_database"],
             "platform rank for 'reference': the five of SECMASTER1, then sec_mutual_funds", f"{rank}")
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
            need(steps == ["security_master.mutual_funds"], f"{spec.name} steps", f"{steps}")
        n = await conn.fetchval("SELECT count(*) FROM portfolio.securities_global_identifiers WHERE id_type IN "
                                "('sec_series_id', 'sec_class_id')")
        print(f"[INFO] SEC series / class identifiers in the master now: {n} (the operator's load adds them)")
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
