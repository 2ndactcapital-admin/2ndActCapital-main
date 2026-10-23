"""REP2 — custodian capabilities (/custodians).

  staff         GET /custodians/capabilities                          matrix + envelope
                GET /custodians/capabilities/{code}/{key}/history     every version of one cell
  super admin   PUT /custodians/capabilities/{code}/{key}             record a new version

Platform data: every org reads the same record; only Super Admin writes it
(the RLS write policy is ``app.is_super_admin`` as well). Every edit is a new
version with its own evidence and as-of date; the old one is kept. 'unknown'
is shown plainly and is treated as NOT available by every caller.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from services import custodian_capabilities as CC
from services.authz import require_staff, require_super_admin
from services.database import get_pool

router = APIRouter(tags=["custodians"])


class CapabilityEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=10)
    evidence_kind: str = Field(min_length=1, max_length=20)
    evidence: str = Field(min_length=1, max_length=2000)
    as_of: date
    note: str | None = Field(default=None, max_length=2000)


def _permissions(principal) -> dict:
    return {"can_read": True, "can_write": bool(principal.is_super_admin),
            "is_super_admin": bool(principal.is_super_admin),
            "read_permission": "staff", "write_permission": "super_admin"}


@router.get("/custodians/capabilities")
async def capabilities(request: Request):
    principal = require_staff(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        m = await CC.matrix(conn)
    perms = _permissions(principal)
    vocab = CC.vocabularies()
    vocab["editable"] = list(CC.CAPABILITY_KEYS) if perms["can_write"] else []
    return {"rows": m["custodians"], "permissions": perms, "vocabularies": vocab}


@router.get("/custodians/capabilities/{custodian_code}/{capability_key}/history")
async def capability_history(request: Request, custodian_code: str, capability_key: str):
    require_staff(request)
    if capability_key not in CC.CAPABILITY_LABELS:
        raise HTTPException(status_code=404, detail="unknown capability")
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await CC.history(conn, custodian_code, capability_key)
    return {"rows": rows}


@router.put("/custodians/capabilities/{custodian_code}/{capability_key}")
async def set_capability(request: Request, custodian_code: str, capability_key: str, body: CapabilityEdit):
    principal = require_super_admin(request)
    if capability_key not in CC.CAPABILITY_LABELS:
        raise HTTPException(status_code=404, detail="unknown capability")
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            row = await CC.set_capability(
                conn, custodian_code=custodian_code, key=capability_key, value=body.value,
                evidence_kind=body.evidence_kind, evidence=body.evidence, as_of=body.as_of,
                actor_id=principal.user_id, actor_org_id=principal.org_id,
                is_super_admin=principal.is_super_admin, note=body.note)
    except CC.CapabilityPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except CC.CapabilityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"capability": row}
