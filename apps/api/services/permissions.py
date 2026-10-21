"""Back-compat shim over :mod:`services.authz` (AUTHZ1).

This module used to read ``roles`` / ``permissions`` claims off the access
token and **default to allow / staff when no claim was present**. Real Auth0
access tokens never carry those claims, so every check here passed for every
real caller. It now delegates to the one database-backed path in
``services.authz``; nothing in this module grants on an absent claim.

``get_user_id`` returns the request's real ``users.id`` (the Principal's).
The old fallbacks — a namespaced/UUID ``sub`` claim, a uuid5 derived from
``sub``, the ``DEV_USER_ID`` constant — exist only under ``AUTHZ_TEST_MODE``.
In production an unresolvable identity is a 401: the uuid5 phantom id matched
no ``users`` row, so every ``rbac`` gate fed it saw "no roles" and allowed.
"""

from uuid import UUID, uuid5, NAMESPACE_URL

from fastapi import HTTPException, Request

from services import authz
from services.authz import STAFF_ROLE_NAMES as STAFF_ROLES  # noqa: F401  (kept for importers)

USER_ID_CLAIMS = (
    "https://2ndactcapital.com/user_id",
    "https://api.2ndactcapital.com/user_id",
    "user_id",
)

# Test-mode-only fallback identity (AUTHZ_TEST_MODE=1). Never used in production.
DEV_USER_ID = "00000000-0000-0000-0000-0000000000aa"


def is_staff(request: Request) -> bool:
    return authz.is_staff(request)


def has_permission(request: Request, permission: str) -> bool:
    return authz.has_permission(request, permission)


def require_permission(request: Request, permission: str) -> None:
    authz.require_permission(request, permission)


def require_staff(request: Request) -> None:
    authz.require_staff(request)


def get_user_id(request: Request) -> str:
    """The caller's real ``users.id``; 401 when there is none (production)."""
    principal = authz.principal_of(request)
    if principal is not None:
        return principal.user_id
    if not authz.TEST_MODE:
        raise HTTPException(status_code=401, detail=authz.NO_USER_DETAIL)

    claims = getattr(request.state, "user", None) or {}
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
