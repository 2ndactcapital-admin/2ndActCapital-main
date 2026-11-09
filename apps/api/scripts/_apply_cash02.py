"""CASH02 — apply docs/cash02_part1.sql and prove it landed.

    python3 apps/api/scripts/_apply_cash02.py --schema    # docs/cash02_part1.sql, one transaction
    python3 apps/api/scripts/_apply_cash02.py --verify    # read it back from the catalog
    python3 apps/api/scripts/_apply_cash02.py --all       # both

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the table owner). Idempotent
(the guarded DROP of cash01's cash_coverage_minimums refuses to run while that table holds any row). Never prints a
secret. DDL + catalog rows only (the permission, its role grants, the SoD item type) — writes no data row.
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

SCHEMA_SQL = REPO / "docs" / "cash02_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TABLE = "cash_policies"
CHECKS = ("cash_policies_scope_chk", "cash_policies_boundaries_chk", "cash_policies_horizon_chk",
          "cash_policies_rungs_chk", "cash_policies_buffer_chk", "cash_policies_min_chk", "cash_policies_objective_chk",
          "cash_policies_preset_chk", "cash_policies_status_chk", "cash_policies_version_chk",
          "cash_policies_makers_chk", "cash_policies_maker_checker_chk", "cash_policies_pending_chk",
          "cash_policies_approved_chk", "cash_policies_rejected_chk", "cash_policies_retired_chk",
          "cash_policies_live_valid_chk", "cash_policies_archive_chk")
UNIQUES = ("cash_policies_active_household_uq", "cash_policies_active_entity_uq", "cash_policies_open_household_uq",
           "cash_policies_open_entity_uq", "cash_policies_version_uq")


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
        t = f"public.{TABLE}"
        if await conn.fetchval("SELECT to_regclass($1)", t) is None:
            bad.append(f"{TABLE}: absent")
        else:
            if not await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", t):
                bad.append(f"{TABLE}: RLS off")
            ops = {r["cmd"] for r in await conn.fetch("SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                     "tablename = $1", TABLE)}
            if ops != {"SELECT", "INSERT", "UPDATE", "DELETE"}:
                bad.append(f"{TABLE}: policies {sorted(ops)}")
            have = {r["conname"] for r in await conn.fetch("SELECT conname FROM pg_constraint WHERE conrelid = "
                                                          "$1::regclass AND contype = 'c'", t)}
            miss = set(CHECKS) - have
            if miss:
                bad.append(f"{TABLE}: missing checks {sorted(miss)}")
            if not await conn.fetchval("SELECT has_table_privilege('app_service', $1, 'INSERT')", t):
                bad.append(f"{TABLE}: app_service lacks INSERT")
        idx = {r["indexname"] for r in await conn.fetch("SELECT indexname FROM pg_indexes WHERE indexname = "
                                                        "ANY($1::text[])", list(UNIQUES))}
        if idx != set(UNIQUES):
            bad.append(f"unique indexes missing {sorted(set(UNIQUES) - idx)}")
        if await conn.fetchval("SELECT to_regclass('public.cash_coverage_minimums')") is not None:
            bad.append("cash_coverage_minimums still exists (the minimum must have ONE home)")
        if not await conn.fetchval("SELECT 1 FROM sod_item_types WHERE item_type = 'cash_policy' AND maker_duty = "
                                   "'set_cash_policy' AND checker_duty = 'approve_cash_policy'"):
            bad.append("sod_item_types: cash_policy absent")
        n = await conn.fetchval("SELECT count(*) FROM sod_rules WHERE org_id IS NULL AND item_type = 'cash_policy' "
                                "AND status = 'active' AND rule_kind IN ('default', 'policy')")
        if n != 2:
            bad.append(f"sod_rules: {n} active platform rules for cash_policy (want default + policy)")
        if not await conn.fetchval("SELECT 1 FROM permissions WHERE name = 'manage_cash_policy'"):
            bad.append("permission manage_cash_policy absent")
        roles = await conn.fetchval(
            "SELECT count(*) FROM roles r JOIN role_permissions rp ON rp.role_id = r.id JOIN permissions p ON "
            "p.id = rp.permission_id WHERE p.name = 'manage_cash_policy' AND r.name = 'org_admin'")
        print(f"[INFO] org_admin roles holding manage_cash_policy: {roles}")
    finally:
        await conn.close()
    print("SCHEMA OK" if not bad else f"SCHEMA PROBLEMS: {bad}")
    return 0 if not bad else 1


async def main(argv: list[str]) -> int:
    if "--schema" in argv or "--all" in argv:
        await apply_schema()
    if "--verify" in argv or "--all" in argv:
        return await verify_schema()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
