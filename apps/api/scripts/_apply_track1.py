"""TRACK1 — apply docs/track1_part1.sql (tracked securities + named security lists) and prove it landed.

    python3 apps/api/scripts/_apply_track1.py --schema     # the migration, one transaction (operator DSN)
    python3 apps/api/scripts/_apply_track1.py --verify     # read the catalog back
    python3 apps/api/scripts/_apply_track1.py --all        # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``: DDL needs the table owner).
Idempotent. Never prints a secret. Writes NO security, price, list member, tracked row or client-org row.
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

SCHEMA_SQL = REPO / "docs" / "track1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TABLES = ("tracked_securities", "security_lists", "security_list_members")
LIVE_UQ = {"tracked_securities": "tracked_securities_live_uq", "security_lists": "security_lists_live_uq",
           "security_list_members": "security_list_members_live_uq"}


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


async def verify() -> int:
    conn = await _conn()
    gaps: list[str] = []

    def need(ok: bool, label: str, detail: str = "") -> None:
        print(f"{'[OK]  ' if ok else '[GAP] '}{label}" + (f" — {detail}" if detail else ""))
        if not ok:
            gaps.append(label)

    try:
        for t in TABLES:
            rls = await conn.fetchval("SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = "
                                      "c.relnamespace WHERE n.nspname = 'portfolio' AND c.relname = $1", t)
            need(rls is True, f"portfolio.{t} exists with RLS enabled", f"{rls}")
            pol = {r["cmd"] for r in await conn.fetch(
                "SELECT cmd FROM pg_policies WHERE schemaname = 'portfolio' AND tablename = $1", t)}
            need(pol == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(pol)}")
            priv = await conn.fetchval(
                "SELECT has_table_privilege('app_service', $1, 'SELECT,INSERT,UPDATE,DELETE')", f"portfolio.{t}")
            need(priv is True, f"{t}: app_service holds the four privileges")
            idx = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE indexname = $1", LIVE_UQ[t])
            need("UNIQUE" in (idx or "") and "valid_to IS NULL" in (idx or ""), f"{t}: live unique index", idx or "")
            n = await conn.fetchval(f"SELECT count(*) FROM portfolio.{t}")
            print(f"[INFO] portfolio.{t}: {n} rows")
    finally:
        await conn.close()
    print(f"\n{'SCHEMA OK' if not gaps else f'{len(gaps)} GAP(S)'}")
    return 1 if gaps else 0


def main() -> int:
    args = sys.argv[1:]
    if "--all" in args:
        asyncio.run(apply_schema())
        return asyncio.run(verify())
    if "--schema" in args:
        asyncio.run(apply_schema())
        return 0
    if "--verify" in args:
        return asyncio.run(verify())
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
