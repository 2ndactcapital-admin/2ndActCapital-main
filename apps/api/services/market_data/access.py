"""THE gate for reading market data (mkt03), and its permissions envelope.

Every market-data read endpoint calls ``require_market_data_read`` and nothing
else decides access. Today it admits any valid, active, authenticated session:

- No existing permission clearly means "may read market data" (mkt03 Task 1c
  [FIND]: the nearest candidates were ``view_dashboard`` and
  ``view_portfolio``; neither is about platform reference data). So none is
  invented, and the envelope publishes ``read_permission = null``.
- The token is checked by ``auth0_jwt_middleware`` (401) and a deactivated
  account by the active-account gate in ``rls_context_middleware`` (403),
  before this function runs. This function re-asserts the session itself so
  it stays safe if the path were ever added to PUBLIC_PATHS by mistake.

ADDING A PERMISSION LATER is a one-line change: set
``MARKET_DATA_READ_PERMISSION`` to the permission's name. The branch below then
resolves it through ``services.rbac.has_permission`` — the shared helper that
checks super-admin FIRST — exactly like every other permission in this app.

The caller's org and super-admin flag come only from the verified session
(``get_org_id(request)`` and the RLS context the middleware resolved), never
from a body, query or path.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from services.database import current_rls_context

# None = gated on a valid session only. Set to a permission name to require it.
MARKET_DATA_READ_PERMISSION: str | None = None


@dataclass(frozen=True)
class MarketDataReader:
    sub: str
    org_id: str
    is_super_admin: bool


async def require_market_data_read(request: Request) -> MarketDataReader:
    claims = getattr(request.state, "user", None) or {}
    sub = claims.get("sub") if isinstance(claims, dict) else None
    if not sub:
        raise HTTPException(status_code=401, detail="Authentication required")

    from routers.entities import get_org_id  # lazy: routers import services, not the reverse

    org_id = get_org_id(request)
    _org, is_super, _sub = current_rls_context()

    if MARKET_DATA_READ_PERMISSION is not None:
        from services.database import get_pool
        from services.rbac import has_permission
        from services.users import ensure_user

        pool = await get_pool()
        async with pool.acquire() as conn:
            user_id = await ensure_user(conn, request)
        if not await has_permission(pool, user_id, org_id, MARKET_DATA_READ_PERMISSION):
            raise HTTPException(
                status_code=403,
                detail=f"Permission required: {MARKET_DATA_READ_PERMISSION}",
            )
    return MarketDataReader(sub=str(sub), org_id=str(org_id), is_super_admin=bool(is_super))


def permissions_envelope(reader: MarketDataReader) -> dict:
    return {
        "can_read": True,
        "can_write": False,  # read-only API: there is no write to grant
        "is_super_admin": reader.is_super_admin,
        "read_permission": MARKET_DATA_READ_PERMISSION,
        "write_permission": None,
    }
