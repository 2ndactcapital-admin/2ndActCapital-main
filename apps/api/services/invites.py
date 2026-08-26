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

EMAIL DELIVERY (SMTP/SES sprint, 2026-08-26)
────────────────────────────────────────────────────────────────────────────
Delivery IS now wired: :func:`create_invite` renders the invite through
``services.email.render_invite_email`` and attempts a real SES send, and every
invite carries an explicit :class:`InviteDelivery` record saying what actually
happened. The previous note here said delivery was "intentionally not wired"
because the SES gate failed; that gate STILL fails (the credentials resolve to
the Textract-only IAM user, which has no ``ses:SendEmail``), but the difference
is that the failure is now surfaced, specific and actionable instead of being a
comment. The enrollment link remains available for manual sharing — announced
as a fallback, never as a silent substitute for delivery.
"""

import secrets
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from services import email as email_service
from services.org_settings import (
    DEFAULT_SETTINGS,
    INVITE_EXPIRY_DAYS_KEY,
    get_setting,
    get_setting_with_origin,
)

# 32 random bytes → ~43 URL-safe chars. Comfortably beyond guessing range while
# staying short enough for a clean enrollment link.
_TOKEN_BYTES = 32

# The Hollisworks platform domain each tenant's subdomain hangs off. Used ONLY
# to derive a per-org enrollment base when organizations.enroll_url is NULL —
# never to override a stored value. Mirrors services.tenant's resolution rule
# (``<slug>.hollisworks.com``) from the other direction.
PLATFORM_DOMAIN = "hollisworks.com"
DEFAULT_ENROLL_PATH = "/enroll"

# Query param the enrollment page reads the token from.
TOKEN_PARAM = "invite_token"

# Default invite lifetime. Reasonable window for a member to accept.
#
# This is now only the FALLBACK. The effective lifetime is the org's own
# ``invite.expiry_days`` setting, resolved by :func:`resolve_invite_ttl_days`;
# an org that has never configured one still gets exactly this value, so
# behaviour for every existing org is unchanged. The number itself lives in
# ``services.org_settings.DEFAULT_SETTINGS`` (that module's docstring: it *is*
# the default data) and is read from there rather than duplicated.
INVITE_TTL_DAYS = DEFAULT_SETTINGS[INVITE_EXPIRY_DAYS_KEY]


async def resolve_invite_ttl_days(conn, org_id: str) -> int:
    """The invite lifetime, in days, THIS org is configured for.

    Reads ``invite.expiry_days`` through the normal settings resolver, so an org
    that has never set it transparently gets :data:`INVITE_TTL_DAYS`. The value
    is re-validated here as well as at write time: settings rows predate the
    write-time validator, and an invite with a nonsense expiry is worse than one
    with the default.

    NULL is "not configured", not "broken". ``get_setting`` only falls back to
    DEFAULT_SETTINGS when NO row exists — an org that CLEARS the key keeps a row
    holding jsonb ``null``, which is the documented way to un-configure a
    setting. That has to resolve quietly to the default; logging it as unusable
    would put an error in the logs every time an invite is created by an org
    that has deliberately reset the key.
    """
    value = await get_setting(conn, org_id, INVITE_EXPIRY_DAYS_KEY)
    if value is None:
        return INVITE_TTL_DAYS
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        print(f"[invites] unusable {INVITE_EXPIRY_DAYS_KEY} for org {org_id!r}: {exc}")
        return INVITE_TTL_DAYS
    return days if days > 0 else INVITE_TTL_DAYS

# Roles an admin may mint via an invite. Deliberately excludes 'super_admin'
# (Ripasso platform staff) so an org admin can never escalate a new account to
# platform staff through the invite path.
ALLOWED_INVITE_ROLES = ("member", "org_admin")


def generate_invite_token() -> str:
    """Return a fresh cryptographically-random, URL-safe invite token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


class EnrollmentUrlError(ValueError):
    """The creating org has no usable enrollment base URL. Fail loud, never relative."""


def build_enrollment_url(enroll_url: str | None, token: str, *, slug: str | None = None) -> str:
    """Return the FULLY-QUALIFIED enrollment link for ``token``.

    THE BUG THIS FIXES (real, observed): the link was previously built in
    ``routers/invites.py`` as::

        base = os.environ.get("WEB_BASE_URL") or os.environ.get("APP_BASE_URL") or ""
        f"{base}/enroll?invite_token={token}"

    Neither env var is set in production, so every invite came back as the bare
    relative path ``/enroll?invite_token=…`` — unusable to paste into an email.
    And the fallback was WORSE than useless when set: ``APP_BASE_URL`` is a
    single shared value pointing at 2nd Act, so a Hollisworks invite would have
    been handed 2nd Act's domain. That is the identical "silently inherit the
    other tenant's env var" shape as the three Auth0 bugs fixed earlier this
    session (domain ?? AUTH0_DOMAIN, appBaseUrl ?? APP_BASE_URL, audience ||
    2nd Act's). A URL that a member clicks MUST be derived per-org.

    The base therefore comes from the creating org's own
    ``organizations.enroll_url`` — the column exists precisely for this and is
    populated for the live orgs. Environment variables are not consulted at all.

    Rules, all fail-loud:
      * a stored ``enroll_url`` is used verbatim (path preserved) — we never
        rewrite an org's configured path;
      * when it is NULL/blank we DERIVE ``https://<slug>.hollisworks.com/enroll``,
        which is exactly the format the live rows already hold;
      * a value that is not an absolute http(s) URL with a host raises
        :class:`EnrollmentUrlError` rather than degrading to a relative path —
        returning something unusable is the bug, so we refuse instead;
      * the token is added with proper query-string merging, so a base that
        already carries a query (``…/enroll?ref=x``) gets ``&`` not a second
        ``?``, and the token is percent-encoded.
    """
    base = (enroll_url or "").strip()
    if not base:
        clean_slug = (slug or "").strip().lower()
        if not clean_slug:
            raise EnrollmentUrlError(
                "Organization has neither an enroll_url nor a slug — cannot build "
                "a fully-qualified enrollment link. Set organizations.enroll_url."
            )
        base = f"https://{clean_slug}.{PLATFORM_DOMAIN}{DEFAULT_ENROLL_PATH}"

    parts = urlsplit(base)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise EnrollmentUrlError(
            f"organizations.enroll_url must be an absolute http(s) URL, got {base!r}. "
            "Refusing to emit a relative enrollment link."
        )

    # Preserve any existing query params, then set/replace the token.
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != TOKEN_PARAM]
    query.append((TOKEN_PARAM, token))
    # Fragments are meaningless on an enrollment link and would swallow the query
    # for a naive copy/paste, so drop any.
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


async def org_enrollment_base(conn, org_id: str) -> tuple[str | None, str | None]:
    """Return ``(enroll_url, slug)`` for ``org_id`` — the inputs to the URL build.

    Read org-blind by primary key on purpose: the caller has ALREADY resolved
    ``org_id`` from its own request context (never the request body), so this is
    a lookup of the caller's own org, and RLS on ``organizations`` independently
    confines it.
    """
    row = await conn.fetchrow(
        "SELECT enroll_url, slug FROM organizations WHERE id = $1", org_id
    )
    if row is None:
        raise EnrollmentUrlError(f"No organization {org_id!r} — cannot build an invite link")
    return row["enroll_url"], row["slug"]


async def enrollment_url_for_org(conn, org_id: str, token: str) -> str:
    """Fully-qualified enrollment link for ``token``, from ``org_id``'s own config."""
    enroll_url, slug = await org_enrollment_base(conn, org_id)
    return build_enrollment_url(enroll_url, token, slug=slug)


# ── Invite email delivery ──────────────────────────────────────────────────


@dataclass(frozen=True)
class InviteDelivery:
    """What actually happened to the invite email. Always present on an invite.

    This type is the whole point of the SES sprint. The old behaviour was to
    return ``enrollment_url`` and say nothing about delivery, which reads
    identically whether mail was sent, was never attempted, or was refused. An
    admin cannot act on that. So every invite now carries one of three HONEST
    states in :attr:`status`:

    * ``"sent"``    — SES accepted the message and returned a real message id.
    * ``"blocked"`` — a send was required and could not happen. :attr:`reason`
      names the actual gap and the action that fixes it. The enrollment URL is
      still returned, but :attr:`manual_share_required` is True, so the caller
      and the admin both SEE that they must share it by hand.
    * ``"skipped"`` — the caller explicitly asked for no email (verification
      runs, bulk back-fills). Never used to paper over a failure.

    There is no fourth "probably fine" state and no default that means success.
    """

    status: str
    manual_share_required: bool
    message_id: str | None = None
    reason: str | None = None
    gap: str | None = None
    recipient_redacted: str | None = None
    subject: str | None = None

    @property
    def sent(self) -> bool:
        return self.status == "sent" and bool(self.message_id)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "sent": self.sent,
            "manual_share_required": self.manual_share_required,
            "message_id": self.message_id,
            "reason": self.reason,
            "gap": self.gap,
            "recipient": self.recipient_redacted,
            "subject": self.subject,
        }


async def resolve_org_display_name(conn, org_id: str) -> str:
    """The name THIS org's outbound mail should be signed with.

    Prefers the org's OWN ``brand.name`` setting, but only when it is genuinely
    the org's — ``get_setting_with_origin`` is used instead of ``get_setting``
    for exactly this reason. ``DEFAULT_SETTINGS['brand.name']`` is the literal
    string ``"2nd Act Capital"``, so an org that has never configured branding
    would otherwise have every invite it sends signed with ANOTHER TENANT'S
    NAME. That is the same "silently inherit the other tenant's value" shape as
    the Auth0 ``domain ?? AUTH0_DOMAIN`` and ``APP_BASE_URL`` bugs, and it is
    worse in an email because the recipient sees it.

    So: org's own brand.name if set, else ``organizations.name`` (per-org and
    NOT NULL), and the platform default is never reachable here.
    """
    value, is_default = await get_setting_with_origin(conn, org_id, "brand.name")
    if not is_default and isinstance(value, str) and value.strip():
        return value.strip()

    row = await conn.fetchrow("SELECT name FROM organizations WHERE id = $1", org_id)
    if row is None or not (row["name"] or "").strip():
        raise EnrollmentUrlError(
            f"Organization {org_id!r} has no name — refusing to send an invite "
            "that cannot say who it is from."
        )
    return row["name"].strip()


def _blocked_delivery(exc: Exception, *, recipient: str, subject: str | None) -> InviteDelivery:
    gap = getattr(exc, "gap", "unknown")
    return InviteDelivery(
        status="blocked",
        manual_share_required=True,
        reason=str(exc),
        gap=gap,
        recipient_redacted=email_service.redact_email(recipient),
        subject=subject,
    )


async def send_invite_email(
    conn,
    *,
    org_id: str,
    email: str,
    full_name: str | None,
    enrollment_url: str,
    expiry_days: int,
    expires_at=None,
) -> InviteDelivery:
    """Render and send ONE org's invite email. Returns the honest delivery state.

    Does NOT raise for a blocked send: the invite itself was created
    successfully and must still be returned to the admin with its link. What it
    does instead is refuse to be quiet — the returned :class:`InviteDelivery`
    carries ``status="blocked"``, ``manual_share_required=True`` and the exact
    reason, and a line is logged. Raising here would either lose the created
    invite or force every caller to re-implement this same handling.

    All content comes from THIS org: its own display name, its own enrollment
    URL, its own ``invite.expiry_days``.
    """
    org_name = await resolve_org_display_name(conn, org_id)
    rendered = email_service.render_invite_email(
        org_name=org_name,
        enrollment_url=enrollment_url,
        expiry_days=expiry_days,
        expires_at=expires_at,
        full_name=full_name,
    )

    try:
        # boto3 is synchronous; keep the event loop free.
        from starlette.concurrency import run_in_threadpool

        result = await run_in_threadpool(
            lambda: email_service.send_email(
                to_address=email,
                subject=rendered.subject,
                text_body=rendered.text,
                html_body=rendered.html,
            )
        )
    except email_service.EmailBlocked as exc:
        print(
            f"[invites] invite email BLOCKED for {email_service.redact_email(email)} "
            f"(org {org_id}, gap={getattr(exc, 'gap', 'unknown')}): {exc}"
        )
        return _blocked_delivery(exc, recipient=email, subject=rendered.subject)
    except Exception as exc:  # noqa: BLE001 — an unexpected fault is still a blocked send
        print(
            f"[invites] invite email FAILED unexpectedly for "
            f"{email_service.redact_email(email)} (org {org_id}): "
            f"{type(exc).__name__}: {exc}"
        )
        return _blocked_delivery(exc, recipient=email, subject=rendered.subject)

    print(
        f"[invites] invite email SENT to {result.recipient_redacted} "
        f"(org {org_id}) message_id={result.message_id}"
    )
    return InviteDelivery(
        status="sent",
        manual_share_required=False,
        message_id=result.message_id,
        recipient_redacted=result.recipient_redacted,
        subject=result.subject,
    )


async def create_invite(
    conn,
    *,
    org_id: str,
    email: str,
    full_name: str | None,
    role: str,
    invited_by: str,
    profile_id: str | None = None,
    ttl_days: int | None = None,
    send_email: bool = True,
):
    """Insert a pending invite ``users`` row and return it.

    ``auth0_sub`` is left NULL; ``invite_status`` is ``'pending'``; the expiry is
    ``now() + ttl_days``. Raises ``asyncpg.UniqueViolationError`` if the email
    (or, astronomically unlikely, the token) already exists — the caller maps
    that to a 409.

    ``ttl_days`` defaults to the ORG'S configured ``invite.expiry_days`` (see
    :func:`resolve_invite_ttl_days`), resolved on this same connection so the
    setting and the insert see one consistent state. An explicit value still
    wins — the verify script and any future "invite valid for N days" control
    need to override it.

    ``profile_id`` is the OPTIONAL, additive permission persona. It is written
    at invite time so the account carries its profile from the moment it is
    created, rather than needing a second admin action after enrolment. It is
    NOT a substitute for ``role``, which stays required: the two are separate
    grants (see the SOC Phase A note on ``users.profile_id``), and the caller is
    responsible for having validated the profile against its own org — this
    function is org-blind by design, like everything else here.

    ``send_email`` controls whether a REAL SES send is attempted. It defaults to
    True — the whole point of this path is that an invite is delivered — and the
    outcome is always reported in ``result["email_delivery"]``, never inferred.
    Callers that must not emit mail (verification runs, back-fills) pass False
    and get an explicit ``"skipped"`` status, which is a different thing from a
    failure and is recorded as such.
    """
    if ttl_days is None:
        ttl_days = await resolve_invite_ttl_days(conn, org_id)

    token = generate_invite_token()
    row = await conn.fetchrow(
        """
        INSERT INTO users (
            id, org_id, email, full_name, role, auth0_sub, profile_id,
            invite_token, invite_status, invited_by, invited_at, invite_expires_at
        )
        VALUES (
            extensions.uuid_generate_v4(), $1, $2, $3, $4, NULL, $8,
            $5, 'pending', $6, now(), now() + make_interval(days => $7)
        )
        RETURNING id, org_id, email, full_name, role, profile_id,
                  invite_token, invite_status,
                  invited_by, invited_at, invite_expires_at
        """,
        org_id, email, full_name, role, token, invited_by, ttl_days, profile_id,
    )
    # The returned link is built HERE, from the creating org's own stored
    # enroll_url — so the value the admin copies is always fully qualified and
    # always points at that org's real subdomain. `org_id` is the one the caller
    # resolved from its request context, so an org can only ever mint a link on
    # its own domain (see the router's get_org_id, and Task 4's cross-org test).
    result = dict(row)
    result["enrollment_url"] = await enrollment_url_for_org(conn, org_id, token)

    # Delivery. The invite row is already committed-and-correct at this point,
    # so a blocked send must NOT undo it — an admin who cannot email the link
    # can still share it, and destroying a valid invite because SES is
    # unavailable would be a worse outcome than the gap itself. What must never
    # happen is delivery failing quietly, which is what `email_delivery`
    # prevents: it is populated on EVERY path, including the skipped one.
    if send_email:
        delivery = await send_invite_email(
            conn,
            org_id=org_id,
            email=email,
            full_name=full_name,
            enrollment_url=result["enrollment_url"],
            expiry_days=ttl_days,
            expires_at=row["invite_expires_at"],
        )
    else:
        delivery = InviteDelivery(
            status="skipped",
            manual_share_required=True,
            reason="Caller requested no email; share the enrollment link manually.",
            gap="not_requested",
            recipient_redacted=email_service.redact_email(email),
        )
    result["email_delivery"] = delivery.as_dict()
    return result


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


# ── Enrollment (redemption) ────────────────────────────────────────────────
# The states the /enroll page distinguishes. Each maps to its OWN honest,
# specific message — an expired token and an already-used token must never
# collapse into one generic "invalid link" error, because the two need opposite
# next actions (ask for a new invite vs. just sign in).
STATUS_VALID = "valid"
STATUS_EXPIRED = "expired"
STATUS_ACCEPTED = "accepted"
STATUS_REVOKED = "revoked"
STATUS_NOT_FOUND = "not_found"
STATUS_MISSING = "missing_token"

ENROLL_STATUSES = (
    STATUS_VALID, STATUS_EXPIRED, STATUS_ACCEPTED,
    STATUS_REVOKED, STATUS_NOT_FOUND, STATUS_MISSING,
)


async def inspect_invite_token(conn, token: str | None) -> tuple[str, dict | None]:
    """Classify ``token`` into one of :data:`ENROLL_STATUSES`; return ``(status, row)``.

    This is the richer sibling of :func:`validate_invite_token`, which collapses
    every failure into ``None``. The enrollment page needs to tell the invitee
    WHICH thing went wrong, so this reports the state instead of hiding it.

    Deliberately org-BLIND: the person redeeming has no session and therefore no
    org context yet — the token itself is the only credential, and the row it
    resolves to carries its own ``org_id``, which is what the account ends up
    bound to. That is the cross-org guarantee: redemption can never move a user
    into an org other than the one that issued the token, because we never read
    an org from the caller.

    Expiry is judged by the DATABASE clock (``now()``), never the app server's.
    """
    if not token or not str(token).strip():
        return STATUS_MISSING, None

    row = await conn.fetchrow(
        """
        SELECT u.id, u.org_id, u.email, u.full_name, u.role, u.auth0_sub,
               u.invite_status, u.invited_at, u.invite_expires_at,
               u.invite_expires_at <= now() AS is_expired,
               o.name AS org_name, o.slug AS org_slug,
               o.enroll_url, o.login_url
        FROM users u
        JOIN organizations o ON o.id = u.org_id
        WHERE u.invite_token = $1
        """,
        str(token).strip(),
    )
    if row is None:
        return STATUS_NOT_FOUND, None

    data = dict(row)
    status = data.get("invite_status")

    # An already-linked row is accepted regardless of what the status column
    # says — the auth0_sub is the fact on the ground.
    if status == "accepted" or data.get("auth0_sub"):
        return STATUS_ACCEPTED, data
    if status == "revoked":
        return STATUS_REVOKED, data
    if status != "pending":
        # Unknown/NULL status on a row carrying a token: not a usable invite.
        return STATUS_NOT_FOUND, data
    if data.get("is_expired"):
        return STATUS_EXPIRED, data
    return STATUS_VALID, data


async def accept_invite(
    conn,
    *,
    token: str,
    auth0_sub: str,
    full_name: str | None = None,
) -> tuple[str, dict | None]:
    """Redeem ``token`` for ``auth0_sub``. Returns ``(status, row)``.

    MATCH, DON'T DUPLICATE — the same pattern ``ensure_user`` follows. The
    pending invite row ALREADY exists (the admin created it, with the right
    org_id, email and role); enrolling must CLAIM that row by writing
    ``auth0_sub`` onto it, never insert a second row for the same person. A
    duplicate would be the real damage here: ``users.email`` is UNIQUE, so the
    insert would either fail outright or, worse, strand the member on a row with
    none of the role/org the admin assigned.

    Rule 3 (bi-temporal) does NOT apply: ``users`` is an identity table with an
    ``updated_at`` column, mutated in place by ``ensure_user`` and the admin
    role-assignment path. This follows the established convention.

    ``email`` is NOT overwritten from the identity provider: the invited address
    is what the admin authorised and what ``users.email`` is UNIQUE on. Only a
    missing ``full_name`` is back-filled.

    Statuses other than ``valid`` are returned unchanged so the caller can emit
    the specific message. Two extra outcomes:
      * ``sub_conflict``  — this auth0_sub is already linked to a DIFFERENT user
        row (someone signed in with an account that is already a member).
      * ``race``          — the row stopped being claimable between the check and
        the update; re-inspected and reported as its real, current status.
    """
    status, row = await inspect_invite_token(conn, token)
    if status != STATUS_VALID:
        return status, row

    existing = await conn.fetchrow(
        "SELECT id, org_id, email FROM users WHERE auth0_sub = $1", auth0_sub
    )
    if existing is not None and str(existing["id"]) != str(row["id"]):
        return "sub_conflict", dict(existing)

    claimed = await conn.fetchrow(
        """
        UPDATE users
        SET auth0_sub = $2,
            invite_status = 'accepted',
            full_name = COALESCE(full_name, $3),
            updated_at = now()
        WHERE id = $1
          AND invite_status = 'pending'
          AND auth0_sub IS NULL
          AND invite_expires_at > now()
        RETURNING id, org_id, email, full_name, role, auth0_sub, invite_status,
                  invite_expires_at
        """,
        row["id"], auth0_sub, full_name,
    )
    if claimed is None:
        # Lost a race (or the row changed underneath us). Report the truth.
        recheck, recheck_row = await inspect_invite_token(conn, token)
        return ("race" if recheck == STATUS_VALID else recheck), recheck_row
    return STATUS_VALID, dict(claimed)
