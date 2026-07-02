"""Reference data endpoints — Sprint 16 + Sprint 19."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Query, Request

from services.database import get_pool
from services.fx import get_rate
from services.reference_data import get_list

router = APIRouter(tags=["reference"])


@router.get("/reference/{list_key}")
async def get_reference_list(
    request: Request,
    list_key: str,
    parent_code: str | None = Query(None),
):
    pool = await get_pool()
    items = await get_list(pool, list_key, parent_code)
    return {"list_key": list_key, "items": items}


@router.get("/fx-rates")
async def get_fx_rate(
    request: Request,
    base: str = Query(...),
    quote: str = Query(...),
    as_of: Optional[date] = Query(None),
):
    pool = await get_pool()
    rate = await get_rate(pool, base, quote, as_of)
    return {
        "base": base.upper(),
        "quote": quote.upper(),
        "rate": rate,
        "as_of": (as_of or date.today()).isoformat(),
    }
