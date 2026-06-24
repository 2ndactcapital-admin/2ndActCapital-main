"""Pydantic request/response models for the Marketplace module (Sprint 5).

Configurable values (scoring dimensions, deal types, asset classes) are NOT
modelled as enums here — they live in the ``config`` table and are served via
the /config endpoint. Only structural fields are typed.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Deals
# ---------------------------------------------------------------------------
class DealBase(BaseModel):
    name: str
    description: str | None = None
    deal_status: str | None = None
    asset_super_class: str | None = None
    asset_class: str | None = None
    asset_sub_category: str | None = None
    sponsor_entity_id: UUID | None = None
    sponsor_name_override: str | None = None
    target_raise: float | None = None
    minimum_investment: float | None = None
    expected_return_pct: float | None = None
    term_months: int | None = None
    deal_date: date | None = None
    close_date: date | None = None
    location: str | None = None
    highlights: list[str] = []
    tags: list[str] = []
    is_featured: bool = False


class DealCreate(DealBase):
    """All fields optional except name."""

    name: str
    highlights: list[str] | None = None
    tags: list[str] | None = None
    is_featured: bool | None = None


class DealUpdate(BaseModel):
    """All fields optional — only provided fields are changed."""

    name: str | None = None
    description: str | None = None
    deal_status: str | None = None
    asset_super_class: str | None = None
    asset_class: str | None = None
    asset_sub_category: str | None = None
    sponsor_entity_id: UUID | None = None
    sponsor_name_override: str | None = None
    target_raise: float | None = None
    minimum_investment: float | None = None
    expected_return_pct: float | None = None
    term_months: int | None = None
    deal_date: date | None = None
    close_date: date | None = None
    location: str | None = None
    highlights: list[str] | None = None
    tags: list[str] | None = None
    is_featured: bool | None = None


class DealResponse(DealBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    slug: str | None = None
    deal_status: str | None = None
    submitted_by: UUID | None = None
    published_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Aggregates / per-user context
    composite_score: float | None = None
    vote_count: int = 0
    upvotes: int = 0
    downvotes: int = 0
    user_vote: int | None = None
    has_indicated_interest: bool = False
    document_count: int = 0

    # Resolved taxonomy display labels (keys stored in the DB; labels resolved
    # at read time so renames in config propagate automatically).
    asset_super_class_label: str | None = None
    asset_class_label: str | None = None
    asset_sub_category_label: str | None = None


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------
class DealScoreCreate(BaseModel):
    dimension: str
    score: float = Field(ge=0, le=100)
    weight: float
    notes: str | None = None
    scored_by_ai: bool = False
    ai_model: str | None = None
    ai_confidence: float | None = None


class DealScoreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deal_id: UUID
    dimension: str
    score: float
    weight: float
    notes: str | None = None
    scored_by: UUID | None = None
    scored_by_ai: bool = False
    ai_model: str | None = None
    ai_confidence: float | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
class DealDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deal_id: UUID
    file_name: str
    file_type: str | None = None
    file_size_bytes: int | None = None
    document_type: str | None = None
    processing_status: str = "pending"
    extracted_data: dict | list | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Votes / interest
# ---------------------------------------------------------------------------
class VoteRequest(BaseModel):
    vote: int = Field(description="1 (up) or -1 (down)")


class InterestRequest(BaseModel):
    entity_id: UUID | None = None
    amount_interest: float | None = None
    notes: str | None = None


class InterestOverrideRequest(BaseModel):
    user_id: UUID | None = None
    entity_id: UUID | None = None
    notes: str | None = None


class InterestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deal_id: UUID
    entity_id: UUID | None = None
    user_id: UUID | None = None
    amount_interest: float | None = None
    notes: str | None = None
    compliance_override: bool = False
    created_at: datetime | None = None


class InterestUserResponse(BaseModel):
    """A member who indicated interest (with display names, staff-only view)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deal_id: UUID
    entity_id: UUID | None = None
    entity_name: str | None = None
    user_id: UUID | None = None
    amount_interest: float | None = None
    notes: str | None = None
    compliance_override: bool = False
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Deal detail envelope
# ---------------------------------------------------------------------------
class DealDetail(BaseModel):
    deal: DealResponse
    scores: list[DealScoreResponse] = []
    documents: list[DealDocumentResponse] = []
    # People metadata (names, not IDs) — populated for the detail view.
    created_by_name: str | None = None
    submitted_by_name: str | None = None
    sponsor_name: str | None = None
    interest_count: int = 0


# ---------------------------------------------------------------------------
# Status transition
# ---------------------------------------------------------------------------
class StatusUpdate(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
class ConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    config_key: str
    config_value: str | dict | list | int | float | bool | None = None
    value_type: str | None = None
    category: str | None = None
    display_order: int | None = None
