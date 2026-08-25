"""REST endpoints for portfolio transactions — the Transactions grid's backend.

THE GAP THIS FILLS
──────────────────────────────────────────────────────────────────────────────
``services.portfolio_assets.record_transaction`` shipped in Portfolio A2 with no
HTTP surface. Its only callers since have been
``services.portfolio_corporate_actions`` and verify scripts — the same gap
Portfolio UX 1 found for positions, one table over. These are the first
endpoints that expose the ledger.

THE ONE THING THAT IS NOT LIKE THE POSITIONS ROUTER
──────────────────────────────────────────────────────────────────────────────
There is no ``PATCH``. ``portfolio.transactions`` is an append-only ledger —
nothing in the codebase has ever issued an ``UPDATE`` against it — so an edit is
a CORRECTION, and it is exposed as
``POST /portfolio/transactions/{id}/corrections``: a sub-resource that MINTS a
row, not a verb that implies mutating one. ``PATCH`` would name a semantics this
table does not have, and the URL is where a reader looks first.

The correction closes the original (``valid_to = now()``) and records a
successor pointing back at it. The original stays queryable and appears in the
pane's correction chain. See ``services.portfolio_transactions`` for why this,
rather than an offsetting reversal.

STANDING RULES, ENFORCED HERE
──────────────────────────────────────────────────────────────────────────────
``org_id`` comes from ``routers.entities.get_org_id`` (JWT claims) on every
route and is NEVER accepted from a request body or a path segment. The bodies
below have no ``org_id`` field at all, so there is nothing for a caller to send
and nothing for a future edit to start trusting.

Monetary values arrive and leave as STRINGS and are converted with ``Decimal``.
The float refusal runs ``mode="before"``, ahead of Pydantic's own coercion —
written any later it would be dead code, because ``Decimal`` is in the field
union and lax mode accepts a float into it happily.

Reads require ``view_portfolio``; writes require ``manage_portfolio``. Both
already exist in ``public.permissions``.

WHAT UX 4 CHANGED, AND WHY IT WAS A REAL HOLE
──────────────────────────────────────────────────────────────────────────────
UX 2 shipped all five endpoints already gated — ``require_permission`` with the
right constant on each, and a genuine 403 for a view-only caller attempting a
create or a correction. That half was never broken.

What was missing is the half that makes the refusal legible before it happens.
The list endpoint published ``vocabularies.correctable`` and
``.inline_correctable`` UNCONDITIONALLY and published no ``permissions`` block
at all, so ``TransactionsGrid`` rendered inline correction controls and
``TransactionDetailPane`` rendered a "Correct" button and a full form for a
caller who could not write. Every one led to a 403 nobody could anticipate.

The correction endpoint is the sharpest case and is called out on its own in
``verify_portfolioux4``: a view-only user MUST be able to read the correction
chain — that is history, and history is a read — and must never be able to add
to it. Those are two different assertions and the second does not follow from
the first.

Now, as in ``routers.portfolio_securities`` since UX 3, the caller's real
permissions are resolved server-side and shipped with the page, and the
correctable lists are EMPTY without ``manage_portfolio``. The envelope is
advisory to the client and binding on nobody: every write endpoint re-checks,
and the verify script asserts the two independently.

TWO FIELDS THE API DELIBERATELY DOES NOT ACCEPT
──────────────────────────────────────────────────────────────────────────────
``corporate_action_id`` and ``is_corporate_action_adjustment`` are not settable
on create and not correctable. Together they are Phase F's idempotency key: a
hand-written row claiming a corporate action would make
``already_applied_transactions`` believe an action had been applied that never
was, and the position would silently miss its adjustment. Adjustments are
recorded by ``portfolio_corporate_actions.apply_corporate_action``, which is the
only thing that knows the event actually happened.

``related_transaction_id`` is not settable on create either. Within this API it
means exactly one thing — "the row this row corrects" — and that is only true if
nothing else writes it.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, field_validator

from routers.entities import get_org_id
from services.database import get_pool
from services.permissions import get_user_id
from services.portfolio_assets import (
    AUTHORITIES,
    SOURCE_SYSTEMS,
    PortfolioError,
    TransactionMarketError,
    record_transaction,
)
from services.portfolio_transactions import (
    CORRECTABLE_FIELDS,
    DEFAULT_LIMIT,
    INLINE_CORRECTABLE_FIELDS,
    MAX_LIMIT,
    MONEY_FIELDS,
    READ_PERMISSION,
    WRITE_PERMISSION,
    correct_transaction,
    get_transaction,
    list_positions_for_picker,
    list_transactions,
    transaction_types,
)
from services.rbac import has_permission, is_super_admin, load_principal, require_permission

router = APIRouter(tags=["portfolio-transactions"])


# ═══════════════════════════════════════════════════════════════════════════
# The gate, and what it publishes
# ═══════════════════════════════════════════════════════════════════════════


async def _tenant_gate(request: Request, permission: str) -> tuple[str, str, Any]:
    """Resolve ``(org_id, user_id, pool)`` and enforce a TENANT permission.

    ``rbac.require_permission`` raises 403 with the permission NAME in the
    detail. Super Admin passes — checked FIRST inside ``rbac.has_permission``,
    ahead of any granular lookup, which is the codebase-wide escape-hatch
    convention rather than a special case introduced here.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, permission)
    return org_id, user_id, pool


async def _permission_envelope(pool, user_id: str, org_id: str) -> dict[str, Any]:
    """What this caller may do, resolved server-side and shipped with the page.

    ``can_correct`` is published as its own key even though it is currently
    identical to ``can_write``. Creating a ledger entry and correcting an
    existing one are different acts on different rows — a firm that later wants
    to let operations record entries but only a supervisor amend them changes
    THIS function and nothing on the client, because the pane already reads the
    two keys separately.
    """
    can_write = bool(await has_permission(pool, user_id, org_id, WRITE_PERMISSION))
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    super_admin = is_super_admin(principal)
    return {
        "can_read": True,          # this envelope is only built after the gate
        "can_write": can_write,
        "can_correct": can_write,
        "is_super_admin": bool(super_admin),
        "read_permission": READ_PERMISSION,
        "write_permission": WRITE_PERMISSION,
    }


def _vocabularies(perms: dict[str, Any], types: list[dict[str, Any]]) -> dict[str, Any]:
    """The page's vocabularies, with the correctable lists cut to the caller.

    ``correctable`` and ``inline_correctable`` are EMPTY without
    ``manage_portfolio``. The rest — authority, source system, the type codes
    and categories — are published to everyone, because they are what the FILTER
    controls and the type LABELS are built from, and both of those are reads.
    """
    return {
        "authority": sorted(AUTHORITIES),
        "source_system": sorted(SOURCE_SYSTEMS),
        "transaction_type_code": [t["code"] for t in types],
        "transaction_type_category": sorted({t["category"] for t in types}),
        "inline_correctable": (
            sorted(INLINE_CORRECTABLE_FIELDS) if perms["can_correct"] else []
        ),
        "correctable": sorted(CORRECTABLE_FIELDS) if perms["can_correct"] else [],
    }


# ── Money at the API boundary ───────────────────────────────────────────────

#: A monetary/quantity field on the wire.
MoneyIn = str | Decimal | int | None


def _reject_float(value: Any) -> Any:
    """A ``mode='before'`` validator: refuse ``float``, never convert it.

    Has to run BEFORE Pydantic's own coercion. ``Decimal`` is in the field union
    and Pydantic accepts a float into it in lax mode, so an ``isinstance(...,
    float)`` check further down the call chain is dead code that never fires and
    the endpoint would LOOK float-safe while accepting floats.
    """
    if isinstance(value, float):
        raise ValueError(
            "monetary and quantity values must be sent as JSON STRINGS "
            '(e.g. "1234.56"), not as JSON numbers with a decimal point. A '
            "float has already lost precision by the time this endpoint sees "
            "it, and nothing downstream can tell the error from a real figure."
        )
    return value


def _money_or_none(value: MoneyIn, field: str) -> Decimal | None:
    """Parse an inbound monetary value into an exact Decimal, or None."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"{field} is not a valid decimal: {value!r}"
        ) from exc


class TransactionCreate(BaseModel):
    """Body for POST /portfolio/transactions. Note the absence of ``org_id``.

    Also absent, on purpose: ``corporate_action_id``,
    ``is_corporate_action_adjustment`` and ``related_transaction_id``. See the
    module docstring.
    """

    model_config = ConfigDict(extra="forbid")

    position_id: _uuid.UUID
    transaction_type_code: str
    trade_date: date
    authority: str
    source_system: str
    settle_date: date | None = None
    quantity: MoneyIn = None
    price: MoneyIn = None
    gross_amount: MoneyIn = None
    fees: MoneyIn = None
    taxes: MoneyIn = None
    net_amount: MoneyIn = None
    currency_code: str | None = None
    fx_rate_id: _uuid.UUID | None = None
    external_ref: str | None = None

    _no_floats = field_validator(*MONEY_FIELDS, mode="before")(_reject_float)

    @field_validator("authority")
    @classmethod
    def _check_authority(cls, v: str) -> str:
        if v not in AUTHORITIES:
            raise ValueError(f"authority must be one of {sorted(AUTHORITIES)}")
        return v

    @field_validator("source_system")
    @classmethod
    def _check_source(cls, v: str) -> str:
        if v not in SOURCE_SYSTEMS:
            raise ValueError(f"source_system must be one of {sorted(SOURCE_SYSTEMS)}")
        return v


class TransactionCorrection(BaseModel):
    """Body for POST /portfolio/transactions/{id}/corrections.

    Every field defaults to ``None`` and ``model_fields_set`` is what
    distinguishes "absent" from "explicitly null", because ``None`` is a
    MEANINGFUL value here: an explicit ``null`` clears a figure, and "the fee
    was never real" is a different correction from "the fee was zero".
    """

    model_config = ConfigDict(extra="forbid")

    transaction_type_code: str | None = None
    trade_date: date | None = None
    settle_date: date | None = None
    quantity: MoneyIn = None
    price: MoneyIn = None
    gross_amount: MoneyIn = None
    fees: MoneyIn = None
    taxes: MoneyIn = None
    net_amount: MoneyIn = None
    currency_code: str | None = None
    fx_rate_id: _uuid.UUID | None = None
    authority: str | None = None
    source_system: str | None = None
    external_ref: str | None = None

    _no_floats = field_validator(*MONEY_FIELDS, mode="before")(_reject_float)

    @field_validator("authority")
    @classmethod
    def _check_authority(cls, v: str | None) -> str | None:
        if v is not None and v not in AUTHORITIES:
            raise ValueError(f"authority must be one of {sorted(AUTHORITIES)}")
        return v

    @field_validator("source_system")
    @classmethod
    def _check_source(cls, v: str | None) -> str | None:
        if v is not None and v not in SOURCE_SYSTEMS:
            raise ValueError(f"source_system must be one of {sorted(SOURCE_SYSTEMS)}")
        return v

    def changes(self) -> dict[str, Any]:
        """Only the fields the caller actually sent, money already Decimal'd."""
        out: dict[str, Any] = {}
        for name in self.model_fields_set:
            value = getattr(self, name)
            if name in MONEY_FIELDS:
                out[name] = _money_or_none(value, name)
            elif name == "fx_rate_id":
                out[name] = str(value) if value else None
            else:
                out[name] = value
        return out


# The model's fields and the service's ``CORRECTABLE_FIELDS`` must be the SAME
# set, and this raises at import if they drift. Without it the two lists could
# disagree in either direction and neither failure would be loud: a field added
# to the model but not to the service would 400 at runtime with a confusing
# message, and a field added to the service but not the model would be silently
# unreachable — an "editable" field nobody can edit, which is the kind of gap
# that reads as working.
_MODEL_FIELDS = frozenset(TransactionCorrection.model_fields)
if _MODEL_FIELDS != CORRECTABLE_FIELDS:  # pragma: no cover — import-time guard
    raise RuntimeError(
        "TransactionCorrection and services.portfolio_transactions."
        "CORRECTABLE_FIELDS have drifted apart: "
        f"model-only={sorted(_MODEL_FIELDS - CORRECTABLE_FIELDS)} "
        f"service-only={sorted(CORRECTABLE_FIELDS - _MODEL_FIELDS)}"
    )


# ── Reads ───────────────────────────────────────────────────────────────────


@router.get("/portfolio/transactions")
async def get_transactions(
    request: Request,
    position_id: _uuid.UUID | None = Query(default=None),
    asset_id: _uuid.UUID | None = Query(default=None),
    owner_entity_id: _uuid.UUID | None = Query(default=None),
    transaction_type_code: str | None = Query(default=None),
    transaction_type_category: str | None = Query(default=None),
    trade_from: date | None = Query(default=None),
    trade_to: date | None = Query(default=None),
    is_corporate_action_adjustment: bool | None = Query(
        default=None,
        description="Tri-state. Unset returns both kinds; false is the "
                    "realized-gain population, which is a different question "
                    "from 'no filter'.",
    ),
    source_system: str | None = Query(default=None),
    authority: str | None = Query(default=None),
    include_history: bool = Query(
        default=False,
        description="Include entries closed by a correction. Off by default — "
                    "a grid showing both rows shows one ledger entry twice and "
                    "doubles the net-amount column.",
    ),
    search: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    """The Transactions grid's data. Org-scoped from JWT claims, always.

    Filters are applied in SQL rather than handed to the client whole, so the
    row cap bounds a FILTERED set. The grid then sorts and filters again on the
    loaded page for instant feedback, and ``total`` reports the difference so a
    truncated page never looks complete.
    """
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)

    async with pool.acquire() as conn:
        try:
            result = await list_transactions(
                conn,
                org_id=org_id,
                position_id=str(position_id) if position_id else None,
                asset_id=str(asset_id) if asset_id else None,
                owner_entity_id=str(owner_entity_id) if owner_entity_id else None,
                transaction_type_code=transaction_type_code,
                transaction_type_category=transaction_type_category,
                trade_from=trade_from,
                trade_to=trade_to,
                is_corporate_action_adjustment=is_corporate_action_adjustment,
                source_system=source_system,
                authority=authority,
                include_history=include_history,
                search=search,
                limit=limit,
                offset=offset,
            )
            # Shipped with the page so the type column renders a LABEL and the
            # type filter offers real codes without a second round-trip.
            # Rule 1: the vocabulary comes from the database, never the
            # frontend.
            types = await transaction_types(conn)
        except PortfolioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    perms = await _permission_envelope(pool, user_id, org_id)
    result["transaction_types"] = types
    result["permissions"] = perms
    result["vocabularies"] = _vocabularies(perms, types)
    return result


@router.get("/portfolio/transactions/{transaction_id}")
async def get_transaction_detail(request: Request, transaction_id: _uuid.UUID):
    """Everything the right-hand detail pane shows, in one call.

    The transaction, the position it belongs to (with the id the pane should
    link through to on the Positions screen, which is frequently NOT the id the
    entry is attached to), and the full correction chain.

    A 404 here means "not in your org" as well as "does not exist", and
    deliberately does not distinguish them — telling a caller that a transaction
    id exists somewhere else is itself a cross-tenant leak.
    """
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)

    async with pool.acquire() as conn:
        try:
            detail = await get_transaction(
                conn, org_id=org_id, transaction_id=str(transaction_id)
            )
            types = await transaction_types(conn)
        except PortfolioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="transaction not found")

    # The correction chain in this response is READ data and is returned to
    # every caller who passed the read gate. What the envelope decides is
    # whether the pane offers to ADD to it.
    perms = await _permission_envelope(pool, user_id, org_id)
    detail["permissions"] = perms
    detail["vocabularies"] = _vocabularies(perms, types)
    return detail


@router.get("/portfolio/transaction-positions")
async def get_positions_for_picker(
    request: Request,
    owner_entity_id: _uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Current positions, for the "record against…" picker on the create form.

    A distinct path from ``/portfolio/positions`` rather than a flag on it: this
    returns a deliberately thin row and skips the per-asset valuation
    resolution, which is that endpoint's expensive part and which a picker does
    not need.
    """
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)

    async with pool.acquire() as conn:
        positions = await list_positions_for_picker(
            conn, org_id=org_id,
            owner_entity_id=str(owner_entity_id) if owner_entity_id else None,
            search=search, limit=limit,
        )

    perms = await _permission_envelope(pool, user_id, org_id)
    return {"count": len(positions), "positions": positions, "permissions": perms}


# ── Writes ──────────────────────────────────────────────────────────────────


@router.post("/portfolio/transactions", status_code=201)
async def create_transaction_endpoint(request: Request, body: TransactionCreate):
    """Record a transaction. Returns the new entry in full.

    Phase E's market-compatibility check and the type existence/``is_active``
    check are enforced by ``record_transaction`` — not re-implemented here. A
    second copy of either rule in this router would be a second thing to drift,
    and A2's docstring records that ``record_transaction`` is the ONLY place
    checking the type's market against the asset's.

    ``TransactionMarketError`` maps to 422 rather than 400: the body was
    well-formed and every field was individually valid — what failed was the
    relationship between the type and the position's asset, which is what 422
    means.
    """
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)

    async with pool.acquire() as conn:
        try:
            new_id = await record_transaction(
                conn,
                org_id=org_id,
                position_id=str(body.position_id),
                transaction_type_code=body.transaction_type_code,
                trade_date=body.trade_date,
                authority=body.authority,
                source_system=body.source_system,
                settle_date=body.settle_date,
                quantity=_money_or_none(body.quantity, "quantity"),
                price=_money_or_none(body.price, "price"),
                gross_amount=_money_or_none(body.gross_amount, "gross_amount"),
                fees=_money_or_none(body.fees, "fees"),
                taxes=_money_or_none(body.taxes, "taxes"),
                net_amount=_money_or_none(body.net_amount, "net_amount"),
                currency_code=body.currency_code,
                fx_rate_id=str(body.fx_rate_id) if body.fx_rate_id else None,
                external_ref=body.external_ref,
            )
        except TransactionMarketError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PortfolioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        detail = await get_transaction(conn, org_id=org_id, transaction_id=new_id)
        types = await transaction_types(conn)

    perms = await _permission_envelope(pool, user_id, org_id)
    detail["permissions"] = perms
    detail["vocabularies"] = _vocabularies(perms, types)
    return detail


@router.post("/portfolio/transactions/{transaction_id}/corrections",
             status_code=201)
async def correct_transaction_endpoint(
    request: Request, transaction_id: _uuid.UUID, body: TransactionCorrection
):
    """Correct a transaction. **Returns a NEW transaction id.**

    201, not 200, and a sub-resource rather than a ``PATCH``: this CREATES a
    row. The original is closed (``valid_to = now()``) and stays independently
    queryable — it is what the pane's correction chain shows — and the successor
    points back at it.

    The response is the full detail of the SUCCESSOR, not a patch echo, because
    the caller's ``transaction_id`` is stale the moment this returns and a
    client that kept using it would be reading history. The grid swaps the row
    id from this response.
    """
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)

    changes = body.changes()
    if not changes:
        raise HTTPException(
            status_code=400,
            detail=(
                "no fields supplied. Send at least one of "
                f"{sorted(CORRECTABLE_FIELDS)}."
            ),
        )

    async with pool.acquire() as conn:
        try:
            new_id = await correct_transaction(
                conn, org_id=org_id, transaction_id=str(transaction_id),
                changes=changes,
            )
        except TransactionMarketError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PortfolioError as exc:
            # A field outside CORRECTABLE_FIELDS is a 422: well-formed body,
            # legal values, wrong field for this operation. Everything else the
            # service refuses (a stale row, a missing position) is a 400.
            status = 422 if "not correctable" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc

        detail = await get_transaction(conn, org_id=org_id, transaction_id=new_id)
        types = await transaction_types(conn)

    perms = await _permission_envelope(pool, user_id, org_id)
    detail["permissions"] = perms
    detail["vocabularies"] = _vocabularies(perms, types)
    detail["corrected_from"] = str(transaction_id)
    return detail


__all__ = ["router"]
