"""Database-backed RBAC (Sprint 9; fail-closed since AUTHZ1).

Resolves a user's effective permissions by joining
``user_roles -> role_permissions -> permissions``. These are the real checks;
``services.authz`` builds its per-request Principal from the same queries.

AUTHZ1: there is NO default-allow. A user with no role rows holds no
permissions. The old "zero roles = allow everything" bootstrap shielded a lone
operator before RBAC was populated, but it also admitted every brand-new login
(and, through ``get_user_id``'s uuid5 phantom id, every real caller) to every
``require_permission`` gate in the app. Super Admin is still checked FIRST.
"""

from uuid import UUID

from fastapi import HTTPException

async def fetch_role_names(conn, user_id, org_id=None) -> list[str]:
    """Role names ``user_id`` holds — only roles of ``org_id`` when given.

    A role is per-org (``roles.org_id``); filtering here means a grant in one
    org can never surface in another org's Principal even on a connection
    whose RLS context would let both through (super admin).
    """
    rows = await conn.fetch(
        """
        SELECT r.name
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = $1 AND ($2::uuid IS NULL OR r.org_id = $2::uuid)
        ORDER BY r.name
        """,
        user_id, org_id,
    )
    return [r["name"] for r in rows]


async def fetch_permission_names(conn, user_id, org_id=None) -> set[str]:
    """Permission names granted to ``user_id`` through roles (of ``org_id``)."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT p.name
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        JOIN role_permissions rp ON rp.role_id = ur.role_id
        JOIN permissions p ON p.id = rp.permission_id
        WHERE ur.user_id = $1 AND ($2::uuid IS NULL OR r.org_id = $2::uuid)
        """,
        user_id, org_id,
    )
    return {r["name"] for r in rows}


async def fetch_all_permission_names(conn) -> set[str]:
    """The whole catalog — a Super Admin's effective permission set."""
    return {r["name"] for r in await conn.fetch("SELECT name FROM permissions")}


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


async def has_permission(pool, user_id, org_id, permission_name: str) -> bool:
    """True if the user holds ``permission_name``.

    Super Admin (platform staff) always passes — checked FIRST, before any
    other logic runs, via the one shared ``is_super_admin`` helper. Then the
    check is exact: the permission must be granted through a role. There is no
    branch for "the user has no roles" — that user holds nothing (AUTHZ1).
    """
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    if is_super_admin(principal):
        return True
    if principal is None:
        return False
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

    Grants only — the same exact rule ``has_permission`` applies (AUTHZ1
    removed the zero-role default-allow from both).
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

    Exact since AUTHZ1: a user with no roles is not an org admin.
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


# ── org_admin write-path reconciliation (orgadminwrites.structural) ────────
#
# org_admin role reconciliation (see above) migrated every EXISTING holder
# and switched every READER to resolve by permission. It deliberately left
# every WRITER alone. These helpers are the one, real, reusable mechanism for
# keeping a user_roles grant and the users.role string in lockstep going
# forward — lifted from `scripts/_apply_orgadminrole_migration.py`'s one-time
# migration logic, made safe to call on every invite/promotion/demotion
# instead of only once. Every statement is additive/ON CONFLICT DO NOTHING or
# scoped to exactly the (user_id, org_id, org_admin) triple — never a second,
# bespoke grant mechanism.

ORG_ADMIN_ROLE_DESCRIPTION = (
    "Organization administrator — manages org-level settings, profiles, "
    "permission sets, and workflow authoring for this org."
)


async def ensure_permission(conn, name: str, resource: str, action: str) -> str:
    """Idempotently ensure a row exists in the ``permissions`` catalog."""
    return await conn.fetchval(
        """
        INSERT INTO permissions (name, resource, action)
        VALUES ($1, $2, $3)
        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        name, resource, action,
    )


async def ensure_role(conn, org_id, name: str, description: str) -> str:
    """Idempotently ensure ``org_id`` has a role named ``name``.

    ``roles`` is per-org (``(org_id, name)`` UNIQUE) — there is no global role
    catalog, so this must be called per-org, not once globally.
    """
    return await conn.fetchval(
        """
        INSERT INTO roles (org_id, name, description)
        VALUES ($1, $2, $3)
        ON CONFLICT (org_id, name) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        org_id, name, description,
    )


async def ensure_org_admin_role(conn, org_id) -> str:
    """Idempotently ensure THIS org has an org_admin role wired to
    ``manage_org_settings``. Safe to call unconditionally (e.g. on every
    invite or every ``GET /admin/roles``) — an org that already has the role
    is a no-op; an org that has never had an org_admin holder before (e.g.
    Hollisworks, confirmed live to have zero today) gets one created on
    demand instead of needing a second manual migration. Returns the role id.
    """
    perm_id = await ensure_permission(conn, ORG_ADMIN_PERMISSION, "org_settings", "manage")
    role_id = await ensure_role(conn, org_id, ORG_ADMIN_ROLE, ORG_ADMIN_ROLE_DESCRIPTION)
    await conn.execute(
        "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
        "ON CONFLICT DO NOTHING",
        role_id, perm_id,
    )
    return role_id


async def grant_org_admin(conn, user_id, org_id) -> None:
    """Additive: ensure ``user_id`` holds the org_admin RBAC grant IN
    ``org_id``. Never touches any other role the user may separately hold —
    ``user_roles`` is a plain (user_id, role_id) join, not single-valued."""
    role_id = await ensure_org_admin_role(conn, org_id)
    await conn.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        user_id, role_id,
    )


async def revoke_org_admin(conn, user_id, org_id) -> None:
    """Remove ONLY the org_admin grant scoped to ``org_id`` — never another
    org's org_admin role, never another role this user separately holds."""
    await conn.execute(
        """
        DELETE FROM user_roles
        WHERE user_id = $1
          AND role_id = (SELECT id FROM roles WHERE org_id = $2 AND name = $3)
        """,
        user_id, org_id, ORG_ADMIN_ROLE,
    )


async def has_org_admin_grant(conn, user_id, org_id) -> bool:
    """True if ``user_id`` holds a real user_roles grant for org_id's own
    org_admin role — used to detect string/grant drift, not as a gate
    (``is_org_admin`` above is the gate)."""
    return bool(await conn.fetchval(
        """
        SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = $1 AND r.org_id = $2 AND r.name = $3
        """,
        user_id, org_id, ORG_ADMIN_ROLE,
    ))


async def load_principal(conn, user_id) -> dict | None:
    """Fetch the minimal ``{id, org_id, role}`` the checks above operate on.

    Read from ``users.role`` rather than ``user_roles`` because the
    platform/tenant admin distinction is a property of the account itself,
    not of a per-org role grant.

    A row that is not admitted (``status`` other than 'active', or
    ``is_active`` false) is returned with ``role`` blanked to None, so
    ``is_super_admin`` can never pass for a pending, retired or deactivated
    account no matter what its role string says (AUTHZ1).
    """
    try:
        user_uuid = UUID(str(user_id))
    except (ValueError, TypeError):
        return None
    row = await conn.fetchrow(
        "SELECT id, org_id, role, status, is_active FROM users WHERE id = $1",
        user_uuid,
    )
    if row is None:
        return None
    admitted = row["status"] == "active" and bool(row["is_active"])
    return {
        "id": str(row["id"]),
        "org_id": str(row["org_id"]),
        "role": row["role"] if admitted else None,
    }
