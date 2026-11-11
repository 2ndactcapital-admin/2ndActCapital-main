"""ALTREAD1 — apply docs/altread1_part1.sql and read it back from the catalog.

    python3 apps/api/scripts/_apply_altread1.py --schema      # the migration (its own BEGIN / COMMIT)
    python3 apps/api/scripts/_apply_altread1.py --verify      # read every object back (namespace + relname, no ::regclass)
    python3 apps/api/scripts/_apply_altread1.py --all

Operator postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP`` in Doppler — DDL needs the owner). Idempotent. Never
prints a secret. Writes no data row.
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

SCHEMA_SQL = REPO / "docs" / "altread1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
REL = ("SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $1 "
       "AND c.relname = $2")
#: the RLS tables this sprint adds: (schema, table)
RLS_TABLES = (("public", "account_custodian_balances"), ("portfolio", "custodian_tax_lots"))
CMDS = {"r", "a", "w", "d"}           # pg_policy.polcmd: SELECT / INSERT / UPDATE / DELETE
CPSIM_PRIVS = {"SELECT", "INSERT", "UPDATE", "DELETE"}


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


async def _col(conn, nsp, rel, col):
    return await conn.fetchrow(
        "SELECT format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull, pg_get_expr(d.adbin, d.adrelid) AS dflt "
        "FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE a.attrelid = (" + REL + ") AND a.attname = $3 AND NOT a.attisdropped", nsp, rel, col)


async def verify() -> int:
    conn = await _conn()
    bad = 0

    def need(ok, label, detail=""):
        nonlocal bad
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))
        bad += 0 if ok else 1

    try:
        for nsp, t in RLS_TABLES:
            oid = await conn.fetchval(REL, nsp, t)
            need(oid is not None, f"{nsp}.{t} exists")
            if oid is None:
                continue
            rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1", oid)
            need(rls is True, f"{nsp}.{t} has row level security enabled")
            pols = await conn.fetch("SELECT polname, polcmd::text AS cmd, pg_get_expr(polqual, polrelid) AS q, "
                                    "pg_get_expr(polwithcheck, polrelid) AS w FROM pg_policy WHERE polrelid = $1", oid)
            need({p["cmd"] for p in pols} == CMDS and len(pols) == 4,
                 f"{nsp}.{t} has one policy per operation (SELECT / INSERT / UPDATE / DELETE)",
                 str([(p["polname"], p["cmd"]) for p in pols]))
            need(all("NULLIF" in ((p["q"] or "") + (p["w"] or "")) for p in pols),
                 f"every {nsp}.{t} policy reads the org GUC through NULLIF")
            privs = {r["privilege_type"] for r in await conn.fetch(
                "SELECT x.privilege_type FROM pg_class c, aclexplode(c.relacl) x JOIN pg_roles r ON r.oid = x.grantee "
                "WHERE c.oid = $1 AND r.rolname = 'app_service'", oid)}
            need(privs == CPSIM_PRIVS, f"app_service on {nsp}.{t} = SELECT/INSERT/UPDATE/DELETE", str(sorted(privs)))
            bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'app_service'")
            need(bypass is False, "app_service does not bypass RLS")
        idx = {(r["schemaname"], r["indexname"]): r["indexdef"] for r in await conn.fetch(
            "SELECT schemaname, indexname, indexdef FROM pg_indexes WHERE (schemaname, tablename) IN "
            "(('public', 'account_custodian_balances'), ('portfolio', 'custodian_tax_lots'), ('cpsim', 'book_lots'))")}
        for key in (("public", "account_custodian_balances_live_uq"), ("portfolio", "custodian_tax_lots_live_uq"),
                    ("cpsim", "book_lots_live_uq")):
            d = idx.get(key)
            need(bool(d and "UNIQUE" in d and "valid_to IS NULL" in d), f"{key[0]}.{key[1]} is a partial unique "
                 "index on the live rows", str(d))
        for nsp, rel, con, must in (
                ("public", "account_custodian_balances", "account_custodian_balances_source_chk", "'altruist_simulator'"),
                ("public", "account_custodian_balances", "account_custodian_balances_env_chk", "'simulator'"),
                ("portfolio", "custodian_tax_lots", "custodian_tax_lots_source_chk", "'altruist'"),
                ("portfolio", "custodian_tax_lots", "custodian_tax_lots_env_chk", "'production'"),
                ("public", "account_lot_method_settings", "account_lot_method_settings_lot_source_chk", "'custodian'"),
                ("cpsim", "book_uploads", "book_uploads_kind_chk", "'lots'"),
                ("cpsim", "book_lots", "book_lots_qty_chk", "quantity > ")):
            d = await _condef(conn, nsp, rel, con)
            need(bool(d and must in d), f"{nsp}.{rel} {con}", str(d))
        c = await _col(conn, "public", "account_lot_method_settings", "lot_source")
        need(bool(c and c["attnotnull"] and "'hollisworks'" in (c["dflt"] or "")),
             "account_lot_method_settings.lot_source NOT NULL DEFAULT 'hollisworks'", str(dict(c) if c else None))
        c = await _col(conn, "public", "altruist_connections", "refresh_token_issued_at")
        need(bool(c and c["type"].startswith("timestamp") and not c["attnotnull"]),
             "altruist_connections.refresh_token_issued_at (timestamptz, nullable)", str(dict(c) if c else None))
        oid = await conn.fetchval(REL, "cpsim", "book_lots")
        need(oid is not None, "cpsim.book_lots exists")
        if oid is not None:
            app_role = await conn.fetchval("SELECT oid FROM pg_roles WHERE rolname = 'cpsim_app'")
            privs = {r["privilege_type"] for r in await conn.fetch(
                "SELECT x.privilege_type, x.grantee FROM pg_class c, aclexplode(c.relacl) x WHERE c.oid = $1", oid)
                if r["grantee"] == app_role}
            need(privs == CPSIM_PRIVS, "cpsim_app on cpsim.book_lots = SELECT/INSERT/UPDATE/DELETE", str(sorted(privs)))
            others = await conn.fetch(
                "SELECT r.rolname, x.privilege_type FROM pg_class c, aclexplode(c.relacl) x JOIN pg_roles r ON "
                "r.oid = x.grantee WHERE c.oid = $1 AND r.rolname NOT IN ('cpsim_app', 'postgres')", oid)
            need(not others, "no other role holds a privilege on cpsim.book_lots", f"{[dict(r) for r in others]}")
        app_usage = await conn.fetchval("SELECT has_schema_privilege('app_service', 'cpsim', 'USAGE')")
        need(app_usage is False, "app_service still has no USAGE on schema cpsim")
        n = await conn.fetchval("SELECT count(*) FROM public.account_lot_method_settings WHERE lot_source <> 'hollisworks'")
        need(n == 0, "every existing lot-method row reads lot_source = 'hollisworks' (the default; nothing changed)",
             str(n))
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
