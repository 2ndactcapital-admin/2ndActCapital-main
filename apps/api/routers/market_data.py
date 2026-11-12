"""mkt03 — the market data READ API: catalog, series, grid, correlations.

    GET  /market/catalog                                   categories, indicators, SELECTABLE securities
    GET  /market/securities/search?q=                      security-master search (dailyops1), <= 25 rows
    GET  /market/series?keys=a,b&from=&to=&frequency=      raw (optionally resampled) points
    POST /market/grid          {keys, anchor, end?, mode, frequency}
    POST /market/correlations  {focus_key, keys, anchor, end?, lag_months?, min_periods?}

Mounted under /api/v1 (main.py). Platform-global reference data: nothing here
takes or returns an org_id, and nothing writes. Access is decided by ONE
function, ``services.market_data.access.require_market_data_read``; every
response carries its permissions envelope and vocabularies.

Reads run on the normal request connection (``get_pool().acquire()``, which
carries the caller's RLS context), switched to a read-only transaction. Never
platform_scope(): the tables read here all have a global SELECT policy.

Bodies are read raw and validated in ``services.market_data.read_service`` —
not by FastAPI — so a 422 never echoes what the caller sent. Values cross as
strings (exact Decimal text); see docs/MARKET_DATA_DESIGN_V1.md.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.database import get_pool
from services.market_data import read_repository as repo
from services.market_data import read_service as svc
from services.market_data.access import permissions_envelope, require_market_data_read

router = APIRouter(prefix="/market", tags=["market-data"])


def _today():
    return datetime.now(timezone.utc).date()


def _refusal(exc: svc.MarketDataRequestError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail()})


async def _read(fn, *args) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await repo.make_read_only(conn)
        return await fn(conn, *args)


@router.get("/catalog")
async def market_catalog(request: Request):
    reader = await require_market_data_read(request)
    if request.query_params:
        return _refusal(svc.MarketDataRequestError("Request validation failed", errors=[{
            "loc": ["query"], "type": "extra_forbidden",
            "msg": "This endpoint takes no query parameters"}]))
    body = await _read(svc.build_catalog)
    return {**body, "permissions": permissions_envelope(reader)}


@router.get("/securities/search")
async def market_securities_search(request: Request):
    """dailyops1 — the catalog lists only selectable securities; the rest of
    the security master is found here. Same gate and envelope as /catalog."""
    reader = await require_market_data_read(request)
    try:
        q = svc.parse_search_query(list(request.query_params.multi_items()))
        body = await _read(svc.search_securities, q)
    except svc.MarketDataRequestError as exc:
        return _refusal(exc)
    body["tracking"] = await _read(_tracking_block, reader, body["securities"])
    return {**body, "permissions": permissions_envelope(reader)}


async def _tracking_block(conn, reader, securities: list) -> dict:
    """TRACK1 — the per-security "Track" on an unpriced search result. Only a Super Admin may track (the write is
    POST /admin/security-data/master/tracked/track, gated require_super_admin); everyone else gets the disabled
    reason. ``tracked`` lists the results already tracked. Tracking loads the PRICE STORE — it does not by itself make
    a security selectable here (that needs a linked market series); the note says so."""
    ids = [s["id"] for s in securities]
    tracked = [r[0] for r in await conn.fetch(
        "SELECT global_security_id::text FROM portfolio.tracked_securities WHERE global_security_id = ANY($1::uuid[]) "
        "AND status = 'tracked' AND valid_to IS NULL AND system_to IS NULL", ids)] if ids else []
    su = bool(reader.is_super_admin)
    return {"can_track": su,
            "disabled_reason": None if su else "Only a Super Admin can track a security for prices.",
            "tracked": tracked,
            "note": "Tracking queues this security's 5-year daily price history for the prices job. It becomes "
                    "selectable on this page once a market series is linked to it."}


@router.get("/series")
async def market_series(request: Request):
    reader = await require_market_data_read(request)
    try:
        query = svc.parse_series_query(list(request.query_params.multi_items()))
        body = await _read(svc.read_series, query)
    except svc.MarketDataRequestError as exc:
        return _refusal(exc)
    return {**body, "permissions": permissions_envelope(reader)}


@router.post("/grid")
async def market_grid(request: Request):
    reader = await require_market_data_read(request)
    try:
        req = svc.parse_grid(await request.body(), _today())
        body = await _read(svc.build_grid, req)
    except svc.MarketDataRequestError as exc:
        return _refusal(exc)
    return {**body, "permissions": permissions_envelope(reader)}


@router.post("/correlations")
async def market_correlations(request: Request):
    reader = await require_market_data_read(request)
    try:
        req = svc.parse_correlations(await request.body(), _today())
        body = await _read(svc.build_correlations, req)
    except svc.MarketDataRequestError as exc:
        return _refusal(exc)
    return {**body, "permissions": permissions_envelope(reader)}
