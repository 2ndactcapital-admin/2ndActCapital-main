"""Admin endpoint: SSVI volatility-surface calibration (Sprint 31).

    POST /api/v1/admin/pricing/surface   body: {"ticker": "^SPX"}

Super Admin only. ``org_id`` is resolved server-side from the session and is
never read from the request body.

NOTE ON THE PATH: admin routes in this codebase declare a bare ``/admin/...``
path and ``main.py`` includes the router with ``prefix="/api/v1"``. The full
path is therefore ``/api/v1/admin/pricing/surface``, not ``/api/v1/pricing/...``.

WHY THIS ROUTER IS SEPARATE FROM ``pricing_admin.py``
    ``routers/pricing_admin.py`` already owns ``/admin/pricing/note-terms/*``
    and ``/admin/pricing/stp-policy`` (Sprint 30). Those are AI-extraction
    review endpoints with completely different dependencies. Keeping the
    surface engine — which drags in numpy/scipy/pandas/yfinance — in its own
    module means the note-terms queue never pays for those imports.

NO PERSISTENCE THIS SPRINT
    Deliberate. This sprint answers "does a free surface work on real SPX", and
    that question needs no storage. ``vol_surface_fits``, R2 quote snapshots and
    the nightly trigger are deferred (sprint Part 2 notes).

MEMORY
    Render SIGKILLs an OOM container, so the guard is preemptive: headroom is
    asserted BEFORE the heavy imports and the network fetch, and RLIMIT_AS is
    pinned below the container limit so an overrun surfaces as a catchable
    ``MemoryError`` rather than a dropped connection. See
    ``services.pricing.memory_guard``.

ERROR CONTRACT
    Every failure returns a typed ``status`` plus the exception message in
    ``detail`` — never a bare 500.

        InsufficientDataError   422  insufficient_data
        SurfaceQualityError     422  quality_gate_failed
        SurfaceArbitrageError   422  arbitrage_violation
        unsupported ticker      422  invalid_ticker
        low memory headroom     503  insufficient_memory
        MemoryError during run  503  out_of_memory
        engine import failed    503  module_unavailable
        upstream fetch failure  502  data_provider_error
        exceeded 90s            504  timeout
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from routers.entities import get_org_id
from services.database import get_pool
from services.pricing import memory_guard
from services.rbac import is_super_admin, load_principal
from services.users import ensure_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin", "pricing", "surface"])

# European-exercise index options only. The module never falls back to SPY:
# SPY is American, which turns put-call parity into an inequality and silently
# biases the derived forward. If both of these fail that is a real 422.
ALLOWED_TICKERS = ("^SPX", "^XSP")

# Synchronous by design this sprint; 15-40s is the expected range.
REQUEST_TIMEOUT_SECONDS = 90.0

# Headroom required before we import numpy/scipy/pandas/yfinance and fetch.
REQUIRED_HEADROOM_MB = 400


class SurfaceRequest(BaseModel):
    """Request body.

    Deliberately only a ticker. The module reads the market, so there is very
    little to parameterize — and org_id is resolved from the session, never
    accepted from the caller.
    """

    ticker: str = "^SPX"


class _ProviderError(Exception):
    """Upstream (yfinance/Yahoo) fetch failed — distinct from 'no good quotes'."""


class _ModuleUnavailable(Exception):
    """The calibration module could not be imported (missing numpy/scipy)."""


def _typed_error(http_status: int, status: str, detail: str) -> JSONResponse:
    """Typed failure body.

    ``HTTPException`` would emit only ``{"detail": ...}``; the UI needs the
    machine-readable ``status`` alongside it to render a first-class failure
    state rather than a toast.
    """
    return JSONResponse(
        status_code=http_status, content={"status": status, "detail": detail}
    )


async def _require_super_admin(request: Request) -> tuple[str, str]:
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if not is_super_admin(principal):
        raise HTTPException(status_code=403, detail="Super Admin access required")
    return actor_id, org_id


def _calibrate_blocking(ticker: str) -> dict[str, Any]:
    """Fetch chains and calibrate. Runs in a worker thread — blocking by nature.

    Imports numpy/scipy (via the engine) and pandas/yfinance (inside
    ``build_slices_from_yahoo``) here rather than at module scope, so the base
    service does not carry them at idle. matplotlib is never imported
    server-side; the engine only pulls it under the CLI's ``--plot``.
    """
    try:
        from services.pricing import ssvi_surface as ssvi
    except Exception as exc:  # numpy/scipy missing or broken in the image
        raise _ModuleUnavailable(f"{type(exc).__name__}: {exc}") from exc

    stats: dict[str, Any] = {}
    try:
        slices, resolved_ticker = ssvi.build_slices_from_yahoo(ticker, stats=stats)
    except (ssvi.InsufficientDataError, MemoryError):
        raise
    except Exception as exc:
        # Network error, Yahoo schema change, rate limit — anything that is not
        # "we fetched fine but the quotes were unusable".
        raise _ProviderError(f"{type(exc).__name__}: {exc}") from exc

    fit = ssvi.calibrate(slices, ticker=resolved_ticker)
    return build_payload(fit, slices, stats)


# Slice T is rounded to this many places when it crosses the wire. It MUST match
# the rounding `iv_diagnostics` applies when it builds `per_slice` (round(T, 4)):
# the smile chart joins each market point to its slice on T, so if the two are
# rounded differently every join misses and the chart silently renders empty.
# 4dp is unambiguous here — consecutive expiries differ by at least 1/365 ≈ .0027.
SLICE_T_DP = 4


def build_payload(fit, slices, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assemble the response body. Pure — no I/O, so it is directly testable."""
    from dataclasses import asdict

    # Raw per-slice quotes for the smile chart. The client computes the model
    # curve itself from rho/eta/gamma/theta_atm, so only the market points cross
    # the wire.
    market_points = [
        {
            "T": round(float(s.T), SLICE_T_DP),
            "k": round(float(k), 6),
            "iv": round(float(iv), 6),
        }
        for s in slices
        for k, iv in zip(s.k, s.iv)
    ]

    payload: dict[str, Any] = {
        "status": "ok",
        "fit": asdict(fit),
        "market_points": market_points,
    }

    # Report the expiry cap only when it actually bound — a cap that did not
    # bind is noise, but one that did changes what the fit was computed from.
    if stats and stats.get("expiry_cap_applied"):
        payload["expiry_cap"] = {
            "cap": stats.get("expiry_cap"),
            "available": stats.get("n_expiries_available"),
            "processed": stats.get("n_expiries_processed"),
            "note": (
                "Expiry count exceeded the processing cap. An evenly spaced "
                "subsample was used, retaining the shortest and longest tenors."
            ),
        }
    return payload


@router.post("/admin/pricing/surface")
async def calibrate_surface(request: Request, body: SurfaceRequest):
    """Calibrate an arbitrage-free SSVI surface from live listed index options."""
    await _require_super_admin(request)

    ticker = (body.ticker or "").strip().upper()
    if ticker not in ALLOWED_TICKERS:
        return _typed_error(
            422,
            "invalid_ticker",
            f"{ticker or '(empty)'} is not supported. European-exercise index "
            f"options only: {', '.join(ALLOWED_TICKERS)}. SPY is American "
            "exercise and would bias the derived forward.",
        )

    # 1. Preemptive headroom check — before the heavy imports and the fetch.
    try:
        snapshot = memory_guard.assert_headroom(REQUIRED_HEADROOM_MB)
    except memory_guard.InsufficientMemoryError as exc:
        logger.warning("[surface] refused for memory headroom: %s", exc)
        return _typed_error(503, "insufficient_memory", str(exc))

    # 2. Turn a would-be SIGKILL into a catchable MemoryError. No-op when no
    #    container limit is discoverable (see memory_guard for why).
    memory_guard.apply_address_space_limit()

    # 3. Import the engine lazily inside the handler so the exception classes
    #    are available for the mapping below without a module-scope numpy pull.
    try:
        from services.pricing import ssvi_surface as ssvi
    except Exception as exc:
        logger.exception("[surface] engine import failed")
        return _typed_error(
            503, "module_unavailable", f"{type(exc).__name__}: {exc}"
        )

    try:
        payload = await asyncio.wait_for(
            asyncio.to_thread(_calibrate_blocking, ticker),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        # wait_for stops awaiting; the worker thread cannot be cancelled and
        # will run to completion in the background. Acceptable for a
        # super-admin-only, manually triggered endpoint — worth revisiting if
        # this ever becomes a background job.
        logger.warning("[surface] %s timed out after %ss", ticker, REQUEST_TIMEOUT_SECONDS)
        return _typed_error(
            504,
            "timeout",
            f"Calibration exceeded {int(REQUEST_TIMEOUT_SECONDS)}s for {ticker}.",
        )
    except ssvi.InsufficientDataError as exc:
        return _typed_error(422, "insufficient_data", str(exc))
    except ssvi.SurfaceQualityError as exc:
        return _typed_error(422, "quality_gate_failed", str(exc))
    except ssvi.SurfaceArbitrageError as exc:
        return _typed_error(422, "arbitrage_violation", str(exc))
    except MemoryError as exc:
        logger.error("[surface] MemoryError during calibration: %s", exc)
        return _typed_error(
            503, "out_of_memory", f"Ran out of memory during calibration: {exc}"
        )
    except _ModuleUnavailable as exc:
        logger.exception("[surface] engine import failed in worker")
        return _typed_error(503, "module_unavailable", str(exc))
    except _ProviderError as exc:
        logger.warning("[surface] upstream fetch failed: %s", exc)
        return _typed_error(502, "data_provider_error", str(exc))
    except Exception as exc:
        # Last resort: still typed, still carries the message. A bare 500 here
        # would leave the UI with nothing to show.
        logger.exception("[surface] unexpected failure")
        return _typed_error(500, "unexpected_error", f"{type(exc).__name__}: {exc}")

    # 4. Log peak RSS so the Render instance gets sized from data (Part 4, #3).
    peak = memory_guard.peak_rss_mb()
    limit_mb = snapshot.limit_mb if snapshot else None
    logger.info(
        "[surface] %s ok — %s quotes / %s slices, pooled RMSE %.4f, peak RSS %s MB"
        " (limit %s MB)",
        payload["fit"]["ticker"],
        payload["fit"]["n_points"],
        payload["fit"]["n_slices"],
        payload["fit"]["rmse_iv_pooled"],
        f"{peak:.1f}" if peak is not None else "unknown",
        f"{limit_mb:.0f}" if limit_mb is not None else "unknown",
    )
    payload["memory"] = {
        "peak_rss_mb": round(peak, 1) if peak is not None else None,
        "limit_mb": round(limit_mb, 1) if limit_mb is not None else None,
        "source": snapshot.source if snapshot else None,
    }
    return payload
