WORKFLOW SCHEDULER — NOTIFICATIONS. 4 tasks + verification. This
sprint is likely mostly CONFIRMATION, not new build — Sprint 4
already proved the real alerting mechanism (_hold_run ->
create_held_run_alerts -> member_todos) works correctly for
scheduler-fired runs exactly as it does for manual ones. Do not
build a second notification system. This sprint's real job is
finding whatever genuine gap remains, not inventing work.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- The real mechanism is services/workflow_todos.py's
  create_held_run_alerts, called from workflow_engine.py::
  _hold_run. Recipients = the run's started_by user, plus every
  users.role='org_admin' in the org. Already proven (Sprint 4) to
  work identically for scheduler-fired and manually-started runs.
- The Sprint 9 notification_bus (services/notifications.py) is a
  SEPARATE, real, working system with zero production usage in
  this environment — only routers/marketplace.py calls it. Do NOT
  wire the scheduler into this second system; member_todos is the
  proven, real precedent per the design doc's own finding.
- Two ORPHANED workflow_run_held todos already exist live in the
  database — alert rows pointing at run ids that no longer exist.
  This is a real, found data-integrity gap, not hypothetical.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

=== TASK 1: DISCOVER — what's genuinely still missing ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Confirm whether a WARNING-level alert exists anywhere for a
      scheduled trigger that has been consistently SKIPPING (e.g.
      due to repeated overlap, or approaching end_date/
      max_occurrences) — as opposed to only alerting on an
      outright HELD run. Report honestly if this genuinely does
      not exist — it may be a real, small gap Sprint 2-4 didn't
      cover, since they focused on firing correctness, not
      operator awareness of a schedule quietly winding down.
  1b. Confirm the real, current handling (if any) of the two
      orphaned workflow_run_held todos found in Sprint 4's
      discovery — are they visible anywhere to an admin today, or
      silently inert? Report honestly.
  1c. Confirm whether member_todos has ANY mechanism to alert on
      a trigger nearing its end_date or max_occurrences BEFORE it
      stops firing — e.g. "this schedule will stop running in 3
      occurrences" — or whether a schedule simply goes quiet with
      no warning. This is a real, plausible operator-experience
      gap worth surfacing honestly whether or not it gets built
      in this sprint.

=== TASK 2: FIX — orphaned alert todos, if Task 1b found them
genuinely unaddressed ===
If orphaned workflow_run_held todos are confirmed to be silently
inert (an admin has no way to know they exist or resolve them):
build a minimal, real cleanup — either a one-time data-fix
migration dismissing orphans whose related run no longer exists,
or (better, if a real recurring cause is found) a fix to whatever
is DELETING run rows without cleaning up their alerts. Discover
the REAL cause of the orphans before choosing between these — do
not blindly delete data without knowing why it became orphaned.

=== TASK 3: THE END-OF-LIFE WARNING, IF TASK 1c CONFIRMS THE GAP
===
If a schedule approaching its end_date or max_occurrences
genuinely has no warning today: add ONE, using the exact same
real create_held_run_alerts / member_todos pattern (a new,
distinct source marker, e.g. 'workflow_trigger_expiring') — fired
from the scheduler's own tick when a trigger's NEXT occurrence
would be its last (per max_occurrences) or falls within a real,
reasonable window before end_date. This is a small, targeted
addition to the EXISTING proven mechanism, not a new system.

=== TASK 4: REAL PROOF ===
  - Report Task 1's three findings honestly — if 1a or 1c found
    no real gap worth building, say so plainly and do not
    manufacture speculative work.
  - IF Task 2 built a fix: the real orphaned todos are resolved,
    and a NEW orphan (created via a real test scenario) is either
    prevented or correctly cleaned up — proven against the live
    database.
  - IF Task 3 built the expiring-schedule warning: a trigger one
    occurrence away from its cap fires the warning on its
    second-to-last real tick, and a trigger with several
    occurrences remaining does NOT — proven with real ticks, not
    a mocked scheduler.
  - Cross-org isolation on any new alert path.

=== VERIFICATION: apps/api/scripts/verify_schedulernotify.py ===
Pass/fail only.

Assertions:
  [Y] Report Task 1's three findings explicitly and honestly —
      including if the honest answer is "no real gap found"
  [Y] IF orphans were fixed: real orphans resolved, proven against
      the live database
  [Y] IF the expiring-schedule warning was built: fires at the
      correct real tick, does not fire prematurely, proven with
      real scheduler ticks
  [Y] Cross-org isolation on any new alert path
  [Y] Teardown: zero leftover rows
