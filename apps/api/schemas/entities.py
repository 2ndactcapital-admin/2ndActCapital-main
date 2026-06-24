"""Pydantic request/response models for the Entity/CRM core."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EntityType(str, Enum):
    individual = "individual"
    trust = "trust"
    llc = "llc"
    lp = "lp"
    gp = "gp"
    s_corp = "s_corp"
    c_corp = "c_corp"
    corporation = "corporation"
    foundation = "foundation"
    family_office = "family_office"
    corp_uk = "corp_uk"
    corp_eu = "corp_eu"
    corp_cayman = "corp_cayman"
    corp_luxembourg = "corp_luxembourg"
    corp_other_intl = "corp_other_intl"
    other = "other"


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------
class EntityCreate(BaseModel):
    entity_type: EntityType
    display_name: str
    legal_name: str | None = None
    tax_id: str | None = None
    date_of_birth: date | None = None
    country_of_formation: str | None = None
    notes: str | None = None


class EntityUpdate(BaseModel):
    """All fields optional — only provided fields are changed."""

    entity_type: EntityType | None = None
    display_name: str | None = None
    legal_name: str | None = None
    tax_id: str | None = None
    date_of_birth: date | None = None
    country_of_formation: str | None = None
    notes: str | None = None


class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    entity_type: EntityType
    display_name: str
    legal_name: str | None = None
    tax_id: str | None = None
    date_of_birth: date | None = None
    country_of_formation: str | None = None
    notes: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    system_from: datetime | None = None
    system_to: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------
class AttributeCreate(BaseModel):
    attribute_key: str
    attribute_value: str | None = None
    value_type: str = "string"


class AttributeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_id: UUID
    attribute_key: str
    attribute_value: str | None = None
    value_type: str
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------
class OwnershipCreate(BaseModel):
    parent_id: UUID
    child_id: UUID
    ownership_pct: float = Field(gt=0, le=100)
    ownership_type: str = "equity"


class OwnershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    parent_id: UUID
    child_id: UUID
    ownership_pct: float
    ownership_type: str
    # Convenience labels populated where available.
    parent_name: str | None = None
    child_name: str | None = None


# ---------------------------------------------------------------------------
# Detail + graph
# ---------------------------------------------------------------------------
class EntityDetail(BaseModel):
    entity: EntityOut
    attributes: list[AttributeOut] = []
    owners: list[OwnershipOut] = []  # rows where this entity is the child
    holdings: list[OwnershipOut] = []  # rows where this entity is the parent


class GraphNode(BaseModel):
    id: UUID
    display_name: str
    entity_type: EntityType
    depth: int


class GraphEdge(BaseModel):
    parent_id: UUID
    child_id: UUID
    ownership_pct: float
    ownership_type: str


class OwnershipGraph(BaseModel):
    root_id: UUID
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
