"""Run the repo-root schema refresh with Doppler-sourced credentials.

The DATABASE_URL in apps/api/.env and ~/.bashrc is a stale copy whose password
Postgres rejects; the working one lives in Doppler. Hydrate first, then delegate
to scripts/refresh_schema.py unchanged.
"""
import os
import pathlib
import runpy
import sys

HERE = pathlib.Path(__file__).resolve()
API_DIR = HERE.parents[1]
REPO_ROOT = API_DIR.parent.parent
sys.path.insert(0, str(HERE.parent))

for site_packages in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    sys.path.insert(0, str(site_packages))

from _doppler_env import hydrate_from_doppler  # noqa: E402

names, error = hydrate_from_doppler()
if error:
    sys.exit(f"Doppler hydrate failed: {error}")
print(f"[doppler] hydrated {len(names)} secrets; DATABASE_URL present: {bool(os.environ.get('DATABASE_URL'))}")


def _repair_database_url() -> None:
    """Doppler's DATABASE_URL carries a stale password; DB_PASSWORD is current.

    Verified 2026-08-26: the embedded password is rejected by Postgres while the
    same URL with DB_PASSWORD substituted authenticates as `postgres`. Probe
    before substituting so this self-heals if the URL is ever fixed upstream.
    """
    import asyncio
    import urllib.parse

    import asyncpg

    url = os.environ.get("DATABASE_URL", "")
    password = os.environ.get("DB_PASSWORD", "")
    if not url or not password:
        return

    async def probe(candidate: str) -> bool:
        try:
            conn = await asyncpg.connect(
                candidate, statement_cache_size=0, ssl="require", timeout=20
            )
        except Exception:  # noqa: BLE001
            return False
        await conn.close()
        return True

    if asyncio.run(probe(url)):
        return

    parts = urllib.parse.urlparse(url)
    netloc = (
        f"{urllib.parse.quote(parts.username or '')}:"
        f"{urllib.parse.quote(password, safe='')}@{parts.hostname}:{parts.port}"
    )
    repaired = urllib.parse.urlunparse(
        (parts.scheme, netloc, parts.path, parts.params, parts.query, parts.fragment)
    )
    if asyncio.run(probe(repaired)):
        os.environ["DATABASE_URL"] = repaired
        print("[doppler] DATABASE_URL password was stale; substituted DB_PASSWORD")


_repair_database_url()

runpy.run_path(str(REPO_ROOT / "scripts" / "refresh_schema.py"), run_name="__main__")
