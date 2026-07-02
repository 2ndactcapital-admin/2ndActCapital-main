"""User identity sync.

Resolves the authenticated caller to a row in the ``users`` table, creating one
on first sight. Any write that references ``users(id)`` as a foreign key —
deal_votes, deal_interest, compliance_override_requests, member_investments,
notification_recipients — needs a real users row.

Identity model: the ``users`` table is keyed on ``auth0_sub`` (the raw JWT
``sub`` string) and its primary key is a DB-generated ``uuid_generate_v4()``.
The token ``sub`` is NOT a UUID and the v5-derived id from ``get_user_id`` is
only a last-resort fallback — it never matches a v4 PK, which is why FK inserts
were failing. ``ensure_user`` therefore resolves strictly by ``auth0_sub`` and
returns the **DB-generated** id, which is the value callers must use for FKs.
"""

from uuid import UUID

from fastapi import Request

from services.permissions import get_user_id
from routers.entities import get_org_id


def _claims(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def _as_uuid(value) -> str | None:
    """Return the value as a canonical UUID string, or None if it isn't one."""
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        return None


async def ensure_user(conn, request: Request) -> str:
    """Return the caller's ``users.id``, inserting the row if it does not exist.

    Resolution order:
      1. By ``auth0_sub`` — the canonical key (raw JWT ``sub`` string).
      2. If ``sub`` is itself a UUID that matches an existing row id (the verify
         scripts stub ``sub`` = a seeded user's UUID), use that row.
      3. Insert a new row, letting Postgres generate the id
         (``uuid_generate_v4()``); return the generated id.

    Never raises — on unexpected error it falls back to the token-derived id so
    read paths are unaffected (the traceback below is the signal in the logs).
    """
    claims = _claims(request)
    sub = claims.get("sub")
    org_id = get_org_id(request)

    if not sub:
        return get_user_id(request)

    try:
        # 1. Canonical lookup by auth0_sub.
        by_sub = await conn.fetchrow(
            "SELECT id FROM users WHERE auth0_sub = $1", sub
        )
        if by_sub:
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
        # Some Auth0 strategies (e.g. social login without verified email) omit
        # the email claim. users.email is NOT NULL, so use a deterministic
        # placeholder so the insert never violates the constraint. The real email
        # should be back-filled via the Auth0 management API or a profile-complete
        # flow once the user verifies their address.
        email = claims.get("email") or f"{sub}@placeholder.local"
        full_name = (
            claims.get("name")
            or claims.get("nickname")
            or claims.get("email")
            or "Member"
        )
        inserted = await conn.fetchrow(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES (uuid_generate_v4(), $1, $2, $3, $4, 'member')
            ON CONFLICT (auth0_sub) DO UPDATE
                SET email = COALESCE(
                    NULLIF(EXCLUDED.email, EXCLUDED.auth0_sub || '@placeholder.local'),
                    users.email
                )
            RETURNING id
            """,
            org_id, email, full_name, sub,
        )
        if inserted:
            return str(inserted["id"])
    except Exception as exc:
        import traceback

        print(f"ERROR in ensure_user (sub={sub!r}): {exc}")
        print(traceback.format_exc())

    return get_user_id(request)
