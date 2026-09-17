LITELLM PHASE G — BUDGETS.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

Phases A-F and the availability work are complete and merged. Spend
is now attributable three ways (D1b): org_owned_key,
platform_on_behalf_of_org, hollisworks_platform — tagged per call
in LiteLLM_SpendLogs. Budgets you cannot attribute are not
enforceable, which is why this comes after D1b and not before.

SCOPE: a per-org spend budget with a warning threshold and
graceful degradation at the cap, plus a separate Hollisworks-wide
ceiling.

Facts that change decisions:
- AT THE CAP, DEGRADE — DO NOT HARD-STOP. Per the design doc, an
  org at its cap falls back to its safe model, it does not lose AI
  entirely. A hard stop turns a billing threshold into an outage.
- app_service CANNOT read the litellm schema (permission denied,
  correct least-privilege). Spend data is reachable only via the
  proxy's admin API (GET /spend/logs). This is the sprint's
  central design constraint: enforcement on every call cannot
  depend on a synchronous admin-API round trip. Task 1 must decide
  how spend is actually known at call time — a cached running
  total, a periodic sync, LiteLLM's own native budget mechanism,
  or something else — and justify the choice.
- LITELLM HAS NATIVE BUDGETS (max_budget on keys and teams).
  Probe whether they are usable here. D1a established that per-org
  isolation is by DEPLOYMENT, not by virtual key — so native
  key/team budgets may not map onto this architecture at all.
  Report honestly either way; do not force a fit.
- ALERTS REUSE THE EXISTING PATH.
  services/workflow_todos.py has create_credential_failure_alerts
  (D1c) and create_model_availability_alerts (availability) as
  established siblings: manage_org_settings holders, _upsert_todo,
  _record_undelivered_alert for zero recipients. Add a sibling; do
  NOT build a fourth notification mechanism.
- The real Hollisworks org has ZERO manage_org_settings holders — a
  live case for the zero-recipient path.
- platform_ai_controls (Phase F) is the established home for
  platform-scoped AI controls. The Hollisworks-wide ceiling likely
  belongs there rather than in a new table — confirm.
- RLS POLICIES ARE PER-OPERATION, AND CONTEXT MUST BE SET. Both
  failure modes cost real time this session and both surface as
  "row not found" rather than "permission denied". If this sprint
  adds a table or needs UPDATE on one, confirm a policy exists for
  that operation AND that the calling connection sets the context
  it checks.
- NEVER let code assert a cause it cannot know. Zero affected rows
  does not distinguish a missing row from an RLS-blocked write.
- Voyage free tier: 3 req/min. Pace real embedding calls ~65s
  apart.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Probe LiteLLM's native budget support live — what
      max_budget actually applies to, and whether it can map onto
      per-org DEPLOYMENT isolation rather than virtual keys.
      Report plainly if it cannot.
  1b. DECIDE and JUSTIFY how spend is known at call time given
      app_service cannot read the litellm schema. State the real
      staleness window of whatever you choose and why it is
      acceptable — a budget enforced on data an hour old is a real
      design decision, not an implementation detail.
  1c. Confirm the exact shape of GET /spend/logs' response and
      which fields let you total spend per org — D1b's tags are
      the attribution, confirm they are queryable in aggregate and
      not just per row.
  1d. Where the per-org budget and the Hollisworks ceiling should
      live, per the established platform-scope and org_settings
      conventions.

=== TASK 2: THE BUDGETS ===
Per 1d: a per-org monthly budget with a warning threshold, and a
separate Hollisworks-wide ceiling. Org admins set their own org's
budget; only a Hollisworks super-admin sets the platform ceiling.
Default for every existing org: no budget — nothing changes until
one is deliberately set.

=== TASK 3: WARNING AND DEGRADATION ===
- Crossing the warning threshold alerts that org's
  manage_org_settings holders, once per period, not once per call.
- At the cap, calls degrade to the org's safe model and the org is
  alerted. They do NOT fail.
- At the Hollisworks ceiling, the equivalent applies platform-wide.

=== TASK 4: PROOF ===
  - Report Task 1's four findings, with 1b's staleness
    justification.
  - An org with NO budget is completely unaffected — no
    regression. Every existing org is in this state.
  - Crossing the warning threshold alerts exactly once, not once
    per call — prove with multiple calls after the crossing.
  - At the cap, a real call SUCCEEDS on the safe model — prove
    from ai_decision_log's model_used, not from config. It must
    not fail.
  - The cap alert reaches the org's manage_org_settings holders.
  - A zero-recipient org leaves the audit_log row — prove against
    the real Hollisworks org.
  - The Hollisworks ceiling is enforced independently of any org's
    own budget.
  - org_admin can set its own org's budget but gets 403 on the
    platform ceiling; super_admin gets 200 on the identical
    request.
  - Cross-org: org A's spend does not count against org B's
    budget, and org A's alerts do not reach org B.

=== TASK 5: STATUS ===
Update docs/PROJECT_STATUS.md and
docs/LITELLM_INTEGRATION_DESIGN_V1.md §8. Record 1b's staleness
decision prominently — it is the load-bearing compromise in this
design.

=== VERIFY: apps/api/scripts/verify_litellmphaseg.py ===
Pass/fail only. MUST print a 'TOTAL: N PASS, M FAIL' line and exit
non-zero on failure. Must hydrate its own secrets from Doppler over
HTTPS at startup (verify_litellmphasef.py is the pattern —
run_sprint.sh's Step 3 does not use doppler run --). Never print a
credential value. Set RLS context on every connection that touches
an RLS-protected table.

  [Y] Task 1's four findings reported, staleness justified
  [Y] An org with no budget is unaffected — no regression
  [Y] Warning alerts exactly once, not per call
  [Y] At the cap, a real call SUCCEEDS on the safe model, proven
      from ai_decision_log
  [Y] The cap alert reaches manage_org_settings holders
  [Y] Zero-recipient org leaves the audit_log row
  [Y] The Hollisworks ceiling enforces independently
  [Y] org_admin 403 on the platform ceiling; super_admin 200 on
      the identical request
  [Y] Cross-org: spend and alerts both isolated
  [Y] Teardown: zero leftover rows, no budgets left set, proxy
      deployment set unchanged
