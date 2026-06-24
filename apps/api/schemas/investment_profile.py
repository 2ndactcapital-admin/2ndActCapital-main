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
