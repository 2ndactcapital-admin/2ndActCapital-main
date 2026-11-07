"""ROLLUP2 — Super Admin read-outs and the fund category map.

    GET /admin/ownership/duplicates   owner / owned pairs with more than one live ownership entry (Super Admin)
    GET /admin/fund-categories        the fund category → taxonomy key map: default + overrides (Super Admin)
    PUT /admin/fund-categories        record an override — a new version, the prior archived (Super Admin)
    GET /admin/fund-categories/proposals   each fund asset's proposed key in the caller's org (Super Admin)

Every gate is ``require_super_admin`` (services.authz — checked first, no default-allow). org_id only from the
caller's session (``get_org_id``); the request models are ``extra='forbid'``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import fund_classification as FC
from services import ownership_duplicates as OD
from services.authz import require_super_admin
from services.database import get_pool

router = APIRouter(tags=["rollup2"])


def _env(rows, *, can_write: bool, editable: list[str]) -> dict:
    return {"rows": rows,
            "permissions": {"can_read": True, "can_write": can_write, "is_super_admin": True,
                            "read_permission": "super_admin", "write_permission": "super_admin"},
            "vocabularies": {"editable": editable if can_write else [], "inline_editable": []}}


@router.get("/admin/ownership/duplicates")
async def ownership_duplicates(request: Request):
    require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        pairs = await OD.find(conn, get_org_id(request))
    return {**_env([{**p, "warning": OD.warning_text(p)} for p in pairs], can_write=False, editable=[]),
            "count": len(pairs),
            "repair": "scripts/repair_ownership_duplicates.py --org <org> --dry-run, then without --dry-run"}


class FundCategoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_key: str = Field(default=FC.PROVIDER, max_length=80)
    category: str = Field(max_length=200)
    taxonomy_key: Optional[str] = Field(default=None, max_length=80)
    note: Optional[str] = Field(default=None, max_length=1000)


@router.get("/admin/fund-categories")
async def fund_categories(request: Request):
    require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        out = await FC.list_map(conn)
    return {**_env(out["rows"], can_write=True, editable=["taxonomy_key", "note"]),
            "overrides": out["overrides"], "unmapped_categories": out["unmapped_categories"]}


@router.put("/admin/fund-categories")
async def set_fund_category(request: Request, body: FundCategoryBody):
    p = require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await FC.set_mapping(conn, actor_id=p.user_id, provider_key=body.provider_key,
                                        category=body.category, taxonomy_key=body.taxonomy_key, note=body.note)
        except FC.FundMapError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/admin/fund-categories/proposals")
async def fund_proposals(request: Request):
    require_super_admin(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await FC.propose(conn, get_org_id(request))
    return {**_env([{**r, "live_value": str(r["live_value"])} for r in rows], can_write=False, editable=[]),
            "apply": "scripts/backfill_taxonomy_keys.py --org <org> --funds --dry-run, then without --dry-run "
                     "(add --refine to apply the 'refine' proposals: an ancestor key -> the sharper one)"}
