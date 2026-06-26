"""User identity sync.

Resolves the authenticated caller to a row in the ``users`` table, creating one
on first sight. This closes the gap noted across the codebase ("avoiding a FK to
users before the auth->users mapping is finalized"): any write that references
``users(id)`` as a foreign key — deal_interest, compliance_override_requests,
member_investments, notification_recipients — needs a real users row, and live
Auth0 users were never being inserted, which produced 500s on the first such
write (e.g. POST /deals/{id}/compliance-requests).

``ensure_user`` returns the canonical ``users.id`` so callers use the id that
actually satisfies the FK, rather than a value merely derived from the token.
"""

from fastapi import Request

from services.permissions import get_user_id
from routers.entities import get_org_id


def _claims(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


async def ensure_user(conn, request: Request) -> str:
    """Return the caller's ``users.id``, inserting the row if it does not exist.

    Resolution order, chosen so the in-process verify scripts (which seed a user
    by ``id``) and live Auth0 users (identified by ``auth0_sub``) both work:
      1. An existing row whose ``id`` equals the token-derived id.
      2. An existing row whose ``auth0_sub`` matches the token ``sub``.
      3. A freshly inserted row keyed on ``auth0_sub``.

    Never raises — on any unexpected error it falls back to the token-derived id
    so read paths are unaffected. NOTE: when the INSERT fails the returned id is
    NOT in the DB, so the caller's FK insert will 500; the traceback below is the
    signal to look for in the logs.
    """
    claims = _claims(request)
    user_id = get_user_id(request)
    org_id = get_org_id(request)
    sub = claims.get("sub")

    try:
        existing = await conn.fetchrow("SELECT id FROM users WHERE id = $1", user_id)
        if existing:
            return str(existing["id"])

        if sub:
            by_sub = await conn.fetchrow(
                "SELECT id FROM users WHERE auth0_sub = $1", sub
            )
            if by_sub:
                return str(by_sub["id"])

            email = claims.get("email")
            full_name = (
                claims.get("name")
                or claims.get("nickname")
                or email
                or "Member"
            )
            # Include `role` — it is NOT NULL in the users table (every seed
            # INSERT sets it), so omitting it made this INSERT fail silently and
            # the caller's FK insert 500. Default new live users to 'member'.
            inserted = await conn.fetchrow(
                """
                INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                VALUES ($1, $2, $3, $4, $5, 'member')
                ON CONFLICT (auth0_sub) DO UPDATE
                    SET email = COALESCE(EXCLUDED.email, users.email)
                RETURNING id
                """,
                user_id, org_id, email, full_name, sub,
            )
            if inserted:
                return str(inserted["id"])
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        print(f"ERROR in ensure_user (sub={sub!r}, user_id={user_id!r}): {exc}")
        print(traceback.format_exc())

    return user_id
