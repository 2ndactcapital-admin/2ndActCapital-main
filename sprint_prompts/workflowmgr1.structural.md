WORKFLOW MANAGER — PHASE 1 (object model + SpiffWorkflow
foundation). 3 tasks + verification. This is Phase 1 of a
multi-phase build — this phase is FOUNDATION ONLY: no UI, no
NL-to-BPMN generation, no run console. Just prove the execution
engine works end to end against real seeded data.

CONTEXT: 6 tables already exist (Part 1 SQL applied directly):
workflow_definitions, workflow_versions (bpmn_xml text is the
canonical stored artifact), workflow_steps (autonomy_tier
integer, assigned_role_profile_id references profiles(id),
action_registry_key for Service Tasks), workflow_runs
(spiff_serialized_state jsonb — for pausing/resuming a run),
workflow_run_steps (status text, proposed_by/approved_by with a
HARD CHECK constraint: approved_by != proposed_by when both are
set — the same maker-checker pattern already used on
assistant_activities, confirmed by Joe to apply broadly, not
just to money-movement), workflow_triggers (trigger_type
defaults to 'manual' — scheduled/event triggers exist as schema
only in this phase, they do NOT fire autonomously yet, that is
Wave 4, a separate later effort).

CONFIRMED DESIGN DECISIONS (do not re-litigate, build to these):
  - Execution engine is SpiffWorkflow (Python BPMN+DMN engine),
    paired with bpmn-js-authored XML. Install via pip
    (--break-system-packages per this repo's standing pip rule).
    SpiffWorkflow owns correct BPMN token-passing/gateway/timer/
    DMN semantics; our own tables are the audit/governance layer
    on top (autonomy tiers, proposed-state rows, the run/step
    history) — do not reimplement BPMN execution semantics
    yourself, delegate to SpiffWorkflow.
  - User Task steps carry an assigned_role_profile_id, set by
    whoever authors the workflow (a static, per-step authoring
    decision) — referencing the REAL, EXISTING profiles table
    from the SOC/RBAC build. Find its actual current schema
    live, do not assume.
  - Wave-4-style autonomous firing is explicitly OUT OF SCOPE.
    This phase only needs to prove a run can be started
    manually and stepped through correctly.

STANDING RULES: org_id never from request body; Decimal for any
money-touching result values; no interactive prompts; light
theme if any UI is touched (none expected this phase).

=== TASK 1: Discover, don't assume ===
  (a) Confirm SpiffWorkflow installs cleanly in the existing
      Python environment (pip install SpiffWorkflow
      --break-system-packages) and report its installed version.
  (b) Read the REAL, live profiles table schema (from the SOC
      build) — confirm its actual columns before writing any
      code that joins against it.
  (c) Find and read the Sprint-11 action registry's REAL current
      shape (referenced throughout this project but its exact
      table/module/format has not been independently re-verified
      this session) — report what a Service Task's
      action_registry_key should actually reference.
Report all three findings before proceeding.

=== TASK 2: Core execution service ===
Build apps/api/services/workflow_engine.py:
  - A function to parse a BPMN XML string (via SpiffWorkflow's
    parser) into a runnable spec
  - start_workflow_run(workflow_version_id, org_id, context,
    started_by) -> creates a workflow_runs row + workflow_steps-
    matching workflow_run_steps rows, instantiates the
    SpiffWorkflow spec, and steps it forward until it hits a
    User Task (pause) or completes
  - A function to serialize SpiffWorkflow's in-flight state into
    workflow_runs.spiff_serialized_state, and a matching
    deserialize function to resume a paused run
  - complete_user_task(workflow_run_step_id, completed_by,
    result) -> for a Tier-1 step, this is the "approve" action —
    ENFORCE that completed_by != the row's proposed_by (the DB
    constraint already prevents storing this state, but fail
    with a clear application-level error too, don't rely on the
    DB constraint alone to communicate the problem)

=== TASK 3: End-to-end proof with a real seeded test process ===
Create a SIMPLE, real BPMN XML test fixture (do not need bpmn-js
for this — a minimal hand-written or SpiffWorkflow-example-
derived BPMN file is fine) with: a Start Event, one Service Task
(mapped to a real, harmless, read-only action_registry_key —
find one via Task 1c), one User Task (with an
assigned_role_profile_id set to a real seeded Profile from Task
1b), and an End Event. Store this as a workflow_versions row
under a test workflow_definitions row.

=== VERIFICATION ===
Write verify_workflowmgr1.py (apps/api/scripts/) — pass/fail
only, no interactive prompts, teardown-at-start and teardown-
at-end.

Assertions to include:
  [Y] All 6 tables confirmed to exist matching the snapshot,
      including the maker-checker CHECK constraint on
      workflow_run_steps
  [Y] Report Task 1's three discovery findings explicitly
  [Y] SpiffWorkflow successfully parses the test BPMN XML into a
      spec
  [Y] start_workflow_run on the test process correctly executes
      the Service Task automatically, then PAUSES at the User
      Task (workflow_run_steps status reflects this correctly —
      Service Task step = completed, User Task step = pending/
      active)
  [Y] The paused run's state can be serialized to
      spiff_serialized_state and correctly DESERIALIZED back into
      a resumable SpiffWorkflow instance (proves pause/resume
      genuinely works, not just parse-and-run-to-completion)
  [Y] complete_user_task with a DIFFERENT completed_by than the
      step's proposed_by succeeds, and the run then correctly
      proceeds to the End Event / completes
  [Y] complete_user_task attempting completed_by == proposed_by
      is REJECTED (both at the DB constraint level AND with a
      clear application-level error)
  [Y] The completed run's workflow_run_steps show the correct
      assigned_role_profile_id was honored (matches the real
      seeded Profile from Task 1b)
  [Y] Teardown: zero leftover rows, confirm via count(*)

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier, given this establishes the
core execution engine every future phase builds on.
