LITELLM PHASE F — FORCE-ANTHROPIC EMERGENCY BYPASS.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

Phases A-E and the availability work are complete and merged. Every
production AI call — text and embeddings — routes through the
LiteLLM proxy. That is exactly why this sprint exists: a single
proxy is now a single point of failure for every AI feature in the
platform.

SCOPE: a Hollisworks-level control that bypasses LiteLLM entirely
and calls Anthropic directly. That is all.

Facts that change decisions:
- THE MECHANISM ALREADY EXISTS. LITELLM_ROUTING_DISABLED=1 is a
  real, proven rollback (litellmphaseb, 25/25 — proven by genuine
  ABSENCE of a LiteLLM spend-log row after the full flush window,
  not merely by a result coming back). This sprint makes it an
  admin control rather than an env var someone must remember, and
  makes it observable. Do NOT build a second bypass mechanism.
- THIS IS A BLUNT INSTRUMENT, DELIBERATELY. Per the design doc, it
  is a THIRD tier above the two-tier safe-model hierarchy: every
  task goes to one fixed Anthropic model, full stop. Simple and
  predictable beats clever during an incident. Do not make it
  per-task or per-org conditional.
- EMBEDDINGS CANNOT BYPASS. Voyage is not Anthropic — there is no
  direct-Anthropic path for an embedding call. Decide and state
  plainly what happens to the embedding path when the bypass is
  active: it either keeps using LiteLLM (likely correct — the
  proxy may be fine for embeddings even if text is degraded) or
  fails loudly. Do NOT silently return empty vectors.
- COST ATTRIBUTION GOES DARK while active. Bypassed calls never
  touch LiteLLM's spend log. ai_decision_log survives independently
  of LiteLLM — write there so there is still SOME record of what
  ran during the incident.
- PLATFORM-SCOPED, not per-org: owner_scope='platform' with NULL
  org_id (CLAUDE.md Rule 6), not the default org id as a stand-in.
- Super-admin only. An org admin must never be able to flip this.
- RLS policies are PER-OPERATION. If this sprint adds a table or
  needs UPDATE on an existing one, confirm a policy exists for
  that operation — a missing UPDATE policy silently matches zero
  rows and surfaces as "row not found" (confirmed live this
  session on platform_model_catalog).
- app_service cannot read the litellm schema. Use the proxy's
  admin API.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The real current LITELLM_ROUTING_DISABLED check — exact file,
      exact function, exactly where in _execute_chain it branches,
      and whether the embedding path has an equivalent.
  1b. Whether a platform-scoped org_settings key can drive this
      with no schema change, and the concrete key shape. Report how
      platform-scope resolution actually works today.
  1c. What the direct-Anthropic path uses for a model name — it
      cannot be a proxy deployment name like 'claude-haiku', it
      needs a real upstream Anthropic model id. Report exactly what
      the existing rollback path sends today.

=== TASK 2: THE CONTROL ===
Per 1b: a platform-scoped setting a Hollisworks super-admin can
toggle, driving the existing bypass. Default OFF. No org's
behaviour changes until it is deliberately turned on.

=== TASK 3: OBSERVABILITY ===
While the bypass is active, every call still writes a real
ai_decision_log row, clearly marked as bypassed, so an incident
leaves a queryable record even though LiteLLM's spend log is dark.

=== TASK 4: PROOF ===
  - Report Task 1's three findings, including the embedding
    decision and its justification.
  - Bypass OFF: a real call routes through LiteLLM and lands in
    its spend log — unchanged, no regression. This is the state the
    platform is in.
  - Bypass ON: the same real call succeeds via direct Anthropic,
    and produces NO new LiteLLM spend-log row — prove the ABSENCE
    after the full flush window, not just that a result came back.
  - Bypass ON: ai_decision_log records the call, marked as
    bypassed.
  - Turning it back OFF restores LiteLLM routing — prove with a
    real call and a real spend-log row.
  - The embedding path behaves as Task 1's decision states —
    proven, whichever way it was decided.
  - super_admin can toggle it; org_admin gets 403 on the identical
    request.
  - The setting is genuinely platform-scoped, not stored against
    the default org.

=== TASK 5: STATUS ===
Update docs/PROJECT_STATUS.md and
docs/LITELLM_INTEGRATION_DESIGN_V1.md §7.5. Record the embedding
decision prominently — it is the non-obvious part of this feature.

=== VERIFY: apps/api/scripts/verify_litellmphasef.py ===
Pass/fail only. MUST print a 'TOTAL: N PASS, M FAIL' line and exit
non-zero on failure. Must hydrate its own secrets from Doppler over
HTTPS at startup (verify_litellmavailability.py is the pattern —
run_sprint.sh's Step 3 does not use doppler run --). Never print a
credential value.

  [Y] Task 1's three findings reported, embedding decision
      justified
  [Y] Bypass OFF: unchanged, lands in LiteLLM's spend log
  [Y] Bypass ON: succeeds via direct Anthropic
  [Y] Bypass ON: NO new LiteLLM spend-log row, proven by absence
      after the flush window
  [Y] Bypass ON: ai_decision_log records it, marked bypassed
  [Y] Toggling back OFF restores LiteLLM routing, proven
  [Y] The embedding path behaves as decided
  [Y] super_admin toggles; org_admin 403 on the identical request
  [Y] The setting is genuinely platform-scoped
  [Y] Teardown: bypass OFF, zero leftover rows, proxy deployment
      set unchanged
