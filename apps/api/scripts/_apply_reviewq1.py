"""REVIEWQ1 — apply docs/reviewq1_part1.sql (review choices, Known discrepancies sightings, alternate identifiers) and
prove it landed.

    python3 apps/api/scripts/_apply_reviewq1.py --schema     # the migration, one transaction (operator DSN)
    python3 apps/api/scripts/_apply_reviewq1.py --verify     # read the catalog back
    python3 apps/api/scripts/_apply_reviewq1.py --all        # both, in order

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``: DDL needs the table owner).
Idempotent. Never prints a secret. Writes NO review item, security or identifier row (the live open count is read
before and after and must not move).
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

SCHEMA_SQL = REPO / "docs" / "reviewq1_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
STATUSES = ("open", "resolved", "dismissed", "used_incoming", "kept_existing", "kept_both")
KINDS = ("list_match", "add_to_master", "identifier_conflict", "ticker_conflict")
OPEN_SQL = ("SELECT count(*) FROM portfolio.security_master_review WHERE status = 'open' AND valid_to IS NULL AND "
            "system_to IS NULL")


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
        before = await conn.fetchval(OPEN_SQL)
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout = '15s'")
            await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        after = await conn.fetchval(OPEN_SQL)
        print(f"[OK] schema applied (open review items {before} → {after})")
        if before != after:
            raise SystemExit("[FAIL] the open review count moved")
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
        defs = {r["conname"]: r["d"] for r in await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conrelid = "
            "'portfolio.security_master_review'::regclass AND contype = 'c'")}
        st = defs.get("security_master_review_status_chk", "")
        need(all(f"'{s}'" in st for s in STATUSES), "status CHECK admits every status", st[:200])
        kd = defs.get("security_master_review_kind_chk", "")
        need(all(f"'{k}'" in kd for k in KINDS), "kind CHECK admits list_match / add_to_master and the old kinds")
        col = await conn.fetchrow(
            "SELECT data_type, is_nullable, column_default FROM information_schema.columns WHERE table_schema = "
            "'portfolio' AND table_name = 'securities_global_identifiers' AND column_name = 'is_alternate'")
        need(col is not None and col["is_nullable"] == "NO" and "false" in (col["column_default"] or ""),
             "securities_global_identifiers.is_alternate boolean NOT NULL DEFAULT false", f"{dict(col) if col else None}")
        t = "security_master_review_sightings"
        rls = await conn.fetchval("SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = "
                                  "c.relnamespace WHERE n.nspname = 'portfolio' AND c.relname = $1", t)
        need(rls is True, f"portfolio.{t} exists with RLS enabled", f"{rls}")
        pol = {r["cmd"] for r in await conn.fetch(
            "SELECT cmd FROM pg_policies WHERE schemaname = 'portfolio' AND tablename = $1", t)}
        need(pol == {"SELECT", "INSERT", "UPDATE", "DELETE"}, f"{t}: a policy for every operation", f"{sorted(pol)}")
        priv = await conn.fetchval("SELECT has_table_privilege('app_service', $1, 'SELECT,INSERT,UPDATE,DELETE')",
                                   f"portfolio.{t}")
        need(priv is True, f"{t}: app_service holds the four privileges")
        idx = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE indexname = 'security_master_review_known_ix'")
        need(bool(idx), "the Known discrepancies index", idx or "")
        print(f"[INFO] open review items: {await conn.fetchval(OPEN_SQL)}; "
              f"alternates: {await conn.fetchval('SELECT count(*) FROM portfolio.securities_global_identifiers WHERE is_alternate')}")
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
