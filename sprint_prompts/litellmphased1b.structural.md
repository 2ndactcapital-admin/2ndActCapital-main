LITELLM PHASE D1b — ROUTING & SPEND ATTRIBUTION. Backend only.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

D1a is merged (58/58). An org can now store its own provider key:
ai.credential_source.<provider> ('org' | 'platform', default
'platform') drives provisioning of a dedicated per-org deployment
on the proxy, holding a literal api_key that LiteLLM encrypts at
rest and never returns.

SCOPE: make calls actually USE the right deployment, and make spend
attributable. Credential-failure alerting is the NEXT sprint — do
not build it.

Facts that change decisions:
- extraction.py currently sends NO metadata, so every call lands in
  LiteLLM_SpendLogs attributed to the master key with a null team.
  "Which org incurred this, against whose key" is unanswerable
  today. That is what this sprint fixes.
- Deployment names are INTERNAL. An org must never see one.
- app_service cannot read the litellm schema. Use the proxy's admin
  API (GET /spend/logs) — never query the schema directly.
- ai_decision_log reads/writes REQUIRE set_rls_context, or you get
  SILENT denials: zero rows, no error.
- Voyage is on the free tier: 3 req/min, 10K tokens/min. Pace any
  real embedding calls ~65s apart; 25s was proven insufficient.
- Every existing org is 'platform'. Their behaviour must not change.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The real function that resolves which model/deployment a call
      uses today, and exactly where per-org resolution slots in.
  1b. The real metadata fields LiteLLM accepts on /v1/messages and
      /v1/embeddings, and which of them actually land in
      LiteLLM_SpendLogs columns. Probe it — do not assume field
      names.
  1c. Whether the text path and the embedding path share a metadata
      mechanism or need separate work.

=== TASK 2: ROUTING ===
Extend Task 1a's resolver so a call uses the org's own deployment
when that org is configured 'org' for the relevant provider, and
the platform deployment when 'platform'. The logical model name the
caller asks for must NOT change — only which deployment serves it.

=== TASK 3: ATTRIBUTION ===
Attach real metadata (per 1b's actual field names) to every
outgoing LiteLLM request, text and embedding both, so spend logs
can answer which org incurred a call and against whose key.
Platform-key usage on behalf of an org must be distinguishable from
Hollisworks' own usage — that is the point.

=== TASK 4: PROOF ===
  - Report Task 1's three findings.
  - A 'platform' org's real call still succeeds end-to-end and
    still lands in the spend log. This is the no-regression proof
    and matters most — every existing org is in this state.
  - An 'org'-configured org's real call is served by ITS OWN
    deployment — proven from the spend log's own record of which
    deployment ran it, not inferred from config.
  - The spend log now carries org attribution where it was null
    before — prove the before/after difference, not just presence.
  - Platform-key-on-behalf-of-org is distinguishable from
    Hollisworks' own usage in the log.
  - Embedding calls carry attribution too, not just text.
  - No deployment name appears in any org-facing response — grep
    the raw payload text.
  - Cross-org: org A's call never routes to org B's deployment.

=== TASK 5: STATUS ===
Update docs/PROJECT_STATUS.md and
docs/LITELLM_INTEGRATION_DESIGN_V1.md. Next: D1c
(credential-failure alerting).

=== VERIFY: apps/api/scripts/verify_litellmphased1b.py ===
Pass/fail only. Must hydrate its own secrets from Doppler over
HTTPS at startup (verify_litellmphased1a.py is the pattern —
run_sprint.sh's Step 3 does not use doppler run --). Never print a
credential value.

  [Y] Task 1's three findings reported
  [Y] A 'platform' org's call is unchanged — no regression
  [Y] An 'org' org's call is served by its own deployment, proven
      from the spend log
  [Y] Spend log carries org attribution where it was null before
  [Y] Platform-on-behalf-of-org distinguishable from Hollisworks'
      own usage
  [Y] Embedding calls carry attribution too
  [Y] No deployment name in any org-facing response
  [Y] Cross-org: no call crosses to another org's deployment
  [Y] Teardown: zero leftover rows AND zero leftover test
      deployments on the live proxy
