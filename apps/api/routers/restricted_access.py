"""Admin endpoints: restricted-access accounts (SOC Phase 4).

Lets a Super Admin flag/unflag an entity as restricted and manage its
allow-list (``restricted_access_grants``) so the data the unified
``services.restricted_access.filter_restricted`` filter reads can be populated.

SCOPE / SAFETY (SOC Phase 4): these endpoints only CREATE/READ restriction data
and write to ``restricted_access_audit``. They do NOT change any existing
endpoint's visibility/authorization behavior and do NOT wire the filter into any
enforcement path — that is a separate, later decision.

Super Admin only (``services.rbac.is_super_admin``). ``org_id`` is resolved
server-side (the caller's org for listing; the entity's own org for writes),
never from a request body.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routers.entities import get_org_id
from services.database import get_pool
from services.rbac import is_super_admin, load_principal
from services.users import ensure_user
from services import restricted_access as ra

router = APIRouter(tags=["admin", "restricted-access"])


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class SetRestricted(BaseModel):
    restricted: bool
    notes: str | None = None


class GrantAccess(BaseModel):
    user_id: UUID
    reason: str | None = None


# --------------------------------------------------------------------------
# Auth helper — Super Admin only
# --------------------------------------------------------------------------
async def _require_super_admin(request: Request) -> tuple[str, str]:
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if not is_super_admin(principal):
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return actor_id, org_id


# --------------------------------------------------------------------------
# Read: restricted entities + their allow-lists
# --------------------------------------------------------------------------
@router.get("/admin/restricted")
async def list_restricted(request: Request):
    """List the org's restricted entities, each with its current allow-list."""
    _, org_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        entity_rows = await conn.fetch(
            """
            SELECT id, display_name, entity_type
            FROM entities
            WHERE org_id = $1
              AND access_restricted = true
              AND valid_to IS NULL
              AND system_to IS NULL
            ORDER BY display_name
            """,
            org_id,
        )
        grant_rows = await conn.fetch(
            """
            SELECT g.entity_id, g.user_id, g.reason, g.granted_at,
                   u.full_name, u.email
            FROM restricted_access_grants g
            LEFT JOIN users u ON u.id = g.user_id
            WHERE g.org_id = $1
            ORDER BY g.granted_at DESC
            """,
            org_id,
        )
    grants_by_entity: dict[str, list] = {}
    for r in grant_rows:
        grants_by_entity.setdefault(str(r["entity_id"]), []).append(
            {
                "user_id": str(r["user_id"]),
                "full_name": r["full_name"],
                "email": r["email"],
                "reason": r["reason"],
            }
        )
    return {
        "restricted": [
            {
                "id": str(r["id"]),
                "display_name": r["display_name"],
                "entity_type": r["entity_type"],
                "grants": grants_by_entity.get(str(r["id"]), []),
            }
            for r in entity_rows
        ]
    }


# --------------------------------------------------------------------------
# Write: flip the restricted flag
# --------------------------------------------------------------------------
@router.post("/admin/restricted/{entity_id}")
async def set_restricted_endpoint(
    request: Request, entity_id: UUID, body: SetRestricted
):
    actor_id, org_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM entities WHERE id = $1 AND org_id = $2",
            entity_id, org_id,
        )
    if not exists:
        raise HTTPException(status_code=404, detail="Entity not found in org")
    try:
        result = await ra.set_restricted(
            pool, entity_id, body.restricted, actor_id, notes=body.notes
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return result


# --------------------------------------------------------------------------
# Write: manage the allow-list
# --------------------------------------------------------------------------
@router.post("/admin/restricted/{entity_id}/grants", status_code=201)
async def grant_access_endpoint(
    request: Request, entity_id: UUID, body: GrantAccess
):
    actor_id, org_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM entities WHERE id = $1 AND org_id = $2",
            entity_id, org_id,
        )
        user_ok = await conn.fetchval(
            "SELECT 1 FROM users WHERE id = $1 AND org_id = $2",
            body.user_id, org_id,
        )
    if not exists:
        raise HTTPException(status_code=404, detail="Entity not found in org")
    if not user_ok:
        raise HTTPException(status_code=404, detail="User not found in org")
    try:
        result = await ra.grant_restricted_access(
            pool, entity_id, body.user_id, actor_id, body.reason
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return result


@router.delete("/admin/restricted/{entity_id}/grants/{user_id}")
async def revoke_access_endpoint(
    request: Request, entity_id: UUID, user_id: UUID
):
    actor_id, org_id = await _require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM entities WHERE id = $1 AND org_id = $2",
            entity_id, org_id,
        )
    if not exists:
        raise HTTPException(status_code=404, detail="Entity not found in org")
    try:
        result = await ra.revoke_restricted_access(
            pool, entity_id, user_id, actor_id
        )
    except PermissionError:
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return result
