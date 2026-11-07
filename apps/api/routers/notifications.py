"""Notification API endpoints (Sprint 9).

All routes are scoped to the authenticated caller. The bell icon polls
``GET /notifications/count``; the panel and full page use ``GET /notifications``.
"""

from uuid import UUID

from fastapi import APIRouter, Query, Request

from routers.entities import get_org_id
from schemas.notifications import (
    MarkActedRequest,
    NotificationListResponse,
    NotificationPreference,
    NotificationPreferenceResponse,
    UnreadCountResponse,
)
from services.database import get_pool
from services.family_visibility import notification_visible, scope_for_request
from services.notifications import notification_bus
from services.permissions import get_user_id
from services.users import ensure_user

router = APIRouter(tags=["notifications"])


async def _resolve_user(request: Request) -> tuple[str, str]:
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        user_id = await ensure_user(conn, request)
    return user_id, org_id


@router.get("/notifications", response_model=NotificationListResponse)
async def list_notifications(
    request: Request,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user_id, org_id = await _resolve_user(request)
    pool = await get_pool()
    items = await notification_bus.get_for_user(
        pool, user_id, org_id, status=status, limit=limit, offset=offset
    )
    unread = await notification_bus.get_unread_count(pool, user_id, org_id)
    staff = await scope_for_request(request, pool)      # VISIBILITY1 — a family's notification only if visible
    if staff is not None:
        items = [n for n in items if notification_visible(n, staff)]
        unread = await _visible_unread(pool, user_id, org_id, staff)
    return NotificationListResponse(notifications=items, unread_count=unread)


async def _visible_unread(pool, user_id, org_id, staff) -> int:
    """VISIBILITY1 — the unread count over the notifications this restricted caller may see (two reads)."""
    pending = await notification_bus.get_for_user(pool, user_id, org_id, status="pending", limit=1000, offset=0)
    delivered = await notification_bus.get_for_user(pool, user_id, org_id, status="delivered", limit=1000,
                                                    offset=0)
    return sum(1 for n in pending + delivered if notification_visible(n, staff))


@router.get("/notifications/count", response_model=UnreadCountResponse)
async def notifications_count(request: Request):
    user_id, org_id = await _resolve_user(request)
    pool = await get_pool()
    count = await notification_bus.get_unread_count(pool, user_id, org_id)
    staff = await scope_for_request(request, pool)      # VISIBILITY1
    if staff is not None:
        count = await _visible_unread(pool, user_id, org_id, staff)
    return UnreadCountResponse(unread_count=count)


@router.put("/notifications/read-all")
async def mark_all_read(request: Request):
    user_id, org_id = await _resolve_user(request)
    pool = await get_pool()
    updated = await notification_bus.mark_all_read(pool, user_id, org_id)
    return {"updated": updated}


@router.put("/notifications/{notification_id}/read")
async def mark_read(request: Request, notification_id: UUID):
    user_id, _ = await _resolve_user(request)
    pool = await get_pool()
    ok = await notification_bus.mark_read(pool, notification_id, user_id)
    return {"ok": ok}


@router.put("/notifications/{notification_id}/acted")
async def mark_acted(request: Request, notification_id: UUID, body: MarkActedRequest):
    user_id, _ = await _resolve_user(request)
    pool = await get_pool()
    ok = await notification_bus.mark_acted(
        pool, notification_id, user_id, body.action_taken
    )
    return {"ok": ok}


@router.put("/notifications/{notification_id}/dismiss")
async def dismiss(request: Request, notification_id: UUID):
    user_id, _ = await _resolve_user(request)
    pool = await get_pool()
    ok = await notification_bus.dismiss(pool, notification_id, user_id)
    return {"ok": ok}


@router.get(
    "/notifications/preferences",
    response_model=list[NotificationPreferenceResponse],
)
async def get_preferences(request: Request):
    user_id, org_id = await _resolve_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event_type, channel, is_enabled
            FROM user_notification_preferences
            WHERE user_id = $1 AND org_id = $2
            ORDER BY event_type, channel
            """,
            user_id, org_id,
        )
    return [NotificationPreferenceResponse(**dict(r)) for r in rows]


@router.put(
    "/notifications/preferences",
    response_model=list[NotificationPreferenceResponse],
)
async def set_preferences(request: Request, body: list[NotificationPreference]):
    user_id, org_id = await _resolve_user(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for pref in body:
                await conn.execute(
                    """
                    INSERT INTO user_notification_preferences
                        (org_id, user_id, event_type, channel, is_enabled)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (user_id, event_type, channel)
                    DO UPDATE SET is_enabled = EXCLUDED.is_enabled,
                                  updated_at = now()
                    """,
                    org_id, user_id, pref.event_type, pref.channel, pref.is_enabled,
                )
        rows = await conn.fetch(
            """
            SELECT event_type, channel, is_enabled
            FROM user_notification_preferences
            WHERE user_id = $1 AND org_id = $2
            ORDER BY event_type, channel
            """,
            user_id, org_id,
        )
    return [NotificationPreferenceResponse(**dict(r)) for r in rows]
