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


# ── Sprint 24: platform / org administration roles ────────────────────────
#
# ``users.role`` is free text with no CHECK constraint (confirmed against
# docs/schema_snapshot.sql). Sprint 24 starts using two new values alongside
# the existing 'member':
#
#   'super_admin' — Ripasso platform staff. Belongs to the Ripasso platform
#                   org, but may administer *any* tenant org, so their own
#                   org_id must never restrict which orgs they manage.
#   'org_admin'   — a client tenant's own administrator, scoped strictly to
#                   their users.org_id.
#
# No CHECK constraint is added: the platform-wide role taxonomy is not
# finalised, and constraining the column now risks rejecting values other
# parts of the app already write.

SUPER_ADMIN_ROLE = "super_admin"
ORG_ADMIN_ROLE = "org_admin"


def _field(user, name):
    """Read a field off a users row, dict, or object — whichever we were handed."""
    if user is None:
        return None
    try:
        value = user[name]
    except (TypeError, KeyError, IndexError):
        value = getattr(user, name, None)
    return None if value is None else str(value)


def is_super_admin(user) -> bool:
    """True when the user is Ripasso platform staff.

    Deliberately ignores org_id — a super_admin sits in the Ripasso platform
    org yet administers every tenant.
    """
    return _field(user, "role") == SUPER_ADMIN_ROLE


def is_org_admin(user, org_id) -> bool:
    """True when the user administers ``org_id`` as that org's own admin."""
    if _field(user, "role") != ORG_ADMIN_ROLE:
        return False
    return _field(user, "org_id") == str(org_id)


def can_manage_org_settings(user, org_id) -> bool:
    """Write gate for org_settings: super_admin anywhere, org_admin at home."""
    return is_super_admin(user) or is_org_admin(user, org_id)


async def load_principal(conn, user_id) -> dict | None:
    """Fetch the minimal ``{id, org_id, role}`` the checks above operate on.

    Read from ``users.role`` rather than ``user_roles`` because the
    platform/tenant admin distinction is a property of the account itself,
    not of a per-org role grant.
    """
    row = await conn.fetchrow(
        "SELECT id, org_id, role FROM users WHERE id = $1", user_id
    )
    if row is None:
        return None
    return {"id": str(row["id"]), "org_id": str(row["org_id"]), "role": row["role"]}
