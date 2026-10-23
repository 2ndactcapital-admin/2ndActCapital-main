"""Governed human tasks — ONE reusable path for "a person must act / approve" (rep2).

Built on the workflow engine, not beside it. rep1 found the engine opened User
Tasks only from a published BPMN version and fell back to member_todos +
domain_events of its own; this module is the proper path every later module
uses instead (rep3 moves rep1's terms approvals onto it).

HOW A TASK IS REPRESENTED — THE ENGINE'S OWN TABLES, NO SIDE TABLE
──────────────────────────────────────────────────────────────────────────────
* One SYSTEM workflow definition per (org, eligible permission), named
  ``system:human_task:<permission>``, created idempotently on first use
  (advisory-locked), version 1, one BPMN User Task ``human_task`` whose
  ``workflow_steps.assigned_permission`` is that permission (the additive engine
  column rep2 added — NULL on every pre-existing step).
* A task IS a run of that definition: ``workflow_runs.context.human_task``
  records the kind, the subject (type + id), the maker, the title, the payload
  and the screen to act from; ``workflow_runs.started_by`` is the maker (NULL
  for a non-maker-checker "action" task) so the engine's own
  ``proposed_by``/``approved_by`` CHECK enforces maker != checker.
* The task's id is its ``workflow_run_steps.id``. The decision lands in
  ``workflow_run_steps.result`` (``{"outcome", "note", "completed_by"}``) and
  ``approved_by``; the run completes. History is read back from those tables.
* Notification: the engine opens one ``member_todos`` row per admitted holder
  of the permission EXCEPT the maker (workflow_todos.sync_user_task_todos'
  rep2 permission leg); this module then retitles those rows and points them at
  the subject's screen. Completing closes them (the engine does it).
* Every open / complete / cancel writes an ``audit_log`` row and, after commit,
  a ``domain_events`` row (``human_task.opened`` / ``.completed`` /
  ``.cancelled``, source_type ``workflow_run_step``).

TIER: these tasks are approvals of HUMAN-initiated work. No agent can open or
complete one — the actor must be an admitted ``users`` row acting as a user
(:func:`assert_human`). Nothing here sits anywhere near the custody cliff by
itself; a module whose subject is a trade (execution instructions) is still
only recording what a person decided.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from services import workflow_engine as E
from services.workflow_todos import TODO_SOURCE_USER_TASK

DEFINITION_PREFIX = "system:human_task:"
STEP_KEY = "human_task"
SUBJECT_SOURCE_TYPE = "workflow_run_step"

BPMN_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" id="D_system_human_task" '
    'targetNamespace="https://hollisworks.com/bpmn/system">'
    '<bpmn:process id="system_human_task" isExecutable="true">'
    '<bpmn:startEvent id="start"><bpmn:outgoing>f1</bpmn:outgoing></bpmn:startEvent>'
    '<bpmn:userTask id="human_task" name="Human task"><bpmn:incoming>f1</bpmn:incoming>'
    '<bpmn:outgoing>f2</bpmn:outgoing></bpmn:userTask>'
    '<bpmn:endEvent id="end"><bpmn:incoming>f2</bpmn:incoming></bpmn:endEvent>'
    '<bpmn:sequenceFlow id="f1" sourceRef="start" targetRef="human_task"/>'
    '<bpmn:sequenceFlow id="f2" sourceRef="human_task" targetRef="end"/>'
    '</bpmn:process></bpmn:definitions>'
)

STATUS_LABELS = {"open": "Open", "completed": "Completed", "cancelled": "Cancelled", "held": "Held"}
OUTCOME_LABELS = {"approve": "Approved", "reject": "Rejected", "done": "Done"}


class HumanTaskError(Exception):
    """A structural problem (unknown kind, wrong state, altered definition)."""


class HumanTaskNotFound(HumanTaskError):
    pass


class HumanTaskConflict(HumanTaskError):
    pass


class HumanTaskPermissionError(HumanTaskError):
    """The actor does not hold the task's eligible permission."""


class AgentActorRefused(HumanTaskError):
    """An agent (or anything that is not an admitted user) tried to act."""


class MakerCheckerError(HumanTaskError):
    """The maker tried to decide their own task."""


# ── Kinds ───────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TaskKind:
    key: str
    label: str
    outcomes: tuple[str, ...]
    maker_checker: bool
    #: ``await on_complete(pool, task, outcome, actor_id, note)`` — runs after
    #: the engine has completed the user task. Must be idempotent: if it fails
    #: after the engine committed, the subject module re-applies the recorded
    #: outcome (see services.execution.sync_task_outcome).
    on_complete: Callable[..., Awaitable[Any]] | None = None
    #: rep3 (additive): a kind the platform itself may OPEN — a review of work
    #: a system import produced (a library subscription's new version, a
    #: composite following a child). Never maker-checker (there is no human
    #: maker to exclude); never completable by anything but a person.
    system_open: bool = False


_KINDS: dict[str, TaskKind] = {}


def register_kind(key: str, *, label: str, outcomes: tuple[str, ...], maker_checker: bool,
                  on_complete=None, system_open: bool = False) -> TaskKind:
    if not outcomes or any(o not in OUTCOME_LABELS for o in outcomes):
        raise HumanTaskError(f"task kind {key!r}: outcomes must be from {sorted(OUTCOME_LABELS)}")
    if maker_checker and "done" in outcomes:
        raise HumanTaskError(f"task kind {key!r}: a maker-checker kind decides approve/reject, not done")
    if maker_checker and system_open:
        raise HumanTaskError(f"task kind {key!r}: a system-opened kind has no maker, so it cannot be maker-checker")
    kind = TaskKind(key, label, tuple(outcomes), maker_checker, on_complete, system_open)
    existing = _KINDS.get(key)
    if existing is not None and (existing.outcomes, existing.maker_checker, existing.system_open) != (
            kind.outcomes, kind.maker_checker, kind.system_open):
        raise HumanTaskError(f"task kind {key!r} is already registered differently")
    _KINDS[key] = kind
    return kind


def get_kind(key: str) -> TaskKind:
    try:
        return _KINDS[key]
    except KeyError:
        raise HumanTaskError(f"unknown human task kind {key!r} (registered: {sorted(_KINDS)})") from None


def kinds() -> dict[str, TaskKind]:
    return dict(_KINDS)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _loads(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _uuid(v, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except (TypeError, ValueError):
        raise HumanTaskNotFound(f"{what} not found") from None


async def assert_human(conn, actor_id, org_id, actor_kind: str = "user") -> dict:
    """The actor must be an admitted person. Refuses an agent outright.

    An agent in this codebase is identified by an agent key, never by a
    ``users`` row, and acts through agent_proposals — so ``actor_kind`` other
    than 'user', or an actor that is not a uuid of an admitted user, is
    refused with :class:`AgentActorRefused`. The user must belong to the org
    or be Super Admin (platform staff act across orgs).
    """
    if actor_kind != "user":
        raise AgentActorRefused(
            f"human tasks are approvals of human-initiated work: an agent ({actor_kind}) "
            "can neither open nor complete one")
    try:
        uid = uuid.UUID(str(actor_id))
    except (TypeError, ValueError):
        raise AgentActorRefused(
            f"human tasks are approvals of human-initiated work: {actor_id!r} is not a person") from None
    row = await conn.fetchrow("SELECT id, org_id, role, status, is_active, full_name, email FROM users "
                              "WHERE id = $1", uid)
    if row is None or not row["is_active"] or (row["status"] or "active") != "active":
        raise AgentActorRefused("human tasks are approvals of human-initiated work: the actor is not an "
                                "admitted user")
    if str(row["org_id"]) != str(org_id) and row["role"] != "super_admin":
        raise AgentActorRefused("the actor does not belong to this organization")
    return dict(row)


def _who(user: dict | None) -> str:
    if not user:
        return "someone"
    return user.get("full_name") or user.get("email") or "a colleague"


async def _audit(conn, org_id, user_id, action: str, task_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, action, SUBJECT_SOURCE_TYPE, str(task_id),
        json.dumps(payload, default=str))


async def _publish(pool, org_id, event_type: str, task_id, payload: dict, actor_id) -> dict:
    from services.domain_events import publish_event

    return await publish_event(pool, str(org_id), event_type, SUBJECT_SOURCE_TYPE, str(task_id), payload,
                               created_by=str(actor_id) if actor_id else None)


# ── The system definition ───────────────────────────────────────────────────
def definition_name(permission: str) -> str:
    return f"{DEFINITION_PREFIX}{permission}"


async def ensure_definition(conn, org_id, eligible_permission: str) -> dict:
    """``{definition_id, version_id}`` for this org + permission, created once.

    Idempotent under concurrency: a transaction-scoped advisory lock keyed on
    (org, name) serialises the first two callers, and the second one finds the
    first one's row. Validated on every call — if someone edited the system
    definition into something that is no longer a single permission-assigned
    User Task, refuse loudly rather than run a different process.
    """
    if not eligible_permission or not eligible_permission.replace("_", "").isalnum():
        raise HumanTaskError(f"invalid eligible permission {eligible_permission!r}")
    name = definition_name(eligible_permission)
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"{org_id}:{name}")
        perm = await conn.fetchval("SELECT 1 FROM permissions WHERE name = $1", eligible_permission)
        if not perm:
            raise HumanTaskError(f"permission {eligible_permission!r} does not exist")
        row = await conn.fetchrow(
            """SELECT d.id AS definition_id, v.id AS version_id, ws.step_type, ws.assigned_permission
               FROM workflow_definitions d
               JOIN workflow_versions v ON v.workflow_definition_id = d.id AND v.is_current
               LEFT JOIN workflow_steps ws ON ws.workflow_version_id = v.id AND ws.step_key = $3
               WHERE d.org_id = $1::uuid AND d.name = $2
               ORDER BY d.created_at LIMIT 1""", str(org_id), name, STEP_KEY)
        if row is not None:
            if row["step_type"] != "user" or row["assigned_permission"] != eligible_permission:
                raise HumanTaskError(
                    f"the system definition {name!r} has been altered (its current version no longer has a "
                    f"'{STEP_KEY}' user task assigned to {eligible_permission}); restore it before opening tasks")
            return {"definition_id": row["definition_id"], "version_id": row["version_id"]}
        from services.workflow_nl_generator import _validate

        errors = _validate(BPMN_XML, set(), set())
        if errors:
            raise HumanTaskError(f"system BPMN failed validation: {errors}")
        def_id = await conn.fetchval(
            "INSERT INTO workflow_definitions (org_id, name, description, is_active) "
            "VALUES ($1::uuid, $2, $3, true) RETURNING id", str(org_id), name,
            f"System — a governed human task decided by a holder of {eligible_permission}. "
            "Created by services.human_tasks; do not edit.")
        ver_id = await conn.fetchval(
            "INSERT INTO workflow_versions (workflow_definition_id, org_id, version_number, bpmn_xml, "
            "change_summary, is_current) VALUES ($1, $2::uuid, 1, $3, $4, true) RETURNING id",
            def_id, str(org_id), BPMN_XML, "rep2 system human-task definition (seeded)")
        await conn.execute(
            "INSERT INTO workflow_steps (workflow_version_id, org_id, step_key, step_type, autonomy_tier, "
            "assigned_permission, display_name) VALUES ($1, $2::uuid, $3, 'user', 1, $4, $5)",
            ver_id, str(org_id), STEP_KEY, eligible_permission, f"Decision by a holder of {eligible_permission}")
        return {"definition_id": def_id, "version_id": ver_id}


# ── Reading ─────────────────────────────────────────────────────────────────
_TASK_SQL = """
SELECT rs.id AS task_id, rs.status AS step_status, rs.proposed_by, rs.approved_by, rs.result,
       rs.started_at, rs.completed_at, r.id AS run_id, r.status AS run_status, r.context,
       r.started_by, r.started_at AS opened_at, r.error_detail, ws.assigned_permission,
       d.name AS definition_name, v.version_number, r.org_id
FROM workflow_run_steps rs
JOIN workflow_runs r ON r.id = rs.workflow_run_id
JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
JOIN workflow_versions v ON v.id = r.workflow_version_id
JOIN workflow_definitions d ON d.id = v.workflow_definition_id
WHERE r.org_id = $1::uuid AND d.name LIKE 'system:human_task:%' AND ws.step_key = 'human_task'
"""


def _status_of(step_status: str, run_status: str) -> str:
    if run_status == "held":
        return "held"
    return {"active": "open", "completed": "completed", "cancelled": "cancelled"}.get(step_status, step_status)


def _task_out(r) -> dict:
    ctx = (_loads(r["context"]) or {}).get("human_task") or {}
    result = _loads(r["result"]) or {}
    status = _status_of(r["step_status"], r["run_status"])
    return {
        "id": str(r["task_id"]), "run_id": str(r["run_id"]), "org_id": str(r["org_id"]),
        "kind": ctx.get("kind"), "title": ctx.get("title"), "subject_type": ctx.get("subject_type"),
        "subject_id": ctx.get("subject_id"), "maker_id": ctx.get("maker_id"),
        "maker_checker": bool(ctx.get("maker_checker")), "eligible_permission": r["assigned_permission"],
        "payload": ctx.get("payload") or {}, "action_path": ctx.get("action_path"),
        "status": status, "outcome": result.get("outcome") if status == "completed" else None,
        "note": result.get("note"), "completed_by": str(r["approved_by"]) if r["approved_by"] else None,
        "cancel_reason": result.get("reason") if status == "cancelled" else None,
        "cancelled_by": result.get("cancelled_by") if status == "cancelled" else None,
        "opened_at": r["opened_at"].isoformat() if r["opened_at"] else None,
        "completed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
        "engine": {"workflow_run_step_id": str(r["task_id"]), "workflow_run_id": str(r["run_id"]),
                   "definition": r["definition_name"], "version": r["version_number"],
                   "run_status": r["run_status"], "step_status": r["step_status"],
                   "proposed_by": str(r["proposed_by"]) if r["proposed_by"] else None},
    }


async def list_tasks(conn, org_id, *, status: str | None = None, kind: str | None = None,
                     subject_type: str | None = None, subject_id=None, task_id=None,
                     limit: int = 200) -> list[dict]:
    """Tasks of this org, newest first, read from the engine's run tables."""
    sql = _TASK_SQL
    args: list = [str(org_id)]
    if kind:
        args.append(kind)
        sql += f" AND r.context->'human_task'->>'kind' = ${len(args)}"
    if subject_type:
        args.append(subject_type)
        sql += f" AND r.context->'human_task'->>'subject_type' = ${len(args)}"
    if subject_id:
        args.append(str(subject_id))
        sql += f" AND r.context->'human_task'->>'subject_id' = ${len(args)}"
    if task_id:
        args.append(_uuid(task_id, "task"))
        sql += f" AND rs.id = ${len(args)}"
    if status == "open":
        sql += " AND rs.status = 'active' AND r.status <> 'held'"
    elif status == "completed":
        sql += " AND rs.status = 'completed'"
    elif status == "cancelled":
        sql += " AND rs.status = 'cancelled'"
    elif status == "held":
        sql += " AND r.status = 'held'"
    elif status:
        raise HumanTaskError(f"unknown task status {status!r}")
    args.append(max(1, min(int(limit), 1000)))
    sql += f" ORDER BY r.started_at DESC, rs.id LIMIT ${len(args)}"
    return [_task_out(r) for r in await conn.fetch(sql, *args)]


async def get_task(conn, org_id, task_id) -> dict:
    rows = await list_tasks(conn, org_id, task_id=task_id, limit=1)
    if not rows:
        raise HumanTaskNotFound("task not found")
    return rows[0]


async def task_history(conn, org_id, task_id) -> dict:
    """The engine's own record of the task: the run, every run step, and the
    audit + domain events written for it."""
    task = await get_task(conn, org_id, task_id)
    run = await conn.fetchrow("SELECT id, status, started_by, started_at, completed_at, error_detail "
                              "FROM workflow_runs WHERE id = $1::uuid AND org_id = $2::uuid",
                              task["run_id"], str(org_id))
    steps = await conn.fetch(
        "SELECT rs.id, ws.step_key, rs.status, rs.proposed_by, rs.approved_by, rs.result, rs.started_at, "
        "rs.completed_at FROM workflow_run_steps rs JOIN workflow_steps ws ON ws.id = rs.workflow_step_id "
        "WHERE rs.workflow_run_id = $1::uuid ORDER BY rs.created_at", task["run_id"])
    events = await conn.fetch("SELECT event_type, occurred_at, created_by, payload FROM domain_events "
                              "WHERE org_id = $1::uuid AND source_type = $2 AND source_id = $3::uuid "
                              "ORDER BY occurred_at", str(org_id), SUBJECT_SOURCE_TYPE, task["id"])
    audits = await conn.fetch("SELECT action, user_id, created_at FROM audit_log WHERE org_id = $1::uuid "
                              "AND resource_type = $2 AND resource_id = $3::uuid ORDER BY created_at",
                              str(org_id), SUBJECT_SOURCE_TYPE, task["id"])

    def iso(v):
        return v.isoformat() if v else None

    return {
        "task": task,
        "run": {"id": str(run["id"]), "status": run["status"], "started_by": str(run["started_by"])
                if run["started_by"] else None, "started_at": iso(run["started_at"]),
                "completed_at": iso(run["completed_at"]), "error_detail": run["error_detail"]} if run else None,
        "steps": [{"id": str(s["id"]), "step_key": s["step_key"], "status": s["status"],
                   "proposed_by": str(s["proposed_by"]) if s["proposed_by"] else None,
                   "approved_by": str(s["approved_by"]) if s["approved_by"] else None,
                   "result": _loads(s["result"]), "started_at": iso(s["started_at"]),
                   "completed_at": iso(s["completed_at"])} for s in steps],
        "events": [{"event_type": e["event_type"], "occurred_at": iso(e["occurred_at"]),
                    "created_by": str(e["created_by"]) if e["created_by"] else None} for e in events],
        "audit": [{"action": a["action"], "user_id": str(a["user_id"]) if a["user_id"] else None,
                   "created_at": iso(a["created_at"])} for a in audits],
    }


async def tasks_for_user(conn, org_id, *, user_id, permissions, is_super_admin: bool) -> dict:
    """The signed-in user's view: open tasks they may decide, and open tasks
    they opened that are waiting on someone else."""
    open_tasks = await list_tasks(conn, org_id, status="open", limit=500)
    perms = set(permissions or ())
    mine = str(user_id)
    actionable = [t for t in open_tasks if t["maker_id"] != mine
                  and (is_super_admin or t["eligible_permission"] in perms)]
    waiting = [t for t in open_tasks if t["maker_id"] == mine]
    return {"actionable": actionable, "waiting_on_others": waiting}


def can_decide(task: dict, *, user_id, permissions, is_super_admin: bool) -> bool:
    """The rule the screens use to show Approve / Reject (the server re-checks)."""
    if task.get("status") != "open":
        return False
    if task.get("maker_checker") and task.get("maker_id") == str(user_id):
        return False
    return bool(is_super_admin or task.get("eligible_permission") in set(permissions or ()))


# ── Writing ─────────────────────────────────────────────────────────────────
async def open_task(pool, *, org_id, kind: str, subject_type: str, subject_id, maker_id,
                    eligible_permission: str, title: str, payload: dict | None = None,
                    action_path: str | None = None, actor_kind: str = "user") -> dict:
    """Start a run of the system definition for this subject and notify the
    eligible holders. One open task per (kind, subject).

    rep3 (additive): ``actor_kind="system"`` with ``maker_id=None`` opens a
    task with NO maker, for a kind registered ``system_open=True`` only (a
    review of a system import). An agent is still refused, and completing a
    task is never open to anything but an admitted person."""
    k = get_kind(kind)
    if not (title or "").strip():
        raise HumanTaskError("a task needs a title")
    system = actor_kind == "system"
    if system and (not k.system_open or maker_id is not None):
        raise AgentActorRefused(f"only a system-opened kind may be opened by the platform itself, with no maker "
                                f"({kind!r} is not one)")
    async with pool.acquire() as conn:
        maker = {"id": None, "full_name": "System import"} if system else await assert_human(
            conn, maker_id, org_id, actor_kind)
        dup = await list_tasks(conn, org_id, status="open", kind=kind, subject_type=subject_type,
                               subject_id=subject_id, limit=1)
        if dup:
            raise HumanTaskConflict(f"an open {k.label.lower()} task already exists for this {subject_type}")
        d = await ensure_definition(conn, org_id, eligible_permission)
    context = {"human_task": {
        "kind": kind, "subject_type": subject_type, "subject_id": str(subject_id),
        "maker_id": str(maker["id"]) if maker["id"] else None,
        "maker_checker": k.maker_checker, "title": title, "payload": payload or {}, "action_path": action_path,
    }}
    started = await E.start_workflow_run(pool, d["version_id"], uuid.UUID(str(org_id)), context,
                                         maker["id"] if k.maker_checker else None)
    async with pool.acquire() as conn:
        async with conn.transaction():
            rsid = await conn.fetchval(
                "SELECT rs.id FROM workflow_run_steps rs JOIN workflow_steps ws ON ws.id = rs.workflow_step_id "
                "WHERE rs.workflow_run_id = $1 AND ws.step_key = $2 AND rs.status = 'active'",
                started["run_id"], STEP_KEY)
            if rsid is None:
                raise HumanTaskError(f"the engine did not open the user task (run status {started['status']})")
            notified = [str(r["user_id"]) for r in await conn.fetch(
                "UPDATE member_todos SET title = $3, detail = $4, action_key = COALESCE($5, action_key), "
                "updated_at = now() WHERE org_id = $1::uuid AND source = $2 AND related_type = 'workflow_run_step' "
                "AND related_id = $6 RETURNING user_id",
                str(org_id), TODO_SOURCE_USER_TASK, title,
                f"{k.label} — opened by {_who(maker)}" + (" (a different person must decide)" if k.maker_checker
                                                          else ""), action_path, rsid)]
            audit = {"kind": kind, "subject_type": subject_type, "subject_id": str(subject_id), "title": title,
                     "eligible_permission": eligible_permission, "notified": notified,
                     "workflow_run_id": str(started["run_id"]), "opened_by": "system" if system else "user"}
            await _audit(conn, org_id, maker["id"], "human_task.opened", rsid, audit)
            if not notified:
                await _audit(conn, org_id, maker["id"], "human_task.alert_undelivered", rsid,
                             {"reason": f"no holder of {eligible_permission} other than the maker", **audit})
            task = await get_task(conn, org_id, rsid)
    await _publish(pool, org_id, "human_task.opened", rsid,
                   {"kind": kind, "subject_type": subject_type, "subject_id": str(subject_id), "title": title,
                    "eligible_permission": eligible_permission}, maker["id"])
    task["notified"] = notified
    return task


async def complete_task(pool, *, org_id, task_id, actor_id, outcome: str, note: str | None = None,
                        actor_kind: str = "user") -> dict:
    """Decide a task through the engine's ``complete_user_task``.

    Order of refusals (each leaves the task open and unchanged): not a person
    → AgentActorRefused; unknown outcome; maker of a maker-checker task →
    MakerCheckerError; not a holder of the eligible permission →
    HumanTaskPermissionError. The engine re-checks maker-checker (and its DB
    CHECK backs it) and the permission.
    """
    async with pool.acquire() as conn:
        actor = await assert_human(conn, actor_id, org_id, actor_kind)
        task = await get_task(conn, org_id, task_id)
    if task["status"] != "open":
        raise HumanTaskConflict(f"this task is {task['status']}, not open")
    k = get_kind(task["kind"])
    if outcome not in k.outcomes:
        raise HumanTaskError(f"a {k.label.lower()} task is decided by {' or '.join(k.outcomes)}, not {outcome!r}")
    if outcome == "reject" and not (note or "").strip():
        raise HumanTaskError("a rejection needs a note saying why")
    if k.maker_checker and task["maker_id"] == str(actor["id"]):
        raise MakerCheckerError(
            f"maker-checker: you opened this task ({task['title']}), so a different holder of "
            f"{task['eligible_permission']} must {'approve' if outcome == 'approve' else 'reject'} it")
    from services.rbac import has_permission

    if not await has_permission(pool, actor["id"], org_id, task["eligible_permission"]):
        raise HumanTaskPermissionError(f"Permission required: {task['eligible_permission']}")
    result = {"outcome": outcome, "note": note, "completed_by": str(actor["id"])}
    try:
        await E.complete_user_task(pool, uuid.UUID(task["id"]), actor["id"], result)
    except E.MakerCheckerError as exc:
        raise MakerCheckerError(f"maker-checker: {exc}") from exc
    except E.WorkflowPermissionError as exc:
        raise HumanTaskPermissionError(f"Permission required: {task['eligible_permission']}") from exc
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _audit(conn, org_id, actor["id"], "human_task.completed", task["id"],
                         {"kind": task["kind"], "subject_type": task["subject_type"],
                          "subject_id": task["subject_id"], "outcome": outcome, "note": note})
            done = await get_task(conn, org_id, task["id"])
    await _publish(pool, org_id, "human_task.completed", task["id"],
                   {"kind": task["kind"], "subject_type": task["subject_type"], "subject_id": task["subject_id"],
                    "outcome": outcome}, actor["id"])
    if k.on_complete is not None:
        done["subject_result"] = await k.on_complete(pool, done, outcome, str(actor["id"]), note)
    return done


async def cancel_task(pool, *, org_id, task_id, actor_id, reason: str, actor_kind: str = "user") -> dict:
    """Withdraw an open task (its subject went away). The caller decides who
    may; this records it on the engine's run and closes the todos."""
    if not (reason or "").strip():
        raise HumanTaskError("a cancelled task needs a reason")
    async with pool.acquire() as conn:
        actor = await assert_human(conn, actor_id, org_id, actor_kind)
        task = await get_task(conn, org_id, task_id)
    if task["status"] != "open":
        raise HumanTaskConflict(f"this task is {task['status']}, not open")
    await E.cancel_user_task(pool, uuid.UUID(task["id"]), actor["id"], reason)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _audit(conn, org_id, actor["id"], "human_task.cancelled", task["id"],
                         {"kind": task["kind"], "subject_type": task["subject_type"],
                          "subject_id": task["subject_id"], "reason": reason})
            done = await get_task(conn, org_id, task["id"])
    await _publish(pool, org_id, "human_task.cancelled", task["id"],
                   {"kind": task["kind"], "subject_type": task["subject_type"], "subject_id": task["subject_id"],
                    "reason": reason}, actor["id"])
    return done
