"""Admin-provisioned member invites (Multi-tenant Sprint 2).

The invite lives on the ``users`` row itself — there is no separate invites
table. An invite is a ``users`` row whose ``auth0_sub`` is still NULL and whose
``invite_status`` is ``'pending'``; the invite lifecycle is carried by the
``invite_token`` / ``invite_status`` / ``invited_by`` / ``invited_at`` /
``invite_expires_at`` columns (all confirmed against docs/schema_snapshot.sql).

Design notes
------------
* ``invite_token`` is generated with :func:`secrets.token_urlsafe` — a real,
  cryptographically-random, URL-safe value. It is globally UNIQUE
  (``users_invite_token_key``).
* ``auth0_sub`` stays NULL until the invitee actually enrolls. Postgres allows
  many NULLs under a UNIQUE constraint, so unlimited pending invites coexist.
* Expiry is computed with the database clock (``now() + interval``) so the app
  server's wall-clock never enters the trust boundary.
* org scoping is enforced two ways: (1) callers pass ``org_id`` from the request
  context (never the request body — standing rule), and (2) the ``users`` RLS
  policy independently confines every read/write to the caller's org, so a
  different org's admin cannot see or mutate these rows even if they guessed an
  id. Bi-temporal Rule 3 does NOT apply: ``users`` is an identity table with an
  ``updated_at`` column, not a bi-temporal history table (``ensure_user`` and
  the admin role-assignment path both mutate it in place).

Email delivery of the invite (Task 3) is intentionally NOT wired here: the SES
credential gate failed this sprint (the Textract IAM user carries no SES
permission), so the enrollment link is returned to the admin for manual sharing
instead. See the sprint report / verify_multitenant2.py for the gate details.
"""

import secrets

# 32 random bytes → ~43 URL-safe chars. Comfortably beyond guessing range while
# staying short enough for a clean enrollment link.
_TOKEN_BYTES = 32

# Default invite lifetime. Reasonable window for a member to accept.
INVITE_TTL_DAYS = 7

# Roles an admin may mint via an invite. Deliberately excludes 'super_admin'
# (Ripasso platform staff) so an org admin can never escalate a new account to
# platform staff through the invite path.
ALLOWED_INVITE_ROLES = ("member", "org_admin")


def generate_invite_token() -> str:
    """Return a fresh cryptographically-random, URL-safe invite token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


async def create_invite(
    conn,
    *,
    org_id: str,
    email: str,
    full_name: str | None,
    role: str,
    invited_by: str,
    ttl_days: int = INVITE_TTL_DAYS,
):
    """Insert a pending invite ``users`` row and return it.

    ``auth0_sub`` is left NULL; ``invite_status`` is ``'pending'``; the expiry is
    ``now() + ttl_days``. Raises ``asyncpg.UniqueViolationError`` if the email
    (or, astronomically unlikely, the token) already exists — the caller maps
    that to a 409.
    """
    token = generate_invite_token()
    return await conn.fetchrow(
        """
        INSERT INTO users (
            id, org_id, email, full_name, role, auth0_sub,
            invite_token, invite_status, invited_by, invited_at, invite_expires_at
        )
        VALUES (
            extensions.uuid_generate_v4(), $1, $2, $3, $4, NULL,
            $5, 'pending', $6, now(), now() + make_interval(days => $7)
        )
        RETURNING id, org_id, email, full_name, role, invite_token, invite_status,
                  invited_by, invited_at, invite_expires_at
        """,
        org_id, email, full_name, role, token, invited_by, ttl_days,
    )


async def validate_invite_token(conn, token: str | None):
    """Return the pending invite row for ``token``, or None if it is not usable.

    Usable means: the token exists, its status is exactly ``'pending'``, and it
    has not passed ``invite_expires_at`` (compared against the DB clock). A
    revoked, already-accepted, or expired token therefore returns None — it is
    never silently treated as valid.
    """
    if not token:
        return None
    return await conn.fetchrow(
        """
        SELECT id, org_id, email, full_name, role,
               invite_status, invite_expires_at
        FROM users
        WHERE invite_token = $1
          AND invite_status = 'pending'
          AND invite_expires_at > now()
        """,
        token,
    )


async def revoke_invite(conn, *, org_id: str, invite_id: str):
    """Revoke a still-pending invite in ``org_id``. Returns the row or None.

    Only a ``'pending'`` invite can be revoked (an already-accepted account is
    not an invite anymore). The explicit ``org_id`` predicate is belt-and-braces
    on top of the RLS policy: a different org's admin gets None, not a mutation.
    """
    return await conn.fetchrow(
        """
        UPDATE users
        SET invite_status = 'revoked', updated_at = now()
        WHERE id = $1 AND org_id = $2 AND invite_status = 'pending'
        RETURNING id, org_id, email, invite_status
        """,
        invite_id, org_id,
    )
