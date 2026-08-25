"""REST endpoints for portfolio positions — the Positions grid's backend.

THE GAP THIS FILLS
──────────────────────────────────────────────────────────────────────────────
``services.portfolio_assets`` shipped in Portfolio A2 with, in its own words,
"no router and no UI". Every caller of ``create_position`` /
``record_transaction`` / ``resolve_current_value`` since then has been another
service or a verify script — nothing reachable over HTTP. These are the first
endpoints that expose that layer.

STANDING RULES, ENFORCED HERE
──────────────────────────────────────────────────────────────────────────────
``org_id`` comes from ``routers.entities.get_org_id`` (JWT claims) on every
route and is NEVER accepted from a request body or a path segment. The request
bodies below deliberately have no ``org_id`` field at all, so there is nothing
for a caller to send and nothing for a future edit to start trusting.

Monetary values arrive and leave as STRINGS and are converted with ``Decimal``,
never ``float``. Pydantic is configured to keep them that way: a ``float`` field
would round the value at the API boundary, before any of the careful Decimal
handling downstream ever saw it.

Reads require ``view_portfolio``; writes require ``manage_portfolio``. Both
names already exist in ``public.permissions`` — A2 recorded them precisely so
the first router would not invent new ones.

WHAT UX 4 CHANGED, AND WHY IT WAS A REAL HOLE
──────────────────────────────────────────────────────────────────────────────
UX 1 shipped these five endpoints already gated: every one of them called
``require_permission`` with the right constant, and a view-only caller was
correctly refused a write with a 403. That part was never broken.

What was missing is the half that makes the refusal legible BEFORE it happens.
The list endpoint published ``vocabularies.editable`` and
``vocabularies.inline_editable`` UNCONDITIONALLY — the full field list, to
every caller, regardless of permission — and published no ``permissions`` block
at all. ``PositionsGrid`` read those lists and rendered an editable taxonomy
picker and a reconciled checkbox for a caller who could not write, and
``PositionDetailPane`` rendered a Save button and a full form for the same
caller. Every one of those controls led to a 403 the user had no way to
anticipate.

So this router now does what ``routers.portfolio_securities`` has done since
UX 3: it resolves the caller's real permissions server-side and ships them with
the page, and it EMPTIES the editable lists for a caller without
``manage_portfolio``. The UI keeps no field list and no permission logic of its
own, so there is nothing on the client that can drift away from this answer.

The envelope is advisory to the client and binding on nobody. Every write
endpoint still re-checks, and ``verify_portfolioux4`` asserts the two
independently — a hidden control and a refused request are different claims,
and a screen that only did the first would look correct right up until somebody
used curl.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, field_validator

from routers.entities import get_org_id
from services.database import get_pool
from services.permissions import get_user_id
from services.portfolio_assets import (
    AUTHORITIES,
    OWNERSHIP_BASES,
    SOURCE_SYSTEMS,
    OwnershipBasisError,
    PortfolioError,
    create_position,
)
from services.portfolio_positions import (
    DEFAULT_LIMIT,
    EDITABLE_FIELDS,
    INLINE_EDITABLE_FIELDS,
    MAX_LIMIT,
    READ_PERMISSION,
    SUPERSEDED_FILTERS,
    WRITE_PERMISSION,
    get_position,
    list_assets,
    list_positions,
    taxonomy_labels,
    update_position,
)
from services.rbac import has_permission, is_super_admin, load_principal, require_permission

router = APIRouter(tags=["portfolio-positions"])


# ═══════════════════════════════════════════════════════════════════════════
# The gate, and what it publishes
# ═══════════════════════════════════════════════════════════════════════════


async def _tenant_gate(request: Request, permission: str) -> tuple[str, str, Any]:
    """Resolve ``(org_id, user_id, pool)`` and enforce a TENANT permission.

    ``rbac.require_permission`` raises 403 with the permission NAME in the
    detail, so a refused caller learns which grant they are missing rather than
    just that they were refused.

    Super Admin passes — checked FIRST inside ``rbac.has_permission``, ahead of
    any granular lookup. That is the codebase-wide escape-hatch convention
    (every RLS policy, ``restricted_access`` check and ``staff_visibility``
    gate carries the same explicit bypass) and not a special case introduced
    here. It is asserted on its own in ``verify_portfolioux4`` rather than
    inferred from the write tests passing: "an admin could write" and "a super
    admin bypassed the check" are different claims, and only the second one
    survives someone revoking ``manage_portfolio`` from ``super_admin``.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, permission)
    return org_id, user_id, pool


async def _permission_envelope(pool, user_id: str, org_id: str) -> dict[str, Any]:
    """What this caller may do, resolved server-side and shipped with the page.

    THE UI RENDERS A WRITE CONTROL ONLY WHEN THIS SAYS SO. The grid and the
    detail pane keep no permission logic and no field list of their own.
    """
    can_write = await has_permission(pool, user_id, org_id, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    super_admin = is_super_admin(principal)
    return {
        "can_read": True,          # this envelope is only built after the gate
        "can_write": bool(can_write),
        "is_super_admin": bool(super_admin),
        "read_permission": READ_PERMISSION,
        "write_permission": WRITE_PERMISSION,
    }


def _vocabularies(perms: dict[str, Any]) -> dict[str, Any]:
    """The page's vocabularies, with the editable lists cut to the caller.

    ``editable`` and ``inline_editable`` are EMPTY for a caller without
    ``manage_portfolio``. The read-only vocabularies (``authority``,
    ``source_system``, ``ownership_basis``, ``superseded``) are published to
    everyone — they are what the FILTER controls offer, and filtering is a read.
    """
    return {
        "authority": sorted(AUTHORITIES),
        "source_system": sorted(SOURCE_SYSTEMS),
        "ownership_basis": sorted(OWNERSHIP_BASES),
        "superseded": sorted(SUPERSEDED_FILTERS),
        "inline_editable": sorted(INLINE_EDITABLE_FIELDS) if perms["can_write"] else [],
        "editable": sorted(EDITABLE_FIELDS) if perms["can_write"] else [],
    }


# ── Money at the API boundary ───────────────────────────────────────────────

#: A monetary/quantity field on the wire.
MoneyIn = str | Decimal | int | None

#: The money/quantity fields, named once. Both models validate them and
#: ``PositionPatch.changes`` converts them, and a field that appeared in one
#: list but not the other would be silently exempt from the float refusal.
MONEY_FIELDS = (
    "quantity", "ownership_pct", "market_value", "market_value_native",
    "cost_basis", "accrued_income",
)


def _reject_float(value: Any) -> Any:
    """A ``mode='before'`` validator: refuse ``float``, never convert it.

    This has to run BEFORE Pydantic's own coercion, not after. ``Decimal`` is in
    the field's union and Pydantic will happily accept a float into it in lax
    mode — so a check written as an ``isinstance(value, float)`` further down
    the call chain is dead code that never fires, and the endpoint would look
    float-safe while accepting floats.

    ``bool`` is excluded from the refusal only because it is not a float; it is
    rejected by the union like any other wrong type.
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


class PositionCreate(BaseModel):
    """Body for POST /portfolio/positions. Note the absence of ``org_id``."""

    model_config = ConfigDict(extra="forbid")

    owner_entity_id: _uuid.UUID
    asset_id: _uuid.UUID
    as_of_date: date
    authority: str
    source_system: str
    ownership_basis: str | None = None
    quantity: MoneyIn = None
    ownership_pct: MoneyIn = None
    market_value: MoneyIn = None
    market_value_native: MoneyIn = None
    cost_basis: MoneyIn = None
    accrued_income: MoneyIn = None
    fx_rate_id: _uuid.UUID | None = None
    taxonomy_key: str | None = None
    is_reconciled: bool = False
    superseded_by_source: str | None = None

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

    @field_validator("ownership_basis")
    @classmethod
    def _check_basis(cls, v: str | None) -> str | None:
        if v is not None and v not in OWNERSHIP_BASES:
            raise ValueError(f"ownership_basis must be one of {sorted(OWNERSHIP_BASES)}")
        return v


class PositionPatch(BaseModel):
    """Body for PATCH /portfolio/positions/{id}.

    Every field defaults to a sentinel rather than ``None``, because ``None`` is
    a MEANINGFUL value here: an explicit ``null`` clears a measure, which is
    exactly what switching ownership basis requires. Pydantic's
    ``model_fields_set`` is what distinguishes "absent" from "explicitly null",
    so the sentinel never actually reaches the service.
    """

    model_config = ConfigDict(extra="forbid")

    as_of_date: date | None = None
    ownership_basis: str | None = None
    quantity: MoneyIn = None
    ownership_pct: MoneyIn = None
    market_value: MoneyIn = None
    market_value_native: MoneyIn = None
    cost_basis: MoneyIn = None
    accrued_income: MoneyIn = None
    authority: str | None = None
    source_system: str | None = None
    taxonomy_key: str | None = None
    is_reconciled: bool | None = None
    superseded_by_source: str | None = None

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

    @field_validator("ownership_basis")
    @classmethod
    def _check_basis(cls, v: str | None) -> str | None:
        if v is not None and v not in OWNERSHIP_BASES:
            raise ValueError(f"ownership_basis must be one of {sorted(OWNERSHIP_BASES)}")
        return v

    def changes(self) -> dict[str, Any]:
        """Only the fields the caller actually sent, money already Decimal'd."""
        out: dict[str, Any] = {}
        for name in self.model_fields_set:
            value = getattr(self, name)
            out[name] = (
                _money_or_none(value, name) if name in MONEY_FIELDS else value
            )
        return out


# ── Reads ───────────────────────────────────────────────────────────────────


@router.get("/portfolio/positions")
async def get_positions(
    request: Request,
    owner_entity_id: _uuid.UUID | None = Query(default=None),
    asset_id: _uuid.UUID | None = Query(default=None),
    taxonomy_key: str | None = Query(default=None),
    taxonomy_prefix: str | None = Query(
        default=None,
        description="Rolls a super/major-class filter up over its descendants.",
    ),
    source_system: str | None = Query(default=None),
    authority: str | None = Query(default=None),
    ownership_basis: str | None = Query(default=None),
    as_of_from: date | None = Query(default=None),
    as_of_to: date | None = Query(default=None),
    superseded: Literal["all", "winners", "losers"] = Query(default="all"),
    include_history: bool = Query(
        default=False,
        description="Include rows closed by a restatement. Off by default — a "
                    "grid showing both rows shows one holding twice.",
    ),
    search: str | None = Query(default=None),
    resolve_values: bool = Query(default=True),
    value_as_of: date | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    """The Positions grid's data. Org-scoped from JWT claims, always.

    Filters are applied in SQL rather than handed to the client whole, so the
    row cap bounds a FILTERED set. The grid then sorts and filters again on the
    loaded page for instant feedback — the server filter narrows what is
    loadable, the client filter narrows what is visible, and ``total`` reports
    the difference so a truncated page never looks complete.
    """
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)

    async with pool.acquire() as conn:
        try:
            result = await list_positions(
                conn,
                org_id=org_id,
                owner_entity_id=str(owner_entity_id) if owner_entity_id else None,
                asset_id=str(asset_id) if asset_id else None,
                taxonomy_key=taxonomy_key,
                taxonomy_prefix=taxonomy_prefix,
                source_system=source_system,
                authority=authority,
                ownership_basis=ownership_basis,
                as_of_from=as_of_from,
                as_of_to=as_of_to,
                superseded=superseded,
                include_history=include_history,
                search=search,
                resolve_values=resolve_values,
                value_as_of=value_as_of,
                limit=limit,
                offset=offset,
            )
            # Shipped with the page so the taxonomy cell can render a label AND
            # an inline reassignment picker without a second round-trip. Rule 1:
            # the vocabulary comes from config, never from the frontend.
            result["taxonomy"] = await taxonomy_labels(conn, org_id)
        except PortfolioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    perms = await _permission_envelope(pool, user_id, org_id)
    result["permissions"] = perms
    result["vocabularies"] = _vocabularies(perms)
    return result


@router.get("/portfolio/positions/{position_id}")
async def get_position_detail(
    request: Request,
    position_id: _uuid.UUID,
    value_as_of: date | None = Query(default=None),
):
    """Everything the right-hand detail pane shows, in one call.

    Position + asset + owner + resolved current value + the governing valuation
    that produced it + the asset's valuation history + the position's
    transaction history + the restatement chain. The pane opens on a row click;
    a request waterfall would render it in pieces.

    A 404 here means "not in your org" as well as "does not exist", and
    deliberately does not distinguish them — telling a caller that a position id
    exists somewhere else is itself a cross-tenant leak.
    """
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)

    async with pool.acquire() as conn:
        try:
            detail = await get_position(
                conn, org_id=org_id, position_id=str(position_id),
                value_as_of=value_as_of,
            )
        except PortfolioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="position not found")

    # The pane fetches this endpoint on its own and must not have to be told by
    # its parent what the caller may do — a prop threaded down from the grid is
    # one more place the answer could go stale.
    perms = await _permission_envelope(pool, user_id, org_id)
    detail["permissions"] = perms
    detail["vocabularies"] = _vocabularies(perms)
    return detail


@router.get("/portfolio/assets")
async def get_assets(
    request: Request,
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Tenant assets, for the create-position asset picker."""
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)

    async with pool.acquire() as conn:
        assets = await list_assets(conn, org_id=org_id, search=search, limit=limit)

    # A view-only caller may legitimately READ the picker's contents — it is an
    # asset list — but should never see a create form built on top of it. The
    # envelope rides along so the caller of this endpoint does not have to
    # cross-reference the positions list to find that out.
    perms = await _permission_envelope(pool, user_id, org_id)
    return {"count": len(assets), "assets": assets, "permissions": perms}


# ── Writes ──────────────────────────────────────────────────────────────────


@router.post("/portfolio/positions", status_code=201)
async def create_position_endpoint(request: Request, body: PositionCreate):
    """Create a position. Returns the new row in full.

    The ownership-basis contract is enforced by ``create_position``'s
    ``_validate_basis`` — not re-implemented here. A2 records that
    ``portfolio.positions`` has NO CHECK constraint covering it, so that
    function is the only backstop, and a second copy of the rule in this router
    would be a second thing to drift.

    ``OwnershipBasisError`` maps to 422 rather than 400: the body was
    well-formed and every field was individually valid — what failed was the
    relationship BETWEEN fields, which is what 422 means.
    """
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)

    async with pool.acquire() as conn:
        try:
            new_id = await create_position(
                conn,
                org_id=org_id,
                owner_entity_id=str(body.owner_entity_id),
                asset_id=str(body.asset_id),
                as_of_date=body.as_of_date,
                authority=body.authority,
                source_system=body.source_system,
                ownership_basis=body.ownership_basis,
                quantity=_money_or_none(body.quantity, "quantity"),
                ownership_pct=_money_or_none(body.ownership_pct, "ownership_pct"),
                market_value=_money_or_none(body.market_value, "market_value"),
                market_value_native=_money_or_none(
                    body.market_value_native, "market_value_native"
                ),
                cost_basis=_money_or_none(body.cost_basis, "cost_basis"),
                accrued_income=_money_or_none(body.accrued_income, "accrued_income"),
                fx_rate_id=str(body.fx_rate_id) if body.fx_rate_id else None,
                taxonomy_key=body.taxonomy_key,
                is_reconciled=body.is_reconciled,
                superseded_by_source=body.superseded_by_source,
            )
        except OwnershipBasisError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PortfolioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        detail = await get_position(conn, org_id=org_id, position_id=new_id)

    perms = await _permission_envelope(pool, user_id, org_id)
    detail["permissions"] = perms
    detail["vocabularies"] = _vocabularies(perms)
    return detail


@router.patch("/portfolio/positions/{position_id}")
async def patch_position(
    request: Request, position_id: _uuid.UUID, body: PositionPatch
):
    """Restate a position. **Returns a NEW position id.**

    CLAUDE.md Rule 3: the current row is closed (``valid_to = now()``) and a
    successor is inserted. Nothing is updated in place, so the previous state
    stays independently queryable and appears in the pane's restatement history.

    The response is the full detail of the SUCCESSOR, not a patch echo, because
    the caller's ``position_id`` is stale the moment this returns and a client
    that kept using it would be reading history. The grid swaps the row id from
    this response.
    """
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)

    changes = body.changes()
    if not changes:
        raise HTTPException(
            status_code=400,
            detail=(
                "no fields supplied. Send at least one of "
                f"{sorted(EDITABLE_FIELDS)}."
            ),
        )

    async with pool.acquire() as conn:
        try:
            new_id = await update_position(
                conn, org_id=org_id, position_id=str(position_id), changes=changes
            )
        except OwnershipBasisError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PortfolioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        detail = await get_position(conn, org_id=org_id, position_id=new_id)

    perms = await _permission_envelope(pool, user_id, org_id)
    detail["permissions"] = perms
    detail["vocabularies"] = _vocabularies(perms)
    detail["restated_from"] = str(position_id)
    return detail


__all__ = ["router"]
