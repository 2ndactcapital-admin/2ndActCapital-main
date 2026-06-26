"""Pydantic models for the Investment Profile module."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    question_key: str
    question_text: str
    question_type: str
    options: dict | None = None
    category: str
    is_required: bool
    display_order: int


class AnswerIn(BaseModel):
    question_id: UUID
    answer_value: str | None = None
    answer_json: Any | None = None


class AnswerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_id: UUID
    question_id: UUID
    answer_value: str | None = None
    answer_json: Any | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Joined question details (present on the answers list endpoint).
    question_key: str | None = None
    question_text: str | None = None
    question_type: str | None = None
    category: str | None = None
    is_required: bool | None = None
    display_order: int | None = None
    options: dict | None = None


# ---------------------------------------------------------------------------
# Foundation conversation (Sprint 10)
# ---------------------------------------------------------------------------
class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_id: UUID
    status: str
    current_question_index: int
    messages: list[dict] = []
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_questions: int = 0


class ConversationMessageIn(BaseModel):
    message: str


class ConversationMessageOut(BaseModel):
    message: str
    question_index: int
    total_questions: int
    is_complete: bool
    progress_pct: float


# ---------------------------------------------------------------------------
# Extractions (Sprint 10)
# ---------------------------------------------------------------------------
class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_id: UUID
    question_id: UUID
    answer_id: UUID
    extracted_fields: Any | None = None
    confidence: float | None = None
    advisor_reviewed: bool = False
    advisor_accepted: bool | None = None
    created_at: datetime | None = None
    # Joined context.
    question_text: str | None = None
    answer_text: str | None = None


class ExtractionReviewIn(BaseModel):
    accepted: bool
    edits: dict | None = None


# ---------------------------------------------------------------------------
# Client brief (Sprint 10)
# ---------------------------------------------------------------------------
class BriefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_id: UUID
    brief_text: str
    key_themes: list[str] | None = None
    risk_profile: str | None = None
    decision_style: str | None = None
    is_current: bool = True
    generated_at: datetime | None = None
    model_used: str | None = None
