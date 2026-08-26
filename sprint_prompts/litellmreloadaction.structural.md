WORKFLOW ACTION — LITELLM MODEL COST MAP RELOAD. 5 tasks +
verification. Real, known dependency: the LiteLLM proxy itself is
NOT YET DEPLOYED (Phase A of the LiteLLM implementation plan is
incomplete — no live Render service, no confirmed reachable
endpoint). This sprint builds and proves everything genuinely
testable now, and is HONEST about what remains blocked pending
Phase A — do not fake an end-to-end proof against a service that
does not exist yet.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts; org_id never from request
body where applicable.

=== TASK 1: DISCOVER — the real, current workflow action system
===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Read the REAL current action registry (Sprint-11 era,
      referenced by Workflow Manager Wave 2) — exactly how a new
      action gets registered, what a BPMN ServiceTask binding
      looks like, and what real actions already exist as
      examples to follow.
  1b. Read the REAL current 3-tier autonomy verb system — the
      exact, real definitions of each tier (not assumed from
      memory), and find or confirm the LEAST restrictive tier's
      real name/value. This action's tier assignment in Task 3
      must cite the real tier it's assigned to and why, against
      these real definitions.
  1c. Confirm the REAL, current state of scheduled/event-triggered
      workflow execution (Wave 4's scope) — is there ANY working
      mechanism today to schedule a workflow to run
      automatically (e.g. daily), even if Wave 4 was never built
      as its own discrete line item? Report honestly what exists,
      including "nothing exists yet" if that's the real finding —
      do not assume unblocked means built.
  1d. Confirm the REAL, current convention for storing an external
      service's base URL + admin credential (this action will
      need LiteLLM's proxy URL and LITELLM_MASTER_KEY once Phase A
      exists) — org_settings, an env var, or another established
      pattern. Follow whatever's real, do not invent a new one.
Report all four findings before proceeding.

=== TASK 2: BUILD — the action itself ===
Register a new action (e.g. reload_litellm_model_cost_map)
following Task 1a's real registration pattern. It makes a real
HTTP POST to {LITELLM_BASE_URL}/reload/model_cost_map with the
master key as a bearer token, per LiteLLM's real documented
endpoint shape. Handle the CURRENT real state honestly: if
LITELLM_BASE_URL is unset or unreachable (expected right now,
pre-Phase-A), the action must fail LOUD with a clear, specific
message — never silently succeed or silently no-op.

=== TASK 3: WIRE — a callable workflow, correct autonomy tier ===
Build a real, minimal BPMN workflow (or ServiceTask addable to
one) that invokes this action, assigned to the LEAST restrictive
tier Task 1b's real definitions support — justify this
explicitly against what that tier actually permits (no money
movement, no external communication, no member data access,
trivially safe to re-run). If Task 1c found a real scheduling
mechanism, wire this workflow to it (e.g. a real recurring
trigger) as the natural, valuable version of this feature. If
Task 1c found NO real scheduling mechanism exists yet, build the
workflow as a manually-triggerable admin action instead, and
report the scheduling gap explicitly as a real, separate,
tracked follow-up — do not build fake scheduling to paper over
this.

=== TASK 4: REAL PROOF, HONEST ABOUT THE LITELLM DEPENDENCY ===
  - The action is correctly registered and appears in whatever
    admin surface lists available actions.
  - Invoked against NO LiteLLM instance (the real current state):
    fails loud with a clear, specific, actionable error message —
    proven, not assumed.
  - Invoked against a LOCAL STAND-IN HTTP server simulating
    LiteLLM's real /reload/model_cost_map response shape:
    succeeds, and the workflow's own execution log correctly
    records success.
  - The autonomy tier assignment is proven against the real
    tier-enforcement mechanism (i.e. it genuinely runs without
    requiring an approval step no other action at that tier
    requires).
  - If a real scheduling mechanism was found and wired: prove a
    scheduled invocation actually fires. If not: this assertion
    is honestly reported as not applicable, with the gap named.

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md: record this action as built and
registered, explicitly note it is BLOCKED from real end-to-end
use until LiteLLM Phase A completes, and record Task 1c's honest
finding on scheduled-workflow readiness.

=== VERIFICATION: apps/api/scripts/verify_litellmreloadaction.py
===
Pass/fail only. No interactive prompts.

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] The action is registered and discoverable via the real
      admin surface
  [Y] Invoking it with no LiteLLM endpoint configured fails loud
      with a specific, actionable message — not a silent no-op
  [Y] Invoking it against a local stand-in server succeeds and is
      correctly logged
  [Y] The autonomy tier assignment is real and justified against
      Task 1b's actual tier definitions, proven to require no
      unnecessary approval step
  [Y] Report Task 1c's real scheduling finding, and whether Task 3
      wired real scheduling or a manual trigger, honestly
  [Y] Teardown: zero leftover rows
