"""Pydantic models for the notification bus (Sprint 9)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NotificationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: str
    title: str
    body: str
    payload: dict | list | None = None
    resource_type: str | None = None
    resource_id: UUID | None = None
    priority: str = "normal"
    status: str = "pending"
    action_taken: str | None = None
    read_at: datetime | None = None
    acted_at: datetime | None = None
    dismissed_at: datetime | None = None
    created_at: datetime | None = None


class NotificationListResponse(BaseModel):
    notifications: list[NotificationItem] = []
    unread_count: int = 0


class UnreadCountResponse(BaseModel):
    unread_count: int = 0


class MarkActedRequest(BaseModel):
    action_taken: str


class NotificationPreference(BaseModel):
    event_type: str
    channel: str
    is_enabled: bool = True


class NotificationPreferenceResponse(NotificationPreference):
    model_config = ConfigDict(from_attributes=True)
