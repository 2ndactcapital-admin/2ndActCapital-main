LITELLM PHASE D1c — CREDENTIAL-FAILURE ALERTING. Backend only.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

D1a (58/58) and D1b (54/54) are merged. An org can store its own
provider key, a dedicated deployment is provisioned for it, calls
route to the right deployment, and spend is attributed three ways:
org_owned_key / platform_on_behalf_of_org / hollisworks_platform.

SCOPE: what happens when an ORG'S OWN credential is bad. That is
all.

Facts that change decisions:
- A BROKEN ORG KEY MUST FAIL LOUDLY. It must NEVER silently fall
  back to the Hollisworks platform key — an org's credential
  problem must never quietly become Hollisworks' bill. Platform-key
  use is a deliberate configuration state, never an error path.
- MODEL UNAVAILABILITY IS DIFFERENT and still falls back via the
  existing safe-model hierarchy (task model -> org safe -> Hollis
  safe). Do not conflate the two. A credential failure is a config
  problem the org must fix; an unreachable model is a routing
  problem the platform routes around.
- org_admin is a REAL role holding manage_org_settings, and alert
  recipients resolve BY PERMISSION (orgadminrole.structural,
  merged). Reuse the existing member_todos path — do not invent a
  second notification mechanism.
- An org with NO resolvable recipient must leave a findable
  audit_log row rather than failing silently. That pattern already
  exists (action='workflow_alert_undelivered') — follow its shape.
- The real Hollisworks org genuinely has ZERO manage_org_settings
  holders, so it is a real live case to prove the zero-recipient
  path against.
- ai_decision_log reads/writes REQUIRE set_rls_context, or you get
  SILENT denials: zero rows, no error.
- app_service cannot read the litellm schema. Use the proxy's admin
  API.
- Voyage free tier: 3 req/min. Pace real embedding calls ~65s
  apart.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. What a real invalid-credential failure actually looks like
      coming back from the proxy — the real exception type, status
      code and message shape. Probe it with a deliberately bad key
      on a throwaway deployment; do not assume.
  1b. How that is currently distinguishable (or not) from a
      model-unavailable failure at the point where _execute_chain
      decides whether to walk the fallback chain.
  1c. create_held_run_alerts' real signature and recipient rule,
      and whether it can be reused directly or needs a sibling.

=== TASK 2: DISTINGUISH AND FAIL LOUDLY ===
Per 1b: when a call fails specifically because an org's OWN
credential is invalid, fail with a specific, actionable error
naming the provider. Do NOT walk the fallback chain onto a platform
deployment. Model-unavailable failures must keep falling back
exactly as they do today.

=== TASK 3: ALERT ===
Raise an alert to that org's manage_org_settings holders via the
existing member_todos path. Zero resolvable recipients leaves the
audit_log record instead.

=== TASK 4: PROOF ===
  - Report Task 1's three findings.
  - An invalid ORG credential fails loudly, names the provider, and
    produces NO platform-attributed spend row for that attempt —
    prove the ABSENCE, after the full flush window, not just that
    an error came back.
  - That failure creates a real member_todos row for the org's
    manage_org_settings holders — read the rows back.
  - An org with zero resolvable recipients leaves the audit_log row
    instead — prove against the real Hollisworks org.
  - A model-unavailable failure STILL falls back via the safe-model
    hierarchy — proven distinct from the credential path, same
    test run.
  - A 'platform'-configured org is completely unaffected — no
    regression. Every existing org is in this state.
  - Cross-org: org A's credential failure never alerts org B's
    admins.

=== TASK 5: STATUS ===
Update docs/PROJECT_STATUS.md and
docs/LITELLM_INTEGRATION_DESIGN_V1.md. Phase D1 complete. Next: D2
(model pick-list UI).

=== VERIFY: apps/api/scripts/verify_litellmphased1c.py ===
Pass/fail only. Must hydrate its own secrets from Doppler over
HTTPS at startup (verify_litellmphased1b.py is the pattern —
run_sprint.sh's Step 3 does not use doppler run --). Never print a
credential value.

  [Y] Task 1's three findings reported
  [Y] Invalid org credential fails loudly and names the provider
  [Y] NO platform-attributed spend row for that attempt, proven by
      absence after the flush window
  [Y] A real member_todos alert reaches the org's
      manage_org_settings holders
  [Y] Zero-recipient org leaves the audit_log row instead, proven
      against the real Hollisworks org
  [Y] Model-unavailable still falls back, distinct from the
      credential path
  [Y] A 'platform' org is unaffected — no regression
  [Y] Cross-org: no alert crosses orgs
  [Y] Teardown: zero leftover rows AND zero leftover test
      deployments on the live proxy
