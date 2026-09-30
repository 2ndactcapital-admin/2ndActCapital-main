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
from services import agent_proposals, workflow_todos

# SpiffWorkflow task-spec class names that map to our governed workflow_steps.
_SERVICE_CLS = "ServiceTask"
_USER_CLS = "UserTask"

# ── Verb-tier gating (tiergating.structural) ────────────────────────────────
#
# THE GAP (docs/WORKFLOW_WAVE2_DISCOVERY.md Task 5): the engine fetched
# workflow_steps.autonomy_tier but never branched on it for a Service Task —
# a Tier-1 verb invoked from a Service Task executed unattended, the instant
# the engine reached it, exactly like a Tier-3 verb.
#
# TIER 1 IS THE DANGEROUS END, NOT THE SAFE ONE — same convention as
# AssistantAction.tier (services/action_registry.py): 1 = highest stakes
# (moves money, creates an obligation, produces a third-party artifact, or
# mutates ownership/economic terms/a posted ledger line), 3 = lowest. Reading
# "tier 1" as permissive-low is backwards and will let an unattended run
# execute the most dangerous class of verb.
#
# EFFECTIVE TIER IS MOST-RESTRICTIVE-WINS: min(registry tier, diagram tier).
# Because 1 is the dangerous end, min() picks the MORE restrictive of the two
# — an author may UPGRADE a Tier-3 verb by marking its diagram element Tier 1
# (it will suspend even though the verb itself is low-stakes), but can never
# DOWNGRADE a Tier-1 verb by marking its diagram element Tier 3 (it still
# suspends). Same rule CLAUDE.md documents for dual-path permission
# resolution — do not invent a second one.
SUSPEND_TIER = 1


def compute_effective_tier(step: dict, action) -> int:
    """The tier that actually governs THIS Service Task at execution time.

    ``step`` is a ``workflow_steps`` row dict (has ``autonomy_tier``, the
    diagram author's choice). ``action`` is the resolved
    ``AssistantAction`` (or ``None`` if the key didn't resolve). Most-
    restrictive-wins: see the module-level note above for why this is
    ``min()``, not ``max()``, despite reading backwards at a glance.
    """
    diagram_tier = step.get("autonomy_tier")
    if diagram_tier is None:
        diagram_tier = 3
    registry_tier = getattr(action, "tier", None) if action is not None else None
    if registry_tier is None:
        # Unknown/unresolved verb: nothing to be MORE restrictive than, so
        # the diagram's own tier (already defaulted conservatively by
        # workflow_steps_deriver) is all there is to go on.
        return diagram_tier
    return min(diagram_tier, registry_tier)


# agent_proposals.agent_key this sprint writes for a suspended Service Task's
# proposal. Not one of the seven conceptual agents in services.agent_proposals
# (AGENT_KEYS) — this is the deterministic gating engine itself, proposing on
# behalf of a workflow run rather than an LLM agent. agent_key has no CHECK/FK
# (documented, not enforced — see agenticmakerchecker_substrate.sql), so this
# is additive.
TIER_GATE_AGENT_KEY = "workflow_engine"
TIER_GATE_OBJECT_TYPE = "workflow_service_task_approval"

# workflow_runs.status / workflow_run_steps.status values this sprint adds.
# Both columns are plain text with no CHECK constraint (confirmed live), so
# these are additive, same as 'running'/'completed'/'held' before them.
RUN_STATUS_AWAITING_APPROVAL = "awaiting_approval"
RUN_STATUS_REJECTED = "rejected"
STEP_STATUS_SUSPENDED = "suspended"
STEP_STATUS_REJECTED = "rejected"


class MakerCheckerError(Exception):
    """Raised when an approver tries to approve their own proposed step."""


class WorkflowEngineError(Exception):
    """Raised for structural problems executing a run (bad XML, missing step)."""


class ScriptTaskRefusedError(WorkflowEngineError):
    """Raised when BPMN XML being parsed contains a ``bpmn:scriptTask``.

    scripttaskrefusal.structural: the real, primary refusal lives at
    validation (``services.workflow_nl_generator._validate``, shared by both
    writers of ``workflow_versions.bpmn_xml``) — nothing containing a Script
    Task is ever stored. This is defense in depth for any future or existing
    code path that calls ``parse_bpmn`` on BPMN XML that bypassed that
    validation (e.g. a row written directly to the database)."""


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


class _ScriptTaskParser(TaskParser):
    """Refuse ``bpmn:scriptTask`` at parse time — same mechanism as
    ``_BusinessRuleTaskParser`` above, opposite outcome.

    SpiffWorkflow's base parser maps ``scriptTask`` to its own ``ScriptTask``
    spec, which executes the element's body with an unsandboxed
    ``PythonScriptEngine`` (bare ``eval``/``exec``) — arbitrary in-process code
    execution with this application's own database credentials, outside the
    action registry, outside every permission check, outside audit, and
    outside the custody cliff. Loud refusal, never a sandboxed execution.
    """

    def create_task(self):
        raise ScriptTaskRefusedError(
            f"Script Task {self.bpmn_id!r} is not permitted: script tasks run "
            "arbitrary Python in-process, bypassing the action registry, "
            "permission checks, and audit log. Model this step as a Service "
            "Task calling a registered action instead."
        )


def _make_bpmn_parser() -> BpmnParser:
    """A BpmnParser that additionally accepts ``bpmn:businessRuleTask`` (DMN-less)
    and REFUSES ``bpmn:scriptTask`` outright.

    Additive to Phase 1: existing service/user/send/gateway XML is unaffected.
    """
    parser = BpmnParser()
    parser.OVERRIDE_PARSER_CLASSES = {
        **parser.OVERRIDE_PARSER_CLASSES,
        full_tag("businessRuleTask"): (_BusinessRuleTaskParser, NoneTask),
        full_tag("scriptTask"): (_ScriptTaskParser, NoneTask),
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


def _started_service_task(workflow: BpmnWorkflow, step_key: str):
    for t in workflow.get_tasks(state=TaskState.STARTED):
        if t.task_spec.__class__.__name__ == _SERVICE_CLS and t.task_spec.bpmn_id == step_key:
            return t
    return None


async def _drive(workflow: BpmnWorkflow) -> tuple[list[str], str | None]:
    """Advance the workflow, auto-executing Service Tasks, until it pauses at a
    User Task, SUSPENDS at a Tier-1 Service Task, or completes.

    A base ``bpmn:serviceTask`` parks in the STARTED state ("external service
    running") rather than auto-completing — that is exactly our hook point.

    Returns ``(executed, suspended_step_key)``: the list of Service Task
    ``bpmn_id``s that actually ran during this drive, and — if a Service Task
    whose EFFECTIVE tier is ``SUSPEND_TIER`` was reached — that task's
    ``bpmn_id``, LEFT UNEXECUTED (still parked STARTED in ``workflow``, never
    completed). ``suspended_step_key`` is ``None`` when the drive instead
    paused at a User Task or ran to completion. The caller is responsible for
    recording the suspension and NOT advancing the workflow further until an
    approval resumes it.
    """
    executed: list[str] = []
    while True:
        workflow.do_engine_steps()
        started = [
            t for t in workflow.get_tasks(state=TaskState.STARTED)
            if t.task_spec.__class__.__name__ == _SERVICE_CLS
        ]
        if not started:
            return executed, None
        for task in started:
            step_key = task.task_spec.bpmn_id
            step = _SERVICE_STEP_MAP.get(step_key, {})
            action_key = step.get("action_registry_key")
            resolved = REGISTRY.get(action_key) if action_key else None
            # Suspension only matters for an action that would actually run a
            # handler — an unresolved key, or one that never opted into
            # workflow_invocable, has no side effect to gate either way (see
            # _execute_service_task), so gating it would add friction with
            # nothing real to approve.
            if (
                getattr(resolved, "workflow_invocable", False)
                and compute_effective_tier(step, resolved) == SUSPEND_TIER
            ):
                return executed, step_key
            result = await _execute_service_task(step_key)
            task.set_data(service_result=result)
            task.complete()
            executed.append(step_key)


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

    # ``run_context``/``run_id`` are passed so an event-driven action can tell
    # WHICH event started it. Without them a Service Task on an event-triggered
    # run knows only its own org — it cannot reach the domain_events row that
    # publish_event put into ``workflow_runs.context``, and every event would
    # look identical to it. Every existing handler already absorbs unexpected
    # keyword arguments (``**_``), so this is additive.
    handler_result = await resolved.handler(
        pool=pool,
        user_id=actor_id,
        org_id=_SERVICE_CONTEXT.get("org_id"),
        run_context=_SERVICE_CONTEXT.get("run_context") or {},
        workflow_run_id=_SERVICE_CONTEXT.get("run_id"),
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


def _independent_acquire(pool):
    """Acquire a connection that is NOT enlisted in any caller's transaction.

    *** THIS EXISTS BECAUSE OF A REAL, MEASURED BUG. ***

    ``start_workflow_run`` documents — correctly — that the run row must be
    persisted "in their own committed transaction, BEFORE driving the engine",
    so a later failure is recordable as 'held' "rather than vanishing on
    rollback". That intent was defeated by the application's own pool.

    ``services.database._RLSPool.acquire()`` opens an OUTER transaction before
    yielding the connection (that is how the RLS ``SET LOCAL`` GUCs are made
    transaction-scoped). asyncpg nests any ``conn.transaction()`` opened inside
    it as a SAVEPOINT — so the engine's "own committed transaction" is a
    savepoint release, not a commit. When ``start_workflow_run`` re-raises after
    holding, the exception escapes ``pool.acquire()``, the OUTER transaction
    rolls back, and the workflow_runs row, its error_detail, and every
    ``create_held_run_alerts`` todo all vanish together. The failure leaves no
    trace whatsoever.

    Measured, not inferred: with a raw ``asyncpg.create_pool`` an inner
    "committed" write survives an exception escaping ``acquire()``; with the
    ``_RLSPool`` the same write is gone. Every existing verify script built a
    RAW pool, which is exactly why this never showed up — the deployed
    event-trigger path (``chancery_workflow_bridge``) passes the real RLS pool
    and has always been exposed to it.

    Taking the connection from the underlying pool sidesteps the wrapper's
    transaction; the RLS GUCs are re-applied by the caller inside its own
    transaction so the write still runs with the correct org context.

    CONSTRAINT: this holds a SECOND pooled connection while the caller still
    holds its first, so ``start_workflow_run`` needs a pool of at least 2. The
    application pool is ``max_size=10``; the explicit ``timeout`` makes an
    exhausted pool fail loudly in seconds instead of hanging forever, which is
    the failure mode that would otherwise look like a stuck workflow.
    """
    inner = getattr(pool, "_pool", None)
    return (inner if inner is not None else pool).acquire(timeout=30)


async def _apply_rls(conn) -> None:
    """Re-apply the RLS context on an independently-acquired connection.

    ``_RLSPool`` normally does this at acquire time; a connection taken from the
    underlying pool has no context, which under a non-bypass role would
    default-deny the write. Imported locally — ``services.database`` is a
    request-layer module and importing it at engine module scope invites a
    cycle."""
    from services.database import _apply_rls_settings

    await _apply_rls_settings(conn)


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

        # Persist the run + its pending steps up-front, in their own GENUINELY
        # committed transaction, BEFORE driving the engine. A later failure must
        # be recordable as 'held' (Wave-4-style HOLD + ALERT) rather than
        # vanishing on rollback or leaving the run silently stuck in 'running'.
        #
        # This runs on an INDEPENDENT connection, not on `conn`. See
        # _independent_acquire: `conn` may already be inside a caller-owned
        # transaction (the real RLS pool always is), which would demote this
        # block to a savepoint and let the later re-raise erase the whole run.
        async with _independent_acquire(pool) as persist_conn:
            async with persist_conn.transaction():
                await _apply_rls(persist_conn)
                run_id = await persist_conn.fetchval(
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
                    rsid = await persist_conn.fetchval(
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
                    "run_context": context, "run_id": run_id,
                }
                try:
                    executed, suspended_step_key = await _drive(workflow)
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

                # Suspension point, pause point, or completion.
                ready = None if suspended_step_key is not None else _ready_user_task(workflow)
                if suspended_step_key is not None:
                    await _suspend_step(
                        conn,
                        run_id=run_id,
                        org_id=org_id,
                        maker=started_by,
                        step_key=suspended_step_key,
                        step_row=step_by_key.get(suspended_step_key, {}),
                        run_step_id=run_step_ids.get(suspended_step_key),
                        workflow=workflow,
                    )
                    status = RUN_STATUS_AWAITING_APPROVAL
                elif ready is not None:
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
            # for a manually-triggered run. Recorded on an INDEPENDENT
            # connection, not on `conn`: the execution transaction above has
            # rolled back, and `conn` itself may be inside a caller-owned
            # transaction that the re-raise below is about to roll back too,
            # which would erase the hold and its alerts along with it.
            async with _independent_acquire(pool) as hold_conn:
                # The RLS GUCs are SET LOCAL, so they must be applied INSIDE a
                # transaction or they are discarded by the implicit
                # single-statement commit before the write ever runs.
                async with hold_conn.transaction():
                    await _apply_rls(hold_conn)
                    await _hold_run(hold_conn, run_id, org_id, started_by, exc)
            raise

        return {
            "run_id": run_id,
            "status": status,
            "executed_service_steps": executed,
            "paused_at": ready.task_spec.bpmn_id if ready is not None else None,
            "suspended_at": suspended_step_key,
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


async def _run_step_id_for(conn, run_id, step_key: str):
    """Look up a ``workflow_run_steps.id`` by ``(run_id, step_key)``.

    Only needed by callers resuming mid-run (``complete_user_task``,
    ``resolve_tier_approval``), which don't have the start-time
    ``run_step_ids`` dict in scope — that dict is built once, inside
    ``start_workflow_run``, and never persisted anywhere else."""
    return await conn.fetchval(
        """
        SELECT rs.id
        FROM workflow_run_steps rs
        JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
        WHERE rs.workflow_run_id = $1 AND ws.step_key = $2
        """,
        run_id, step_key,
    )


async def _suspend_step(
    conn, *, run_id, org_id, maker, step_key: str, step_row: dict, run_step_id,
    workflow: BpmnWorkflow,
) -> None:
    """Record a Tier-1 Service Task's suspension.

    Writes, in the caller's transaction: the ``agent_proposals`` row that
    gates this step (maker-checker eligibility and disclosed self-approval
    are ``services.agent_proposals``'s job, not reimplemented here), the
    ``workflow_run_steps`` row (``status='suspended'``, ``proposed_by`` =
    the maker, linked to the proposal), the ``workflow_runs`` row
    (``status='awaiting_approval'``, state serialized so the run can be
    resumed later), and the reviewer alert.

    ``maker`` is ALWAYS ``workflow_runs.started_by`` — for a manual run, the
    member who started it; for a scheduled run, the trigger's own
    ``created_by`` (``workflow_scheduler._fire`` already passes this as
    ``started_by`` — see docs/WORKFLOW_WAVE2_DISCOVERY.md Task 1c). A run
    with no resolvable maker cannot generate a Tier-1 proposal at all — a
    NULL maker is never treated as "anyone may approve".
    """
    if maker is None:
        raise WorkflowEngineError(
            f"Service Task {step_key!r} requires Tier-1 approval, but this "
            "run has no started_by member to record as the proposal's "
            "maker — a NULL maker is never treated as 'anyone may approve'"
        )
    action = REGISTRY.get(step_row.get("action_registry_key"))
    effective_tier = compute_effective_tier(step_row, action)
    payload = {
        "workflow_run_id": str(run_id),
        "workflow_run_step_id": str(run_step_id),
        "step_key": step_key,
        "action_registry_key": step_row.get("action_registry_key"),
        "display_name": step_row.get("display_name"),
        "diagram_tier": step_row.get("autonomy_tier"),
        "registry_tier": getattr(action, "tier", None),
        "effective_tier": effective_tier,
    }
    proposal_id = await agent_proposals.create_proposal(
        conn, org_id,
        agent_key=TIER_GATE_AGENT_KEY,
        object_type=TIER_GATE_OBJECT_TYPE,
        payload=payload,
        proposed_by=maker,
    )
    await conn.execute(
        """
        UPDATE workflow_run_steps
        SET status = $2, proposed_by = $3, agent_proposal_id = $4::uuid,
            started_at = now()
        WHERE id = $1
        """,
        run_step_id, STEP_STATUS_SUSPENDED, maker, proposal_id,
    )
    await conn.execute(
        """
        UPDATE workflow_runs
        SET status = $2, spiff_serialized_state = $3::jsonb
        WHERE id = $1
        """,
        run_id, RUN_STATUS_AWAITING_APPROVAL, serialize_state(workflow),
    )
    await workflow_todos.create_tier_approval_alerts(
        conn,
        org_id=org_id,
        run_step_id=run_step_id,
        step_key=step_key,
        display_name=step_row.get("display_name"),
    )


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
                   r.workflow_version_id, r.spiff_serialized_state, r.started_by,
                   r.context
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
            # The run's own context travels here too, for the same reason as on
            # the start path: a Service Task downstream of a User Task on an
            # event-triggered run still needs to know which event started it.
            # ``workflow_runs.context`` comes back as jsonb; on these pools no
            # json codec is registered, so it may arrive as text.
            _resume_context = step["context"]
            if isinstance(_resume_context, (str, bytes)):
                try:
                    _resume_context = json.loads(_resume_context)
                except (ValueError, TypeError):
                    _resume_context = {}
            _SERVICE_CONTEXT = {
                "pool": pool, "org_id": org_id, "actor_id": step["started_by"],
                "run_context": _resume_context or {}, "run_id": run_id,
            }
            try:
                executed, suspended_step_key = await _drive(workflow)
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

                # New suspension point, pause point, or completion.
                ready = None if suspended_step_key is not None else _ready_user_task(workflow)
                if suspended_step_key is not None:
                    new_rsid = await _run_step_id_for(conn, run_id, suspended_step_key)
                    await _suspend_step(
                        conn,
                        run_id=run_id,
                        org_id=org_id,
                        maker=step["started_by"],
                        step_key=suspended_step_key,
                        step_row=step_by_key.get(suspended_step_key, {}),
                        run_step_id=new_rsid,
                        workflow=workflow,
                    )
                    run_status = RUN_STATUS_AWAITING_APPROVAL
                elif ready is not None:
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
            "suspended_at": suspended_step_key,
            "is_completed": workflow.is_completed(),
        }


async def resolve_tier_approval(
    pool, workflow_run_step_id, *, reviewed_by, decision,
    review_notes: str | None = None, self_approval_reason: str | None = None,
) -> dict:
    """Approve or reject a SUSPENDED Tier-1 Service Task, and resume the run.

    ``decision`` is ``services.agent_proposals.STATUS_APPROVED`` or
    ``STATUS_REJECTED``. Eligibility — holds ``review_agent_proposals`` and is
    not the maker, OR disclosed self-approval under a genuinely empty checker
    set — is entirely ``agent_proposals.review_proposal``'s job.
    ``MakerCheckerError`` / ``NotEligibleError`` / ``SelfApprovalReasonRequiredError``
    all propagate unchanged; this function does not re-implement or relax any
    of them. Rejecting a self-proposed step under a genuinely empty checker
    set requires the same disclosure a self-APPROVAL would — that is
    ``review_proposal``'s existing behavior, not something added here.

    APPROVAL executes the verb EXACTLY ONCE: ``_execute_service_task`` is
    called directly, precisely once, never through ``_drive``'s own loop
    (which would recompute the effective tier and suspend on the very same
    task again). The run then resumes driving, which may hit ANOTHER Tier-1
    suspension downstream (handled recursively, the same way), a User Task
    pause, or completion.

    REJECTION means the verb NEVER executes. The run ends in the terminal
    ``rejected`` state; SpiffWorkflow's serialized state is left exactly as
    it was suspended (there is no "skip and continue" semantics for a
    rejected Tier-1 step — like a held run, it does not silently resume from
    a different point).
    """
    async with pool.acquire() as conn:
        step = await conn.fetchrow(
            """
            SELECT rs.id, rs.workflow_run_id, rs.org_id, rs.status,
                   rs.agent_proposal_id,
                   ws.step_key, ws.autonomy_tier, ws.action_registry_key,
                   ws.display_name,
                   r.workflow_version_id, r.spiff_serialized_state, r.started_by,
                   r.context
            FROM workflow_run_steps rs
            JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
            JOIN workflow_runs r ON r.id = rs.workflow_run_id
            WHERE rs.id = $1
            """,
            workflow_run_step_id,
        )
        if step is None:
            raise WorkflowEngineError(f"workflow_run_step {workflow_run_step_id} not found")
        if step["status"] != STEP_STATUS_SUSPENDED:
            raise WorkflowEngineError(
                f"step is not awaiting Tier-1 approval (status={step['status']})"
            )
        if step["agent_proposal_id"] is None:
            raise WorkflowEngineError(
                f"workflow_run_step {workflow_run_step_id} is suspended but has "
                "no linked agent_proposals row"
            )

        org_id = step["org_id"]
        run_id = step["workflow_run_id"]

        proposal = await agent_proposals.review_proposal(
            pool, conn, org_id, str(step["agent_proposal_id"]),
            reviewed_by=reviewed_by, decision=decision,
            review_notes=review_notes, self_approval_reason=self_approval_reason,
        )

        if decision == agent_proposals.STATUS_REJECTED:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE workflow_run_steps
                    SET status = $2, approved_by = $3, completed_at = now()
                    WHERE id = $1
                    """,
                    workflow_run_step_id, STEP_STATUS_REJECTED, reviewed_by,
                )
                await conn.execute(
                    """
                    UPDATE workflow_runs
                    SET status = $2, completed_at = now()
                    WHERE id = $1
                    """,
                    run_id, RUN_STATUS_REJECTED,
                )
                await workflow_todos.complete_tier_approval_todos(
                    conn, run_step_id=workflow_run_step_id
                )
            return {
                "run_id": run_id,
                "run_status": RUN_STATUS_REJECTED,
                "step": step["step_key"],
                "executed": False,
                "proposal": proposal,
            }

        # APPROVED — execute the verb exactly once, then resume driving.
        try:
            workflow = deserialize_state(step["spiff_serialized_state"])
            task = _started_service_task(workflow, step["step_key"])
            if task is None:
                raise WorkflowEngineError(
                    f"no STARTED Service Task '{step['step_key']}' in "
                    "serialized state — was this step already resumed?"
                )

            steps = await _load_steps(conn, step["workflow_version_id"], org_id)
            step_by_key = {s["step_key"]: dict(s) for s in steps}
            global _SERVICE_STEP_MAP, _SERVICE_CONTEXT
            _SERVICE_STEP_MAP = {
                k: v for k, v in step_by_key.items() if v["step_type"] == "service"
            }
            _resume_context = step["context"]
            if isinstance(_resume_context, (str, bytes)):
                try:
                    _resume_context = json.loads(_resume_context)
                except (ValueError, TypeError):
                    _resume_context = {}
            _SERVICE_CONTEXT = {
                "pool": pool, "org_id": org_id, "actor_id": step["started_by"],
                "run_context": _resume_context or {}, "run_id": run_id,
            }
            try:
                result = await _execute_service_task(step["step_key"])
                task.set_data(service_result=result)
                task.complete()
                executed = [step["step_key"]]
                more_executed, suspended_step_key = await _drive(workflow)
                executed += more_executed
            finally:
                _SERVICE_STEP_MAP = {}
                _SERVICE_CONTEXT = {}

            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE workflow_run_steps
                    SET status = 'completed', approved_by = $2, completed_at = now(),
                        result = $3::jsonb
                    WHERE id = $1
                    """,
                    workflow_run_step_id, reviewed_by,
                    json.dumps(_service_result_for(workflow, step["step_key"])),
                )
                await workflow_todos.complete_tier_approval_todos(
                    conn, run_step_id=workflow_run_step_id
                )

                for step_key in more_executed:
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

                ready = None if suspended_step_key is not None else _ready_user_task(workflow)
                if suspended_step_key is not None:
                    new_rsid = await _run_step_id_for(conn, run_id, suspended_step_key)
                    await _suspend_step(
                        conn,
                        run_id=run_id,
                        org_id=org_id,
                        maker=step["started_by"],
                        step_key=suspended_step_key,
                        step_row=step_by_key.get(suspended_step_key, {}),
                        run_step_id=new_rsid,
                        workflow=workflow,
                    )
                    run_status = RUN_STATUS_AWAITING_APPROVAL
                elif ready is not None:
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
        except Exception as exc:
            await _hold_run(conn, run_id, org_id, step["started_by"], exc)
            raise

        return {
            "run_id": run_id,
            "run_status": run_status,
            "step": step["step_key"],
            "executed_service_steps": executed,
            "suspended_at": suspended_step_key,
            "is_completed": workflow.is_completed(),
            "proposal": proposal,
        }
