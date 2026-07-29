WORKFLOW MANAGER — PHASE 4 (Run Console + Scheduler/Routine
Viewer + Task/Alert integration + Version History). 3 tasks +
verification. Builds on Phases 1-3 (execution engine, NL
generation + deriver, diagram editor). No changes to Wave 4
autonomous firing — triggers remain manual-only for actual
execution in this phase; scheduled/event trigger TYPES exist in
schema (workflow_triggers) but do not yet fire anything
autonomously. That remains its own separate, later, gated effort.

CONTEXT: workflow_runs.status values in use so far: running/
completed/failed/held/cancelled. workflow_run_steps.status:
pending/active/proposed/approved/completed/failed/skipped.
CONFIRMED DESIGN (do not re-litigate): the task/alert surface for
an active User Task, or a run entering 'held' status after a
failure, REUSES the EXISTING member_todos infrastructure (open/
done/dismissed/snoozed states, from the AI dashboard work) — do
NOT build a new notification system. A Scheduler/Routine Viewer
is ORG-SCOPED for Org Admin, ALL-ORGS for Super Admin. Wave-4-
style failures HOLD and ALERT, never silently retry — this
applies even to a manually-triggered run that fails today, not
just future autonomous runs.

STANDING RULES: org_id never from request body; no interactive
prompts; light theme matching every other admin screen. Reuse
the SAME admin-gating pattern confirmed in Phase 3
(services.rbac.can_manage_org_settings / the _require_admin
helper) — do not invent new gating logic.

=== TASK 1: Discover, don't assume ===
  (a) Read the REAL, live member_todos table schema (referenced
      throughout this project but not independently re-verified
      this session) — exact columns, exact status values in
      actual use.
  (b) Confirm whether any existing code path already creates a
      member_todos row for a comparable "something needs your
      attention" case (e.g. a compliance_override_request or
      similar) — if a reusable pattern/helper exists, use it
      rather than writing raw INSERTs against member_todos
      directly.
  (c) Confirm workflow_runs/workflow_run_steps status values
      actually produced by Phases 1-3's code match what's
      documented above — report any discrepancy found.
Report all three findings before proceeding.

=== TASK 2: Task/Alert integration ===
  - When a workflow_run_step's status becomes 'pending' or
    'active' for a User Task, create (or update, if one already
    exists for that step) a member_todos entry for each user
    holding the step's assigned_role_profile_id in that org —
    reusing whatever pattern Task 1b found, or a minimal correct
    INSERT if none exists. Completing the task (Phase 1's
    complete_user_task) should mark the corresponding todo done.
  - When a workflow_run's status becomes 'held' (a failure —
    build this transition into the engine if it doesn't already
    exist: any unhandled exception during run execution should
    set status='held' with error_detail populated, NOT leave the
    run silently stuck in 'running'), create a member_todos alert
    for the run's started_by user AND for Org Admins of that org
    (a held run is an operational problem, not just the
    initiator's concern).

=== TASK 3: Run Console + Scheduler/Routine Viewer + Version
History screens ===
New admin screens (e.g. under apps/web/app/admin/workflows/):
  - RUN CONSOLE: list workflow_runs for the org (all-orgs for
    Super Admin) with status, started_by, started_at; drill into
    a run to see its workflow_run_steps with per-step status/
    result/error_detail.
  - SCHEDULER / ROUTINE VIEWER: list workflow_triggers for the
    org (all-orgs for Super Admin) showing trigger_type/
    schedule_cron/event_type/is_active — READ/CONFIGURE only in
    this phase, does not need to actually fire anything
    autonomously (that's Wave 4).
  - VERSION HISTORY: for a given workflow_definition, list all
    workflow_versions (version_number, created_by, created_at,
    change_summary, is_current flag) — read-only browsing, no
    diff-rendering required in this phase.

=== VERIFICATION ===
Write verify_workflowmgr4.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-
at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] An active User Task creates a member_todos entry for the
      correct assigned-role user(s), and completing the task
      marks it done
  [Y] A run that fails/errors transitions to status='held' (not
      left stuck in 'running'), with error_detail populated, and
      creates a member_todos alert for both the run's starter AND
      an Org Admin
  [Y] Run Console endpoint returns the org's own runs and NOT
      another org's; Super Admin sees across all orgs
  [Y] Scheduler/Routine Viewer endpoint is correctly org-scoped
      for Org Admin and all-orgs for Super Admin
  [Y] Version History correctly lists all versions for a
      definition in order, with exactly one is_current=true
  [Y] Non-admin (member) is rejected from all three new screens'
      endpoints
  [Y] npm run build exits 0
  [Y] No hardcoded Signature-palette hex in any new file
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier.
