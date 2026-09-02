"""fee42b post-run state check — the two things a killed verify run could
have left behind: disabled immutability triggers, and orphan fixture rows."""
from __future__ import annotations

import asyncio
import glob
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
for _s in sorted(glob.glob(str(API_DIR / "venv/lib/python3*/site-packages"))):
    if _s not in sys.path:
        sys.path.insert(0, _s)
for _p in (str(HERE), str(API_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _db_connect import admin_dsn, connect  # noqa: E402

TAG = "fee42bverify"


async def main():
    dsn, _ = await admin_dsn()
    conn = await connect(dsn)
    bad = 0
    try:
        for r in await conn.fetch(
            "SELECT tgname, tgenabled FROM pg_trigger"
            " WHERE tgname LIKE 'spv_carry%immutable%' ORDER BY tgname"
        ):
            state = r["tgenabled"]
            ok = state in ("O", b"O")
            bad += 0 if ok else 1
            print(f"  trigger {r['tgname']}: tgenabled={state!r} "
                  f"{'ENABLED' if ok else '!! NOT ENABLED'}")

        for t, sql in (
            ("spv_carry_runs", "SELECT count(*) FROM spv_carry_runs"),
            ("spv_carry_run_lines", "SELECT count(*) FROM spv_carry_run_lines"),
        ):
            n = await conn.fetchval(sql)
            print(f"  {t}: {n} rows")

        leftovers = {
            "users": "SELECT count(*) FROM users WHERE email LIKE '%@' || $1 || '.local'",
            "spvs": "SELECT count(*) FROM spvs WHERE name LIKE $1 || '%'",
            "deals": "SELECT count(*) FROM deals WHERE name LIKE $1 || '%'",
            "entities": "SELECT count(*) FROM entities WHERE display_name LIKE $1 || '%'",
            "profiles": "SELECT count(*) FROM profiles WHERE name LIKE $1 || '%'",
            "workflow_definitions":
                "SELECT count(*) FROM workflow_definitions WHERE name LIKE $1 || '%'",
        }
        for label, sql in leftovers.items():
            n = await conn.fetchval(sql, TAG)
            bad += n
            print(f"  leftover {label}: {n}")

        n = await conn.fetchval(
            "SELECT count(*) FROM assistant_activities "
            "WHERE related_type = 'spv_carry_run'")
        bad += n
        print(f"  leftover assistant_activities(spv_carry_run): {n}")
    finally:
        await conn.close()
    print("\nRESULT:", "CLEAN" if bad == 0 else f"DIRTY ({bad})")
    return 0 if bad == 0 else 1


sys.exit(asyncio.run(main()))
