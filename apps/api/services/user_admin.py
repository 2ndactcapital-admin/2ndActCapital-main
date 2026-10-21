"""People & Access helpers (USERMGMT1).

Pure derivations the user list and the admin actions share, plus the ONE
outbound call the password-reset action makes.

HOW A PERSON SIGNS IN, AS THE DATABASE KNOWS IT
-----------------------------------------------
``users.auth0_sub`` is the primary login; ``user_identities`` holds extra
logins linked to the same person (AUTHZ1). Auth0 prefixes every sub with the
connection strategy that issued it — ``auth0|…`` is a database (email &
password) connection, ``google-oauth2|…`` is Google, and so on — so the method
is read off the prefix. The tenant ("2nd Act" vs "Platform") is the identity
row's stored ``issuer``; the primary sub has no stored issuer, but each tenant
provisions into exactly one org (``routers.entities.get_org_id``), so the
user's org says which tenant issued it.

PASSWORD RESET — AND ITS LIMITS
-------------------------------
There is no Auth0 Management API grant on either tenant. What exists for free
is the PUBLIC endpoint ``POST https://<tenant>/dbconnections/change_password``
``{client_id, email, connection}``: Auth0 emails a reset link to that address
IF a user with it exists on that database connection. It needs no token, and
it always answers 200 with the same sentence whether or not anyone matched —
so a success here means "Auth0 accepted the request", never "a mail was sent".
It cannot reset a Google login (that password belongs to Google), and it
cannot reach an account whose Auth0 login email differs from ``users.email``.
"""

from __future__ import annotations

import os
import re

import httpx

from routers.entities import HOLLISWORKS_ORG_ID
from services.users import PLACEHOLDER_EMAIL_SUFFIX

ANONYMIZED_EMAIL_SUFFIX = "@deleted.invalid"
TEST_EMAIL_SUFFIX = "@test.local"

TENANT_2NDACT = "2nd Act"
TENANT_PLATFORM = "Platform"

# Auth0 connection strategy (the sub prefix) → how the person signs in.
_METHODS = {
    "auth0": "Email & password",
    "google-oauth2": "Google",
    "windowslive": "Microsoft",
    "apple": "Apple",
    "linkedin": "LinkedIn",
    "github": "GitHub",
    "email": "Email code",
    "sms": "Text message code",
    "samlp": "Single sign-on",
    "waad": "Microsoft (work)",
    "oidc": "Single sign-on",
}
DATABASE_PREFIX = "auth0"

# Doppler names (prd + dev). The client id is the tenant's own web
# application; the connection defaults to Auth0's standard database connection
# name when the per-tenant override is unset — no Doppler secret names one today.
CLIENT_ID_ENV = {TENANT_2NDACT: "AUTH0_CLIENT_ID", TENANT_PLATFORM: "HOLLISWORKS_AUTH0_CLIENT_ID"}
CONNECTION_ENV = {
    TENANT_2NDACT: "AUTH0_DB_CONNECTION",
    TENANT_PLATFORM: "HOLLISWORKS_AUTH0_DB_CONNECTION",
}
DEFAULT_DB_CONNECTION = "Username-Password-Authentication"

# The refusal sentences the screen shows verbatim.
REFUSE_NO_EMAIL = "No real email on file. Add the person's email address first."
REFUSE_GOOGLE = "This account signs in with Google — reset the password in Google."
REFUSE_NEVER_SIGNED_IN = (
    "This person has never signed in, so there is no password to reset. "
    "Resend their invitation instead."
)
REFUSE_RETIRED = "This account is retired."

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def sub_prefix(sub: str | None) -> str | None:
    if not sub or "|" not in sub:
        return None
    return sub.split("|", 1)[0]


def method_for_sub(sub: str | None) -> str:
    prefix = sub_prefix(sub)
    if prefix is None:
        return "Unknown"
    return _METHODS.get(prefix, f"Other ({prefix})")


def is_placeholder_email(email: str | None) -> bool:
    e = (email or "").strip().lower()
    return not e or e.endswith(PLACEHOLDER_EMAIL_SUFFIX) or e.endswith(ANONYMIZED_EMAIL_SUFFIX)


def is_test_email(email: str | None) -> bool:
    return (email or "").strip().lower().endswith(TEST_EMAIL_SUFFIX)


def valid_email(email: str | None) -> bool:
    e = (email or "").strip()
    return bool(_EMAIL_RE.match(e)) and not is_placeholder_email(e) and len(e) <= 320


def _settings():
    from main import get_settings

    return get_settings()


def tenant_for_org(org_id) -> str:
    return TENANT_PLATFORM if str(org_id) == HOLLISWORKS_ORG_ID else TENANT_2NDACT


def tenant_for_issuer(issuer: str | None, org_id) -> str:
    """A stored identity issuer → tenant label; falls back to the org's tenant."""
    iss = (issuer or "").rstrip("/")
    if iss:
        try:
            s = _settings()
            if s.hollisworks_enabled and iss == s.hollisworks_issuer.rstrip("/"):
                return TENANT_PLATFORM
            if iss == s.issuer.rstrip("/"):
                return TENANT_2NDACT
        except Exception:  # noqa: BLE001 — a label never fails a list
            pass
        if "dev-gy85vzuf6mruzv3j" in iss:
            return TENANT_PLATFORM
        if "dev-smmrfubsfscif3t1" in iss:
            return TENANT_2NDACT
    return tenant_for_org(org_id)


def issuer_for_tenant(tenant: str) -> str:
    s = _settings()
    return s.hollisworks_issuer if tenant == TENANT_PLATFORM else s.issuer


def domain_for_tenant(tenant: str) -> str:
    s = _settings()
    return s.hollisworks_auth0_domain if tenant == TENANT_PLATFORM else s.auth0_domain


def sign_in_methods(*, org_id, auth0_sub: str | None, identities: list[dict]) -> list[dict]:
    """Every login that resolves to this user, primary first."""
    out = []
    if auth0_sub:
        out.append({
            "tenant": tenant_for_org(org_id),
            "method": method_for_sub(auth0_sub),
            "is_database": sub_prefix(auth0_sub) == DATABASE_PREFIX,
            "linked_via": "primary",
        })
    for ident in identities or []:
        sub = ident.get("auth0_sub")
        out.append({
            "tenant": tenant_for_issuer(ident.get("issuer"), org_id),
            "method": method_for_sub(sub),
            "is_database": sub_prefix(sub) == DATABASE_PREFIX,
            "linked_via": ident.get("linked_via"),
        })
    return out


def reset_plan(*, email: str | None, status: str | None, is_anonymized: bool,
               sign_in: list[dict]) -> tuple[dict | None, str | None]:
    """``(login, None)`` naming the database login a reset targets, or
    ``(None, refusal sentence)``. Order: retired, no email, no login, Google."""
    if is_anonymized or status in ("retired", "rejected"):
        return None, REFUSE_RETIRED
    if is_placeholder_email(email):
        return None, REFUSE_NO_EMAIL
    if not sign_in:
        return None, REFUSE_NEVER_SIGNED_IN
    database = [s for s in sign_in if s["is_database"]]
    if not database:
        if any(s["method"] == "Google" for s in sign_in):
            return None, REFUSE_GOOGLE
        method = sign_in[0]["method"]
        return None, f"This account signs in with {method}, which has no password here to reset."
    return database[0], None


class ResetNotConfigured(RuntimeError):
    """The tenant's client id is not available to the API."""


def reset_request(tenant: str, email: str) -> tuple[str, dict]:
    """``(url, json body)`` for the tenant's change_password call. Raises
    :class:`ResetNotConfigured` naming the missing variable (never a value)."""
    client_env = CLIENT_ID_ENV[tenant]
    client_id = (os.environ.get(client_env) or "").strip()
    if not client_id:
        raise ResetNotConfigured(
            f"Password reset is not configured for the {tenant} sign-in: "
            f"{client_env} is not set on the API service."
        )
    domain = (domain_for_tenant(tenant) or "").strip()
    if not domain:
        raise ResetNotConfigured(
            f"Password reset is not configured for the {tenant} sign-in: its Auth0 domain is not set."
        )
    connection = (os.environ.get(CONNECTION_ENV[tenant]) or "").strip() or DEFAULT_DB_CONNECTION
    return (
        f"https://{domain}/dbconnections/change_password",
        {"client_id": client_id, "email": email, "connection": connection},
    )


async def post_change_password(url: str, body: dict) -> int:
    """The ONE outbound Auth0 call. Returns the HTTP status. Verify mocks this."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=body)
    return response.status_code


def likely_match(pending: dict, candidates: list[dict]) -> dict | None:
    """For a pending row: an admitted user with the same email, or whose email
    equals the pending row's name or typed contact email (the /userinfo email of
    an unverified login lands in ``full_name`` when the address was taken)."""
    keys = set()
    for value in (pending.get("email"), pending.get("full_name"), pending.get("contact_email")):
        v = (value or "").strip().lower()
        if v and "@" in v and not is_placeholder_email(v):
            keys.add(v)
    if not keys:
        return None
    for c in candidates:
        if str(c["id"]) != str(pending["id"]) and (c.get("email") or "").strip().lower() in keys:
            return {"id": str(c["id"]), "email": c["email"], "full_name": c.get("full_name")}
    return None
