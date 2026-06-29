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
