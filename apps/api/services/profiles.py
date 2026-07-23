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
    """Union of the user's profile grants and all permission-set grants.

    Reads ``users.profile_id`` for the user, then combines its base grants
    with the additive grants from any assigned permission sets.
    """
    async with pool.acquire() as conn:
        profile_id = await conn.fetchval(
            "SELECT profile_id FROM users WHERE id = $1", user_id
        )
    profile_perms = await get_profile_permissions(pool, profile_id)
    set_perms = await get_permission_set_permissions(pool, user_id)
    return profile_perms | set_perms


async def user_has_permission(pool, user_id, permission_key: str) -> bool:
    """True if the user's profile OR any assigned permission set grants the key.

    This is the profile-layer check only; it does not consult roles or the
    Super/Org Admin flags. Callers that need the full picture should also
    consult ``services.rbac`` and the admin helpers as appropriate.
    """
    return permission_key in await get_effective_permissions(pool, user_id)
