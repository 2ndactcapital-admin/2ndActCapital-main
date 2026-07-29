"""member_todos integration for the Workflow Manager (Phase 4).

The task/alert surface for the Workflow Manager REUSES the existing
``member_todos`` infrastructure (the AI-dashboard todo/alert surface) rather
than a new notification system:

  * an active/pending **User Task** becomes an ``open`` todo for every user
    holding that step's assigned role profile, and is marked ``done`` when the
    task is completed;
  * a run that transitions to **held** (a failure) becomes an alert todo for
    the run's starter AND every Org Admin of the org.

Discovery notes that shape this module (verified live this phase):
  * ``member_todos`` has NO unique constraint beyond its ``id`` PK, so
    ``ON CONFLICT DO NOTHING`` can only ever fire on the generated id and can
    NOT dedupe. Idempotency here is therefore an explicit
    SELECT-then-INSERT/UPDATE keyed on
    ``(user_id, org_id, source, related_type, related_id)``.
  * Status vocabulary in actual use across the app is ``open`` / ``done`` /
    ``dismissed`` (see routers/dashboard.py); ``list_todos`` only surfaces
    ``status='open'``. We use those same values.
  * The role -> user link is ``users.profile_id`` (a SOC ``profiles.id``);
    Org Admins are ``users.role = 'org_admin'``.

Every function takes an open ``conn`` so the engine can enlist these writes in
its own transaction / connection.
"""
from __future__ import annotations

# Stable ``source`` markers so a run/step's todos can be found and updated
# idempotently (there is no natural unique key on member_todos to rely on).
TODO_SOURCE_USER_TASK = "workflow_user_task"
TODO_SOURCE_RUN_HELD = "workflow_run_held"

TODO_CATEGORY = "workflow"
_RUN_CONSOLE_PATH = "/admin/workflows/runs"


async def _upsert_todo(
    conn,
    *,
    org_id,
    user_id,
    source: str,
    related_type: str,
    related_id,
    title: str,
    detail: str,
    priority: int,
    action_key: str | None = None,
):
    """Insert a todo, or refresh (and re-open) the existing one for this
    (user, org, source, related_type, related_id). Returns the todo id."""
    existing = await conn.fetchrow(
        """
        SELECT id FROM member_todos
        WHERE user_id = $1 AND org_id = $2 AND source = $3
          AND related_type = $4 AND related_id = $5
        LIMIT 1
        """,
        user_id, org_id, source, related_type, related_id,
    )
    if existing is not None:
        await conn.execute(
            """
            UPDATE member_todos
            SET title = $2, detail = $3, priority = $4, action_key = $5,
                status = 'open', updated_at = now()
            WHERE id = $1
            """,
            existing["id"], title, detail, priority, action_key,
        )
        return existing["id"]
    return await conn.fetchval(
        """
        INSERT INTO member_todos
            (org_id, user_id, kind, category, source,
             related_type, related_id, title, detail, action_key,
             priority, status)
        VALUES ($1, $2, 'actual', $3, $4, $5, $6, $7, $8, $9, $10, 'open')
        RETURNING id
        """,
        org_id, user_id, TODO_CATEGORY, source, related_type, related_id,
        title, detail, action_key, priority,
    )


async def sync_user_task_todos(
    conn,
    *,
    org_id,
    run_step_id,
    step_key: str,
    display_name: str | None,
    assigned_role_profile_id,
) -> list:
    """Create/refresh an ``open`` todo for every user holding the User Task's
    assigned role profile in this org. Idempotent per (user, step)."""
    if assigned_role_profile_id is None:
        return []
    users = await conn.fetch(
        "SELECT id FROM users WHERE org_id = $1 AND profile_id = $2",
        org_id, assigned_role_profile_id,
    )
    label = display_name or step_key
    ids = []
    for u in users:
        ids.append(
            await _upsert_todo(
                conn,
                org_id=org_id,
                user_id=u["id"],
                source=TODO_SOURCE_USER_TASK,
                related_type="workflow_run_step",
                related_id=run_step_id,
                title=f"Action needed: {label}",
                detail="A workflow task is waiting for your review.",
                priority=15,
                action_key=_RUN_CONSOLE_PATH,
            )
        )
    return ids


async def complete_user_task_todos(conn, *, run_step_id) -> None:
    """Mark the User Task's todo(s) ``done`` when the task is completed."""
    await conn.execute(
        """
        UPDATE member_todos
        SET status = 'done', updated_at = now()
        WHERE source = $1 AND related_type = 'workflow_run_step'
          AND related_id = $2 AND status = 'open'
        """,
        TODO_SOURCE_USER_TASK, run_step_id,
    )


async def create_held_run_alerts(
    conn, *, org_id, run_id, started_by, error_detail: str | None
) -> list:
    """Alert the run's starter AND every Org Admin of the org that a run held.

    A held run is an operational problem, not just the initiator's concern, so
    Org Admins are notified alongside whoever started it."""
    recipients = set()
    if started_by is not None:
        recipients.add(started_by)
    admins = await conn.fetch(
        "SELECT id FROM users WHERE org_id = $1 AND role = 'org_admin'",
        org_id,
    )
    for a in admins:
        recipients.add(a["id"])

    detail = (error_detail or "The run stopped after an error and needs review.")
    detail = detail[:2000]
    ids = []
    for uid in recipients:
        ids.append(
            await _upsert_todo(
                conn,
                org_id=org_id,
                user_id=uid,
                source=TODO_SOURCE_RUN_HELD,
                related_type="workflow_run",
                related_id=run_id,
                title="Workflow run held — needs attention",
                detail=detail,
                priority=5,
                action_key=_RUN_CONSOLE_PATH,
            )
        )
    return ids
