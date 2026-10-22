"""DEALROOM2 — apply the Phase 2 schema.

    python3 apps/api/scripts/_apply_dealroom2.py --schema   # docs/dealroom2_part1.sql

Runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP``
in Doppler — DDL needs the table owner, and the Supabase MCP cancels any
migration text that relaxes a NOT NULL). Idempotent: every statement is
IF NOT EXISTS / guarded by a catalog check. There is no data migration: the
live database held no deal agreement, signature or access row when Phase 2 was
applied (docs/DEALROOM2_DISCOVERY.md). Never prints a secret.
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

SCHEMA_SQL = REPO / "docs" / "dealroom2_part1.sql"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"


async def apply_schema() -> None:
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    try:
        print(f"[INFO] connected as {await conn.fetchval('SELECT current_user')} via {how}")
        async with conn.transaction():
            await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        print("[OK] schema applied")
    finally:
        await conn.close()


def main() -> int:
    if "--schema" not in sys.argv[1:]:
        print(__doc__)
        return 2
    asyncio.run(apply_schema())
    return 0


if __name__ == "__main__":
    sys.exit(main())
