"""REST endpoints for the Securities & Assets screen — Portfolio UX 3.

TWO BOUNDARIES, TWO PATHS, ONE FILE
──────────────────────────────────────────────────────────────────────────────
``/portfolio/securities*``        — the TENANT surface. Org-scoped from JWT
                                    claims, gated on ``view_portfolio`` /
                                    ``manage_portfolio``, exactly as the
                                    Positions and Transactions routers are.

``/portfolio/global-securities*`` — the PLATFORM surface. Reads are open (the
                                    deployed RLS on those tables is
                                    ``SELECT USING (true)`` — there is no
                                    ``org_id`` to scope by), writes are Super
                                    Admin only.

They are separate paths rather than a flag on one path, and separate paths
rather than one path with two gates, because the two surfaces answer to
different authorities and a caller has to be able to tell which one they are
talking to from the URL alone.

THE GAP THIS FILLS
──────────────────────────────────────────────────────────────────────────────
``portfolio.assets`` had exactly ONE endpoint before this file:
``GET /portfolio/assets`` in ``routers.portfolio_positions`` — a nine-column
picker for the create-position form, with no global-security join, no detail,
no create and no edit. It is left exactly as it is; UX 1 depends on its shape.

``portfolio.securities_global`` and its identifier / price / relationship
satellites had NO REST surface at all. ``services.securities_global`` shipped in
Portfolio A1 and its only HTTP-adjacent consumer since has been
``routers.pricing_admin``, which touches ``securities_global_note_terms`` and
the relationship queue — never the security rows, identifiers or prices
themselves. These are the first endpoints that expose the master.

WHY THE GLOBAL GATE IS CHECKED IN THREE PLACES AND THAT IS NOT REDUNDANT
──────────────────────────────────────────────────────────────────────────────
1. :func:`_require_super_admin_actor` here — ``rbac.load_principal`` +
   ``rbac.is_super_admin`` → **403**. The same helper shape
   ``routers.pricing_admin`` already uses. This is the layer that produces a
   legible refusal.
2. ``securities_global._require_super_admin`` in the service — raises
   ``SecuritiesGlobalPermissionError``. This is what protects the service from a
   FUTURE caller that is not this router.
3. The RLS policies on the four global tables. This is the layer that does not
   depend on anyone remembering.

Layer 3 has a caveat that is worth writing down rather than discovering: the
application connects as ``postgres``, which carries ``rolbypassrls``, so in
production layers 1 and 2 are the operative gates and the policies are the
backstop that catches a direct or mis-roled connection. ``verify_portfolioux3``
exercises layer 3 under a real, non-bypassing ``app_service`` connection for
exactly that reason — asserting it under the app's own connection would prove
nothing.

STANDING RULES, ENFORCED HERE
──────────────────────────────────────────────────────────────────────────────
``org_id`` comes from ``routers.entities.get_org_id`` (JWT claims) on every
tenant route and is NEVER accepted from a request body or a path segment. The
bodies below have no ``org_id`` field, so there is nothing for a caller to send
and nothing for a future edit to start trusting.

Monetary values arrive and leave as STRINGS and are converted with ``Decimal``.
The float refusal runs ``mode="before"``, ahead of Pydantic's own coercion —
written any later it is dead code, because ``Decimal`` is in the field union and
lax mode accepts a float into it happily.
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
    ASSET_CLASSES,
    OWNERSHIP_BASES,
    VALUATION_METHODS,
    PortfolioError,
)
from services.portfolio_securities import (
    DEFAULT_LIMIT,
    GLOBAL_SOURCED_FIELDS,
    INLINE_EDITABLE_FIELDS,
    LINK_FILTERS,
    MAX_LIMIT,
    ORG_EDITABLE_FIELDS,
    READ_PERMISSION,
    WRITE_PERMISSION,
    GlobalFieldError,
    asset_version_history,
    create_tenant_asset,
    get_asset,
    get_global_security,
    list_assets,
    list_global_securities,
    taxonomy_labels,
    update_asset,
)
from services.rbac import has_permission, is_super_admin, load_principal, require_permission
from services.securities_global import (
    IDENTIFIER_TYPES as GLOBAL_IDENTIFIER_TYPES,
    PRICE_COVERAGES,
    PRICE_TYPES,
    SECURITY_EDITABLE_FIELDS,
    SECURITY_TYPES,
    SecuritiesGlobalError,
    SecuritiesGlobalPermissionError,
    StructuredNotePricingError,
    add_identifier,
    add_price,
    create_security,
    update_security,
)

router = APIRouter(tags=["portfolio-securities"])


# ── Money at the API boundary ───────────────────────────────────────────────

MoneyIn = str | Decimal | int | None


def _reject_float(value: Any) -> Any:
    """A ``mode='before'`` validator: refuse ``float``, never convert it."""
    if isinstance(value, float):
        raise ValueError(
            "monetary values must be sent as JSON STRINGS (e.g. \"1234.56\"), "
            "not as JSON numbers with a decimal point. A float has already lost "
            "precision by the time this endpoint sees it, and nothing "
            "downstream can tell the error from a real figure."
        )
    return value


def _money(value: MoneyIn, field: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"{field} is not a valid decimal: {value!r}"
        ) from exc


# ═══════════════════════════════════════════════════════════════════════════
# The two gates
# ═══════════════════════════════════════════════════════════════════════════


async def _tenant_gate(request: Request, permission: str) -> tuple[str, str, Any]:
    """Resolve ``(org_id, user_id, pool)`` and enforce a TENANT permission.

    ``rbac.require_permission`` raises 403 with the permission name in the
    detail. Super Admin passes it — checked FIRST inside ``has_permission``,
    which is the codebase-wide escape-hatch convention and not a special case
    introduced here.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, permission)
    return org_id, user_id, pool


async def _require_super_admin_actor(request: Request) -> tuple[str, Any]:
    """Reject anyone who is not a Super Admin. Returns ``(user_id, pool)``.

    Shaped exactly after ``routers.pricing_admin._require_super_admin``. It
    resolves no ``org_id`` and returns none: there is no org in play on the
    global master, and inventing one to match a helper signature would be the
    kind of quiet dishonesty that later reads as a real scoping rule.

    403, never 404. Hiding the endpoint from a non-super-admin would mean a
    tenant admin who guessed the URL could not tell "you may not" from "this
    does not exist", and the first is the answer they need.
    """
    pool = await get_pool()
    user_id = get_user_id(request)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    if not is_super_admin(principal):
        raise HTTPException(
            status_code=403,
            detail=(
                "Super Admin access required. portfolio.securities_global is "
                "the platform-wide security master, shared by every tenant — "
                "there is no org-scoped write path to fall back to. Correct "
                "your own asset instead: PATCH /portfolio/securities/{id}."
            ),
        )
    return user_id, pool


async def _permission_envelope(pool, user_id: str, org_id: str) -> dict[str, Any]:
    """What this caller may do, resolved server-side and shipped with the page.

    THE UI RENDERS A CONTROL ONLY WHEN THIS SAYS SO. That is the whole
    mechanism behind "a view-only user sees the grid but no write controls" and
    "the UI does not even render controls for global-sourced fields" — the
    component keeps no permission logic and no field list of its own, so there
    is nothing on the client that can drift away from the server's answer.

    It is emphatically NOT the enforcement. Every write endpoint re-checks, and
    ``verify_portfolioux3`` asserts the two independently: a hidden control and
    a refused request are different claims, and a screen that only did the first
    would look correct right up until somebody used curl.

    ``editable`` and ``inline_editable`` are EMPTY for a caller without
    ``manage_portfolio``. ``global_fields`` is never empty and is never a subset
    of ``editable`` for anyone, super admin included — the global write path is
    a different endpoint, and a field writable from two places under two rules
    is the bug the whole ``global_`` prefix exists to prevent.
    """
    can_write = await has_permission(pool, user_id, org_id, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    super_admin = is_super_admin(principal)
    return {
        "can_read": True,          # this envelope is only built after the gate
        "can_write": bool(can_write),
        "can_write_global": bool(super_admin),
        "is_super_admin": bool(super_admin),
        "read_permission": READ_PERMISSION,
        "write_permission": WRITE_PERMISSION,
    }


def _vocabularies(perms: dict[str, Any], taxonomy: dict[str, str]) -> dict[str, Any]:
    editable = sorted(ORG_EDITABLE_FIELDS) if perms["can_write"] else []
    inline = sorted(INLINE_EDITABLE_FIELDS) if perms["can_write"] else []
    return {
        "asset_class": sorted(ASSET_CLASSES),
        "ownership_basis": sorted(OWNERSHIP_BASES),
        "valuation_method": sorted(VALUATION_METHODS),
        "security_type": sorted(SECURITY_TYPES),
        "price_coverage": sorted(PRICE_COVERAGES),
        "identifier_type": sorted(GLOBAL_IDENTIFIER_TYPES),
        "linked": sorted(LINK_FILTERS),
        "editable": editable,
        "inline_editable": inline,
        # Read-only for EVERY caller on this screen. Published so the pane can
        # mark them "platform-sourced" from the server's own list rather than a
        # hardcoded one.
        "global_fields": sorted(GLOBAL_SOURCED_FIELDS),
        "global_editable": sorted(SECURITY_EDITABLE_FIELDS),
        "taxonomy": taxonomy,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Request models
# ═══════════════════════════════════════════════════════════════════════════


class AssetCreate(BaseModel):
    """Body for POST /portfolio/securities. Note the absence of ``org_id``."""

    model_config = ConfigDict(extra="forbid")

    name: str
    asset_type: str
    asset_class: str = "financial"
    ownership_basis: str = "units"
    valuation_method: str = "market_price"
    short_name: str | None = None
    # The LINK, settable here and only here. Not the security's attributes —
    # those are platform data and this endpoint cannot write them at all.
    global_security_id: _uuid.UUID | None = None
    default_taxonomy_key: str | None = None
    currency_code: str | None = None
    inception_date: date | None = None
    maturity_date: date | None = None
    include_in_performance: bool = True

    @field_validator("asset_class")
    @classmethod
    def _check_class(cls, v: str) -> str:
        if v not in ASSET_CLASSES:
            raise ValueError(f"asset_class must be one of {sorted(ASSET_CLASSES)}")
        return v

    @field_validator("ownership_basis")
    @classmethod
    def _check_basis(cls, v: str) -> str:
        if v not in OWNERSHIP_BASES:
            raise ValueError(f"ownership_basis must be one of {sorted(OWNERSHIP_BASES)}")
        return v

    @field_validator("valuation_method")
    @classmethod
    def _check_method(cls, v: str) -> str:
        if v not in VALUATION_METHODS:
            raise ValueError(
                f"valuation_method must be one of {sorted(VALUATION_METHODS)}"
            )
        return v


class AssetPatch(BaseModel):
    """Body for PATCH /portfolio/securities/{asset_id}.

    ``extra="allow"``, deliberately, and it is the one place in this file that
    diverges from the UX 1 / UX 2 models.

    With ``extra="forbid"`` a caller sending ``{"security_type": "equity"}``
    would be refused by PYDANTIC, as a 422 shape error, before the request ever
    reached the code that knows what ``security_type`` actually is. That is the
    wrong refusal for the right request: the field exists, the value is legal,
    and what is missing is Super Admin. It would also mean the boundary was
    enforced in two places — a Pydantic model and a service field set — that
    could drift apart, with the drift showing up as a confusing error message
    rather than a failing test.

    So every unknown key is accepted here and handed to
    ``portfolio_securities.update_asset``, which owns the ONLY copy of the two
    field sets and answers 403 for a platform field and 422 for genuine junk.
    """

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    short_name: str | None = None
    asset_type: str | None = None
    asset_class: str | None = None
    ownership_basis: str | None = None
    valuation_method: str | None = None
    default_taxonomy_key: str | None = None
    currency_code: str | None = None
    include_in_performance: bool | None = None
    inception_date: date | None = None
    maturity_date: date | None = None
    is_active: bool | None = None

    def changes(self) -> dict[str, Any]:
        """Only the keys the caller actually sent — declared AND extra.

        ``model_fields_set`` is what distinguishes "absent" from "explicitly
        null", because ``None`` is MEANINGFUL here: an explicit null clears
        ``short_name`` or ``maturity_date``, and "this asset has no maturity" is
        a different statement from "leave the maturity alone".
        """
        extra = self.model_extra or {}
        out: dict[str, Any] = {}
        for name in set(self.model_fields_set) | set(extra):
            out[name] = extra[name] if name in extra else getattr(self, name)
        return out


# The declared fields must be exactly the org-editable set, and this raises at
# IMPORT if they drift. Without it the two lists could disagree in either
# direction and neither failure would be loud: a field on the model but not in
# the service would 422 at runtime with a confusing message, and a field in the
# service but not on the model would still work (extra="allow") while being
# invisible in the OpenAPI schema — an editable field nobody can discover.
_MODEL_FIELDS = frozenset(AssetPatch.model_fields)
if _MODEL_FIELDS != ORG_EDITABLE_FIELDS:  # pragma: no cover — import-time guard
    raise RuntimeError(
        "AssetPatch and services.portfolio_securities.ORG_EDITABLE_FIELDS have "
        f"drifted: model-only={sorted(_MODEL_FIELDS - ORG_EDITABLE_FIELDS)} "
        f"service-only={sorted(ORG_EDITABLE_FIELDS - _MODEL_FIELDS)}"
    )

# The org-editable set and the platform-sourced set must never intersect. If
# they ever did, one field would be writable by an org admin AND marked
# read-only-platform in the same response, and which one won would depend on
# check order. Asserted at import rather than in a test, because the failure is
# a permission hole and not a broken feature.
_OVERLAP = ORG_EDITABLE_FIELDS & GLOBAL_SOURCED_FIELDS
if _OVERLAP:  # pragma: no cover — import-time guard
    raise RuntimeError(
        f"ORG_EDITABLE_FIELDS and GLOBAL_SOURCED_FIELDS overlap on "
        f"{sorted(_OVERLAP)} — a field cannot be both org-editable and "
        f"platform-sourced-read-only."
    )


class GlobalSecurityCreate(BaseModel):
    """Body for POST /portfolio/global-securities. **Super Admin only.**"""

    model_config = ConfigDict(extra="forbid")

    name: str
    security_type: str
    short_name: str | None = None
    currency_code: str | None = None
    price_coverage: str = "unknown"

    @field_validator("security_type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in SECURITY_TYPES:
            raise ValueError(f"security_type must be one of {sorted(SECURITY_TYPES)}")
        return v

    @field_validator("price_coverage")
    @classmethod
    def _check_coverage(cls, v: str) -> str:
        if v not in PRICE_COVERAGES:
            raise ValueError(f"price_coverage must be one of {sorted(PRICE_COVERAGES)}")
        return v


class GlobalSecurityPatch(BaseModel):
    """Body for PATCH /portfolio/global-securities/{id}. **Super Admin only.**"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    short_name: str | None = None
    security_type: str | None = None
    currency_code: str | None = None
    price_coverage: str | None = None

    def changes(self) -> dict[str, Any]:
        return {n: getattr(self, n) for n in self.model_fields_set}


class GlobalIdentifierCreate(BaseModel):
    """Body for POST /portfolio/global-securities/{id}/identifiers."""

    model_config = ConfigDict(extra="forbid")

    id_type: str
    id_value: str
    is_primary: bool = False

    @field_validator("id_type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in GLOBAL_IDENTIFIER_TYPES:
            raise ValueError(
                f"id_type must be one of {sorted(GLOBAL_IDENTIFIER_TYPES)}. Note "
                f"that 'parcel' and 'vin' are valid on a TENANT asset "
                f"identifier and not on a global one — a parcel number is not "
                f"a market identifier."
            )
        return v


class GlobalPriceCreate(BaseModel):
    """Body for POST /portfolio/global-securities/{id}/prices."""

    model_config = ConfigDict(extra="forbid")

    price_date: date
    price: MoneyIn
    currency_code: str | None = None
    price_type: str = "close"
    source: str | None = None

    _no_floats = field_validator("price", mode="before")(_reject_float)

    @field_validator("price_type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        if v not in PRICE_TYPES:
            raise ValueError(f"price_type must be one of {sorted(PRICE_TYPES)}")
        return v


# ═══════════════════════════════════════════════════════════════════════════
# TENANT surface — /portfolio/securities
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/portfolio/securities")
async def get_securities(
    request: Request,
    search: str | None = Query(default=None),
    asset_type: str | None = Query(default=None),
    asset_class: str | None = Query(default=None),
    valuation_method: str | None = Query(default=None),
    taxonomy_key: str | None = Query(default=None),
    taxonomy_prefix: str | None = Query(
        default=None,
        description="Rolls a super/major-class filter up over its descendants.",
    ),
    security_type: str | None = Query(
        default=None,
        description="Filters on the LINKED global security's type. An asset "
                    "with no link is excluded by any value of this filter.",
    ),
    linked: Literal["all", "linked", "unlinked"] = Query(default="all"),
    include_inactive: bool = Query(default=False),
    include_history: bool = Query(
        default=False,
        description="Include versions archived by an edit. Off by default — a "
                    "grid showing both rows shows one instrument twice.",
    ),
    resolve_values: bool = Query(default=True),
    value_as_of: date | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
):
    """The Securities & Assets grid's data. Org-scoped from JWT claims, always.

    Each row is the tenant's asset LEFT JOINed to the global security it is
    linked to, resolved through the merge chain in one hop. An unlinked asset
    comes back with every ``global_*`` key NULL — that is a legitimate permanent
    state for a property or a private interest, not missing data.

    The ``permissions`` and ``vocabularies`` blocks are what the UI reads to
    decide which controls exist. They are advisory to the client and binding on
    nobody: every write endpoint re-checks.
    """
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)

    async with pool.acquire() as conn:
        try:
            result = await list_assets(
                conn,
                org_id=org_id,
                search=search,
                asset_type=asset_type,
                asset_class=asset_class,
                valuation_method=valuation_method,
                taxonomy_key=taxonomy_key,
                taxonomy_prefix=taxonomy_prefix,
                security_type=security_type,
                linked=linked,
                include_inactive=include_inactive,
                include_history=include_history,
                resolve_values=resolve_values,
                value_as_of=value_as_of,
                limit=limit,
                offset=offset,
            )
            taxonomy = await taxonomy_labels(conn, org_id)
        except PortfolioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    perms = await _permission_envelope(pool, user_id, org_id)
    result["permissions"] = perms
    result["taxonomy"] = taxonomy
    result["vocabularies"] = _vocabularies(perms, taxonomy)
    return result


@router.get("/portfolio/securities/{asset_id}")
async def get_security_detail(
    request: Request,
    asset_id: _uuid.UUID,
    value_as_of: date | None = Query(default=None),
):
    """Everything the right-hand detail pane shows, in one call.

    Asset + linked global security (identity, identifiers, price history,
    underlyings) + both identifier sets + resolved current value with its
    governing valuation + valuation history + positions held + version history.

    A 404 here means "not in your org" as well as "does not exist", and
    deliberately does not distinguish them — telling a caller that an asset id
    exists somewhere else is itself a cross-tenant leak. The GLOBAL security in
    the response is a different matter: those rows belong to no tenant and are
    readable by everyone by deployed policy.
    """
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)

    async with pool.acquire() as conn:
        try:
            detail = await get_asset(
                conn, org_id=org_id, asset_id=str(asset_id), value_as_of=value_as_of
            )
            taxonomy = await taxonomy_labels(conn, org_id)
        except PortfolioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="asset not found")

    perms = await _permission_envelope(pool, user_id, org_id)
    detail["permissions"] = perms
    detail["vocabularies"] = _vocabularies(perms, taxonomy)
    return detail


@router.post("/portfolio/securities", status_code=201)
async def create_security_endpoint(request: Request, body: AssetCreate):
    """Create a tenant asset. Returns the new row in full.

    ``global_security_id`` chooses which platform instrument this asset TRACKS.
    That is an org decision about an org row and is settable here — it is the
    asset's own FK column. It is not settable afterwards, and none of the
    security's own attributes are settable at all, from here or anywhere else on
    the tenant surface.
    """
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)

    async with pool.acquire() as conn:
        try:
            new_id = await create_tenant_asset(
                conn,
                org_id=org_id,
                name=body.name,
                asset_type=body.asset_type,
                asset_class=body.asset_class,
                ownership_basis=body.ownership_basis,
                valuation_method=body.valuation_method,
                short_name=body.short_name,
                global_security_id=(
                    str(body.global_security_id) if body.global_security_id else None
                ),
                default_taxonomy_key=body.default_taxonomy_key,
                currency_code=body.currency_code,
                inception_date=body.inception_date,
                maturity_date=body.maturity_date,
                include_in_performance=body.include_in_performance,
            )
        except GlobalFieldError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except PortfolioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        detail = await get_asset(conn, org_id=org_id, asset_id=new_id)

    perms = await _permission_envelope(pool, user_id, org_id)
    detail["permissions"] = perms
    return detail


@router.patch("/portfolio/securities/{asset_id}")
async def patch_security(request: Request, asset_id: _uuid.UUID, body: AssetPatch):
    """Correct a tenant asset. **The id does NOT change.**

    Unlike ``PATCH /portfolio/positions/{id}``, which returns a NEW id because a
    position restates on the valid axis, an asset is a REFERENCED master row:
    ``asset_identifiers``, ``positions`` and ``valuations`` all carry a foreign
    key to ``assets.id``. The outgoing version is archived on the SYSTEM axis
    and the live row keeps its id, so nothing is orphaned. See
    ``services.portfolio_securities`` for the full reasoning.

    THREE refusal codes, and the difference between them is the point:

    * **403** — the change named a platform field
      (``GLOBAL_SOURCED_FIELDS`` / ``GLOBAL_TABLE_COLUMNS``). The body was
      fine; the caller is not a Super Admin and no org-scoped path writes those.
    * **422** — the change named a field that is not an editable asset column at
      all. A shape error.
    * **400** — the row is not current, or a value failed a vocabulary check.
    """
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)

    changes = body.changes()
    if not changes:
        raise HTTPException(
            status_code=400,
            detail=f"no fields supplied. Send at least one of "
                   f"{sorted(ORG_EDITABLE_FIELDS)}.",
        )

    async with pool.acquire() as conn:
        try:
            outcome = await update_asset(
                conn, org_id=org_id, asset_id=str(asset_id), changes=changes
            )
        except GlobalFieldError as exc:
            # Checked BEFORE the generic field check, so this is reached for a
            # platform field even though such a field is also "not an editable
            # asset column". The permission answer is the true one.
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except PortfolioError as exc:
            status = 422 if "not editable" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc

        detail = await get_asset(conn, org_id=org_id, asset_id=str(asset_id))

    perms = await _permission_envelope(pool, user_id, org_id)
    detail["permissions"] = perms
    detail["changed"] = outcome["changed"]
    detail["archived_version_id"] = outcome["archived_version_id"]
    return detail


@router.get("/portfolio/securities/{asset_id}/versions")
async def get_security_versions(request: Request, asset_id: _uuid.UUID):
    """The asset's version history — the live row plus its system-axis archives."""
    org_id, _user_id, pool = await _tenant_gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        versions = await asset_version_history(
            conn, org_id=org_id, asset_id=str(asset_id)
        )
    return {"count": len(versions), "versions": versions}


# ═══════════════════════════════════════════════════════════════════════════
# PLATFORM surface — /portfolio/global-securities
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/portfolio/global-securities")
async def get_global_securities(
    request: Request,
    search: str | None = Query(default=None),
    security_type: str | None = Query(default=None),
    price_coverage: str | None = Query(default=None),
    include_merged: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """The platform security master. READ — gated on ``view_portfolio`` only.

    Not Super-Admin-gated, and that is correct rather than an oversight. The
    deployed policy on these tables is ``SELECT USING (true)``: the rows have no
    ``org_id`` and belong to no tenant, so a tenant learning that a CUSIP exists
    is not a cross-tenant leak. What a tenant must never do is WRITE one — which
    is enforced on the write path below, three layers deep.

    It is gated on ``view_portfolio`` anyway, because this is a portfolio screen
    and an unauthenticated or unentitled caller has no business enumerating the
    master either.
    """
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)

    async with pool.acquire() as conn:
        try:
            result = await list_global_securities(
                conn,
                search=search,
                security_type=security_type,
                price_coverage=price_coverage,
                include_merged=include_merged,
                limit=limit,
                offset=offset,
            )
        except (PortfolioError, SecuritiesGlobalError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    result["permissions"] = await _permission_envelope(pool, user_id, org_id)
    return result


@router.get("/portfolio/global-securities/{security_id}")
async def get_global_security_detail(request: Request, security_id: _uuid.UUID):
    """One global security in full. READ — ``view_portfolio``."""
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        detail = await get_global_security(conn, str(security_id))
    if detail is None:
        raise HTTPException(status_code=404, detail="global security not found")
    detail["permissions"] = await _permission_envelope(pool, user_id, org_id)
    return detail


@router.post("/portfolio/global-securities", status_code=201)
async def create_global_security(request: Request, body: GlobalSecurityCreate):
    """Mint a global security. **Super Admin only.**

    ``is_super_admin=True`` is passed to the service EXPLICITLY, never inferred
    from the request, and only after :func:`_require_super_admin_actor` has
    already accepted the caller. The service then raises the connection's
    ``app.is_super_admin`` GUC for exactly one transaction, which is what the
    RLS policy compares against.
    """
    _user_id, pool = await _require_super_admin_actor(request)
    async with pool.acquire() as conn:
        try:
            new_id = await create_security(
                conn,
                name=body.name,
                security_type=body.security_type,
                short_name=body.short_name,
                currency_code=body.currency_code,
                price_coverage=body.price_coverage,
                is_super_admin=True,
            )
        except SecuritiesGlobalPermissionError as exc:  # pragma: no cover
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except SecuritiesGlobalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        detail = await get_global_security(conn, new_id)
    return detail


@router.patch("/portfolio/global-securities/{security_id}")
async def patch_global_security(
    request: Request, security_id: _uuid.UUID, body: GlobalSecurityPatch
):
    """Correct a global security's own attributes. **Super Admin only.**

    The id does not change here either, and for the same reason it does not
    change on an asset: ``portfolio.assets.global_security_id`` and three
    satellite tables point at it. ``securities_global.update_security`` archives
    the outgoing version on the system axis.
    """
    _user_id, pool = await _require_super_admin_actor(request)
    changes = body.changes()
    if not changes:
        raise HTTPException(
            status_code=400,
            detail=f"no fields supplied. Send at least one of "
                   f"{sorted(SECURITY_EDITABLE_FIELDS)}.",
        )
    async with pool.acquire() as conn:
        try:
            await update_security(
                conn,
                global_security_id=str(security_id),
                changes=changes,
                is_super_admin=True,
            )
        except SecuritiesGlobalPermissionError as exc:  # pragma: no cover
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except SecuritiesGlobalError as exc:
            status = 422 if "not editable" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        detail = await get_global_security(conn, str(security_id))
    return detail


@router.post("/portfolio/global-securities/{security_id}/identifiers",
             status_code=201)
async def add_global_identifier(
    request: Request, security_id: _uuid.UUID, body: GlobalIdentifierCreate
):
    """Attach an identifier to a global security. **Super Admin only.**"""
    _user_id, pool = await _require_super_admin_actor(request)
    async with pool.acquire() as conn:
        try:
            await add_identifier(
                conn,
                global_security_id=str(security_id),
                id_type=body.id_type,
                id_value=body.id_value,
                is_primary=body.is_primary,
                is_super_admin=True,
            )
        except SecuritiesGlobalPermissionError as exc:  # pragma: no cover
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except SecuritiesGlobalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        detail = await get_global_security(conn, str(security_id))
    return detail


@router.post("/portfolio/global-securities/{security_id}/prices", status_code=201)
async def add_global_price(
    request: Request, security_id: _uuid.UUID, body: GlobalPriceCreate
):
    """Write one price point. **Super Admin only, and structured notes refuse.**

    ``StructuredNotePricingError`` maps to **422**, not 400: the body was
    well-formed and every value was legal — what failed was the relationship
    between the price and the instrument it was aimed at. A1 refuses it in code
    rather than in a comment because the natural implementation of a price
    loader would otherwise write a quarter of a million individually-plausible,
    collectively-meaningless rows without raising anything.
    """
    _user_id, pool = await _require_super_admin_actor(request)
    amount = _money(body.price, "price")
    if amount is None:
        raise HTTPException(
            status_code=422,
            detail="price is required and must be a decimal STRING, not null",
        )
    async with pool.acquire() as conn:
        try:
            await add_price(
                conn,
                global_security_id=str(security_id),
                price_date=body.price_date,
                price=amount,
                currency_code=body.currency_code,
                price_type=body.price_type,
                source=body.source,
                is_super_admin=True,
            )
        except StructuredNotePricingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except SecuritiesGlobalPermissionError as exc:  # pragma: no cover
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except SecuritiesGlobalError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        detail = await get_global_security(conn, str(security_id))
    return detail


__all__ = ["router"]
