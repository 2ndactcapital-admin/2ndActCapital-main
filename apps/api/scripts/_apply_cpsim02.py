"""CPSIM02 — apply docs/cpsim02_part1.sql and read it back from the catalog.

    python3 apps/api/scripts/_apply_cpsim02.py --schema      # the migration (its own BEGIN / COMMIT)
    python3 apps/api/scripts/_apply_cpsim02.py --verify      # read every object back (namespace + relname, no ::regclass)
    python3 apps/api/scripts/_apply_cpsim02.py --all

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the owner). Idempotent. Never
prints a secret. Writes no data row (no counterparty, no setting, no book).
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

SCHEMA_SQL = REPO / "docs" / "cpsim02_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
NEW_TABLES = ("book_uploads", "book_households", "book_accounts", "book_positions", "book_transactions",
              "oauth_codes", "oauth_tokens", "conformance_runs")
EXPECT_PRIVS = {t: {"SELECT", "INSERT", "UPDATE", "DELETE"} for t in NEW_TABLES}
EXPECT_PRIVS["book_uploads"] = {"SELECT", "INSERT"}


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


REL = ("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $1 "
       "AND c.relname = $2")


async def verify() -> int:
    conn = await _conn()
    bad = 0

    def need(ok, label, detail=""):
        nonlocal bad
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))
        bad += 0 if ok else 1

    try:
        for t in NEW_TABLES:
            oid = await conn.fetchval(REL, "cpsim", t)
            need(oid is not None, f"cpsim.{t} exists")
            if oid is None:
                continue
            privs = {r["privilege_type"] for r in await conn.fetch(
                "SELECT (aclexplode(c.relacl)).privilege_type, (aclexplode(c.relacl)).grantee FROM pg_class c "
                "WHERE c.oid = $1", oid) if r["grantee"] == await conn.fetchval(
                    "SELECT oid FROM pg_roles WHERE rolname = 'cpsim_app'")}
            need(privs == EXPECT_PRIVS[t], f"cpsim_app on cpsim.{t} = {sorted(EXPECT_PRIVS[t])}", f"{sorted(privs)}")
            others = await conn.fetch(
                "SELECT r.rolname, x.privilege_type FROM pg_class c, aclexplode(c.relacl) x JOIN pg_roles r ON "
                "r.oid = x.grantee WHERE c.oid = $1 AND r.rolname NOT IN ('cpsim_app', 'postgres')", oid)
            need(not others, f"no other role holds a privilege on cpsim.{t}", f"{[dict(r) for r in others]}")
        app_usage = await conn.fetchval("SELECT has_schema_privilege('app_service', 'cpsim', 'USAGE')")
        need(app_usage is False, "app_service still has no USAGE on schema cpsim")
        for rel, con, must in (
                ("positions", "positions_source_chk", "'altruist_simulator'::text"),
        ):
            d = await conn.fetchval(
                "SELECT pg_get_constraintdef(k.oid) FROM pg_constraint k WHERE k.conrelid = (" + REL + ") "
                "AND k.conname = $3", "portfolio", rel, con)
            need(bool(d and must in d and "'altruist'::text" in d), f"portfolio.{rel}.{con} admits {must}", str(d))
        for rel, con in (("altruist_connections", "altruist_connections_environment_check"),
                         ("altruist_oauth_states", "altruist_oauth_states_environment_check")):
            d = await conn.fetchval(
                "SELECT pg_get_constraintdef(k.oid) FROM pg_constraint k WHERE k.conrelid = (" + REL + ") "
                "AND k.conname = $3", "public", rel, con)
            need(bool(d and "'simulator'::text" in d and "'sandbox'::text" in d and "'production'::text" in d),
                 f"public.{rel}.{con} = sandbox, production, simulator", str(d))
        g = await conn.fetchval("SELECT prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                                "WHERE n.nspname = 'public' AND p.proname = 'cpsim_org_setting_guard'")
        need(bool(g and "counterparty_sim.altruist_counterparty" in g), "the org-setting guard covers the counterparty key")
        w = await conn.fetchrow("SELECT p.prosecdef, has_function_privilege('app_service', p.oid, 'EXECUTE') AS ex "
                                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = "
                                "'public' AND p.proname = 'counterparty_sim_may_connect'")
        need(bool(w and w["prosecdef"] and w["ex"]), "public.counterparty_sim_may_connect: definer, app_service EXECUTE")
        col = await conn.fetchval("SELECT count(*) FROM pg_attribute WHERE attrelid = (" + REL + ") AND attname IN "
                                  "('webhook_url') AND NOT attisdropped", "cpsim", "counterparty_behaviour")
        need(col == 1, "cpsim.counterparty_behaviour.webhook_url exists")
        col = await conn.fetchval("SELECT count(*) FROM pg_attribute WHERE attrelid = (" + REL + ") AND attname IN "
                                  "('direction', 'latency_ms') AND NOT attisdropped", "cpsim", "request_log")
        need(col == 2, "cpsim.request_log.direction / latency_ms exist")
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
