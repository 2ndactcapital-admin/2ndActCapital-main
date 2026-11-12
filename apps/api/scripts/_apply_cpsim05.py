"""CPSIM05 — apply docs/cpsim05_part1.sql and read it back from the catalog.

    python3 apps/api/scripts/_apply_cpsim05.py --schema      # the migration (its own BEGIN / COMMIT)
    python3 apps/api/scripts/_apply_cpsim05.py --verify      # read every object back (namespace + relname, no ::regclass)
    python3 apps/api/scripts/_apply_cpsim05.py --all

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the owner). Idempotent. Never
prints a secret. Writes no data row except the reference-data UPDATE of cpsim.institution_types 'altruist' (+ 'fix').
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

SCHEMA_SQL = REPO / "docs" / "cpsim05_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
NEW_TABLES = ("fix_messages", "fix_allocations")
EXPECT_PRIVS = {"fix_messages": {"SELECT", "INSERT"}, "fix_allocations": {"SELECT", "INSERT"}}
POLICY_TABLES = ("execution_instructions", "execution_instruction_events", "trade_blocks")
REL = ("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $1 "
       "AND c.relname = $2")


async def _conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
    return conn


async def _policies(conn) -> dict:
    return {t: sorted(r["cmd"] for r in await conn.fetch(
        "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = $1", t)) for t in POLICY_TABLES}


async def apply_schema() -> None:
    conn = await _conn()
    try:
        before = await _policies(conn)
        await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        after = await _policies(conn)
        print("[OK] schema applied")
        print(f"[{'OK' if before == after else 'FAIL'}] RLS policies on {', '.join(POLICY_TABLES)} unchanged by the "
              f"migration ({ {t: len(v) for t, v in after.items()} })")
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
            need(trg == 1, f"cpsim.{t} has its append-only guard trigger", str(trg))
        idx = {r["indexname"] for r in await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'cpsim' AND tablename = ANY($1::text[])",
            list(NEW_TABLES))}
        for i in ("fix_messages_exchange_uq", "fix_messages_recent_idx", "fix_messages_clordid_idx",
                  "fix_allocations_leg_uq"):
            need(i in idx, f"cpsim index {i} exists")
        for c in ("protocol", "is_block"):
            n = await conn.fetchval("SELECT count(*) FROM pg_attribute WHERE attrelid = (" + REL + ") AND attname = $3 "
                                    "AND NOT attisdropped", "cpsim", "orders", c)
            need(n == 1, f"cpsim.orders.{c} exists")
        d = await _condef(conn, "cpsim", "orders", "orders_protocol_chk")
        need(bool(d and "'rest'" in d and "'fix'" in d), "orders_protocol_chk admits rest / fix", str(d))
        d = await _condef(conn, "cpsim", "fix_messages", "fix_messages_no_secret_chk")
        need(bool(d and "554" in d), "fix_messages_no_secret_chk refuses an unredacted password", str(d))
        groups = await conn.fetchval("SELECT field_groups FROM cpsim.institution_types WHERE key = 'altruist'")
        need(bool(groups and "fix" in groups), "institution type 'altruist' carries the 'fix' field group", str(groups))
        app_usage = await conn.fetchval("SELECT has_schema_privilege('app_service', 'cpsim', 'USAGE')")
        need(app_usage is False, "app_service still has no USAGE on schema cpsim")
        d = await _condef(conn, "public", "execution_instructions", "execution_instructions_custodian_order_chk")
        need(bool(d and "'fix_sim'" in d and "'custodian_api'" in d), "custodian_order_chk admits a fix_sim OrderID",
             str(d))
        d = await _condef(conn, "public", "execution_instructions", "execution_instructions_fix_sim_chk")
        need(bool(d and "'fix_sim'" in d and "routed_via" in d), "execution_instructions_fix_sim_chk exists", str(d))
        d = await _condef(conn, "public", "execution_instruction_events", "execution_instruction_events_action_chk")
        need(bool(d and "'approve_upstream'" in d and "'automatic_failed'" in d and "'custodian_cancel'" in d),
             "events action CHECK admits approve_upstream / automatic_failed (and keeps cpsim03's)", str(d))
        d = await _condef(conn, "public", "trade_blocks", "trade_blocks_route_chk")
        need(bool(d and "'manual'" in d and "'fix_sim'" in d and "'fix'::" not in d),
             "trade_blocks_route_chk = manual, fix_sim ('fix' still refused)", str(d))
        trg = await conn.fetchval(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid = (" + REL + ") AND tgname = "
            "'execution_instruction_events_fix_sim_guard_trg' AND NOT tgisinternal", "public",
            "execution_instruction_events")
        need(trg == 1, "the D5 event trigger exists on public.execution_instruction_events")
        pols = await _policies(conn)
        need(all(len(v) >= 1 for v in pols.values()), "RLS policies present on the three public tables",
             str({t: v for t, v in pols.items()}))
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
