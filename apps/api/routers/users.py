"""Current-user endpoints (Sprint 9 + Sprint 12 prefs).

``GET  /users/me``   — profile + resolved role / permissions
``PATCH /users/me``  — update nav_pinned, assistant_panel_posture
``PUT  /users/me/contact-email`` — USERMGMT1: a person whose account has no
real email (a ``<sub>@placeholder.local`` row) tells the admins how to reach
them. Reachable while PENDING (main.PENDING_ALLOWED_PATHS).
"""

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from services import authz, user_admin
from services.audit import write_audit_log
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
    # USERMGMT1 — the pending page asks for an address only when the account
    # has no real one. `contact_email` is what the person typed: UNVERIFIED,
    # shown to admins, never used for linking or provisioning.
    email_is_placeholder: bool = False
    contact_email: str | None = None


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
            "is_active, last_login_at, contact_email "
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
        email_is_placeholder=user_admin.is_placeholder_email(profile["email"] if profile else None),
        contact_email=profile["contact_email"] if profile else None,
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


class ContactEmailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_email: str


@router.put("/users/me/contact-email", response_model=MeResponse)
async def set_contact_email(request: Request, body: ContactEmailRequest):
    """Record how to reach a person whose account has no real email.

    Allowed while pending — this is the one write a pending login may make,
    and it touches only the caller's OWN row. Refused when the account already
    has a real email. The value is stored as ``contact_email``: it is NOT
    ``users.email``, it is never verified, and ``services.users`` never reads
    it, so typing someone else's address links nothing.
    """
    principal = await authz.get_principal(request)
    email = (body.contact_email or "").strip().lower()
    if not user_admin.valid_email(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        current = await conn.fetchrow(
            "SELECT email, org_id FROM users WHERE id = $1", UUID(principal.user_id)
        )
        if current is None:
            raise HTTPException(status_code=401, detail=authz.NO_USER_DETAIL)
        if not user_admin.is_placeholder_email(current["email"]):
            raise HTTPException(
                status_code=409, detail="Your account already has an email address."
            )
        await conn.execute(
            "UPDATE users SET contact_email = $2, contact_email_at = now(), updated_at = now() "
            "WHERE id = $1",
            UUID(principal.user_id), email,
        )
        await write_audit_log(
            conn,
            org_id=str(current["org_id"]),
            action="set_contact_email",
            table_name="users",
            record_id=UUID(principal.user_id),
            new={"contact_email_set": True, "verified": False},
            actor=principal.user_id,
        )
    return await get_me(request)
