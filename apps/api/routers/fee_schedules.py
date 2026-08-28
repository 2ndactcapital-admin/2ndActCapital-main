"""REST endpoints for the fee schedule catalog — fee34.

``org_id`` comes from ``routers.entities.get_org_id`` (the caller's own verified
session context) on every route and is NEVER accepted from a request body or a
path segment. Every model below sets ``extra='forbid'`` and declares no
``org_id`` field, so there is nothing for a caller to send and nothing for a
later edit to start trusting.

The same rule applies here to a SECOND field, for the same reason.
``fee_assignments.precedence`` is ``NOT NULL`` with no default and no tie to
``scope_type`` in the database, so a body carrying ``precedence: 1`` on an
ORG_DEFAULT assignment would outrank every account-specific agreement in the
org. :class:`AssignmentCreate` does not declare it, ``extra='forbid'`` rejects
it if sent, and ``services.fee_schedules`` derives it from ``scope_type``.

Reads require ``view_portfolio``; writes require ``manage_billing``. Both
already exist in ``public.permissions`` — this router invents no new permission
name, matching fee31/fee33.


STATUS CODES, AND WHY VALIDATION IS 422 AND NOT 400
──────────────────────────────────────────────────────────────────────────────
A rejected approval is not a malformed request. The body was well-formed and
the caller was entitled; the SCHEDULE is not ready. A 400 sends the operator
looking at their own input. The 422 body carries the full error list — every
entry with its ``code``, ``field`` and, for a tier rule, its ``tier_seq`` — so
the form can mark each offending input rather than showing one message and
making the operator resubmit to discover the next problem.

A status conflict (editing a RETIRED schedule, approving something already
APPROVED) is 409: the request is fine, the row's state is not.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, field_validator

from routers.entities import get_org_id
from services.database import get_pool
from services.fee_schedules import (
    EDITABLE_SCHEDULE_FIELDS,
    READ_PERMISSION,
    SCOPE_PRECEDENCE,
    WRITE_PERMISSION,
    FeeScheduleError,
    FeeScheduleInvalid,
    FeeScheduleNotFoundError,
    ScheduleStatusError,
    ScopeIdRequiredError,
    ScopeLinkError,
    create_assignment,
    create_schedule,
    end_assignment,
    get_schedule,
    list_assignments,
    list_schedules,
    resolve_assignment_for_account,
    retire_schedule,
    submit_for_approval,
    update_schedule,
)
from services.fee_validation import (
    ASSIGNMENT_SCOPE_TYPES,
    MINIMUM_FEE_SCOPES,
    ORDERING_STEPS,
    SCHEDULE_STATUSES,
)
from services.permissions import get_user_id
from services.rbac import has_permission, is_super_admin, load_principal, require_permission

router = APIRouter(prefix="/fee-schedules", tags=["fee-schedules"])


# ── Gates and the envelope ───────────────────────────────────────────────────


async def _gate(request: Request, permission: str) -> tuple[str, str, Any]:
    """Resolve ``(org_id, user_id, pool)`` and enforce a permission.

    ``rbac.require_permission`` raises 403 naming the permission, and checks
    Super Admin FIRST inside the one shared helper — never a second local
    re-implementation of the same check.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, permission)
    return org_id, user_id, pool


async def _permission_envelope(pool, user_id: str, org_id: str) -> dict[str, Any]:
    """What this caller may do, resolved server-side and shipped with the page.

    Advisory to the client and binding on nobody: every write endpoint re-checks
    independently. The UI renders a write control ONLY inside a
    ``permissions.can_write`` test with no truthy fallback, so a lost envelope
    fails closed rather than silently restoring full write access.
    """
    can_write = await has_permission(pool, user_id, org_id, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    return {
        "can_read": True,          # only built after the read gate passed
        "can_write": bool(can_write),
        "is_super_admin": bool(is_super_admin(principal)),
        "read_permission": READ_PERMISSION,
        "write_permission": WRITE_PERMISSION,
    }


def _vocabularies(perms: dict[str, Any]) -> dict[str, Any]:
    """Every label the screen needs, from the server. Rule 1.

    ``editable`` and ``inline_editable`` are EMPTY LISTS for a view-only caller
    — never omitted, never defaulted client-side.

    ``scope_precedence`` is published as data because the screen has to explain
    why one assignment won. Hardcoding the order in the frontend would put a
    second copy of the rule somewhere it could drift from the one the server
    actually applies.
    """
    can_write = perms["can_write"]
    return {
        "status": list(SCHEDULE_STATUSES),
        "scope_type": list(ASSIGNMENT_SCOPE_TYPES),
        "scope_precedence": dict(SCOPE_PRECEDENCE),
        "minimum_fee_scope": list(MINIMUM_FEE_SCOPES),
        "ordering_steps": list(ORDERING_STEPS),
        "editable": sorted(EDITABLE_SCHEDULE_FIELDS) if can_write else [],
        "inline_editable": ["name"] if can_write else [],
    }


def _raise_for(exc: Exception) -> None:
    """Map the service's typed errors onto status codes that mean something."""
    if isinstance(exc, FeeScheduleInvalid):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "schedule_invalid",
                "message": str(exc),
                # One entry per broken rule, each naming its field and — for a
                # tier rule — its tier_seq, so the form marks every offending
                # input at once instead of one per round trip.
                "errors": [e.as_dict() for e in exc.errors],
            },
        )
    if isinstance(exc, FeeScheduleNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ScheduleStatusError):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "schedule_status",
                "message": str(exc),
                "schedule_id": exc.schedule_id,
                "status": exc.status,
            },
        )
    if isinstance(exc, ScopeLinkError):
        raise HTTPException(
            status_code=409 if exc.reason == "closed" else 400,
            detail={
                "error": f"scope_{exc.reason}",
                "message": str(exc),
                "scope_type": exc.scope_type,
                "scope_id": exc.scope_id,
            },
        )
    if isinstance(exc, ScopeIdRequiredError):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "scope_id_required",
                "message": str(exc),
                "scope_type": exc.scope_type,
            },
        )
    raise HTTPException(status_code=400, detail=str(exc))


# ── Request models ───────────────────────────────────────────────────────────
#
# Every money field is typed ``Decimal``, never ``float``. Pydantic parses a
# JSON number straight into Decimal without going through a float, so
# "1000000.01" survives intact — a float field would round it at the boundary,
# before any validator could object.


class TierIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier_seq: int
    lower_bound: Decimal
    upper_bound: Decimal | None = None
    rate_bps: Decimal | None = None
    flat_amount: Decimal | None = None


class ScheduleCreate(BaseModel):
    """``extra='forbid'`` makes the org_id rule mechanical, not a review habit.

    ``status`` and ``version`` are absent by design: a schedule is always
    created DRAFT at version 1. A body that could assert APPROVED would be the
    one door around the validation gate this sprint exists to build.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    product_type: Literal[
        "ASSET_MANAGEMENT", "SPV", "STRUCTURED_INVESTMENT",
        "PLANNING", "CLUB_DUES", "TRANSACTION",
    ]
    rate_type: Literal["BPS", "FLAT", "HYBRID", "HOURLY", "PER_ACCOUNT"]
    billing_frequency: Literal["MONTHLY", "QUARTERLY", "SEMIANNUAL", "ANNUAL"]
    billing_timing: Literal["ADVANCE", "ARREARS"]
    valuation_method: Literal[
        "PERIOD_END", "PERIOD_START", "AVG_DAILY", "AVG_MONTH_END"
    ]
    tier_method: Literal["GRADUATED", "CLIFF", "BLENDED_PUBLISHED"] | None = None
    day_weight_flows: bool | None = None
    day_weight_threshold: Decimal | None = None
    proration_method: Literal["CALENDAR_DAYS", "BUSINESS_DAYS", "NONE"] | None = None
    minimum_fee: Decimal | None = None
    minimum_fee_scope: Literal["ACCOUNT", "BILLING_GROUP", "HOUSEHOLD"] | None = None
    maximum_fee: Decimal | None = None
    minimum_billable_value: Decimal | None = None
    cash_treatment: Literal["INCLUDE", "EXCLUDE", "EXCLUDE_ABOVE_PCT"] | None = None
    cash_exclusion_pct: Decimal | None = None
    margin_treatment: Literal["IGNORE", "REDUCE_BILLABLE"] | None = None
    ordering_policy: list[str] | None = None
    currency: str | None = None
    tiers: list[TierIn] | None = None

    @field_validator("code", "name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("cannot be blank")
        return v


class SchedulePatch(BaseModel):
    """Sparse. ``model_fields_set`` is what distinguishes an explicit null.

    Without it, ``minimum_fee`` omitted and ``minimum_fee: null`` are the same
    request, and every PATCH would silently clear the minimum. ``code`` is not
    patchable: it is the versioning identity, so changing it would not produce
    version N+1 of this schedule, it would fork a differently-named one.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    product_type: str | None = None
    rate_type: str | None = None
    tier_method: str | None = None
    billing_frequency: str | None = None
    billing_timing: str | None = None
    valuation_method: str | None = None
    day_weight_flows: bool | None = None
    day_weight_threshold: Decimal | None = None
    proration_method: str | None = None
    minimum_fee: Decimal | None = None
    minimum_fee_scope: str | None = None
    maximum_fee: Decimal | None = None
    minimum_billable_value: Decimal | None = None
    cash_treatment: str | None = None
    cash_exclusion_pct: Decimal | None = None
    margin_treatment: str | None = None
    ordering_policy: list[str] | None = None
    currency: str | None = None
    tiers: list[TierIn] | None = None


class AssignmentCreate(BaseModel):
    """Declares NO ``precedence``. See the module docstring — it is derived
    from ``scope_type`` server-side, and ``extra='forbid'`` refuses it if a
    caller sends one anyway."""

    model_config = ConfigDict(extra="forbid")

    fee_schedule_id: str
    scope_type: Literal["ACCOUNT", "BILLING_GROUP", "HOUSEHOLD", "ENTITY", "ORG_DEFAULT"]
    scope_id: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    agreement_document_id: str | None = None
    replace_existing: bool = True


class AssignmentEnd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_to: date | None = None


# ── Reads ────────────────────────────────────────────────────────────────────


@router.get("")
async def list_schedules_route(
    request: Request,
    status: str | None = Query(None),
    code: str | None = Query(None),
    include_superseded: bool = Query(True),
) -> dict[str, Any]:
    """The catalog grid. READ — ``view_portfolio``."""
    org_id, user_id, pool = await _gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        try:
            rows = await list_schedules(
                conn, org_id, status=status, code=code,
                include_superseded=include_superseded,
            )
        except FeeScheduleError as exc:
            _raise_for(exc)
    perms = await _permission_envelope(pool, user_id, org_id)
    return {
        "rows": rows,
        "permissions": perms,
        "vocabularies": _vocabularies(perms),
    }


@router.get("/{schedule_id}")
async def get_schedule_route(request: Request, schedule_id: str) -> dict[str, Any]:
    """One schedule, its tiers, and its CURRENT validation state.

    ``validation_errors`` is published on a plain read so a DRAFT screen can
    show what still blocks approval without the operator having to attempt it
    and be refused.
    """
    org_id, user_id, pool = await _gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        try:
            payload = await get_schedule(conn, org_id, schedule_id)
        except FeeScheduleError as exc:
            _raise_for(exc)
    perms = await _permission_envelope(pool, user_id, org_id)
    return {
        **payload,
        "permissions": perms,
        "vocabularies": _vocabularies(perms),
    }


@router.get("/{schedule_id}/assignments")
async def list_schedule_assignments(
    request: Request, schedule_id: str, include_ended: bool = Query(False)
) -> dict[str, Any]:
    """Which scopes point at this schedule. READ — ``view_portfolio``."""
    org_id, user_id, pool = await _gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        try:
            rows = await list_assignments(
                conn, org_id, fee_schedule_id=schedule_id,
                include_ended=include_ended,
            )
        except FeeScheduleError as exc:
            _raise_for(exc)
    perms = await _permission_envelope(pool, user_id, org_id)
    return {"rows": rows, "permissions": perms, "vocabularies": _vocabularies(perms)}


@router.get("/resolve/account/{account_id}")
async def resolve_for_account(
    request: Request, account_id: str, as_of: date | None = Query(None)
) -> dict[str, Any]:
    """Which schedule governs an account, and what it beat.

    ``losers`` is published alongside the winner because "why is this account
    on the household schedule rather than the org default" is a question an
    operator asks, and answering it from a response that carried only the
    winner would mean re-deriving the whole resolution client-side.
    """
    org_id, user_id, pool = await _gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        try:
            resolved = await resolve_assignment_for_account(
                conn, org_id, account_id, as_of=as_of
            )
        except FeeScheduleError as exc:
            _raise_for(exc)
    perms = await _permission_envelope(pool, user_id, org_id)
    return {
        "account_id": account_id,
        "as_of": as_of or date.today(),
        # None is a real answer — no assignment and no org default means the
        # account is NOT billed, which is different from billed at zero.
        "resolved": None if resolved is None else {
            "assignment_id": resolved.assignment_id,
            "fee_schedule_id": resolved.fee_schedule_id,
            "scope_type": resolved.scope_type,
            "scope_id": resolved.scope_id,
            "precedence": resolved.precedence,
            "schedule_code": resolved.schedule_code,
            "schedule_version": resolved.schedule_version,
            "schedule_status": resolved.schedule_status,
        },
        "losers": [] if resolved is None else list(resolved.losers),
        "permissions": perms,
        "vocabularies": _vocabularies(perms),
    }


# ── Writes ───────────────────────────────────────────────────────────────────


@router.post("")
async def create_schedule_route(
    request: Request, body: ScheduleCreate
) -> dict[str, Any]:
    """Create a DRAFT schedule at version 1. WRITE — ``manage_billing``."""
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)
    payload = body.model_dump(exclude_unset=True)
    tiers = payload.pop("tiers", None)
    code = payload.pop("code")
    async with pool.acquire() as conn:
        try:
            result = await create_schedule(
                conn, org_id, code=code,
                tiers=[dict(t) for t in tiers] if tiers else None,
                created_by=user_id, **payload,
            )
        except (FeeScheduleError, FeeScheduleInvalid) as exc:
            _raise_for(exc)
    return result


@router.patch("/{schedule_id}")
async def update_schedule_route(
    request: Request, schedule_id: str, body: SchedulePatch
) -> dict[str, Any]:
    """Edit a schedule. WRITE — ``manage_billing``.

    A DRAFT is edited in place. An APPROVED schedule is NOT modified — the
    response's ``versioned`` flag says a new DRAFT at version+1 was created
    instead, and ``schedule_id`` differs from the one in the path. The UI must
    read both rather than assume the edit landed on the row it opened.
    """
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)
    changes = body.model_dump(exclude_unset=True)
    tiers = changes.pop("tiers", ...)   # sentinel: absent vs explicitly null
    async with pool.acquire() as conn:
        try:
            outcome = await update_schedule(
                conn, org_id, schedule_id,
                tiers=None if tiers is ... else (
                    [dict(t) for t in tiers] if tiers else []
                ),
                created_by=user_id, **changes,
            )
            payload = await get_schedule(conn, org_id, outcome.schedule_id)
        except (FeeScheduleError, FeeScheduleInvalid) as exc:
            _raise_for(exc)
    return {
        **payload,
        "versioned": outcome.versioned,
        "schedule_id": outcome.schedule_id,
        "source_schedule_id": outcome.source_schedule_id,
    }


@router.post("/{schedule_id}/submit")
async def submit_route(request: Request, schedule_id: str) -> dict[str, Any]:
    """DRAFT → APPROVED, only on a validation all-clear. WRITE.

    Refused with 422 and the full error list otherwise; the schedule stays
    DRAFT and nothing is partially applied.
    """
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            return await submit_for_approval(
                conn, org_id, schedule_id, approved_by=user_id
            )
        except (FeeScheduleError, FeeScheduleInvalid) as exc:
            _raise_for(exc)


@router.post("/{schedule_id}/retire")
async def retire_route(request: Request, schedule_id: str) -> dict[str, Any]:
    """→ RETIRED. Existing assignments are deliberately left alone. WRITE."""
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            return await retire_schedule(conn, org_id, schedule_id)
        except (FeeScheduleError, FeeScheduleInvalid) as exc:
            _raise_for(exc)


@router.post("/assignments")
async def create_assignment_route(
    request: Request, body: AssignmentCreate
) -> dict[str, Any]:
    """Point a scope at a schedule. WRITE — ``manage_billing``."""
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            return await create_assignment(
                conn, org_id,
                fee_schedule_id=body.fee_schedule_id,
                scope_type=body.scope_type,
                scope_id=body.scope_id,
                effective_from=body.effective_from,
                effective_to=body.effective_to,
                agreement_document_id=body.agreement_document_id,
                created_by=user_id,
                replace_existing=body.replace_existing,
            )
        except (FeeScheduleError, FeeScheduleInvalid) as exc:
            _raise_for(exc)


@router.post("/assignments/{assignment_id}/end")
async def end_assignment_route(
    request: Request, assignment_id: str, body: AssignmentEnd
) -> dict[str, Any]:
    """Close an assignment. It is CLOSED, never deleted — a fee run for a past
    period has to be able to see the assignment that governed it. WRITE."""
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            return await end_assignment(
                conn, org_id, assignment_id, effective_to=body.effective_to
            )
        except (FeeScheduleError, FeeScheduleInvalid) as exc:
            _raise_for(exc)
