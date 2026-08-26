LITELLM PHASE B — ROUTE TEXT CALLS THROUGH LITELLM. 6 tasks +
verification. Phase A is COMPLETE and verified end-to-end — see
docs/LITELLM_INTEGRATION_DESIGN_V1.md §13.5 for the real, hard-won
deployment record. LiteLLM is live at a real Render service,
schema-migrated, proven to persist virtual keys.

CONFIRMED REAL FACTS FROM DISCOVERY (litellmdiscovery.lowrisk),
DO NOT RE-DERIVE:
- All 16 production Claude call sites are in
  services/extraction.py, through ONE function. The Anthropic SDK
  is imported in exactly ONE place (extraction.py:231). Zero
  hardcoded model strings at any call site.
- ai.model.provider and ai.model.fallback are DEAD keys — zero
  consumers. The real, live mechanism is ai.model.fallback_chain
  (model-level, not provider-level).
- ai_decision_log: 12 columns, RLS on, 1 writer (_write_ai_
  decision in extraction.py), 1 reader, 0 endpoints. Confirmed by
  schedulerdiscovery.lowrisk (a LATER sprint) to still have no
  run/step correlation column — this sprint does not need to add
  one, since no workflow run step invokes AI (confirmed same
  sprint).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

*** CRITICAL: this is the single most load-bearing code path in
the entire platform — every extraction, classification, and
generation call in production goes through this one function.
Treat with the same care as the tenant-boundary/Auth0 work. A
real, working ROLLBACK PATH must exist and be proven, not just
described. ***

STANDING RULES: no interactive prompts. Never print a real secret
value (LITELLM_MASTER_KEY, LITELLM_BASE_URL if it contains
anything sensitive) in any log or commit.

=== TASK 1: DISCOVER — re-confirm before touching the chokepoint
===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Re-read the REAL current services/extraction.py — confirm
      the exact current function signature(s), the exact current
      Anthropic SDK call, and exactly how ai.model.fallback_chain
      is currently read and iterated. Confirm nothing has changed
      since the original discovery sprint.
  1b. Confirm LiteLLM's real, current OpenAI-compatible endpoint
      shape (POST /v1/chat/completions or /chat/completions —
      confirm the exact real path against the live deployed
      instance, do not assume) and confirm how a model name should
      be passed (per Phase A's real deployment, using the
      provider/model_name convention confirmed earlier in the
      design work).
  1c. Confirm LITELLM_BASE_URL and LITELLM_MASTER_KEY are both
      present in Doppler's prd config right now (LITELLM_BASE_URL
      was confirmed ABSENT by a later discovery sprint — verify
      whether this has been fixed; if still absent, this sprint
      must add it, pointing at the real, live Render URL).
  1d. Confirm the real current _write_ai_decision function's
      exact schema — this sprint's LiteLLM-routed calls must
      continue writing to ai_decision_log in the SAME shape,
      unchanged, so nothing downstream (verify scripts, any admin
      surface) breaks.

=== TASK 2: FIX — LITELLM_BASE_URL, if Task 1c found it missing
===
Add LITELLM_BASE_URL to Doppler's prd config, pointing at the
real, confirmed-live Render URL for the LiteLLM service. Report
this explicitly as a real gap closed, not a new feature.

=== TASK 3: THE ROUTING CHANGE — minimal, at the one chokepoint
===
Modify services/extraction.py's single call point to route
through LiteLLM's real endpoint (Task 1b) instead of the Anthropic
SDK directly, using LITELLM_MASTER_KEY as the bearer token. The
REST OF THE FUNCTION — the fallback chain iteration, the retry
logic, ai_decision_log writing, error handling — changes as
LITTLE as possible. This is a transport-layer swap, not a
rewrite.

=== TASK 4: THE ROLLBACK PATH — real, not theoretical ===
Build a real, simple toggle (an environment variable or
org_settings key — confirm the more appropriate real convention
per Task 1's findings) that, when set, reverts extraction.py to
calling Anthropic directly, bypassing LiteLLM entirely. This is
NOT the same as the design doc's §7.5 force_anthropic feature
(that is a future, UI-driven, Hollis-admin-facing capability) —
this is a blunt, ops-level escape hatch for THIS sprint's own
deployment, proving the platform is never left with no way to
generate text if LiteLLM has a problem.

=== TASK 5: REAL PROOF ===
  - A real extraction call, run end-to-end against the LIVE
    LiteLLM instance, produces a real, correct result — not
    mocked, not stubbed.
  - The SAME call's cost/success is written to ai_decision_log in
    the EXACT SAME shape as before this sprint — proven by
    comparing column-by-column against a pre-existing row.
  - LiteLLM's OWN spend log (LiteLLM_SpendLogs, in the litellm
    schema) ALSO shows this call — proving the dual-write/dual-
    visibility actually works, not just that our own log still
    works.
  - The fallback chain genuinely still functions: force the
    first model in a real org's ai.model.fallback_chain to fail
    (e.g. an invalid model name for one entry) and confirm the
    NEXT model in the chain is tried, exactly as before this
    sprint, now via LiteLLM.
  - The rollback path (Task 4) genuinely works: with it engaged,
    the SAME call succeeds via direct Anthropic, with LiteLLM
    never contacted — proven by confirming zero new rows in
    LiteLLM's own spend log for that call.
  - A deliberately wrong LITELLM_MASTER_KEY produces a clear,
    actionable failure — not a silent fallback, not a confusing
    generic error.

=== TASK 6: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md and
docs/LITELLM_INTEGRATION_DESIGN_V1.md's phasing table: Phase B
complete, LITELLM_BASE_URL gap closed if it existed, rollback
path documented as a real, tested capability.

=== VERIFICATION: apps/api/scripts/verify_litellmphaseb.py ===
Pass/fail only. No interactive prompts. Never print
LITELLM_MASTER_KEY.

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] A real extraction call succeeds end-to-end via the live
      LiteLLM instance
  [Y] ai_decision_log's shape is unchanged, proven column-by-
      column
  [Y] LiteLLM's own spend log shows the same call
  [Y] The fallback chain still works, now via LiteLLM, proven
      with a real forced-failure case
  [Y] The rollback path genuinely bypasses LiteLLM when engaged —
      proven by absence in LiteLLM's spend log, not just presence
      of a result
  [Y] A wrong master key fails loud and specifically
  [Y] Teardown: zero leftover rows
