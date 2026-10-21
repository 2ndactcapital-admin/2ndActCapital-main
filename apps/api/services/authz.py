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


async def load_principal(conn, user_id, *, claims: dict | None = None,
                         resolved_via: str = "sub") -> Principal | None:
    """Build the Principal for ``user_id`` from the DB (rbac's queries).

    A row that is not admitted gets NO roles, NO permissions, not staff, not
    super — fail closed regardless of what is granted to it.
    """
    row = await conn.fetchrow(
        "SELECT id, org_id, email, role, status, is_active FROM users WHERE id = $1",
        UUID(str(user_id)),
    )
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
        is_super = rbac.is_super_admin(dict(row))
        roles = await rbac.fetch_role_names(conn, row["id"], org_id)
        perms = await rbac.fetch_permission_names(conn, row["id"], org_id)
        if TEST_MODE and claims:
            roles = sorted(set(roles) | set(_claim_list(claims, _TEST_ROLES_CLAIMS)))
            perms |= set(_claim_list(claims, _TEST_PERMISSIONS_CLAIMS))
        if is_super:
            perms |= await rbac.fetch_all_permission_names(conn)

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


def require_super_admin(request: Request) -> Principal:
    p = require_active(request)
    if p.is_super_admin:
        return p
    raise HTTPException(status_code=403, detail="Super Admin required")


def is_staff(request: Request) -> bool:
    """Staff flag from the DB Principal. No Principal → False."""
    p = principal_of(request)
    return bool(p and p.admitted and (p.is_super_admin or p.is_staff))


def has_permission(request: Request, permission: str) -> bool:
    p = principal_of(request)
    return bool(p and p.has(permission))


async def require_entity_access(request: Request, pool, entity_id) -> Principal:
    """Staff / Super Admin: any entity in the org (RLS keeps it in-org).
    Anyone else: only entities the member visibility engine grants them
    (``document_embedding.visible_entity_ids`` — delegate grants + restricted
    access filter). 404 rather than 403 so ids in other members' scope are
    not disclosed."""
    p = require_active(request)
    if p.is_super_admin or p.is_staff:
        return p
    from services.document_embedding import visible_entity_ids

    allowed = await visible_entity_ids(pool, p.org_id, p.user_id, False)
    if str(entity_id) not in allowed:
        raise HTTPException(status_code=404, detail="Entity not found")
    return p
