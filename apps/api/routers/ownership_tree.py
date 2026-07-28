"""Ownership-tree graph endpoints (Sprint ownershiptreea — interactive graph).

Two routes over ONE builder, both applying the restricted-access wrapper on top
of their underlying visibility engine:

  GET /entities/{entity_id}/ownership-graph  — STAFF focal tree
      data via the staff visibility engine (assignment + team + hierarchy)
      + ``filter_restricted``.

  GET /me/ownership-graph                     — MEMBER's own tree
      data via the member visibility engine (``resolve_entity_set`` /
      ownership + beneficiary look-through, scoped to the member's own grants)
      + ``filter_restricted``. Focal is derived from identity; an out-of-scope
      focal is a 403 so a member can never reach another member's tree.

Shared query params: ``direction`` (owns|owned_by), ``edge_types`` (csv of
ownership,beneficiary), ``as_of`` (YYYY-MM-DD valid-time slice), ``max_depth``.
``org_id`` and (for the member route) the focal are NEVER trusted from the body.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from routers.entities import get_org_id
from services.database import get_pool
from services.ownership_tree import (
    VALID_DIRECTIONS,
    member_tree,
    parse_edge_types,
    staff_tree,
)
from services.permissions import require_staff
from services.users import ensure_user

router = APIRouter(tags=["ownership_graph"])

_MAX_DEPTH_CAP = 40


def _validate_direction(direction: str) -> str:
    if direction not in VALID_DIRECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"direction must be one of {list(VALID_DIRECTIONS)}",
        )
    return direction


def _clamp_depth(max_depth: int) -> int:
    return max(1, min(_MAX_DEPTH_CAP, max_depth))


async def _current_user_id(request: Request) -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await ensure_user(conn, request)


@router.get("/entities/{entity_id}/ownership-graph")
async def staff_ownership_graph(
    entity_id: UUID,
    request: Request,
    direction: str = Query("owns"),
    edge_types: str = Query("ownership,beneficiary"),
    as_of: Optional[str] = Query(None, description="YYYY-MM-DD"),
    max_depth: int = Query(20),
):
    require_staff(request)
    org_id = get_org_id(request)
    direction = _validate_direction(direction)
    ets = parse_edge_types(edge_types)
    depth = _clamp_depth(max_depth)

    pool = await get_pool()
    user_id = await _current_user_id(request)

    tree = await staff_tree(
        pool,
        org_id,
        str(entity_id),
        user_id,
        direction=direction,
        edge_types=ets,
        as_of=as_of,
        max_depth=depth,
    )
    return {
        "scope": "staff",
        "focal_entity_id": str(entity_id),
        "direction": direction,
        "edge_types": list(ets),
        "as_of": as_of,
        "tree": tree,
    }


@router.get("/me/ownership-graph")
async def member_ownership_graph(
    request: Request,
    focal_id: Optional[UUID] = Query(None),
    direction: str = Query("owns"),
    edge_types: str = Query("ownership,beneficiary"),
    as_of: Optional[str] = Query(None, description="YYYY-MM-DD"),
    max_depth: int = Query(20),
):
    org_id = get_org_id(request)
    direction = _validate_direction(direction)
    ets = parse_edge_types(edge_types)
    depth = _clamp_depth(max_depth)

    pool = await get_pool()
    user_id = await _current_user_id(request)

    try:
        result = await member_tree(
            pool,
            org_id,
            user_id,
            focal_id=str(focal_id) if focal_id else None,
            direction=direction,
            edge_types=ets,
            as_of=as_of,
            max_depth=depth,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    result.update(
        {
            "scope": "member",
            "direction": direction,
            "edge_types": list(ets),
            "as_of": as_of,
        }
    )
    return result
