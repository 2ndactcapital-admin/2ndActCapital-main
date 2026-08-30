"""fee38 discovery part 4 — exact insert shape for users + account_balances_daily."""

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
    if dsn is None:
        print(f"NO DSN: {prov}")
        return 1
    conn = await connect(dsn)
    out: dict = {}
    for t in ("users", "account_balances_daily", "accounts", "entities"):
        out[f"{t}.cols"] = [
            dict(r)
            for r in await conn.fetch(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name=$1 ORDER BY ordinal_position",
                t,
            )
        ]
        out[f"{t}.checks"] = [
            r["def"]
            for r in await conn.fetch(
                "SELECT pg_get_constraintdef(c.oid) AS def FROM pg_constraint c "
                "JOIN pg_class k ON k.oid=c.conrelid WHERE k.relname=$1 AND c.contype='c'",
                t,
            )
        ]
    print(json.dumps(out, indent=1, default=str))
    await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
