"""Pydantic request/response models for the Portfolio module (Sprint 8)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TargetAllocationItem(BaseModel):
    taxonomy_key: str
    taxonomy_level: str | None = None
    target_pct: float
    notes: str | None = None


class TargetAllocationWrite(BaseModel):
    items: list[TargetAllocationItem]


class TargetAllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_id: UUID
    taxonomy_key: str
    target_pct: float
    effective_date: date
    end_date: date | None = None
    notes: str | None = None
    created_at: datetime | None = None
    taxonomy_label: str | None = None
    taxonomy_level: str | None = None
    inherited: bool = False
    inherited_from_entity_id: UUID | None = None
    inherited_from_entity_name: str | None = None


class AllocationBreakdownItem(BaseModel):
    taxonomy_key: str
    taxonomy_label: str | None = None
    taxonomy_level: str | None = None
    total_invested: float = 0.0
    deal_count: int = 0
    actual_pct: float = 0.0
    target_pct: float | None = None
    gap_pct: float | None = None


class TaxonomyPlacementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    deal_id: UUID
    asset_super_class: str | None = None
    asset_class: str | None = None
    asset_sub_category: str | None = None
    asset_super_class_label: str | None = None
    asset_class_label: str | None = None
    asset_sub_category_label: str | None = None
