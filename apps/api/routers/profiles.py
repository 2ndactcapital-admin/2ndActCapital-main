"""Admin endpoints: Profiles + Permission Sets (SOC Phase A — admin UI).

Management CRUD on top of the already-verified SOC Phase 1 permission layer
(``services.profiles``). Lets an Org Admin (own org) or Super Admin:

  * list / create / delete org profiles and toggle each profile's grants
    against the action registry (``permissions`` table);
  * list / create / delete permission sets, toggle their grants, and
    assign / remove a set from a specific user (the ADDITIVE layer);
  * set a user's ``users.profile_id`` (see also the user-management screen).

SCOPE / SAFETY: these endpoints only manage the profile-layer data that
``services.profiles`` reads. They do NOT change roles (``users.role`` /
``user_roles``) or any enforcement path. ``org_id`` is always resolved
server-side (never from a request body); permission keys are validated
against the ``permissions`` registry before they are written.

Gate: ``can_manage_org_settings`` — super_admin anywhere, org_admin at home.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routers.entities import get_org_id
from services.audit import write_audit_log
from services.database import get_pool
from services.rbac import can_manage_org_settings, load_principal
from services.users import ensure_user

router = APIRouter(tags=["admin", "profiles"])


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class PermissionOption(BaseModel):
    name: str
    resource: str
    action: str


class Profile(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    is_seed: bool = False
    user_count: int = 0
    permission_keys: list[str] = []


class ProfileCreate(BaseModel):
    name: str
    description: str | None = None


class PermissionSetUser(BaseModel):
    user_id: UUID
    full_name: str | None = None
    email: str | None = None


class PermissionSet(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    permission_keys: list[str] = []
    users: list[PermissionSetUser] = []


class PermissionSetCreate(BaseModel):
    name: str
    description: str | None = None


class GrantToggle(BaseModel):
    permission_key: str
    granted: bool


class UserAssign(BaseModel):
    user_id: UUID


class ProfileAssign(BaseModel):
    # Nullable: clears the user's profile when None.
    profile_id: UUID | None = None


# --------------------------------------------------------------------------
# Auth helper — Org Admin (own org) or Super Admin (any org)
# --------------------------------------------------------------------------
async def _require_admin(request: Request) -> tuple[str, str]:
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if principal is None:
        principal = {"id": actor_id, "org_id": org_id, "role": None}
    if not can_manage_org_settings(principal, org_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    return actor_id, org_id


async def _valid_permission_keys(conn) -> set[str]:
    """The action registry — the flat keys the checklist UI edits."""
    rows = await conn.fetch("SELECT name FROM permissions")
    return {r["name"] for r in rows}


# --------------------------------------------------------------------------
# Action registry (checklist source)
# --------------------------------------------------------------------------
@router.get("/admin/permissions", response_model=list[PermissionOption])
async def list_permissions(request: Request):
    """Full action-registry permission list for the grant checklist."""
    await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT name, resource, action FROM permissions ORDER BY resource, action"
        )
    return [PermissionOption(**dict(r)) for r in rows]


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------
@router.get("/admin/profiles", response_model=list[Profile])
async def list_profiles(request: Request):
    _, org_id = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.id, p.name, p.description, p.is_seed,
                   (SELECT count(*) FROM users u WHERE u.profile_id = p.id) AS user_count,
                   COALESCE(
                       array_agg(pp.permission_key) FILTER (WHERE pp.permission_key IS NOT NULL),
                       '{}'
                   ) AS permission_keys
            FROM profiles p
            LEFT JOIN profile_permissions pp ON pp.profile_id = p.id
            WHERE p.org_id = $1
            GROUP BY p.id
            ORDER BY p.is_seed DESC, p.name
            """,
            org_id,
        )
    return [
        Profile(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            is_seed=r["is_seed"],
            user_count=int(r["user_count"]),
            permission_keys=sorted(r["permission_keys"]),
        )
        for r in rows
    ]


@router.post("/admin/profiles", response_model=Profile, status_code=201)
async def create_profile(request: Request, body: ProfileCreate):
    actor_id, org_id = await _require_admin(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Profile name is required")
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM profiles WHERE org_id = $1 AND name = $2", org_id, name
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="A profile with that name exists")
        profile_id = await conn.fetchval(
            """
            INSERT INTO profiles (org_id, name, description, is_seed)
            VALUES ($1, $2, $3, false) RETURNING id
            """,
            org_id, name, body.description,
        )
        await write_audit_log(
            conn, org_id=org_id, action="create_profile", table_name="profiles",
            record_id=profile_id,
            new={"name": name, "created_by": str(actor_id)}, actor=actor_id,
        )
    return Profile(
        id=profile_id, name=name, description=body.description,
        is_seed=False, user_count=0, permission_keys=[],
    )


@router.put("/admin/profiles/{profile_id}/permissions")
async def toggle_profile_permission(
    request: Request, profile_id: UUID, body: GrantToggle
):
    actor_id, org_id = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM profiles WHERE id = $1 AND org_id = $2", profile_id, org_id
        )
        if not exists:
            raise HTTPException(status_code=404, detail="Profile not found in org")
        if body.permission_key not in await _valid_permission_keys(conn):
            raise HTTPException(status_code=422, detail="Unknown permission key")

        if body.granted:
            await conn.execute(
                """
                INSERT INTO profile_permissions (org_id, profile_id, permission_key)
                VALUES ($1, $2, $3)
                ON CONFLICT (profile_id, permission_key) DO NOTHING
                """,
                org_id, profile_id, body.permission_key,
            )
        else:
            await conn.execute(
                """
                DELETE FROM profile_permissions
                WHERE profile_id = $1 AND permission_key = $2
                """,
                profile_id, body.permission_key,
            )
        await write_audit_log(
            conn, org_id=org_id, action="toggle_profile_permission",
            table_name="profile_permissions", record_id=profile_id,
            new={"permission_key": body.permission_key, "granted": body.granted,
                 "actor": str(actor_id)},
            actor=actor_id,
        )
        keys = await conn.fetch(
            "SELECT permission_key FROM profile_permissions WHERE profile_id = $1",
            profile_id,
        )
    return {"ok": True, "permission_keys": sorted(r["permission_key"] for r in keys)}


@router.delete("/admin/profiles/{profile_id}", status_code=200)
async def delete_profile(request: Request, profile_id: UUID):
    actor_id, org_id = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT is_seed FROM profiles WHERE id = $1 AND org_id = $2",
            profile_id, org_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Profile not found in org")
        if row["is_seed"]:
            raise HTTPException(status_code=409, detail="Seed profiles cannot be deleted")
        assigned = await conn.fetchval(
            "SELECT count(*) FROM users WHERE profile_id = $1", profile_id
        )
        if int(assigned) > 0:
            raise HTTPException(
                status_code=409,
                detail=f"{assigned} user(s) still assigned this profile; reassign first",
            )
        # Clear grants, then the profile itself.
        await conn.execute(
            "DELETE FROM profile_permissions WHERE profile_id = $1", profile_id
        )
        await conn.execute("DELETE FROM profiles WHERE id = $1", profile_id)
        await write_audit_log(
            conn, org_id=org_id, action="delete_profile", table_name="profiles",
            record_id=profile_id, new={"deleted_by": str(actor_id)}, actor=actor_id,
        )
    return {"ok": True}


# --------------------------------------------------------------------------
# Permission sets
# --------------------------------------------------------------------------
@router.get("/admin/permission-sets", response_model=list[PermissionSet])
async def list_permission_sets(request: Request):
    _, org_id = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        set_rows = await conn.fetch(
            """
            SELECT ps.id, ps.name, ps.description,
                   COALESCE(
                       array_agg(psp.permission_key)
                           FILTER (WHERE psp.permission_key IS NOT NULL),
                       '{}'
                   ) AS permission_keys
            FROM permission_sets ps
            LEFT JOIN permission_set_permissions psp
                ON psp.permission_set_id = ps.id
            WHERE ps.org_id = $1
            GROUP BY ps.id
            ORDER BY ps.name
            """,
            org_id,
        )
        user_rows = await conn.fetch(
            """
            SELECT ups.permission_set_id, u.id AS user_id, u.full_name, u.email
            FROM user_permission_sets ups
            JOIN users u ON u.id = ups.user_id
            JOIN permission_sets ps ON ps.id = ups.permission_set_id
            WHERE ps.org_id = $1
            ORDER BY u.full_name NULLS LAST, u.email
            """,
            org_id,
        )
    users_by_set: dict[str, list[PermissionSetUser]] = {}
    for r in user_rows:
        users_by_set.setdefault(str(r["permission_set_id"]), []).append(
            PermissionSetUser(
                user_id=r["user_id"], full_name=r["full_name"], email=r["email"]
            )
        )
    return [
        PermissionSet(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            permission_keys=sorted(r["permission_keys"]),
            users=users_by_set.get(str(r["id"]), []),
        )
        for r in set_rows
    ]


@router.post("/admin/permission-sets", response_model=PermissionSet, status_code=201)
async def create_permission_set(request: Request, body: PermissionSetCreate):
    actor_id, org_id = await _require_admin(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Permission set name is required")
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT id FROM permission_sets WHERE org_id = $1 AND name = $2",
            org_id, name,
        )
        if existing is not None:
            raise HTTPException(
                status_code=409, detail="A permission set with that name exists"
            )
        set_id = await conn.fetchval(
            """
            INSERT INTO permission_sets (org_id, name, description)
            VALUES ($1, $2, $3) RETURNING id
            """,
            org_id, name, body.description,
        )
        await write_audit_log(
            conn, org_id=org_id, action="create_permission_set",
            table_name="permission_sets", record_id=set_id,
            new={"name": name, "created_by": str(actor_id)}, actor=actor_id,
        )
    return PermissionSet(
        id=set_id, name=name, description=body.description,
        permission_keys=[], users=[],
    )


@router.put("/admin/permission-sets/{set_id}/permissions")
async def toggle_permission_set_permission(
    request: Request, set_id: UUID, body: GrantToggle
):
    actor_id, org_id = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM permission_sets WHERE id = $1 AND org_id = $2",
            set_id, org_id,
        )
        if not exists:
            raise HTTPException(status_code=404, detail="Permission set not found in org")
        if body.permission_key not in await _valid_permission_keys(conn):
            raise HTTPException(status_code=422, detail="Unknown permission key")

        if body.granted:
            await conn.execute(
                """
                INSERT INTO permission_set_permissions
                    (org_id, permission_set_id, permission_key)
                VALUES ($1, $2, $3)
                ON CONFLICT (permission_set_id, permission_key) DO NOTHING
                """,
                org_id, set_id, body.permission_key,
            )
        else:
            await conn.execute(
                """
                DELETE FROM permission_set_permissions
                WHERE permission_set_id = $1 AND permission_key = $2
                """,
                set_id, body.permission_key,
            )
        await write_audit_log(
            conn, org_id=org_id, action="toggle_permission_set_permission",
            table_name="permission_set_permissions", record_id=set_id,
            new={"permission_key": body.permission_key, "granted": body.granted,
                 "actor": str(actor_id)},
            actor=actor_id,
        )
        keys = await conn.fetch(
            "SELECT permission_key FROM permission_set_permissions "
            "WHERE permission_set_id = $1",
            set_id,
        )
    return {"ok": True, "permission_keys": sorted(r["permission_key"] for r in keys)}


@router.delete("/admin/permission-sets/{set_id}", status_code=200)
async def delete_permission_set(request: Request, set_id: UUID):
    actor_id, org_id = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM permission_sets WHERE id = $1 AND org_id = $2",
            set_id, org_id,
        )
        if not exists:
            raise HTTPException(status_code=404, detail="Permission set not found in org")
        await conn.execute(
            "DELETE FROM user_permission_sets WHERE permission_set_id = $1", set_id
        )
        await conn.execute(
            "DELETE FROM permission_set_permissions WHERE permission_set_id = $1",
            set_id,
        )
        await conn.execute("DELETE FROM permission_sets WHERE id = $1", set_id)
        await write_audit_log(
            conn, org_id=org_id, action="delete_permission_set",
            table_name="permission_sets", record_id=set_id,
            new={"deleted_by": str(actor_id)}, actor=actor_id,
        )
    return {"ok": True}


@router.post("/admin/permission-sets/{set_id}/users", status_code=201)
async def assign_permission_set(request: Request, set_id: UUID, body: UserAssign):
    actor_id, org_id = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        set_ok = await conn.fetchval(
            "SELECT 1 FROM permission_sets WHERE id = $1 AND org_id = $2",
            set_id, org_id,
        )
        if not set_ok:
            raise HTTPException(status_code=404, detail="Permission set not found in org")
        user_ok = await conn.fetchval(
            "SELECT 1 FROM users WHERE id = $1 AND org_id = $2", body.user_id, org_id
        )
        if not user_ok:
            raise HTTPException(status_code=404, detail="User not found in org")
        await conn.execute(
            """
            INSERT INTO user_permission_sets (user_id, permission_set_id, granted_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, permission_set_id) DO NOTHING
            """,
            body.user_id, set_id, actor_id,
        )
    return {"ok": True}


@router.delete("/admin/permission-sets/{set_id}/users/{user_id}", status_code=200)
async def remove_permission_set(request: Request, set_id: UUID, user_id: UUID):
    _, org_id = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        set_ok = await conn.fetchval(
            "SELECT 1 FROM permission_sets WHERE id = $1 AND org_id = $2",
            set_id, org_id,
        )
        if not set_ok:
            raise HTTPException(status_code=404, detail="Permission set not found in org")
        await conn.execute(
            "DELETE FROM user_permission_sets "
            "WHERE permission_set_id = $1 AND user_id = $2",
            set_id, user_id,
        )
    return {"ok": True}


# --------------------------------------------------------------------------
# User → profile assignment (Task 3; also reachable from user-management screen)
# --------------------------------------------------------------------------
@router.put("/admin/users/{user_id}/profile", status_code=200)
async def set_user_profile(request: Request, user_id: UUID, body: ProfileAssign):
    """Set (or clear) ``users.profile_id``. Leaves ``users.role`` untouched —
    profile is a separate, additive field."""
    actor_id, org_id = await _require_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_ok = await conn.fetchval(
            "SELECT 1 FROM users WHERE id = $1 AND org_id = $2", user_id, org_id
        )
        if not user_ok:
            raise HTTPException(status_code=404, detail="User not found in org")
        if body.profile_id is not None:
            profile_ok = await conn.fetchval(
                "SELECT 1 FROM profiles WHERE id = $1 AND org_id = $2",
                body.profile_id, org_id,
            )
            if not profile_ok:
                raise HTTPException(status_code=404, detail="Profile not found in org")
        await conn.execute(
            "UPDATE users SET profile_id = $1, updated_at = now() WHERE id = $2",
            body.profile_id, user_id,
        )
        await write_audit_log(
            conn, org_id=org_id, action="set_user_profile", table_name="users",
            record_id=user_id,
            new={"profile_id": str(body.profile_id) if body.profile_id else None,
                 "actor": str(actor_id)},
            actor=actor_id,
        )
    return {"ok": True, "profile_id": str(body.profile_id) if body.profile_id else None}
