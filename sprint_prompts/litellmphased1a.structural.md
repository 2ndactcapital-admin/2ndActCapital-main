LITELLM PHASE D1a — PER-ORG CREDENTIAL STORAGE. Backend only.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

Full context: docs/LITELLM_PHASE_D_DISCOVERY.md. Read it in Task 1.

SCOPE: can an org store its own provider key, and does a dedicated
deployment get created for it? That is all. Routing, spend
attribution and failure alerting are LATER sprints — do not build
them.

Facts that change decisions:
- LiteLLM Virtual Keys CANNOT carry provider credentials (probed,
  confirmed). Per-deployment api_key CAN. So one deployment per
  (provider x credential-owner) is the mechanism.
- Existing deployments pass credentials as os.environ/<NAME>
  indirection. That works for platform keys because the value is
  in the proxy's own environment. A per-org key is NOT — Task 1
  must establish how it can be passed instead.
- Deployment names are INTERNAL. An org must never see one.
- app_service cannot read the litellm schema. Use the proxy's
  admin API.
- Default for every existing org must remain platform credentials.
  No org's behaviour changes in this sprint.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Can POST /model/new accept a literal key value (encrypted at
      rest with LITELLM_SALT_KEY), or only os.environ/ indirection?
      Probe the live proxy. This is the sprint's central question —
      if literal values are not accepted, report that plainly and
      stop rather than inventing a workaround.
  1b. Can org_settings express a per-provider credential source
      ('org' | 'platform') with no schema change? Report the
      concrete key shape.
  1c. Confirm the exact POST /model/new payload that created the
      existing claude-sonnet and voyage-3.5 deployments.

=== TASK 2: THE SETTING ===
Per 1b: establish the per-provider credential source per org.
Default 'platform' for every existing org.

=== TASK 3: PROVISIONING ===
When an org supplies a provider key, create a dedicated deployment
for that (provider x org) pair and record the mapping. Removing the
key must remove or disable that deployment — a stale deployment
holding a revoked key is a real hazard.

=== TASK 4: PROOF ===
  - Report Task 1's three findings.
  - Every existing org still resolves to 'platform' — no
    behavioural change.
  - Supplying a test org key creates a real deployment, confirmed
    by reading the proxy's admin API back.
  - Removing it removes/disables that deployment, likewise
    confirmed.
  - The org-facing response for that org contains no deployment
    name — grep the real payload, confirm zero matches.
  - Cross-org: org A's deployment is not visible or usable by
    org B.

=== TASK 5: STATUS ===
Update docs/PROJECT_STATUS.md and
docs/LITELLM_INTEGRATION_DESIGN_V1.md. Record Task 1a's answer
prominently — every later phase depends on it.

=== VERIFY: apps/api/scripts/verify_litellmphased1a.py ===
Pass/fail only. Must hydrate its own secrets from Doppler over
HTTPS at startup (verify_orgadminrole.py is the pattern —
run_sprint.sh's Step 3 does not use doppler run --). Never print a
credential value.

  [Y] Task 1's three findings reported
  [Y] Existing orgs unchanged, still 'platform'
  [Y] A test org key creates a real deployment, confirmed via API
  [Y] Removing it removes/disables the deployment, confirmed
  [Y] No deployment name in any org-facing response
  [Y] Cross-org isolation
  [Y] Teardown: zero leftover rows AND zero leftover test
      deployments on the live proxy
