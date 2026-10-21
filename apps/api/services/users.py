"""User identity resolution and provisioning (AUTHZ1).

Resolves the authenticated caller to a row in the ``users`` table. Any write
that references ``users(id)`` as a foreign key needs a real users row, and
every authorization decision (``services.authz``) is made against that row.

Identity model: ``users.auth0_sub`` is a user's PRIMARY Auth0 identity; extra
logins (a second connection, the other tenant's sub in the same org) live in
``user_identities`` and resolve to the same user. The primary key is a
DB-generated ``extensions.uuid_generate_v4()``. The schema qualification is
REQUIRED: the uuid-ossp functions live only in the ``extensions`` schema, and
the application role's ``search_path`` is ``"$user", public`` — a bare
``uuid_generate_v4()`` in statement text resolves at parse time against that
search_path and raises ``function uuid_generate_v4() does not exist``. Column
DEFAULTs are exempt because they are name-resolved once at DDL time.

PROVISIONING RULES (AUTHZ1 — no login becomes a user with access by itself):

  Known sub
    1. ``user_identities.auth0_sub`` → that user (a linked second login).
    2. ``users.auth0_sub`` → that user. If the row still carries the
       ``<sub>@placeholder.local`` address, ask ``/userinfo``: a VERIFIED email
       that matches exactly one other admitted user in the same org merges the
       duplicate — the sub is linked to that user and this row is RETIRED
       (never deleted; its references are kept). Otherwise the real email is
       back-filled when no other row already holds it.
  AUTHZ_TEST_MODE only
    3. ``sub`` is a UUID equal to a ``users.id`` → that row (verify stubs).
  Unknown sub
    4. A verified ``/userinfo`` email matching exactly one admitted user in the
       org the issuing tenant maps to → LINK (``user_identities``).
    5. A Hollisworks-issued login whose verified email is in
       ``PLATFORM_STAFF_EMAILS`` → created ``super_admin``, active.
    6. Anything else → created ``status='pending'`` with no roles; refused
       everywhere but /users/me until an org admin approves it.

  Super admin is NEVER granted by issuer alone, and existing rows are never
  promoted or demoted by this module. ``touch_last_login`` runs on every
  resolution path.
"""

from __future__ import annotations

import os
from uuid import UUID

import httpx
from fastapi import HTTPException, Request

from routers.entities import HOLLISWORKS_ORG_ID, get_org_id

PLACEHOLDER_EMAIL_SUFFIX = "@placeholder.local"

# Doppler secret (prd + dev). Comma-separated, case-insensitive. Seed value —
# the operator sets it; this code never falls back to it:
#   PLATFORM_STAFF_EMAILS=jlarizza@gmail.com,jlarizza@culmina.io
PLATFORM_STAFF_EMAILS_ENV = "PLATFORM_STAFF_EMAILS"

# How stale ``users.last_login_at`` may get before the next authenticated
# request refreshes it. Resolution runs on EVERY request, so an unconditional
# UPDATE would be a row write per API call. Postgres interval literal; the
# comparison uses the DATABASE clock.
LOGIN_TOUCH_INTERVAL = "5 minutes"

# Subs whose placeholder back-fill / duplicate-merge lookup has already been
# attempted in this process — an Auth0 outage must not make every request
# retry /userinfo. Unknown subs are NOT cached: provisioning needs the answer.
_userinfo_attempted: set[str] = set()


def placeholder_email(sub: str) -> str:
    """The synthetic address used when no real one is known (or it is taken)."""
    return f"{sub}{PLACEHOLDER_EMAIL_SUFFIX}"


def platform_staff_emails() -> set[str]:
    raw = os.environ.get(PLATFORM_STAFF_EMAILS_ENV) or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _claims(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def _as_uuid(value) -> str | None:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        return None


def _bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" and token else None


def _is_hollisworks(claims: dict) -> bool:
    try:
        from main import is_hollisworks_claims

        return is_hollisworks_claims(claims)
    except Exception:
        return False


async def fetch_auth0_profile(request: Request, claims: dict) -> dict | None:
    """The issuing tenant's ``/userinfo`` profile, or None.

    An access token minted for a custom API audience carries only
    ``sub``/``iss``/``aud``/``azp``/``scope``; ``email``/``email_verified``/
    ``name`` live in the ID token, which the API never sees. ``/userinfo``
    returns the profile for exactly the sub the presented token was issued to.
    The URL comes from the VALIDATED ``iss``, and only if it is one of the
    issuers this API accepts — a claim never chooses an outbound host alone.

    Best-effort: any failure returns None. This is the single function the
    verify script mocks.
    """
    token = _bearer_token(request)
    issuer = claims.get("iss")
    if not token or not issuer:
        return None

    from main import get_settings

    settings = get_settings()
    allowed = {settings.issuer}
    if settings.hollisworks_enabled:
        allowed.add(settings.hollisworks_issuer)
    if issuer not in allowed:
        return None

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{issuer.rstrip('/')}/userinfo",
                headers={"Authorization": f"Bearer {token}"},
            )
        response.raise_for_status()
        profile = response.json()
    except Exception as exc:
        print(f"[users] /userinfo lookup failed for sub={claims.get('sub')!r}: {exc}")
        return None
    return profile if isinstance(profile, dict) else None


async def fetch_auth0_identity(request: Request, claims: dict) -> tuple[str | None, str | None]:
    """``(email, full_name)`` from ``/userinfo`` — kept for routers/enroll."""
    profile = await fetch_auth0_profile(request, claims) or {}
    email = (profile.get("email") or "").strip() or None
    full_name = profile.get("name") or profile.get("nickname") or email or None
    return email, full_name


def _verified_email(profile: dict | None) -> str | None:
    if not profile or profile.get("email_verified") is not True:
        return None
    email = (profile.get("email") or "").strip()
    return email or None


async def touch_last_login(conn, user_id) -> bool:
    """Stamp ``users.last_login_at`` (throttled). Returns True if it wrote.

    Called on EVERY resolution path — by primary sub, linked identity, merge,
    link, creation and the test-mode UUID stub — so "Last sign-in" is true for
    every way a person can arrive (the culmina.io row read "Never" because its
    path skipped the stamp). Never raises.
    """
    try:
        status = await conn.execute(
            f"""
            UPDATE users
            SET last_login_at = now()
            WHERE id = $1
              AND (last_login_at IS NULL
                   OR last_login_at < now() - interval '{LOGIN_TOUCH_INTERVAL}')
            """,
            user_id,
        )
        return status.rsplit(" ", 1)[-1] != "0"
    except Exception as exc:  # noqa: BLE001
        print(f"[users] last_login_at touch failed for {user_id!r}: {exc}")
        return False


async def _admitted_match(conn, org_id, email: str, *, exclude_id=None) -> list:
    """Admitted users in ``org_id`` with this email (case-insensitive).

    An invited-but-unredeemed row (``invite_status='pending'``) is never a link
    target: it must be claimed with its invite token, not by email alone.
    """
    return await conn.fetch(
        """
        SELECT id FROM users
        WHERE org_id = $1 AND lower(email) = lower($2)
          AND status = 'active' AND is_active
          AND COALESCE(invite_status, '') <> 'pending'
          AND ($3::uuid IS NULL OR id <> $3::uuid)
        """,
        org_id, email, exclude_id,
    )


async def _email_taken(conn, email: str, *, exclude_id=None) -> bool:
    """``users.email`` is UNIQUE platform-wide — across orgs, too."""
    return bool(await conn.fetchval(
        "SELECT 1 FROM users WHERE lower(email) = lower($1) "
        "AND ($2::uuid IS NULL OR id <> $2::uuid) LIMIT 1",
        email, exclude_id,
    ))


async def _link_identity(conn, *, user_id, org_id, sub, issuer, email, via) -> None:
    await conn.execute(
        """
        INSERT INTO user_identities (user_id, org_id, auth0_sub, issuer, email, linked_via)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        user_id, org_id, sub, issuer or "", email, via,
    )


async def _notify_pending(conn, *, org_id, user_id, email) -> None:
    try:
        from services.workflow_todos import create_pending_user_alerts

        await create_pending_user_alerts(conn, org_id=org_id, pending_user_id=user_id, email=email)
    except Exception as exc:  # noqa: BLE001 — a failed alert never fails a login
        print(f"[users] pending-user alert failed for {user_id!r}: {exc}")


async def resolve_identity(conn, request: Request) -> tuple[str | None, str]:
    """``(users.id, path)`` for the caller, provisioning per the module rules.

    ``path`` is one of identity / sub / merged / test_uuid / linked /
    created_super_admin / created_pending / none. Returns ``(None, 'none')``
    when there is no sub or the resolution failed — the caller turns that
    into a 401. Never returns a phantom or dev id.
    """
    from services.authz import STATUS_PENDING, STATUS_RETIRED, TEST_MODE

    claims = _claims(request)
    sub = claims.get("sub")
    if not sub:
        return None, "none"
    org_id = get_org_id(request)
    issuer = claims.get("iss") or ""

    # 1. A linked second login.
    linked = await conn.fetchrow(
        "SELECT user_id FROM user_identities WHERE auth0_sub = $1", sub
    )
    if linked:
        await touch_last_login(conn, linked["user_id"])
        return str(linked["user_id"]), "identity"

    # 2. The primary sub.
    by_sub = await conn.fetchrow(
        "SELECT id, email, org_id, status FROM users WHERE auth0_sub = $1", sub
    )
    if by_sub:
        await touch_last_login(conn, by_sub["id"])
        if (
            by_sub["email"] == placeholder_email(sub)
            and by_sub["status"] != STATUS_RETIRED
            and sub not in _userinfo_attempted
        ):
            _userinfo_attempted.add(sub)
            profile = await fetch_auth0_profile(request, claims)
            email = _verified_email(profile)
            if email and str(by_sub["org_id"]) == str(org_id):
                matches = await _admitted_match(conn, org_id, email, exclude_id=by_sub["id"])
                if len(matches) == 1:
                    target = matches[0]["id"]
                    async with conn.transaction():
                        await _link_identity(conn, user_id=target, org_id=org_id, sub=sub,
                                             issuer=issuer, email=email, via="duplicate_merge")
                        await conn.execute(
                            "UPDATE users SET status = $2, updated_at = now() WHERE id = $1",
                            by_sub["id"], STATUS_RETIRED,
                        )
                    await touch_last_login(conn, target)
                    return str(target), "merged"
            real = email or ((profile or {}).get("email") or "").strip() or None
            if real and not await _email_taken(conn, real, exclude_id=by_sub["id"]):
                name = (profile or {}).get("name") or (profile or {}).get("nickname")
                await conn.execute(
                    "UPDATE users SET email = $2, full_name = COALESCE($3, full_name), "
                    "updated_at = now() WHERE id = $1",
                    by_sub["id"], real, name,
                )
        return str(by_sub["id"]), "sub"

    # 3. Verify-script stub: sub = a seeded users.id. Test mode only.
    if TEST_MODE:
        maybe_uuid = _as_uuid(sub)
        if maybe_uuid:
            by_id = await conn.fetchrow("SELECT id FROM users WHERE id = $1", UUID(maybe_uuid))
            if by_id:
                await touch_last_login(conn, by_id["id"])
                return str(by_id["id"]), "test_uuid"

    # 4–6. Unknown login.
    profile = await fetch_auth0_profile(request, claims)
    verified = _verified_email(profile)
    any_email = verified or ((profile or {}).get("email") or "").strip() or None
    full_name = (
        (profile or {}).get("name") or (profile or {}).get("nickname") or any_email or "Member"
    )

    if verified:
        matches = await _admitted_match(conn, org_id, verified)
        if len(matches) == 1:
            target = matches[0]["id"]
            await _link_identity(conn, user_id=target, org_id=org_id, sub=sub,
                                 issuer=issuer, email=verified, via="verified_email")
            await touch_last_login(conn, target)
            return str(target), "linked"

    staff = bool(
        verified
        and _is_hollisworks(claims)
        and str(org_id) == HOLLISWORKS_ORG_ID
        and verified.lower() in platform_staff_emails()
    )
    role = "super_admin" if staff else "member"
    status = "active" if staff else STATUS_PENDING
    email = any_email if any_email and not await _email_taken(conn, any_email) else placeholder_email(sub)

    inserted = await conn.fetchrow(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, last_login_at)
        VALUES (extensions.uuid_generate_v4(), $1, $2, $3, $4, $5, $6, now())
        ON CONFLICT (auth0_sub) DO UPDATE SET last_login_at = now()
        RETURNING id, status
        """,
        org_id, email, full_name, sub, role, status,
    )
    if inserted is None:
        return None, "none"
    if inserted["status"] == STATUS_PENDING:
        await _notify_pending(conn, org_id=org_id, user_id=inserted["id"], email=any_email or email)
    return str(inserted["id"]), ("created_super_admin" if staff else "created_pending")


async def resolve_request_principal(request: Request):
    """Resolve (and provision) the caller, load the Principal, cache it.

    Runs under the RLS context the caller has set (the middleware sets the
    token's org + the bootstrap sub before calling this). Returns None when
    the token resolves to no user.
    """
    from services.authz import load_principal
    from services.database import get_pool

    cached = getattr(request.state, "principal", None)
    if cached is not None:
        return cached
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            user_id, via = await resolve_identity(conn, request)
        except Exception as exc:  # noqa: BLE001
            import traceback

            print(f"ERROR resolving identity (sub={_claims(request).get('sub')!r}): {exc}")
            print(traceback.format_exc())
            return None
        if user_id is None:
            return None
        principal = await load_principal(conn, user_id, claims=_claims(request), resolved_via=via)
    request.state.principal = principal
    return principal


async def ensure_user(conn, request: Request) -> str:
    """Return the caller's ``users.id``.

    The middleware has already resolved the Principal for every authenticated
    request, so this is normally a cache read. Without one it resolves on
    ``conn``. An unresolvable caller is a 401 — except under AUTHZ_TEST_MODE,
    where the old token-derived fallback id is kept for verify scripts.
    """
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        return principal.user_id
    try:
        user_id, _ = await resolve_identity(conn, request)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR in ensure_user (sub={_claims(request).get('sub')!r}): {exc}")
        user_id = None
    if user_id:
        return user_id
    from services.authz import NO_USER_DETAIL, TEST_MODE

    if TEST_MODE:
        from services.permissions import get_user_id

        return get_user_id(request)
    raise HTTPException(status_code=401, detail=NO_USER_DETAIL)
