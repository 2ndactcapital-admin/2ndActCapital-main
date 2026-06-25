"""Current-user endpoint (Sprint 9).

``GET /users/me`` returns the caller's profile plus resolved role and permission
list, used by the frontend to gate UI elements (scoring, document review,
pipeline, admin nav).
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from routers.entities import get_org_id
from services.database import get_pool
from services.rbac import get_user_permissions, get_user_roles
from services.users import ensure_user

router = APIRouter(tags=["users"])


class MeResponse(BaseModel):
    id: str
    email: str | None = None
    full_name: str | None = None
    role: str | None = None
    roles: list[str] = []
    permissions: list[str] = []


@router.get("/users/me", response_model=MeResponse)
async def get_me(request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        profile = await conn.fetchrow(
            "SELECT id, email, full_name, role FROM users WHERE id = $1",
            user_id,
        )

    roles = await get_user_roles(pool, user_id, org_id)
    role_names = [r["name"] for r in roles]
    permissions = sorted(await get_user_permissions(pool, user_id, org_id))

    # Primary role: first assigned RBAC role, else the denormalized users.role.
    primary_role = role_names[0] if role_names else (
        profile["role"] if profile else None
    )

    return MeResponse(
        id=user_id,
        email=profile["email"] if profile else None,
        full_name=profile["full_name"] if profile else None,
        role=primary_role,
        roles=role_names,
        permissions=permissions,
    )
