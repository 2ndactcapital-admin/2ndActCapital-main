"""fee42b Task 1, pass E — confirm the permission key actually exists."""
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


async def main():
    dsn, _ = await admin_dsn()
    conn = await connect(dsn)
    try:
        print("public.permissions rows containing 'billing' or 'portfolio':")
        for r in await conn.fetch(
            "SELECT * FROM permissions WHERE name ILIKE '%billing%'"
            " OR name ILIKE '%portfolio%' ORDER BY name"
        ):
            print("   ", dict(r))
        print("\nEVERY permissions.name (the closed vocabulary):")
        for r in await conn.fetch("SELECT name FROM permissions ORDER BY name"):
            print("   ", r["name"])
    finally:
        await conn.close()


asyncio.run(main())
