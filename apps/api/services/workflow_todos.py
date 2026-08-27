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
TODO_SOURCE_TRIGGER_EXPIRING = "workflow_trigger_expiring"

TODO_CATEGORY = "workflow"
_RUN_CONSOLE_PATH = "/admin/workflows/runs"
_TRIGGER_CONSOLE_PATH = "/admin/workflows/triggers"


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


async def create_trigger_expiring_alerts(
    conn, *, org_id, trigger_id, created_by, title: str, detail: str
) -> list:
    """Tell the people who own a schedule that it is about to stop running.

    SAME MECHANISM, DIFFERENT SUBJECT. This is deliberately not a second
    notification system and not a second recipient rule: it reuses
    :func:`_upsert_todo` and mirrors :func:`create_held_run_alerts` exactly —
    the person who configured the thing (here the trigger's ``created_by``,
    there the run's ``started_by``) plus every Org Admin of that org, because a
    schedule silently winding down is an operational fact, not just the
    author's business.

    Keyed on (``source='workflow_trigger_expiring'``,
    ``related_type='workflow_trigger'``, ``related_id=trigger_id``), so the
    "one run left" notice and the "that was the last run" notice that follows
    it UPDATE one row per recipient rather than stacking two.

    ``priority=4`` is chosen against the reader, not in the abstract:
    ``routers/dashboard.py`` sorts ``priority DESC``, so 4 sits immediately
    below the held-run alert's 5. A schedule that is ending is worth knowing
    about; a run that has already broken is worth knowing about first.
    """
    recipients = set()
    if created_by is not None:
        recipients.add(created_by)
    admins = await conn.fetch(
        "SELECT id FROM users WHERE org_id = $1 AND role = 'org_admin'",
        org_id,
    )
    for a in admins:
        recipients.add(a["id"])

    ids = []
    for uid in recipients:
        ids.append(
            await _upsert_todo(
                conn,
                org_id=org_id,
                user_id=uid,
                source=TODO_SOURCE_TRIGGER_EXPIRING,
                related_type="workflow_trigger",
                related_id=trigger_id,
                title=title,
                detail=detail[:2000],
                priority=4,
                action_key=_TRIGGER_CONSOLE_PATH,
            )
        )
    return ids


async def dismiss_orphaned_run_alerts(conn, *, org_id=None) -> int:
    """Close held-run alerts whose run no longer exists. Returns how many.

    WHY THIS EXISTS, stated from what was actually measured rather than from a
    hypothetical. Two such rows were live in the deployed database when this
    was written, both pointing at runs deleted by a verify script's teardown,
    both belonging to a REAL org_admin rather than to a fixture user —
    ``create_held_run_alerts`` fans out to every ``users.role='org_admin'`` in
    the org, so a teardown that deletes its todos by fixture user id strands
    the real admins' copies. ``verify_workflowmgr1.py``,
    ``verify_chancery7.py`` and ``verify_workflowpermsfix.py`` all delete
    ``workflow_runs`` with no ``member_todos`` cleanup at all, so this is a
    recurring producer, not a one-off.

    NO PRODUCTION CODE PATH DELETES A ``workflow_runs`` ROW — checked across
    every router and service; every deleter in the repo is a test teardown. So
    "fix whatever is deleting runs" has nothing in the application to fix, and
    a one-time data migration would be stale the next time a verify script
    runs. A sweep is the honest shape.

    ``dismissed``, not ``DELETE``. The alert is a real record that a real run
    really held; what is no longer true is that anyone can act on it. Dismissed
    rows drop out of ``list_todos`` and the dashboard brief (both filter
    ``status='open'``) while remaining auditable.

    ``org_id=None`` sweeps every org — that is the scheduler's platform scope,
    the same scope its trigger scan already runs at, and it is why this takes
    the tick's plain connection rather than an org-scoped pool connection.
    Passing an ``org_id`` narrows it to one tenant.
    """
    note = " [Auto-dismissed: the workflow run this alert points to no longer exists.]"
    return int(
        (
            await conn.execute(
                """
                UPDATE member_todos t
                SET status = 'dismissed',
                    detail = left(coalesce(t.detail, '') || $2, 2000),
                    updated_at = now()
                WHERE t.source = $1
                  AND t.related_type = 'workflow_run'
                  AND t.related_id IS NOT NULL
                  AND t.status = 'open'
                  AND ($3::uuid IS NULL OR t.org_id = $3)
                  AND NOT EXISTS (
                      SELECT 1 FROM workflow_runs r WHERE r.id = t.related_id
                  )
                """,
                TODO_SOURCE_RUN_HELD, note, org_id,
            )
        ).split()[-1]
    )
