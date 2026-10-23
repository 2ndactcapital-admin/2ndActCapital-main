# Governed human tasks (rep2)

One reusable path for "a person must act or approve", built on the workflow
engine. Code: `apps/api/services/human_tasks.py`. First user:
`services/execution.py` (execution-instruction approvals). rep3 moves rep1's
fund-terms approvals onto it.

## What a task is

There is no side table. A task is a run of a SYSTEM workflow definition:

| Piece | Where it lives |
|---|---|
| Definition | `workflow_definitions.name = 'system:human_task:<permission>'`, one per (org, permission), created on first use (advisory-locked, idempotent) |
| The single step | `workflow_steps` row `step_key = 'human_task'`, `step_type = 'user'`, `assigned_permission = <permission>` |
| Subject, maker, title, payload, screen | `workflow_runs.context.human_task` |
| Maker | `workflow_runs.started_by` → `workflow_run_steps.proposed_by` (NULL for a non-maker-checker "action" kind) |
| Task id | `workflow_run_steps.id` |
| Decision | `workflow_run_steps.result = {outcome, note, completed_by}`, `approved_by`, run `completed` |
| Withdrawal | step + run `cancelled`, `result = {cancelled, reason, cancelled_by}` |
| Notification | `member_todos` (source `workflow_user_task`, `related_id` = task id) for every admitted holder of the permission except the maker; retitled with the task title and linked to `action_path`; closed on completion or cancellation |
| Log | `audit_log` (`human_task.opened` / `.completed` / `.cancelled` / `.alert_undelivered`) and, after commit, `domain_events` with the same names (source_type `workflow_run_step`) |

## The engine change (additive)

`workflow_steps.assigned_permission` (nullable text, no default) is the only
schema change. When it is set, the engine notifies the permission's holders
(minus the maker) and refuses a completer who lacks it
(`WorkflowPermissionError`). `cancel_user_task` is new. Every step that
existed before rep2 has NULL, so it takes exactly the old path: todos by
assigned profile, and any completer other than the maker.

## Using it from a module

```python
from services import human_tasks as HT

HT.register_kind("my_approval", label="My approval", outcomes=("approve", "reject"),
                 maker_checker=True, on_complete=my_handler)   # at import time

task = await HT.open_task(pool, org_id=org, kind="my_approval", subject_type="my_thing",
                          subject_id=thing_id, maker_id=user_id, eligible_permission="approve_my_thing",
                          title="Approve my thing", payload={...}, action_path=f"/my/{thing_id}")
done = await HT.complete_task(pool, org_id=org, task_id=task["id"], actor_id=checker_id,
                              outcome="approve", note=None)
await HT.cancel_task(pool, org_id=org, task_id=task["id"], actor_id=user_id, reason="withdrawn")
rows = await HT.list_tasks(conn, org, status="open", subject_type="my_thing", subject_id=thing_id)
history = await HT.task_history(conn, org, task_id)
```

Rules the helper enforces, in this order: the actor is an admitted user (an
agent key or `actor_kind != 'user'` raises `AgentActorRefused`, because these
are approvals of HUMAN-initiated work); one open task per (kind, subject); the
outcome is one the kind allows; a rejection has a note; the maker of a
maker-checker task cannot decide it (`MakerCheckerError`, message names the
rule); the actor holds the permission (`HumanTaskPermissionError`, Super Admin
first). The engine re-checks maker-checker and the permission, and the
`workflow_run_steps_maker_checker` CHECK backs it.

`on_complete(pool, task, outcome, actor_id, note)` runs AFTER the engine
commits, so it must be idempotent; the subject module should also be able to
re-apply a recorded outcome (`services.execution.sync_task_outcome` is the
pattern).

The decision is made on the subject's own gated route (e.g.
`POST /execution/instructions/{id}/approve`), never on a generic task route.
`GET /human-tasks/mine` lists the caller's actionable and waiting tasks for the
dashboard's My tasks panel.

## Known

- The system definitions are ordinary rows, so they appear in an org's Workflow
  library. `ensure_definition` refuses to run one that has been edited into
  something other than a single permission-assigned user task.
