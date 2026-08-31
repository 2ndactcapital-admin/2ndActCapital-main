PLATFORM INFRA — SPRINT event-emission (the missing publish side of
workflow_triggers). 3 tasks + verification. Part 1 SQL (domain_events,
domain_event_deliveries) is already applied by Joe directly via
Supabase MCP — confirm it live before writing any code.

WHY THIS ISN'T A "FEE" SPRINT: workflow_triggers/workflow_runs are
platform infrastructure, not fee-module tables. This sprint builds the
generic publish mechanism the platform was clearly designed to pair
with (a trigger row that says "listen for event_type X" implies
something publishes X) but never had. SPV realizations are this
sprint's first real emitter and its proof case, not its only intended
consumer — a future NAV-update job, an LP-notification workflow, or
anything else can subscribe to the same event_type without touching
this sprint's code again.

CONTEXT, settled, do not re-derive:
- Only transaction_type_id='dist_gain' represents a carry-triggering
  realization (an actual profit distribution). dist_roc (return of
  capital), dist_income, dist_recallable, dist_stock are distributions
  but NOT realizations for this purpose — verify this reading against
  the actual accounting semantics in Task 1 (a return-of-capital
  distribution genuinely has no gain to carry against; confirm this is
  still true rather than trusting this prompt's restatement of it).
- Events fire only when the underlying spv_transactions row is
  POSTED, never on draft/pending — same discipline as fee36's posted-
  only credit basis and fee37/39's posted-only cost recognition.
- domain_events is append-only and retains every event regardless of
  whether a trigger existed to catch it at the time. Do not couple
  publishing to whether any subscriber currently exists.
- workflow_runs.workflow_version_id vs workflow_triggers.
  workflow_definition_id is a real mapping gap this sprint must
  resolve concretely in Task 1 (most likely: the latest published
  version of that definition) — report the actual resolution, do not
  guess silently.
- This sprint does NOT build carry calculation. It builds the
  publish mechanism and proves ONE realistic trigger-to-run round trip
  end to end. fee42b (a separate, later sprint) subscribes to the
  event this sprint defines; it does not get built here.
- Per the standing sequencing note, workflow-triggered WRITE actions
  are gated on SOC/RBAC's maker-checker discipline already being real
  — it is (confirmed: SOC/RBAC fully finalized, fee36 already uses
  assistant_activities for exactly this kind of approval gate). A
  workflow_run this sprint creates should PROPOSE a downstream action
  (in this sprint's proof case, nothing more than a workflow_run
  existing with the right context — no write-verb execution belongs in
  this sprint at all).

OUT OF SCOPE: carry calculation (fee42b). Any other future consumer of
this event (NAV updates, LP notifications) — this sprint proves the
mechanism works, it does not build every future subscriber. Any
Altruist-API-shaped work.

STANDING RULES: org_id never from request bodies. Decimal for any
monetary figure carried in an event payload. No interactive prompts.
Additive-first — this sprint must not require changing
workflow_triggers' or workflow_runs' existing schema or behavior for
any trigger/run not created by this sprint's own code path.

=== TASK 1: Discover, don't assume ===
Confirm live: domain_events/domain_event_deliveries exactly as
deployed. Read the REAL workflow_triggers and workflow_runs code
(routers/services, wherever workflow runs are actually created today
for scheduled/cron triggers) to find the existing, real mechanism for
turning a workflow_definition_id into a workflow_version_id for a new
run — do not invent a second one. Confirm the transaction_types
vocabulary and the dist_gain/dist_roc distinction against real
accounting logic elsewhere in the codebase (e.g. does spv_transaction_
allocations or the capital-account calculation already treat these
differently — if so, align with that existing distinction rather than
re-deriving it). Report findings before writing code.

=== TASK 2: publish_event() + trigger matching ===
A single function: given org_id, event_type, source_type, source_id,
payload — inserts the domain_events row (idempotent via the dedupe
unique index: re-publishing the identical event is a clean no-op, not
an error), then finds all matching, active workflow_triggers
(trigger_type='event', matching event_type, is_active=true, this
org), and for each one creates a workflow_runs row using the REAL
definition-to-version resolution from Task 1, with context containing
at minimum {event_type, source_type, source_id, payload, occurred_at}.
Record each attempt in domain_event_deliveries (DELIVERED with the
real workflow_run_id, or FAILED with error_detail — a trigger whose
definition has no publishable version, for instance, must FAIL loudly
here, not silently skip).

=== TASK 3: wire the SPV realization emitter + end-to-end proof ===
Wherever an spv_transactions row transitions to POSTED (find the real
code path, do not write a second one), call publish_event with
event_type='spv_realization' when transaction_type_id resolves to
dist_gain (and only then). Payload must include at minimum: spv_id,
spv_transaction_id, amount, class_label if applicable, and the
investor-level allocations from spv_transaction_allocations (fee42's
carry engine will need per-investor amounts, not just the vehicle
total). Build the actual end-to-end proof: register a real
workflow_trigger for event_type='spv_realization' pointing at some
real (even minimal/placeholder) workflow_definition, post a real
dist_gain spv_transaction, and confirm a real workflow_runs row was
created with the correct context — not a mocked call to publish_event
in isolation.

=== VERIFICATION ===
Write scripts/verify_event_emission.py — pass/fail only, app_service
for RLS, teardown discipline.
Assert:
  1. Both tables deployed, RLS on, expected constraint/policy shape;
     the dedupe unique index is genuinely enforced (publishing the
     identical event twice creates one domain_events row, not two).
  2. Posting a dist_gain spv_transaction fires the event; posting a
     dist_roc spv_transaction does NOT (prove both directions on
     otherwise-identical fixtures — a filter that fires on everything
     would pass the first half and fail this one).
  3. A draft (unposted) dist_gain transaction fires nothing; posting it
     afterward does.
  4. An active, matching workflow_trigger produces a real workflow_runs
     row with the correct context payload, and a domain_event_deliveries
     row marked DELIVERED naming that run.
  5. An INACTIVE trigger (is_active=false) matching the same event_type
     does NOT fire — prove the flag is actually read.
  6. A trigger for a DIFFERENT event_type does not fire on this event.
  7. Two triggers both matching the same event_type each get their own
     workflow_runs row and their own domain_event_deliveries row — one
     event can fan out to multiple subscribers.
  8. A trigger whose definition cannot be resolved to a publishable
     version produces a FAILED delivery with a real error_detail, not
     a silent skip and not an unhandled exception that aborts the
     whole publish call for other, valid triggers.
  9. Re-publishing the identical event (same org/event_type/source)
     is a clean no-op — no duplicate domain_events row, no duplicate
     workflow_runs created for triggers that already fired once.
  10. The event payload carries per-investor allocation amounts, not
      just the vehicle-level total — confirmed against a real
      spv_transaction_allocations fixture with more than one investor.
  11. Cross-org isolation on both tables via app_service.
  12. No table's row count differs from its pre-test count after the
      script exits.
Report actual results, then stop. Do not build fee42b in this same
run.
