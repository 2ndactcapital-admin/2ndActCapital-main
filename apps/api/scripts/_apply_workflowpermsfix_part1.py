"""Apply docs/workflowpermsfix_part1.sql to the deployed DB. Idempotent."""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

import asyncpg  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
SQL = REPO / "docs" / "workflowpermsfix_part1.sql"


async def main():
    dsn = await bootstrap_async()
    if not dsn:
        sys.exit("no working DATABASE_URL")
    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        await conn.execute(SQL.read_text())
        print("[apply] part1 executed")
        for label, sql in {
            "role_grants": """
                SELECT p.name AS permission, r.name AS role
                FROM role_permissions rp
                JOIN permissions p ON p.id = rp.permission_id
                JOIN roles r ON r.id = rp.role_id
                WHERE p.resource = 'workflows' ORDER BY p.name, r.name
            """,
            "profile_grants": """
                SELECT pp.permission_key, pr.name AS profile
                FROM profile_permissions pp
                JOIN profiles pr ON pr.id = pp.profile_id
                WHERE pp.permission_key LIKE '%workflow%'
                ORDER BY pp.permission_key, pr.name
            """,
            "org_admins_without_profile": """
                SELECT count(*) AS n FROM users
                WHERE role = 'org_admin' AND profile_id IS NULL
            """,
        }.items():
            print(f"\n===== {label} =====")
            for r in await conn.fetch(sql):
                print("  " + " | ".join(f"{k}={v}" for k, v in dict(r).items()))
    finally:
        await conn.close()


asyncio.run(main())
