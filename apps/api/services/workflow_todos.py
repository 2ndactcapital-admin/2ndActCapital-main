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

org_admin role reconciliation (Task 4): the recipient rule's "every Org Admin
of the org" leg used to be a raw ``users.role = 'org_admin'`` scan. It now
resolves by the real ``manage_org_settings`` PERMISSION
(``rbac.get_users_with_permission``) — an org can grant alert visibility to
someone who is not a full admin later without touching this module again.

org_admin role reconciliation (Task 5): a recipient set that resolves to
EMPTY used to write nothing at all and fail silently (the live, confirmed
case: the Hollisworks org, which currently has zero org_admin holders and
would have zero recipients for any run/trigger with no ``started_by`` /
``created_by``). ``_alert_recipients_or_record_failure`` below now writes a
findable ``audit_log`` row instead — see its docstring.

LiteLLM Phase D1c (litellmphased1c.structural): a THIRD alert kind,
``create_credential_failure_alerts``, reuses this same module and the same
``_upsert_todo`` / ``_record_undelivered_alert`` machinery for an org's own
AI provider credential going bad — proof that this module's mechanism
generalizes to a subject with no natural "who started it" recipient, not
just to workflow runs/triggers.

LiteLLM D2 §3 follow-up (litellmavailability.structural): a FOURTH alert
kind, ``create_model_availability_alerts``, reuses the same mechanism again
for a Hollisworks super-admin deprecating/disabling a platform catalog
model that one or more orgs have selected — same manage_org_settings
recipient rule, same non-uuid-subject-folded-into-``source`` encoding
``create_credential_failure_alerts`` already established.
"""
from __future__ import annotations

from services.audit import write_audit_log
from services.database import get_pool, platform_scope
from services.rbac import ORG_ADMIN_PERMISSION, get_users_with_permission

# Stable ``source`` markers so a run/step's todos can be found and updated
# idempotently (there is no natural unique key on member_todos to rely on).
TODO_SOURCE_USER_TASK = "workflow_user_task"
TODO_SOURCE_RUN_HELD = "workflow_run_held"
TODO_SOURCE_TRIGGER_EXPIRING = "workflow_trigger_expiring"

# audit_log.action for Task 5's loud-failure record.
ALERT_UNDELIVERED_ACTION = "workflow_alert_undelivered"


async def _org_admin_recipients(org_id) -> set[str]:
    """Every real holder of the org-admin permission in ``org_id``, as
    strings — the permission-based replacement for the old
    ``role = 'org_admin'`` scan. Recipients are collected as strings
    throughout this module (``get_users_with_permission`` already returns
    ``str(uuid)``) so a ``started_by``/``created_by`` UUID object and its
    string twin dedupe correctly in the same ``set``."""
    pool = await get_pool()
    ids = await get_users_with_permission(pool, org_id, ORG_ADMIN_PERMISSION)
    return set(ids)


async def _record_undelivered_alert(
    conn, *, org_id, source: str, related_type: str, related_id, reason: str,
    action: str = ALERT_UNDELIVERED_ACTION,
) -> None:
    """Task 5: a recipient set that resolved to EMPTY used to write nothing
    and fail silently — this is the loud, findable replacement.

    Reuses ``audit_log`` (``user_id`` is nullable there — confirmed against
    docs/schema_snapshot.sql) rather than a new table: no schema change, and
    it is already the place every other "something happened, nobody to
    attribute it to a specific write" event in this app is queryable from.
    ``write_audit_log`` never raises, so a failure here cannot itself break
    the caller's hold/expiry transaction — the finding is the audit row
    existing at all, not an exception propagating.

    ``action`` defaults to the workflow-alert value every existing caller
    already relies on; D1c's credential-failure alert passes its own distinct
    action name (CREDENTIAL_ALERT_UNDELIVERED_ACTION) so the two event kinds
    stay independently queryable rather than colliding under one string.
    """
    await write_audit_log(
        org_id=org_id,
        action=action,
        table_name=related_type,
        record_id=related_id,
        new={"source": source, "reason": reason},
    )

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
        recipients.add(str(started_by))
    recipients |= await _org_admin_recipients(org_id)

    detail = (error_detail or "The run stopped after an error and needs review.")
    detail = detail[:2000]

    if not recipients:
        await _record_undelivered_alert(
            conn,
            org_id=org_id,
            source=TODO_SOURCE_RUN_HELD,
            related_type="workflow_run",
            related_id=run_id,
            reason=(
                "run held with no resolvable recipient: started_by is null "
                "and the org has no manage_org_settings holder"
            ),
        )
        return []

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



# ── D1c: AI credential-failure alerting ─────────────────────────────────────
#
# services.extraction._execute_chain (LiteLLM Phase D1c) reuses this SAME
# member_todos path when an org's OWN AI provider credential fails — not a
# second notification mechanism. Unlike a held run or an expiring trigger,
# there is no natural "who did this" recipient (a bad credential is not tied
# to any one run or trigger a person started), so the recipient set is
# manage_org_settings holders only.

TODO_SOURCE_CREDENTIAL_FAILURE = "ai_credential_failure"
CREDENTIAL_ALERT_UNDELIVERED_ACTION = "ai_credential_alert_undelivered"
_CREDENTIAL_CONSOLE_PATH = "/admin/settings"


async def create_credential_failure_alerts(
    conn, *, org_id, provider: str, error_detail: str
) -> list:
    """Alert every manage_org_settings holder that this org's OWN
    ``provider`` credential is failing.

    Keyed on (source=f'ai_credential_failure:{provider}',
    related_type='org_settings', related_id=org_id) so a repeated failure for
    the SAME org+provider refreshes (re-opens) one todo rather than stacking
    duplicates — the same idempotency discipline every other ``_upsert_todo``
    caller in this module follows.

    Zero resolvable recipients (the live Hollisworks org is a real, current
    case — it holds zero manage_org_settings grants) writes a findable
    audit_log row instead of failing silently, via the same
    ``_record_undelivered_alert`` helper ``create_held_run_alerts`` uses, with
    its own distinct action name so the two event kinds never collide.
    """
    recipients = await _org_admin_recipients(org_id)
    source = f"{TODO_SOURCE_CREDENTIAL_FAILURE}:{provider}"
    detail = (error_detail or f"This org's {provider} credential is failing.")[:2000]

    if not recipients:
        await _record_undelivered_alert(
            conn,
            org_id=org_id,
            source=source,
            related_type="org_settings",
            related_id=org_id,
            reason=(
                f"{provider} credential failure with no resolvable "
                "recipient: the org has no manage_org_settings holder"
            ),
            action=CREDENTIAL_ALERT_UNDELIVERED_ACTION,
        )
        return []

    ids = []
    for uid in recipients:
        ids.append(
            await _upsert_todo(
                conn,
                org_id=org_id,
                user_id=uid,
                source=source,
                related_type="org_settings",
                related_id=org_id,
                title=f"AI provider credential failing — {provider}",
                detail=detail,
                priority=5,
                action_key=_CREDENTIAL_CONSOLE_PATH,
            )
        )
    return ids


# ── litellmavailability: model catalog availability alerting ───────────────
#
# routers/org_settings.py's PUT /admin/model-catalog/{model_id}/availability
# (LiteLLM D2 §3) reuses this SAME module and mechanism for a FOURTH alert
# kind: a Hollisworks super-admin moving a catalog model to 'deprecated' or
# 'disabled'. Recipients are manage_org_settings holders, same as the
# credential-failure alert above — there is no natural "who picked this
# model" individual, only the org's settings owners. The router computes the
# AFFECTED org set itself (services.model_catalog.get_orgs_selecting) and
# calls this once per affected org — never every org on the platform.

TODO_SOURCE_MODEL_AVAILABILITY = "ai_model_availability"
MODEL_AVAILABILITY_ALERT_UNDELIVERED_ACTION = "ai_model_availability_alert_undelivered"
_MODEL_CATALOG_CONSOLE_PATH = "/admin/settings"


async def create_model_availability_alerts(
    conn, *, org_id, model_id: str, availability: str
) -> list:
    """Alert every manage_org_settings holder of ``org_id`` that
    ``model_id`` — a model this org has selected — just moved to
    ``availability`` ('deprecated' or 'disabled').

    Keyed on (source=f'ai_model_availability:{model_id}',
    related_type='org_settings', related_id=org_id) — the identical
    encoding ``create_credential_failure_alerts`` uses for a subject
    (``provider``, here ``model_id``) that has no uuid of its own:
    ``related_id`` on both ``member_todos`` and ``audit_log`` is a real
    uuid column, so the non-uuid identifier is folded into ``source``
    instead, and ``org_id`` (a real uuid) is the actual related resource.
    A repeated transition for the SAME org+model refreshes one todo rather
    than stacking duplicates.

    Zero resolvable recipients (the live Hollisworks org is a real, current
    case) writes a findable audit_log row via the same
    ``_record_undelivered_alert`` helper every other alert kind in this
    module uses, with its own distinct action name.
    """
    recipients = await _org_admin_recipients(org_id)
    source = f"{TODO_SOURCE_MODEL_AVAILABILITY}:{model_id}"
    if availability == "disabled":
        detail = (
            f"The '{model_id}' model your organization uses is now "
            f"disabled on the Hollisworks platform. Calls that would have "
            f"used it now fall back to your organization's safe model."
        )
    else:
        detail = (
            f"The '{model_id}' model your organization uses has been "
            f"deprecated on the Hollisworks platform. It still works today, "
            f"but Hollisworks recommends migrating to another model."
        )
    detail = detail[:2000]

    if not recipients:
        await _record_undelivered_alert(
            conn,
            org_id=org_id,
            source=source,
            related_type="org_settings",
            related_id=org_id,
            reason=(
                f"model '{model_id}' set to {availability!r} with no "
                "resolvable recipient: the org has no manage_org_settings "
                "holder"
            ),
            action=MODEL_AVAILABILITY_ALERT_UNDELIVERED_ACTION,
        )
        return []

    ids = []
    for uid in recipients:
        ids.append(
            await _upsert_todo(
                conn,
                org_id=org_id,
                user_id=uid,
                source=source,
                related_type="org_settings",
                related_id=org_id,
                title=f"AI model {availability} — {model_id}",
                detail=detail,
                priority=5,
                action_key=_MODEL_CATALOG_CONSOLE_PATH,
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
        recipients.add(str(created_by))
    recipients |= await _org_admin_recipients(org_id)

    if not recipients:
        await _record_undelivered_alert(
            conn,
            org_id=org_id,
            source=TODO_SOURCE_TRIGGER_EXPIRING,
            related_type="workflow_trigger",
            related_id=trigger_id,
            reason=(
                "trigger expiring with no resolvable recipient: created_by "
                "is null and the org has no manage_org_settings holder"
            ),
        )
        return []

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


# ── litellmphaseg: AI spend budget alerting ─────────────────────────────────
#
# A FIFTH and SIXTH alert kind, reusing the SAME mechanism yet again:
# services.ai_budgets.sync_org_spend / sync_platform_spend call these when
# an org's own monthly AI budget, or the separate Hollisworks-wide ceiling,
# crosses its warning threshold or its cap — never a new notification path.
# Recipients are manage_org_settings holders, the same rule every AI-alert
# kind in this module already uses. The Hollisworks-wide ceiling's alerts
# always target HOLLISWORKS_ORG_ID's manage_org_settings holders
# specifically (the ceiling protects HOLLISWORKS' bill, regardless of which
# org's marginal spend tipped it over) — the real Hollisworks org currently
# has ZERO of them, the same live, current case D1c's credential-failure
# alert already documented, and a real instance of the zero-recipient path
# below every single time the ceiling is exercised.

from services.litellm_credentials import HOLLISWORKS_ORG_ID

TODO_SOURCE_BUDGET_WARNING = "ai_budget_warning"
TODO_SOURCE_BUDGET_CAP = "ai_budget_cap"
BUDGET_WARNING_ALERT_UNDELIVERED_ACTION = "ai_budget_warning_alert_undelivered"
BUDGET_CAP_ALERT_UNDELIVERED_ACTION = "ai_budget_cap_alert_undelivered"

TODO_SOURCE_PLATFORM_CEILING_WARNING = "ai_platform_ceiling_warning"
TODO_SOURCE_PLATFORM_CEILING_CAP = "ai_platform_ceiling_cap"
PLATFORM_CEILING_WARNING_ALERT_UNDELIVERED_ACTION = "ai_platform_ceiling_warning_alert_undelivered"
PLATFORM_CEILING_CAP_ALERT_UNDELIVERED_ACTION = "ai_platform_ceiling_cap_alert_undelivered"

_BUDGET_CONSOLE_PATH = "/admin/settings"


async def create_budget_warning_alert(
    conn, *, org_id, spend_usd: float, budget_usd: float, warning_pct: float
) -> list:
    """Alert every manage_org_settings holder of ``org_id`` that this org's
    OWN monthly AI budget just crossed its warning threshold. Called by
    services.ai_budgets.sync_org_spend AT MOST ONCE PER PERIOD (guarded by
    org_ai_spend_cache.warning_alerted_at, not by this function) — repeated
    syncs after the crossing are expected to skip calling this entirely."""
    recipients = await _org_admin_recipients(org_id)
    detail = (
        f"This organization has used ${spend_usd:,.2f} of its ${budget_usd:,.2f} "
        f"monthly AI budget ({warning_pct:.0f}% threshold reached). No action "
        f"is required yet — at the full budget, AI calls automatically switch "
        f"to the organization's safe model rather than stopping."
    )[:2000]

    if not recipients:
        await _record_undelivered_alert(
            conn, org_id=org_id, source=TODO_SOURCE_BUDGET_WARNING,
            related_type="org_settings", related_id=org_id,
            reason=(
                "budget warning threshold crossed with no resolvable "
                "recipient: the org has no manage_org_settings holder"
            ),
            action=BUDGET_WARNING_ALERT_UNDELIVERED_ACTION,
        )
        return []

    ids = []
    for uid in recipients:
        ids.append(
            await _upsert_todo(
                conn, org_id=org_id, user_id=uid, source=TODO_SOURCE_BUDGET_WARNING,
                related_type="org_settings", related_id=org_id,
                title="AI budget warning threshold reached",
                detail=detail, priority=6, action_key=_BUDGET_CONSOLE_PATH,
            )
        )
    return ids


async def create_budget_cap_alert(conn, *, org_id, spend_usd: float, budget_usd: float) -> list:
    """Alert every manage_org_settings holder of ``org_id`` that this org's
    OWN monthly AI budget cap was reached and calls have degraded to the
    safe model — NOT that AI has stopped working."""
    recipients = await _org_admin_recipients(org_id)
    detail = (
        f"This organization reached its ${budget_usd:,.2f} monthly AI budget "
        f"(${spend_usd:,.2f} spent). AI calls have automatically switched to "
        f"the organization's safe model for the remainder of the period — "
        f"they are NOT disabled."
    )[:2000]

    if not recipients:
        await _record_undelivered_alert(
            conn, org_id=org_id, source=TODO_SOURCE_BUDGET_CAP,
            related_type="org_settings", related_id=org_id,
            reason=(
                "budget cap crossed with no resolvable recipient: the org "
                "has no manage_org_settings holder"
            ),
            action=BUDGET_CAP_ALERT_UNDELIVERED_ACTION,
        )
        return []

    ids = []
    for uid in recipients:
        ids.append(
            await _upsert_todo(
                conn, org_id=org_id, user_id=uid, source=TODO_SOURCE_BUDGET_CAP,
                related_type="org_settings", related_id=org_id,
                title="AI budget cap reached — degraded to safe model",
                detail=detail, priority=5, action_key=_BUDGET_CONSOLE_PATH,
            )
        )
    return ids


async def create_platform_ceiling_warning_alert(
    conn, *, spend_usd: float, ceiling_usd: float, warning_pct: float
) -> list:
    """Alert every manage_org_settings holder of HOLLISWORKS_ORG_ID that the
    platform-wide (shared-key) spend ceiling crossed its warning threshold."""
    recipients = await _org_admin_recipients(HOLLISWORKS_ORG_ID)
    detail = (
        f"Platform-wide AI spend (the shared Hollisworks key) has reached "
        f"${spend_usd:,.2f} of the ${ceiling_usd:,.2f} monthly ceiling "
        f"({warning_pct:.0f}% threshold)."
    )[:2000]

    if not recipients:
        await _record_undelivered_alert(
            conn, org_id=HOLLISWORKS_ORG_ID, source=TODO_SOURCE_PLATFORM_CEILING_WARNING,
            related_type="org_settings", related_id=HOLLISWORKS_ORG_ID,
            reason=(
                "platform ceiling warning threshold crossed with no "
                "resolvable recipient: the Hollisworks org has no "
                "manage_org_settings holder"
            ),
            action=PLATFORM_CEILING_WARNING_ALERT_UNDELIVERED_ACTION,
        )
        return []

    ids = []
    for uid in recipients:
        ids.append(
            await _upsert_todo(
                conn, org_id=HOLLISWORKS_ORG_ID, user_id=uid,
                source=TODO_SOURCE_PLATFORM_CEILING_WARNING,
                related_type="org_settings", related_id=HOLLISWORKS_ORG_ID,
                title="Platform AI spend ceiling warning",
                detail=detail, priority=6, action_key=_BUDGET_CONSOLE_PATH,
            )
        )
    return ids


async def create_platform_ceiling_cap_alert(conn, *, spend_usd: float, ceiling_usd: float) -> list:
    """Alert every manage_org_settings holder of HOLLISWORKS_ORG_ID that the
    platform-wide ceiling was reached — every org still on the shared
    platform key now degrades to its own safe model, independent of any
    org's own budget."""
    recipients = await _org_admin_recipients(HOLLISWORKS_ORG_ID)
    detail = (
        f"Platform-wide AI spend (the shared Hollisworks key) reached its "
        f"${ceiling_usd:,.2f} monthly ceiling (${spend_usd:,.2f} spent). "
        f"Every organization still on the shared platform key now degrades "
        f"to its own safe model for the remainder of the period."
    )[:2000]

    if not recipients:
        await _record_undelivered_alert(
            conn, org_id=HOLLISWORKS_ORG_ID, source=TODO_SOURCE_PLATFORM_CEILING_CAP,
            related_type="org_settings", related_id=HOLLISWORKS_ORG_ID,
            reason=(
                "platform ceiling cap crossed with no resolvable recipient: "
                "the Hollisworks org has no manage_org_settings holder"
            ),
            action=PLATFORM_CEILING_CAP_ALERT_UNDELIVERED_ACTION,
        )
        return []

    ids = []
    for uid in recipients:
        ids.append(
            await _upsert_todo(
                conn, org_id=HOLLISWORKS_ORG_ID, user_id=uid,
                source=TODO_SOURCE_PLATFORM_CEILING_CAP,
                related_type="org_settings", related_id=HOLLISWORKS_ORG_ID,
                title="Platform AI spend ceiling reached",
                detail=detail, priority=5, action_key=_BUDGET_CONSOLE_PATH,
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
    Passing an ``org_id`` narrows it to one tenant. See
    :func:`services.database.platform_scope` for why the ``is_super_admin``
    carve-out is re-asserted fresh in its own transaction here.
    """
    note = " [Auto-dismissed: the workflow run this alert points to no longer exists.]"
    async with platform_scope(conn):
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
