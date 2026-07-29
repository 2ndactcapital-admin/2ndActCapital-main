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
    vendor-neutral.  Phase 1 *resolves* the action against the Sprint-11
    registry to prove the key is real; actually invoking action handlers is
    a later phase.
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


def _drive(workflow: BpmnWorkflow):
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
            result = _execute_service_task(step_key)
            task.set_data(service_result=result)
            task.complete()
            executed.append(step_key)
    return executed


def _execute_service_task(step_key: str) -> dict:
    """Resolve (and, in a later phase, invoke) the action for a Service Task.

    Phase 1: resolve ``action_registry_key`` against the Sprint-11 registry so
    we prove the key is real and record it in the run-step audit trail.  We do
    NOT run the underlying handler yet (that needs full request context and is
    out of scope for the foundation).
    """
    # step_row is looked up by the caller and passed via _SERVICE_STEP_MAP.
    step = _SERVICE_STEP_MAP.get(step_key, {})
    action_key = step.get("action_registry_key")
    resolved = REGISTRY.get(action_key) if action_key else None
    return {
        "action_registry_key": action_key,
        "resolved": resolved is not None,
        "access_type": getattr(resolved, "access_type", None),
        "executed_at": _now().isoformat(),
    }


# Per-run map from service step_key -> workflow_steps row (set by callers before
# driving; kept module-local so _execute_service_task stays a pure hook).
_SERVICE_STEP_MAP: dict[str, dict] = {}


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

            # Drive the engine: Service Tasks execute automatically.
            global _SERVICE_STEP_MAP
            _SERVICE_STEP_MAP = {
                k: v for k, v in step_by_key.items() if v["step_type"] == "service"
            }
            executed = _drive(workflow)
            _SERVICE_STEP_MAP = {}

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
                # Activate the pausing User Task; started_by "proposes" it so a
                # different approver is required by maker-checker.
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

        return {
            "run_id": run_id,
            "status": status,
            "executed_service_steps": executed,
            "paused_at": ready.task_spec.bpmn_id if ready is not None else None,
        }


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
                   r.workflow_version_id, r.spiff_serialized_state
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
        global _SERVICE_STEP_MAP
        _SERVICE_STEP_MAP = {
            k: v for k, v in step_by_key.items() if v["step_type"] == "service"
        }
        executed = _drive(workflow)
        _SERVICE_STEP_MAP = {}

        async with conn.transaction():
            # Complete this User Task step — the DB CHECK also guards approved_by
            # != proposed_by, so surface a clear error if it ever fires.
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
                await conn.execute(
                    """
                    UPDATE workflow_run_steps rs
                    SET status = 'active', started_at = now()
                    FROM workflow_steps ws
                    WHERE rs.workflow_step_id = ws.id
                      AND rs.workflow_run_id = $1 AND ws.step_key = $2
                      AND rs.status = 'pending'
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

        return {
            "run_id": run_id,
            "run_status": run_status,
            "completed_step": step["step_key"],
            "executed_service_steps": executed,
            "is_completed": workflow.is_completed(),
        }
