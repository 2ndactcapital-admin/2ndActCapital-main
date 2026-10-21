"""Current-user endpoints (Sprint 9 + Sprint 12 prefs).

``GET  /users/me``   — profile + resolved role / permissions
``PATCH /users/me``  — update nav_pinned, assistant_panel_posture
"""

from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel

from services import authz
from services.database import get_pool
from services.users import ensure_user

router = APIRouter(tags=["users"])


class MeResponse(BaseModel):
    id: str
    email: str | None = None
    full_name: str | None = None
    role: str | None = None
    # Sprint 24: the raw users.role value ('member' / 'org_admin' /
    # 'super_admin'). Distinct from `role` above, which prefers a granted
    # user_roles name — the admin gates key off the account role itself.
    account_role: str | None = None
    roles: list[str] = []
    permissions: list[str] = []
    nav_pinned: bool | None = None
    assistant_panel_posture: str | None = None
    # Account lifecycle (user-management sprint). `is_active` is always True by
    # the time a caller can read this — the middleware gate rejects an inactive
    # account's requests before any handler runs — so it is here for
    # completeness and for the admin screens that reuse this shape, not as the
    # enforcement point.
    is_active: bool | None = None
    last_login_at: str | None = None
    # AUTHZ1 — straight from the request's DB Principal (services.authz), the
    # SAME object every API gate reads. `permissions` above is the Principal's
    # effective set (a Super Admin's is the whole catalog); an unadmitted
    # account has none. The front end renders the pending page from `status`.
    status: str | None = None
    is_staff: bool = False
    is_super_admin: bool = False


class MePatch(BaseModel):
    nav_pinned: bool | None = None
    assistant_panel_posture: str | None = None


@router.get("/users/me", response_model=MeResponse)
async def get_me(request: Request):
    principal = await authz.get_principal(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        profile = await conn.fetchrow(
            "SELECT id, email, full_name, role, nav_pinned, assistant_panel_posture, "
            "is_active, last_login_at "
            "FROM users WHERE id = $1",
            UUID(principal.user_id),
        )

    role_names = list(principal.roles)
    primary_role = role_names[0] if role_names else (
        profile["role"] if profile else None
    )

    return MeResponse(
        id=principal.user_id,
        email=profile["email"] if profile else None,
        full_name=profile["full_name"] if profile else None,
        role=primary_role,
        account_role=profile["role"] if profile else None,
        roles=role_names,
        permissions=sorted(principal.permissions),
        nav_pinned=profile["nav_pinned"] if profile else None,
        assistant_panel_posture=profile["assistant_panel_posture"] if profile else None,
        is_active=profile["is_active"] if profile else None,
        last_login_at=(
            str(profile["last_login_at"])
            if profile and profile["last_login_at"]
            else None
        ),
        status=principal.status,
        is_staff=principal.is_staff,
        is_super_admin=principal.is_super_admin,
    )


@router.patch("/users/me", response_model=MeResponse)
async def patch_me(request: Request, body: MePatch):
    """Update per-user preferences stored on the users row."""
    authz.require_active(request)  # AUTHZ1: a pending login may only READ /users/me
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
