"""Admin invite endpoints (Multi-tenant Sprint 2).

An Org Admin (or Super Admin) provisions a pending member account; the invitee
later enrolls with the returned token. Gated by the same ``manage_members``
permission as the rest of member management (which already grants Super Admin a
first-checked bypass — see services.rbac.has_permission).

``org_id`` is ALWAYS taken from the request context (get_org_id), NEVER from the
request body — standing multi-tenant rule. Combined with the ``users`` RLS
policy, this makes cross-org invite creation/listing/revocation impossible.

Email delivery IS performed here as of the SMTP/SES sprint: ``create_invite``
attempts a real SES send and the outcome is returned to the admin verbatim in
``email_delivery``. When sending is blocked (today: the credentials resolve to
the Textract-only IAM user, which has no ``ses:SendEmail``), the response says
so explicitly with ``manual_share_required=true`` and the actionable reason —
``enrollment_url`` remains usable for manual sharing, but it is no longer
returned as though delivery had happened.
"""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from routers.entities import get_org_id
from services import email as email_service
from services.audit import write_audit_log
from services.database import get_pool
from services.invites import (
    ALLOWED_INVITE_ROLES,
    EnrollmentUrlError,
    build_enrollment_url,
    create_invite,
    org_enrollment_base,
    revoke_invite,
)
from services.rbac import require_permission
from services.users import ensure_user

router = APIRouter(tags=["invites"])


class InviteCreateRequest(BaseModel):
    # `extra="forbid"` is the standing-rule guard made mechanical: a body that
    # carries `org_id` (or any other field this endpoint does not own) is a 422,
    # not a silently-ignored key. The org comes from get_org_id, always.
    model_config = ConfigDict(extra="forbid")

    email: str
    full_name: str | None = None
    role: str = "member"
    # OPTIONAL, additive permission persona granted at invite time. Validated
    # against the CALLER'S OWN org below — never trusted from the body beyond
    # being an id to look up. `role` above stays required and unchanged; this
    # does not replace it.
    profile_id: UUID | None = None


class InviteDeliveryResponse(BaseModel):
    """What happened to the invite EMAIL — reported, never inferred.

    ``status`` is one of ``sent`` / ``blocked`` / ``skipped``. When it is not
    ``sent``, ``manual_share_required`` is true and ``reason`` names the real,
    actionable gap, so the admin UI can say "we could not email this — here is
    the link, and here is why" instead of silently showing a link that looks
    like a convenience copy of a mail that never went out.
    """

    status: str
    sent: bool
    manual_share_required: bool
    message_id: str | None = None
    reason: str | None = None
    gap: str | None = None
    # Redacted (``j…e@e…m``) on purpose — an API response is a log line
    # somewhere, and a member's address does not belong in one.
    recipient: str | None = None
    subject: str | None = None


class InviteResponse(BaseModel):
    id: UUID
    email: str
    full_name: str | None = None
    role: str | None = None
    profile_id: UUID | None = None
    invite_status: str | None = None
    invite_token: str | None = None
    enrollment_url: str | None = None
    invited_by: UUID | None = None
    invited_at: str | None = None
    invite_expires_at: str | None = None
    # Only ever populated on CREATE — listing existing invites re-reads rows
    # that carry no delivery record, and inventing one there would be a guess.
    email_delivery: InviteDeliveryResponse | None = None


async def _require_manage_members(request: Request) -> tuple[str, str]:
    """Resolve the caller, enforce ``manage_members``, return (actor_id, org_id)."""
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
    await require_permission(pool, actor_id, org_id, "manage_members")
    return actor_id, org_id


@router.post("/admin/invites", response_model=InviteResponse, status_code=201)
async def create_invite_endpoint(request: Request, body: InviteCreateRequest):
    actor_id, org_id = await _require_manage_members(request)

    role = (body.role or "member").strip()
    if role not in ALLOWED_INVITE_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"role must be one of {', '.join(ALLOWED_INVITE_ROLES)}",
        )
    email = (body.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    pool = await get_pool()
    async with pool.acquire() as conn:
        # The profile is validated against the CALLER'S OWN org, resolved from
        # the request context — so an admin cannot attach another tenant's
        # profile to an invite even by guessing a real id. Same predicate and
        # same 404 as the existing PUT /admin/users/{id}/profile, deliberately:
        # one rule for what "a profile this admin may grant" means.
        profile_id = str(body.profile_id) if body.profile_id else None
        if profile_id is not None:
            profile_ok = await conn.fetchval(
                "SELECT 1 FROM profiles WHERE id = $1 AND org_id = $2",
                body.profile_id, org_id,
            )
            if not profile_ok:
                raise HTTPException(
                    status_code=404, detail="Profile not found in org"
                )

        try:
            row = await create_invite(
                conn,
                org_id=org_id,
                email=email,
                full_name=(body.full_name or None),
                role=role,
                invited_by=actor_id,
                profile_id=profile_id,
            )
        except EnrollmentUrlError as exc:
            # The org has no usable enrollment base. Fail loudly with the real
            # reason rather than handing back an unusable relative link — that
            # silent degradation IS the bug this sprint fixes.
            raise HTTPException(status_code=500, detail=str(exc))
        except asyncpg.UniqueViolationError:
            # Email already exists (this org or, unseen under RLS, another org),
            # or a token collision. Either way the invite cannot be created.
            raise HTTPException(
                status_code=409,
                detail="A user with this email already exists",
            )

        # Populated by services.invites on every path — never absent, never a
        # bare success default. `.get` with an explicit blocked fallback so a
        # future caller that forgets to set it fails VISIBLY rather than
        # reporting a send that did not happen.
        delivery = row.get("email_delivery") or {
            "status": "blocked",
            "sent": False,
            "manual_share_required": True,
            "reason": "No delivery record was produced for this invite.",
            "gap": "unknown",
        }

        await write_audit_log(
            conn,
            org_id=org_id,
            action="create_invite",
            table_name="users",
            record_id=row["id"],
            new={
                "email": email,
                "role": role,
                "profile_id": profile_id,
                "invited_by": str(actor_id),
                "invite_status": "pending",
                # The audit trail records whether the invite was actually
                # DELIVERED, not merely created. "We sent it" and "we made a
                # link" are different claims and only one of them is auditable
                # against SES's message id.
                "email_status": delivery.get("status"),
                "email_message_id": delivery.get("message_id"),
            },
            actor=actor_id,
        )

    return InviteResponse(
        id=row["id"],
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
        profile_id=row["profile_id"],
        invite_status=row["invite_status"],
        invite_token=row["invite_token"],
        # Built by services.invites from THIS org's organizations.enroll_url —
        # always absolute, always this org's own subdomain.
        enrollment_url=row["enrollment_url"],
        invited_by=row["invited_by"],
        invited_at=str(row["invited_at"]) if row["invited_at"] else None,
        invite_expires_at=str(row["invite_expires_at"]) if row["invite_expires_at"] else None,
        email_delivery=InviteDeliveryResponse(**delivery),
    )


@router.get("/admin/invites", response_model=list[InviteResponse])
async def list_invites(
    request: Request,
    status: str | None = Query(None, description="Filter by invite_status"),
):
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()

    conditions = ["org_id = $1", "invite_status IS NOT NULL"]
    params: list = [org_id]
    if status:
        params.append(status)
        conditions.append(f"invite_status = ${len(params)}")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, email, full_name, role, profile_id, invite_status,
                   invite_token, invited_by, invited_at, invite_expires_at
            FROM users
            WHERE {' AND '.join(conditions)}
            ORDER BY invited_at DESC NULLS LAST
            """,
            *params,
        )
        # One org lookup for the whole list — every row here belongs to the
        # caller's own org by construction (org_id = $1 above), so they all share
        # the same enrollment base. Same builder as create_invite: this listing
        # had the IDENTICAL relative-path bug and is fixed by the same change.
        enroll_url, slug = await org_enrollment_base(conn, org_id)

    def _url(token):
        if not token:
            return None
        try:
            return build_enrollment_url(enroll_url, token, slug=slug)
        except EnrollmentUrlError:
            # A misconfigured org must not break the whole listing — omit the
            # link rather than emit a relative one.
            return None

    return [
        InviteResponse(
            id=r["id"],
            email=r["email"],
            full_name=r["full_name"],
            role=r["role"],
            profile_id=r["profile_id"],
            invite_status=r["invite_status"],
            invite_token=r["invite_token"],
            enrollment_url=_url(r["invite_token"]),
            invited_by=r["invited_by"],
            invited_at=str(r["invited_at"]) if r["invited_at"] else None,
            invite_expires_at=str(r["invite_expires_at"]) if r["invite_expires_at"] else None,
        )
        for r in rows
    ]


@router.post("/admin/invites/{invite_id}/revoke", response_model=InviteResponse)
async def revoke_invite_endpoint(request: Request, invite_id: UUID):
    actor_id, org_id = await _require_manage_members(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await revoke_invite(conn, org_id=org_id, invite_id=str(invite_id))
        if row is None:
            raise HTTPException(
                status_code=404,
                detail="No pending invite with that id in this org",
            )
        await write_audit_log(
            conn,
            org_id=org_id,
            action="revoke_invite",
            table_name="users",
            record_id=row["id"],
            new={"invite_status": "revoked", "revoked_by": str(actor_id)},
            actor=actor_id,
        )

    return InviteResponse(
        id=row["id"],
        email=row["email"],
        invite_status=row["invite_status"],
    )


class EmailGateResponse(BaseModel):
    """Operator-facing report of whether invite email can be sent AT ALL.

    Exists so the AWS-side action items this sprint is blocked on can be
    re-checked from the running deployment the moment they are done, without a
    redeploy or a code read. ``sandbox_known=false`` is a real answer, not a
    missing one: this deployment's principal is denied ``ses:GetAccount``, so
    the account's sandbox state genuinely cannot be determined from here, and
    saying "not in sandbox" would be a guess.
    """

    ok: bool
    attempted: bool
    reason: str
    gap: str
    missing_vars: list[str] = []
    error_code: str | None = None
    production_access: bool | None = None
    sandbox_known: bool = False


@router.get("/admin/email/status", response_model=EmailGateResponse)
async def email_gate_status(request: Request):
    """Probe SES with ONE real authenticated call and report the honest state.

    Behind ``manage_members`` — the same permission that mints the invites this
    transport exists to deliver. Returns 200 with ``ok=false`` when sending is
    blocked: a status endpoint that errors when the thing it reports on is
    broken is a status endpoint that cannot report.
    """
    await _require_manage_members(request)
    gate = await run_in_threadpool(email_service.probe)
    return EmailGateResponse(
        ok=gate.ok,
        attempted=gate.attempted,
        reason=gate.reason,
        gap=gate.gap,
        missing_vars=list(gate.missing_vars),
        error_code=gate.error_code,
        production_access=gate.production_access,
        sandbox_known=gate.sandbox_known,
    )
