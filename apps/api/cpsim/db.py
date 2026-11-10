"""cpsim01 — the simulator's OWN database access.

Every unit of work runs inside one transaction that first says ``SET LOCAL ROLE cpsim_app`` (docs/cpsim01_part1.sql):
the app's login role ``app_service`` is a member of ``cpsim_app`` WITH INHERIT FALSE, SET TRUE, so it holds none of the
simulator's privileges until that statement — and the role ends with the transaction (``SET LOCAL``, safe under the
transaction pooler). No new login, no new secret: the same ``DATABASE_URL``.

Two entry points:
  * :func:`tx` — the sub-app's own small pool (independent of services.database, so the package lifts out cleanly);
  * :func:`as_simulator` — for the Super Admin admin router, which already holds a connection inside a transaction on
    the app pool: enter the simulator role for the cpsim work, then :func:`leave_simulator` before writing audit_log.

Rule 2 (PgBouncer): ``statement_cache_size=0`` on the pool.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import asyncpg

ROLE = "cpsim_app"
_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL is not set")
        kwargs = {"statement_cache_size": 0, "min_size": 0, "max_size": 4}
        if "sslmode=" not in dsn and "localhost" not in dsn and "127.0.0.1" not in dsn:
            kwargs["ssl"] = "require"
        _pool = await asyncpg.create_pool(dsn=dsn, **kwargs)
    return _pool


async def close_pool() -> None:
    global _pool
    p, _pool = _pool, None
    if p is not None:
        await p.close()


async def as_simulator(conn) -> None:
    """Enter role cpsim_app for the rest of the caller's CURRENT transaction (refuses outside one)."""
    if not conn.is_in_transaction():
        raise RuntimeError("cpsim.db.as_simulator: must run inside a transaction (SET LOCAL)")
    await conn.execute(f"SET LOCAL ROLE {ROLE}")


async def leave_simulator(conn) -> None:
    """Back to the login role for the rest of the transaction (still LOCAL)."""
    await conn.execute("SET LOCAL ROLE NONE")


@asynccontextmanager
async def tx():
    """One simulator transaction on the sub-app's own pool, as cpsim_app, with no tenant context."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id', '', true), "
                               "set_config('app.is_super_admin', 'false', true)")
            await as_simulator(conn)
            yield conn
