"""Tasks assistant actions (Sprint 11)."""
from services.action_registry import AssistantAction, REGISTRY


async def _my_todos(pool, user_id: str, org_id: str, **_):
    """Return open assistant activities and pending notification to-dos."""
    async with pool.acquire() as conn:
        activity_rows = await conn.fetch(
            """
            SELECT id, action_key, label, status, created_at
            FROM assistant_activities
            WHERE user_id = $1 AND org_id = $2
              AND status IN ('awaiting_review', 'in_progress', 'blocked')
            ORDER BY created_at DESC
            LIMIT 20
            """,
            user_id,
            org_id,
        )
        notif_rows = await conn.fetch(
            """
            SELECT n.id, n.title, n.body, n.created_at
            FROM notifications n
            JOIN notification_recipients nr ON nr.notification_id = n.id
            WHERE nr.user_id = $1
              AND n.org_id = $2
              AND nr.status = 'unread'
            ORDER BY n.created_at DESC
            LIMIT 10
            """,
            user_id,
            org_id,
        )

    todos = [
        {
            "id": str(r["id"]),
            "type": "activity",
            "label": r["label"] or r["action_key"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in activity_rows
    ] + [
        {
            "id": str(r["id"]),
            "type": "notification",
            "label": r["title"],
            "status": "unread",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in notif_rows
    ]

    count = len(todos)
    return {
        "data": {"todos": todos},
        "render": {
            "component": "ToDoList",
            "target": "inline",
            "props": {"todos": todos},
        },
        "text": f"You have {count} open item{'s' if count != 1 else ''}.",
    }


def register_actions() -> None:
    REGISTRY.register(
        AssistantAction(
            key="tasks.my_todos",
            module="tasks",
            description=(
                "Show the member's open workflow items — in-progress activities "
                "and unread notifications."
            ),
            access_type="read",
            required_permission=None,
            default_autonomy="auto",
            reversible=False,
            render_target="inline",
            handler=_my_todos,
            params_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )
    )
