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

    Super Admin (platform staff) always passes — checked FIRST, before any
    other logic runs. This mirrors the explicit ``is_super_admin`` escape hatch
    every RLS policy, ``restricted_access`` check, ``staff_visibility`` gate and
    the Workflow Manager's own permission resolver already carry. Without it, a
    super_admin who ever picks up *any* row in ``user_roles`` would fall through
    to the strict per-permission check below and be silently locked out of every
    endpoint using ``require_permission`` — the "zero roles = default-allow"
    posture only shields them by the accident of that table being empty.

    Default-allow when the user has no roles assigned (single-admin stage).
    """
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    if is_super_admin(principal):
        return True
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


async def get_users_with_permission(pool, org_id, permission_name: str) -> list[str]:
    """Return ids of users in ``org_id`` who hold ``permission_name`` via a
    granted role — org_admin role migration (Task 4), used for alert
    recipient fan-out (``services.workflow_todos``).

    Deliberately does NOT apply ``has_permission``'s zero-role default-allow
    bootstrap: that escape hatch exists to keep a single not-yet-provisioned
    operator from locking themselves out of a page THEY are trying to reach.
    It has no sensible meaning for a broadcast recipient list — "everyone
    with no roles assigned is a recipient" would silently balloon an alert's
    audience rather than protect anyone.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT u.id
            FROM users u
            JOIN user_roles ur ON ur.user_id = u.id
            JOIN role_permissions rp ON rp.role_id = ur.role_id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE u.org_id = $1 AND p.name = $2
            """,
            org_id, permission_name,
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

# ── org_admin role reconciliation ──────────────────────────────────────────
#
# The permission that gates org-level admin access (org_settings writes,
# Profiles, Permission Sets, Workflow authoring, TA model defaults). Resolved
# through the real RBAC axis (``roles`` / ``role_permissions`` / user_roles``)
# now, not the ``users.role`` string below — see ``is_org_admin``.
# ``routers/modeling_ta.py`` already referenced this exact string in its
# envelope's ``write_permission`` field before a real permission row backed
# it; this is that permission, made real.
ORG_ADMIN_PERMISSION = "manage_org_settings"


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


async def is_org_admin(pool, user, org_id) -> bool:
    """True when the user administers ``org_id`` as that org's own admin.

    Resolved by PERMISSION (``ORG_ADMIN_PERMISSION``, via ``has_permission``),
    not by the ``users.role`` string — org_admin role migration Task 4. Still
    scoped to the caller's OWN org_id first: a permission granted in one org
    must never authorize a different org, and ``has_permission`` itself has
    no org-crossing concept to rely on for that.

    Note this inherits ``has_permission``'s zero-role default-allow bootstrap
    for a user who has never been assigned ANY role — the same posture that
    already governs every other ``has_permission``-gated permission in this
    app (``manage_members`` included). It is not a new risk introduced here;
    it is the existing single-admin-safety posture extended consistently to
    this permission. See docs/PROJECT_STATUS.md for the real accounts this
    currently affects.
    """
    if _field(user, "org_id") != str(org_id):
        return False
    user_id = _field(user, "id")
    if user_id is None:
        return False
    return await has_permission(pool, user_id, org_id, ORG_ADMIN_PERMISSION)


async def can_manage_org_settings(pool, user, org_id) -> bool:
    """Write gate for org_settings: super_admin anywhere, org_admin (by
    permission) at home."""
    return is_super_admin(user) or await is_org_admin(pool, user, org_id)


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
