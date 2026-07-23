"""Ledger endpoints — Sprint 22.

Module 1 — Chart of Accounts:
  GET    /ledger/accounts                     list current accounts (or as_of)
  POST   /ledger/accounts                     create account
  PATCH  /ledger/accounts/{id}                bi-temporal update

Module 2 — Posting engine:
  POST   /ledger/entries                      build draft entry
  POST   /ledger/entries/{id}/post            post draft
  POST   /ledger/entries/{id}/reverse         reverse posted entry
  GET    /ledger/entries                      list entries by vehicle

Module 3 — Reporting:
  GET    /ledger/trial-balance                query v_trial_balance
  GET    /ledger/capital-accounts             query v_capital_accounts

Extras:
  GET    /ledger/templates                    template metadata for UI preview

Tenancy: org_id resolved from JWT (COA) or from spvs.org_id (entries).
         Never accepted from caller in any request field.
Money: Decimal everywhere.
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from routers.entities import get_org_id
from services.audit import write_audit_log
from services.database import get_pool
from services.ledger import coa as coa_svc
from services.ledger import posting as posting_svc
from services.permissions import get_user_id

router = APIRouter(tags=["ledger"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class AccountCreate(BaseModel):
    code: str
    name: str
    account_type: str
    is_capital_account: bool = False
    tax_character_code: Optional[str] = None
    normal_balance: str  # D or C


class AccountUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    account_type: Optional[str] = None
    is_capital_account: Optional[bool] = None
    tax_character_code: Optional[str] = None
    normal_balance: Optional[str] = None


class EntryCreate(BaseModel):
    vehicle_id: UUID
    transaction_type_code: str
    entry_date: date
    amount: Decimal
    dims: dict = {}
    ledger_basis: str = "GAAP"

    @field_validator("amount")
    @classmethod
    def amount_nonzero(cls, v: Decimal) -> Decimal:
        if v == 0:
            raise ValueError("amount must be non-zero")
        return v


class ReverseRequest(BaseModel):
    reason: str


# ── Module 1 — Chart of Accounts ─────────────────────────────────────────────

@router.get("/ledger/accounts")
async def list_accounts(
    request: Request,
    as_of: Optional[str] = Query(None, description="YYYY-MM-DD for time travel"),
):
    org_id = get_org_id(request)
    pool = await get_pool()
    return await coa_svc.list_accounts(pool, org_id, as_of=as_of)


@router.post("/ledger/accounts", status_code=201)
async def create_account(request: Request, body: AccountCreate):
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            account = await coa_svc.create_account(conn, org_id, body.model_dump(), created_by=user_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    await write_audit_log(
        org_id=org_id, action="CREATE", table_name="chart_of_accounts",
        record_id=account["id"], new=account, actor=user_id,
    )
    return account


@router.patch("/ledger/accounts/{account_id}")
async def update_account(request: Request, account_id: UUID, body: AccountUpdate):
    org_id = get_org_id(request)
    user_id = get_user_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            account = await coa_svc.update_account(
                conn, org_id, str(account_id),
                body.model_dump(exclude_none=True), updated_by=user_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    await write_audit_log(
        org_id=org_id, action="UPDATE", table_name="chart_of_accounts",
        record_id=account["id"], new=account, actor=user_id,
    )
    return account


# ── Module 2 — Posting engine ─────────────────────────────────────────────────

@router.post("/ledger/entries", status_code=201)
async def build_entry(request: Request, body: EntryCreate):
    user_id = get_user_id(request)
    pool = await get_pool()
    try:
        entry = await posting_svc.build_entry(
            pool,
            vehicle_id=str(body.vehicle_id),
            transaction_type_code=body.transaction_type_code,
            entry_date=body.entry_date,
            amount=body.amount,
            dims=body.dims,
            ledger_basis=body.ledger_basis,
            created_by=user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return entry


@router.post("/ledger/entries/{entry_id}/post")
async def post_entry(request: Request, entry_id: UUID):
    user_id = get_user_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT org_id, memo FROM journal_entries WHERE id = $1::uuid",
            str(entry_id),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    org_id = str(row["org_id"])

    try:
        entry = await posting_svc.post(pool, str(entry_id), user_id)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    await write_audit_log(
        org_id=org_id, action="POST_JOURNAL_ENTRY", table_name="journal_entries",
        record_id=str(entry_id),
        new={"memo": row["memo"], "entry_id": str(entry_id)},
        actor=user_id,
    )
    return entry


@router.post("/ledger/entries/{entry_id}/reverse")
async def reverse_entry(request: Request, entry_id: UUID, body: ReverseRequest):
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=400, detail="reason is required")

    user_id = get_user_id(request)
    pool = await get_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT org_id, memo FROM journal_entries WHERE id = $1::uuid",
            str(entry_id),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Entry not found")
    org_id = str(row["org_id"])

    try:
        reversal = await posting_svc.reverse(pool, str(entry_id), body.reason, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    await write_audit_log(
        org_id=org_id, action="REVERSE_JOURNAL_ENTRY", table_name="journal_entries",
        record_id=str(entry_id),
        new={"reason": body.reason, "memo": row["memo"], "entry_id": str(entry_id)},
        actor=user_id,
    )
    return reversal


@router.get("/ledger/entries")
async def list_entries(
    request: Request,
    vehicle_id: UUID = Query(...),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
):
    pool = await get_pool()
    conditions = ["vehicle_id = $1::uuid"]
    params: list = [str(vehicle_id)]

    if from_date:
        params.append(from_date)
        conditions.append(f"entry_date >= ${len(params)}::date")
    if to_date:
        params.append(to_date)
        conditions.append(f"entry_date <= ${len(params)}::date")

    where = " AND ".join(conditions)
    query = (
        f"SELECT * FROM journal_entries WHERE {where} "
        "ORDER BY entry_date DESC, created_at DESC"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


# ── Module 3 — Reporting ──────────────────────────────────────────────────────

@router.get("/ledger/trial-balance")
async def trial_balance(
    request: Request,
    vehicle_id: UUID = Query(...),
    basis: str = Query("GAAP"),
    as_of: Optional[date] = Query(None),
):
    pool = await get_pool()
    conditions = ["vehicle_id = $1::uuid"]
    params: list = [str(vehicle_id)]

    if basis:
        params.append(basis)
        conditions.append(f"ledger_basis = ${len(params)}")
    if as_of:
        params.append(as_of)
        conditions.append(f"entry_date <= ${len(params)}::date")

    where = " AND ".join(conditions)
    query = f"SELECT * FROM v_trial_balance WHERE {where} ORDER BY account_code"
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


@router.get("/ledger/capital-accounts")
async def capital_accounts(
    request: Request,
    vehicle_id: UUID = Query(...),
    basis: str = Query("GAAP"),
    as_of: Optional[date] = Query(None),
):
    pool = await get_pool()
    conditions = ["vehicle_id = $1::uuid"]
    params: list = [str(vehicle_id)]

    if basis:
        params.append(basis)
        conditions.append(f"ledger_basis = ${len(params)}")
    if as_of:
        params.append(as_of)
        conditions.append(f"entry_date <= ${len(params)}::date")

    where = " AND ".join(conditions)
    query = f"SELECT * FROM v_capital_accounts WHERE {where}"
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


# ── Template metadata (UI preview support) ────────────────────────────────────

@router.get("/ledger/templates")
async def list_templates(
    request: Request,
    vehicle_id: UUID = Query(...),
):
    """Return posting templates with their lines for a vehicle's org.

    Used by the frontend to show the journal preview before posting.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        templates = await conn.fetch(
            "SELECT id, name, transaction_type_code, vehicle_type_scope, is_active "
            "FROM posting_templates "
            "WHERE org_id = $1 AND is_active = true "
            "ORDER BY transaction_type_code",
            org_id,
        )
        result = []
        for tmpl in templates:
            lines = await conn.fetch(
                "SELECT ptl.side, ptl.dimension_source, ptl.line_no, "
                "       ptl.account_code, "
                "       coa.name AS account_name, "
                "       coa.tax_character_code, coa.normal_balance "
                "FROM posting_template_lines ptl "
                "JOIN chart_of_accounts coa "
                "     ON coa.org_id = $1 AND coa.code = ptl.account_code "
                "     AND coa.system_to IS NULL AND coa.is_active = true "
                "WHERE ptl.template_id = $2::uuid "
                "ORDER BY ptl.line_no",
                org_id, str(tmpl["id"]),
            )
            d = dict(tmpl)
            d["lines"] = [dict(ln) for ln in lines]
            result.append(d)
    return result
