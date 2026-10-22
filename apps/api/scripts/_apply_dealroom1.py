"""DEALROOM1 — apply the Part 1 schema and run the reported data migration.

    python3 apps/api/scripts/_apply_dealroom1.py --schema     # docs/dealroom1_part1.sql
    python3 apps/api/scripts/_apply_dealroom1.py --templates  # seed platform_nda / platform_joinder v1
    python3 apps/api/scripts/_apply_dealroom1.py --backfill   # publishers, folders, documents
    python3 apps/api/scripts/_apply_dealroom1.py --report     # print the migration report only

The schema runs on the operator's postgres DSN (``DATABASE_URL_PRECUTOVER_
POSTGRES_BACKUP`` in Doppler — DDL needs the table owner). The template seed
and the backfill run on that same DSN under ``platform_scope`` semantics
(``app.is_super_admin`` SET LOCAL) and go through the SAME service functions
the app uses (``services.deal_room``), so there is one implementation of
"ensure a deal's nine folders", "the house publisher" and "map a document".

Every step is idempotent. The backfill writes docs/DEALROOM1_MIGRATION.json:
one entry per live deal (publisher chosen and why, folders created) and one
per document (old visible_to_members → folder and level). Never prints a
secret.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
sys.path.insert(0, str(HERE))

from _db_connect import connect, hydrate, resolve_dsn  # noqa: E402

hydrate()
sys.path.insert(0, str(API_DIR))

SCHEMA_SQL = REPO / "docs" / "dealroom1_part1.sql"
REPORT = REPO / "docs" / "DEALROOM1_MIGRATION.json"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise SystemExit(f"no operator DSN: {how}")
    conn = await connect(dsn)
    who = await conn.fetchval("SELECT current_user")
    print(f"[INFO] connected as {who} via {how}")
    return conn


async def apply_schema() -> None:
    conn = await operator_conn()
    try:
        async with conn.transaction():
            await conn.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        print("[OK] schema applied")
    finally:
        await conn.close()


async def seed_templates() -> None:
    from services import deal_room_templates as T

    conn = await operator_conn()
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            out = await T.seed_platform_templates(conn)
        print(f"[OK] templates: {out}")
    finally:
        await conn.close()


async def backfill() -> dict:
    from services import deal_room as R

    conn = await operator_conn()
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            report = await R.backfill_existing(conn)
        REPORT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"[OK] backfill: {len(report['deals'])} deals, {len(report['documents'])} documents "
              f"→ {REPORT.relative_to(REPO)}")
        return report
    finally:
        await conn.close()


def main() -> int:
    args = set(sys.argv[1:])
    if not args:
        print(__doc__)
        return 2
    if "--schema" in args:
        asyncio.run(apply_schema())
    if "--templates" in args:
        asyncio.run(seed_templates())
    if "--backfill" in args:
        asyncio.run(backfill())
    if "--report" in args:
        print(REPORT.read_text(encoding="utf-8") if REPORT.exists() else "(no report yet)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
