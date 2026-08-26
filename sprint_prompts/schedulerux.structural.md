WORKFLOW SCHEDULER — CRUD UX. 5 tasks + verification. Sprint 2
(Core Engine) is complete and merged — the real API and firing
mechanism exist. This sprint builds the screen on top of it. Same
established pattern as the Portfolio UX sprints: DataGrid.jsx
reused, right-pane detail, server-published permission envelope
(can_read/can_write), no client-side fallback lists.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

CONFIRMED REAL FACTS FROM SPRINT 2, DO NOT RE-DERIVE:
- POST /admin/workflow-triggers now accepts trigger_type
  ('event' | 'scheduled'), schedule_cron, timezone, start_date,
  end_date, max_occurrences — full validation at the API boundary
  (malformed cron, unknown IANA zone, end before start, etc. all
  422 with specific messages).
- The real, deployed vocabulary value is 'scheduled', NOT
  'schedule' — use this exact string throughout the UI.
- Permissions: configure_workflow_triggers gates this whole
  surface (author_workflows for workflow definitions themselves,
  view_workflow_runs for run history — this sprint is
  triggers-only, do not build run-history UI here, that is
  Sprint 4).
- occurrence_count, last_fired_at are real, live fields on every
  trigger row — the UI should surface these, not just the
  recurrence definition.

STANDING RULES: no interactive prompts; light theme; schema-
qualify where applicable.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Re-read the REAL current WorkflowTriggerScheduler.jsx
      (referenced in Sprint 1/2 discovery) — this is the existing
      component that DISPLAYS triggers today. Confirm its real
      current shape before deciding whether to extend it or
      replace it.
  1b. Confirm the REAL current GET /admin/workflow-triggers
      response shape — does it already return the new recurrence
      fields, or does it need extending alongside the create
      endpoint from Sprint 2?
  1c. Confirm whether a DELETE or PATCH endpoint exists for a
      trigger today (Sprint 2 only confirmed POST and GET) — a
      "pause without delete" and true delete both need real
      endpoints; report what's missing before building blind.
  1d. Confirm the real, established permission-envelope pattern
      from the Portfolio UX sprints (can_read/can_write published
      per-response, empty editable lists for view-only callers) —
      this sprint reuses that exact shape for triggers.

=== TASK 2: API — whatever Task 1c found missing ===
Build real PATCH (edit + pause/resume via is_active) and DELETE
endpoints for a trigger, gated on configure_workflow_triggers,
following Sprint 2's real validation rules (a paused trigger
retains its full recurrence config; editing re-validates cron/
timezone/date-ordering exactly as create does).

=== TASK 3: THE SCREEN ===
A real Triggers management screen: list view (DataGrid-driven)
showing workflow name, trigger_type, recurrence summary (human-
readable, not raw cron — e.g. "Daily at 9:00 AM America/New_York"),
is_active, occurrence_count, last_fired_at. Create/edit via the
right pane, with REAL validation feedback surfaced from the API's
422 responses (not re-derived client-side). A genuine "dry-run
preview" — call a real endpoint (build if missing) that computes
and returns the next 5 real occurrences for a given recurrence
definition BEFORE saving, using the SAME real RRULE logic Sprint
2's scheduler itself uses (import/reuse the function, do not
reimplement the computation in the API layer a second time).

=== TASK 4: PAUSE VS DELETE, DISTINCT AND CLEAR ===
Pause (is_active=false) and delete are two different, clearly
separated actions in the UI — pausing preserves occurrence_count/
last_fired_at/full config; delete is a real, confirmed
irreversible action. A paused trigger displays as visually
distinct from an active one in the list.

=== TASK 5: REAL PROOF ===
  - The screen loads real, live triggers via the real API —
    no mock data.
  - Creating a trigger through the UI produces a row the
    scheduler (Sprint 2's real tick logic) would actually pick up
    — prove this by creating one due imminently and confirming a
    manual tick invocation fires it.
  - The dry-run preview returns the SAME 5 occurrences the real
    scheduler's own RRULE computation would produce for that
    recurrence definition — proven by comparing directly, not
    assumed to match because both "use RRULE."
  - Pausing a trigger stops it from firing (proven with a real
    tick), without losing its occurrence_count/config.
  - Deleting a trigger removes it; a subsequent tick does not
    reference it.
  - A view-only user (configure_workflow_triggers absent) can see
    the screen's read parts but the UI renders no create/edit/
    pause/delete controls, and the API refuses each if attempted
    directly.
  - npm run build exits 0.

=== VERIFICATION: apps/api/scripts/verify_schedulerux.py ===
Pass/fail only.

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] Real triggers load via the real API, no mock data
  [Y] A UI-created trigger is genuinely picked up by a real tick
  [Y] Dry-run preview matches the scheduler's own real RRULE
      computation exactly
  [Y] Pause stops firing without losing state; delete removes
      the trigger entirely
  [Y] View-only: no write controls rendered AND API-level refusal
      on direct attempt, checked independently
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows
