LITELLM PHASE E — PER-TASK MODEL ASSIGNMENT + EFFORT.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

Phase D is complete and merged. An org supplies its own provider
key (D1a), calls route to its own deployment with three-way spend
attribution (D1b), bad org credentials fail loudly and alert that
org's admins (D1c), Hollisworks curates a platform model catalog
and orgs pick from it with real call-path enforcement (D2), and
settings/catalog/proxy all agree on deployment names (seedfix).

SCOPE: which model handles which AI task, and how hard it works.

Facts that change decisions:
- NAMING CONVENTION, settled: org_settings values are ALWAYS the
  proxy's deployment model_name ('claude-haiku', 'claude-sonnet'),
  NEVER a raw upstream provider id. Live deployments today:
  claude-sonnet, claude-haiku, voyage-3.5.
- Per-task keys ALREADY EXIST and are live consumers:
  ai.model.default (extraction, briefs, summaries),
  ai.model.assistant, ai.model.document_classifier. Each resolves
  via extraction.resolve_model(org_id, key=...), checking its
  dedicated key first and falling back to ai.model.default. This
  sprint makes that assignable through a UI — it does not invent
  the mechanism.
- EFFORT GATING IS METADATA-DRIVEN. GET /model_group/info returns
  supports_reasoning (true for claude-sonnet/claude-haiku, false
  for voyage-3.5) and supported_openai_params (which lists both
  'thinking' and 'reasoning_effort' for the Anthropic models).
  Show an effort control ONLY where supports_reasoning is true.
- EFFORT VALUES ARE NOT IN THE METADATA. LiteLLM names the
  parameter, not its legal range. A small local mapping supplies
  the options. Keep it small and in one place.
- EFFORT IS PER TASK, NOT PER ORG. A document classification wants
  low effort; a diligence memo wants high. An org-wide effort
  setting would mostly be a way to spend more without knowing
  which tasks benefit.
- Reads open to any org member; writes gated on
  manage_org_settings — the pattern D1a and D2 both established.
  Permission envelope: server publishes can_write and the editable
  list; client renders write controls ONLY inside a can_write
  check with NO truthy fallback. A missing envelope fails CLOSED.
- The two-tier safe-model hierarchy stays as built.
- Light theme. Reuse the existing settings screen patterns.
- app_service cannot read the litellm schema. Use the proxy's
  admin API.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Enumerate EVERY real AI task in deployed code — every
      distinct task_type passed to the call_claude_* helpers and
      the embedding path. For each: its real name, which
      ai.model.* key it resolves through today, and whether it has
      a dedicated key or falls through to the default.
  1b. How a NEW task added later would appear in this list
      automatically rather than needing a code change to the
      screen. Report the real mechanism, or state plainly that
      none exists and what it would take.
  1c. Probe live: exactly what supports_reasoning and
      supported_openai_params return per deployment, and confirm
      how an effort parameter is actually accepted on a real call
      (test one — do not assume the parameter shape).

=== TASK 2: PER-TASK ASSIGNMENT ===
An org admin assigns a model from the org's authorised list to
each AI task. Unassigned tasks keep resolving exactly as today —
dedicated key, then ai.model.default. No org's behaviour changes
until someone deliberately assigns something.

=== TASK 3: EFFORT ===
Per task, where the assigned model reports supports_reasoning:
true, an effort level. Send it on the real outgoing call per 1c's
findings. Where supports_reasoning is false, no control and no
parameter.

OPEN QUESTION TO SETTLE AND JUSTIFY: when a task with an effort
setting falls back to a model that does NOT support reasoning,
does effort drop silently or does the call fail? Decide, implement,
and record the reasoning in ai_decision_log so the choice is
visible after the fact. Recommend dropping silently and logging it
— failing a working call because a quality knob is unsupported is
the worse outcome — but justify whichever you choose.

=== TASK 4: PROOF ===
  - Report Task 1's three findings.
  - An unassigned task resolves exactly as today — no regression.
    Every existing task is in this state.
  - An assigned task genuinely routes to the assigned model —
    proven from ai_decision_log's model_used, not from config.
  - A task can only be assigned a model the org has authorised
    (D2's catalog + selection) — an unauthorised assignment is
    refused.
  - Effort is genuinely sent on a real call to a
    supports_reasoning model — prove it reached the provider, not
    just that it was stored.
  - No effort control and no parameter for a model that reports
    supports_reasoning: false.
  - The fallback-with-effort case behaves as Task 3 decided, and
    ai_decision_log records what happened.
  - Org admin can assign; plain member gets 403 on the identical
    request.
  - Cross-org: org A's assignments do not affect org B's.
  - npm run build exits 0.

=== TASK 5: STATUS ===
Update docs/PROJECT_STATUS.md,
docs/LITELLM_INTEGRATION_DESIGN_V1.md and
docs/LITELLM_D2_E_SPEC.md. Record Task 3's fallback decision
prominently.

=== VERIFY: apps/api/scripts/verify_litellmphasee.py ===
Pass/fail only. MUST print a 'TOTAL: N PASS, M FAIL' line and exit
non-zero on failure. Must hydrate its own secrets from Doppler over
HTTPS at startup (verify_litellmseedfix.py is the pattern —
run_sprint.sh's Step 3 does not use doppler run --). Never print a
credential value.

  [Y] Task 1's three findings reported
  [Y] An unassigned task is unchanged — no regression
  [Y] An assigned task routes to the assigned model, proven from
      ai_decision_log
  [Y] An unauthorised model cannot be assigned
  [Y] Effort genuinely reaches the provider on a supports_reasoning
      model
  [Y] No effort control or parameter where supports_reasoning is
      false
  [Y] Fallback-with-effort behaves as decided and is logged
  [Y] Org admin assigns; plain member 403 on the identical request
  [Y] Cross-org isolation on assignments
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows AND the proxy's deployment set
      unchanged (claude-sonnet, claude-haiku, voyage-3.5)
