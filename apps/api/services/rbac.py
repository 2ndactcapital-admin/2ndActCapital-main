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
    """The user's effective permission names — the authz Principal's (PERMS1:
    roles ∪ profile ∪ permission sets, one statement). ``org_id`` given and not
    the user's own org → nothing (a grant never crosses orgs)."""
    from services.authz import load_principal

    principal = await load_principal(conn, user_id)
    if principal is None or not principal.admitted:
        return set()
    if org_id is not None and str(org_id) != principal.org_id:
        return set()
    return set(principal.permissions)


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
    """Return the set of permission names the user holds.

    PERMS1 — the authz Principal's permissions (the ONE source: roles ∪ profile
    ∪ permission sets). It used to read roles only, and of ANY org.
    """
    async with pool.acquire() as conn:
        return await fetch_permission_names(conn, user_id, org_id)


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
    """Return ids of admitted users in ``org_id`` who hold ``permission_name``
    by an explicit grant — alert recipient fan-out (``services.workflow_todos``).

    PERMS1 — the same Principal statement (roles ∪ profile ∪ permission sets,
    client rule applied), evaluated for the org's users at once. The super-
    admin parameter is passed as NULL so a Super Admin is NOT counted as holding
    everything here: recipients are the users explicitly granted the key,
    as before (fan-out never widened to every Super Admin).
    """
    from services.authz import PRINCIPAL_COLUMNS, principal_from_row

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {PRINCIPAL_COLUMNS} FROM users u WHERE u.org_id = $1",
            UUID(str(org_id)), None,
        )
    out = []
    for row in rows:
        p = principal_from_row(row)
        if p is not None and p.admitted and permission_name in p.permissions:
            out.append(str(row["id"]))
    return out


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
#: BUGFIX4 — the ONE Super Admin source, as a SQL expression over ``users u``: a grant of the user's OWN org's
#: ``super_admin`` role. Used by both principal loaders (services.authz, load_principal below).
RBAC_SUPER_SQL = ("EXISTS (SELECT 1 FROM user_roles ur_s JOIN roles r_s ON r_s.id = ur_s.role_id "
                  "WHERE ur_s.user_id = u.id AND r_s.org_id = u.org_id AND r_s.name = 'super_admin')")
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
    flag = getattr(user, "is_super_admin", None)
    if isinstance(flag, bool):                     # a services.authz Principal — already decided from user_roles
        return flag
    return _field(user, "role") == SUPER_ADMIN_ROLE  # a load_principal dict (role derived from user_roles)


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


#: Granted to org_admin by default alongside ``manage_org_settings`` —
#: replication (rep1), execution (rep2), models (rep3), proposals (rep5) and tax (rep6). Kept in step with the
#: docs/rep1_part1.sql §14, docs/rep2_part1.sql §8 and docs/rep3_part1.sql §14 grants.
ORG_ADMIN_DEFAULT_PERMISSIONS = (
    ("view_replication", "replication", "view"),
    ("manage_replication", "replication", "manage"),
    ("approve_replication", "replication", "approve"),
    ("view_execution", "execution", "view"),
    ("manage_execution", "execution", "manage"),
    ("approve_execution", "execution", "approve"),
    # CASH07 — payment instructions (stage A): initiate (draft / submit / mark sent / reconcile) and approve (the S30
    # floor: always a different person). Kept in step with docs/cash07_part1.sql §1.
    ("initiate_payment", "payments", "initiate"),
    ("approve_payment", "payments", "approve"),
    # rep3 — model management (Replication Studio models, uploads, library).
    ("view_models", "models", "view"),
    ("manage_models", "models", "manage"),
    ("approve_models", "models", "approve"),
    # rep5 — accounts join models; rebalance proposals and their approval.
    ("view_proposals", "proposals", "view"),
    ("manage_proposals", "proposals", "manage"),
    ("approve_proposals", "proposals", "approve"),
    # rep6 — the tax engine: profiles, carryforwards, substitutions, declared trades, harvest.
    ("view_tax", "tax", "view"),
    ("manage_tax", "tax", "manage"),
    ("approve_tax", "tax", "approve"),
    # rep7 — held-away accounts: sources, imports (maker-checker), stale-data confirmations.
    ("view_held_away", "held_away", "view"),
    ("manage_held_away", "held_away", "manage"),
    ("approve_held_away", "held_away", "approve"),
    # rep7b — account authority (read_only / advise / managed) and custody; changes are maker-checker.
    ("view_authority", "authority", "view"),
    ("manage_authority", "authority", "manage"),
    ("approve_authority", "authority", "approve"),
    # rep9 — block trading: build / send / fill / allocate / settle blocks; approve blocks, allocation policies and
    # allocation corrections (maker-checker).
    ("view_blocks", "blocks", "view"),
    ("manage_blocks", "blocks", "manage"),
    ("approve_blocks", "blocks", "approve"),
    # rep10 — the firm's hypothetical-performance policy (who may see a backtest; its disclosure); maker-checker.
    ("manage_hypothetical_policy", "hypothetical_policy", "manage"),
    # DISCLOSURES1 — the disclosure library: read it; manage the org's (and its households' / accounts') versions.
    # Publishing is maker-checker (a second holder). Platform defaults are Super Admin only. Kept in step with
    # docs/disclosures1_part1.sql §0 (the grant to every EXISTING org_admin role).
    ("view_disclosures", "disclosures", "view"),
    ("manage_disclosures", "disclosures", "manage"),
    # PERMS1 — the org admin's baseline READS (the ones support staff already hold by role), the workflow keys its
    # "Org Admin" profile carried (now on the role too), and Joe's decisions of 2026-11-04: org admins create deals
    # (DEAL-01, manage_deals) and households (REP-01, manage_members). No approve_* is added here. Kept in step with
    # docs/perms1_part1.sql (the grant to every EXISTING org_admin role).
    ("view_portfolio", "portfolio", "view"),
    ("view_members", "members", "view"),
    ("view_dashboard", "dashboard", "view"),
    ("view_deals", "deals", "view"),
    ("view_workflow_runs", "workflows", "view"),
    ("author_workflows", "workflows", "author"),
    ("configure_workflow_triggers", "workflows", "configure"),
    ("manage_deals", "deals", "manage"),
    ("manage_members", "members", "manage"),
    # CASH02 — cash policies (the three sleeves): author AND approve; SoD makes the approver a different person. Kept
    # in step with docs/cash02_part1.sql §0 (the grant to every EXISTING org_admin / advisor / admin role).
    ("manage_cash_policy", "cash_policy", "manage"),
)

#: PERMS1 — exactly the keys this sprint added to ORG_ADMIN_DEFAULT_PERMISSIONS (the verify reads it).
PERMS1_ORG_ADMIN_ADDED = (
    "view_portfolio", "view_members", "view_dashboard", "view_deals", "view_workflow_runs",
    "author_workflows", "configure_workflow_triggers", "manage_deals", "manage_members",
)


async def ensure_permission(conn, name: str, resource: str, action: str) -> str:
    """Idempotently ensure a row exists in the ``permissions`` catalog.

    BUGFIX2 (NAV-02): READ FIRST, write only when the row is genuinely missing.
    ``permissions`` is a global catalog whose INSERT/UPDATE RLS policies require
    ``app.is_super_admin = 'true'``. The previous single statement,
    ``INSERT ... ON CONFLICT (name) DO UPDATE``, is an UPDATE of the existing
    row under RLS even when the row already exists — so for an org admin (not
    super admin) it raised a row-level-security violation on EVERY call, and
    ``GET /admin/roles`` (which calls ``ensure_org_admin_role`` on each view of
    People & Access) answered 500. A catalog row that already exists needs no
    write at all; a genuinely missing one still needs super-admin scope, as
    before (the INSERT is ``DO NOTHING`` so a concurrent creator is not an
    error).
    """
    existing = await conn.fetchval("SELECT id FROM permissions WHERE name = $1", name)
    if existing is not None:
        return existing
    created = await conn.fetchval(
        """
        INSERT INTO permissions (name, resource, action)
        VALUES ($1, $2, $3)
        ON CONFLICT (name) DO NOTHING
        RETURNING id
        """,
        name, resource, action,
    )
    if created is not None:
        return created
    return await conn.fetchval("SELECT id FROM permissions WHERE name = $1", name)


async def ensure_role(conn, org_id, name: str, description: str) -> str:
    """Idempotently ensure ``org_id`` has a role named ``name``.

    ``roles`` is per-org (``(org_id, name)`` UNIQUE) — there is no global role
    catalog, so this must be called per-org, not once globally.

    BUGFIX2: read first (as ``ensure_permission``) so an existing role costs no
    write — the old ``ON CONFLICT DO UPDATE`` rewrote the row on every call.
    """
    existing = await conn.fetchval(
        "SELECT id FROM roles WHERE org_id = $1 AND name = $2", org_id, name
    )
    if existing is not None:
        return existing
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
    # rep2: the org_admin defaults rep1 / rep2 grant every EXISTING org_admin
    # role by migration, so a NEW org's first org_admin gets them too (rep1's
    # [FIND]: before this, a new org's org_admin held manage_org_settings only).
    for name, resource, action in ORG_ADMIN_DEFAULT_PERMISSIONS:
        pid = await ensure_permission(conn, name, resource, action)
        await conn.execute(
            "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING",
            role_id, pid,
        )
    return role_id


#: PORTENTRY1 (GAP-CRM-06, Joe 2026-11-04) — support staff edit CRM notes / addresses / phones / emails through ONE
#: narrow key; nothing legal, ownership, relationship, tax-id, detail, status or tag. Kept in step with
#: docs/portentry1_part1.sql §2 (the grant to every EXISTING support_staff role and the 'CSA / Ops' profile).
SUPPORT_STAFF_ROLE = "support_staff"
EDIT_CRM_CONTACT = "edit_crm_contact"
SUPPORT_STAFF_DEFAULT_PERMISSIONS = ((EDIT_CRM_CONTACT, "crm_contact", "edit"),)


async def ensure_support_staff_grants(conn, org_id) -> str | None:
    """Idempotently grant THIS org's support_staff role its defaults. Never creates the role (an org without one has
    nobody to grant), never touches a client role. Returns the role id, or None when the org has no support_staff."""
    role_id = await conn.fetchval("SELECT id FROM roles WHERE org_id = $1 AND name = $2", org_id, SUPPORT_STAFF_ROLE)
    if role_id is None:
        return None
    for name, resource, action in SUPPORT_STAFF_DEFAULT_PERMISSIONS:
        pid = await ensure_permission(conn, name, resource, action)
        await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                           "ON CONFLICT DO NOTHING", role_id, pid)
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
    # BUGFIX4 — ONE role source: Super Admin is the user's ``user_roles`` grant of the org's ``super_admin`` role (the
    # table People & Access writes), never the ``users.role`` string on its own. The string is kept in lockstep by
    # every write path (routers/admin.py) for the SQL that still reads it; a string the grant does not back no
    # longer makes anyone a Super Admin (and a grant the string lacks no longer leaves one out — the "Super Admin
    # required everywhere" Joe hit after promoting a test login in the UI).
    row = await conn.fetchrow(
        f"SELECT u.id, u.org_id, u.role, u.status, u.is_active, {RBAC_SUPER_SQL} AS rbac_super "
        "FROM users u WHERE u.id = $1",
        user_uuid,
    )
    if row is None:
        return None
    admitted = row["status"] == "active" and bool(row["is_active"])
    role = row["role"]
    if row["rbac_super"]:
        role = SUPER_ADMIN_ROLE
    elif role == SUPER_ADMIN_ROLE:
        role = None
    return {
        "id": str(row["id"]),
        "org_id": str(row["org_id"]),
        "role": role if admitted else None,
    }


# ── BUGFIX4 — ONE role source: user_roles decides, users.role mirrors it ─────
async def grant_named_role(conn, user_id, org_id, role_name: str) -> str:
    """Ensure the org has a ``role_name`` role row and grant it to the user (idempotent). Returns the role id."""
    rid = await conn.fetchval("SELECT id FROM roles WHERE org_id = $1::uuid AND name = $2", str(org_id), role_name)
    if rid is None:
        rid = await conn.fetchval(
            "INSERT INTO roles (org_id, name) VALUES ($1::uuid, $2) ON CONFLICT (org_id, name) DO UPDATE SET "
            "name = EXCLUDED.name RETURNING id", str(org_id), role_name)
    await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1::uuid, $2) ON CONFLICT DO NOTHING",
                       str(user_id), rid)
    return str(rid)


#: the account-role string each RBAC role is mirrored to (users.role); any other role mirrors to 'member'
MIRRORED_ROLE_STRINGS = ("super_admin", "org_admin", "support_staff")


def mirrored_role_string(role_names) -> str:
    """The ``users.role`` string that agrees with a user's RBAC roles: the highest of MIRRORED_ROLE_STRINGS held,
    else 'member'. Pure — the write paths call it so the two sources can never drift again."""
    names = set(role_names or ())
    for r in MIRRORED_ROLE_STRINGS:
        if r in names:
            return r
    return "member"


async def sync_role_string(conn, user_id) -> str:
    """Set ``users.role`` from the user's own-org RBAC roles (mirrored_role_string). Returns the string."""
    names = [r["name"] for r in await conn.fetch(
        "SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id JOIN users u ON u.id = ur.user_id "
        "WHERE ur.user_id = $1::uuid AND r.org_id = u.org_id", str(user_id))]
    want = mirrored_role_string(names)
    await conn.execute("UPDATE users SET role = $2, updated_at = now() WHERE id = $1::uuid AND role IS DISTINCT FROM "
                       "$2", str(user_id), want)
    return want


ROLE_DISAGREEMENT_SQL = """
    SELECT u.id, u.email, u.org_id, u.role AS account_role, u.status,
           COALESCE(array_agg(r.name ORDER BY r.name) FILTER (WHERE r.name IS NOT NULL), '{}') AS rbac_roles
      FROM users u
      LEFT JOIN user_roles ur ON ur.user_id = u.id
      LEFT JOIN roles r ON r.id = ur.role_id AND r.org_id = u.org_id
     WHERE NOT u.is_test AND COALESCE(u.status, 'active') <> 'retired'
     GROUP BY u.id
"""


async def role_disagreements(conn) -> list[dict]:
    """READ-ONLY report: real (non-test, not retired) users whose users.role string differs from what their RBAC
    roles mirror to. Changes nothing."""
    out = []
    for r in await conn.fetch(ROLE_DISAGREEMENT_SQL):
        want = mirrored_role_string(r["rbac_roles"])
        if (r["account_role"] or "member") != want:
            out.append({"id": str(r["id"]), "org_id": str(r["org_id"]), "account_role": r["account_role"],
                        "rbac_roles": list(r["rbac_roles"]), "mirrors_to": want, "status": r["status"]})
    return out

