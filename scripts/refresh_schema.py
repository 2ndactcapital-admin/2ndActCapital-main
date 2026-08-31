"""Repo-root entry point for the schema snapshot refresh.

Usage (from repo root, what /refresh-schema runs):
    python3 scripts/refresh_schema.py

Resolves DATABASE_URL through ``apps/api/scripts/_db_connect.resolve_dsn`` —
the same probe-before-use resolver every verify script uses — puts the
apps/api venv on sys.path so asyncpg resolves, then delegates to
apps/api/scripts/refresh_schema.py, the real implementation documented in
docs/refresh_schema.md.

This used to read ``DATABASE_URL=`` straight out of ``apps/api/.env``. That
file holds a STALE password which Postgres rejects, so the refresh failed with
``InvalidPasswordError`` — the same stale-copy bug that has bitten this repo
three separate times. ``resolve_dsn`` hydrates from Doppler and PROBES each
candidate with a real connection before returning it, so presence of a URL is
never mistaken for a working one.
"""
import asyncio
import os
import pathlib
import runpy
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
API_SCRIPTS = API_DIR / "scripts"
REAL_SCRIPT = API_SCRIPTS / "refresh_schema.py"

for site_packages in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    sys.path.insert(0, str(site_packages))
for _path in (str(API_SCRIPTS), str(API_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from _db_connect import admin_dsn  # noqa: E402

dsn, provenance = asyncio.run(admin_dsn())
if not dsn:
    sys.exit(f"No working DATABASE_URL: {provenance}")
os.environ["DATABASE_URL"] = dsn
print(f"DATABASE_URL resolved from {provenance}")

runpy.run_path(str(REAL_SCRIPT), run_name="__main__")
