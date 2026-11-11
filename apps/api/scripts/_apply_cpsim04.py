"""CPSIM04 — apply docs/cpsim04_part1.sql and read it back from the catalog.

    python3 apps/api/scripts/_apply_cpsim04.py --schema      # the migration (its own BEGIN / COMMIT)
    python3 apps/api/scripts/_apply_cpsim04.py --verify      # read every object back (namespace + relname, no ::regclass)
    python3 apps/api/scripts/_apply_cpsim04.py --all

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the owner). Idempotent. Never
prints a secret. Writes no data row (no counterparty, no transfer, no capability, no payment).
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

SCHEMA_SQL = REPO / "docs" / "cpsim04_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
NEW_TABLES = ("transfers", "transfer_events", "cash_movements")
EXPECT_PRIVS = {"transfers": {"SELECT", "INSERT", "UPDATE"}, "transfer_events": {"SELECT", "INSERT"},
                "cash_movements": {"SELECT", "INSERT"}}
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
        for i in ("transfers_client_live_uq", "transfers_transfer_id_live_uq", "transfer_events_seq_uq",
                  "cash_movements_leg_uq"):
            need(i in idx, f"cpsim index {i} exists")
        col = await conn.fetchval("SELECT count(*) FROM pg_attribute WHERE attrelid = (" + REL + ") AND attname = "
                                  "'consent_required' AND NOT attisdropped", "cpsim", "counterparty_behaviour")
        need(col == 1, "cpsim.counterparty_behaviour.consent_required exists")
        d = await _condef(conn, "cpsim", "book_transactions", "book_transactions_type_chk")
        need(bool(d and all(f"'{t}'::text" in d for t in ("buy", "adjustment", "transfer_in", "transfer_out", "ach_in",
                                                           "ach_out", "wire_in", "wire_out", "ach_return"))),
             "book_transactions_type_chk admits the client's 7 types and the 7 D1 cash types", str(d))
        d = await _condef(conn, "cpsim", "transfers", "transfers_bank_refs_chk")
        need(bool(d and "[0-9]{4}" in d), "transfers_bank_refs_chk keeps bank references to four digits", str(d))
        app_usage = await conn.fetchval("SELECT has_schema_privilege('app_service', 'cpsim', 'USAGE')")
        need(app_usage is False, "app_service still has no USAGE on schema cpsim")
        pols = await conn.fetch("SELECT tablename, cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = "
                                "ANY($1::text[])", ["payment_instructions", "payment_events"])
        need(len(pols) == 8, "RLS policies on payment_instructions / payment_events unchanged (4 each)", str(len(pols)))
        d = await _condef(conn, "public", "payment_events", "payment_events_human_chk")
        need(bool(d and "actor_id IS NOT NULL" in d), "payment_events_human_chk unchanged", str(d))
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
