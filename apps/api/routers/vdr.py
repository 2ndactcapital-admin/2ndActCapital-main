"""Chancery Phase 10 — VDR deal-proposal endpoints.

Thin HTTP layer over ``services.vdr_analysis``. Auth + org-scoping follow the
same pattern as the other Chancery routers: a valid JWT is enforced by the global
middleware in ``main.py``; ``org_id`` comes from JWT claims via
``routers.entities.get_org_id`` and is NEVER read from the request body; the
acting user is resolved with ``services.users.ensure_user`` (reviewer). Every DB
call runs on the RLS-scoped pool.

Deal CREATION on approval routes through the SAME shared core the marketplace
``POST /api/v1/deals`` endpoint uses (``services.deal_creation.insert_deal``) —
never a second, parallel deal-creation path — and approval requires the same
``manage_deals`` permission.

Routes (mounted under ``/api/v1``):
  POST /document-drops/{drop_id}/analyze-vdr   run aggregate VDR analysis → proposal
  GET  /vdr-proposals                          pending proposals (org)
  POST /vdr-proposals/{proposal_id}/approve    approve → create deal + link docs
  POST /vdr-proposals/{proposal_id}/reject     reject (no deal, no links)
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routers.entities import get_org_id
from services.database import get_pool
from services.permissions import require_permission
from services.users import ensure_user
from services import vdr_analysis as vdr

router = APIRouter(tags=["vdr"])


class ProposalApprove(BaseModel):
    # Human edits merged OVER the AI-proposed fields — e.g. corrected name,
    # description, or the real taxonomy KEYS (asset_super_class / asset_class /
    # asset_sub_category) the AI could not know. Omit to accept the proposal as-is.
    fields: dict | None = None


def _raise(err: "vdr.VDRAnalysisError"):
    raise HTTPException(status_code=err.status_code, detail=err.detail)


@router.post("/document-drops/{drop_id}/analyze-vdr")
async def analyze_vdr(drop_id: UUID, request: Request):
    """Aggregate-analyze a completed drop as a VDR and, if confident, create a
    pending proposal. Reports honestly when the batch is too weak to propose."""
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        created_by = await ensure_user(conn, request)
    try:
        return await vdr.analyze_drop(pool, org_id, drop_id, created_by=created_by)
    except vdr.VDRAnalysisError as err:
        _raise(err)


@router.get("/vdr-proposals")
async def list_vdr_proposals(request: Request):
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        proposals = await vdr.list_pending_proposals(conn, org_id)
    return {"proposals": proposals}


@router.post("/vdr-proposals/{proposal_id}/approve")
async def approve_vdr_proposal(proposal_id: UUID, request: Request,
                               body: ProposalApprove | None = None):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    body = body or ProposalApprove()
    pool = await get_pool()
    async with pool.acquire() as conn:
        reviewer = await ensure_user(conn, request)
        try:
            return await vdr.approve_proposal(
                conn, org_id, proposal_id,
                reviewed_by=reviewer, overrides=body.fields,
            )
        except vdr.VDRAnalysisError as err:
            _raise(err)


@router.post("/vdr-proposals/{proposal_id}/reject")
async def reject_vdr_proposal(proposal_id: UUID, request: Request):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        reviewer = await ensure_user(conn, request)
        try:
            return await vdr.reject_proposal(
                conn, org_id, proposal_id, reviewed_by=reviewer)
        except vdr.VDRAnalysisError as err:
            _raise(err)
