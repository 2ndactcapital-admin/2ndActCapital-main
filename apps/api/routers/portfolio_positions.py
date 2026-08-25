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
from services.rbac import require_permission

router = APIRouter(tags=["portfolio-positions"])


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
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, READ_PERMISSION)

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

    result["vocabularies"] = {
        "authority": sorted(AUTHORITIES),
        "source_system": sorted(SOURCE_SYSTEMS),
        "ownership_basis": sorted(OWNERSHIP_BASES),
        "superseded": sorted(SUPERSEDED_FILTERS),
        "inline_editable": sorted(INLINE_EDITABLE_FIELDS),
        "editable": sorted(EDITABLE_FIELDS),
    }
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
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, READ_PERMISSION)

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
    return detail


@router.get("/portfolio/assets")
async def get_assets(
    request: Request,
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Tenant assets, for the create-position asset picker."""
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, READ_PERMISSION)

    async with pool.acquire() as conn:
        assets = await list_assets(conn, org_id=org_id, search=search, limit=limit)
    return {"count": len(assets), "assets": assets}


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
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, WRITE_PERMISSION)

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
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, WRITE_PERMISSION)

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

    detail["restated_from"] = str(position_id)
    return detail


__all__ = ["router"]
