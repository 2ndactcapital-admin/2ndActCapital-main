"""Shared Doppler->DATABASE_URL bootstrap for unattended scripts.

Extracted verbatim in behaviour from ``_run_refresh_schema.py``: hydrate from
Doppler over HTTPS, then repair DATABASE_URL's stale embedded password with the
separate, current ``DB_PASSWORD`` secret. Probe-before-substitute so this
self-heals the day the URL is fixed upstream.

No secret value is ever printed.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import urllib.parse

HERE = pathlib.Path(__file__).resolve()
API_DIR = HERE.parents[1]

for _sp in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    if str(_sp) not in sys.path:
        sys.path.insert(0, str(_sp))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))


async def _probe(candidate: str) -> bool:
    import asyncpg

    try:
        conn = await asyncpg.connect(
            candidate, statement_cache_size=0, ssl="require", timeout=20
        )
    except Exception:  # noqa: BLE001
        return False
    await conn.close()
    return True


async def bootstrap_async(*, quiet: bool = False) -> str | None:
    """Hydrate env from Doppler and return a WORKING DATABASE_URL, or None."""
    from _doppler_env import hydrate_from_doppler

    names, error = hydrate_from_doppler()
    if error:
        if not quiet:
            print(f"[doppler] hydrate failed: {error}")
        return os.environ.get("DATABASE_URL") or None
    if not quiet:
        print(f"[doppler] hydrated {len(names)} secrets")

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None
    if await _probe(url):
        return url

    password = os.environ.get("DB_PASSWORD", "")
    if not password:
        return None
    parts = urllib.parse.urlparse(url)
    netloc = (
        f"{urllib.parse.quote(parts.username or '')}:"
        f"{urllib.parse.quote(password, safe='')}@{parts.hostname}:{parts.port}"
    )
    repaired = urllib.parse.urlunparse(
        (parts.scheme, netloc, parts.path, parts.params, parts.query, parts.fragment)
    )
    if await _probe(repaired):
        os.environ["DATABASE_URL"] = repaired
        if not quiet:
            print("[doppler] DATABASE_URL password was stale; substituted DB_PASSWORD")
        return repaired
    return None


def bootstrap(*, quiet: bool = False) -> str | None:
    """Sync wrapper. Only valid outside a running event loop."""
    return asyncio.run(bootstrap_async(quiet=quiet))
