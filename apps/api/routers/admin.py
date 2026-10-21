"""Admin endpoints: member management and role assignment (Sprint 9).

Gated by the ``manage_members`` permission (DB-backed RBAC). Role changes are
written to the ``user_roles`` join table and recorded in the audit log.

USER MANAGEMENT SPRINT — the lifecycle endpoints added here
-----------------------------------------------------------
    PATCH  /admin/users/{id}              rename (full_name)
    POST   /admin/users/{id}/deactivate   close the account
    POST   /admin/users/{id}/reactivate   re-open it
    DELETE /admin/users/{id}              anonymize (see below)

Before this sprint there was NO endpoint that edited the ``users`` row at all
from an admin surface: ``PUT /admin/users/{id}/role`` writes ``user_roles``, and
``PUT /admin/users/{id}/profile`` (routers/profiles.py) writes only
``profile_id``. ``full_name`` and account state were unreachable.

WHY ``DELETE`` DOES NOT DELETE — measured, not assumed. Live introspection of
the deployed database found **92 foreign-key columns across 69 public tables**
referencing ``users(id)``. **89 of them are ``ON DELETE NO ACTION``**:
``audit_log.user_id``, ``deals.created_by``, ``documents.created_by``,
``entities.created_by``, ``member_investments.user_id``,
``spv_subscriptions.created_by``, ``users.invited_by``, and so on. A row-level
DELETE therefore raises a ForeignKeyViolation for any member who has ever done
anything — one audit_log row is enough.

The remaining **3 are ``ON DELETE CASCADE``** (``deal_interest.user_id``,
``deal_votes.user_id``, ``user_roles.user_id``), which makes the case stronger
rather than weaker: if the NO ACTION constraints were ever relaxed to let a
delete through, those three would silently take a member's votes and expressions
of interest with them. Neither outcome is acceptable for a firm under
recordkeeping obligations.

So ``DELETE`` is implemented as ANONYMIZATION: the row survives so every FK
still resolves, and the PII on it does not. Specifically it clears
``auth0_sub`` (which is what actually severs the login — the identity can no
longer resolve to this row), replaces ``email`` and ``full_name`` with
non-identifying sentinels, drops ``avatar_url`` / ``profile_id`` / ``manager_id``
and any outstanding invite token, revokes every ``user_roles`` and
``user_permission_sets`` grant, and marks the account inactive. It is reported
to the caller as what it is — see ``AdminUserMutation.hard_deleted``, which is
always False.
"""

import json
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from routers.entities import get_org_id
from services.audit import write_audit_log
from services.database import get_pool
from services.org_settings import USER_INACTIVITY_TIMEOUT_DAYS_KEY, get_setting
from services.rbac import (
    ORG_ADMIN_ROLE,
    ensure_org_admin_role,
    is_super_admin,
    load_principal,
    require_permission,
)
from services.users import ensure_user
from services import authz, user_admin

router = APIRouter(tags=["admin"])

# The domain anonymized accounts' emails are moved to. `.invalid` is reserved by
# RFC 2606 and can never be a real, routable address, so a sentinel built on it
# cannot collide with a live one. `users.email` is UNIQUE and NOT NULL, which is
# why the row's own id is embedded — two anonymizations must not collide either.
ANONYMIZED_EMAIL_DOMAIN = "deleted.invalid"
ANONYMIZED_FULL_NAME = "Deleted account"


def anonymized_email(user_id) -> str:
    return f"deleted-{user_id}@{ANONYMIZED_EMAIL_DOMAIN}"


def is_anonymized(email: str | None) -> bool:
    return bool(email) and email.endswith(f"@{ANONYMIZED_EMAIL_DOMAIN}")


class RoleOption(BaseModel):
    id: UUID
    name: str


class AdminUser(BaseModel):
    id: UUID
    email: str | None = None
    full_name: str | None = None
    role: str | None = None
    role_id: UUID | None = None
    created_at: str | None = None
    # SOC Phase A: the additive profile layer (users.profile_id). Separate from
    # `role`/`role_id` above — surfaced so the user-management screen can show a
    # profile selector alongside the untouched role dropdown.
    profile_id: UUID | None = None
    profile_name: str | None = None
    # Multi-tenant Sprint 2 added invite columns to `users`, but this list never
    # selected them, so an invited (pending) account was indistinguishable from
    # an enrolled one — the screen hardcoded every row as "Active". Surfacing it
    # is what makes the invite flow legible on the screen that creates it.
    invite_status: str | None = None
    # users.role — the ACCOUNT role ('member' / 'org_admin' / 'super_admin'),
    # distinct from the granted `role` above (which comes from user_roles).
    account_role: str | None = None
    # ── Account lifecycle (this sprint) ────────────────────────────────────
    is_active: bool = True
    deactivated_at: str | None = None
    last_login_at: str | None = None
    # True once DELETE has anonymized the row. Derived from the sentinel email
    # domain rather than stored, so it cannot drift out of sync with the actual
    # state of the row.
    is_deleted: bool = False
    # AUTHZ1 provisioning lifecycle: 'active' / 'pending' / 'retired' /
    # 'rejected'. A pending row is a sign-in waiting for approval.
    status: str = "active"
    # ── USERMGMT1 — one row per person ─────────────────────────────────────
    # Every granted role (the old list JOINed user_roles and so repeated a
    # person once per role). `role` / `role_id` above stay as the FIRST role,
    # by name, for callers written against the old shape.
    roles: list[RoleOption] = []
    # How this person signs in: one entry per login that resolves to them
    # (primary auth0_sub, then user_identities). See services.user_admin.
    sign_in: list["SignInMethod"] = []
    never_signed_in: bool = False
    email_is_placeholder: bool = False
    is_test: bool = False
    # An address a PENDING person typed on the Access pending page.
    # Unverified — shown, never used for linking.
    contact_email: str | None = None
    invited_at: str | None = None
    invite_expires_at: str | None = None
    # Pending rows only: an admitted user this sign-in most likely belongs to.
    likely_match: "LikelyMatch | None" = None
    # What THIS caller may do to THIS row, decided by the server (Rule 1 /
    # permission envelope): the screen renders a control only when its key is
    # here. Empty for a view-only caller — never a client-side default.
    actions: list[str] = []
    # Why "Send password reset" would be refused for this row, or None.
    reset_password_refusal: str | None = None


class SignInMethod(BaseModel):
    tenant: str
    method: str
    is_database: bool = False
    linked_via: str | None = None


class LikelyMatch(BaseModel):
    id: UUID
    email: str | None = None
    full_name: str | None = None


AdminUser.model_rebuild()


class RoleAssignRequest(BaseModel):
    role_id: UUID


class UserUpdateRequest(BaseModel):
    """Fields an admin may change on another user's ``users`` row.

    `extra="forbid"` makes the standing rule mechanical rather than a matter of
    remembering: a body carrying `org_id` — or `role`, or `is_active`, which
    have their own audited endpoints — is rejected as a 422 instead of being
    quietly dropped. The org is resolved from the caller's request context.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None


class AdminUserMutation(BaseModel):
    """Result of a lifecycle action, stated in terms of what actually happened."""

    id: UUID
    email: str | None = None
    full_name: str | None = None
    is_active: bool
    deactivated_at: str | None = None
    # Always False. Present so the caller is told, in the response itself, that
    # DELETE anonymized rather than removed the row — see the module docstring.
    hard_deleted: bool = False
    anonymized: bool = False


# AUTHZ1: who may see and decide pending sign-ins in People & Access — a
# member manager, or the org's own admin (manage_org_settings). Super Admin
# passes first inside ``authz.require_any_permission``.
APPROVE_PERMISSIONS = ("manage_members", "manage_org_settings")


class PendingApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: UUID


async def _require_people_access(request: Request) -> tuple[str, str]:
    principal = authz.require_any_permission(request, APPROVE_PERMISSIONS)
    return principal.user_id, get_org_id(request)


async def _require_manage_members(request: Request) -> tuple[str, str]:
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
    await require_permission(pool, actor_id, org_id, "manage_members")
    return actor_id, org_id


async def _resolve_target(conn, actor_id, org_id: str, user_id: UUID):
    """Return the target ``users`` row, or raise 404/403.

    THE ORG RULE, in one place so all four lifecycle endpoints share it: the
    target must live in the CALLER'S OWN org — the org resolved from the request
    context, never from a body — unless the caller is a Super Admin, who by the
    platform-wide escape-hatch convention may act across orgs. A cross-org target
    is a 404, not a 403: telling one tenant's admin that a given id exists
    somewhere else is itself a disclosure.
    """
    principal = await load_principal(conn, actor_id)
    caller_is_super = is_super_admin(principal or {})

    row = await conn.fetchrow(
        "SELECT id, org_id, email, full_name, role, is_active, auth0_sub, status, "
        "invite_status, invited_at "
        "FROM users WHERE id = $1",
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not caller_is_super and str(row["org_id"]) != str(org_id):
        raise HTTPException(status_code=404, detail="User not found")
    return row, caller_is_super


def _forbid_self(actor_id, target, verb: str) -> None:
    """An admin may not deactivate or delete their own account.

    Not paternalism — it is the one mistake with no in-app recovery: the
    active-account gate in ``main.rls_context_middleware`` rejects every
    subsequent request from that identity, including the one that would undo it,
    so a lone org admin who does this locks the whole tenant out of member
    management until someone runs SQL.
    """
    if str(target["id"]) == str(actor_id):
        raise HTTPException(
            status_code=400,
            detail=f"You cannot {verb} your own account.",
        )


def _forbid_staff_target(target, caller_is_super: bool, verb: str) -> None:
    """Only a Super Admin may deactivate or delete a Super Admin.

    Without this an org_admin holding ``manage_members`` could switch off
    platform staff, which inverts the privilege ordering.
    """
    if target["role"] == "super_admin" and not caller_is_super:
        raise HTTPException(
            status_code=403,
            detail=f"Only a Super Admin may {verb} a Super Admin account.",
        )


@router.get("/admin/roles", response_model=list[RoleOption])
async def list_roles(request: Request):
    """The roles an admin may assign — scoped to THEIR OWN org.

    THE BUG THIS FIXES (orgadminwrites.structural): this previously read
    ``SELECT id, name FROM roles`` with no ``org_id`` filter at all — ``roles``
    is a per-org table (``(org_id, name)`` UNIQUE, confirmed live: every row
    today happens to belong to 2nd Act, which is why the leak was not yet
    visible), so this returned every org's role catalog to every org's admin,
    and ``PUT /admin/users/{id}/role`` below trusted a bare ``role_id`` with no
    matching org check — an admin who saw (or guessed) another org's role id
    could grant it to one of their own users.

    Also lazily ensures THIS org has its own org_admin role — additive,
    idempotent (``ensure_org_admin_role``), so an org with zero org_admin
    holders today (confirmed live: Hollisworks) gets one created on first
    view of this screen instead of needing a second manual migration before
    "Organization Admin" is even selectable.

    AUTHZ1: readable by an org admin as well (they pick the role when
    approving a pending sign-in).
    """
    _, org_id = await _require_people_access(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await ensure_org_admin_role(conn, org_id)
        rows = await conn.fetch(
            "SELECT id, name FROM roles WHERE org_id = $1 ORDER BY name", org_id
        )
    return [RoleOption(**dict(r)) for r in rows]


# USERMGMT1 list filters. `status` is the screen's vocabulary, not the raw
# column: "deactivated" is is_active=false, and "retired" includes anonymized
# rows (DELETE retires as well as anonymizes).
STATUS_FILTERS = ("active", "pending", "deactivated", "retired", "rejected")

# Action keys published per row in AdminUser.actions.
ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
ACTION_LINK = "link"
ACTION_RESET_PASSWORD = "reset_password"
ACTION_RESEND_INVITE = "resend_invite"
ACTION_EDIT_EMAIL = "edit_email"
ACTION_EDIT = "edit"
ACTION_RETIRE = "retire"

_RETIRED_SQL = f"(u.status = 'retired' OR u.email LIKE '%@{ANONYMIZED_EMAIL_DOMAIN}')"


def _row_actions(row: dict, *, caller, can_manage: bool, never_signed_in: bool,
                 placeholder: bool, anonymized: bool) -> list[str]:
    """Server-decided controls for one row. Mirrors the endpoint gates below,
    so a button is shown only where the identical request would be allowed."""
    acts: list[str] = []
    status = row["status"]
    if status == authz.STATUS_PENDING:
        acts += [ACTION_APPROVE, ACTION_REJECT]
    if not can_manage:
        return acts
    staff_blocked = row["role"] == "super_admin" and not caller.is_super_admin
    if staff_blocked:
        return acts
    is_self = str(row["id"]) == str(caller.user_id)
    if status == authz.STATUS_PENDING and row["auth0_sub"]:
        acts.append(ACTION_LINK)
    if anonymized:
        return acts
    acts.append(ACTION_EDIT)
    if status not in (authz.STATUS_RETIRED, authz.STATUS_REJECTED):
        acts.append(ACTION_RESET_PASSWORD)
        if never_signed_in and row["invited_at"] is not None and not placeholder:
            acts.append(ACTION_RESEND_INVITE)
    if placeholder or never_signed_in:
        acts.append(ACTION_EDIT_EMAIL)
    if not is_self:
        acts.append(ACTION_RETIRE)
    return acts


@router.get("/admin/users", response_model=list[AdminUser])
async def list_users(
    request: Request,
    search: str | None = None,
    role: str | None = None,
    status: str | None = None,
    include_test: bool = False,
    include_retired: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """One row per person (USERMGMT1).

    THE BUG THIS FIXES: the list was ``users LEFT JOIN user_roles LEFT JOIN
    roles``, so a person holding N roles came back as N rows (Joe's culmina.io
    account twice, gmail three times) — and LIMIT/OFFSET paged over the
    duplicated rows. Roles are now an aggregated array and the role filter is
    an EXISTS, so the row count is the people count.

    Test fixtures (``users.is_test``) and retired accounts are hidden unless
    asked for (``include_test`` / ``include_retired``, or ``status=retired``).
    """
    caller = authz.require_any_permission(request, APPROVE_PERMISSIONS)
    org_id = get_org_id(request)
    can_manage = caller.is_super_admin or "manage_members" in caller.permissions

    conditions = ["u.org_id = $1"]
    params: list = [org_id]
    if status:
        if status not in STATUS_FILTERS:
            raise HTTPException(
                status_code=400,
                detail=f"status must be one of {', '.join(STATUS_FILTERS)}",
            )
        if status == "deactivated":
            conditions.append(f"u.is_active = false AND NOT {_RETIRED_SQL}")
        elif status == "retired":
            conditions.append(_RETIRED_SQL)
        elif status == "active":
            conditions.append("u.status = 'active' AND u.is_active")
        else:
            params.append(status)
            conditions.append(f"u.status = ${len(params)}")
    if status != "retired" and not include_retired:
        conditions.append(f"NOT {_RETIRED_SQL}")
    if not include_test:
        conditions.append("NOT u.is_test")
    if search:
        params.append(f"%{search}%")
        conditions.append(
            f"(u.full_name ILIKE ${len(params)} OR u.email ILIKE ${len(params)} "
            f"OR u.contact_email ILIKE ${len(params)})"
        )
    if role:
        params.append(role)
        conditions.append(
            f"EXISTS (SELECT 1 FROM user_roles ur2 JOIN roles r2 ON r2.id = ur2.role_id "
            f"WHERE ur2.user_id = u.id AND r2.name = ${len(params)})"
        )

    params.append(limit)
    limit_pos = len(params)
    params.append(offset)
    offset_pos = len(params)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT u.id, u.email, u.full_name, u.created_at, u.org_id, u.auth0_sub,
                   u.profile_id, p.name AS profile_name,
                   u.invite_status, u.invited_at, u.invite_expires_at,
                   u.role, u.is_active, u.deactivated_at, u.last_login_at, u.status,
                   u.is_test, u.contact_email,
                   COALESCE((
                       SELECT json_agg(json_build_object('id', r.id, 'name', r.name) ORDER BY r.name)
                       FROM user_roles ur JOIN roles r ON r.id = ur.role_id
                       WHERE ur.user_id = u.id
                   ), '[]'::json) AS roles,
                   COALESCE((
                       SELECT json_agg(json_build_object(
                                  'auth0_sub', i.auth0_sub, 'issuer', i.issuer,
                                  'linked_via', i.linked_via) ORDER BY i.created_at)
                       FROM user_identities i WHERE i.user_id = u.id
                   ), '[]'::json) AS identities
            FROM users u
            LEFT JOIN profiles p ON p.id = u.profile_id
            WHERE {' AND '.join(conditions)}
            ORDER BY u.full_name NULLS LAST, u.email
            LIMIT ${limit_pos} OFFSET ${offset_pos}
            """,
            *params,
        )
        candidates = []
        if any(r["status"] == authz.STATUS_PENDING for r in rows):
            candidates = [dict(c) for c in await conn.fetch(
                "SELECT id, email, full_name FROM users WHERE org_id = $1 "
                "AND status = 'active' AND is_active ORDER BY email",
                org_id,
            )]

    out = []
    for r in rows:
        roles = json.loads(r["roles"]) if isinstance(r["roles"], str) else (r["roles"] or [])
        identities = (
            json.loads(r["identities"]) if isinstance(r["identities"], str) else (r["identities"] or [])
        )
        sign_in = user_admin.sign_in_methods(
            org_id=r["org_id"], auth0_sub=r["auth0_sub"], identities=identities
        )
        never = not sign_in
        placeholder = user_admin.is_placeholder_email(r["email"])
        anonymized = is_anonymized(r["email"])
        _, refusal = user_admin.reset_plan(
            email=r["email"], status=r["status"], is_anonymized=anonymized, sign_in=sign_in
        )
        match = None
        if r["status"] == authz.STATUS_PENDING:
            match = user_admin.likely_match(dict(r), candidates)
        first = roles[0] if roles else None
        out.append(AdminUser(
            id=r["id"],
            email=r["email"],
            full_name=r["full_name"],
            role=first["name"] if first else None,
            role_id=first["id"] if first else None,
            roles=[RoleOption(**x) for x in roles],
            created_at=str(r["created_at"]) if r["created_at"] else None,
            profile_id=r["profile_id"],
            profile_name=r["profile_name"],
            invite_status=r["invite_status"],
            account_role=r["role"],
            is_active=r["is_active"],
            deactivated_at=str(r["deactivated_at"]) if r["deactivated_at"] else None,
            last_login_at=str(r["last_login_at"]) if r["last_login_at"] else None,
            is_deleted=anonymized,
            status=r["status"],
            sign_in=[SignInMethod(**s) for s in sign_in],
            never_signed_in=never,
            email_is_placeholder=placeholder,
            is_test=bool(r["is_test"]),
            contact_email=r["contact_email"],
            invited_at=str(r["invited_at"]) if r["invited_at"] else None,
            invite_expires_at=str(r["invite_expires_at"]) if r["invite_expires_at"] else None,
            likely_match=LikelyMatch(**match) if match else None,
            actions=_row_actions(
                dict(r), caller=caller, can_manage=can_manage, never_signed_in=never,
                placeholder=placeholder, anonymized=anonymized,
            ),
            reset_password_refusal=refusal,
        ))
    return out


@router.get("/admin/users/settings")
async def user_management_settings(request: Request):
    """The org-configurable knobs the user-management screen needs.

    Read through the ordinary settings resolver, so an org that has configured
    neither key gets the platform defaults and the screen never has to hardcode
    a number of its own.
    """
    _, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        from services.invites import resolve_invite_ttl_days

        invite_expiry_days = await resolve_invite_ttl_days(conn, org_id)
        inactivity = await get_setting(
            conn, org_id, USER_INACTIVITY_TIMEOUT_DAYS_KEY
        )
    return {
        "invite_expiry_days": invite_expiry_days,
        "user_inactivity_timeout_days": inactivity,
    }


@router.put("/admin/users/{user_id}/role", response_model=AdminUser)
async def assign_role(request: Request, user_id: UUID, body: RoleAssignRequest):
    """Assign an RBAC role to a user in the caller's own org.

    THE GAP THIS CLOSES (orgadminwrites.structural): this endpoint always
    wrote the ``user_roles`` grant but never touched ``users.role`` — so
    promoting someone to org_admin here left the account role string
    unchanged (drift in the OPPOSITE direction from the invite gap), and
    demoting someone away from org_admin left the stale 'org_admin' string
    behind with the grant already gone — a privilege-ratchet-shaped bug even
    though the actual permission WAS correctly revoked. The role string and
    the grant now move together, in both directions, guarded to never rewrite
    a super_admin's string (that axis is untouched by RBAC — see
    ``services.rbac.is_super_admin``).
    """
    actor_id, org_id = await _require_manage_members(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                "SELECT id, email, full_name, role FROM users WHERE id = $1 AND org_id = $2",
                user_id, org_id,
            )
            if target is None:
                raise HTTPException(status_code=404, detail="User not found")

            # Scoped to the CALLER'S OWN org — a bare `id = $1` here (the
            # pre-fix behaviour) trusted a role_id from ANY org, since
            # GET /admin/roles used to leak every org's role catalog.
            role = await conn.fetchrow(
                "SELECT id, name FROM roles WHERE id = $1 AND org_id = $2",
                body.role_id, org_id,
            )
            if role is None:
                raise HTTPException(status_code=400, detail="Unknown role")

            await conn.execute("DELETE FROM user_roles WHERE user_id = $1", user_id)
            await conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)",
                user_id, body.role_id,
            )

            if role["name"] == ORG_ADMIN_ROLE:
                await conn.execute(
                    "UPDATE users SET role = $2, updated_at = now() "
                    "WHERE id = $1 AND role <> 'super_admin'",
                    user_id, ORG_ADMIN_ROLE,
                )
            elif target["role"] == ORG_ADMIN_ROLE:
                await conn.execute(
                    "UPDATE users SET role = 'member', updated_at = now() "
                    "WHERE id = $1 AND role = $2",
                    user_id, ORG_ADMIN_ROLE,
                )

        await write_audit_log(
            conn,
            org_id=org_id,
            action="assign_role",
            table_name="user_roles",
            record_id=user_id,
            new={"user_id": str(user_id), "role_id": str(body.role_id),
                 "role": role["name"], "assigned_by": str(actor_id)},
            actor=actor_id,
        )

    return AdminUser(
        id=target["id"],
        email=target["email"],
        full_name=target["full_name"],
        role=role["name"],
        role_id=role["id"],
    )


# ──────────────────────────────────────────────────────────────────────────
# AUTHZ1 — pending sign-ins: approve (grant a role, admit) or reject
# ──────────────────────────────────────────────────────────────────────────

async def _pending_target(conn, org_id: str, user_id: UUID):
    row = await conn.fetchrow(
        "SELECT id, email, full_name, role, status FROM users WHERE id = $1 AND org_id = $2",
        user_id, org_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    if row["status"] != authz.STATUS_PENDING:
        raise HTTPException(status_code=409, detail=f"User is not pending (status: {row['status']})")
    return row


async def _close_pending_todos(conn, org_id: str, user_id) -> None:
    from services.workflow_todos import TODO_SOURCE_PENDING_USER

    await conn.execute(
        """
        UPDATE member_todos SET status = 'done', updated_at = now()
        WHERE org_id = $1 AND source = $2 AND related_type = 'users'
          AND related_id = $3 AND status = 'open'
        """,
        org_id, TODO_SOURCE_PENDING_USER, user_id,
    )


@router.post("/admin/users/{user_id}/approve", response_model=AdminUser)
async def approve_pending_user(request: Request, user_id: UUID, body: PendingApproval):
    """Admit a pending sign-in by granting it ONE role of this org.

    The role grant and ``status='active'`` land in one transaction, so a
    pending user is never admitted with no role, and never holds a role while
    still refused. An ``org_admin`` grant keeps ``users.role`` in lockstep via
    the shared ``services.rbac`` helpers (CLAUDE.md).
    """
    actor_id, org_id = await _require_people_access(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            target = await _pending_target(conn, org_id, user_id)
            role = await conn.fetchrow(
                "SELECT id, name FROM roles WHERE id = $1 AND org_id = $2",
                body.role_id, org_id,
            )
            if role is None:
                raise HTTPException(status_code=400, detail="Unknown role")
            if role["name"] == ORG_ADMIN_ROLE:
                from services.rbac import grant_org_admin

                await grant_org_admin(conn, user_id, org_id)
                await conn.execute(
                    "UPDATE users SET role = $2 WHERE id = $1 AND role <> 'super_admin'",
                    user_id, ORG_ADMIN_ROLE,
                )
            else:
                await conn.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    user_id, role["id"],
                )
            await conn.execute(
                "UPDATE users SET status = 'active', updated_at = now() WHERE id = $1",
                user_id,
            )
            await _close_pending_todos(conn, org_id, user_id)
        await write_audit_log(
            conn,
            org_id=org_id,
            action="approve_pending_user",
            table_name="users",
            record_id=user_id,
            new={"status": "active", "role": role["name"], "approved_by": str(actor_id)},
            actor=actor_id,
        )
    return AdminUser(
        id=target["id"], email=target["email"], full_name=target["full_name"],
        role=role["name"], role_id=role["id"], status="active",
    )


@router.post("/admin/users/{user_id}/reject", response_model=AdminUser)
async def reject_pending_user(request: Request, user_id: UUID):
    """Decline a pending sign-in. The row is kept (status 'rejected', no
    roles) so the same login stays refused and the decision is auditable."""
    actor_id, org_id = await _require_people_access(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            target = await _pending_target(conn, org_id, user_id)
            await conn.execute(
                "UPDATE users SET status = 'rejected', updated_at = now() WHERE id = $1",
                user_id,
            )
            await _close_pending_todos(conn, org_id, user_id)
        await write_audit_log(
            conn,
            org_id=org_id,
            action="reject_pending_user",
            table_name="users",
            record_id=user_id,
            new={"status": "rejected", "rejected_by": str(actor_id)},
            actor=actor_id,
        )
    return AdminUser(
        id=target["id"], email=target["email"], full_name=target["full_name"],
        status="rejected",
    )


# ──────────────────────────────────────────────────────────────────────────
# USERMGMT1 — password reset, resend invite, link a pending login, edit email
# ──────────────────────────────────────────────────────────────────────────
# Every one is behind the same gate as the other user edits
# (``_require_manage_members``), the same org rule (``_resolve_target``: a
# cross-org target is a 404 unless the caller is Super Admin), and the same
# staff rule (``_forbid_staff_target``: only a Super Admin acts on a Super
# Admin). Every refusal happens before any write; every success is audited.


class LinkPendingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_user_id: UUID


class EmailUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str


class ResendInviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # False → no SES attempt; the link is returned for manual sharing and the
    # delivery record says "skipped".
    send_email: bool = True


async def _identities(conn, user_id) -> list[dict]:
    return [dict(r) for r in await conn.fetch(
        "SELECT auth0_sub, issuer, linked_via FROM user_identities WHERE user_id = $1 "
        "ORDER BY created_at",
        user_id,
    )]


async def _sign_in(conn, target) -> list[dict]:
    return user_admin.sign_in_methods(
        org_id=target["org_id"], auth0_sub=target["auth0_sub"],
        identities=await _identities(conn, target["id"]),
    )


async def email_taken_anywhere(conn, email: str, *, exclude_id=None) -> bool:
    """``users.email`` is UNIQUE platform-wide, so uniqueness is checked across
    every org — case-insensitively, which the DB constraint is not. The read
    needs the super-admin RLS carve-out; it is ``set_config(..., true)``
    (transaction-local) and restored to its prior value immediately, so it
    never outlives this one existence check."""
    prior = await conn.fetchval("SELECT current_setting('app.is_super_admin', true)")
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
    try:
        return bool(await conn.fetchval(
            "SELECT 1 FROM users WHERE lower(email) = lower($1) "
            "AND ($2::uuid IS NULL OR id <> $2::uuid) LIMIT 1",
            email, exclude_id,
        ))
    finally:
        await conn.execute(
            "SELECT set_config('app.is_super_admin', $1, true)", prior or "false"
        )


@router.post("/admin/users/{user_id}/password-reset")
async def send_password_reset(request: Request, user_id: UUID):
    """Ask Auth0 to email this person a password-reset link.

    Uses the tenant's PUBLIC ``/dbconnections/change_password`` endpoint — no
    Management API grant exists on either tenant. Allowed only for a real email
    and a database (email & password) login; a Google login, a placeholder
    email and a never-signed-in account are refused with the sentence the
    screen shows. Auth0 answers 200 whether or not the address matched a
    login, so the response says "requested", never "delivered".
    """
    actor_id, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        target, caller_is_super = await _resolve_target(conn, actor_id, org_id, user_id)
        _forbid_staff_target(target, caller_is_super, "reset the password of")
        sign_in = await _sign_in(conn, target)
        login, refusal = user_admin.reset_plan(
            email=target["email"], status=target["status"],
            is_anonymized=is_anonymized(target["email"]), sign_in=sign_in,
        )
        if refusal:
            raise HTTPException(status_code=409, detail=refusal)
        tenant = login["tenant"]
        try:
            url, body = user_admin.reset_request(tenant, target["email"])
        except user_admin.ResetNotConfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        try:
            auth0_status = await user_admin.post_change_password(url, body)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"Could not reach the {tenant} sign-in service: {type(exc).__name__}",
            ) from exc
        if auth0_status >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"The {tenant} sign-in service refused the reset request (HTTP {auth0_status}).",
            )
        await write_audit_log(
            conn,
            org_id=str(target["org_id"]),
            action="send_password_reset",
            table_name="users",
            record_id=user_id,
            new={"tenant": tenant, "connection": body["connection"],
                 "auth0_status": auth0_status, "actor": str(actor_id)},
            actor=actor_id,
        )
    return {
        "id": str(user_id),
        "requested": True,
        "tenant": tenant,
        "connection": body["connection"],
        "message": (
            f"Reset requested. If {target['email']} has an email & password login on the "
            f"{tenant} sign-in, Auth0 is sending it a reset link."
        ),
    }


@router.post("/admin/users/{user_id}/resend-invite")
async def resend_invite(request: Request, user_id: UUID, body: ResendInviteRequest | None = None):
    """Issue a fresh enrollment link for an invited person who never signed in.

    Rotates ``invite_token`` (the old link stops working), re-opens the invite
    and resets its expiry to the org's ``invite.expiry_days``. Delivery goes
    through the existing invite mailer and is reported honestly — today SES is
    blocked, so the link is shown to copy.
    """
    from services.invites import (
        InviteDelivery,
        enrollment_url_for_org,
        generate_invite_token,
        resolve_invite_ttl_days,
        send_invite_email,
    )
    from services import email as email_service

    send_email = True if body is None else body.send_email
    actor_id, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        target, caller_is_super = await _resolve_target(conn, actor_id, org_id, user_id)
        _forbid_staff_target(target, caller_is_super, "re-invite")
        if is_anonymized(target["email"]) or target["status"] in (
            authz.STATUS_RETIRED, authz.STATUS_REJECTED
        ):
            raise HTTPException(status_code=409, detail=user_admin.REFUSE_RETIRED)
        if await _sign_in(conn, target):
            raise HTTPException(
                status_code=409,
                detail="This person has already signed in, so there is no invitation to resend.",
            )
        if target["invited_at"] is None:
            raise HTTPException(
                status_code=409, detail="This account was not created by an invitation."
            )
        if user_admin.is_placeholder_email(target["email"]):
            raise HTTPException(status_code=409, detail=user_admin.REFUSE_NO_EMAIL)

        target_org = str(target["org_id"])
        ttl_days = await resolve_invite_ttl_days(conn, target_org)
        token = generate_invite_token()
        row = await conn.fetchrow(
            """
            UPDATE users
            SET invite_token = $2, invite_status = 'pending', invited_by = $3,
                invited_at = now(), invite_expires_at = now() + make_interval(days => $4),
                updated_at = now()
            WHERE id = $1 AND auth0_sub IS NULL
            RETURNING invite_expires_at
            """,
            user_id, token, UUID(str(actor_id)), ttl_days,
        )
        if row is None:
            raise HTTPException(status_code=409, detail="The account changed; reload and try again.")
        url = await enrollment_url_for_org(conn, target_org, token)
        if send_email:
            delivery = await send_invite_email(
                conn, org_id=target_org, email=target["email"], full_name=target["full_name"],
                enrollment_url=url, expiry_days=ttl_days, expires_at=row["invite_expires_at"],
            )
        else:
            delivery = InviteDelivery(
                status="skipped", manual_share_required=True,
                reason="Caller requested no email; share the enrollment link manually.",
                gap="not_requested",
                recipient_redacted=email_service.redact_email(target["email"]),
            )
        # The token is a credential: never written to the audit trail.
        await write_audit_log(
            conn,
            org_id=target_org,
            action="resend_invite",
            table_name="users",
            record_id=user_id,
            old={"invite_status": target["invite_status"]},
            new={"invite_status": "pending", "token_rotated": True,
                 "email_delivery": delivery.status, "actor": str(actor_id)},
            actor=actor_id,
        )
    return {
        "id": str(user_id),
        "email": target["email"],
        "invite_status": "pending",
        "enrollment_url": url,
        "invite_expires_at": row["invite_expires_at"].isoformat(),
        "email_delivery": delivery.as_dict(),
    }


@router.post("/admin/users/{user_id}/link", response_model=AdminUser)
async def link_pending_login(request: Request, user_id: UUID, body: LinkPendingRequest):
    """Attach a pending sign-in to the existing person it belongs to.

    The pending row's ``auth0_sub`` moves to the target — onto
    ``target.auth0_sub`` when that is empty (an invited person who signed in
    without the invite link), otherwise as a ``user_identities`` row with
    ``linked_via='operator'``. The pending row is RETIRED (never deleted; its
    references keep resolving), any identities it held move with it, and its
    pending to-dos close. All in one transaction. On the login's next request
    ``ensure_user`` resolves it to the target.
    """
    actor_id, org_id = await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        pending, caller_is_super = await _resolve_target(conn, actor_id, org_id, user_id)
        if pending["status"] != authz.STATUS_PENDING:
            raise HTTPException(
                status_code=409, detail=f"User is not pending (status: {pending['status']})"
            )
        sub = pending["auth0_sub"]
        if not sub:
            raise HTTPException(status_code=409, detail="This pending row has no sign-in to move.")
        if body.target_user_id == pending["id"]:
            raise HTTPException(status_code=400, detail="Choose a different user to link to.")
        target = await conn.fetchrow(
            "SELECT id, org_id, email, full_name, role, status, is_active, auth0_sub "
            "FROM users WHERE id = $1",
            body.target_user_id,
        )
        if target is None:
            raise HTTPException(status_code=404, detail="Link target not found")
        if str(target["org_id"]) != str(pending["org_id"]):
            raise HTTPException(
                status_code=400,
                detail="A sign-in can only be linked to a user in the same organization.",
            )
        if (target["status"] != authz.STATUS_ACTIVE or not target["is_active"]
                or is_anonymized(target["email"])):
            raise HTTPException(status_code=409, detail="The link target must be an active user.")
        _forbid_staff_target(target, caller_is_super, "link a sign-in to")

        target_org = str(target["org_id"])
        async with conn.transaction():
            await conn.execute(
                "UPDATE users SET auth0_sub = NULL, status = 'retired', updated_at = now() "
                "WHERE id = $1 AND status = 'pending'",
                pending["id"],
            )
            await conn.execute(
                "UPDATE user_identities SET user_id = $2 WHERE user_id = $1",
                pending["id"], target["id"],
            )
            if target["auth0_sub"] is None:
                claimed = await conn.fetchval(
                    """
                    UPDATE users
                    SET auth0_sub = $2,
                        invite_status = CASE WHEN invite_status = 'pending'
                                             THEN 'accepted' ELSE invite_status END,
                        updated_at = now()
                    WHERE id = $1 AND auth0_sub IS NULL
                    RETURNING id
                    """,
                    target["id"], sub,
                )
                if claimed is None:
                    raise HTTPException(status_code=409, detail="The target changed; reload and try again.")
                linked_as = "primary"
            else:
                issuer = user_admin.issuer_for_tenant(user_admin.tenant_for_org(target_org))
                ident_email = None if user_admin.is_placeholder_email(pending["email"]) else pending["email"]
                await conn.execute(
                    """
                    INSERT INTO user_identities (user_id, org_id, auth0_sub, issuer, email, linked_via)
                    VALUES ($1, $2, $3, $4, $5, 'operator')
                    """,
                    target["id"], target_org, sub, issuer, ident_email,
                )
                linked_as = "operator"
            await _close_pending_todos(conn, target_org, pending["id"])
        for record_id, new in (
            (pending["id"], {"status": "retired", "linked_to": str(target["id"]),
                             "linked_as": linked_as, "actor": str(actor_id)}),
            (target["id"], {"linked_login_from": str(pending["id"]),
                            "linked_as": linked_as, "actor": str(actor_id)}),
        ):
            await write_audit_log(
                conn, org_id=target_org, action="link_pending_login", table_name="users",
                record_id=record_id, old={"status": "pending"} if record_id == pending["id"] else None,
                new=new, actor=actor_id,
            )
    return AdminUser(
        id=target["id"], email=target["email"], full_name=target["full_name"],
        account_role=target["role"], status=target["status"],
    )


@router.patch("/admin/users/{user_id}/email", response_model=AdminUserMutation)
async def update_user_email(request: Request, user_id: UUID, body: EmailUpdateRequest):
    """Set the email of an account that has a placeholder or never signed in.

    A signed-in account's email comes from its login and is refused here.
    Unique case-insensitively across the platform (``users.email`` is UNIQUE
    platform-wide). This does NOT change the Auth0 login's email — it changes
    the address the platform shows, invites and resets to.
    """
    actor_id, org_id = await _require_manage_members(request)
    email = (body.email or "").strip().lower()
    if not user_admin.valid_email(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    pool = await get_pool()
    async with pool.acquire() as conn:
        target, caller_is_super = await _resolve_target(conn, actor_id, org_id, user_id)
        _forbid_staff_target(target, caller_is_super, "change the email of")
        if is_anonymized(target["email"]):
            raise HTTPException(status_code=409, detail=user_admin.REFUSE_RETIRED)
        never = not await _sign_in(conn, target)
        if not (user_admin.is_placeholder_email(target["email"]) or never):
            raise HTTPException(
                status_code=409,
                detail="This account signs in with a real email address, which can't be changed here.",
            )
        if await email_taken_anywhere(conn, email, exclude_id=user_id):
            raise HTTPException(status_code=409, detail="Another account already uses that email address.")
        try:
            row = await conn.fetchrow(
                "UPDATE users SET email = $2, updated_at = now() WHERE id = $1 "
                "RETURNING id, email, full_name, is_active, deactivated_at",
                user_id, email,
            )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(
                status_code=409, detail="Another account already uses that email address."
            ) from exc
        await write_audit_log(
            conn,
            org_id=str(target["org_id"]),
            action="update_user_email",
            table_name="users",
            record_id=user_id,
            old={"email": target["email"]},
            new={"email": email, "actor": str(actor_id)},
            actor=actor_id,
        )
    return AdminUserMutation(
        id=row["id"], email=row["email"], full_name=row["full_name"],
        is_active=row["is_active"],
        deactivated_at=str(row["deactivated_at"]) if row["deactivated_at"] else None,
    )


# ──────────────────────────────────────────────────────────────────────────
# Account lifecycle — edit / deactivate / reactivate / anonymize
# ──────────────────────────────────────────────────────────────────────────


@router.patch("/admin/users/{user_id}", response_model=AdminUserMutation)
async def update_user(request: Request, user_id: UUID, body: UserUpdateRequest):
    """Edit an admin-editable field on another user's row. Today: ``full_name``.

    Scope is deliberately narrow. ``email`` is NOT editable here: it is the
    UNIQUE natural key an invite is issued against and what identifies the
    account, so changing it is an identity change rather than a correction and
    needs its own flow. ``role`` and ``is_active`` have their own audited
    endpoints.
    """
    actor_id, org_id = await _require_manage_members(request)

    if "full_name" not in body.model_fields_set:
        raise HTTPException(status_code=400, detail="Nothing to update")

    full_name = (body.full_name or "").strip()
    if not full_name:
        raise HTTPException(status_code=400, detail="full_name cannot be empty")

    pool = await get_pool()
    async with pool.acquire() as conn:
        target, _ = await _resolve_target(conn, actor_id, org_id, user_id)

        row = await conn.fetchrow(
            "UPDATE users SET full_name = $2, updated_at = now() "
            "WHERE id = $1 RETURNING id, email, full_name, is_active, deactivated_at",
            user_id, full_name,
        )
        await write_audit_log(
            conn,
            org_id=str(target["org_id"]),
            action="update_user",
            table_name="users",
            record_id=user_id,
            old={"full_name": target["full_name"]},
            new={"full_name": full_name, "actor": str(actor_id)},
            actor=actor_id,
        )

    return AdminUserMutation(
        id=row["id"],
        email=row["email"],
        full_name=row["full_name"],
        is_active=row["is_active"],
        deactivated_at=str(row["deactivated_at"]) if row["deactivated_at"] else None,
    )


@router.post("/admin/users/{user_id}/deactivate", response_model=AdminUserMutation)
async def deactivate_user(request: Request, user_id: UUID):
    """Close an account: ``is_active=false``, stamped with who and when.

    The flag is not decorative. ``main.rls_context_middleware`` reads
    ``users.is_active`` on every authenticated request and returns 403 before any
    route handler runs, so the effect is immediate for a session already holding
    a valid Auth0 token — which is the only kind of revocation that matters,
    since we cannot un-issue a token Auth0 has already minted.
    """
    actor_id, org_id = await _require_manage_members(request)

    pool = await get_pool()
    async with pool.acquire() as conn:
        target, caller_is_super = await _resolve_target(conn, actor_id, org_id, user_id)
        _forbid_self(actor_id, target, "deactivate")
        _forbid_staff_target(target, caller_is_super, "deactivate")

        row = await conn.fetchrow(
            """
            UPDATE users
            SET is_active = false,
                deactivated_at = now(),
                deactivated_by = $2,
                updated_at = now()
            WHERE id = $1
            RETURNING id, email, full_name, is_active, deactivated_at
            """,
            user_id, actor_id,
        )
        await write_audit_log(
            conn,
            org_id=str(target["org_id"]),
            action="deactivate_user",
            table_name="users",
            record_id=user_id,
            old={"is_active": target["is_active"]},
            new={"is_active": False, "deactivated_by": str(actor_id)},
            actor=actor_id,
        )

    return AdminUserMutation(
        id=row["id"],
        email=row["email"],
        full_name=row["full_name"],
        is_active=row["is_active"],
        deactivated_at=str(row["deactivated_at"]) if row["deactivated_at"] else None,
    )


@router.post("/admin/users/{user_id}/reactivate", response_model=AdminUserMutation)
async def reactivate_user(request: Request, user_id: UUID):
    """Re-open a deactivated account, clearing the deactivation stamps.

    Refuses on an anonymized row: DELETE cleared ``auth0_sub`` and the PII, so
    flipping the flag back would produce an active account nobody can sign into
    and whose owner is no longer recorded. Saying so is more useful than
    succeeding vacuously.
    """
    actor_id, org_id = await _require_manage_members(request)

    pool = await get_pool()
    async with pool.acquire() as conn:
        target, _ = await _resolve_target(conn, actor_id, org_id, user_id)
        if is_anonymized(target["email"]):
            raise HTTPException(
                status_code=409,
                detail="This account was anonymized and cannot be reactivated.",
            )

        row = await conn.fetchrow(
            """
            UPDATE users
            SET is_active = true,
                deactivated_at = NULL,
                deactivated_by = NULL,
                updated_at = now()
            WHERE id = $1
            RETURNING id, email, full_name, is_active, deactivated_at
            """,
            user_id,
        )
        await write_audit_log(
            conn,
            org_id=str(target["org_id"]),
            action="reactivate_user",
            table_name="users",
            record_id=user_id,
            old={"is_active": target["is_active"]},
            new={"is_active": True, "actor": str(actor_id)},
            actor=actor_id,
        )

    return AdminUserMutation(
        id=row["id"],
        email=row["email"],
        full_name=row["full_name"],
        is_active=row["is_active"],
        deactivated_at=str(row["deactivated_at"]) if row["deactivated_at"] else None,
    )


@router.delete("/admin/users/{user_id}", response_model=AdminUserMutation)
async def delete_user(request: Request, user_id: UUID):
    """Anonymize an account. The row is NOT removed — see the module docstring.

    92 FK columns across 69 public tables point at ``users(id)``, 89 of them ON
    DELETE NO ACTION, so a real DELETE fails for anyone with history — and the 3
    that DO cascade would destroy that member's votes and interest records if the
    others were ever loosened. This is the stronger-deactivation form instead,
    and the response says so explicitly
    (``hard_deleted=False``, ``anonymized=True``) rather than letting the verb
    imply something that did not happen.

    The grant revocations and the PII clear run in ONE transaction: a
    half-anonymized row — PII cleared but grants still standing, or the reverse —
    is worse than either end state.
    """
    actor_id, org_id = await _require_manage_members(request)

    pool = await get_pool()
    async with pool.acquire() as conn:
        target, caller_is_super = await _resolve_target(conn, actor_id, org_id, user_id)
        _forbid_self(actor_id, target, "delete")
        _forbid_staff_target(target, caller_is_super, "delete")

        if is_anonymized(target["email"]):
            raise HTTPException(
                status_code=409, detail="This account has already been anonymized."
            )

        async with conn.transaction():
            # Revoke every grant first. Both tables are pure join rows carrying
            # no history worth keeping, so these ARE real deletes.
            await conn.execute("DELETE FROM user_roles WHERE user_id = $1", user_id)
            await conn.execute(
                "DELETE FROM user_permission_sets WHERE user_id = $1", user_id
            )
            row = await conn.fetchrow(
                """
                UPDATE users
                SET email          = $2,
                    full_name      = $3,
                    -- Clearing auth0_sub is what actually severs access: the
                    -- identity can no longer resolve to this row, so a fresh
                    -- login creates a new (empty) account instead of reclaiming
                    -- this one. NULL is legal and repeatable here — many NULLs
                    -- coexist under the UNIQUE constraint (the same property the
                    -- invite flow relies on).
                    auth0_sub      = NULL,
                    avatar_url     = NULL,
                    profile_id     = NULL,
                    manager_id     = NULL,
                    invite_token   = NULL,
                    invite_status  = NULL,
                    is_active      = false,
                    -- The user_roles DELETE above already revoked the real
                    -- org_admin grant (if any) — this keeps the STRING in
                    -- lockstep with that, the same rule assign_role enforces,
                    -- so an anonymized row never keeps reading as an admin in
                    -- the user list with no grant behind it. Never touches
                    -- 'super_admin' (untouched by RBAC).
                    role = CASE WHEN role = 'org_admin' THEN 'member' ELSE role END,
                    -- USERMGMT1: the screen calls this "Retire (keeps
                    -- history)", and the list hides retired rows by default.
                    status         = 'retired',
                    deactivated_at = COALESCE(deactivated_at, now()),
                    deactivated_by = COALESCE(deactivated_by, $4),
                    updated_at     = now()
                WHERE id = $1
                RETURNING id, email, full_name, is_active, deactivated_at
                """,
                user_id,
                anonymized_email(user_id),
                ANONYMIZED_FULL_NAME,
                actor_id,
            )

        # The audit row records only the fact and the actor, never the cleared
        # PII — writing the old email into audit_log would defeat the
        # anonymization it is recording.
        await write_audit_log(
            conn,
            org_id=str(target["org_id"]),
            action="anonymize_user",
            table_name="users",
            record_id=user_id,
            new={
                "anonymized": True,
                "hard_deleted": False,
                "reason": "users.id has 92 FK dependents across 69 tables, 89 ON DELETE NO ACTION",
                "actor": str(actor_id),
            },
            actor=actor_id,
        )

    return AdminUserMutation(
        id=row["id"],
        email=row["email"],
        full_name=row["full_name"],
        is_active=row["is_active"],
        deactivated_at=str(row["deactivated_at"]) if row["deactivated_at"] else None,
        hard_deleted=False,
        anonymized=True,
    )
