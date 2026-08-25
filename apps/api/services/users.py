"""User identity sync.

Resolves the authenticated caller to a row in the ``users`` table, creating one
on first sight. Any write that references ``users(id)`` as a foreign key —
deal_votes, deal_interest, compliance_override_requests, member_investments,
notification_recipients — needs a real users row.

Identity model: the ``users`` table is keyed on ``auth0_sub`` (the raw JWT
``sub`` string) and its primary key is a DB-generated
``extensions.uuid_generate_v4()``. The schema qualification is REQUIRED: the
uuid-ossp functions live only in the ``extensions`` schema, and the application
role's ``search_path`` is ``"$user", public`` — a bare ``uuid_generate_v4()`` in
statement text resolves at parse time against that search_path and raises
``function uuid_generate_v4() does not exist``. Column DEFAULTs are exempt
because they are name-resolved once at DDL time and stored as a parse tree
holding the function's OID, not as text.
The token ``sub`` is NOT a UUID and the v5-derived id from ``get_user_id`` is
only a last-resort fallback — it never matches a v4 PK, which is why FK inserts
were failing. ``ensure_user`` therefore resolves strictly by ``auth0_sub`` and
returns the **DB-generated** id, which is the value callers must use for FKs.
"""

from uuid import UUID

import httpx
from fastapi import Request

from services.permissions import get_user_id
from routers.entities import get_org_id

PLACEHOLDER_EMAIL_SUFFIX = "@placeholder.local"

# Subs whose /userinfo lookup has already been attempted in this process. An
# Auth0 outage (or a token without the openid scope) must not make every
# subsequent request retry the call — one attempt per sub per process is enough
# to back-fill, and a restart retries.
_userinfo_attempted: set[str] = set()


def placeholder_email(sub: str) -> str:
    """The synthetic address ``ensure_user`` falls back to when no real one is known."""
    return f"{sub}{PLACEHOLDER_EMAIL_SUFFIX}"


def _claims(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def _as_uuid(value) -> str | None:
    """Return the value as a canonical UUID string, or None if it isn't one."""
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        return None


def _bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" and token else None


async def fetch_auth0_identity(request: Request, claims: dict) -> tuple[str | None, str | None]:
    """Return ``(email, full_name)`` from the issuing tenant's ``/userinfo``.

    WHY THIS EXISTS. ``ensure_user`` reads ``claims.get("email")`` off the
    **access token**, but an Auth0 access token minted for a custom API audience
    carries only ``sub``/``iss``/``aud``/``azp``/``scope`` — ``email`` and
    ``name`` live in the **ID token**, which the API never sees. So the email
    claim was ALWAYS absent and every row a real login created got the
    ``{sub}@placeholder.local`` fallback. The live database proves it: the one
    row created by a real Auth0 login holds
    ``auth0|6a3af4c9a1c6aeb8baddf3eb@placeholder.local``. A row does exist, but
    it is unfindable by the person's actual address and renders as noise in
    /admin/users.

    ``/userinfo`` is the trustworthy fix — it returns the verified profile for
    exactly the sub the presented access token was issued to, needs no Auth0
    dashboard change (the ``openid profile email`` scope is already requested),
    and cannot be spoofed by the caller the way a client-supplied header could.

    The URL is derived from the token's **validated** ``iss`` claim, and only
    after checking it against the issuers this API is configured to accept — a
    claim is never used to choose an outbound host on its own.

    Best-effort by contract: any failure returns ``(None, None)`` and the caller
    keeps the placeholder. Identity resolution must never break a read path.
    """
    token = _bearer_token(request)
    issuer = claims.get("iss")
    if not token or not issuer:
        return None, None

    from main import get_settings

    settings = get_settings()
    allowed = {settings.issuer}
    if settings.hollisworks_enabled:
        allowed.add(settings.hollisworks_issuer)
    if issuer not in allowed:
        return None, None

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
        return None, None

    email = (profile.get("email") or "").strip() or None
    full_name = (
        profile.get("name") or profile.get("nickname") or email or None
    )
    return email, full_name


async def ensure_user(conn, request: Request) -> str:
    """Return the caller's ``users.id``, inserting the row if it does not exist.

    Resolution order:
      1. By ``auth0_sub`` — the canonical key (raw JWT ``sub`` string).
      2. If ``sub`` is itself a UUID that matches an existing row id (the verify
         scripts stub ``sub`` = a seeded user's UUID), use that row.
      3. Insert a new row, letting Postgres generate the id
         (``extensions.uuid_generate_v4()`` — schema-qualified; see the module
         docstring for why a bare call breaks under the app role's
         search_path); return the generated id.

    Never raises — on unexpected error it falls back to the token-derived id so
    read paths are unaffected (the traceback below is the signal in the logs).
    """
    claims = _claims(request)
    sub = claims.get("sub")
    org_id = get_org_id(request)

    if not sub:
        return get_user_id(request)

    # Hollisworks-tenant identity IS platform staff → role 'super_admin'. Detected
    # from the validated token issuer (lazy import avoids a circular import with
    # main). Non-Hollisworks callers are unaffected and stay 'member'.
    try:
        from main import is_hollisworks_claims

        is_staff = is_hollisworks_claims(claims)
    except Exception:
        is_staff = False
    role = "super_admin" if is_staff else "member"

    try:
        # 1. Canonical lookup by auth0_sub.
        by_sub = await conn.fetchrow(
            "SELECT id, role, email FROM users WHERE auth0_sub = $1", sub
        )
        if by_sub:
            # Promote an existing Hollisworks staff row if it predates this
            # mapping. Never demotes and never touches non-staff rows.
            if is_staff and by_sub["role"] != "super_admin":
                await conn.execute(
                    "UPDATE users SET role = 'super_admin' WHERE id = $1",
                    by_sub["id"],
                )
            # Back-fill a row still carrying the synthetic address. Rows created
            # before this fix ALL hold `{sub}@placeholder.local`, because the
            # access token never carried an email claim (see
            # `fetch_auth0_identity`). One attempt per sub per process.
            if by_sub["email"] == placeholder_email(sub) and sub not in _userinfo_attempted:
                _userinfo_attempted.add(sub)
                real_email, real_name = await fetch_auth0_identity(request, claims)
                if real_email:
                    await conn.execute(
                        """
                        UPDATE users
                        SET email = $2,
                            full_name = COALESCE($3, full_name),
                            updated_at = now()
                        WHERE id = $1
                        """,
                        by_sub["id"], real_email, real_name,
                    )
            return str(by_sub["id"])

        # 2. Verify scripts stub sub = a seeded user's UUID id.
        maybe_uuid = _as_uuid(sub)
        if maybe_uuid:
            by_id = await conn.fetchrow(
                "SELECT id FROM users WHERE id = $1", maybe_uuid
            )
            if by_id:
                return str(by_id["id"])

        # 3. Create the user; the DB generates the v4 id.
        # An Auth0 access token minted for a custom API audience carries NO
        # email/name claims — they live in the ID token, which this API never
        # sees — so these lookups essentially always miss. Ask the issuing
        # tenant's /userinfo for the real profile before falling back.
        email = claims.get("email")
        full_name = claims.get("name") or claims.get("nickname")
        if not email and sub not in _userinfo_attempted:
            _userinfo_attempted.add(sub)
            email, resolved_name = await fetch_auth0_identity(request, claims)
            full_name = full_name or resolved_name

        # users.email is NOT NULL, so a deterministic placeholder keeps the
        # insert legal when /userinfo is unavailable. The back-fill on the
        # by_sub path above repairs such a row on a later request.
        email = email or placeholder_email(sub)
        full_name = full_name or claims.get("email") or "Member"
        inserted = await conn.fetchrow(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES (extensions.uuid_generate_v4(), $1, $2, $3, $4, $5)
            ON CONFLICT (auth0_sub) DO UPDATE
                SET email = COALESCE(
                    NULLIF(EXCLUDED.email, EXCLUDED.auth0_sub || '@placeholder.local'),
                    users.email
                ),
                role = CASE
                    WHEN EXCLUDED.role = 'super_admin' THEN 'super_admin'
                    ELSE users.role
                END
            RETURNING id
            """,
            org_id, email, full_name, sub, role,
        )
        if inserted:
            return str(inserted["id"])
    except Exception as exc:
        import traceback

        print(f"ERROR in ensure_user (sub={sub!r}): {exc}")
        print(traceback.format_exc())

    return get_user_id(request)
