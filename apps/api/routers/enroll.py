"""Invite redemption — the backend half of the /enroll page.

    GET  /enroll/validate   (PUBLIC)  classify an invite token, pre-auth
    POST /enroll/accept     (AUTHED)  claim the pending users row for a new sub

WHY A PUBLIC VALIDATE ENDPOINT. The invitee has no session — that is the entire
point of an invite — so the page they land on MUST be able to check the token
before Auth0 is involved. This is the same pre-auth carve-out shape as
``/theme/public`` and ``/tenant/resolve``, and like those it exposes only what
the token's holder is already entitled to see: the org's public name/slug and
the address the invite was addressed to. The token IS the credential; someone
without it gets ``not_found``.

CROSS-ORG SAFETY. Neither endpoint ever reads an org id from the caller. The
invite row carries its own ``org_id`` (set when the admin created it, from THAT
admin's request context), and redemption writes only to that row — so a redeemed
account always lands in the org that issued the token, and there is no input
that could move it elsewhere. The optional ``host`` check below is an additional
UX guard on top of that, not the boundary itself.

WHY ``ensure_user`` IS NOT USED HERE. ``ensure_user`` INSERTs a fresh row for an
unknown ``auth0_sub``. On the enrollment path that is exactly wrong: the row
already exists (pending, with the org/role/email the admin authorised) and must
be CLAIMED, not duplicated — ``users.email`` is UNIQUE, so a duplicate would
either fail or strand the member on a row with none of their assignments. This
router therefore reads the validated ``sub`` claim directly and calls
``services.invites.accept_invite``, which matches on the invite token.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.audit import write_audit_log
from services.database import get_pool
from services.invites import (
    STATUS_ACCEPTED,
    STATUS_EXPIRED,
    STATUS_MISSING,
    STATUS_NOT_FOUND,
    STATUS_REVOKED,
    STATUS_VALID,
    accept_invite,
    inspect_invite_token,
)
from services.tenant import resolve_tenant
from services.users import fetch_auth0_identity

router = APIRouter(tags=["enroll"])

# One honest, specific sentence per outcome. These are the strings the member
# actually reads, so each names what happened and what to do next. An expired
# token and an already-used token get OPPOSITE advice — collapsing them into a
# generic "invalid link" is the failure mode this sprint exists to avoid.
STATUS_MESSAGES = {
    STATUS_VALID: "Your invitation is valid. Create your account to continue.",
    STATUS_EXPIRED: (
        "This invitation has expired. Invitations are valid for a limited time — "
        "ask your administrator to send you a new one."
    ),
    STATUS_ACCEPTED: (
        "This invitation has already been used. The account it created is ready — "
        "sign in instead."
    ),
    STATUS_REVOKED: (
        "This invitation was withdrawn by an administrator and can no longer be "
        "used. Contact them if you believe this is a mistake."
    ),
    STATUS_NOT_FOUND: (
        "We do not recognise this invitation link. Check that you copied the "
        "whole link, or ask your administrator to send a new one."
    ),
    STATUS_MISSING: (
        "This link is missing its invitation token. Use the full link from your "
        "invitation email."
    ),
    "wrong_tenant": (
        "This invitation belongs to a different firm. Open it on that firm's own "
        "site — the link in your invitation goes to the right place."
    ),
    "sub_conflict": (
        "The account you signed in with already belongs to another member here. "
        "Sign out and enrol with the address your invitation was sent to."
    ),
    "race": "This invitation was just used. Sign in instead.",
}


class EnrollAcceptRequest(BaseModel):
    invite_token: str
    # The browser's Host, forwarded by the Next.js server component exactly as
    # lib/tenant.js forwards it to /tenant/resolve (a server->API fetch would
    # otherwise carry the API's own host). This can only NARROW the outcome: the
    # org an account lands in always comes from the invite row, never from here.
    host: str | None = None


def _public_payload(status: str, row: dict | None, *, host_org: dict | None = None) -> dict:
    """The response body shared by validate and the error legs of accept."""
    payload = {
        "status": status,
        "message": STATUS_MESSAGES.get(status, STATUS_MESSAGES[STATUS_NOT_FOUND]),
        "valid": status == STATUS_VALID,
        "email": None,
        "full_name": None,
        "org_id": None,
        "org_name": None,
        "org_slug": None,
        "login_url": None,
        "expires_at": None,
    }
    if row:
        payload.update(
            email=row.get("email"),
            full_name=row.get("full_name"),
            org_id=str(row["org_id"]) if row.get("org_id") else None,
            org_name=row.get("org_name"),
            org_slug=row.get("org_slug"),
            login_url=row.get("login_url"),
            expires_at=(
                row["invite_expires_at"].isoformat()
                if row.get("invite_expires_at")
                else None
            ),
        )
        # On the wrong-tenant leg, tell them where the link DOES belong so the
        # message is actionable rather than a dead end. The token is deliberately
        # NOT re-attached here — this value is shown, and we do not want a
        # complete working link rendered on the wrong firm's site.
        if status == "wrong_tenant":
            payload["correct_url"] = row.get("enroll_url")
    if host_org:
        payload["host_org_id"] = host_org.get("org_id")
        payload["host_org_name"] = host_org.get("org_name")
    return payload


async def _host_org(conn, host: str | None) -> dict | None:
    """Resolve a forwarded browser Host to a tenant org, or None if not a tenant."""
    if not host:
        return None
    tenant = await resolve_tenant(conn, host)
    return tenant if tenant.get("resolved") and tenant.get("org_id") else None


@router.get("/enroll/validate")
async def validate(request: Request, invite_token: str | None = None, host: str | None = None):
    """Classify an invite token. Public by design — see the module docstring.

    Always 200: the page renders a specific message for every outcome, so a
    failure state is DATA, not an HTTP error. An error status would force the
    frontend into a generic catch block, which is precisely how an expired token
    ends up looking identical to a typo'd one.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        status, row = await inspect_invite_token(conn, invite_token)
        host_org = await _host_org(conn, host or request.headers.get("host"))

    # A valid token opened on ANOTHER tenant's host: the Auth0 tenant that this
    # host would sign them into is the wrong one, so send them to the right site
    # rather than let them enrol against a tenant that does not hold their row.
    if (
        status == STATUS_VALID
        and host_org
        and str(host_org["org_id"]) != str(row["org_id"])
    ):
        status = "wrong_tenant"

    return _public_payload(status, row, host_org=host_org)


@router.post("/enroll/accept")
async def accept(request: Request, body: EnrollAcceptRequest):
    """Claim the pending invite row for the now-authenticated Auth0 identity.

    Requires a valid bearer token (this route is NOT in PUBLIC_PATHS): the sub
    written onto the row must be one Auth0 issued and this API verified, never
    one the caller typed. Returns 200 with the linked row, or a 4xx whose
    ``detail`` is the same specific message the page shows.
    """
    claims = getattr(request.state, "user", None) or {}
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="No authenticated identity to enrol")

    # An access token minted for a custom API audience carries no name claim —
    # it lives in the ID token, which this API never sees. Ask the issuing
    # tenant's /userinfo, exactly as services.users does. Best-effort: a failure
    # just leaves full_name as the admin entered it.
    full_name = claims.get("name") or claims.get("nickname")
    if not full_name:
        _, resolved_name = await fetch_auth0_identity(request, claims)
        full_name = resolved_name

    pool = await get_pool()
    async with pool.acquire() as conn:
        if body.host:
            status, row = await inspect_invite_token(conn, body.invite_token)
            host_org = await _host_org(conn, body.host)
            if (
                status == STATUS_VALID
                and host_org
                and str(host_org["org_id"]) != str(row["org_id"])
            ):
                payload = _public_payload("wrong_tenant", row, host_org=host_org)
                payload["detail"] = payload["message"]
                return JSONResponse(status_code=403, content=payload)

        status, row = await accept_invite(
            conn, token=body.invite_token, auth0_sub=sub, full_name=full_name
        )

        if status != STATUS_VALID:
            # A failed claim is a real HTTP error (nothing was written), but the
            # body carries the SAME `status`/`message` shape as /enroll/validate
            # so the page can render the specific outcome. Making the frontend
            # infer the reason from the status code would collapse expired and
            # revoked — both naturally 400 — into one message, which is exactly
            # the generic-error failure this sprint is closing.
            code = 409 if status in (STATUS_ACCEPTED, "sub_conflict", "race") else 400
            if status in (STATUS_NOT_FOUND, STATUS_MISSING):
                code = 404
            payload = _public_payload(status, row)
            payload["detail"] = payload["message"]  # keep generic clients working
            return JSONResponse(status_code=code, content=payload)

        # org_id comes from the INVITE row — the org that issued the token —
        # never from the caller's context.
        await write_audit_log(
            conn,
            org_id=str(row["org_id"]),
            action="accept_invite",
            table_name="users",
            record_id=row["id"],
            new={
                "invite_status": "accepted",
                "auth0_sub": sub,
                "email": row["email"],
            },
            actor=row["id"],
        )

    return {
        "status": STATUS_VALID,
        "message": "Enrollment complete.",
        "user_id": str(row["id"]),
        "org_id": str(row["org_id"]),
        "email": row["email"],
        "full_name": row["full_name"],
        "role": row["role"],
        "invite_status": row["invite_status"],
    }
