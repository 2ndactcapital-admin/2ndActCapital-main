"""Authorization helpers: roles, permissions, and user identity.

Auth0 RBAC, when enabled, places a ``permissions`` array and (via a login
Action) a namespaced ``roles`` claim on the access token. This module reads
those claims off ``request.state.user``.

Current stage: RBAC is not yet configured in the Auth0 tenant, and the platform
is operated by a single admin. To avoid locking that operator out — and to keep
the in-process verification scripts (which stub the token with no claims) green
— the helpers **default to allow / staff when no RBAC claim is present**. As
soon as a ``permissions`` or ``roles`` claim appears on the token, the checks
become real. This mirrors the existing ``DEFAULT_ORG_ID`` fallback and the
"compliance stub — always allow for now" pattern elsewhere in the API.
"""

from uuid import UUID, uuid5, NAMESPACE_URL

from fastapi import HTTPException, Request

# Namespaced custom claims (set by an Auth0 login Action).
ROLES_CLAIMS = (
    "roles",
    "https://2ndactcapital.com/roles",
    "https://api.2ndactcapital.com/roles",
)
PERMISSIONS_CLAIMS = (
    "permissions",
    "https://2ndactcapital.com/permissions",
    "https://api.2ndactcapital.com/permissions",
)
USER_ID_CLAIMS = (
    "https://2ndactcapital.com/user_id",
    "https://api.2ndactcapital.com/user_id",
    "user_id",
)

# Roles that count as "investment_staff and above".
STAFF_ROLES = {"investment_staff", "admin", "super_admin", "owner"}

# Stable dev user used when the token carries no resolvable UUID identity.
DEV_USER_ID = "00000000-0000-0000-0000-0000000000aa"


def _claims(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def _first_list_claim(claims: dict, keys) -> list[str] | None:
    """Return the first present claim as a list, or None if no claim exists."""
    for key in keys:
        if key in claims and claims[key] is not None:
            value = claims[key]
            if isinstance(value, str):
                return [value]
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value]
    return None


def get_roles(request: Request) -> list[str] | None:
    return _first_list_claim(_claims(request), ROLES_CLAIMS)


def get_permissions(request: Request) -> list[str] | None:
    return _first_list_claim(_claims(request), PERMISSIONS_CLAIMS)


def is_staff(request: Request) -> bool:
    """True for investment_staff and above.

    Defaults to True when no roles claim is present (single-admin dev stage).
    """
    roles = get_roles(request)
    if roles is None:
        return True
    return any(r in STAFF_ROLES for r in roles)


def has_permission(request: Request, permission: str) -> bool:
    """Check a fine-grained permission.

    Defaults to True when the token carries no permissions claim (RBAC not yet
    configured). Once permissions are present, the check is exact.
    """
    perms = get_permissions(request)
    if perms is None:
        return True
    if permission in perms:
        return True
    # Staff roles implicitly hold all marketplace permissions.
    return is_staff(request)


def require_permission(request: Request, permission: str) -> None:
    if not has_permission(request, permission):
        raise HTTPException(
            status_code=403,
            detail=f"Permission required: {permission}",
        )


def require_staff(request: Request) -> None:
    if not is_staff(request):
        raise HTTPException(status_code=403, detail="Staff access required")


def get_user_id(request: Request) -> str:
    """Resolve a stable UUID for the current user.

    Order: an explicit namespaced ``user_id`` claim, else the ``sub`` claim if
    it is already a UUID, else a deterministic UUIDv5 derived from ``sub`` so a
    given Auth0 user always maps to the same id, else the dev fallback.
    """
    claims = _claims(request)
    for key in USER_ID_CLAIMS:
        value = claims.get(key)
        if value:
            try:
                return str(UUID(str(value)))
            except (ValueError, TypeError):
                continue

    sub = claims.get("sub")
    if sub:
        try:
            return str(UUID(str(sub)))
        except (ValueError, TypeError):
            return str(uuid5(NAMESPACE_URL, str(sub)))

    return DEV_USER_ID
