"""Notification event bus (Sprint 9).

A single ``publish`` call fans an event out to recipients and records a delivery
attempt per channel. The in-app channel is delivered synchronously; every other
channel is recorded as ``pending`` for an out-of-process worker to pick up and
report back through ``update_delivery_status`` (the two-way status callback).

Schema (deployed): notifications, notification_recipients,
notification_delivery_log, user_notification_preferences. All connections come
from the shared pool (statement_cache_size=0 — PgBouncer safe).
"""

import json
from typing import Any, Iterable

DEFAULT_CHANNELS = ["in_app"]

# Recipient statuses that count as "not yet seen".
UNREAD_STATUSES = ("pending", "delivered")


def _json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


class NotificationBus:
    async def publish(
        self,
        pool,
        org_id,
        event_type: str,
        title: str,
        body: str,
        recipient_user_ids: Iterable,
        *,
        resource_type: str | None = None,
        resource_id=None,
        payload: dict | None = None,
        priority: str = "normal",
        created_by=None,
        channels: list[str] | None = None,
    ) -> str | None:
        """Create a notification, its recipients, and per-channel delivery rows.

        Returns the new notification id, or ``None`` when there are no
        recipients (nothing to publish).
        """
        recipients = [str(u) for u in dict.fromkeys(u for u in recipient_user_ids if u)]
        if not recipients:
            return None
        channels = channels or DEFAULT_CHANNELS

        async with pool.acquire() as conn:
            async with conn.transaction():
                notification_id = await conn.fetchval(
                    """
                    INSERT INTO notifications
                        (org_id, event_type, title, body, payload,
                         resource_type, resource_id, priority, created_by)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    RETURNING id
                    """,
                    org_id, event_type, title, body, _json(payload),
                    resource_type, resource_id, priority, created_by,
                )

                for user_id in recipients:
                    recipient_id = await conn.fetchval(
                        """
                        INSERT INTO notification_recipients
                            (org_id, notification_id, user_id, status)
                        VALUES ($1, $2, $3, 'pending')
                        RETURNING id
                        """,
                        org_id, notification_id, user_id,
                    )
                    for channel in channels:
                        delivered = channel == "in_app"
                        await conn.execute(
                            """
                            INSERT INTO notification_delivery_log
                                (org_id, notification_id, recipient_id, channel,
                                 status, attempted_at, delivered_at)
                            VALUES ($1, $2, $3, $4, $5,
                                    now(), CASE WHEN $6 THEN now() ELSE NULL END)
                            """,
                            org_id, notification_id, recipient_id, channel,
                            "delivered" if delivered else "pending", delivered,
                        )
                    # Mark the in-app recipient row delivered immediately so the
                    # bell reflects it without waiting on a channel worker.
                    if "in_app" in channels:
                        await conn.execute(
                            """
                            UPDATE notification_recipients
                            SET status = 'delivered', updated_at = now()
                            WHERE id = $1 AND status = 'pending'
                            """,
                            recipient_id,
                        )

        return str(notification_id)

    async def mark_read(self, pool, notification_id, user_id) -> bool:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE notification_recipients
                SET status = 'read', read_at = now(), updated_at = now()
                WHERE notification_id = $1 AND user_id = $2
                  AND status IN ('pending', 'delivered')
                """,
                notification_id, user_id,
            )
        return result != "UPDATE 0"

    async def mark_acted(self, pool, notification_id, user_id, action_taken: str) -> bool:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE notification_recipients
                SET status = 'acted', acted_at = now(),
                    action_taken = $3, updated_at = now()
                WHERE notification_id = $1 AND user_id = $2
                """,
                notification_id, user_id, action_taken,
            )
        return result != "UPDATE 0"

    async def dismiss(self, pool, notification_id, user_id) -> bool:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE notification_recipients
                SET status = 'dismissed', dismissed_at = now(), updated_at = now()
                WHERE notification_id = $1 AND user_id = $2
                """,
                notification_id, user_id,
            )
        return result != "UPDATE 0"

    async def mark_all_read(self, pool, user_id, org_id) -> int:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE notification_recipients
                SET status = 'read', read_at = now(), updated_at = now()
                WHERE user_id = $1 AND org_id = $2
                  AND status IN ('pending', 'delivered')
                """,
                user_id, org_id,
            )
        # result like "UPDATE 5"
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def get_for_user(
        self, pool, user_id, org_id,
        status: str | None = None, limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        conditions = ["r.user_id = $1", "r.org_id = $2"]
        params: list = [user_id, org_id]
        if status:
            params.append(status)
            conditions.append(f"r.status = ${len(params)}")
        params.append(limit)
        limit_pos = len(params)
        params.append(offset)
        offset_pos = len(params)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT n.id, n.event_type, n.title, n.body, n.payload,
                       n.resource_type, n.resource_id, n.priority, n.created_at,
                       r.status, r.read_at, r.acted_at, r.dismissed_at,
                       r.action_taken
                FROM notification_recipients r
                JOIN notifications n ON n.id = r.notification_id
                WHERE {' AND '.join(conditions)}
                ORDER BY n.created_at DESC
                LIMIT ${limit_pos} OFFSET ${offset_pos}
                """,
                *params,
            )
        return [dict(r) for r in rows]

    async def get_unread_count(self, pool, user_id, org_id) -> int:
        async with pool.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM notification_recipients
                WHERE user_id = $1 AND org_id = $2
                  AND status IN ('pending', 'delivered')
                """,
                user_id, org_id,
            )
        return int(count or 0)

    async def update_delivery_status(
        self, pool, delivery_log_id,
        status: str,
        external_id: str | None = None,
        failure_reason: str | None = None,
        metadata: dict | None = None,
    ) -> bool:
        """Two-way status callback for delivery channels.

        Records the channel result and, if every delivery attempt for the
        recipient has now failed, marks the recipient row ``failed``.
        """
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE notification_delivery_log
                    SET status = $2,
                        external_id = COALESCE($3, external_id),
                        failure_reason = COALESCE($4, failure_reason),
                        metadata = COALESCE($5, metadata),
                        delivered_at = CASE WHEN $2 = 'delivered'
                                            THEN now() ELSE delivered_at END,
                        failed_at = CASE WHEN $2 = 'failed'
                                         THEN now() ELSE failed_at END,
                        updated_at = now()
                    WHERE id = $1
                    RETURNING recipient_id
                    """,
                    delivery_log_id, status, external_id, failure_reason,
                    _json(metadata),
                )
                if row is None:
                    return False

                recipient_id = row["recipient_id"]
                remaining = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM notification_delivery_log
                    WHERE recipient_id = $1 AND status <> 'failed'
                    """,
                    recipient_id,
                )
                if int(remaining or 0) == 0:
                    await conn.execute(
                        """
                        UPDATE notification_recipients
                        SET status = 'failed', updated_at = now()
                        WHERE id = $1 AND status IN ('pending', 'delivered')
                        """,
                        recipient_id,
                    )
        return True


notification_bus = NotificationBus()
