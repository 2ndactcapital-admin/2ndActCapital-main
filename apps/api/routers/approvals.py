"""BULKAPPROVE1 — bulk approval of maker-checker items (/approvals/…).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  any(view_authority|approve_authority|view_held_away|approve_held_away|manage_cash_policy)
      GET  /approvals/bulk?type=authority|held_away|cash_policy[&household_id=&account_id=]
           the PENDING items of that type — each with the server's own `actions` and, when none, the reason
           (you made it / no permission / no longer pending); the type also needs its own view_* or approve_*
      GET  /approvals/summary[?household_id=&account_id=]
           pending counts per readable type, for the "N pending — review in bulk" entry points
  any(approve_authority|approve_held_away|manage_cash_policy)
      POST /approvals/bulk {type, item_ids, decision, note}
           the type's OWN approval permission is then required (approve_authority / approve_held_away — 403
           naming it); each item goes through the SAME single-approval service function
           (services.authority.decide / services.held_away.decide_import / services.cash_policy.decide — CASH02), in its own
           transaction(s); per-item
           results (approved / rejected / refused + reason); at most services.bulk_approvals.MAX_ITEMS ids

Super Admin first (services.authz). org_id is only ever the caller's (get_org_id); every request model forbids
extras; another org's id is refused per item as not found.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import bulk_approvals as BA
from services.authz import require_any_permission
from services import family_visibility as FV
from services.database import get_pool

router = APIRouter(tags=["approvals"])

READ_KEYS = BA.READ_KEYS
APPROVE_KEYS = BA.APPROVE_KEYS


class BulkDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["authority", "held_away", "cash_policy"]
    # strings, not UUIDs: one malformed id is refused on its own line and never stops the others
    item_ids: list[str] = Field(min_length=1, max_length=BA.MAX_ITEMS)
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)


def _type_read_or_403(principal, type_key: str) -> BA.BulkType:
    try:
        bt = BA.bulk_type(type_key)
    except BA.BulkError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not (BA.holds(principal, bt.view) or BA.holds(principal, bt.approve)):
        raise HTTPException(status_code=403, detail=f"Permission required: {bt.view} or {bt.approve}")
    return bt


def _permissions(principal, bt: BA.BulkType) -> dict:
    can_approve = BA.holds(principal, bt.approve)
    return {"can_read": True, "can_write": can_approve, "can_approve": can_approve,
            "is_super_admin": bool(principal.is_super_admin), "read_permission": bt.view,
            "write_permission": bt.approve, "approve_permission": bt.approve}


@router.get("/approvals/bulk")
async def pending(request: Request, type: str = "authority", household_id: UUID | None = None,
                  account_id: UUID | None = None):
    principal = require_any_permission(request, READ_KEYS)
    bt = _type_read_or_403(principal, type)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        items = await BA.pending(conn, org_id, principal, bt.key, household_id=household_id, account_id=account_id)
    perms = _permissions(principal, bt)
    v = BA.vocabularies(items, bt.key)
    v["editable"] = ["decision"] if perms["can_approve"] else []
    v["inline_editable"] = []
    return {"type": bt.key, "type_label": bt.label, "rows": items, "permissions": perms, "vocabularies": v,
            "limits": {"max_items": BA.MAX_ITEMS},
            "tabs": [{"key": k, "label": t.label, "readable": BA.holds(principal, t.view) or
                      BA.holds(principal, t.approve)} for k, t in BA.TYPES.items()]}


@router.get("/approvals/summary")
async def summary(request: Request, household_id: UUID | None = None, account_id: UUID | None = None):
    principal = require_any_permission(request, READ_KEYS)
    org_id = get_org_id(request)
    pool = await get_pool()
    staff = await FV.scope_for_request(request, pool)     # VISIBILITY1 — counts over visible families only
    keep = None if staff is None else (lambda item: FV.row_visible(item, staff))
    async with pool.acquire() as conn:
        out = await BA.summary(conn, org_id, principal, household_id=household_id, account_id=account_id,
                               keep=keep)
    return {"types": out, "link": "/portfolio/approvals"}


@router.post("/approvals/bulk")
async def decide(request: Request, body: BulkDecision):
    principal = require_any_permission(request, APPROVE_KEYS)
    org_id = get_org_id(request)
    pool = await get_pool()
    staff = await FV.scope_for_request(request, pool)
    if staff is not None:   # VISIBILITY1 — a restricted caller decides only items of families they can see
        try:
            async with pool.acquire() as conn:   # FIX 1 (E): an internal, ungated helper — the route's gate unchanged
                visible = await BA.visible_item_ids(conn, org_id, principal, body.type,
                                                    lambda item: FV.row_visible(item, staff))
        except BA.BulkError as exc:            # an unknown type is the same 422 decide_bulk gives, never a 500
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if any(str(x) not in visible for x in body.item_ids):
            raise HTTPException(status_code=404, detail="Not found")
    try:
        return await BA.decide_bulk(pool, org_id=org_id, caller=principal, type_key=body.type,
                                    item_ids=body.item_ids, decision=body.decision, note=body.note)
    except BA.BulkPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except BA.BulkError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
