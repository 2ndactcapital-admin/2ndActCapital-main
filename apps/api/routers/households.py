"""Admin endpoints: households — flexible rollup groups + strict primary (SOC Phase 3).

Exposes the household service:
  * CRUD on households.
  * Add/remove an entity to/from a household (flexible MANY-TO-MANY).
  * Set/clear an entity's strict primary household (single-value FK).
  * List an entity's flexible households AND, separately, its primary household.
  * Task 2 flexible rollup vs Task 3 strict primary-household net-worth.

SCOPE / SAFETY (SOC Phase 3): household membership grants NO staff visibility.
Nothing here reads/writes ``staff_assignments`` or invokes the staff-visibility
resolver.

Gated by ``manage_members`` (DB-backed RBAC), same as the other SOC admin
endpoints. ``org_id`` is always resolved server-side, never from a request body.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routers.entities import get_org_id
from services.database import get_pool
from services.rbac import require_permission
from services.users import ensure_user
from services import households as hh

router = APIRouter(tags=["admin", "households"])


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class HouseholdCreate(BaseModel):
    name: str


class HouseholdRename(BaseModel):
    name: str


class MemberAdd(BaseModel):
    entity_id: UUID


class PrimarySet(BaseModel):
    household_id: UUID


# --------------------------------------------------------------------------
# Auth helper
# --------------------------------------------------------------------------
async def _require_manage_members(request: Request) -> tuple[str, str]:
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
    await require_permission(pool, actor_id, org_id, "manage_members")
    return actor_id, org_id


def _money(d) -> float:
    # Decimal → float only at the JSON boundary (money is Decimal in the service).
    return float(d)


# --------------------------------------------------------------------------
# Household CRUD
# --------------------------------------------------------------------------
@router.post("/admin/households", status_code=201)
async def create_household(request: Request, body: HouseholdCreate):
    actor_id, org_id = await _require_manage_members(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Household name is required")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await hh.create_household(conn, org_id, name, created_by=actor_id)
    return {"id": str(row["id"]), "name": row["name"]}


@router.patch("/admin/households/{household_id}")
async def rename_household(request: Request, household_id: UUID, body: HouseholdRename):
    _, org_id = await _require_manage_members(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Household name is required")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await hh.rename_household(conn, org_id, household_id, name)
    if row is None:
        raise HTTPException(status_code=404, detail="Household not found")
    return {"id": str(row["id"]), "name": row["name"]}


@router.delete("/admin/households/{household_id}")
async def delete_household(request: Request, household_id: UUID):
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted = await hh.delete_household(conn, org_id, household_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Household not found")
    return {"ok": True}


# --------------------------------------------------------------------------
# Flexible membership (MANY-TO-MANY)
# --------------------------------------------------------------------------
@router.post("/admin/households/{household_id}/members", status_code=201)
async def add_member(request: Request, household_id: UUID, body: MemberAdd):
    actor_id, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            await hh.add_entity_to_household(
                conn, org_id, household_id, body.entity_id, added_by=actor_id
            )
        except ValueError:
            raise HTTPException(status_code=404, detail="Household or entity not found")
    return {"ok": True}


@router.delete("/admin/households/{household_id}/members/{entity_id}")
async def remove_member(request: Request, household_id: UUID, entity_id: UUID):
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Confirm the household is in the caller's org before touching the junction.
        in_org = await conn.fetchval(
            "SELECT 1 FROM households WHERE id = $1 AND org_id = $2",
            household_id, org_id,
        )
        if not in_org:
            raise HTTPException(status_code=404, detail="Household not found")
        await hh.remove_entity_from_household(conn, household_id, entity_id)
    return {"ok": True}


# --------------------------------------------------------------------------
# Strict primary household (single-value FK)
# --------------------------------------------------------------------------
@router.put("/admin/entities/{entity_id}/primary-household")
async def set_primary(request: Request, entity_id: UUID, body: PrimarySet):
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        in_org = await conn.fetchval(
            "SELECT 1 FROM households WHERE id = $1 AND org_id = $2",
            body.household_id, org_id,
        )
        if not in_org:
            raise HTTPException(status_code=404, detail="Household not found")
        updated = await hh.set_primary_household(
            conn, org_id, entity_id, body.household_id
        )
    if not updated:
        raise HTTPException(status_code=404, detail="Entity not found")
    return {"ok": True}


@router.delete("/admin/entities/{entity_id}/primary-household")
async def clear_primary(request: Request, entity_id: UUID):
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        updated = await hh.clear_primary_household(conn, org_id, entity_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Entity not found")
    return {"ok": True}


# --------------------------------------------------------------------------
# Membership queries (flexible list vs strict primary — kept separate)
# --------------------------------------------------------------------------
@router.get("/admin/entities/{entity_id}/households")
async def list_entity_households(request: Request, entity_id: UUID):
    """ALL flexible households the entity belongs to (many-to-many)."""
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await hh.list_households_for_entity(conn, org_id, entity_id)
    return [{"id": str(r["id"]), "name": r["name"]} for r in rows]


@router.get("/admin/entities/{entity_id}/primary-household")
async def get_entity_primary(request: Request, entity_id: UUID):
    """The entity's SINGLE primary household (strict FK), or null."""
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await hh.get_primary_household(conn, org_id, entity_id)
    if row is None:
        return {"primary_household": None}
    return {"primary_household": {"id": str(row["id"]), "name": row["name"]}}


# --------------------------------------------------------------------------
# Rollups — flexible (Task 2) vs strict primary net-worth (Task 3)
# --------------------------------------------------------------------------
@router.get("/admin/households/{household_id}/rollup")
async def household_rollup(request: Request, household_id: UUID):
    """FLEXIBLE reporting rollup across many-to-many members (may overlap)."""
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await hh.household_rollup(conn, org_id, household_id)
    return {**r, "total_holdings_value": _money(r["total_holdings_value"])}


@router.get("/admin/households/{household_id}/networth")
async def household_networth(request: Request, household_id: UUID):
    """STRICT primary-household net-worth aggregate (no double-counting)."""
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        r = await hh.primary_household_networth(conn, org_id, household_id)
    return {**r, "total_holdings_value": _money(r["total_holdings_value"])}


@router.get("/admin/households/networth")
async def all_primary_networth(request: Request):
    """STRICT net-worth aggregate for every primary household in the org."""
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await hh.primary_household_networth(conn, org_id, None)
    return [{**r, "total_holdings_value": _money(r["total_holdings_value"])} for r in rows]
