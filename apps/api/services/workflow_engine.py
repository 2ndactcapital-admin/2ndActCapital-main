"""Workflow execution engine (Workflow Manager — Phase 1 foundation).

SpiffWorkflow (a Python BPMN engine) owns the real BPMN token-passing,
gateway, timer and DMN semantics.  This module is the *governance / audit*
layer on top of it:

  * ``workflow_versions.bpmn_xml`` is the canonical stored BPMN artifact.
  * ``workflow_steps`` rows describe each Service/User task's governance
    metadata (autonomy tier, assigned role profile, action registry key).
    A step's ``step_key`` MUST equal the BPMN element id it governs.
  * ``workflow_runs`` / ``workflow_run_steps`` are the per-run audit trail.
    ``workflow_runs.spiff_serialized_state`` holds SpiffWorkflow's own
    serialized state so a run can be paused (at a User Task) and resumed.

Design decisions (confirmed, do not re-litigate):
  * We do NOT reimplement BPMN semantics — SpiffWorkflow does the stepping.
  * A ``bpmn:serviceTask`` maps to an action_registry_key on its
    ``workflow_steps`` row (NOT embedded in the BPMN), keeping the XML
    vendor-neutral.  Phase 1 *resolved* the action against the Sprint-11
    registry to prove the key is real, without running it.  That is still the
    default; an action opts in to real invocation with
    ``AssistantAction.workflow_invocable = True`` (see _execute_service_task).
  * User Task completion is the maker-checker "approve": the approver
    (``completed_by``) MUST differ from the ``proposed_by`` recorded when the
    task became active.  Enforced application-side here AND by the
    ``workflow_run_steps_maker_checker`` DB CHECK constraint.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import asyncpg

from SpiffWorkflow.bpmn.parser.BpmnParser import BpmnParser
from SpiffWorkflow.bpmn.parser.task_parsers import TaskParser
from SpiffWorkflow.bpmn.parser.util import full_tag
from SpiffWorkflow.bpmn.workflow import BpmnWorkflow
from SpiffWorkflow.bpmn.serializer.workflow import BpmnWorkflowSerializer
from SpiffWorkflow.bpmn.serializer.config import DEFAULT_CONFIG
from SpiffWorkflow.bpmn.serializer.default.task_spec import BpmnTaskSpecConverter
from SpiffWorkflow.bpmn.specs.defaults import NoneTask, ServiceTask, UserTask
from SpiffWorkflow.util.task import TaskState

from services.action_registry import REGISTRY
from services import workflow_todos

# SpiffWorkflow task-spec class names that map to our governed workflow_steps.
_SERVICE_CLS = "ServiceTask"
_USER_CLS = "UserTask"


class MakerCheckerError(Exception):
    """Raised when an approver tries to approve their own proposed step."""


class WorkflowEngineError(Exception):
    """Raised for structural problems executing a run (bad XML, missing step)."""


# ── SpiffWorkflow serializer ────────────────────────────────────────────────
# The base BPMN serializer config deliberately omits ServiceTask (the vanilla
# service task carries no extra attributes), so register it against the same
# attribute-free converter the other basic tasks use.
def _make_serializer() -> BpmnWorkflowSerializer:
    config = dict(DEFAULT_CONFIG)
    config[ServiceTask] = BpmnTaskSpecConverter
    registry = BpmnWorkflowSerializer.configure(config)
    return BpmnWorkflowSerializer(registry)


SERIALIZER = _make_serializer()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── BPMN parsing ────────────────────────────────────────────────────────────
class _BusinessRuleTaskParser(TaskParser):
    """Parse ``bpmn:businessRuleTask`` as a pass-through task.

    SpiffWorkflow's base parser only supports a Business Rule Task when a full DMN
    decision table is attached (otherwise it raises "no support implemented").
    The Phase-2 governance layer *records* a Business Rule Task (a workflow_steps
    row + a deterministic Tier-3 default) but does not yet evaluate DMN, so we map
    the element to a plain ``NoneTask`` purely so the process parses and validates.
    """

    def create_task(self):
        return NoneTask(self.spec, self.bpmn_id, **self.bpmn_attributes)


def _make_bpmn_parser() -> BpmnParser:
    """A BpmnParser that additionally accepts ``bpmn:businessRuleTask`` (DMN-less).

    Additive to Phase 1: existing service/user/send/gateway XML is unaffected.
    """
    parser = BpmnParser()
    parser.OVERRIDE_PARSER_CLASSES = {
        **parser.OVERRIDE_PARSER_CLASSES,
        full_tag("businessRuleTask"): (_BusinessRuleTaskParser, NoneTask),
    }
    return parser


def parse_bpmn(bpmn_xml: str):
    """Parse a BPMN XML string into a runnable SpiffWorkflow spec.

    Returns ``(spec, process_id)``.  ``bpmn_xml`` is the canonical text stored
    in ``workflow_versions.bpmn_xml``.
    """
    parser = _make_bpmn_parser()
    # lxml rejects unicode strings that carry an encoding declaration; feed bytes.
    parser.add_bpmn_str(bpmn_xml.encode("utf-8"), "workflow.bpmn")
    process_ids = parser.get_process_ids()
    if not process_ids:
        raise WorkflowEngineError("BPMN XML contains no executable process")
    process_id = process_ids[0]
    spec = parser.get_spec(process_id)
    return spec, process_id


def _new_workflow(bpmn_xml: str) -> BpmnWorkflow:
    spec, _ = parse_bpmn(bpmn_xml)
    return BpmnWorkflow(spec)


# ── State (de)serialization ─────────────────────────────────────────────────
def serialize_state(workflow: BpmnWorkflow) -> str:
    """Serialize an in-flight SpiffWorkflow into a JSON string for jsonb storage."""
    return SERIALIZER.serialize_json(workflow)


def deserialize_state(serialized: Any) -> BpmnWorkflow:
    """Rebuild a resumable SpiffWorkflow from stored ``spiff_serialized_state``.

    Accepts either the JSON string SpiffWorkflow produced or an already-decoded
    dict/list (asyncpg may hand back jsonb as either depending on codecs).
    """
    if not isinstance(serialized, str):
        serialized = json.dumps(serialized)
    return SERIALIZER.deserialize_json(serialized)


# ── Engine stepping ─────────────────────────────────────────────────────────
def _ready_user_task(workflow: BpmnWorkflow, step_key: str | None = None):
    for t in workflow.get_tasks(state=TaskState.READY):
        if t.task_spec.__class__.__name__ != _USER_CLS:
            continue
        if step_key is None or t.task_spec.bpmn_id == step_key:
            return t
    return None


async def _drive(workflow: BpmnWorkflow):
    """Advance the workflow, auto-executing Service Tasks, until it pauses at a
    User Task or completes.

    A base ``bpmn:serviceTask`` parks in the STARTED state ("external service
    running") rather than auto-completing — that is exactly our hook point.
    Returns the list of Service Task ``bpmn_id``s executed during this drive.
    """
    executed: list[str] = []
    while True:
        workflow.do_engine_steps()
        started = [
            t for t in workflow.get_tasks(state=TaskState.STARTED)
            if t.task_spec.__class__.__name__ == _SERVICE_CLS
        ]
        if not started:
            break
        for task in started:
            step_key = task.task_spec.bpmn_id
            result = await _execute_service_task(step_key)
            task.set_data(service_result=result)
            task.complete()
            executed.append(step_key)
    return executed


async def _execute_service_task(step_key: str) -> dict:
    """Resolve — and, for opt-in actions, actually INVOKE — a Service Task's action.

    Phase 1 only *resolved* ``action_registry_key`` against the Sprint-11 registry
    to prove the key was real, and deliberately did not run the handler.  That is
    still the default: flipping every Service Task to live invocation at once
    would silently change what every existing workflow does.

    An action opts in with ``AssistantAction.workflow_invocable = True``.  For
    those, the handler really runs, and:

      * its ``required_permission`` is re-checked against the member who started
        the run (Super Admin bypasses, per the platform-wide convention) — a
        workflow must never become a route around a permission gate;
      * any exception propagates, so ``start_workflow_run`` HOLDs the run with
        ``error_detail`` rather than recording a false success.  A failed
        external call must be loud.
    """
    # step_row is looked up by the caller and passed via _SERVICE_STEP_MAP.
    step = _SERVICE_STEP_MAP.get(step_key, {})
    action_key = step.get("action_registry_key")
    resolved = REGISTRY.get(action_key) if action_key else None
    result = {
        "action_registry_key": action_key,
        "resolved": resolved is not None,
        "access_type": getattr(resolved, "access_type", None),
        "invoked": False,
        "executed_at": _now().isoformat(),
    }
    if resolved is None or not getattr(resolved, "workflow_invocable", False):
        return result

    pool = _SERVICE_CONTEXT.get("pool")
    if pool is None:
        raise WorkflowEngineError(
            f"Service Task {step_key!r} invokes action {action_key!r} but the "
            "engine was driven without a database pool in context"
        )
    actor_id = _SERVICE_CONTEXT.get("actor_id")
    await _assert_action_permission(pool, resolved, actor_id, step_key)

    handler_result = await resolved.handler(
        pool=pool,
        user_id=actor_id,
        org_id=_SERVICE_CONTEXT.get("org_id"),
    )
    handler_result = handler_result or {}
    result["invoked"] = True
    result["ok"] = True
    # Only the JSON-safe parts: `render` may carry component props that are not
    # guaranteed serializable, and the run-step `result` column is jsonb.
    result["handler_data"] = handler_result.get("data")
    result["handler_text"] = handler_result.get("text")
    result["completed_at"] = _now().isoformat()
    return result


async def _assert_action_permission(pool, action, actor_id, step_key: str) -> None:
    """Raise unless ``actor_id`` may run ``action``'s handler.

    Imported locally: services.rbac / services.profiles are request-layer modules
    and importing them at module scope from the engine invites an import cycle.
    """
    permission_key = getattr(action, "required_permission", None)
    if not permission_key:
        return
    from services.profiles import user_has_permission
    from services.rbac import is_super_admin, load_principal

    if actor_id is None:
        raise WorkflowEngineError(
            f"Service Task {step_key!r} invokes action {action.key!r}, which "
            f"requires permission {permission_key!r}, but this run has no "
            "started_by member to check it against"
        )
    async with pool.acquire() as conn:
        principal = await load_principal(conn, actor_id)
    if principal is not None and is_super_admin(principal):
        return
    if await user_has_permission(pool, actor_id, permission_key):
        return
    raise WorkflowEngineError(
        f"Service Task {step_key!r} invokes action {action.key!r}, which requires "
        f"permission {permission_key!r}. The member who started this run "
        f"({actor_id}) does not hold it. A workflow does not widen a member's "
        "permissions."
    )


# Per-run map from service step_key -> workflow_steps row (set by callers before
# driving; kept module-local so _execute_service_task stays a pure hook).
_SERVICE_STEP_MAP: dict[str, dict] = {}

# Per-run invocation context (pool / org_id / actor_id) for workflow_invocable
# actions.  Kept module-local for the same reason as _SERVICE_STEP_MAP: it keeps
# _execute_service_task's signature at exactly ``(step_key)``, which existing
# tests monkeypatch.
_SERVICE_CONTEXT: dict[str, Any] = {}


# ── DB helpers ──────────────────────────────────────────────────────────────
async def _load_version(conn, workflow_version_id, org_id) -> asyncpg.Record:
    row = await conn.fetchrow(
        """
        SELECT id, workflow_definition_id, bpmn_xml
        FROM workflow_versions
        WHERE id = $1 AND org_id = $2
        """,
        workflow_version_id, org_id,
    )
    if row is None:
        raise WorkflowEngineError(f"workflow_version {workflow_version_id} not found for org")
    return row


async def _load_steps(conn, workflow_version_id, org_id) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, step_key, step_type, autonomy_tier,
               assigned_role_profile_id, action_registry_key, display_name
        FROM workflow_steps
        WHERE workflow_version_id = $1 AND org_id = $2
        """,
        workflow_version_id, org_id,
    )


# ── Public API ──────────────────────────────────────────────────────────────
async def start_workflow_run(
    pool, workflow_version_id, org_id, context: dict | None, started_by
) -> dict:
    """Start a run of a workflow version.

    Creates the ``workflow_runs`` row plus one ``workflow_run_steps`` row per
    governed ``workflow_steps`` row, instantiates the SpiffWorkflow spec, and
    steps it forward until it hits a User Task (pause) or completes.
    """
    context = context or {}
    async with pool.acquire() as conn:
        version = await _load_version(conn, workflow_version_id, org_id)
        steps = await _load_steps(conn, workflow_version_id, org_id)
        step_by_key = {s["step_key"]: dict(s) for s in steps}

        workflow = _new_workflow(version["bpmn_xml"])
        if context:
            workflow.set_data(**context)

        # Persist the run + its pending steps up-front, in their own committed
        # transaction, BEFORE driving the engine. A later failure must be
        # recordable as 'held' (Wave-4-style HOLD + ALERT) rather than vanishing
        # on rollback or leaving the run silently stuck in 'running'.
        async with conn.transaction():
            run_id = await conn.fetchval(
                """
                INSERT INTO workflow_runs
                    (workflow_version_id, org_id, status, context, started_by, started_at)
                VALUES ($1, $2, 'running', $3::jsonb, $4, now())
                RETURNING id
                """,
                workflow_version_id, org_id, json.dumps(context), started_by,
            )
            run_step_ids: dict[str, Any] = {}
            for s in steps:
                rsid = await conn.fetchval(
                    """
                    INSERT INTO workflow_run_steps
                        (workflow_run_id, workflow_step_id, org_id, status)
                    VALUES ($1, $2, $3, 'pending')
                    RETURNING id
                    """,
                    run_id, s["id"], org_id,
                )
                run_step_ids[s["step_key"]] = rsid

        try:
            async with conn.transaction():
                # Drive the engine: Service Tasks execute automatically.
                global _SERVICE_STEP_MAP, _SERVICE_CONTEXT
                _SERVICE_STEP_MAP = {
                    k: v for k, v in step_by_key.items() if v["step_type"] == "service"
                }
                _SERVICE_CONTEXT = {
                    "pool": pool, "org_id": org_id, "actor_id": started_by,
                }
                try:
                    executed = await _drive(workflow)
                finally:
                    _SERVICE_STEP_MAP = {}
                    _SERVICE_CONTEXT = {}

                # Mark executed Service Task steps completed.
                for step_key in executed:
                    rsid = run_step_ids.get(step_key)
                    if rsid is None:
                        continue
                    await conn.execute(
                        """
                        UPDATE workflow_run_steps
                        SET status = 'completed', started_at = now(), completed_at = now(),
                            result = $2::jsonb
                        WHERE id = $1
                        """,
                        rsid,
                        json.dumps(_service_result_for(workflow, step_key)),
                    )

                # Pause point or completion.
                ready = _ready_user_task(workflow)
                if ready is not None:
                    # Activate the pausing User Task; started_by "proposes" it so
                    # a different approver is required by maker-checker.
                    rsid = run_step_ids.get(ready.task_spec.bpmn_id)
                    await conn.execute(
                        """
                        UPDATE workflow_run_steps
                        SET status = 'active', started_at = now(), proposed_by = $2
                        WHERE id = $1
                        """,
                        rsid, started_by,
                    )
                    await conn.execute(
                        """
                        UPDATE workflow_runs
                        SET status = 'running', spiff_serialized_state = $2::jsonb
                        WHERE id = $1
                        """,
                        run_id, serialize_state(workflow),
                    )
                    # Surface the active User Task as a member_todos entry for
                    # each user holding its assigned role profile.
                    active_step = step_by_key.get(ready.task_spec.bpmn_id, {})
                    await workflow_todos.sync_user_task_todos(
                        conn,
                        org_id=org_id,
                        run_step_id=rsid,
                        step_key=ready.task_spec.bpmn_id,
                        display_name=active_step.get("display_name"),
                        assigned_role_profile_id=active_step.get("assigned_role_profile_id"),
                    )
                    status = "running"
                else:
                    await conn.execute(
                        """
                        UPDATE workflow_runs
                        SET status = 'completed', completed_at = now(),
                            spiff_serialized_state = $2::jsonb
                        WHERE id = $1
                        """,
                        run_id, serialize_state(workflow),
                    )
                    status = "completed"
        except Exception as exc:
            # Wave-4-style failure: HOLD and ALERT, never silently retry — even
            # for a manually-triggered run. Recorded in its own transaction
            # because the execution transaction above has rolled back.
            await _hold_run(conn, run_id, org_id, started_by, exc)
            raise

        return {
            "run_id": run_id,
            "status": status,
            "executed_service_steps": executed,
            "paused_at": ready.task_spec.bpmn_id if ready is not None else None,
        }


async def _hold_run(conn, run_id, org_id, started_by, exc: Exception) -> None:
    """Transition a run to 'held' with error_detail and raise the HOLD alert.

    Idempotent-safe to call once per failure; runs in its own transaction so it
    persists independently of the rolled-back execution transaction."""
    error_detail = f"{type(exc).__name__}: {exc}"
    async with conn.transaction():
        await conn.execute(
            """
            UPDATE workflow_runs
            SET status = 'held', error_detail = $2
            WHERE id = $1
            """,
            run_id, error_detail,
        )
        await workflow_todos.create_held_run_alerts(
            conn,
            org_id=org_id,
            run_id=run_id,
            started_by=started_by,
            error_detail=error_detail,
        )


def _service_result_for(workflow: BpmnWorkflow, step_key: str) -> dict:
    for t in workflow.get_tasks():
        if getattr(t.task_spec, "bpmn_id", None) == step_key:
            return t.data.get("service_result", {"action_registry_key": None})
    return {"action_registry_key": None}


async def complete_user_task(pool, workflow_run_step_id, completed_by, result: dict | None) -> dict:
    """Complete (approve) a paused User Task step and resume the run.

    For a Tier-1 step this is the maker-checker "approve": ``completed_by`` MUST
    differ from the step's ``proposed_by``.  Enforced here at the application
    level (clear error) in addition to the DB CHECK constraint.
    """
    result = result or {}
    async with pool.acquire() as conn:
        step = await conn.fetchrow(
            """
            SELECT rs.id, rs.workflow_run_id, rs.org_id, rs.status, rs.proposed_by,
                   ws.step_key, ws.autonomy_tier, ws.assigned_role_profile_id,
                   r.workflow_version_id, r.spiff_serialized_state, r.started_by
            FROM workflow_run_steps rs
            JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
            JOIN workflow_runs r ON r.id = rs.workflow_run_id
            WHERE rs.id = $1
            """,
            workflow_run_step_id,
        )
        if step is None:
            raise WorkflowEngineError(f"workflow_run_step {workflow_run_step_id} not found")
        if step["status"] != "active":
            raise WorkflowEngineError(
                f"step is not awaiting completion (status={step['status']})"
            )

        # Application-level maker-checker guard (do not rely on the DB alone to
        # communicate the problem).
        if step["proposed_by"] is not None and completed_by == step["proposed_by"]:
            raise MakerCheckerError(
                "maker-checker violation: the approver of a Tier-1 User Task must "
                "differ from the member who proposed it "
                f"(proposed_by == completed_by == {completed_by})"
            )

        org_id = step["org_id"]
        run_id = step["workflow_run_id"]

        try:
            # Resume the paused SpiffWorkflow and run the User Task forward.
            workflow = deserialize_state(step["spiff_serialized_state"])
            task = _ready_user_task(workflow, step["step_key"])
            if task is None:
                raise WorkflowEngineError(
                    f"no ready User Task '{step['step_key']}' in serialized state"
                )
            task.set_data(user_task_result=result, completed_by=str(completed_by))
            task.run()

            # Continue past any downstream Service Tasks.
            steps = await _load_steps(conn, step["workflow_version_id"], org_id)
            step_by_key = {s["step_key"]: dict(s) for s in steps}
            global _SERVICE_STEP_MAP, _SERVICE_CONTEXT
            _SERVICE_STEP_MAP = {
                k: v for k, v in step_by_key.items() if v["step_type"] == "service"
            }
            # A Service Task downstream of a User Task is still attributable to
            # the member who STARTED the run, not to whoever approved the task.
            _SERVICE_CONTEXT = {
                "pool": pool, "org_id": org_id, "actor_id": step["started_by"],
            }
            try:
                executed = await _drive(workflow)
            finally:
                _SERVICE_STEP_MAP = {}
                _SERVICE_CONTEXT = {}

            async with conn.transaction():
                # Complete this User Task step — the DB CHECK also guards
                # approved_by != proposed_by, so surface a clear error if it fires.
                try:
                    await conn.execute(
                        """
                        UPDATE workflow_run_steps
                        SET status = 'completed', approved_by = $2, completed_at = now(),
                            result = $3::jsonb
                        WHERE id = $1
                        """,
                        workflow_run_step_id, completed_by, json.dumps(result),
                    )
                except asyncpg.CheckViolationError as exc:
                    raise MakerCheckerError(
                        "maker-checker violation rejected by database CHECK constraint: "
                        "approver must differ from proposer"
                    ) from exc

                # Completing the User Task marks its member_todos entry done.
                await workflow_todos.complete_user_task_todos(
                    conn, run_step_id=workflow_run_step_id
                )

                # Mark any downstream Service Tasks completed.
                for step_key in executed:
                    await conn.execute(
                        """
                        UPDATE workflow_run_steps rs
                        SET status = 'completed', started_at = now(), completed_at = now(),
                            result = $3::jsonb
                        FROM workflow_steps ws
                        WHERE rs.workflow_step_id = ws.id
                          AND rs.workflow_run_id = $1 AND ws.step_key = $2
                        """,
                        run_id, step_key,
                        json.dumps(_service_result_for(workflow, step_key)),
                    )

                # New pause point or completion.
                ready = _ready_user_task(workflow)
                if ready is not None:
                    new_rsid = await conn.fetchval(
                        """
                        UPDATE workflow_run_steps rs
                        SET status = 'active', started_at = now()
                        FROM workflow_steps ws
                        WHERE rs.workflow_step_id = ws.id
                          AND rs.workflow_run_id = $1 AND ws.step_key = $2
                          AND rs.status = 'pending'
                        RETURNING rs.id
                        """,
                        run_id, ready.task_spec.bpmn_id,
                    )
                    await conn.execute(
                        """
                        UPDATE workflow_runs SET status = 'running',
                            spiff_serialized_state = $2::jsonb
                        WHERE id = $1
                        """,
                        run_id, serialize_state(workflow),
                    )
                    if new_rsid is not None:
                        next_step = step_by_key.get(ready.task_spec.bpmn_id, {})
                        await workflow_todos.sync_user_task_todos(
                            conn,
                            org_id=org_id,
                            run_step_id=new_rsid,
                            step_key=ready.task_spec.bpmn_id,
                            display_name=next_step.get("display_name"),
                            assigned_role_profile_id=next_step.get("assigned_role_profile_id"),
                        )
                    run_status = "running"
                else:
                    await conn.execute(
                        """
                        UPDATE workflow_runs
                        SET status = 'completed', completed_at = now(),
                            spiff_serialized_state = $2::jsonb
                        WHERE id = $1
                        """,
                        run_id, serialize_state(workflow),
                    )
                    run_status = "completed"
        except MakerCheckerError:
            # A rejected approval is a validation outcome, not a run failure —
            # leave the run/step untouched (still active) and surface the error.
            raise
        except Exception as exc:
            # Any real execution failure holds the run and alerts (Wave-4 style).
            await _hold_run(conn, run_id, org_id, step["started_by"], exc)
            raise

        return {
            "run_id": run_id,
            "run_status": run_status,
            "completed_step": step["step_key"],
            "executed_service_steps": executed,
            "is_completed": workflow.is_completed(),
        }
