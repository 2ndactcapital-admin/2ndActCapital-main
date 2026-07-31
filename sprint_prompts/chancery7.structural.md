CHANCERY — PHASE 7 (Workflow Manager integration). 4 tasks +
verification. FIRST real firing of workflow_triggers.event_type
— narrowly scoped to exactly ONE event type ('document_confirmed'),
not a general-purpose autonomous-trigger system. Builds on
Chancery Phases 1-6 (all merged) and Workflow Manager Phases 1-5
(all merged, including the real execution engine and governance).

CRITICAL SAFETY CONSTRAINT: a workflow_run started by this
mechanism must respect EVERY step's existing autonomy tier
exactly as already built — a Tier-1 step must still pause for
maker-checker approval, Tier-2 still confirm-and-log, only Tier-3
executes freely. This phase automates WHICH RUNS START, never
bypasses what happens WITHIN a run. Do not weaken any existing
governance to make this "simpler."

STANDING RULES: org_id never from request body; no interactive
prompts; light theme if any UI is touched.

=== TASK 1: Discover, don't assume ===
  (a) Confirm NO event-type trigger firing exists anywhere yet
      (workflow_triggers.event_type is schema-only per Workflow
      Manager Phase 4 — verify this is still true, nothing built
      since changed it).
  (b) Re-read the REAL start_workflow_run signature (Workflow
      Manager Phase 1's services/workflow_engine.py) — confirm
      exactly what context/org_id/started_by it needs, and
      confirm it still correctly pauses at Tier-1 steps (do not
      assume, re-verify against the real current code).
  (c) Re-read Phase 6's REAL confirm-document endpoint — the
      exact hook point where a 'document_confirmed' event should
      fire from.
Report all three findings before proceeding.

=== TASK 2: Event-trigger firing service ===
Build apps/api/services/chancery_workflow_bridge.py:
  - A function that, given a confirmed document's org_id and
    doc_family/category, looks up ACTIVE workflow_triggers rows
    where trigger_type='event' and event_type='document_confirmed'
    for that org (optionally further filtered by category if a
    trigger is scoped that narrowly — check Task 1a's real schema
    for whether such filtering is possible, do not invent a new
    column if one isn't needed).
  - If ONE OR MORE matching triggers exist: start a
    workflow_run for each (via Task 1b's real
    start_workflow_run), passing the document's real context
    (document_id, entity_id if linked via Phase 5, extracted/
    confirmed mapped_fields) as the run's context.
  - If NONE exist: do nothing — log it clearly (e.g. "no
    matching workflow trigger for org X, category Y" at info
    level), do NOT fail, do NOT error the confirm action itself.

=== TASK 3: Wire into Phase 6's confirm endpoint ===
Call Task 2's function from the real confirm-document hook
point (Task 1c) — AFTER the document's status is successfully
set to 'confirmed', not before (a failed confirm should never
have started a workflow).

=== TASK 4: Minimal admin capability to configure a trigger ===
Extend whatever real Scheduler/Routine Viewer screen already
exists (Workflow Manager Phase 4) with the ability to create an
event-type trigger (event_type='document_confirmed') pointing at
a chosen workflow_definition — reuse the existing screen/pattern,
do not build a new one. If the existing screen's UI doesn't
cleanly support this without significant rework, a minimal
backend-only endpoint to create this trigger type is an
acceptable fallback — report which you built and why.

=== VERIFICATION ===
Write verify_chancery7.py (apps/api/scripts/) — pass/fail only,
no interactive prompts, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] Report Task 1's three discovery findings explicitly
  [Y] With a REAL active event trigger configured for
      'document_confirmed', confirming a matching document
      correctly starts a real workflow_run with correct context
      (document_id/entity_id/mapped_fields all present and
      correct in the run's context)
  [Y] The started run's Tier-1 step STILL pauses for approval —
      does NOT execute autonomously just because the run itself
      auto-started (this is the critical governance-preservation
      proof, do not skip it)
  [Y] With NO matching trigger configured, confirming a document
      succeeds normally and does NOT start any workflow_run
      (graceful no-op, confirmed via absence, not just lack of
      error)
  [Y] A trigger scoped to a DIFFERENT org does not fire for this
      org's document confirmation (cross-org trigger isolation)
  [Y] Teardown: zero leftover rows (runs/run_steps/triggers/
      documents/etc.)

Report each assertion explicitly. Push when 100% pass — hold for
manual review regardless of tier, given this is the first real
event-triggered execution in the platform.
