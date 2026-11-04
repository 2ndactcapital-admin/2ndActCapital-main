"""The one authorization path (AUTHZ1).

Every API request resolves ONE :class:`Principal` from the database — once, in
``main.rls_context_middleware``, cached on ``request.state.principal`` — and
every gate in every router reads it. Nothing here reads a ``roles`` or
``permissions`` claim off the token in production, and nothing grants on an
absent claim, an absent role, or an absent row:

  * no users row / no identity      → 401 (never a dev or phantom id)
  * row not admitted (pending,      → 403 (``require_active``), except the
    retired, rejected, deactivated)   pending-allowed paths in ``main``
  * permission not granted          → 403 "Permission required: <name>"

Super Admin is checked FIRST in every helper, via the one shared
``services.rbac.is_super_admin`` rule (``users.role = 'super_admin'`` on an
admitted row). The queries are ``services.rbac``'s — this module adds no
second resolver.

WHY "STAFF" IS ROLE MEMBERSHIP. ``services.permissions.STAFF_ROLES`` always
defined staff as investment_staff / admin / owner / super_admin; it was read
from a token claim that real tokens never carry, so every caller was staff.
The DB records the same thing in ``user_roles`` → ``roles.name``
(``staff_assignments`` / ``team_members`` record WHICH entities a staff user
covers, not whether they are staff). ``is_staff`` is therefore: Super Admin,
or an admitted user holding a role in :data:`STAFF_ROLE_NAMES` in their org.

TEST MODE. ``AUTHZ_TEST_MODE=1`` in ``os.environ`` BEFORE this module is
imported (verify scripts set it in-process) enables exactly two things the
in-process verify scripts rely on: the UUID-sub stub (a token ``sub`` that is
a ``users.id``) and claim-based test grants (a ``roles`` / ``permissions``
claim is UNIONED onto the DB grants — never substituted for them, and an
absent claim still grants nothing). Production never sets it; ``main`` logs a
warning at startup if it is set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import HTTPException, Request

from services import rbac

# Read ONCE at import: a process either is a test harness or it is not.
TEST_MODE: bool = os.environ.get("AUTHZ_TEST_MODE") == "1"

STAFF_ROLE_NAMES = frozenset({"investment_staff", "admin", "super_admin", "owner"})

# STAFFROLES1 — TWO separate ideas that used to share the word "staff":
#
#   * WHO SKIPS PERMISSION CHECKS — ``STAFF_ROLE_NAMES`` / ``Principal.is_staff``
#     (unchanged). ``require_staff``, ``require_staff_or_permission`` and every
#     ``is_staff`` short-circuit on a write or an approval read this. It is
#     NEVER widened by this sprint.
#   * WHO MAY SEE STAFF DATA — ``INTERNAL_ROLE_NAMES`` / ``Principal.is_internal``:
#     every internal role of the firm. ``require_internal`` /
#     ``require_internal_permission`` gate the staff-data READS (CRM, SPV
#     manager, ledger, document links, billing reads) and the member scope
#     (``member_entity_scope`` / ``require_entity_access`` on a read) reads it.
#     Seeing is not acting: an internal role that is not staff still needs the
#     named permission for every write and every approval.
#
# Client roles (``member``, ``next_gen``) are NOT internal, nor is any role not
# named here — an unknown role (a fixture role, a new role nobody classified)
# is treated as NOT internal (fail closed). A new internal role must be added
# here by name.
INTERNAL_ROLE_NAMES = STAFF_ROLE_NAMES | frozenset({
    "org_admin", "support_staff", "advisor", "compliance", "fund_finance",
    "investment_committee", "ir_member_relations", "member_manager",
})
CLIENT_ROLE_NAMES = frozenset({"member", "next_gen"})
assert STAFF_ROLE_NAMES <= INTERNAL_ROLE_NAMES and not (INTERNAL_ROLE_NAMES & CLIENT_ROLE_NAMES)

INTERNAL_ACCESS_DETAIL = "Staff access required"   # the same words a member saw before (firm staff only)

STATUS_ACTIVE = "active"
STATUS_PENDING = "pending"
STATUS_RETIRED = "retired"
STATUS_REJECTED = "rejected"

ACCESS_PENDING_DETAIL = (
    "Access pending — an administrator has been notified."
)
ACCOUNT_RETIRED_DETAIL = (
    "This sign-in is no longer linked to an active account. Contact your administrator."
)
NO_USER_DETAIL = "No resolvable user for this token"

# Claim names honored ONLY under TEST_MODE (unioned onto DB grants).
_TEST_ROLES_CLAIMS = (
    "roles", "https://2ndactcapital.com/roles", "https://api.2ndactcapital.com/roles",
)
_TEST_PERMISSIONS_CLAIMS = (
    "permissions", "https://2ndactcapital.com/permissions",
    "https://api.2ndactcapital.com/permissions",
)


@dataclass(frozen=True)
class Principal:
    user_id: str
    org_id: str
    email: str | None
    account_role: str | None
    status: str
    is_active: bool
    is_super_admin: bool
    is_staff: bool
    roles: tuple[str, ...] = ()
    permissions: frozenset[str] = field(default_factory=frozenset)
    resolved_via: str = "sub"
    # STAFFROLES1 — may SEE staff data (every internal firm role; never a bypass).
    is_internal: bool = False

    @property
    def admitted(self) -> bool:
        return self.status == STATUS_ACTIVE and self.is_active

    def has(self, permission: str) -> bool:
        """Super Admin first, then exact. Not admitted → nothing."""
        if not self.admitted:
            return False
        if self.is_super_admin:
            return True
        return permission in self.permissions


def _claim_list(claims: dict, keys) -> list[str]:
    for key in keys:
        value = claims.get(key)
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
    return []


# PREVIEWPERF FIX 1 — the Principal in ONE statement (was four: the users row,
# rbac.fetch_role_names, rbac.fetch_permission_names, and for a Super Admin
# rbac.fetch_all_permission_names). Same predicates as those helpers: roles and
# permissions only through roles of the user's OWN org (u.org_id, NOT NULL);
# the whole catalog only when users.role is the super-admin string ($2). Each
# round trip costs 40-200 ms from a laptop to the pooler, and this runs on
# every authenticated request. Not a cache: read fresh, every request.
# Callers append "FROM users u ..." (and may prepend a CTE / extra columns).
#
# PERMS1 — ONE SOURCE OF PERMISSIONS. The SOC profile / permission-set axis
# (users.profile_id → profile_permissions; user_permission_sets →
# permission_set_permissions) used to be read only by the workflow gates
# (services.profiles), while every other gate read the role axis through this
# Principal — so the "Org Admin" profile's workflow keys never reached
# /users/me, and the two axes disagreed. Now this SAME statement also returns:
#   * granted_names    — the user's profile + permission-set keys, of the
#                        user's OWN org only, JOINED TO THE CATALOG
#                        (permissions.name): a key that is not a real
#                        permission (the legacy profile key "staff") grants
#                        nothing — it never becomes is_staff (roles only);
#   * member_safe_names — the org's `member` role grants (BUGFIX2's
#                        member-safe keys), so the client-role rule is applied
#                        here, in principal_from_row, not by a second reader.
# Still one statement per request; no new parameter.
PRINCIPAL_COLUMNS = """
    u.id, u.org_id, u.email, u.role, u.status, u.is_active,
    ARRAY(SELECT r.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id
           WHERE ur.user_id = u.id AND r.org_id = u.org_id
           ORDER BY r.name) AS role_names,
    ARRAY(SELECT DISTINCT p.name FROM user_roles ur JOIN roles r ON r.id = ur.role_id
           JOIN role_permissions rp ON rp.role_id = ur.role_id
           JOIN permissions p ON p.id = rp.permission_id
           WHERE ur.user_id = u.id AND r.org_id = u.org_id) AS permission_names,
    ARRAY(SELECT DISTINCT pm.name FROM (
             SELECT pp.permission_key AS k FROM profile_permissions pp
               JOIN profiles pr ON pr.id = pp.profile_id
              WHERE pp.profile_id = u.profile_id AND pr.org_id = u.org_id
             UNION
             SELECT psp.permission_key FROM user_permission_sets ups
               JOIN permission_sets ps ON ps.id = ups.permission_set_id
               JOIN permission_set_permissions psp ON psp.permission_set_id = ups.permission_set_id
              WHERE ups.user_id = u.id AND ps.org_id = u.org_id) g
           JOIN permissions pm ON pm.name = g.k) AS granted_names,
    ARRAY(SELECT DISTINCT p.name FROM roles r
           JOIN role_permissions rp ON rp.role_id = r.id
           JOIN permissions p ON p.id = rp.permission_id
           WHERE r.org_id = u.org_id AND r.name = 'member') AS member_safe_names,
    CASE WHEN u.role = $2 THEN ARRAY(SELECT name FROM permissions) END AS catalog_names"""

PRINCIPAL_BY_ID_SQL = f"SELECT {PRINCIPAL_COLUMNS} FROM users u WHERE u.id = $1"


async def load_principal(conn, user_id, *, claims: dict | None = None,
                         resolved_via: str = "sub") -> Principal | None:
    """Build the Principal for ``user_id`` from the DB — one statement.

    A row that is not admitted gets NO roles, NO permissions, not staff, not
    super — fail closed regardless of what is granted to it.
    """
    row = await conn.fetchrow(PRINCIPAL_BY_ID_SQL, UUID(str(user_id)), rbac.SUPER_ADMIN_ROLE)
    return principal_from_row(row, claims=claims, resolved_via=resolved_via)


def principal_from_row(row, *, claims: dict | None = None, resolved_via: str = "sub") -> Principal | None:
    """The Principal for a row carrying PRINCIPAL_COLUMNS (None for no row)."""
    if row is None:
        return None
    org_id = str(row["org_id"])
    status = row["status"] or STATUS_ACTIVE
    is_active = bool(row["is_active"])
    admitted = status == STATUS_ACTIVE and is_active

    roles: list[str] = []
    perms: set[str] = set()
    is_super = False
    if admitted:
        is_super = rbac.is_super_admin({"role": row["role"]})
        roles = list(row["role_names"] or [])
        perms = set(row["permission_names"] or [])
        # PERMS1 — profile + permission-set keys (catalog keys only). The client
        # rule (BUGFIX2) is decided from ROLE standing alone — a profile key can
        # never lift a user out of it: a user with no Super / staff / internal
        # role and neither org-administration permission BY ROLE keeps only the
        # member-safe keys of what their profile / sets carry.
        granted = set(row["granted_names"] or [])
        client_like = not (is_super or STAFF_ROLE_NAMES.intersection(roles)
                           or INTERNAL_ROLE_NAMES.intersection(roles)
                           or rbac.ORG_ADMIN_PERMISSION in perms or "manage_members" in perms)
        if client_like:
            granted &= set(row["member_safe_names"] or [])
        perms |= granted
        if TEST_MODE and claims:
            roles = sorted(set(roles) | set(_claim_list(claims, _TEST_ROLES_CLAIMS)))
            perms |= set(_claim_list(claims, _TEST_PERMISSIONS_CLAIMS))
        if is_super:
            perms |= set(row["catalog_names"] or [])

    return Principal(
        user_id=str(row["id"]),
        org_id=org_id,
        email=row["email"],
        account_role=row["role"],
        status=status,
        is_active=is_active,
        is_super_admin=is_super,
        is_staff=bool(admitted and (is_super or STAFF_ROLE_NAMES.intersection(roles))),
        roles=tuple(roles),
        permissions=frozenset(perms),
        resolved_via=resolved_via,
        is_internal=bool(admitted and (is_super or INTERNAL_ROLE_NAMES.intersection(roles))),
    )


# ── Request access ──────────────────────────────────────────────────────────

def principal_of(request: Request) -> Principal | None:
    """The cached Principal, or None (never loads — the middleware does)."""
    return getattr(request.state, "principal", None)


async def get_principal(request: Request) -> Principal:
    """FastAPI dependency: the request's Principal, resolved once.

    Normally already cached by the middleware; resolves (and caches) here only
    when a handler runs without it (e.g. a path the middleware skipped).
    """
    p = principal_of(request)
    if p is None:
        from services.users import resolve_request_principal

        p = await resolve_request_principal(request)
    if p is None:
        raise HTTPException(status_code=401, detail=NO_USER_DETAIL)
    return p


def require_authenticated(request: Request) -> Principal:
    p = principal_of(request)
    if p is None:
        raise HTTPException(status_code=401, detail=NO_USER_DETAIL)
    return p


def require_active(request: Request) -> Principal:
    p = require_authenticated(request)
    if not p.is_active:
        from main import ACCOUNT_DEACTIVATED_DETAIL

        raise HTTPException(status_code=403, detail=ACCOUNT_DEACTIVATED_DETAIL)
    if p.status == STATUS_PENDING:
        raise HTTPException(status_code=403, detail=ACCESS_PENDING_DETAIL)
    if p.status != STATUS_ACTIVE:
        raise HTTPException(status_code=403, detail=ACCOUNT_RETIRED_DETAIL)
    return p


def require_permission(request: Request, permission: str) -> Principal:
    """403 naming ``permission`` unless granted. Super Admin first."""
    p = require_active(request)
    if p.is_super_admin or permission in p.permissions:
        return p
    raise HTTPException(status_code=403, detail=f"Permission required: {permission}")


def require_any_permission(request: Request, permissions) -> Principal:
    p = require_active(request)
    if p.is_super_admin or any(perm in p.permissions for perm in permissions):
        return p
    raise HTTPException(
        status_code=403,
        detail="Permission required: " + " or ".join(permissions),
    )


def require_staff(request: Request) -> Principal:
    p = require_active(request)
    if p.is_super_admin or p.is_staff:
        return p
    raise HTTPException(status_code=403, detail="Staff access required")


def require_staff_or_permission(request: Request, permission: str) -> Principal:
    """Staff (Super Admin first) OR a holder of ``permission`` — e.g. a platform
    read that org admins share via manage_org_settings."""
    p = require_active(request)
    if p.is_super_admin or p.is_staff or permission in p.permissions:
        return p
    raise HTTPException(status_code=403, detail=f"Staff access or permission required: {permission}")


def require_internal(request: Request) -> Principal:
    """STAFFROLES1 — a staff-DATA read: Super Admin, or any internal firm role
    (``INTERNAL_ROLE_NAMES``). A client role, an unknown role, or no role → 403.
    Grants nothing beyond the read it gates."""
    p = require_active(request)
    if p.is_super_admin or p.is_internal:
        return p
    raise HTTPException(status_code=403, detail=INTERNAL_ACCESS_DETAIL)


def require_internal_permission(request: Request, *permissions: str) -> Principal:
    """STAFFROLES1 — a staff-data read that ALSO needs a permission for a
    non-bypass role: Super Admin first; staff (``is_staff``, the unchanged
    bypass) as before; any other internal role only with one of
    ``permissions``. A client role is refused even when it holds the permission
    (``view_spv`` / ``view_portfolio`` are member grants)."""
    p = require_active(request)
    if p.is_super_admin or p.is_staff:
        return p
    if not p.is_internal:
        raise HTTPException(status_code=403, detail=INTERNAL_ACCESS_DETAIL)
    if any(perm in p.permissions for perm in permissions):
        return p
    raise HTTPException(status_code=403, detail="Permission required: " + " or ".join(permissions))


def require_super_admin(request: Request) -> Principal:
    p = require_active(request)
    if p.is_super_admin:
        return p
    raise HTTPException(status_code=403, detail="Super Admin required")


def is_staff(request: Request) -> bool:
    """Staff flag from the DB Principal. No Principal → False."""
    p = principal_of(request)
    return bool(p and p.admitted and (p.is_super_admin or p.is_staff))


def is_internal(request: Request) -> bool:
    """STAFFROLES1 — may SEE staff data (internal firm role / Super Admin). No
    Principal → False. Never use it to skip a permission check: that is
    :func:`is_staff`."""
    p = principal_of(request)
    return bool(p and p.admitted and (p.is_super_admin or p.is_internal))


def _sees_unscoped(request: Request, p: Principal) -> bool:
    """STAFFROLES1 — unscoped (all of the org's families, RLS keeps it in-org):
    Super Admin and staff always; any other internal role on a READ only — a
    write keeps the member path, so seeing never widens what a role may change."""
    if p.is_super_admin or p.is_staff:
        return True
    return p.is_internal and request.method in ("GET", "HEAD")


def has_permission(request: Request, permission: str) -> bool:
    p = principal_of(request)
    return bool(p and p.has(permission))


async def require_entity_access(request: Request, pool, entity_id) -> Principal:
    """Staff / Super Admin: any entity in the org (RLS keeps it in-org); any
    other internal role (STAFFROLES1): any entity on a READ, its own family on a
    write. Anyone else: only entities the member visibility engine grants them
    (``document_embedding.visible_entity_ids`` — delegate grants + restricted
    access filter). 404 rather than 403 so ids in other members' scope are
    not disclosed."""
    p = require_active(request)
    if _sees_unscoped(request, p):
        return p
    from services.document_embedding import visible_entity_ids

    allowed = await visible_entity_ids(pool, p.org_id, p.user_id, False)
    if entity_id is None or str(entity_id) not in allowed:
        raise HTTPException(status_code=404, detail="Entity not found")
    return p


# ── BUGFIX2 — member data isolation ─────────────────────────────────────────
# A MEMBER's family = the entities the member visibility engine grants them
# (``document_embedding.visible_entity_ids`` with is_staff=False: the member's
# own delegate grants → resolve_entity_set subtree, then the restricted-access
# filter). It is the same engine the member portfolio path
# (routers.portfolio_positions) already uses — never "all entities of the org".
# Staff / Super Admin are unscoped (RLS keeps them in-org).

async def member_entity_scope(request: Request, pool) -> set | None:
    """None for staff / Super Admin (unscoped); otherwise the member's own
    entity-id set (possibly EMPTY — an empty set means "nothing", never "all").
    Callers filter IN SQL with ``= ANY($n::uuid[])``. STAFFROLES1: any internal
    role reads unscoped too (``_sees_unscoped``)."""
    p = require_active(request)
    if _sees_unscoped(request, p):
        return None
    from services.document_embedding import visible_entity_ids

    return await visible_entity_ids(pool, p.org_id, p.user_id, False)


def scope_ids(scope: set) -> list[UUID]:
    """A member scope as a uuid list for ``= ANY($n::uuid[])``."""
    return [UUID(str(x)) for x in scope]


# ── BUGFIX2 — staff-only profiles / permission sets ─────────────────────────
# The profile / permission-set axis (services.profiles) is read by the workflow
# engine and routers.workflows. A MEMBER held the "CSA / Ops" profile (keys
# `staff`, manage_documents, view_members, view_workflow_runs) — a staff grant
# on a member account. Rule:
#   * a key is MEMBER-SAFE iff the org's `member` role grants it (DB, read
#     fresh; no member role → nothing is member-safe — fail closed);
#   * a profile / set is STAFF-ONLY iff it carries any key that is not
#     member-safe;
#   * a "member user" is an admitted user who is not Super Admin, not staff, not
#     an internal firm role (STAFFROLES1 — support staff's "CSA / Ops" profile is
#     theirs) and holds neither org-administration permission (manage_org_settings,
#     manage_members). Assigning a staff-only profile /
#     set to one is refused, and a member who already holds one is granted only
#     its member-safe keys (services.profiles.get_effective_permissions).
MEMBER_ROLE_NAME = "member"

MEMBER_SAFE_KEYS_SQL = """
    SELECT DISTINCT p.name FROM roles r
    JOIN role_permissions rp ON rp.role_id = r.id
    JOIN permissions p ON p.id = rp.permission_id
    WHERE r.org_id = $1 AND r.name = $2"""


def is_member_principal(p: Principal | None) -> bool:
    """True for an admitted user with no staff / super / org-admin standing.
    No Principal or not admitted → True (treated as a member: fail closed)."""
    if p is None or not p.admitted:
        return True
    return not (p.is_super_admin or p.is_staff or p.is_internal or rbac.ORG_ADMIN_PERMISSION in p.permissions
                or "manage_members" in p.permissions)


async def member_safe_keys(conn, org_id) -> frozenset:
    rows = await conn.fetch(MEMBER_SAFE_KEYS_SQL, UUID(str(org_id)), MEMBER_ROLE_NAME)
    return frozenset(r["name"] for r in rows)


async def staff_only_keys(conn, org_id, keys) -> list[str]:
    """The keys of ``keys`` a member may not hold (sorted). Empty → member-safe."""
    safe = await member_safe_keys(conn, org_id)
    return sorted({k for k in keys if k} - safe)
