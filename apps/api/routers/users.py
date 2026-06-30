"""Current-user endpoints (Sprint 9 + Sprint 12 prefs).

``GET  /users/me``   — profile + resolved role / permissions
``PATCH /users/me``  — update nav_pinned, assistant_panel_posture
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
    nav_pinned: bool | None = None
    assistant_panel_posture: str | None = None


class MePatch(BaseModel):
    nav_pinned: bool | None = None
    assistant_panel_posture: str | None = None


@router.get("/users/me", response_model=MeResponse)
async def get_me(request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        profile = await conn.fetchrow(
            "SELECT id, email, full_name, role, nav_pinned, assistant_panel_posture "
            "FROM users WHERE id = $1",
            user_id,
        )

    roles = await get_user_roles(pool, user_id, org_id)
    role_names = [r["name"] for r in roles]
    permissions = sorted(await get_user_permissions(pool, user_id, org_id))

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
        nav_pinned=profile["nav_pinned"] if profile else None,
        assistant_panel_posture=profile["assistant_panel_posture"] if profile else None,
    )


@router.patch("/users/me", response_model=MeResponse)
async def patch_me(request: Request, body: MePatch):
    """Update per-user preferences stored on the users row."""
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)

        # Build SET clause only for fields that were explicitly provided.
        provided = body.model_fields_set
        updates: dict = {}
        if "nav_pinned" in provided:
            updates["nav_pinned"] = body.nav_pinned
        if "assistant_panel_posture" in provided:
            updates["assistant_panel_posture"] = body.assistant_panel_posture

        if updates:
            cols = list(updates.keys())
            set_clause = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
            await conn.execute(
                f"UPDATE users SET {set_clause}, updated_at = now() WHERE id = $1",
                user_id,
                *updates.values(),
            )

    # Return the updated profile (re-use get_me logic).
    return await get_me(request)
