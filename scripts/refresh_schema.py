"""Repo-root entry point for the schema snapshot refresh.

Usage (from repo root, what /refresh-schema runs):
    python3 scripts/refresh_schema.py

Loads DATABASE_URL from apps/api/.env when it isn't already in the
environment, puts the apps/api venv on sys.path so asyncpg resolves, then
delegates to apps/api/scripts/refresh_schema.py — the real implementation
documented in docs/refresh_schema.md.
"""
import os
import pathlib
import runpy
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
REAL_SCRIPT = API_DIR / "scripts" / "refresh_schema.py"

for site_packages in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    sys.path.insert(0, str(site_packages))

if not os.environ.get("DATABASE_URL"):
    env_file = API_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip().strip("\"'")
                break

if not os.environ.get("DATABASE_URL"):
    sys.exit("DATABASE_URL is not set and was not found in apps/api/.env")

runpy.run_path(str(REAL_SCRIPT), run_name="__main__")
