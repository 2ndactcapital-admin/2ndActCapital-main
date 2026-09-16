LITELLM — SEEDED CHAIN MISMATCH + VERIFY SCRIPT FIX.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

Two D2 follow-ups, both small.

THE REAL BUG (found by verify_litellmphased2.py, live-probed):
org_settings' stored model names are NOT callable against the live
proxy. Confirmed live right now:

  ai.model.assistant            = 'claude-sonnet-4-6'
  ai.model.default              = 'claude-haiku-4-5-20251001'
  ai.model.document_classifier  = 'claude-haiku-4-5-20251001'
  ai.model.fallback_chain       = ['claude-haiku-4-5-20251001']

The proxy has exactly two registered deployments: 'claude-sonnet'
and 'voyage-3.5'. Every name above returns LiteLLM's own 'Invalid
model name' HTTP 400. HAIKU HAS NO DEPLOYMENT AT ALL.

Calls work today only because callers pass an explicit model=
override. Anything that actually fell through to the seeded chain
would fail. This is a latent failure under every AI call.

Facts that change decisions:
- There is a REAL COST CONSEQUENCE. Haiku is roughly 20x cheaper
  than Sonnet. Collapsing document_classifier/default onto Sonnet
  because Haiku has no deployment would quietly multiply the cost
  of every classification call. Do not do that silently — if it is
  the right answer, say so explicitly and justify it.
- ai.model.provider and ai.model.fallback are DEAD KEYS with zero
  consumers (confirmed by earlier discovery). Do not preserve them
  out of caution; report them as dead.
- D1b's resolver translates a LOGICAL model name to a DEPLOYMENT.
  One legitimate fix is making that translation handle versioned
  Anthropic model ids; another is aligning the stored names to the
  proxy's registered names; another is registering deployments
  that match. Task 1 decides which, on evidence.
- D2's model catalog is now live and stores model_id values —
  whatever naming is chosen must be consistent with what the
  catalog holds, or the picker and the resolver will disagree.
- app_service cannot read the litellm schema. Use the proxy's
  admin API.

=== TASK 1: DISCOVER AND DECIDE ===
Report findings, then continue immediately.
  1a. Confirm live which model names are actually callable on the
      proxy, and what the registered deployments' real model_name
      and upstream litellm_params.model values are.
  1b. Confirm what D2's platform_model_catalog actually stores as
      model_id — the versioned Anthropic ids, the proxy's
      deployment names, or something else. The catalog, the
      settings and the proxy must agree.
  1c. Report every real consumer of these settings keys, so the
      blast radius of a rename is known before anything changes.
  1d. DECIDE and JUSTIFY: align settings to deployment names,
      register deployments matching the settings, or translate in
      the resolver. State the cost consequence of your choice
      explicitly, especially for Haiku.

=== TASK 2: FIX THE MISMATCH ===
Implement Task 1d's decision. Whatever is chosen, the seeded
default chain must ACTUALLY WORK end to end with no explicit
model= override.

=== TASK 3: FIX THE VERIFY SCRIPT ===
apps/api/scripts/verify_litellmphased2.py has two real defects: it
prints no 'TOTAL: N PASS, M FAIL' summary line, and it does not
exit non-zero on failure — which is why run_sprint.sh pushed a run
that had failing assertions. Fix both, matching the pattern every
other verify script in this repo uses. Re-run it afterwards and
confirm it now reports a real total and exits correctly.

=== TASK 4: PROOF ===
  - Report Task 1's four findings, including the cost
    justification from 1d.
  - A real AI call using the SEEDED default chain — no explicit
    model= override — succeeds end to end.
  - The fallback chain genuinely walks on a forced first-model
    failure, still with no override.
  - The document_classifier task resolves to whatever 1d decided,
    and that model is genuinely callable.
  - D2's catalog, org_settings, and the proxy all agree on naming
    — prove it, do not assert it.
  - verify_litellmphased2.py now prints a real total and exits
    non-zero when an assertion fails — prove by forcing a failure.
  - No regression: every existing AI call path still works.

=== TASK 5: STATUS ===
Update docs/PROJECT_STATUS.md and
docs/LITELLM_INTEGRATION_DESIGN_V1.md. Record the naming
convention decided in 1d prominently — every future provider
integration depends on it.

=== VERIFY: apps/api/scripts/verify_litellmseedfix.py ===
Pass/fail only. Must print a TOTAL line and exit non-zero on
failure. Must hydrate its own secrets from Doppler over HTTPS at
startup (verify_litellmphased1c.py is the pattern —
run_sprint.sh's Step 3 does not use doppler run --). Never print a
credential value.

  [Y] Task 1's four findings reported, with the cost justification
  [Y] A real call on the seeded chain succeeds with NO model=
      override
  [Y] The fallback chain walks on a forced failure, no override
  [Y] document_classifier resolves to a genuinely callable model
  [Y] Catalog, settings and proxy agree on naming — proven
  [Y] verify_litellmphased2.py prints a total and exits non-zero
      on failure, proven by forcing one
  [Y] No regression on existing call paths
  [Y] Teardown: zero leftover rows AND the proxy's deployment set
      is exactly as intended (state what it should be and why)
