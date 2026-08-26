WORKFLOW SCHEDULER — CORE ENGINE. 6 tasks + verification. Design:
docs/WORKFLOW_SCHEDULER_DESIGN_V1.md. Discovery:
docs/WORKFLOW_SCHEDULER_DISCOVERY_FINDINGS.md — READ THIS FIRST,
it corrects several assumptions below.

CONFIRMED REAL FACTS FROM DISCOVERY, DO NOT RE-DERIVE:
- Render's `type: cron` is real, documented, and the correct
  mechanism — NOT an in-process scheduler. Properties: single-run
  guarantee (Render itself delays a run if the prior one is still
  active — this may make part of your own overlap-protection
  logic redundant; verify and report, do not build a duplicate
  mechanism), 12-hour hard timeout, UTC-only scheduling, no free
  plan ($1/month minimum), no persistent disk.
- Because Render's cron scheduling is UTC-only, PER-ORG TIMEZONE
  (per the design doc) must be handled in YOUR OWN recurrence
  computation — the cron job runs frequently (e.g. every 5-15
  min) in UTC and checks which due triggers, in THEIR OWN
  timezone, are actually due right now. Do not attempt to make
  Render's own schedule field timezone-aware; it cannot be.
- start_workflow_run's real location and the real function this
  sprint must call: confirm exact signature directly (discovery
  did not capture it — find it in workflow_engine.py).
- workflow_triggers' REAL current columns (from discovery):
  confirm against docs/schema_snapshot.sql directly — discovery's
  Task 1 was about run/step schema, not triggers' own columns in
  full; do not assume the design doc's proposed columns already
  exist.
- POST /admin/workflow-triggers currently has NO path to create a
  schedule-type trigger — the body model only accepts
  workflow_definition_id, event_type, and is_active. This sprint
  must extend it for schedule_cron and the new recurrence fields,
  or Sprint 3 (CRUD UX) has no API to call.
- render.yaml is CONFIRMED STALE — still states LiteLLM is not
  deployed, when hollisworks-litellm is live (confirmed via HTTP
  probe). Fix this file's LiteLLM section as part of adding the
  new cron service declaration in this same sprint, since you are
  already touching render.yaml.
- Cost/duration correlation to a run is OUT OF SCOPE — discovery
  confirmed zero workflow run steps have ever invoked AI, so there
  is nothing to correlate yet. Do not build speculative plumbing
  for this.
- Failure alerting reuses the REAL, exact precedent:
  workflow_engine.py::_hold_run -> workflow_todos.
  create_held_run_alerts(conn, org_id=, run_id=, started_by=,
  error_detail=) — recipients are the run starter plus every
  org_admin in the org, deduped via SELECT-then-upsert on
  (user_id, org_id, source, related_type, related_id). A
  scheduler-fired run that fails should produce the SAME alert
  shape as any other held run — it already will, if you call the
  same start_workflow_run/execution path event triggers use.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately. If uncertain, continue.

STANDING RULES: no interactive prompts; workflowpermsfix.
structural must be merged before this sprint, so a real org_admin
can be used in Task 5's proof — confirm it landed, do not assume.

=== TASK 1: DISCOVER — confirm what's real right now ===
Report findings, THEN CONTINUE IMMEDIATELY.
  1a. workflow_triggers' EXACT current full column list (not
      assumed from the design doc).
  1b. start_workflow_run's real signature and file location.
  1c. Confirm a real, maintained Python RRULE library
      (python-dateutil's rrule) is available or add it.
  1d. Confirm POST /admin/workflow-triggers' exact current
      Pydantic body model — this sprint extends it.

=== TASK 2: SCHEMA ===
Add to workflow_triggers: timezone (text, IANA name, e.g.
'America/New_York'), start_date, end_date, max_occurrences,
occurrence_count (running counter), last_fired_at. Extend
POST /admin/workflow-triggers' body model to accept
schedule_cron + these new fields when trigger_type='schedule'.

=== TASK 3: THE FIRING MECHANISM ===
A new, minimal, separate entrypoint script (not the main API
process) that: queries all ACTIVE trigger_type='schedule' rows,
computes per-trigger (using its own real timezone) whether it is
due right now via the real RRULE library, respecting start_date/
end_date/max_occurrences, and if due AND last_fired_at does not
already cover this occurrence: calls the real start_workflow_run
function and updates last_fired_at + increments occurrence_count
in the SAME transaction as the fire decision (the idempotency
guarantee). Add this as a NEW `type: cron` service in render.yaml
(fixing the stale LiteLLM section in the same edit), with a
reasonable real schedule (e.g. every 5 minutes, UTC).

=== TASK 4: OVERLAP — confirm Render's guarantee, don't
duplicate blindly ===
Render's cron job type itself guarantees at most one run of the
CRON JOB SERVICE ITSELF at a time. This does NOT mean two
DIFFERENT triggers' fired WORKFLOWS can't run concurrently — it
only means the checker script itself won't overlap with its own
prior invocation. Your own overlap check (per the design doc) is
still needed at the WORKFLOW level: before firing, check for a
real, current, non-terminal workflow_runs row for this same
trigger's workflow, and skip (logged visibly, not silently) if
one is in progress.

=== TASK 5: REAL PROOF ===
  - A real trigger with a genuinely due schedule (in ITS OWN
    timezone) fires a real workflow_runs row via the real
    start_workflow_run function.
  - A trigger not yet due (including a timezone-boundary case —
    due in one timezone's "now" but not another's) does not fire.
  - Running the check-and-fire logic twice in immediate
    succession against the same due trigger fires EXACTLY ONCE.
  - A trigger whose prior WORKFLOW run is still in-progress is
    skipped, logged visibly — proven with a real in-progress
    workflow_runs row.
  - A trigger past its end_date or max_occurrences does not fire.
  - A held/failed scheduler-fired run produces the SAME
    create_held_run_alerts alert shape as any other held run.
  - Cross-org isolation on scheduled firing.
  - An org_admin (not just super_admin, confirming
    workflowpermsfix merged correctly) can create a real
    schedule-type trigger via the extended API.

=== TASK 6: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md: scheduler core engine built,
render.yaml's LiteLLM section corrected, workflow_triggers.
schedule_cron no longer dead code, Sprint 3 (CRUD UX) next.

=== VERIFICATION: apps/api/scripts/verify_schedulercore.py ===
  [Y] Report Task 1's four findings
  [Y] A due trigger (own timezone) fires a real run
  [Y] A not-yet-due trigger, including a timezone-boundary case,
      does not fire
  [Y] Double-invocation on the same due trigger fires exactly once
  [Y] An in-progress workflow's trigger is skipped, logged visibly
  [Y] end_date/max_occurrences correctly stop firing
  [Y] A held scheduler-fired run alerts via the real, existing
      mechanism
  [Y] Cross-org isolation
  [Y] An org_admin can create a schedule trigger via the API
  [Y] Teardown: zero leftover rows
