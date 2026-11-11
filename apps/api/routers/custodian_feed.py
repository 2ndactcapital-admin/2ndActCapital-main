"""ALTREAD1 — what the custodian reports for an account (/custodian-feed/…): its balance (as of …), its tax lots from
the cost-basis feed, and the reconciliation against Hollisworks' own lots. READ ONLY — the lot-source setting is
changed through the existing lot-method account change (/lot-method/accounts/{id}/changes, maker-checker).

Every route names its gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it):

  view_proposals   GET /custodian-feed/accounts/{id}    balance(s), custodian lots, reconciliation, effective lot source

The lot source is a lot-method (proposal) setting, so this panel reads under the same permission as the lot-method
panel. The account is scoped to the caller's family exactly as every other family resource: ``require_entity_access``
on the account's primary entity (a member sees only their own family's accounts; another family's or org's id is a
404). org_id is only ever the caller's (get_org_id). The envelope publishes ``permissions`` and empty
``vocabularies.editable`` (nothing is edited here).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from routers.entities import get_org_id
from services import custodian_lots as CL
from services import lot_method as LM
from services.authz import require_entity_access, require_permission
from services.database import get_pool

router = APIRouter(tags=["custodian-feed"])

VIEW = LM.VIEW


@router.get("/custodian-feed/accounts/{account_id}")
async def account_feed(request: Request, account_id: UUID):
    principal = require_permission(request, VIEW)
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        owner = await conn.fetchval(
            "SELECT primary_entity_id FROM public.accounts WHERE id = $1::uuid AND org_id = $2::uuid "
            "AND valid_to IS NULL AND system_to IS NULL", str(account_id), str(org_id))
    if owner is None:
        raise HTTPException(status_code=404, detail="account not found")
    await require_entity_access(request, pool, owner)
    async with pool.acquire() as conn:
        panel = await CL.account_panel(conn, org_id, str(account_id))
    has = (lambda p: principal.is_super_admin or p in principal.permissions)
    return {**panel, "account_id": str(account_id),
            "permissions": {"can_read": True, "can_write": False, "is_super_admin": principal.is_super_admin,
                            "read_permission": VIEW, "write_permission": None,
                            "can_request_lot_source": has(LM.MANAGE), "lot_source_permission": LM.MANAGE},
            "vocabularies": {"editable": [], "inline_editable": [],
                             "lot_sources": [{"key": k, "label": v} for k, v in LM.LOT_SOURCE_LABELS.items()]}}
