"""Admin endpoints: trading authority grants (SOC Phase 5).

Assign a user's per-entity trading-authority tier
(``inquiry`` | ``limited`` | ``full``) in ``trading_authority_grants``. This is
the data the SOC Phase 5 enforcement engine
(``services.trading_authority.assert_can_propose``) reads to gate who may
propose a money-movement action.

SCOPE / SAFETY (SOC Phase 5): these endpoints only CREATE/READ/DELETE grant
rows. The maker-checker + tier enforcement lives in
``services.trading_authority`` and is HELD for wiring into the assistant
confirm flow at manual review; nothing here changes an existing endpoint's
behavior.

Super Admin only. ``org_id`` is resolved server-side, never from the body.
"""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routers.entities import get_org_id
from services.database import get_pool
from services.rbac import is_super_admin, load_principal
from services.trading_authority import INQUIRY, LIMITED, FULL
from services.users import ensure_user

router = APIRouter(tags=["admin", "trading-authority"])

_VALID_TIERS = {INQUIRY, LIMITED, FULL}


class UpsertGrant(BaseModel):
    entity_id: UUID
    user_id: UUID
    authority_tier: str


async def _require_super_admin(request: Request) -> tuple[str, str]:
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if not is_super_admin(principal):
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return actor_id, org_id


@router.get("/admin/trading-authority")
async def list_grants(request: Request):
    """List the org's trading-authority grants with entity + user display."""
    _, org_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT g.id, g.entity_id, g.user_id, g.authority_tier, g.granted_at,
                   e.display_name AS entity_name,
                   u.full_name    AS user_name,
                   u.email        AS user_email
            FROM trading_authority_grants g
            LEFT JOIN entities e ON e.id = g.entity_id
            LEFT JOIN users u    ON u.id = g.user_id
            WHERE g.org_id = $1
            ORDER BY e.display_name, u.full_name
            """,
            org_id,
        )
    return {
        "grants": [
            {
                "id": str(r["id"]),
                "entity_id": str(r["entity_id"]),
                "entity_name": r["entity_name"],
                "user_id": str(r["user_id"]),
                "user_name": r["user_name"],
                "user_email": r["user_email"],
                "authority_tier": r["authority_tier"],
                "granted_at": r["granted_at"].isoformat() if r["granted_at"] else None,
            }
            for r in rows
        ],
        "tiers": [INQUIRY, LIMITED, FULL],
    }


@router.post("/admin/trading-authority", status_code=201)
async def upsert_grant(request: Request, body: UpsertGrant):
    """Assign (or update) a user's authority tier for an entity."""
    actor_id, org_id = await _require_super_admin(request)
    if body.authority_tier not in _VALID_TIERS:
        raise HTTPException(
            status_code=400,
            detail=f"authority_tier must be one of {sorted(_VALID_TIERS)}",
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        entity_ok = await conn.fetchval(
            "SELECT 1 FROM entities WHERE id = $1 AND org_id = $2",
            body.entity_id, org_id,
        )
        user_ok = await conn.fetchval(
            "SELECT 1 FROM users WHERE id = $1 AND org_id = $2",
            body.user_id, org_id,
        )
        if not entity_ok:
            raise HTTPException(status_code=404, detail="Entity not found in org")
        if not user_ok:
            raise HTTPException(status_code=404, detail="User not found in org")
        row = await conn.fetchrow(
            """
            INSERT INTO trading_authority_grants
                (org_id, entity_id, user_id, authority_tier, granted_by)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (entity_id, user_id) DO UPDATE SET
                authority_tier = EXCLUDED.authority_tier,
                granted_by     = EXCLUDED.granted_by,
                granted_at     = now()
            RETURNING id, authority_tier
            """,
            org_id, body.entity_id, body.user_id, body.authority_tier, actor_id,
        )
    return {"id": str(row["id"]), "authority_tier": row["authority_tier"]}


@router.delete("/admin/trading-authority/{entity_id}/{user_id}")
async def revoke_grant(request: Request, entity_id: UUID, user_id: UUID):
    """Remove a user's authority grant for an entity."""
    _, org_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            """
            DELETE FROM trading_authority_grants
            WHERE org_id = $1 AND entity_id = $2 AND user_id = $3
            RETURNING id
            """,
            org_id, entity_id, user_id,
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="Grant not found")
    return {"ok": True}
