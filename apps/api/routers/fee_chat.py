"""The fee chat interface — describe an arrangement, get a schedule. fee40.

    GET  /fee-chat/conversation    — get or create the advisor's draft session
    POST /fee-chat/propose         — NL -> FeeSpec -> resolver -> diff -> fee34
    POST /fee-chat/worked-example  — price a proposal with fee35, on real data
    POST /fee-chat/corrections     — record advisor edits to model-proposed fields
    POST /fee-chat/save            — fee34's gate, then fee34's own writer

``org_id`` comes from ``get_org_id(request)`` — the caller's verified session —
on every route. No model below declares an ``org_id`` and every one sets
``extra='forbid'``, so there is nothing for a caller to send and nothing for a
later edit to start trusting.

Reads require ``view_portfolio``; writes require ``manage_billing``. The same
two permissions fee31/33/34 use — this router invents no new permission name.
Proposing is a WRITE: it spends money on a model call and writes a conversation
row. A view-only advisor may read a draft someone else made and may not create
one.

WHAT THIS ROUTER DOES NOT DO
──────────────────────────────────────────────────────────────────────────────
It does not validate a schedule. ``services.fee_validation.validate_schedule``
(fee34) does, and the errors published here are ``e.as_dict()`` from that
call — the same objects, with the same ``code``/``field``/``tier_seq``, that
``routers/fee_schedules`` puts in its own 422. A second, friendlier message set
would drift from the one that actually refuses the save.

It does not compute a fee. ``services.fee_calc`` (fee35) does.

It does not resolve a name to an id. ``services.fee_spec_resolver`` does, and
returns candidates rather than a pick when it cannot be certain.

STATUS CODES
──────────────────────────────────────────────────────────────────────────────
422 — the proposal is well-formed and fee34 refuses it. The body carries the
      full error list so the form marks every offending input at once.
502 — the MODEL misbehaved: no JSON, or no model answered at all. Upstream's
      fault, not the caller's, so not a 4xx.
409 — the schedule state conflicts (a code already exists).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services.database import get_pool
from services.fee_schedules import (
    READ_PERMISSION,
    WRITE_PERMISSION,
    FeeScheduleError,
    FeeScheduleInvalid,
    FeeScheduleNotFoundError,
    create_schedule,
)
from services.fee_spec import (
    FEE_SPEC_VERSION,
    GROUNDED_FIELDS,
    REQUIRED_SCHEDULE_FIELDS,
    SPEC_SCHEDULE_FIELDS,
    SPEC_VOCABULARIES,
    FeeSpecError,
    FeeSpecParseError,
    FeeSpecShapeError,
    FeeSpecUnavailableError,
    NormalisedSpec,
    normalise_fee_spec,
    propose_fee_spec,
    spec_to_json,
)
from services.fee_spec_corrections import (
    TARGET_TYPE as CORRECTION_TARGET_TYPE,
    FeeSpecCorrectionError,
    log_fee_spec_corrections,
)
from services.fee_spec_diff import build_diff
from services.fee_spec_resolver import (
    FeeSpecResolutionError,
    apply_resolutions,
    resolve_spec_references,
)
from services.fee_validation import ORDERING_STEPS, validate_schedule
from services.fee_worked_example import (
    WorkedExampleUnavailable,
    compute_worked_example,
    default_period,
)
from services.permissions import get_user_id
from services.rbac import has_permission, is_super_admin, load_principal, require_permission
from services.users import ensure_user

router = APIRouter(prefix="/fee-chat", tags=["fee-chat"])

#: ``assistant_conversations.context_ref->>'type'`` for a fee drafting session.
#: Namespaced so the general assistant's ``_load_conversation`` never picks one
#: of these up as the user's active chat, and vice versa.
CONTEXT_TYPE = "fee_schedule_spec"


# ── Gates and the envelope ───────────────────────────────────────────────────


async def _gate(request: Request, permission: str) -> tuple[str, str, Any]:
    """``(org_id, user_id, pool)``, with ``permission`` enforced.

    ``rbac.require_permission`` raises 403 naming the permission and checks
    Super Admin FIRST inside that one shared helper — not re-implemented here.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, permission)
    return org_id, user_id, pool


async def _permission_envelope(pool, user_id: str, org_id: str) -> dict[str, Any]:
    can_write = await has_permission(pool, user_id, org_id, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    return {
        "can_read": True,               # only built after the read gate passed
        "can_write": bool(can_write),
        "is_super_admin": bool(is_super_admin(principal)),
        "read_permission": READ_PERMISSION,
        "write_permission": WRITE_PERMISSION,
    }


def _vocabularies(perms: dict[str, Any]) -> dict[str, Any]:
    """Every label and rule the screen needs, from the server. Rule 1.

    ``editable`` and ``inline_editable`` are EMPTY LISTS for a view-only caller —
    never omitted, never defaulted client-side. ``grounded`` is published as data
    because the screen has to explain WHY a field came back unresolved; a second
    copy of that set in the frontend would drift from the one that enforces it.
    """
    can_write = perms["can_write"]
    return {
        "schedule_fields": list(SPEC_SCHEDULE_FIELDS),
        "required": list(REQUIRED_SCHEDULE_FIELDS),
        "grounded": sorted(GROUNDED_FIELDS),
        "ordering_steps": list(ORDERING_STEPS),
        "values": {k: list(v) for k, v in SPEC_VOCABULARIES.items()},
        "spec_version": FEE_SPEC_VERSION,
        "editable": list(SPEC_SCHEDULE_FIELDS) if can_write else [],
        "inline_editable": list(SPEC_SCHEDULE_FIELDS) if can_write else [],
    }


def _raise_for(exc: Exception) -> None:
    """Typed service errors -> status codes that mean something."""
    if isinstance(exc, (FeeSpecParseError, FeeSpecUnavailableError)):
        # 502, not 400 and not 500: the caller's request was fine and this
        # process did not fail — an upstream model did.
        raise HTTPException(status_code=502, detail=exc.as_dict())
    if isinstance(exc, FeeSpecShapeError):
        raise HTTPException(status_code=502, detail=exc.as_dict())
    if isinstance(exc, FeeSpecError):
        raise HTTPException(status_code=400, detail=exc.as_dict())
    if isinstance(exc, WorkedExampleUnavailable):
        # 200 is wrong (there is no example) and 500 is wrong (nothing broke).
        raise HTTPException(status_code=422, detail=exc.as_dict())
    if isinstance(exc, FeeScheduleInvalid):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "schedule_invalid",
                "message": str(exc),
                "errors": [e.as_dict() for e in exc.errors],
            },
        )
    if isinstance(exc, FeeScheduleNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (FeeSpecResolutionError, FeeSpecCorrectionError)):
        raise HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, FeeScheduleError):
        raise HTTPException(status_code=409, detail=str(exc))
    raise HTTPException(status_code=400, detail=str(exc))


# ── Conversation state ───────────────────────────────────────────────────────
#
# Same shape as routers/assistant.py's own helpers — context_ref is one jsonb
# column holding {"type": ..., "id": ...}. Not a new convention.


async def _load_conversation(conn, org_id: str, user_id: str, conversation_id: str | None):
    if conversation_id:
        row = await conn.fetchrow(
            "SELECT id, messages, context_ref, created_at, updated_at "
            "FROM assistant_conversations "
            "WHERE id = $1::uuid AND org_id = $2::uuid AND user_id = $3::uuid",
            conversation_id, org_id, user_id,
        )
        return dict(row) if row else {}
    row = await conn.fetchrow(
        "SELECT id, messages, context_ref, created_at, updated_at "
        "FROM assistant_conversations "
        "WHERE user_id = $1::uuid AND org_id = $2::uuid AND status = 'active' "
        "  AND context_ref->>'type' = $3 "
        "ORDER BY updated_at DESC LIMIT 1",
        user_id, org_id, CONTEXT_TYPE,
    )
    return dict(row) if row else {}


async def _create_conversation(conn, org_id: str, user_id: str, fee_schedule_id: str | None):
    row = await conn.fetchrow(
        """
        INSERT INTO assistant_conversations
            (org_id, user_id, context_ref, messages, status, title)
        VALUES ($1::uuid, $2::uuid, $3::jsonb, '[]'::jsonb, 'active', $4)
        RETURNING id, messages, context_ref, created_at, updated_at
        """,
        org_id, user_id,
        json.dumps({
            "type": CONTEXT_TYPE, "id": fee_schedule_id,
            "spec_version": FEE_SPEC_VERSION,
        }),
        "Fee schedule draft",
    )
    return dict(row)


def _parse_jsonb(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        return json.loads(value) if value else default
    return value


async def _append_turn(conn, conversation_id: str, turn: dict[str, Any]) -> None:
    """Append one turn. ``||`` in SQL, not read-modify-write in Python.

    Two advisors — or one advisor in two tabs — reading the array, appending and
    writing it back would silently drop a turn. Concatenating in the UPDATE
    makes the append atomic against the row's own lock.
    """
    await conn.execute(
        """
        UPDATE assistant_conversations
        SET messages = messages || $1::jsonb, updated_at = now()
        WHERE id = $2::uuid
        """,
        json.dumps([turn], default=str), conversation_id,
    )


# ── Request models ───────────────────────────────────────────────────────────
#
# No model declares org_id. ``extra='forbid'`` makes that mechanical rather than
# a review habit. Money arrives as str/Decimal, never float — see fee_spec.


class ProposeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    fee_schedule_id: str | None = None
    conversation_id: str | None = None


class WorkedExampleBody(BaseModel):
    """``spec`` is the CLIENT's current spec, including the advisor's edits.

    Taken from the body rather than re-read from the conversation on purpose:
    the whole point of this screen is to price what the advisor is looking at
    right now, edits included, before any of it is saved. It is re-normalised
    server-side against ``description`` — an edited field is still subject to
    the same vocabulary checks the model's own answer was.
    """

    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    description: str = ""
    account_id: str | None = None
    household_id: str | None = None
    period_start: date | None = None
    period_end: date | None = None


class CorrectionsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str
    edits: dict[str, dict[str, Any]]
    fee_schedule_id: str | None = None


class SaveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    description: str = ""
    conversation_id: str | None = None


# ── Shared assembly ──────────────────────────────────────────────────────────


def _spec_payload(spec: NormalisedSpec) -> dict[str, Any]:
    """The spec as JSON-safe data. Decimals become exact digit strings."""
    return json.loads(spec_to_json(spec.as_dict()))


def _schedule_for_validation(spec: NormalisedSpec) -> dict[str, Any]:
    """The proposed schedule as fee34 expects to receive it.

    ``status`` is set to DRAFT so ``validate_schedule``'s status-vocabulary check
    runs against a real value rather than passing vacuously on a missing one.
    """
    return {**spec.schedule, "status": "DRAFT"}


def _validation_errors(spec: NormalisedSpec) -> list[dict[str, Any]]:
    """fee34's OWN errors, verbatim. See the module docstring."""
    errors = validate_schedule(
        _schedule_for_validation(spec),
        spec.tiers,
        exclusions=spec.exclusions or None,
    )
    return [e.as_dict() for e in errors]


async def _assemble(conn, org_id: str, spec: NormalisedSpec, fee_schedule_id: str | None):
    """Resolve references, apply them, diff, validate. The shared read path."""
    report = await resolve_spec_references(conn, org_id, spec)
    apply_resolutions(spec, report)
    diff = await build_diff(conn, org_id, spec, fee_schedule_id=fee_schedule_id)
    return {
        "spec": _spec_payload(spec),
        "references": report.as_dict(),
        "diff": diff,
        "validation_errors": _validation_errors(spec),
    }


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/conversation")
async def get_conversation(
    request: Request,
    conversation_id: str | None = Query(None),
    fee_schedule_id: str | None = Query(None),
) -> dict[str, Any]:
    """The advisor's drafting session. READ — ``view_portfolio``.

    Creating one is a write, so a view-only caller gets the existing session if
    there is one and an explicit null if there is not — never a silently created
    row under a permission they do not hold.
    """
    org_id, user_id, pool = await _gate(request, READ_PERMISSION)
    perms = await _permission_envelope(pool, user_id, org_id)
    async with pool.acquire() as conn:
        await ensure_user(conn, request)
        row = await _load_conversation(conn, org_id, user_id, conversation_id)
        if not row and perms["can_write"]:
            row = await _create_conversation(conn, org_id, user_id, fee_schedule_id)

    return {
        "conversation": {
            "id": str(row["id"]),
            "context": _parse_jsonb(row.get("context_ref"), {}),
            "turns": _parse_jsonb(row.get("messages"), []),
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        } if row else None,
        "permissions": perms,
        "vocabularies": _vocabularies(perms),
    }


@router.post("/propose")
async def propose(request: Request, body: ProposeBody) -> dict[str, Any]:
    """Natural language in, a resolved and validated FeeSpec out.

    WRITE — ``manage_billing``. It spends a model call and writes a turn.
    """
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)

    try:
        spec, raw = await propose_fee_spec(body.description, org_id=org_id)
    except FeeSpecError as exc:
        _raise_for(exc)

    async with pool.acquire() as conn:
        await ensure_user(conn, request)
        row = await _load_conversation(conn, org_id, user_id, body.conversation_id)
        if not row:
            row = await _create_conversation(conn, org_id, user_id, body.fee_schedule_id)
        conversation_id = str(row["id"])

        try:
            assembled = await _assemble(conn, org_id, spec, body.fee_schedule_id)
        except (FeeSpecError, FeeSpecResolutionError) as exc:
            _raise_for(exc)

        await _append_turn(conn, conversation_id, {
            "role": "user", "content": body.description,
        })
        await _append_turn(conn, conversation_id, {
            "role": "assistant",
            # The model's raw text is kept alongside the normalised spec. When a
            # field was discarded as ungrounded, "what did it actually say?" is
            # answerable from the row rather than only from a log line.
            "content": raw,
            "spec": assembled["spec"],
            "spec_version": FEE_SPEC_VERSION,
        })

    perms = await _permission_envelope(pool, user_id, org_id)
    return {
        "conversation_id": conversation_id,
        **assembled,
        "permissions": perms,
        "vocabularies": _vocabularies(perms),
    }


@router.post("/worked-example")
async def worked_example(request: Request, body: WorkedExampleBody) -> dict[str, Any]:
    """The figure. Computed by fee35, on a real account's real balances.

    READ — ``view_portfolio``. It writes nothing; an advisor who may see a
    schedule may see what it would charge.
    """
    org_id, user_id, pool = await _gate(request, READ_PERMISSION)

    try:
        payload, advisor_set = _reconstitute(body.spec)
        spec = normalise_fee_spec(payload, body.description, trusted_fields=advisor_set)
    except FeeSpecError as exc:
        _raise_for(exc)

    frequency = spec.schedule.get("billing_frequency")
    period_start, period_end = (body.period_start, body.period_end)
    if period_start is None or period_end is None:
        period_start, period_end = default_period(date.today(), frequency)

    async with pool.acquire() as conn:
        try:
            example = await compute_worked_example(
                conn, org_id, spec,
                period_start=period_start, period_end=period_end,
                household_id=body.household_id, account_id=body.account_id,
            )
        except WorkedExampleUnavailable as exc:
            _raise_for(exc)

    perms = await _permission_envelope(pool, user_id, org_id)
    return {
        "worked_example": example.as_dict(),
        "validation_errors": _validation_errors(spec),
        "permissions": perms,
    }


@router.post("/corrections")
async def corrections(request: Request, body: CorrectionsBody) -> dict[str, Any]:
    """Record what the advisor changed about the model's proposal.

    WRITE — ``manage_billing``. Correcting a proposal is part of authoring a fee
    arrangement; a view-only caller has nothing to correct.
    """
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)

    async with pool.acquire() as conn:
        await ensure_user(conn, request)
        owned = await _load_conversation(conn, org_id, user_id, body.conversation_id)
        if not owned:
            # 404 and not 403: naming which conversations exist in other orgs is
            # itself a cross-tenant disclosure.
            raise HTTPException(
                status_code=404,
                detail=f"conversation {body.conversation_id} not found in this organisation",
            )
        try:
            results = await log_fee_spec_corrections(
                conn, org_id=org_id, conversation_id=body.conversation_id,
                edits=body.edits, corrected_by=user_id,
                fee_schedule_id=body.fee_schedule_id,
            )
        except FeeSpecCorrectionError as exc:
            _raise_for(exc)

    return {
        "target_type": CORRECTION_TARGET_TYPE,
        "results": results,
        "logged": sum(1 for r in results if r["logged"]),
    }


@router.post("/save")
async def save(request: Request, body: SaveBody) -> dict[str, Any]:
    """fee34's gate, then fee34's writer. WRITE — ``manage_billing``.

    The refusal here is fee34's, produced by the same ``validate_schedule`` call
    the manual admin screen makes, and reaching the caller as the same 422 body.
    """
    org_id, user_id, pool = await _gate(request, WRITE_PERMISSION)

    try:
        payload, advisor_set = _reconstitute(body.spec)
        spec = normalise_fee_spec(payload, body.description, trusted_fields=advisor_set)
    except FeeSpecError as exc:
        _raise_for(exc)

    errors = _validation_errors(spec)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "schedule_invalid",
                "message": (
                    f"{len(errors)} rule(s) must be resolved before this schedule "
                    f"can be saved"
                ),
                "errors": errors,
            },
        )
    if not spec.is_priceable:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "schedule_incomplete",
                "message": "required fields are still unresolved",
                "errors": [
                    {"code": "field_unresolved", "field": u["field"], "message": u["reason"]}
                    for u in spec.unresolved
                    if u["field"] in REQUIRED_SCHEDULE_FIELDS
                ],
            },
        )

    definition = {k: v for k, v in spec.schedule.items() if k != "code"}
    async with pool.acquire() as conn:
        try:
            created = await create_schedule(
                conn, org_id, code=spec.schedule["code"], tiers=spec.tiers,
                created_by=user_id, **definition,
            )
        except (FeeScheduleInvalid, FeeScheduleError) as exc:
            _raise_for(exc)
        if body.conversation_id:
            await conn.execute(
                "UPDATE assistant_conversations "
                "SET context_ref = context_ref || $1::jsonb, updated_at = now() "
                "WHERE id = $2::uuid AND org_id = $3::uuid",
                json.dumps({"id": created["id"], "saved": True}),
                body.conversation_id, org_id,
            )

    return {"schedule": json.loads(json.dumps(created, default=str)), "status": "DRAFT"}


def _reconstitute(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """A spec that came back from a client, re-typed for normalisation.

    The client round-trips money as strings (that is how it was sent out), and
    ``normalise_fee_spec`` converts strings to Decimal — so nothing needs
    converting here. What this DOES is strip the derived keys the server added
    on the way out (``is_priceable``, ``discarded``, ``warnings``) so a client
    cannot assert them back. ``unresolved`` is deliberately KEPT: it is the
    model's own admission and dropping it would lose the reason a field is
    blank.

    Returns ``(spec, advisor_set)``. ``advisor_set`` names the fields the
    advisor typed themselves, which are exempt from the grounding check — see
    ``normalise_fee_spec``. Asserting a field there buys a client nothing it
    could not get by editing the field for real, and every request reaching
    this function has already passed a permission gate.
    """
    if not isinstance(payload, dict):
        raise FeeSpecShapeError("spec must be an object", field="spec")
    advisor_set = payload.get("advisor_set")
    if advisor_set is not None and not isinstance(advisor_set, list):
        raise FeeSpecShapeError(
            "advisor_set must be a list of field names", field="advisor_set"
        )
    spec = {
        k: v for k, v in payload.items()
        if k in ("schedule", "tiers", "exclusions", "discounts", "credits",
                 "references", "unresolved", "evidence", "notes")
    }
    return spec, [str(f) for f in (advisor_set or [])]
