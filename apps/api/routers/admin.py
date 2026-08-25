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

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from routers.entities import get_org_id
from services.audit import write_audit_log
from services.database import get_pool
from services.org_settings import USER_INACTIVITY_TIMEOUT_DAYS_KEY, get_setting
from services.rbac import is_super_admin, load_principal, require_permission
from services.users import ensure_user

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
        "SELECT id, org_id, email, full_name, role, is_active, auth0_sub "
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
    await _require_manage_members(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name FROM roles ORDER BY name")
    return [RoleOption(**dict(r)) for r in rows]


@router.get("/admin/users", response_model=list[AdminUser])
async def list_users(
    request: Request,
    search: str | None = None,
    role: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    _, org_id = await _require_manage_members(request)

    conditions = ["u.org_id = $1"]
    params: list = [org_id]
    if search:
        params.append(f"%{search}%")
        conditions.append(
            f"(u.full_name ILIKE ${len(params)} OR u.email ILIKE ${len(params)})"
        )
    if role:
        params.append(role)
        conditions.append(f"r.name = ${len(params)}")

    params.append(limit)
    limit_pos = len(params)
    params.append(offset)
    offset_pos = len(params)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT u.id, u.email, u.full_name, u.created_at,
                   r.id AS role_id, r.name AS role,
                   u.profile_id, p.name AS profile_name,
                   u.invite_status, u.role AS account_role,
                   u.is_active, u.deactivated_at, u.last_login_at
            FROM users u
            LEFT JOIN user_roles ur ON ur.user_id = u.id
            LEFT JOIN roles r ON r.id = ur.role_id
            LEFT JOIN profiles p ON p.id = u.profile_id
            WHERE {' AND '.join(conditions)}
            ORDER BY u.full_name NULLS LAST, u.email
            LIMIT ${limit_pos} OFFSET ${offset_pos}
            """,
            *params,
        )
    return [
        AdminUser(
            id=r["id"],
            email=r["email"],
            full_name=r["full_name"],
            role=r["role"],
            role_id=r["role_id"],
            created_at=str(r["created_at"]) if r["created_at"] else None,
            profile_id=r["profile_id"],
            profile_name=r["profile_name"],
            invite_status=r["invite_status"],
            account_role=r["account_role"],
            is_active=r["is_active"],
            deactivated_at=str(r["deactivated_at"]) if r["deactivated_at"] else None,
            last_login_at=str(r["last_login_at"]) if r["last_login_at"] else None,
            is_deleted=is_anonymized(r["email"]),
        )
        for r in rows
    ]


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
    actor_id, org_id = await _require_manage_members(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                "SELECT id, email, full_name FROM users WHERE id = $1 AND org_id = $2",
                user_id, org_id,
            )
            if target is None:
                raise HTTPException(status_code=404, detail="User not found")

            role = await conn.fetchrow(
                "SELECT id, name FROM roles WHERE id = $1", body.role_id
            )
            if role is None:
                raise HTTPException(status_code=400, detail="Unknown role")

            await conn.execute("DELETE FROM user_roles WHERE user_id = $1", user_id)
            await conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)",
                user_id, body.role_id,
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
