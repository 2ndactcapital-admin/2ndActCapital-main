"""fee38 Task 1 discovery, part 3 — fixture facts."""

from __future__ import annotations

import asyncio
import glob
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _site in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _site not in sys.path:
        sys.path.insert(0, _site)
for _path in (str(HERE), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn, connect  # noqa: E402


async def main() -> int:
    dsn, prov = await admin_dsn()
    conn = await connect(dsn)
    out: dict = {}
    out["orgs"] = [
        dict(r) for r in await conn.fetch("SELECT id, name FROM organizations ORDER BY name")
    ]
    out["users_sample"] = [
        dict(r) for r in await conn.fetch("SELECT id, email FROM users LIMIT 5")
    ]
    out["abd_indexes"] = [
        dict(r)
        for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public' "
            "AND tablename IN ('account_balances_daily','accounts','households') "
            "ORDER BY tablename, indexname"
        )
    ]
    out["rls_on"] = [
        dict(r)
        for r in await conn.fetch(
            """
            SELECT c.relname, c.relrowsecurity,
                   (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid) AS npol
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname = ANY($1::text[])
            """,
            [
                "accounts",
                "households",
                "account_balances_daily",
                "account_flows",
                "cost_schedules",
                "cost_providers",
            ],
        )
    ]
    out["app_service_bypassrls"] = await conn.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname='app_service'"
    )
    # does a benefit-schedule-shaped notes column exist on cost_schedules? (no)
    out["cost_schedules_has_notes"] = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' "
        "AND table_name='cost_schedules' AND column_name='notes'"
    )
    await conn.close()
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
