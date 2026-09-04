"""TA Model endpoints — Sprint 1 (Task 4).

    GET  /api/v1/modeling/ta/defaults                  org's TA settings
    PUT  /api/v1/admin/modeling/ta/defaults             admin: write them
    GET  /api/v1/modeling/ta/projection/{commitment_id} project one commitment
    POST /api/v1/modeling/ta/projection/preview         ad-hoc "what if"
    POST /api/v1/modeling/ta/calibrate/{commitment_id}  fit + persist params

Every route resolves ``org_id`` from JWT claims via ``routers.entities.
get_org_id`` — never from the request body (CLAUDE.md Rule 6 / the brief's own
non-negotiable). Admin routes live under the literal ``/admin/`` path segment,
matching this codebase's real convention (``apps/api/routers/admin.py``,
``pricing_admin.py``: no router-level prefix, the ``/admin/...`` string is
baked into each route decorator, and ``main.py`` mounts every router at the
same top-level ``/api/v1`` — see the brief's Task-1 discovery notes for why
this was checked explicitly rather than assumed).

Settings are fetched ONCE per request via ``services.org_settings.
get_all_settings`` and threaded through — never re-fetched mid-handler. This
is the real precedent the brief cites (the ``/admin/platform`` theme-caching
bug): a settings read that can return a different answer the second time a
single request calls it is a correctness bug waiting to happen, not a
performance nitpick.

PROJECTED CASH FLOWS ARE NEVER PERSISTED by anything in this file — every
projection is computed inline in the handler and returned; nothing here
writes to a table shaped like a projection result. Only parameter overrides
(``services.ta_params.set_override_params``) and calibration results
(``services.ta_params.record_calibration_result``) persist, both bi-temporal
or append-only per Task 2's schema decision.
"""

from __future__ import annotations

import uuid as _uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, field_validator

from routers.entities import get_org_id
from services.database import get_pool
from services.org_settings import (
    SettingsPermissionError,
    SettingsValidationError,
    get_all_settings,
    set_settings,
)
from services.permissions import get_user_id
from services.portfolio_assets import READ_PERMISSION, WRITE_PERMISSION
from services.portfolio_commitments import CommitmentError, get_commitment
from services.rbac import can_manage_org_settings, is_super_admin, load_principal, require_permission
from services.ta_calibrate import TACalibrationError, calibrate_strategy, minimum_realized_periods
from services.ta_config import (
    DEFAULT_CALIBRATION_MIN_YEARS,
    DEFAULT_TA_STRATEGY_PARAMS,
    TA_CALIBRATION_MIN_YEARS_KEY,
    TA_SETTINGS_KEYS,
    TA_STRATEGY_DEFAULTS_KEY,
    TA_STRATEGY_KEYS,
    TAConfigError,
    params_for_strategy,
    projection_horizon_periods,
    strategy_overrides,
)
from services.ta_model import TAModelError, TAParams, _fixed, project_cash_flows
from services.ta_params import (
    TAParamsError,
    get_active_params,
    realized_periods_from_transactions,
    record_calibration_result,
    set_override_params,
)

router = APIRouter(tags=["modeling-ta"])

_SUPPORTED_CALIBRATION_FREQUENCIES = (1, 4)


# ── Money at the API boundary — same discipline as portfolio_positions.py ──


MoneyIn = str | Decimal | int | None


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError(
            "TA model rate/money fields must be sent as JSON STRINGS "
            '(e.g. "0.28"), not JSON numbers with a decimal point.'
        )
    return value


def _decimal(value: MoneyIn, field: str) -> Decimal:
    if value is None:
        raise HTTPException(status_code=422, detail=f"{field} is required")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{field} is not a valid decimal: {value!r}") from exc


class TAParamsIn(BaseModel):
    """A directly-supplied parameter set (POST preview's ``params_override``)."""

    model_config = ConfigDict(extra="forbid")

    rate_of_contribution: MoneyIn
    rate_of_distribution: MoneyIn
    growth_rate: MoneyIn
    bow_factor: MoneyIn
    fund_life_years: MoneyIn
    periods_per_year: int

    _no_floats = field_validator(
        "rate_of_contribution", "rate_of_distribution", "growth_rate",
        "bow_factor", "fund_life_years", mode="before",
    )(_reject_float)

    def to_params(self) -> TAParams:
        try:
            return TAParams(
                rate_of_contribution=_decimal(self.rate_of_contribution, "rate_of_contribution"),
                rate_of_distribution=_decimal(self.rate_of_distribution, "rate_of_distribution"),
                growth_rate=_decimal(self.growth_rate, "growth_rate"),
                bow_factor=_decimal(self.bow_factor, "bow_factor"),
                fund_life_years=_decimal(self.fund_life_years, "fund_life_years"),
                periods_per_year=self.periods_per_year,
            )
        except TAModelError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


class ProjectionPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_key: str | None = None
    params_override: TAParamsIn | None = None
    committed_capital: MoneyIn
    called_to_date: MoneyIn = "0"
    distributed_to_date: MoneyIn = "0"
    current_nav: MoneyIn = "0"
    horizon_periods: int | None = None
    periods_per_year: int | None = None

    _no_floats = field_validator(
        "committed_capital", "called_to_date", "distributed_to_date", "current_nav",
        mode="before",
    )(_reject_float)


class CalibrateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ta_strategy_key: str
    periods_per_year: int


class DefaultsWriteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any]


# ── Permission envelope — CLAUDE.md "Permission Envelope Pattern": every
# permission-gated screen's response publishes can_read/can_write/
# is_super_admin from the server, never a client-derived default. Neither
# ``routers/org_settings.py`` nor ``OrgSettingsEditor.jsx`` do this today (a
# real, pre-existing gap in that older screen — confirmed by this sprint's
# own Task 1 discovery); the TA settings screen follows the newer, correct
# convention established by the Workflow Triggers screen instead.


def _ta_permissions(principal: dict | None, org_id: str) -> dict:
    return {
        "can_read": True,
        "can_write": bool(can_manage_org_settings(principal, org_id)),
        "is_super_admin": bool(is_super_admin(principal)),
        "read_permission": None,  # open read — no permission required
        "write_permission": "manage_org_settings",
    }


def _defaults_envelope(settings: dict, principal: dict | None, org_id: str) -> dict:
    strategy_defaults = settings.get(TA_STRATEGY_DEFAULTS_KEY) or DEFAULT_TA_STRATEGY_PARAMS
    return {
        **{key: settings.get(key) for key in TA_SETTINGS_KEYS},
        "strategy_overrides": strategy_overrides(strategy_defaults),
        "permissions": _ta_permissions(principal, org_id),
    }


# ── GET /modeling/ta/defaults — open read, mirrors org_settings' own pattern ─


@router.get("/modeling/ta/defaults")
async def get_ta_defaults(request: Request):
    """The org's TA settings. Open to any authenticated member of the org —
    no permission check — the SAME real pattern ``GET /orgs/{org_id}/settings``
    uses (``routers/org_settings.py``: "Reads are open to any authenticated
    user of the org"). TA defaults are configuration an org member needs to
    understand any projection they see; there is no reason to gate them more
    tightly than org_settings itself.

    Also publishes ``strategy_overrides`` (per-strategy "your override" vs.
    "platform default" — see ``ta_config.strategy_overrides``) and the real
    ``permissions`` envelope (Task 1a/1b gap: neither existed before this
    sprint) so the admin screen never has to guess or default either.
    """
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        settings = await get_all_settings(conn, org_id)  # fetched ONCE
        principal = await load_principal(conn, user_id)
    return _defaults_envelope(settings, principal, org_id)


# ── GET /modeling/ta/calibration-floor — reuses the real frequency-aware floor


@router.get("/modeling/ta/calibration-floor")
async def get_calibration_floor(
    request: Request,
    periods_per_year: int = Query(..., ge=1),
):
    """The real minimum realized-history requirement at a given frequency,
    computed by calling ``ta_calibrate.minimum_realized_periods`` itself — so
    the settings screen can show the true floor as an admin edits
    periods_per_year (e.g. 12 quarters, not a flat 3), never a value
    re-derived in the browser that could drift from the real calibration gate.
    Open read, same convention as ``GET /modeling/ta/defaults``.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        settings = await get_all_settings(conn, org_id)
    min_years = Decimal(str(settings.get(TA_CALIBRATION_MIN_YEARS_KEY) or DEFAULT_CALIBRATION_MIN_YEARS))
    try:
        periods = minimum_realized_periods(periods_per_year, min_years=min_years)
    except TACalibrationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "periods_per_year": periods_per_year,
        "calibration_min_years": str(min_years),
        "minimum_realized_periods": periods,
    }


# ── PUT /admin/modeling/ta/defaults — write, gated like org_settings writes ─


@router.put("/admin/modeling/ta/defaults")
async def put_ta_defaults(request: Request, body: DefaultsWriteBody):
    """Write one or more of the 4 TA settings keys. Gated by
    ``can_manage_org_settings`` — NOT a new permission — because this IS an
    org_settings write; ``services.org_settings.set_settings`` already
    enforces that check internally, so this handler does not duplicate it.

    ``modeling.ta.strategy_defaults`` is stored as ONE jsonb blob covering all
    8 strategies (Task 1a). A caller that submits only the strategies it
    actually edited is MERGED into the org's existing blob here, not written
    as a full replacement — writing a partial dict as-is would silently drop
    every other strategy's prior override the first time an admin edited just
    one strategy through this screen. Submitting the full 8-strategy object
    (the previous, only-safe usage) still works unchanged, since merging a
    superset into itself is a no-op.
    """
    org_id = get_org_id(request)
    user_id = get_user_id(request)

    unknown = set(body.values) - set(TA_SETTINGS_KEYS)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown TA settings key(s): {sorted(unknown)} — allowed: {TA_SETTINGS_KEYS}",
        )

    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await get_all_settings(conn, org_id)  # fetched ONCE, base for merge
        principal = await load_principal(conn, user_id)

    values_to_write = dict(body.values)
    submitted_strategy_defaults = values_to_write.get(TA_STRATEGY_DEFAULTS_KEY)
    if submitted_strategy_defaults is not None:
        for strategy_key, raw in submitted_strategy_defaults.items():
            if strategy_key not in TA_STRATEGY_KEYS:
                raise HTTPException(
                    status_code=400,
                    detail=f"strategy_defaults key {strategy_key!r} is not one of {TA_STRATEGY_KEYS}",
                )
            try:
                TAParams(
                    rate_of_contribution=Decimal(str(raw["rate_of_contribution"])),
                    rate_of_distribution=Decimal(str(raw["rate_of_distribution"])),
                    growth_rate=Decimal(str(raw["growth_rate"])),
                    bow_factor=Decimal(str(raw["bow_factor"])),
                    fund_life_years=Decimal(str(raw["fund_life_years"])),
                    periods_per_year=4,
                )
            except (TAModelError, KeyError, InvalidOperation) as exc:
                raise HTTPException(
                    status_code=400, detail=f"malformed params for {strategy_key!r}: {exc}"
                ) from exc

        merged = dict(existing.get(TA_STRATEGY_DEFAULTS_KEY) or DEFAULT_TA_STRATEGY_PARAMS)
        merged.update(submitted_strategy_defaults)
        values_to_write[TA_STRATEGY_DEFAULTS_KEY] = merged

    async with pool.acquire() as conn:
        try:
            resolved = await set_settings(conn, org_id, values_to_write, user_id, principal=principal)
        except SettingsPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except SettingsValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _defaults_envelope(resolved, principal, org_id)


# ── GET /modeling/ta/projection/{commitment_id} ─────────────────────────────


@router.get("/modeling/ta/projection/{commitment_id}")
async def get_projection(
    request: Request,
    commitment_id: _uuid.UUID,
    strategy_key: str | None = Query(default=None),
    periods_per_year: int | None = Query(default=None),
    horizon_periods: int | None = Query(default=None),
):
    """Project one real commitment's cash flows forward. Computed inline —
    never persisted. Gated on ``view_portfolio``: this reads and computes,
    never writes.
    """
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    pool = await get_pool()
    await require_permission(pool, user_id, org_id, READ_PERMISSION)

    async with pool.acquire() as conn:
        try:
            commitment = await get_commitment(conn, org_id=org_id, commitment_id=str(commitment_id))
        except CommitmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if commitment is None:
            raise HTTPException(status_code=404, detail="commitment not found")

        settings = await get_all_settings(conn, org_id)  # fetched ONCE

        override = await get_active_params(conn, org_id=org_id, commitment_id=str(commitment_id))
        if override is not None:
            params = override["params"]
            ta_strategy_key = override["ta_strategy_key"]
        else:
            if not strategy_key:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "this commitment has no TA parameter override set — pass "
                        "?strategy_key=<one of "
                        f"{TA_STRATEGY_KEYS}> to project against a strategy default"
                    ),
                )
            try:
                params = params_for_strategy(strategy_key, settings, periods_per_year=periods_per_year)
            except (TAConfigError, TAModelError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            ta_strategy_key = strategy_key

        position = await conn.fetchrow(
            """
            SELECT p.market_value
            FROM portfolio.positions p
            JOIN portfolio.commitments c ON c.position_id = p.id AND c.org_id = p.org_id
            WHERE c.id = $1::uuid AND c.org_id = $2::uuid
              AND p.valid_to IS NULL AND p.system_to IS NULL
              AND c.valid_to IS NULL AND c.system_to IS NULL
            """,
            str(commitment_id), org_id,
        )

    committed = commitment["commitment_amount"] or Decimal(0)
    called = commitment["called_to_date"] or Decimal(0)
    distributed = commitment["distributed_to_date"] or Decimal(0)
    if position is not None and position["market_value"] is not None:
        current_nav = Decimal(position["market_value"])
    else:
        current_nav = called - distributed
        if current_nav < 0:
            current_nav = Decimal(0)

    horizon = horizon_periods or projection_horizon_periods(settings, params.periods_per_year)

    try:
        periods = project_cash_flows(
            committed_capital=committed, called_to_date=called,
            distributed_to_date=distributed, current_nav=current_nav,
            params=params, horizon_periods=horizon,
        )
    except TAModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "commitment_id": str(commitment_id),
        "ta_strategy_key": ta_strategy_key,
        "params": params.to_json(),
        "current_nav": _fixed(current_nav),
        "periods": [p.to_json() for p in periods],
    }


# ── POST /modeling/ta/projection/preview — ad-hoc, no commitment required ──


@router.post("/modeling/ta/projection/preview")
async def preview_projection(request: Request, body: ProjectionPreviewBody):
    """Project against ARBITRARY inputs — no commitment_id, nothing read or
    written. Gated on ``view_portfolio`` (same read-only gate as the
    commitment projection) rather than left open: a preview still exposes an
    org's configured TA strategy defaults if ``strategy_key`` is used without
    ``params_override``.
    """
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    pool = await get_pool()
    await require_permission(pool, user_id, org_id, READ_PERMISSION)

    if body.params_override is None and not body.strategy_key:
        raise HTTPException(
            status_code=422, detail="one of strategy_key or params_override is required"
        )

    async with pool.acquire() as conn:
        settings = await get_all_settings(conn, org_id)  # fetched ONCE

    if body.params_override is not None:
        params = body.params_override.to_params()
    else:
        try:
            params = params_for_strategy(
                body.strategy_key, settings, periods_per_year=body.periods_per_year
            )
        except (TAConfigError, TAModelError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    horizon = body.horizon_periods or projection_horizon_periods(settings, params.periods_per_year)

    try:
        periods = project_cash_flows(
            committed_capital=_decimal(body.committed_capital, "committed_capital"),
            called_to_date=_decimal(body.called_to_date, "called_to_date"),
            distributed_to_date=_decimal(body.distributed_to_date, "distributed_to_date"),
            current_nav=_decimal(body.current_nav, "current_nav"),
            params=params, horizon_periods=horizon,
        )
    except TAModelError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "params": params.to_json(),
        "periods": [p.to_json() for p in periods],
    }


# ── POST /modeling/ta/calibrate/{commitment_id} ─────────────────────────────


@router.post("/modeling/ta/calibrate/{commitment_id}")
async def calibrate(request: Request, commitment_id: _uuid.UUID, body: CalibrateBody):
    """Fit TAParams to this commitment's REAL realized transaction history and
    persist the result — both the append-only calibration-run log and the new
    active parameter override (Rule 3: closes any prior override row first).

    Gated on ``manage_portfolio``: this writes two tables.
    """
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    pool = await get_pool()
    await require_permission(pool, user_id, org_id, WRITE_PERMISSION)

    if body.ta_strategy_key not in TA_STRATEGY_KEYS:
        raise HTTPException(
            status_code=422,
            detail=f"ta_strategy_key={body.ta_strategy_key!r} is not one of {TA_STRATEGY_KEYS}",
        )
    if body.periods_per_year not in _SUPPORTED_CALIBRATION_FREQUENCIES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"periods_per_year={body.periods_per_year} is not supported for "
                f"calibration — must be one of {_SUPPORTED_CALIBRATION_FREQUENCIES}"
            ),
        )

    async with pool.acquire() as conn:
        try:
            commitment = await get_commitment(conn, org_id=org_id, commitment_id=str(commitment_id))
        except CommitmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if commitment is None:
            raise HTTPException(status_code=404, detail="commitment not found")

        committed_capital = commitment["commitment_amount"]
        if committed_capital is None or committed_capital <= 0:
            raise HTTPException(
                status_code=422, detail="commitment has no positive commitment_amount to calibrate against"
            )

        settings = await get_all_settings(conn, org_id)  # fetched ONCE
        min_years = Decimal(str(settings.get(TA_CALIBRATION_MIN_YEARS_KEY) or DEFAULT_CALIBRATION_MIN_YEARS))

        base_params = params_for_strategy(
            body.ta_strategy_key, settings, periods_per_year=body.periods_per_year
        )

        try:
            realized = await realized_periods_from_transactions(
                conn, org_id=org_id, commitment_id=str(commitment_id),
                periods_per_year=body.periods_per_year,
            )
        except TAParamsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            calibrated = calibrate_strategy(
                realized,
                committed_capital=Decimal(committed_capital),
                periods_per_year=body.periods_per_year,
                bow_factor=base_params.bow_factor,
                fund_life_years=base_params.fund_life_years,
                min_years=min_years,
            )
        except TACalibrationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        calibration_id = await record_calibration_result(
            conn, org_id=org_id, commitment_id=str(commitment_id),
            ta_strategy_key=body.ta_strategy_key, calibrated_params=calibrated,
            realized_periods_used=len(realized), created_by=user_id,
        )
        params_id = await set_override_params(
            conn, org_id=org_id, commitment_id=str(commitment_id),
            ta_strategy_key=body.ta_strategy_key, params=calibrated,
            created_by=user_id, source="calibrated",
        )

    return {
        "commitment_id": str(commitment_id),
        "calibration_id": calibration_id,
        "params_id": params_id,
        "realized_periods_used": len(realized),
        "params": calibrated.to_json(),
    }
