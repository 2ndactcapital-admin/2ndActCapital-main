"""The scheduled-trigger firing loop.

One tick = scan every active ``trigger_type='scheduled'`` row across all orgs,
ask :mod:`services.workflow_schedule` whether each is due *in its own
timezone*, and fire the ones that are through the REAL
``workflow_engine.start_workflow_run`` — the same entrypoint a manual start and
an event trigger use. Nothing about execution, holding, or alerting is
reimplemented here; a scheduler-fired run that fails is held and alerted by
``workflow_engine._hold_run`` → ``workflow_todos.create_held_run_alerts``
exactly like any other run, because it IS any other run.

The process entrypoint is ``apps/api/workflow_scheduler_tick.py`` (the Render
``type: cron`` service). This module holds the logic so it can be driven
directly, with an injected instant, by ``scripts/verify_schedulercore.py``.

────────────────────────────────────────────────────────────────────────────
THE TWO OVERLAP QUESTIONS ARE NOT THE SAME QUESTION
────────────────────────────────────────────────────────────────────────────
Render's ``type: cron`` guarantees at most one run of **the cron job service
itself** at a time (it delays the next run while one is still active). That is
real and it is why this module does not take a global "is a tick already
running" lock — building one would duplicate a platform guarantee.

It says nothing about the **workflows** a tick fires. Two different triggers
fire two independent runs, and a single trigger can come due again while its
previous run is still going. That is the overlap this module checks, at
:func:`_workflow_in_progress`, and it is checked per trigger, before the claim.

``held`` counts as in-progress. There is no unhold/resume path anywhere in the
engine, so a held run is an unresolved run of that workflow — re-firing it
every five minutes would stampede a broken workflow and bury the operator in
alert todos. Terminal means ``completed`` / ``failed`` / ``cancelled``.

────────────────────────────────────────────────────────────────────────────
THE IDEMPOTENCY GUARANTEE, AND ITS ONE HONEST LIMIT
────────────────────────────────────────────────────────────────────────────
The fire decision and its record are ONE statement:

    UPDATE workflow_triggers
       SET last_fired_at = $occurrence, occurrence_count = occurrence_count + 1
     WHERE id = $1 AND is_active
       AND (last_fired_at IS NULL OR last_fired_at < $occurrence)
       AND (max_occurrences IS NULL OR occurrence_count < max_occurrences)

Two concurrent ticks evaluating the same occurrence both compute the same
``$occurrence``; the first UPDATE to commit moves ``last_fired_at`` and the
second matches **zero rows**. The loser does not fire. The ``max_occurrences``
predicate is repeated inside the claim on purpose — checking it only in the
pure evaluator would leave a TOCTOU gap that lets a capped trigger overshoot.

The limit, stated rather than glossed: ``start_workflow_run`` takes a *pool*
and opens its own transactions, so it cannot be enlisted in the claim's
transaction. The order is therefore **claim, commit, then fire**. If the
process dies between the two, the occurrence is recorded as fired and no run
exists — one missed run, logged. The alternative (fire, then claim) risks
firing the same occurrence repeatedly, which is strictly worse: a duplicated
side effect cannot be un-done, a missed one is visible in the tick log and
fires again on the next occurrence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from services import workflow_engine
from services.action_registry import REGISTRY
from services.database import reset_rls_context, set_rls_context
from services.workflow_schedule import (
    DEFAULT_LOOKBACK_MINUTES,
    ScheduleError,
    evaluate_trigger,
)

SCHEDULED_TRIGGER_TYPE = "scheduled"

# A run in one of these states is finished and does not block the next fire.
# Anything else — 'running', 'held' — does. See the module docstring.
TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled")


@dataclass
class TickResult:
    """What one tick did. Every trigger examined lands in exactly one bucket."""

    examined: int = 0
    fired: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"examined={self.examined} fired={len(self.fired)} "
            f"skipped={len(self.skipped)} errors={len(self.errors)}"
        )


def ensure_actions_registered() -> int:
    """Populate the global action REGISTRY, and return how many actions it holds.

    THIS IS NOT OPTIONAL, and it is not the API's job. ``REGISTRY`` is filled by
    ``services.assistant_actions.register_all()``, which until now was called
    from exactly one place: ``main.py``'s FastAPI ``startup`` hook. The
    scheduler is a SEPARATE process that never starts FastAPI, so without this
    call its registry is EMPTY — and an empty registry does not fail loudly. It
    fails quietly: ``_execute_service_task`` resolves every action to ``None``,
    returns ``{"resolved": false}``, and the engine marks the step
    **completed**. Every scheduled workflow would report success while invoking
    nothing at all.

    Caught by verify_schedulercore.py, which asserted a Service Task really held
    on a permission check and instead watched the run sail to 'completed'.

    Idempotent — ``REGISTRY.register`` is a dict assignment, so repeated calls
    are free and the function is safe to call once per tick.
    """
    from services.assistant_actions import register_all

    register_all()
    return len(REGISTRY.all())


async def load_due_candidates(conn) -> list:
    """Every active scheduled trigger, all orgs, with its definition's current version.

    Platform scope by design: the scheduler is not acting for a tenant, it is
    acting for all of them, so this scan carries no org filter. Isolation is
    enforced where it matters — each run is created under ITS OWN trigger's
    org_id and RLS context (see :func:`_fire`), never the scanning connection's.
    """
    return await conn.fetch(
        """
        SELECT t.id, t.org_id, t.workflow_definition_id, t.schedule_cron,
               t.timezone, t.start_date, t.end_date, t.max_occurrences,
               t.occurrence_count, t.last_fired_at, t.created_by,
               d.name AS workflow_name,
               v.id   AS workflow_version_id
        FROM workflow_triggers t
        JOIN workflow_definitions d ON d.id = t.workflow_definition_id
        LEFT JOIN workflow_versions v
               ON v.workflow_definition_id = t.workflow_definition_id
              AND v.is_current
        WHERE t.trigger_type = $1 AND t.is_active
        ORDER BY t.created_at, t.id
        """,
        SCHEDULED_TRIGGER_TYPE,
    )


async def _workflow_in_progress(conn, *, definition_id, org_id):
    """The non-terminal run blocking this trigger's workflow, or None.

    Scoped to the trigger's own org as well as its definition: two orgs running
    copies of the same definition must not block each other.
    """
    return await conn.fetchrow(
        """
        SELECT r.id, r.status, r.started_at
        FROM workflow_runs r
        JOIN workflow_versions v ON v.id = r.workflow_version_id
        WHERE v.workflow_definition_id = $1
          AND r.org_id = $2
          AND r.status <> ALL($3::text[])
        ORDER BY r.started_at DESC
        LIMIT 1
        """,
        definition_id, org_id, list(TERMINAL_RUN_STATUSES),
    )


async def _claim(conn, *, trigger_id, occurrence_utc) -> int | None:
    """Atomically claim ``occurrence_utc`` for this trigger.

    Returns the new occurrence_count, or ``None`` when the claim was lost —
    another tick already recorded this occurrence, the trigger was deactivated,
    or its cap filled in between. See the module docstring for why this single
    statement IS the idempotency guarantee.
    """
    return await conn.fetchval(
        """
        UPDATE workflow_triggers
        SET last_fired_at = $2,
            occurrence_count = occurrence_count + 1
        WHERE id = $1
          AND is_active
          AND (last_fired_at IS NULL OR last_fired_at < $2)
          AND (max_occurrences IS NULL OR occurrence_count < max_occurrences)
        RETURNING occurrence_count
        """,
        trigger_id, occurrence_utc,
    )


async def _fire(pool, *, trigger, occurrence_utc):
    """Start the run through the REAL engine, under the trigger's own org context.

    ``started_by`` is the trigger's ``created_by`` — the real user who
    configured the schedule. That is what makes a held scheduler-fired run
    alert the same
    people as any other held run: ``create_held_run_alerts`` notifies the
    starter plus every org_admin, and the starter here is a real row.
    """
    tokens = set_rls_context(trigger["org_id"], False)
    try:
        return await workflow_engine.start_workflow_run(
            pool,
            trigger["workflow_version_id"],
            trigger["org_id"],
            {
                "trigger_id": str(trigger["id"]),
                "trigger_type": SCHEDULED_TRIGGER_TYPE,
                "scheduled_occurrence": occurrence_utc.isoformat(),
            },
            trigger["created_by"],
        )
    finally:
        reset_rls_context(tokens)


async def run_scheduler_tick(
    conn,
    pool,
    *,
    now_utc: datetime | None = None,
    lookback_minutes: int = DEFAULT_LOOKBACK_MINUTES,
    log=print,
) -> TickResult:
    """Run one scan-evaluate-claim-fire pass.

    ``conn`` is a plain connection used for the platform-wide scan and for the
    claim; ``pool`` is the RLS-aware application pool the engine runs on.
    ``now_utc`` is injectable so the decision can be proven against a fixed
    instant rather than against the clock.

    EVERY outcome is logged with its reason. A scheduler whose quiet ticks and
    whose suppressed ticks look identical cannot be operated.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    result = TickResult()
    n_actions = ensure_actions_registered()
    log(f"[scheduler] tick at {now_utc.isoformat()} "
        f"(lookback {lookback_minutes}m, {n_actions} actions registered)")

    rows = await load_due_candidates(conn)
    result.examined = len(rows)

    for row in rows:
        trigger = dict(row)
        tag = (f"trigger={trigger['id']} org={trigger['org_id']} "
               f"workflow={trigger['workflow_name']!r}")

        try:
            decision = evaluate_trigger(
                schedule_cron=trigger["schedule_cron"],
                timezone_name=trigger["timezone"],
                now_utc=now_utc,
                last_fired_at=trigger["last_fired_at"],
                start_date=trigger["start_date"],
                end_date=trigger["end_date"],
                max_occurrences=trigger["max_occurrences"],
                occurrence_count=trigger["occurrence_count"] or 0,
                lookback_minutes=lookback_minutes,
            )
        except ScheduleError as exc:
            # A misconfigured schedule must be loud and must not stop the tick:
            # one bad cron string cannot be allowed to starve every other org's
            # triggers for the rest of the scan.
            log(f"[scheduler] ERROR  {tag} — unusable schedule: {exc}")
            result.errors.append({"trigger_id": trigger["id"], "error": str(exc)})
            continue

        if not decision.due:
            log(f"[scheduler] skip   {tag} — {decision.reason}")
            result.skipped.append({
                "trigger_id": trigger["id"], "reason": decision.reason,
            })
            continue

        if trigger["workflow_version_id"] is None:
            log(f"[scheduler] ERROR  {tag} — due, but the definition has no "
                f"current version; nothing to run")
            result.errors.append({
                "trigger_id": trigger["id"],
                "error": "definition has no current version",
            })
            continue

        blocking = await _workflow_in_progress(
            conn,
            definition_id=trigger["workflow_definition_id"],
            org_id=trigger["org_id"],
        )
        if blocking is not None:
            # Visible, not silent: this is the case an operator most needs to
            # see, because a workflow that never finishes looks exactly like a
            # scheduler that never fires.
            log(f"[scheduler] SKIP-OVERLAP {tag} — occurrence "
                f"{decision.occurrence_utc.isoformat()} suppressed: run "
                f"{blocking['id']} is still {blocking['status']} "
                f"(started {blocking['started_at'].isoformat()})")
            result.skipped.append({
                "trigger_id": trigger["id"],
                "reason": f"overlap: run {blocking['id']} is {blocking['status']}",
                "blocking_run_id": blocking["id"],
                "occurrence_utc": decision.occurrence_utc,
            })
            continue

        new_count = await _claim(
            conn, trigger_id=trigger["id"], occurrence_utc=decision.occurrence_utc,
        )
        if new_count is None:
            log(f"[scheduler] skip   {tag} — claim lost; occurrence "
                f"{decision.occurrence_utc.isoformat()} was already taken")
            result.skipped.append({
                "trigger_id": trigger["id"], "reason": "claim lost (already fired)",
            })
            continue

        try:
            run = await _fire(pool, trigger=trigger, occurrence_utc=decision.occurrence_utc)
        except Exception as exc:  # noqa: BLE001
            # The claim is committed and deliberately NOT rolled back — see the
            # module docstring. The engine has already held-and-alerted any
            # failure that got as far as creating a run.
            log(f"[scheduler] ERROR  {tag} — occurrence "
                f"{decision.occurrence_utc.isoformat()} claimed but the run "
                f"failed to start: {type(exc).__name__}: {exc}")
            result.errors.append({
                "trigger_id": trigger["id"],
                "error": f"{type(exc).__name__}: {exc}",
                "occurrence_utc": decision.occurrence_utc,
            })
            continue

        log(f"[scheduler] FIRED  {tag} — run {run['run_id']} status="
            f"{run['status']} for occurrence "
            f"{decision.occurrence_utc.isoformat()} (fire #{new_count})")
        result.fired.append({
            "trigger_id": trigger["id"],
            "run_id": run["run_id"],
            "status": run["status"],
            "occurrence_utc": decision.occurrence_utc,
            "occurrence_count": new_count,
        })

    log(f"[scheduler] tick complete — {result.summary()}")
    return result
