"""Database access layer.

Manages a lazily-initialized asyncpg connection pool sourced from the
``DATABASE_URL`` environment variable. Configured for Supabase: SSL is
enabled and prepared-statement caching is disabled so the pool works with
the transaction-mode (pgBouncer) connection string.
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
        # Supabase requires SSL; pgBouncer (transaction pooling) does not
        # support prepared statements, so disable statement caching.
        _pool = await asyncpg.create_pool(
            dsn=database_url,
            ssl="require",
            statement_cache_size=0,
            min_size=1,
            max_size=10,
        )
    return _pool


async def close_pool() -> None:
    """Close the connection pool, if one was created."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
