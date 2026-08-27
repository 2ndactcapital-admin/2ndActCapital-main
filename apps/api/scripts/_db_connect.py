"""Resolve working Postgres credentials for unattended scripts.

Three separate stale-copy bugs have blocked sprints here, in this order:
``apps/api/.env`` went stale, then Doppler's own ``DATABASE_URL`` went stale
while its separate ``DB_PASSWORD`` stayed current. So presence of a URL proves
nothing — every candidate is probed with a real connection before it is used.

``app_service_dsn()`` is the one that matters for RLS work: RLS policies are
inert under ``postgres`` (superuser bypass), so a cross-org isolation test run
on the postgres DSN passes vacuously.
"""

from __future__ import annotations

import os
import pathlib
import sys
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent

for _site in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    if str(_site) not in sys.path:
        sys.path.insert(0, str(_site))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

import asyncpg  # noqa: E402

from _doppler_env import hydrate_from_doppler  # noqa: E402

CONNECT_KWARGS = {"statement_cache_size": 0, "ssl": "require", "timeout": 30}


async def _probe(dsn: str) -> bool:
    try:
        conn = await asyncpg.connect(dsn, **CONNECT_KWARGS)
    except Exception:  # noqa: BLE001
        return False
    await conn.close()
    return True


def _with_password(dsn: str, password: str) -> str:
    parts = urllib.parse.urlparse(dsn)
    netloc = (
        f"{urllib.parse.quote(parts.username or '')}:"
        f"{urllib.parse.quote(password, safe='')}@{parts.hostname}:{parts.port}"
    )
    return urllib.parse.urlunparse(
        (parts.scheme, netloc, parts.path, parts.params, parts.query, parts.fragment)
    )


_hydrated = False


def hydrate() -> str | None:
    """Pull Doppler's secrets over os.environ once. Returns an error string."""
    global _hydrated
    if _hydrated:
        return None
    _, error = hydrate_from_doppler()
    if error is None:
        _hydrated = True
    return error


async def resolve_dsn(var: str) -> tuple[str | None, str]:
    """Return (working dsn, provenance) for ``var``, or (None, reason)."""
    hydrate_error = hydrate()
    candidates: list[tuple[str, str]] = []
    value = os.environ.get(var)
    if value:
        candidates.append((value, f"{var} (doppler/env)"))
        password = os.environ.get("DB_PASSWORD")
        if password:
            candidates.append((_with_password(value, password), f"{var} + DB_PASSWORD"))

    for dsn, provenance in candidates:
        if await _probe(dsn):
            return dsn, provenance
    if not candidates:
        return None, f"{var} is unset" + (f" ({hydrate_error})" if hydrate_error else "")
    return None, f"{var}: no candidate authenticated"


async def app_service_dsn() -> tuple[str | None, str]:
    """The non-superuser DSN. RLS is only real on this one."""
    return await resolve_dsn("APP_SERVICE_DATABASE_URL")


async def admin_dsn() -> tuple[str | None, str]:
    """The postgres DSN — for fixture setup/teardown only, never for RLS proof."""
    return await resolve_dsn("DATABASE_URL")


async def connect(dsn: str) -> asyncpg.Connection:
    return await asyncpg.connect(dsn, **CONNECT_KWARGS)
