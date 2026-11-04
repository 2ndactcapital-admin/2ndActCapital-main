"""Profile-based permissions (SOC Phase 1).

A NEW, additive persona layer on top of the existing RBAC/role system. Each
non-admin user may be assigned a single ``profile`` (users.profile_id) — an
org-defined persona (Member, Adviser, CSA / Ops, …). A profile grants a base
set of permission keys via ``profile_permissions``; a user may additionally
hold one or more ``permission_sets`` (via ``user_permission_sets``) that ADD
capabilities on top of the profile via ``permission_set_permissions``.

Permission keys are the flat strings the Sprint-11 action registry gates on
(``AssistantAction.required_permission`` — e.g. 'manage_deals', 'staff'),
which line up with ``permissions.name``. A user's effective profile-layer
permission set is therefore::

    profile grants  ∪  (grants of every permission set assigned to the user)

This layer is deliberately separate from ``services.rbac`` (roles) and from
the Super/Org Admin handling on ``users.role`` — those are untouched.
"""


async def get_profile_permissions(pool, profile_id) -> set[str]:
    """Permission keys granted directly by a profile."""
    if profile_id is None:
        return set()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT permission_key
            FROM profile_permissions
            WHERE profile_id = $1
            """,
            profile_id,
        )
    return {r["permission_key"] for r in rows}


async def get_permission_set_permissions(pool, user_id) -> set[str]:
    """Permission keys added by every permission set assigned to the user."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT psp.permission_key
            FROM user_permission_sets ups
            JOIN permission_set_permissions psp
              ON psp.permission_set_id = ups.permission_set_id
            WHERE ups.user_id = $1
            """,
            user_id,
        )
    return {r["permission_key"] for r in rows}


async def get_effective_permissions(pool, user_id) -> set[str]:
    """The user's effective permissions — THE PRINCIPAL'S (PERMS1).

    PERMS1 made the authz Principal the one source: its single statement
    already unions role grants, profile grants and permission-set grants
    (catalog keys only — the legacy key "staff" grants nothing) and applies the
    BUGFIX2 client rule (a member keeps only member-safe profile / set keys).
    This function used to compute a second, profile-only answer that disagreed
    with every other gate; it now returns exactly ``Principal.permissions`` so
    /users/me, the route gates, the workflow engine and this service give one
    answer. A Super Admin's set is the whole catalog (as on the Principal).
    Fail closed: a user the Principal cannot load (or not admitted) → empty.
    """
    from services.authz import load_principal

    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    if principal is None or not principal.admitted:
        return set()
    return set(principal.permissions)


async def user_has_permission(pool, user_id, permission_key: str) -> bool:
    """True if the user's profile OR any assigned permission set grants the key.

    PERMS1: the full picture — the Principal's permissions (roles ∪ profile ∪
    permission sets, client rule applied; Super Admin holds the catalog).
    """
    return permission_key in await get_effective_permissions(pool, user_id)
