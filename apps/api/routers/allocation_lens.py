"""Allocation Lens router — Sprint 21 Portfolio Allocation Lens.

GET /api/v1/allocation-lens

Returns the full 3-level taxonomy tree with actual vs target allocation
figures for a given entity selector.  Every taxonomy node is always
present in the response (even when actual = target = 0).

Selector types
--------------
entity   — single entity by entity_id (members + staff)
subtree  — entity_id as root, look-through weighted (members + staff)
group    — group_id (staff only)
all      — all top-level entities (staff only)
"""
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request

from routers.entities import get_org_id
from services.allocation_lens import aggregate_allocation
from services.database import get_pool
from services.permissions import is_staff
from services.users import ensure_user

router = APIRouter(tags=["allocation-lens"])


@router.get("/allocation-lens")
async def get_allocation_lens(
    request: Request,
    selector_type: str = Query("entity"),  # entity|subtree|group|all
    entity_id: str | None = Query(None),
    group_id: str | None = Query(None),
    as_of: date | None = Query(None),
):
    """Return portfolio allocation vs target tree for a given entity selector."""
    pool = await get_pool()

    # Ensure a users row exists for the caller (side-effect: creates on first sight).
    async with pool.acquire() as conn:
        await ensure_user(conn, request)

    org_id = get_org_id(request)
    staff = is_staff(request)

    # Build and validate the selector.
    if selector_type == "entity":
        if not entity_id:
            raise HTTPException(
                status_code=422,
                detail="entity_id is required when selector_type=entity",
            )
        selector = {"type": "entity", "id": entity_id}

    elif selector_type == "subtree":
        if not entity_id:
            raise HTTPException(
                status_code=422,
                detail="entity_id is required when selector_type=subtree",
            )
        selector = {"type": "subtree", "root_id": entity_id}

    elif selector_type == "group":
        if not staff:
            raise HTTPException(
                status_code=403,
                detail="selector_type=group requires staff access",
            )
        if not group_id:
            raise HTTPException(
                status_code=422,
                detail="group_id is required when selector_type=group",
            )
        selector = {"type": "group", "group_id": group_id}

    elif selector_type == "all":
        if not staff:
            raise HTTPException(
                status_code=403,
                detail="selector_type=all requires staff access",
            )
        selector = {"type": "all"}

    else:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown selector_type '{selector_type}'. "
                "Must be one of: entity, subtree, group, all"
            ),
        )

    try:
        result = await aggregate_allocation(pool, selector, org_id, as_of)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return result
