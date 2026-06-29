"""SPV Manager endpoints (Sprint 12).

Routes:
  POST   /spvs                            — create SPV (staff)
  GET    /spvs                            — list SPVs
  GET    /spvs/{id}                       — SPV detail
  PATCH  /spvs/{id}                       — update metadata (staff)
  POST   /spvs/{id}/status               — transition status (staff)
  POST   /spvs/{id}/form-entity          — set vehicle entity (staff)
  POST   /spvs/{id}/subscriptions        — subscribe (member)
  PATCH  /spvs/{spv_id}/subscriptions/{sub_id} — amend subscription (bi-temporal)
  GET    /spvs/{id}/captable             — cap table (staff)
  GET    /spvs/{id}/documents            — list documents
  POST   /spvs/{id}/documents            — upload document (staff)
  GET    /spvs/{id}/history              — status history
"""
import os
import uuid as _uuid
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from routers.entities import get_org_id
from schemas.spv import (
    CapTableEntry,
    CapTableResponse,
    SPVCreate,
    SPVDocumentResponse,
    SPVFormEntityUpdate,
    SPVResponse,
    SPVStatusUpdate,
    SPVUpdate,
    StatusHistoryEntry,
    SubscriptionAmend,
    SubscriptionCreate,
    SubscriptionResponse,
)
from services.audit import write_audit_log
from services.database import get_pool
from services.permissions import get_user_id, is_staff, require_permission
from services.storage import upload_bytes
from services.users import ensure_user

router = APIRouter(tags=["spvs"])

# Allowed forward transitions per status (any→cancelled always allowed).
SPV_STATUS_TRANSITIONS = {
    "forming": {"open", "cancelled"},
    "open": {"closing", "cancelled"},
    "closing": {"closed", "cancelled"},
    "closed": {"cancelled"},
    "cancelled": set(),
}

# Statuses visible to ordinary members.
MEMBER_VISIBLE_STATUSES = ("open", "closing")

# DB column is spv_status; alias to status for the response.
SPV_SELECT = (
    "id, org_id, deal_id, name, spv_status, target_raise, minimum_raise, "
    "hard_cap, min_commitment, carry_pct, mgmt_fee_pct, vehicle_entity_id, "
    "close_date, created_by, created_at, updated_at"
)

# DB column is subscription_status; alias to status for the response.
SUB_SELECT = (
    "id, org_id, spv_id, entity_id, commitment_amount, funded_amount, "
    "subscription_status, ownership_pct, signed_at, valid_from, valid_to, created_by, created_at"
)

# DB columns: title, storage_key, doc_type — aliased to frontend-friendly names.
DOC_SELECT = (
    "id, org_id, spv_id, title AS file_name, storage_key AS r2_key, "
    "doc_type AS document_type, status, uploaded_by, created_at"
)


def _f(v):
    return float(v) if v is not None else None


def _spv_response(row) -> SPVResponse:
    d = dict(row)
    d["status"] = d.pop("spv_status")  # remap DB column name to response field
    return SPVResponse(
        **{
            **d,
            "target_raise": _f(d.get("target_raise")),
            "minimum_raise": _f(d.get("minimum_raise")),
            "hard_cap": _f(d.get("hard_cap")),
            "min_commitment": _f(d.get("min_commitment")),
            "carry_pct": _f(d.get("carry_pct")),
            "mgmt_fee_pct": _f(d.get("mgmt_fee_pct")),
        }
    )


def _sub_response(row) -> SubscriptionResponse:
    d = dict(row)
    d["status"] = d.pop("subscription_status")  # remap DB column name to response field
    return SubscriptionResponse(
        **{
            **d,
            "commitment_amount": _f(d.get("commitment_amount")),
            "funded_amount": _f(d.get("funded_amount")),
            "ownership_pct": _f(d.get("ownership_pct")),
        }
    )


async def _fetch_spv(conn, org_id, spv_id: UUID):
    return await conn.fetchrow(
        f"SELECT {SPV_SELECT} FROM spvs WHERE id = $1 AND org_id = $2",
        spv_id,
        org_id,
    )


# ---------------------------------------------------------------------------
# Create SPV
# ---------------------------------------------------------------------------
@router.post("/spvs", response_model=SPVResponse, status_code=201)
async def create_spv(request: Request, body: SPVCreate):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                INSERT INTO spvs
                    (org_id, deal_id, name, spv_status, target_raise, minimum_raise,
                     hard_cap, min_commitment, carry_pct, mgmt_fee_pct, close_date,
                     created_by)
                VALUES ($1, $2, $3, 'forming', $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING {SPV_SELECT}
                """,
                org_id,
                body.deal_id,
                body.name,
                body.target_raise,
                body.minimum_raise,
                body.hard_cap,
                body.min_commitment,
                body.carry_pct,
                body.mgmt_fee_pct,
                body.close_date,
                user_id,
            )
            await conn.execute(
                """
                INSERT INTO spv_status_history
                    (org_id, spv_id, from_status, to_status, note, changed_by)
                VALUES ($1, $2, NULL, 'forming', 'SPV created', $3)
                """,
                org_id,
                row["id"],
                user_id,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="create",
                table_name="spvs",
                record_id=row["id"],
                new=dict(row),
            )
    return _spv_response(row)


# ---------------------------------------------------------------------------
# List SPVs
# ---------------------------------------------------------------------------
@router.get("/spvs", response_model=list[SPVResponse])
async def list_spvs(
    request: Request,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    org_id = get_org_id(request)
    staff = is_staff(request)

    conditions = ["org_id = $1"]
    params: list = [org_id]

    if status:
        params.append(status)
        conditions.append(f"spv_status = ${len(params)}")
    elif not staff:
        params.append(list(MEMBER_VISIBLE_STATUSES))
        conditions.append(f"spv_status = ANY(${len(params)})")

    params.append(limit)
    params.append(offset)
    query = (
        f"SELECT {SPV_SELECT} FROM spvs "
        f"WHERE {' AND '.join(conditions)} "
        f"ORDER BY created_at DESC NULLS LAST "
        f"LIMIT ${len(params) - 1} OFFSET ${len(params)}"
    )
    pool = await get_pool()
    rows = await pool.fetch(query, *params)
    return [_spv_response(r) for r in rows]


# ---------------------------------------------------------------------------
# Get single SPV
# ---------------------------------------------------------------------------
@router.get("/spvs/{spv_id}", response_model=SPVResponse)
async def get_spv(request: Request, spv_id: UUID):
    org_id = get_org_id(request)
    staff = is_staff(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await _fetch_spv(conn, org_id, spv_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SPV not found")
    if not staff and row["spv_status"] not in MEMBER_VISIBLE_STATUSES:
        raise HTTPException(status_code=404, detail="SPV not found")
    return _spv_response(row)


# ---------------------------------------------------------------------------
# Update SPV metadata
# ---------------------------------------------------------------------------
@router.patch("/spvs/{spv_id}", response_model=SPVResponse)
async def update_spv(request: Request, spv_id: UUID, body: SPVUpdate):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    pool = await get_pool()

    editable = (
        "name", "deal_id", "target_raise", "minimum_raise", "hard_cap",
        "min_commitment", "carry_pct", "mgmt_fee_pct", "close_date",
    )
    set_clauses = ["updated_at = now()"]
    params: list = [spv_id, org_id]
    for field in editable:
        if field in updates:
            params.append(updates[field])
            set_clauses.append(f"{field} = ${len(params)}")

    async with pool.acquire() as conn:
        async with conn.transaction():
            old = await _fetch_spv(conn, org_id, spv_id)
            if old is None:
                raise HTTPException(status_code=404, detail="SPV not found")
            row = await conn.fetchrow(
                f"""
                UPDATE spvs SET {', '.join(set_clauses)}
                WHERE id = $1 AND org_id = $2
                RETURNING {SPV_SELECT}
                """,
                *params,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="update",
                table_name="spvs",
                record_id=spv_id,
                old=dict(old),
                new=dict(row),
            )
    return _spv_response(row)


# ---------------------------------------------------------------------------
# Status transition
# ---------------------------------------------------------------------------
@router.post("/spvs/{spv_id}/status", response_model=SPVResponse)
async def transition_spv_status(request: Request, spv_id: UUID, body: SPVStatusUpdate):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    target = body.status
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            current = await _fetch_spv(conn, org_id, spv_id)
            if current is None:
                raise HTTPException(status_code=404, detail="SPV not found")

            src = current["spv_status"]
            allowed = SPV_STATUS_TRANSITIONS.get(src, set())
            # cancelled is always allowed except from cancelled itself.
            if src != "cancelled":
                allowed = allowed | {"cancelled"}
            if target not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid transition: {src} -> {target}",
                )

            row = await conn.fetchrow(
                f"""
                UPDATE spvs SET spv_status = $3, updated_at = now()
                WHERE id = $1 AND org_id = $2
                RETURNING {SPV_SELECT}
                """,
                spv_id,
                org_id,
                target,
            )
            await conn.execute(
                """
                INSERT INTO spv_status_history
                    (org_id, spv_id, from_status, to_status, note, changed_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                org_id,
                spv_id,
                src,
                target,
                body.note,
                user_id,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="status_change",
                table_name="spvs",
                record_id=spv_id,
                old={"spv_status": src},
                new={"spv_status": target},
            )
    return _spv_response(row)


# ---------------------------------------------------------------------------
# Set vehicle entity
# ---------------------------------------------------------------------------
@router.post("/spvs/{spv_id}/form-entity", response_model=SPVResponse)
async def set_form_entity(request: Request, spv_id: UUID, body: SPVFormEntityUpdate):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        async with conn.transaction():
            spv = await _fetch_spv(conn, org_id, spv_id)
            if spv is None:
                raise HTTPException(status_code=404, detail="SPV not found")
            entity_ok = await conn.fetchval(
                "SELECT 1 FROM entities WHERE id = $1 AND org_id = $2 AND valid_to IS NULL",
                body.entity_id,
                org_id,
            )
            if not entity_ok:
                raise HTTPException(status_code=400, detail="Unknown entity")
            row = await conn.fetchrow(
                f"""
                UPDATE spvs SET vehicle_entity_id = $3, updated_at = now()
                WHERE id = $1 AND org_id = $2
                RETURNING {SPV_SELECT}
                """,
                spv_id,
                org_id,
                body.entity_id,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="set_vehicle_entity",
                table_name="spvs",
                record_id=spv_id,
                new={"vehicle_entity_id": str(body.entity_id)},
            )
    return _spv_response(row)


# ---------------------------------------------------------------------------
# Subscribe to SPV
# ---------------------------------------------------------------------------
@router.post("/spvs/{spv_id}/subscriptions", response_model=SubscriptionResponse, status_code=201)
async def subscribe_to_spv(request: Request, spv_id: UUID, body: SubscriptionCreate):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        async with conn.transaction():
            spv = await _fetch_spv(conn, org_id, spv_id)
            if spv is None:
                raise HTTPException(status_code=404, detail="SPV not found")
            if spv["spv_status"] not in ("open", "closing"):
                raise HTTPException(
                    status_code=400,
                    detail="Subscriptions only accepted when SPV is open or closing",
                )

            entity_ok = await conn.fetchval(
                "SELECT 1 FROM entities WHERE id = $1 AND org_id = $2 AND valid_to IS NULL",
                body.entity_id,
                org_id,
            )
            if not entity_ok:
                raise HTTPException(status_code=400, detail="Unknown entity")

            # If active subscription exists for this entity, close it first (amend path).
            existing = await conn.fetchrow(
                "SELECT id FROM spv_subscriptions "
                "WHERE spv_id = $1 AND entity_id = $2 AND valid_to IS NULL",
                spv_id,
                body.entity_id,
            )
            if existing:
                await conn.execute(
                    "UPDATE spv_subscriptions SET valid_to = now() WHERE id = $1",
                    existing["id"],
                )

            row = await conn.fetchrow(
                f"""
                INSERT INTO spv_subscriptions
                    (org_id, spv_id, entity_id, commitment_amount, subscription_status,
                     valid_from, created_by)
                VALUES ($1, $2, $3, $4, 'soft', now(), $5)
                RETURNING {SUB_SELECT}
                """,
                org_id,
                spv_id,
                body.entity_id,
                body.commitment_amount,
                user_id,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="subscribe",
                table_name="spv_subscriptions",
                record_id=row["id"],
                new=dict(row),
            )
    return _sub_response(row)


# ---------------------------------------------------------------------------
# Amend subscription (bi-temporal)
# ---------------------------------------------------------------------------
@router.patch("/spvs/{spv_id}/subscriptions/{sub_id}", response_model=SubscriptionResponse)
async def amend_subscription(
    request: Request, spv_id: UUID, sub_id: UUID, body: SubscriptionAmend
):
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        async with conn.transaction():
            old = await conn.fetchrow(
                f"SELECT {SUB_SELECT} FROM spv_subscriptions "
                "WHERE id = $1 AND spv_id = $2 AND org_id = $3 AND valid_to IS NULL",
                sub_id,
                spv_id,
                org_id,
            )
            if old is None:
                raise HTTPException(status_code=404, detail="Active subscription not found")

            # Step 1 — close the old row.
            await conn.execute(
                "UPDATE spv_subscriptions SET valid_to = now() WHERE id = $1",
                sub_id,
            )
            # Step 2 — insert the amended row.
            row = await conn.fetchrow(
                f"""
                INSERT INTO spv_subscriptions
                    (org_id, spv_id, entity_id, commitment_amount, funded_amount,
                     subscription_status, ownership_pct, signed_at, valid_from, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now(), $9)
                RETURNING {SUB_SELECT}
                """,
                org_id,
                spv_id,
                old["entity_id"],
                body.commitment_amount,
                old["funded_amount"],
                old["subscription_status"],
                old["ownership_pct"],
                old["signed_at"],
                user_id,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="amend",
                table_name="spv_subscriptions",
                record_id=row["id"],
                old={"commitment_amount": float(old["commitment_amount"]) if old["commitment_amount"] else None},
                new={"commitment_amount": body.commitment_amount},
            )
    return _sub_response(row)


# ---------------------------------------------------------------------------
# Cap table
# ---------------------------------------------------------------------------
@router.get("/spvs/{spv_id}/captable", response_model=CapTableResponse)
async def get_captable(request: Request, spv_id: UUID):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        spv = await _fetch_spv(conn, org_id, spv_id)
        if spv is None:
            raise HTTPException(status_code=404, detail="SPV not found")

        rows = await conn.fetch(
            """
            SELECT s.id AS subscription_id, s.entity_id,
                   COALESCE(e.display_name, e.legal_name, s.entity_id::text) AS entity_name,
                   s.commitment_amount, s.funded_amount,
                   s.ownership_pct, s.subscription_status AS status, s.signed_at
            FROM spv_subscriptions s
            LEFT JOIN entities e ON e.id = s.entity_id
              AND e.valid_to IS NULL
            WHERE s.spv_id = $1 AND s.org_id = $2 AND s.valid_to IS NULL
            ORDER BY s.commitment_amount DESC NULLS LAST
            """,
            spv_id,
            org_id,
        )

    total_committed = sum(_f(r["commitment_amount"]) or 0 for r in rows)
    total_funded = sum(_f(r["funded_amount"]) or 0 for r in rows)

    return CapTableResponse(
        spv_id=spv_id,
        spv_name=spv["name"],
        total_committed=total_committed,
        total_funded=total_funded,
        target_raise=_f(spv["target_raise"]),
        subscriptions=[
            CapTableEntry(
                subscription_id=r["subscription_id"],
                entity_id=r["entity_id"],
                entity_name=r["entity_name"],
                commitment_amount=_f(r["commitment_amount"]) or 0,
                funded_amount=_f(r["funded_amount"]),
                ownership_pct=_f(r["ownership_pct"]),
                status=r["status"],
                signed_at=r["signed_at"],
            )
            for r in rows
        ],
    )


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
@router.get("/spvs/{spv_id}/documents", response_model=list[SPVDocumentResponse])
async def list_spv_documents(request: Request, spv_id: UUID):
    org_id = get_org_id(request)
    staff = is_staff(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        spv = await _fetch_spv(conn, org_id, spv_id)
        if spv is None:
            raise HTTPException(status_code=404, detail="SPV not found")
        if not staff and spv["spv_status"] not in MEMBER_VISIBLE_STATUSES:
            raise HTTPException(status_code=404, detail="SPV not found")
        rows = await conn.fetch(
            f"SELECT {DOC_SELECT} FROM spv_documents WHERE spv_id = $1 AND org_id = $2 "
            f"ORDER BY created_at DESC NULLS LAST",
            spv_id,
            org_id,
        )
    return [SPVDocumentResponse(**dict(r)) for r in rows]


@router.post("/spvs/{spv_id}/documents", response_model=SPVDocumentResponse, status_code=201)
async def upload_spv_document(
    request: Request,
    spv_id: UUID,
    file: UploadFile = File(...),
    document_type: str | None = Form(None),
):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    pool = await get_pool()

    data = await file.read()
    key = f"spvs/{spv_id}/{_uuid.uuid4()}_{file.filename}"

    async with pool.acquire() as conn:
        spv = await _fetch_spv(conn, org_id, spv_id)
        if spv is None:
            raise HTTPException(status_code=404, detail="SPV not found")

        bucket = os.environ.get("R2_BUCKET_NAME", "2ndactcapital-docs")
        await run_in_threadpool(upload_bytes, key, data, file.content_type, bucket)

        async with conn.transaction():
            row = await conn.fetchrow(
                f"""
                INSERT INTO spv_documents
                    (org_id, spv_id, title, storage_key, doc_type, uploaded_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING {DOC_SELECT}
                """,
                org_id,
                spv_id,
                file.filename,
                key,
                document_type or "general",
                user_id,
            )
            await write_audit_log(
                conn,
                org_id=org_id,
                action="upload",
                table_name="spv_documents",
                record_id=row["id"],
                new=dict(row),
            )
    return SPVDocumentResponse(**dict(row))


# ---------------------------------------------------------------------------
# Status history
# ---------------------------------------------------------------------------
@router.get("/spvs/{spv_id}/history", response_model=list[StatusHistoryEntry])
async def get_spv_history(request: Request, spv_id: UUID):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        spv = await _fetch_spv(conn, org_id, spv_id)
        if spv is None:
            raise HTTPException(status_code=404, detail="SPV not found")
        rows = await conn.fetch(
            "SELECT id, from_status, to_status, note, changed_by, changed_at AS created_at "
            "FROM spv_status_history WHERE spv_id = $1 AND org_id = $2 "
            "ORDER BY changed_at ASC",
            spv_id,
            org_id,
        )
    return [StatusHistoryEntry(**dict(r)) for r in rows]
