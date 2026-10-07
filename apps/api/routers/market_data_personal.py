"""mkt03b — key dates, regimes and saved views around the market data chart.

    GET    /market/key-dates                 key dates, regimes, the caller's own dates, data range
    POST   /market/key-dates/custom          {name, event_date}
    DELETE /market/key-dates/custom/{id}
    GET    /market/views                     platform presets + the caller's own views
    POST   /market/views                     {name, config}
    PUT    /market/views/{id}                {name?, config?}
    DELETE /market/views/{id}

Mounted under /api/v1 (main.py), next to the mkt03 read routes. Access is the
same single gate, ``services.market_data.access.require_market_data_read`` (a
valid session); a caller manages only their own rows.

The org comes from the verified session (``get_org_id`` via the gate) and the
user from ``services.users.ensure_user`` — the same pair the notification
preferences and dashboard todo endpoints use. Neither is ever read from a body
or a path. Every unit of work runs on the request connection
(``get_pool().acquire()``), which carries the caller's RLS context; nothing
here uses platform_scope().

Bodies are read raw and validated in the service modules, so a 422 never echoes
what the caller sent. A refusal is raised INSIDE the connection block, so the
transaction rolls back, and turned into a response outside it.
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.database import get_pool
from services.market_data import key_dates as kd
from services.market_data import saved_views as sv
from services.market_data.access import require_market_data_read
from services.market_data.personal import personal_envelope
from services.market_data.read_service import MarketDataRequestError
from services.users import ensure_user

router = APIRouter(prefix="/market", tags=["market-data"])


def _refusal(exc: MarketDataRequestError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail()})


async def _run(request: Request, fn, *args, status: int = 200, envelope: bool = False):
    reader = await require_market_data_read(request)
    raw = await request.body() if request.method in ("POST", "PUT") else None
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            user_id = await ensure_user(conn, request)
            call_args = (*args, raw) if raw is not None else args
            body = await fn(conn, reader.org_id, user_id, *call_args)
    except MarketDataRequestError as exc:
        return _refusal(exc)
    except asyncpg.ForeignKeyViolationError:
        # ensure_user never raises: if it could not resolve a real users row it
        # returns a token-derived id, and the owner FK refuses the insert.
        return _refusal(MarketDataRequestError("Your account could not be resolved.", status=403))
    if envelope:
        body = {**body, "permissions": personal_envelope(reader)}
    return JSONResponse(status_code=status, content=body)


@router.get("/key-dates")
async def get_key_dates(request: Request):
    return await _run(request, kd.build_key_dates, envelope=True)


@router.post("/key-dates/custom")
async def post_custom_date(request: Request):
    return await _run(request, kd.create_custom_date, status=201)


@router.delete("/key-dates/custom/{row_id}")
async def delete_custom_date(row_id: str, request: Request):
    return await _run(request, kd.delete_custom_date, row_id)


@router.get("/views")
async def get_views(request: Request):
    return await _run(request, sv.build_views, envelope=True)


@router.post("/views")
async def post_view(request: Request):
    return await _run(request, sv.create_view, status=201)


@router.put("/views/{row_id}")
async def put_view(row_id: str, request: Request):
    return await _run(request, sv.update_view, row_id)


@router.delete("/views/{row_id}")
async def delete_view(row_id: str, request: Request):
    return await _run(request, sv.delete_view, row_id)
