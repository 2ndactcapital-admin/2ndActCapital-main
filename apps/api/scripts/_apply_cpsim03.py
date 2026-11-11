"""CPSIM03 — apply docs/cpsim03_part1.sql and read it back from the catalog.

    python3 apps/api/scripts/_apply_cpsim03.py --schema      # the migration (its own BEGIN / COMMIT)
    python3 apps/api/scripts/_apply_cpsim03.py --verify      # read every object back (namespace + relname, no ::regclass)
    python3 apps/api/scripts/_apply_cpsim03.py --all

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the owner). Idempotent. Never
prints a secret. Writes no data row (no counterparty, no order, no capability, no instruction).
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

SCHEMA_SQL = REPO / "docs" / "cpsim03_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
NEW_TABLES = ("orders", "order_executions")
EXPECT_PRIVS = {"orders": {"SELECT", "INSERT", "UPDATE"}, "order_executions": {"SELECT", "INSERT"}}
REL = ("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $1 "
       "AND c.relname = $2")


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
        await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        print("[OK] schema applied")
    finally:
        await conn.close()


async def _condef(conn, nsp, rel, con):
    return await conn.fetchval(
        "SELECT pg_get_constraintdef(k.oid) FROM pg_constraint k WHERE k.conrelid = (" + REL + ") AND k.conname = $3",
        nsp, rel, con)


async def verify() -> int:
    conn = await _conn()
    bad = 0

    def need(ok, label, detail=""):
        nonlocal bad
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))
        bad += 0 if ok else 1

    try:
        app_role = await conn.fetchval("SELECT oid FROM pg_roles WHERE rolname = 'cpsim_app'")
        for t in NEW_TABLES:
            oid = await conn.fetchval(REL, "cpsim", t)
            need(oid is not None, f"cpsim.{t} exists")
            if oid is None:
                continue
            privs = {r["privilege_type"] for r in await conn.fetch(
                "SELECT x.privilege_type, x.grantee FROM pg_class c, aclexplode(c.relacl) x WHERE c.oid = $1", oid)
                if r["grantee"] == app_role}
            need(privs == EXPECT_PRIVS[t], f"cpsim_app on cpsim.{t} = {sorted(EXPECT_PRIVS[t])}", f"{sorted(privs)}")
            others = await conn.fetch(
                "SELECT r.rolname, x.privilege_type FROM pg_class c, aclexplode(c.relacl) x JOIN pg_roles r ON "
                "r.oid = x.grantee WHERE c.oid = $1 AND r.rolname NOT IN ('cpsim_app', 'postgres')", oid)
            need(not others, f"no other role holds a privilege on cpsim.{t}", f"{[dict(r) for r in others]}")
            trg = await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgrelid = $1 AND NOT tgisinternal", oid)
            need(trg == 1, f"cpsim.{t} has its guard trigger", str(trg))
        idx = {r["indexname"] for r in await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'cpsim' AND tablename = ANY($1::text[])",
            list(NEW_TABLES))}
        for i in ("orders_client_order_live_uq", "orders_order_id_live_uq", "order_executions_exec_uq"):
            need(i in idx, f"cpsim index {i} exists")
        app_usage = await conn.fetchval("SELECT has_schema_privilege('app_service', 'cpsim', 'USAGE')")
        need(app_usage is False, "app_service still has no USAGE on schema cpsim")
        col = await conn.fetchval("SELECT count(*) FROM pg_attribute WHERE attrelid = (" + REL + ") AND attname = "
                                  "'custodian_order_id' AND NOT attisdropped", "public", "execution_instructions")
        need(col == 1, "public.execution_instructions.custodian_order_id exists")
        col = await conn.fetchval("SELECT count(*) FROM pg_attribute WHERE attrelid = (" + REL + ") AND attname = "
                                  "'custodian_exec_id' AND NOT attisdropped", "public", "execution_fills")
        need(col == 1, "public.execution_fills.custodian_exec_id exists")
        d = await _condef(conn, "public", "execution_instructions", "execution_instructions_custodian_order_chk")
        need(bool(d and "custodian_api" in d), "execution_instructions_custodian_order_chk ties the id to custodian_api",
             str(d))
        d = await _condef(conn, "public", "execution_instruction_events", "execution_instruction_events_action_chk")
        need(bool(d and "'custodian_reject'::text" in d and "'custodian_cancel'::text" in d and "'letter'::text" in d),
             "events action CHECK admits custodian_reject / custodian_cancel (and keeps the 14)", str(d))
        g = await conn.fetchval("SELECT prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                                "WHERE n.nspname = 'public' AND p.proname = 'execution_instructions_guard'")
        need(bool(g and "'custodian_order_id'" in g and "'approved>sent'" in g),
             "execution_instructions_guard lists custodian_order_id as a lifecycle column")
        u = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = "
                                "'execution_fills_custodian_exec_uq'")
        need(bool(u and "UNIQUE" in u), "execution_fills_custodian_exec_uq is a UNIQUE index", str(u))
        d = await _condef(conn, "public", "trade_blocks", "trade_blocks_route_chk")
        need(d == "CHECK ((route = 'manual'::text))", "trade_blocks_route_chk unchanged (manual only)", str(d))
        pols = await conn.fetch("SELECT tablename, cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = "
                                "ANY($1::text[])", ["execution_instructions", "execution_fills",
                                                    "execution_instruction_events"])
        need(len(pols) == 12, "RLS policies on the three execution tables unchanged (4 each)", str(len(pols)))
    finally:
        await conn.close()
    print(f"VERIFY: {'OK' if not bad else f'{bad} FAIL'}")
    return bad


async def main(argv) -> int:
    if "--schema" in argv or "--all" in argv:
        await apply_schema()
    if "--verify" in argv or "--all" in argv:
        return 1 if await verify() else 0
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
