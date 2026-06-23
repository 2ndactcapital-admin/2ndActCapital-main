"""Database access layer.

Manages a lazily-initialized asyncpg connection pool sourced from the
``DATABASE_URL`` environment variable. No queries are defined yet — this is a
stub that establishes connection lifecycle management for later work.
"""

import os

import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first use."""
    global _pool
    if _pool is None:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL environment variable is not set")
        _pool = await asyncpg.create_pool(dsn=database_url)
    return _pool


async def close_pool() -> None:
    """Close the connection pool, if one was created."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
