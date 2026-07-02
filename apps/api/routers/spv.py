"""SPV Manager endpoints (Sprint 12 + Sprint 14).

Routes:
  POST   /spvs                                          — create SPV (staff)
  GET    /spvs                                          — list SPVs
  GET    /spvs/{id}                                     — SPV detail
  PATCH  /spvs/{id}                                     — update metadata (staff)
  POST   /spvs/{id}/status                              — transition status (staff)
  POST   /spvs/{id}/form-entity                         — set vehicle entity (staff)
  POST   /spvs/{id}/subscriptions                       — subscribe (member)
  PATCH  /spvs/{spv_id}/subscriptions/{sub_id}          — amend subscription (bi-temporal)
  GET    /spvs/{id}/captable                            — cap table (staff)
  GET    /spvs/{id}/documents                           — list documents
  POST   /spvs/{id}/documents                           — upload document (staff)
  GET    /spvs/{id}/history                             — status history

  Sprint 14 — Transaction Ledger:
  POST   /spvs/{id}/transactions                        — create draft transaction (staff)
  GET    /spvs/{id}/transactions                        — list transactions
  PATCH  /spvs/{id}/transactions/{txn_id}               — edit draft only (staff)
  POST   /spvs/{id}/transactions/{txn_id}/allocate      — compute+persist allocations (staff)
  POST   /spvs/{id}/transactions/{txn_id}/post          — post an allocated txn (staff)
  POST   /spvs/{id}/transactions/{txn_id}/void          — void a transaction (staff)
  GET    /spvs/{id}/transactions/{txn_id}/allocations   — allocation rows (staff)
  GET    /spvs/{id}/ledger                              — full ledger view (staff)
"""
import os
import uuid as _uuid
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from routers.entities import get_org_id
from schemas.spv import (
    AllocationRow,
    CapTableEntry,
    CapTableResponse,
    LedgerResponse,
    LedgerSummary,
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
    TransactionCreate,
    TransactionResponse,
    TransactionTypeResponse,
    TransactionUpdate,
)
from services.transaction_types import get_types as get_txn_types
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
        deal_exists = await conn.fetchval(
            "SELECT 1 FROM deals WHERE id = $1 AND org_id = $2",
            body.deal_id,
            org_id,
        )
        if not deal_exists:
            raise HTTPException(status_code=400, detail=f"Deal {body.deal_id} not found")
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


# ===========================================================================
# Sprint 19 — Transaction Types
# ===========================================================================

@router.get("/transaction-types", response_model=list[TransactionTypeResponse])
async def list_transaction_types(
    request: Request,
    category: str | None = Query(None),
    security_type: str | None = Query(None),
):
    org_id = get_org_id(request)
    pool = await get_pool()
    items = await get_txn_types(pool, str(org_id), category=category, security_type=security_type)
    return [TransactionTypeResponse(**it) for it in items]


# ===========================================================================
# Sprint 14 — Transaction Ledger
# ===========================================================================

_TXN_TYPES = ("capital_call", "distribution", "fee", "return_of_capital")
_ALLOC_BASIS = ("ownership_pct", "committed", "funded")
_AMOUNT_BASIS = ("currency", "units", "percent")

_TXN_SELECT = (
    "id, org_id, spv_id, txn_type, txn_date, amount, description, reference, "
    "allocation_basis, status, allocated_at, posted_at, "
    "transaction_type_id, currency_code, amount_basis, "
    "created_by, created_at, updated_at"
)
_ALLOC_SELECT = (
    "id, org_id, transaction_id, subscription_id, allocated_amount, "
    "ownership_pct, status, created_at"
)


def _txn_response(row) -> TransactionResponse:
    d = dict(row)
    d["amount"] = float(d["amount"]) if d.get("amount") is not None else 0.0
    return TransactionResponse(**d)


def _alloc_response(row) -> AllocationRow:
    d = dict(row)
    d["allocated_amount"] = float(d["allocated_amount"])
    d["ownership_pct"] = float(d["ownership_pct"])
    return AllocationRow(**d)


# ---------------------------------------------------------------------------
# POST /spvs/{spv_id}/transactions
# ---------------------------------------------------------------------------
@router.post("/spvs/{spv_id}/transactions", response_model=TransactionResponse, status_code=201)
async def create_transaction(request: Request, spv_id: UUID, body: TransactionCreate):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    if body.allocation_basis not in _ALLOC_BASIS:
        raise HTTPException(status_code=400, detail=f"Invalid allocation_basis; allowed: {_ALLOC_BASIS}")

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        spv = await _fetch_spv(conn, org_id, spv_id)
        if spv is None:
            raise HTTPException(status_code=404, detail="SPV not found")

        # Resolve transaction type: prefer transaction_type_id, fall back to txn_type string.
        txn_type = body.txn_type
        transaction_type_id = body.transaction_type_id
        amount_basis = body.amount_basis

        if transaction_type_id is not None:
            type_row = await conn.fetchrow(
                "SELECT code, amount_basis FROM transaction_types WHERE id = $1 AND is_active = true",
                transaction_type_id,
            )
            if type_row is None:
                raise HTTPException(status_code=400, detail="transaction_type_id not found or inactive")
            txn_type = type_row["code"]
            amount_basis = type_row["amount_basis"]
        elif txn_type is not None:
            if txn_type not in _TXN_TYPES:
                raise HTTPException(status_code=400, detail=f"Invalid txn_type; allowed: {_TXN_TYPES}")
        else:
            raise HTTPException(status_code=400, detail="Either txn_type or transaction_type_id is required")

        try:
            row = await conn.fetchrow(
                f"""
                INSERT INTO spv_transactions
                    (org_id, spv_id, txn_type, txn_date, amount, description, reference,
                     allocation_basis, transaction_type_id, currency_code, amount_basis,
                     status, created_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'draft', $12)
                RETURNING {_TXN_SELECT}
                """,
                org_id, spv_id, txn_type, body.txn_date, body.amount,
                body.description, body.reference, body.allocation_basis,
                transaction_type_id, body.currency_code, amount_basis, user_id,
            )
        except Exception as exc:
            import traceback
            print(f"ERROR create_transaction (spv={spv_id}): {exc}")
            print(traceback.format_exc())
            raise
        await write_audit_log(
            conn, org_id=org_id, action="create", table_name="spv_transactions",
            record_id=row["id"], new=dict(row),
        )
    return _txn_response(row)


# ---------------------------------------------------------------------------
# GET /spvs/{spv_id}/transactions
# ---------------------------------------------------------------------------
@router.get("/spvs/{spv_id}/transactions", response_model=list[TransactionResponse])
async def list_transactions(request: Request, spv_id: UUID):
    org_id = get_org_id(request)
    staff = is_staff(request)
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            user_id = await ensure_user(conn, request)
            spv = await _fetch_spv(conn, org_id, spv_id)
            if spv is None:
                raise HTTPException(status_code=404, detail="SPV not found")

            if staff:
                rows = await conn.fetch(
                    f"SELECT {_TXN_SELECT} FROM spv_transactions "
                    "WHERE spv_id = $1 AND org_id = $2 "
                    "ORDER BY txn_date DESC, created_at DESC",
                    spv_id, org_id,
                )
            else:
                # Members see only transactions where they have an allocation
                rows = await conn.fetch(
                    f"""
                    SELECT DISTINCT ON (t.id) {_TXN_SELECT.replace('id,', 't.id,').replace(', ', ', t.')}
                    FROM spv_transactions t
                    JOIN spv_transaction_allocations sta ON sta.transaction_id = t.id
                    JOIN spv_subscriptions ss ON ss.id = sta.subscription_id
                    JOIN member_investments mi ON mi.id = ss.member_investment_id
                    WHERE t.spv_id = $1 AND t.org_id = $2 AND mi.user_id = $3
                    ORDER BY t.id, t.txn_date DESC, t.created_at DESC
                    """,
                    spv_id, org_id, user_id,
                )
    except HTTPException:
        raise
    except Exception as exc:
        import traceback
        print(f"ERROR list_transactions (spv={spv_id}): {exc}")
        print(traceback.format_exc())
        raise
    return [_txn_response(r) for r in rows]


# ---------------------------------------------------------------------------
# PATCH /spvs/{spv_id}/transactions/{txn_id}
# ---------------------------------------------------------------------------
@router.patch("/spvs/{spv_id}/transactions/{txn_id}", response_model=TransactionResponse)
async def patch_transaction(request: Request, spv_id: UUID, txn_id: UUID, body: TransactionUpdate):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    pool = await get_pool()

    if "txn_type" in updates and updates["txn_type"] not in _TXN_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid txn_type; allowed: {_TXN_TYPES}")
    if "allocation_basis" in updates and updates["allocation_basis"] not in _ALLOC_BASIS:
        raise HTTPException(status_code=400, detail=f"Invalid allocation_basis; allowed: {_ALLOC_BASIS}")

    async with pool.acquire() as conn:
        txn = await conn.fetchrow(
            f"SELECT {_TXN_SELECT} FROM spv_transactions WHERE id = $1 AND spv_id = $2 AND org_id = $3",
            txn_id, spv_id, org_id,
        )
        if txn is None:
            raise HTTPException(status_code=404, detail="Transaction not found")
        if txn["status"] != "draft":
            raise HTTPException(status_code=400, detail="Only draft transactions may be edited")

        editable = (
            "txn_type", "txn_date", "amount", "description", "reference",
            "allocation_basis", "currency_code", "amount_basis",
        )
        set_clauses = ["updated_at = now()"]
        params: list = [txn_id, spv_id, org_id]
        for field in editable:
            if field in updates:
                params.append(updates[field])
                set_clauses.append(f"{field} = ${len(params)}")

        row = await conn.fetchrow(
            f"UPDATE spv_transactions SET {', '.join(set_clauses)} "
            f"WHERE id = $1 AND spv_id = $2 AND org_id = $3 RETURNING {_TXN_SELECT}",
            *params,
        )
        await write_audit_log(
            conn, org_id=org_id, action="update", table_name="spv_transactions",
            record_id=txn_id, old=dict(txn), new=dict(row),
        )
    return _txn_response(row)


# ---------------------------------------------------------------------------
# POST /spvs/{spv_id}/transactions/{txn_id}/allocate
# ---------------------------------------------------------------------------
@router.post("/spvs/{spv_id}/transactions/{txn_id}/allocate")
async def allocate(request: Request, spv_id: UUID, txn_id: UUID):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        txn = await conn.fetchrow(
            "SELECT id, spv_id FROM spv_transactions WHERE id = $1 AND org_id = $2",
            txn_id, org_id,
        )
        if txn is None or str(txn["spv_id"]) != str(spv_id):
            raise HTTPException(status_code=404, detail="Transaction not found")

    from services.spv_allocation import allocate_transaction
    try:
        alloc_rows = await allocate_transaction(pool, str(txn_id), user_id)
    except (ValueError, AssertionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    total_allocated = sum(float(r["allocated_amount"]) for r in alloc_rows)
    return {
        "transaction_id": str(txn_id),
        "subscriber_count": len(alloc_rows),
        "total_allocated": total_allocated,
        "allocations": [
            {
                "subscription_id": r["subscription_id"],
                "allocated_amount": float(r["allocated_amount"]),
                "ownership_pct": float(r["ownership_pct"]),
            }
            for r in alloc_rows
        ],
    }


# ---------------------------------------------------------------------------
# POST /spvs/{spv_id}/transactions/{txn_id}/post
# ---------------------------------------------------------------------------
@router.post("/spvs/{spv_id}/transactions/{txn_id}/post", response_model=TransactionResponse)
async def post_txn(request: Request, spv_id: UUID, txn_id: UUID):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        txn = await conn.fetchrow(
            f"SELECT {_TXN_SELECT} FROM spv_transactions WHERE id = $1 AND org_id = $2",
            txn_id, org_id,
        )
        if txn is None or str(txn["spv_id"]) != str(spv_id):
            raise HTTPException(status_code=404, detail="Transaction not found")
        if txn["status"] != "allocated":
            raise HTTPException(status_code=400, detail="Transaction must be 'allocated' before posting")

    from services.spv_allocation import post_transaction
    try:
        await post_transaction(pool, str(txn_id), user_id)
    except (ValueError, AssertionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_TXN_SELECT} FROM spv_transactions WHERE id = $1", txn_id,
        )
    return _txn_response(row)


# ---------------------------------------------------------------------------
# POST /spvs/{spv_id}/transactions/{txn_id}/void
# ---------------------------------------------------------------------------
@router.post("/spvs/{spv_id}/transactions/{txn_id}/void", response_model=TransactionResponse)
async def void_transaction(request: Request, spv_id: UUID, txn_id: UUID):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
        txn = await conn.fetchrow(
            f"SELECT {_TXN_SELECT} FROM spv_transactions WHERE id = $1 AND org_id = $2",
            txn_id, org_id,
        )
        if txn is None or str(txn["spv_id"]) != str(spv_id):
            raise HTTPException(status_code=404, detail="Transaction not found")

        async with conn.transaction():
            await conn.execute(
                "UPDATE spv_transaction_allocations SET status = 'void' WHERE transaction_id = $1",
                txn_id,
            )
            row = await conn.fetchrow(
                f"UPDATE spv_transactions SET status = 'void', updated_at = now() "
                f"WHERE id = $1 RETURNING {_TXN_SELECT}",
                txn_id,
            )
            await write_audit_log(
                conn, org_id=org_id, action="void", table_name="spv_transactions",
                record_id=txn_id, new={"voided_by": user_id},
            )
    return _txn_response(row)


# ---------------------------------------------------------------------------
# GET /spvs/{spv_id}/transactions/{txn_id}/allocations
# ---------------------------------------------------------------------------
@router.get("/spvs/{spv_id}/transactions/{txn_id}/allocations", response_model=list[AllocationRow])
async def get_allocations(request: Request, spv_id: UUID, txn_id: UUID):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        txn = await conn.fetchrow(
            "SELECT id FROM spv_transactions WHERE id = $1 AND spv_id = $2 AND org_id = $3",
            txn_id, spv_id, org_id,
        )
        if txn is None:
            raise HTTPException(status_code=404, detail="Transaction not found")
        rows = await conn.fetch(
            f"SELECT {_ALLOC_SELECT} FROM spv_transaction_allocations "
            "WHERE transaction_id = $1 AND status = 'active' "
            "ORDER BY allocated_amount DESC",
            txn_id,
        )
    return [_alloc_response(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /spvs/{spv_id}/ledger
# ---------------------------------------------------------------------------
@router.get("/spvs/{spv_id}/ledger", response_model=LedgerResponse)
async def get_ledger(request: Request, spv_id: UUID):
    require_permission(request, "manage_deals")
    org_id = get_org_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        spv = await _fetch_spv(conn, org_id, spv_id)
        if spv is None:
            raise HTTPException(status_code=404, detail="SPV not found")

        try:
            txn_rows = await conn.fetch(
                f"SELECT {_TXN_SELECT} FROM spv_transactions "
                "WHERE spv_id = $1 AND org_id = $2 AND status != 'void' "
                "ORDER BY txn_date DESC, created_at DESC",
                spv_id, org_id,
            )

            # Compute summary from posted transactions using type attributes when available.
            # Falls back to legacy txn_type string matching for rows without a type_id.
            totals = await conn.fetchrow(
                """
                SELECT
                  COALESCE(SUM(CASE
                    WHEN tt.affects_paid_in > 0 THEN t.amount
                    WHEN t.transaction_type_id IS NULL AND t.txn_type = 'capital_call' THEN t.amount
                    ELSE 0
                  END), 0) AS total_called,
                  COALESCE(SUM(CASE
                    WHEN tt.affects_nav < 0 AND COALESCE(tt.is_recallable, false) = false THEN t.amount
                    WHEN t.transaction_type_id IS NULL AND t.txn_type = 'distribution' THEN t.amount
                    ELSE 0
                  END), 0) AS total_distributed,
                  COALESCE(SUM(CASE
                    WHEN tt.category = 'fee' THEN t.amount
                    WHEN t.transaction_type_id IS NULL AND t.txn_type IN ('fee', 'return_of_capital') THEN t.amount
                    ELSE 0
                  END), 0) AS total_fees,
                  COALESCE(SUM(CASE
                    WHEN COALESCE(tt.is_recallable, false) = true THEN t.amount
                    ELSE 0
                  END), 0) AS total_recallable
                FROM spv_transactions t
                LEFT JOIN transaction_types tt ON tt.id = t.transaction_type_id
                WHERE t.spv_id = $1 AND t.org_id = $2 AND t.status = 'posted'
                """,
                spv_id, org_id,
            )
        except Exception as exc:
            import traceback
            print(f"ERROR get_ledger (spv={spv_id}): {exc}")
            print(traceback.format_exc())
            raise

    total_called = float(totals["total_called"])
    total_distributed = float(totals["total_distributed"])
    total_fees = float(totals["total_fees"])
    total_recallable = float(totals["total_recallable"])
    net = total_called - total_distributed - total_fees

    return LedgerResponse(
        spv_id=spv_id,
        spv_name=spv["name"],
        summary=LedgerSummary(
            total_called=total_called,
            total_distributed=total_distributed,
            total_fees=total_fees,
            total_recallable=total_recallable,
            net=net,
        ),
        transactions=[_txn_response(r) for r in txn_rows],
    )
