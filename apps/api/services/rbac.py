"""Database-backed RBAC (Sprint 9).

Resolves a user's effective permissions by joining
``user_roles -> role_permissions -> permissions``. These are the *real* checks
that replace the token-claim stubs once roles are assigned in the DB.

Single-admin safety: a user with **no role rows at all** is treated as
default-allow, mirroring the posture documented in ``services.permissions`` —
this prevents locking out the sole operator before RBAC is populated. As soon as
any role is assigned to a user, their permission set becomes authoritative.
"""

from fastapi import HTTPException

# Sentinel meaning "this user has no roles assigned yet" (default-allow stage).
_NO_ROLES = object()


async def get_user_roles(pool, user_id, org_id) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.name
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = $1
            ORDER BY r.name
            """,
            user_id,
        )
    return [dict(r) for r in rows]


async def get_user_permissions(pool, user_id, org_id) -> set[str]:
    """Return the set of permission names the user holds."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT p.name
            FROM user_roles ur
            JOIN role_permissions rp ON rp.role_id = ur.role_id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE ur.user_id = $1
            """,
            user_id,
        )
    return {r["name"] for r in rows}


async def _has_any_role(pool, user_id) -> bool:
    async with pool.acquire() as conn:
        found = await conn.fetchval(
            "SELECT 1 FROM user_roles WHERE user_id = $1 LIMIT 1", user_id
        )
    return found is not None


async def has_permission(pool, user_id, org_id, permission_name: str) -> bool:
    """True if the user holds ``permission_name``.

    Default-allow when the user has no roles assigned (single-admin stage).
    """
    if not await _has_any_role(pool, user_id):
        return True
    perms = await get_user_permissions(pool, user_id, org_id)
    return permission_name in perms


async def require_permission(pool, user_id, org_id, permission_name: str) -> None:
    if not await has_permission(pool, user_id, org_id, permission_name):
        raise HTTPException(
            status_code=403,
            detail=f"Permission required: {permission_name}",
        )


async def get_users_by_role(pool, org_id, role_name: str) -> list[str]:
    """Return the ids of users in ``org_id`` who hold the named role."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT u.id
            FROM users u
            JOIN user_roles ur ON ur.user_id = u.id
            JOIN roles r ON r.id = ur.role_id
            WHERE r.name = $1 AND u.org_id = $2
            """,
            role_name, org_id,
        )
    return [str(r["id"]) for r in rows]
