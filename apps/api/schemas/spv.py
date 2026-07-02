"""Pydantic schemas for the SPV Manager (Sprint 12)."""
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class SPVCreate(BaseModel):
    name: str
    deal_id: UUID  # NOT NULL in DB
    target_raise: Optional[float] = None
    minimum_raise: Optional[float] = None
    hard_cap: Optional[float] = None
    min_commitment: Optional[float] = None
    carry_pct: Optional[float] = None
    mgmt_fee_pct: Optional[float] = None
    close_date: Optional[date] = None


class SPVUpdate(BaseModel):
    name: Optional[str] = None
    deal_id: Optional[UUID] = None
    target_raise: Optional[float] = None
    minimum_raise: Optional[float] = None
    hard_cap: Optional[float] = None
    min_commitment: Optional[float] = None
    carry_pct: Optional[float] = None
    mgmt_fee_pct: Optional[float] = None
    close_date: Optional[date] = None


class SPVStatusUpdate(BaseModel):
    status: str
    note: Optional[str] = None


class SPVFormEntityUpdate(BaseModel):
    entity_id: UUID


class SPVResponse(BaseModel):
    id: UUID
    org_id: UUID
    deal_id: Optional[UUID]
    name: str
    status: str
    target_raise: Optional[float]
    minimum_raise: Optional[float]
    hard_cap: Optional[float]
    min_commitment: Optional[float]
    carry_pct: Optional[float]
    mgmt_fee_pct: Optional[float]
    vehicle_entity_id: Optional[UUID]
    close_date: Optional[date]
    created_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime


class SubscriptionCreate(BaseModel):
    entity_id: UUID
    commitment_amount: float
    note: Optional[str] = None


class SubscriptionAmend(BaseModel):
    commitment_amount: float
    note: Optional[str] = None


class SubscriptionResponse(BaseModel):
    id: UUID
    org_id: UUID
    spv_id: UUID
    entity_id: UUID
    commitment_amount: float
    funded_amount: Optional[float]
    status: str
    ownership_pct: Optional[float]
    signed_at: Optional[datetime]
    valid_from: datetime
    valid_to: Optional[datetime]
    created_by: Optional[UUID]
    created_at: datetime


class CapTableEntry(BaseModel):
    subscription_id: UUID
    entity_id: UUID
    entity_name: str
    commitment_amount: float
    funded_amount: Optional[float]
    ownership_pct: Optional[float]
    status: str
    signed_at: Optional[datetime]


class CapTableResponse(BaseModel):
    spv_id: UUID
    spv_name: str
    total_committed: float
    total_funded: float
    target_raise: Optional[float]
    subscriptions: list[CapTableEntry]


class SPVDocumentResponse(BaseModel):
    id: UUID
    org_id: UUID
    spv_id: UUID
    # DB columns: title, storage_key, doc_type — aliased to friendly names via DOC_SELECT
    file_name: str
    r2_key: Optional[str]
    document_type: str
    status: str
    uploaded_by: Optional[UUID]
    created_at: datetime


class StatusHistoryEntry(BaseModel):
    id: UUID
    from_status: Optional[str]
    to_status: str
    note: Optional[str]
    changed_by: Optional[UUID]
    created_at: datetime


# ---------------------------------------------------------------------------
# Sprint 14 — Transaction Ledger schemas
# ---------------------------------------------------------------------------

class TransactionCreate(BaseModel):
    # Either txn_type (legacy) OR transaction_type_id must be provided.
    txn_type: Optional[str] = None
    transaction_type_id: Optional[UUID] = None
    txn_date: date
    amount: float
    description: Optional[str] = None
    reference: Optional[str] = None
    allocation_basis: str = "committed"   # 'ownership_pct', 'committed', 'funded'
    currency_code: str = "USD"
    amount_basis: str = "currency"        # 'currency', 'units', 'percent'


class TransactionUpdate(BaseModel):
    txn_type: Optional[str] = None
    txn_date: Optional[date] = None
    amount: Optional[float] = None
    description: Optional[str] = None
    reference: Optional[str] = None
    allocation_basis: Optional[str] = None
    currency_code: Optional[str] = None
    amount_basis: Optional[str] = None


class TransactionResponse(BaseModel):
    id: UUID
    org_id: UUID
    spv_id: UUID
    txn_type: str
    txn_date: date
    amount: float
    description: Optional[str]
    reference: Optional[str]
    allocation_basis: str
    status: str
    allocated_at: Optional[datetime]
    posted_at: Optional[datetime]
    transaction_type_id: Optional[UUID] = None
    currency_code: str = "USD"
    amount_basis: str = "currency"
    created_by: Optional[UUID]
    created_at: datetime
    updated_at: datetime


class TransactionTypeResponse(BaseModel):
    id: UUID
    code: str
    label: str
    category: str
    direction: str
    # affects_* are INTEGER in the DB (-1 = decreases, 0 = no effect, +1 = increases)
    affects_paid_in: int
    affects_unfunded: int
    affects_nav: int
    is_recallable: bool
    performance_impact: Optional[str]
    applies_to_security_types: list[str]
    amount_basis: str
    display_order: Optional[int]
    notes: Optional[str]
    created_at: datetime


class AllocationRow(BaseModel):
    id: UUID
    org_id: UUID
    transaction_id: UUID
    subscription_id: UUID
    allocated_amount: float
    ownership_pct: float
    status: str
    created_at: datetime


class LedgerSummary(BaseModel):
    total_called: float
    total_distributed: float
    total_fees: float
    total_recallable: float = 0.0
    net: float


class LedgerResponse(BaseModel):
    spv_id: UUID
    spv_name: str
    summary: LedgerSummary
    transactions: list[TransactionResponse]
