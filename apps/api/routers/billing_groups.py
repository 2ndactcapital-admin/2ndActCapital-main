"""REST endpoints for billing groups — the manage-billing-groups screen. fee33.

``org_id`` comes from ``routers.entities.get_org_id`` (JWT claims) on every
route and is NEVER accepted from a request body or a path segment. The request
models below use ``extra='forbid'`` and declare no ``org_id`` field, so there is
nothing for a caller to send and nothing for a future edit to start trusting —
a model that merely DECLARED one would itself be the bug.

Reads require ``view_portfolio``; writes require ``manage_billing``. Both
already exist in ``public.permissions`` (measured in Task 1: manage_billing →
admin, super_admin; view_portfolio → six roles), so this router invents no new
permission name. ``manage_billing`` rather than ``manage_portfolio`` is
deliberate and matches fee31's custody importer: deciding what a client is
billed on is a narrower authority than editing a holding.

WHY BreakpointOverlapError IS A 409 AND NOT A 400
──────────────────────────────────────────────────────────────────────────────
The request is well-formed and the caller is authorised; it conflicts with the
current state of another row. A 400 would tell the UI "you sent something
malformed", which sends the operator looking at their own input instead of at
the group the account is already in. The 409 body carries both group ids so the
screen can link straight to the blocker.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, field_validator

from routers.entities import get_org_id
from services.billing_groups import (
    EDITABLE_GROUP_FIELDS,
    GROUP_TYPES,
    READ_PERMISSION,
    WRITE_PERMISSION,
    BillingGroupError,
    BillingGroupNotFoundError,
    BreakpointOverlapError,
    add_member,
    archive_billing_group,
    assignable_accounts,
    create_billing_group,
    list_account_memberships,
    linkable_households,
    list_billing_groups,
    list_members,
    move_member,
    remove_member,
    update_billing_group,
)
from services.database import get_pool
from services.permissions import get_user_id
from services.rbac import has_permission, is_super_admin, load_principal, require_permission

router = APIRouter(prefix="/billing-groups", tags=["billing-groups"])


# ── Gates and the envelope ───────────────────────────────────────────────────


async def _gate(request: Request, permission: str) -> tuple[str, str, Any]:
    """Resolve ``(org_id, user_id, pool)`` and enforce a permission.

    ``rbac.require_permission`` raises 403 naming the permission. Super Admin
    passes it — checked FIRST inside ``has_permission``, via the one shared
    helper, never a second local re-implementation.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, permission)
    return org_id, user_id, pool


async def _permission_envelope(pool, user_id: str, org_id: str) -> dict[str, Any]:
    """What this caller may do, resolved server-side and shipped with the page.

    Advisory to the client, binding on nobody: every write endpoint re-checks.
    The UI renders a write control ONLY inside a ``permissions.can_write`` test
    with no truthy fallback, so a lost envelope fails closed rather than
    silently restoring full write access.
    """
    can_write = await has_permission(pool, user_id, org_id, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    super_admin = is_super_admin(principal)
    return {
        "can_read": True,          # only built after the read gate passed
        "can_write": bool(can_write),
        "is_super_admin": bool(super_admin),
        "read_permission": READ_PERMISSION,
        "write_permission": WRITE_PERMISSION,
    }


def _vocabularies(perms: dict[str, Any]) -> dict[str, Any]:
    """``editable`` is an EMPTY LIST for a view-only caller — never omitted."""
    return {
        "group_type": list(GROUP_TYPES),
        # Which types restrict membership, published so the screen explains the
        # rule from the server's own answer rather than a hardcoded string.
        "exclusive_group_types": ["BREAKPOINT"],
        "editable": sorted(EDITABLE_GROUP_FIELDS) if perms["can_write"] else [],
        "inline_editable": ["name", "notes"] if perms["can_write"] else [],
    }


def _raise_for(exc: BillingGroupError) -> None:
    """Map the service's typed errors onto status codes that mean something."""
    if isinstance(exc, BreakpointOverlapError):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "breakpoint_overlap",
                "message": str(exc),
                "account_id": exc.account_id,
                "account_label": exc.account_label,
                "existing_group_id": exc.existing_group_id,
                "existing_group_name": exc.existing_group_name,
                "attempted_group_id": exc.attempted_group_id,
                "attempted_group_name": exc.attempted_group_name,
            },
        )
    if isinstance(exc, BillingGroupNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc))
    raise HTTPException(status_code=400, detail=str(exc))


# ── Request models ───────────────────────────────────────────────────────────


class GroupCreate(BaseModel):
    """``extra='forbid'`` makes the org_id rule mechanical, not a review habit."""

    model_config = ConfigDict(extra="forbid")

    name: str
    group_type: Literal["BREAKPOINT", "STATEMENT", "PAYER"]
    household_id: str | None = None
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("name cannot be blank")
        return v


class GroupPatch(BaseModel):
    """Sparse. ``model_fields_set`` is what distinguishes an explicit null.

    Without it, ``household_id`` omitted and ``household_id: null`` are the same
    request, and every PATCH would silently unlink the household.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    group_type: Literal["BREAKPOINT", "STATEMENT", "PAYER"] | None = None
    household_id: str | None = None
    notes: str | None = None


class MemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str


class MemberMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_group_id: str


# ── Reads ────────────────────────────────────────────────────────────────────


@router.get("")
async def list_groups(
    request: Request,
    group_type: str | None = Query(None),
    household_id: str | None = Query(None),
) -> dict[str, Any]:
    """The groups grid. READ — ``view_portfolio``."""
    org_id, user_id, pool = await _gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        try:
            rows = await list_billing_groups(
                conn, org_id, group_type=group_type, household_id=household_id
            )
            households = await linkable_households(conn, org_id)
        except BillingGroupError as exc:
            _raise_for(exc)
    perms = await _permission_envelope(pool, user_id, org_id)
    return {
        "rows": rows,
        # The link picker's options, from the server's own response rather than
        # a second endpoint the screen would have to be separately entitled to.
        "households": households,
        "permissions": perms,
        "vocabularies": _vocabularies(perms),
    }


@router.get("/{group_id}/members")
async def get_members(request: Request, group_id: str) -> dict[str, Any]:
    """One group's active members. READ — ``view_portfolio``."""
    org_id, user_id, pool = await _gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        try:
            rows = await list_members(conn, org_id, group_id)
            candidates = await assignable_accounts(conn, org_id, group_id=group_id)
        except BillingGroupError as exc:
            _raise_for(exc)
    perms = await _permission_envelope(pool, user_id, org_id)
    return {
        "rows": rows,
        # Blocked accounts come back WITH their blocker rather than filtered
        # out, so the picker can grey one out and say why.
        "candidates": candidates,
        "permissions": perms,
        "vocabularies": _vocabularies(perms),
    }


@router.get("/accounts/{account_id}/memberships")
async def get_account_memberships(request: Request, account_id: str) -> dict[str, Any]:
    """Every active group one account is in. READ — ``view_portfolio``."""
    org_id, user_id, pool = await _gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        rows = await list_account_memberships(conn, org_id, account_id)
    perms = await _permission_envelope(pool, user_id, org_id)
    return {"rows": rows, "permissions": perms, "vocabularies": _vocabularies(perms)}


# ── Writes ───────────────────────────────────────────────────────────────────


@router.post("", status_code=201)
async def post_group(request: Request, body: GroupCreate) -> dict[str, Any]:
    """Create a group. WRITE — ``manage_billing``."""
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            row = await create_billing_group(
                conn, org_id,
                name=body.name,
                group_type=body.group_type,
                household_id=body.household_id,
                notes=body.notes,
                created_by=user_id,
            )
        except BillingGroupError as exc:
            _raise_for(exc)
    return row


@router.patch("/{group_id}")
async def patch_group(request: Request, group_id: str, body: GroupPatch) -> dict[str, Any]:
    """Restate a group. WRITE — ``manage_billing``.

    Retyping to BREAKPOINT re-checks the whole existing membership; see
    ``services.billing_groups.update_billing_group``.
    """
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)
    if not body.model_fields_set:
        raise HTTPException(status_code=400, detail="no fields supplied")
    async with pool.acquire() as conn:
        try:
            row = await update_billing_group(
                conn, org_id, group_id,
                name=body.name,
                group_type=body.group_type,
                household_id=body.household_id,
                notes=body.notes,
                fields_set=set(body.model_fields_set),
            )
        except BillingGroupError as exc:
            _raise_for(exc)
    return row


@router.delete("/{group_id}")
async def delete_group(request: Request, group_id: str) -> dict[str, Any]:
    """Archive a group and close its memberships. WRITE — ``manage_billing``.

    Not a hard delete — see ``archive_billing_group``.
    """
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            ok = await archive_billing_group(conn, org_id, group_id)
        except BillingGroupError as exc:
            _raise_for(exc)
    return {"archived": bool(ok), "id": group_id}


@router.post("/{group_id}/members", status_code=201)
async def post_member(request: Request, group_id: str, body: MemberAdd) -> dict[str, Any]:
    """Place an account in a group. WRITE — ``manage_billing``.

    409 with both group ids if this would be a second active BREAKPOINT
    membership for the account.
    """
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            row = await add_member(
                conn, org_id,
                group_id=group_id, account_id=body.account_id, added_by=user_id,
            )
        except BillingGroupError as exc:
            _raise_for(exc)
    return row


@router.delete("/{group_id}/members/{account_id}")
async def delete_member(request: Request, group_id: str, account_id: str) -> dict[str, Any]:
    """End a membership. WRITE — ``manage_billing``. Closes the row, never deletes."""
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            ok = await remove_member(conn, org_id, group_id=group_id, account_id=account_id)
        except BillingGroupError as exc:
            _raise_for(exc)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"account {account_id} is not an active member of group {group_id}",
        )
    return {"removed": True, "group_id": group_id, "account_id": account_id}


@router.post("/members/{member_id}/move")
async def post_member_move(request: Request, member_id: str, body: MemberMove) -> dict[str, Any]:
    """Move one membership to another group. WRITE — ``manage_billing``."""
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            row = await move_member(
                conn, org_id,
                member_id=member_id, target_group_id=body.target_group_id,
                moved_by=user_id,
            )
        except BillingGroupError as exc:
            _raise_for(exc)
    return row
