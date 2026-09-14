LITELLM PHASE B — COMPLETION PROOF. 3 tasks + verification.
litellmphaseb.structural (68/68) proved the transport layer fully
— chain walking, rollback, dual-logging, loud auth failures — but
5 assertions were correctly BLOCKED on real external gaps. All
three are now CONFIRMED RESOLVED as of this session:

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- B1 CLOSED: a real model deployment now exists on the live proxy.
  GET /v1/models returns claude-sonnet (was an empty list).
  Confirmed persisted in Postgres: litellm."LiteLLM_ProxyModelTable"
  holds model_id 7fcd845c-0a47-413c-b77d-3da88d984425, model_name
  'claude-sonnet', routing to anthropic/claude-sonnet-4-6. This
  survives restarts — it is a real DB row, not in-memory config.
- B2 CLOSED: LITELLM_MASTER_KEY now authenticates as PROXY_ADMIN.
  POST /model/new succeeded. The earlier role=internal_user failure
  traced to Doppler's sync overwriting the LiteLLM service's
  DATABASE_URL; fixed by a dedicated Doppler branch config
  (lite_llm) syncing only to hollisworks-litellm.
- B3 CLOSED: ANTHROPIC_API_KEY exists in Doppler and is real.
- The proxy's connection uses the litellm_service role (least
  privilege, rolbypassrls=false), schema litellm, with
  ?schema=litellm&pgbouncer=true&sslmode=require. The role also
  has a role-level search_path of 'litellm, public' to catch
  LiteLLM's own unqualified raw SQL.
- NOTE: LiteLLM's /v1/models reports "owned_by": "openai" for
  every model — that is a hardcoded default in its
  OpenAI-compatible response shape, NOT a routing fact. Do not
  treat it as evidence of a misconfiguration.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: never print LITELLM_MASTER_KEY or
ANTHROPIC_API_KEY, or any value that could contain one. A Doppler
command's own confirmation table has leaked a live credential in
this project before — pipe through a redaction step or use
name-only commands.

=== TASK 1: CONFIRM THE THREE BLOCKERS ARE GENUINELY RESOLVED ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. GET /v1/models against the live proxy — confirm at least one
      real model deployment, by name.
  1b. Confirm LITELLM_MASTER_KEY genuinely authenticates as
      PROXY_ADMIN via a real, low-stakes admin call (GET
      /model/info returning real data, not a 500).
  1c. Confirm ANTHROPIC_API_KEY is present and valid — a minimal,
      real Anthropic call succeeding INDEPENDENTLY of LiteLLM.
If ANY is still genuinely unresolved, STOP and report exactly
which, rather than attempting a workaround.

=== TASK 2: THE REAL, FULL END-TO-END CALL ===
Run the same real extraction call litellmphaseb.structural's Task
5 attempted (the one that returned a live BadRequestError naming
an invalid model). This time, using the real configured model
name, prove:
  - A genuine 200 with real generated text comes back through the
    full chain (call_claude_text -> LiteLLM -> Anthropic).
  - ai_decision_log records success=true with a real cost_usd and
    latency_ms.
  - LiteLLM's own litellm."LiteLLM_SpendLogs" records the SAME
    call with NON-ZERO spend — the first real, billed call this
    proxy has ever routed (litellmphaseb.structural found every
    prior row was status=failure, spend=0).
  - The two logs AGREE: same model, same outcome, same event.
  - The rollback path (LITELLM_ROUTING_DISABLED=1), engaged for
    this same call, ALSO succeeds — via direct Anthropic, with
    LiteLLM never contacted. Prove by genuine ABSENCE of a new
    LiteLLM_SpendLogs row after waiting the full flush window,
    not just by earliness. This is the ONE proof
    litellmphaseb.structural could not make (B3 blocked it there).
  - The real fallback chain still walks correctly via LiteLLM —
    force a first-model failure and confirm the next model in
    ai.model.fallback_chain is tried.

=== TASK 3: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md and
docs/LITELLM_INTEGRATION_DESIGN_V1.md: Phase B fully and genuinely
complete, all 5 previously-blocked assertions now proven. Record
the real root cause of the B2 PROXY_ADMIN mystery (Doppler sync
overwriting DATABASE_URL; fixed with a dedicated branch config) in
the design doc's §13.5 deployment gotchas — it cost hours and will
recur for anyone adding a third Render service. Phase C (Voyage)
is next.

=== VERIFICATION: apps/api/scripts/verify_litellmphasebproof.py ===
Pass/fail only. Never print either secret.

Assertions:
  [Y] Report Task 1's three findings explicitly
  [Y] A real model deployment exists and is queryable
  [Y] A genuine 200 with real generated text via the full LiteLLM
      chain
  [Y] ai_decision_log records real success + real cost/latency
  [Y] LiteLLM's own spend log shows the SAME call with non-zero
      spend
  [Y] The two logs agree on model and outcome
  [Y] The rollback path succeeds via direct Anthropic, proven by
      genuine absence of a new LiteLLM spend-log row after the
      full flush window
  [Y] The fallback chain still walks correctly via LiteLLM
  [Y] Teardown: zero leftover rows

The verify script MUST hydrate its own secrets from Doppler at
startup (the pattern used by verify_rlscutover.py /
verify_tamodel*.py — they print "[INFO] hydrated N secrets from
Doppler over HTTPS"). run_sprint.sh's Step 3 does NOT invoke
verify scripts through `doppler run --`, so a script relying on
ambient env vars will fail with a misleading credential error.
