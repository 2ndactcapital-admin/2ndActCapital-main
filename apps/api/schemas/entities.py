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
    household = "household"
    corp_uk = "corp_uk"
    corp_eu = "corp_eu"
    corp_cayman = "corp_cayman"
    corp_luxembourg = "corp_luxembourg"
    corp_other_intl = "corp_other_intl"
    other = "other"


class TaxIdType(str, Enum):
    ssn = "ssn"
    ein = "ein"
    itin = "itin"
    utr = "utr"
    vat = "vat"
    trn = "trn"
    nino = "nino"
    tin_other = "tin_other"


class AddressType(str, Enum):
    primary_residence = "primary_residence"
    mailing = "mailing"
    business = "business"
    registered = "registered"


class SocialPlatform(str, Enum):
    linkedin = "linkedin"
    twitter = "twitter"
    facebook = "facebook"
    instagram = "instagram"
    angellist = "angellist"
    crunchbase = "crunchbase"
    other = "other"


class KycStatus(str, Enum):
    not_started = "not_started"
    in_progress = "in_progress"
    approved = "approved"
    flagged = "flagged"
    expired = "expired"


class OfacStatus(str, Enum):
    not_screened = "not_screened"
    passed = "passed"
    false_positive = "false_positive"
    review_required = "review_required"


class AmlRiskRating(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class AccreditationStatus(str, Enum):
    not_verified = "not_verified"
    self_certified = "self_certified"
    third_party_verified = "third_party_verified"
    expired = "expired"


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
    # Sprint 2b additions
    sub_type: str | None = None
    status: str = "prospect"
    lead_source: str | None = None
    relationship_manager_id: UUID | None = None
    tags: list[str] = []
    linkedin_url: str | None = None
    primary_email: str | None = None
    primary_phone: str | None = None


class EntityUpdate(BaseModel):
    """All fields optional — only provided fields are changed."""

    entity_type: EntityType | None = None
    display_name: str | None = None
    legal_name: str | None = None
    tax_id: str | None = None
    date_of_birth: date | None = None
    country_of_formation: str | None = None
    notes: str | None = None
    sub_type: str | None = None
    status: str | None = None
    lead_source: str | None = None
    relationship_manager_id: UUID | None = None
    tags: list[str] | None = None
    linkedin_url: str | None = None
    primary_email: str | None = None
    primary_phone: str | None = None


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
    sub_type: str | None = None
    status: str | None = None
    lead_source: str | None = None
    relationship_manager_id: UUID | None = None
    tags: list[str] = []
    linkedin_url: str | None = None
    primary_email: str | None = None
    primary_phone: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    system_from: datetime | None = None
    system_to: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# Spec alias.
EntityResponse = EntityOut


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
    parent_name: str | None = None
    child_name: str | None = None


# ---------------------------------------------------------------------------
# Tax IDs
# ---------------------------------------------------------------------------
class TaxIdCreate(BaseModel):
    tax_id_type: TaxIdType = TaxIdType.ssn
    tax_id_country: str = "US"
    value: str  # full value, write-only — never persisted or returned in clear
    is_primary: bool = True


class TaxIdResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_id: UUID
    tax_id_type: TaxIdType
    tax_id_country: str
    tax_id_last4: str
    is_primary: bool
    masked: str | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
class AddressCreate(BaseModel):
    address_type: AddressType = AddressType.primary_residence
    street1: str
    street2: str | None = None
    city: str
    state: str | None = None
    postal_code: str | None = None
    country: str = "US"
    is_primary: bool = False
    is_verified: bool = False


class AddressUpdate(BaseModel):
    address_type: AddressType | None = None
    street1: str | None = None
    street2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    is_primary: bool | None = None
    is_verified: bool | None = None


class AddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_id: UUID
    address_type: AddressType
    street1: str
    street2: str | None = None
    city: str
    state: str | None = None
    postal_code: str | None = None
    country: str
    is_verified: bool
    is_primary: bool
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Employment
# ---------------------------------------------------------------------------
class EmploymentCreate(BaseModel):
    employer_id: UUID
    title: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    notes: str | None = None


class EmploymentUpdate(BaseModel):
    title: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool | None = None
    notes: str | None = None


class EmploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    employee_id: UUID
    employer_id: UUID
    employer_name: str | None = None
    title: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool
    notes: str | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Social profiles
# ---------------------------------------------------------------------------
class SocialProfileCreate(BaseModel):
    platform: SocialPlatform
    url: str
    is_primary: bool = False


class SocialProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_id: UUID
    platform: SocialPlatform
    url: str
    is_primary: bool
    linkedin_import_stub: bool
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------
class ComplianceRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_id: UUID
    kyc_status: KycStatus
    kyc_verified_date: date | None = None
    ofac_screen_status: OfacStatus
    ofac_screen_date: datetime | None = None
    aml_risk_rating: AmlRiskRating
    accreditation_status: AccreditationStatus
    accreditation_basis: str | None = None
    accreditation_verified_date: date | None = None
    next_reverification_due: date | None = None
    pep_status: bool
    pep_details: str | None = None
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ComplianceRecordUpdate(BaseModel):
    kyc_status: KycStatus | None = None
    ofac_screen_status: OfacStatus | None = None
    aml_risk_rating: AmlRiskRating | None = None
    accreditation_status: AccreditationStatus | None = None
    accreditation_basis: str | None = None
    pep_status: bool | None = None
    pep_details: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Detail + graph
# ---------------------------------------------------------------------------
class EntityDetail(BaseModel):
    entity: EntityOut
    attributes: list[AttributeOut] = []
    owners: list[OwnershipOut] = []  # rows where this entity is the child
    holdings: list[OwnershipOut] = []  # rows where this entity is the parent


class EntityFull(BaseModel):
    entity: EntityOut
    attributes: list[AttributeOut] = []
    owners: list[OwnershipOut] = []
    holdings: list[OwnershipOut] = []
    tax_ids: list[TaxIdResponse] = []
    addresses: list[AddressResponse] = []
    employment: list[EmploymentResponse] = []
    social_profiles: list[SocialProfileResponse] = []
    compliance_record: ComplianceRecordResponse | None = None


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
