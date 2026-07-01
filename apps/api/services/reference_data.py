"""Reference data service with in-process TTL cache (300 s).

Queries the `reference_data` table (deployed by the live Sprint 16
migration). Columns: id, org_id (nullable), list_key, code, label,
parent_code, extra (jsonb), display_order, is_active, created_at.
"""

import asyncio
import time
from typing import Optional

_cache: dict[str, tuple[list, float]] = {}
_lock = asyncio.Lock()
_TTL = 300.0  # seconds


async def get_list(
    pool, list_key: str, parent_code: Optional[str] = None
) -> list[dict]:
    """Return active reference items for a list_key, optionally by parent_code."""
    cache_key = f"{list_key}:{parent_code or ''}"

    async with _lock:
        entry = _cache.get(cache_key)
        if entry and (time.monotonic() - entry[1]) < _TTL:
            return entry[0]

    async with pool.acquire() as conn:
        if parent_code is not None:
            rows = await conn.fetch(
                """
                SELECT code, label, parent_code, display_order, extra
                FROM reference_data
                WHERE list_key = $1 AND parent_code = $2 AND is_active = true
                ORDER BY display_order, label
                """,
                list_key,
                parent_code,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT code, label, parent_code, display_order, extra
                FROM reference_data
                WHERE list_key = $1 AND is_active = true
                ORDER BY display_order, label
                """,
                list_key,
            )

    result = [dict(r) for r in rows]

    async with _lock:
        _cache[cache_key] = (result, time.monotonic())

    return result


async def invalidate(list_key: Optional[str] = None) -> None:
    """Clear the cache entirely or for one list_key."""
    async with _lock:
        if list_key is None:
            _cache.clear()
        else:
            for k in [k for k in _cache if k.startswith(f"{list_key}:")]:
                del _cache[k]
