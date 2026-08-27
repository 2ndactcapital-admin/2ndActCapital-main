WORKFLOW SCHEDULER — RUN HISTORY + LOGGING. 5 tasks +
verification. Sprints 1-3 are complete and merged: real permission
grants, a real firing engine (RRULE-based, timezone-aware,
idempotent, overlap-protected), and full CRUD UX for triggers.
This sprint builds the missing other half — seeing what actually
RAN, not just what's scheduled to run.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- workflow_runs (10 cols) and workflow_run_steps (12 cols) have
  NO cost or duration columns that mean anything for a Service
  Task — confirmed by schedulerdiscovery.lowrisk: the engine
  stamps started_at/completed_at in the SAME post-hoc UPDATE, so
  the interval is always exactly zero for a Service Task. User
  Task duration IS real (measures human wait time). Do NOT build
  a "duration" column display for Service Task rows that would
  show a meaningless zero as if it were real data — either omit
  duration for Service Tasks or label it honestly as "not
  measured for this step type."
- ai_decision_log has NO correlation to any run or step (no
  workflow_run_id column) and — confirmed same sprint — ZERO
  workflow run steps have ever invoked AI in this environment.
  Cost display is genuinely OUT OF SCOPE for this sprint; do not
  build speculative plumbing for data that doesn't exist yet.
- The real permission is view_workflow_runs (confirmed real,
    granted on both role and profile axes via workflowpermsfix).
- GET /admin/workflow-runs and GET /admin/workflow-runs/{run_id}
  already exist (confirmed by schedulerdiscovery) — this sprint
  extends/builds UI on top of them, and adds whatever the UI
  genuinely needs that these endpoints don't yet return.
- A run started by the SCHEDULER carries its trigger context in
  workflow_runs.context (confirmed by schedulercore's real proof:
  {"trigger_id": ..., "trigger_type": "scheduled",
  "scheduled_occurrence": ...}) — a MANUALLY-started run's context
  will not have this shape. The UI must display "started by
  schedule" vs "started manually" correctly for both cases, read
  from the real data, not assumed.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts; light theme; same
DataGrid + right-pane + permission-envelope pattern as every
prior UX sprint this project — reuse verbatim, do not reinvent.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Confirm the REAL current GET /admin/workflow-runs and
      GET /admin/workflow-runs/{run_id} response shapes — do they
      already join to workflow_definitions for a human-readable
      name? Do they already return workflow_run_steps for the
      detail view? Report exactly what exists vs. what this
      sprint must add.
  1b. Confirm the REAL current status vocabulary on workflow_runs
      (known values so far: running, completed, held — confirm
      the complete, real set from the CHECK constraint or actual
      deployed data, do not assume this list is complete).
  1c. Confirm whether there is a REAL existing UI component
      anywhere in apps/web that already renders ANY run-related
      data (even partially) — do not build a second one if one
      exists.
  1d. Confirm the REAL current shape of a held run's error_detail
      and its linked member_todos alert (per _hold_run /
      create_held_run_alerts, confirmed real in schedulerdiscovery)
      — the run detail pane should surface this same information,
      not re-derive it separately.

=== TASK 2: API — whatever Task 1a found missing ===
Extend the real endpoints (or add what's missing) so a single
call returns: the run's own fields, its workflow definition's
name, its full step-by-step history (workflow_run_steps, in
order), and — where the run's context indicates a scheduler
origin — the originating trigger's id and recurrence summary
(reusing Sprint 3's real schedule_summary logic, not
reimplementing it).

=== TASK 3: THE SCREEN ===
A real Run History screen: DataGrid-driven list — workflow name,
status, started_at, completed_at (User-Task-derived duration
where meaningful, honestly omitted/labeled otherwise per the
standing rule above), started-by (a real user, OR "Scheduled: 
{trigger name}" when the context indicates scheduler origin).
Filterable by status and by time period. Row select opens a
right-pane detail: full step-by-step timeline, and — for a held
run — the error detail and a real link to see whose todos got
alerted (reusing the real member_todos data, not re-deriving
which users were notified).

=== TASK 4: REAL PROOF ===
  - Screen loads real, live runs via the real API — no mock data.
  - A run started by Sprint 2's scheduler displays correctly as
    "Scheduled: {trigger}" — proven against a REAL scheduler-
    fired run (create one via a real tick, per Sprint 2's own
    proven mechanism).
  - A manually-started run displays correctly as started by its
    real human user — proven against a real manual run, not
    assumed to differ just because the code branches.
  - A held run's detail pane shows the REAL error_detail and
    correctly identifies the REAL set of users who were alerted
    (matching member_todos exactly, not a guessed recipient list).
  - Status and time-period filters genuinely narrow against real
    data, proven both directions (non-empty AND excludes what it
    should exclude).
  - A view-only user (view_workflow_runs) can read this screen;
    a user with neither workflow permission is refused.
  - npm run build exits 0.

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md and
docs/WORKFLOW_SCHEDULER_DESIGN_V1.md's phasing table: Sprint 4
complete, Sprint 5 (notifications — already largely satisfied by
the existing member_todos mechanism, confirm what if anything is
genuinely still missing) next.

=== VERIFICATION: apps/api/scripts/verify_schedulerhistory.py ===
Pass/fail only.

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] Screen loads real runs, no mock data
  [Y] A real scheduler-fired run displays its scheduled origin
      correctly
  [Y] A real manually-started run displays its human starter
      correctly
  [Y] A held run's detail shows real error_detail and the REAL,
      exact set of alerted users
  [Y] Status and time-period filters narrow correctly, both
      directions
  [Y] View-only can read; a user with neither key is refused
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows
