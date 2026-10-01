ENSEMBLE MODELS v2 — TWO LLMs + ONE SYSTEM ONE MODEL. Overwrites
ensemblemodels.structural.

YOU ARE THE SPRINT. Do this work yourself, now, in this session. Do NOT run
run_sprint.sh. Do NOT launch anything in the background. Run every command
synchronously in the foreground and read its output. Nothing will ever notify
you that something finished.

WRITE THE VERIFY SCRIPT BUT DO NOT RUN IT. The operator runs it. Stop once it
is written and your other tasks are complete.

This supersedes both ensemblemodels.structural and the follow-up fix note
sent after it. Where they conflict with this prompt, this prompt wins.

=== DECISIONS (Joe, 2026-10-01) — do not re-litigate ===

1. An ensemble for a task is exactly: Model 1 (an LLM), Model 2 (an LLM), and
   one System One model. The comparison slot is ALWAYS a System One model.
   There is no LLM comparator option. Drop comparison_kind.

2. Two curated universes, both editable by super-admin only:
   - LLMs: the EXISTING public.platform_model_catalog (Phase D2). Model 1 and
     Model 2 come from it: availability 'available' AND /model/info mode
     'chat' (reuse Phase E's existing mode check; do not write a second one).
     Do not build another LLM catalog.
   - System One models: repurpose public.ai_judgment_models into the System
     One catalog (rename it ai_system_one_models; keep its data). Super-admin
     adds and removes entries, sets availability (available / deprecated /
     disabled), and marks exactly ONE entry as the DEFAULT. Jev is the
     default.

3. The picker lives on the note-terms admin screen: Model 1, Model 2, System
   One model, with the System One slot preselected to the catalog default.
   The note-term corpus is GLOBAL — one corpus shared by every org — so the
   active ensemble is platform-wide. No per-org selection in this sprint.

4. Store what can actually be called, and record what actually ran:
   - LLM slots store platform_model_catalog.model_id (the proxy deployment
     name, e.g. 'claude-haiku'). The proxy REJECTS raw upstream ids with HTTP
     400 — proven live. Never store a raw upstream id as the callable value.
   - The System One slot stores the System One catalog key.
   - Snapshot each model's exact version at creation into the *_version
     columns. Configs are immutable, so this is permanent provenance.

5. Pin Jev for reproducibility. Seed Jev's catalog entry to call a pinned
   version (currently jev-1.13.0 — confirm via the models endpoint once the
   route works), not jev-latest, which silently follows new releases. A
   super-admin may add jev-latest as a separate entry later.

=== HOW JEV IS CALLED (researched 2026-10-01) ===

- Jev is not a chat model. It answers typed questions (choice / score /
  yes-no probability) through its own endpoint, POST /v1/systemone, and the
  response must reach us unchanged — probabilities intact. No streaming
  exists. Context limit 32K tokens. A choice question allows up to 255
  options.
- LiteLLM's NATIVE Jev support (/typesafe/{endpoint} pass-through with
  registry-priced spend) first shipped in v1.102.1. This proxy is pinned at
  v1.96.2, so native support is NOT available. Do NOT upgrade in this sprint:
  the proxy runs with DISABLE_SCHEMA_UPDATE=true, so an upgrade needs a
  supervised database migration — that is its own sprint.
- IN SCOPE: configure LiteLLM's generic pass_through_endpoints on the
  installed version — path /typesafe, target https://api.typesafe.ai, include
  subpaths, with the TypeSafe key injected by the proxy from its environment
  variable TYPESAFE_API_KEY. Callers present only a LiteLLM key. First
  confirm the installed version supports this, and how this deployment stores
  and changes proxy configuration (models are DB-stored via
  STORE_MODEL_IN_DB). If it cannot be done without upgrading, STOP and report.
  Do NOT fall back to calling TypeSafe directly from the application.
- The key lives ONLY in Doppler's prd_lite_llm branch (the proxy's config) —
  never in root prd, never in the app. Joe adds it. Refer to it by name only;
  never print its value or any output that could contain it.
- Known gap to record, not fix: on v1.96.2's generic pass-through, Jev spend
  is not token-priced, so Phase G budgets will not see it. Jev costs about
  $42 per billion input tokens, so the exposure is small.

AVAILABILITY RULE: Jev may be marked 'available' ONLY after a real call
through the proxy succeeds — the models listing AND one minimal systemone
decision. A credential existing is not availability. Record last_verified_at.
If TYPESAFE_API_KEY is absent, Jev stays 'disabled' and the picker says why.

=== OVERWRITE — the previous sprint's output must be reshaped ===

Verified live 2026-10-01: ai_ensemble_configs has ZERO rows and already has
the *_version snapshot columns; ai_judgment_models has ONE row
(typesafe-jev, 'disabled'). Both have four RLS policies.

- ai_ensemble_configs: rename review_model_1/2 -> model_1/2 (keep their
  *_version columns), comparison_model -> system_one_model (and its version
  column), drop comparison_kind. Re-create the CHECKs: all three NOT NULL,
  model_1 <> model_2. Keep the immutability trigger (only retiring is
  allowed), the one-active-per-task partial unique index, and all four RLS
  policies — re-verify each after the DDL.
- ai_judgment_models -> ai_system_one_models. Add is_default with a partial
  unique index so at most one row can be default. Add a column for the model
  route to call, if model_version is not already serving that role. Keep all
  four RLS policies.
- REMOVE the previous run's code for an LLM comparator, for comparison_kind,
  and any parallel LLM catalog service or endpoint it built. Report each
  removal by file.

=== FACTS THAT CHANGE DECISIONS ===

- RLS policies are PER OPERATION, and the calling connection must set the
  context they check. Both a missing policy and missing context surface as
  "row not found", never "permission denied". Never let code assert a cause
  it cannot know.
- The LiteLLM master key authenticates as PROXY_ADMIN. app_service cannot
  read the litellm schema; use the proxy's admin API.
- voyage-3.5 is embedding-only and must never appear in an LLM slot.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The installed LiteLLM version, whether generic pass_through_endpoints
      works on it, and exactly how proxy configuration is stored and changed
      for this deployment.
  1b. Everything the previous run built — tables, services, endpoints, UI —
      mapped to keep / reshape / remove.
  1c. Whether TYPESAFE_API_KEY exists in the prd_lite_llm Doppler config.
      Name only.
  1d. Once the route works: the Jev versions TypeSafe reports.

=== TASK 2: SCHEMA OVERWRITE === per the section above.

=== TASK 3: SYSTEM ONE CATALOG (super-admin) ===
Alongside the existing LLM catalog screen: add / remove entries, set
availability, set the default, and a "verify now" action that runs the real
availability check.

=== TASK 4: THE PASS-THROUGH AND THE LIVE CHECK === per the Jev section.

=== TASK 5: THE PICKER (note-terms admin screen) ===
Model 1 and Model 2 offer available chat models from platform_model_catalog;
System One offers available System One entries, preselected to the default.
Unavailable options show with their reason. Activation creates an immutable
config with version snapshots and retires the previous active config in the
same transaction. If no valid ensemble is possible (for example, Jev not yet
available), the picker says exactly why.

OUT OF SCOPE: wiring extraction to read the active ensemble (next sprint),
calling Jev to score real note terms, upgrading LiteLLM, per-org selection.

=== TASK 6: STATUS ===
Update docs/PROJECT_STATUS.md and docs/LITELLM_INTEGRATION_DESIGN_V1.md.
Record: the System One design; the generic pass-through cost gap; upgrading to
LiteLLM >= v1.102.1 as a future sprint; and that the ensemble is platform-wide
because the corpus is global.

=== VERIFY: apps/api/scripts/verify_ensemblesystemone.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Hydrate secrets from
Doppler over HTTPS at startup (verify_tiergating.py is the pattern).

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST, and the helper asserts
    isinstance(passed, bool).
  - Never derive any unique value from a slice of a fixture UUID; use the
    full UUID.
  - Set RLS context on EVERY read, including before/after comparisons.
  - Clear audit_log, assistant_activities and agent_proposals BEFORE deleting
    fixture users.
  - Use fixture rows for catalog entries; never modify the real catalog rows
    or move the real default. Test the one-default rule by attempting a second
    default and asserting the database refuses it.
  - The immutability trigger blocks UPDATE, so teardown DELETEs fixture
    configs with super-admin context set.

Assertions:
  [Y] Task 1's four findings reported
  [Y] comparison_kind gone, columns renamed, no LLM-comparator code remains
  [Y] A second System One default is refused by the database
  [Y] super_admin edits the System One catalog; org_admin gets 403 on the
      identical request
  [Y] Model 1/2 refuse voyage-3.5, refuse unavailable models, and refuse
      Model 1 == Model 2
  [Y] The System One slot refuses an LLM and refuses a disabled entry
  [Y] The picker preselects the default System One model
  [Y] Activation snapshots versions and retires the previous config in one
      transaction; the immutability trigger blocks edits
  [Y] Jev: if TYPESAFE_API_KEY is present, a real call through the proxy
      lists models and returns one decision with probabilities intact, and Jev
      becomes 'available'. If absent, Jev stays 'disabled' and this reports
      BLOCKED — never faked, never FAIL.
  [Y] The TypeSafe key never appears in application code or the app's
      environment; the request carries only a LiteLLM key
  [Y] Four RLS policies on each table, re-verified after the DDL
  [Y] Teardown: zero leftover rows; real catalog rows and the real default
      untouched
