You are a senior platform engineer on Hollisworks. You are adding a
super-admin control that selects the three AI models used to check
structured-note hazard fields: Review model 1, Review model 2, and the
Comparison model. You prove what LiteLLM can and cannot do BEFORE you
build anything on top of it.

ENSEMBLE MODEL SELECTION — 3 tasks + verification script.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (checked live via Supabase):
- org_settings columns: id, org_id, setting_key, setting_value,
  category, is_public, updated_at, updated_by, created_at.
  There is NO owner_scope column.
- The only ai.* settings rows belong to the 2nd Act org (org_id NOT
  NULL): ai.model.assistant="claude-sonnet", ai.model.default=
  "claude-haiku", ai.model.document_classifier="claude-haiku",
  ai.model.fallback_chain=["claude-haiku"]. ZERO platform-level
  (org_id NULL) ai.* rows exist.
- Structured-note data is GLOBAL (portfolio schema, no org_id). It
  has no org whose settings could govern it.
- The current hazard ensemble ran with primary=claude-haiku-4-5-20251001
  and secondary=claude-sonnet-4-6 (an alias, not a dated version),
  per the notes stored on 29 rows in document_field_corrections with
  target_type='note_terms'.
- Those 29 rows are model disagreements stored as if they were human
  corrections (corrected_by NULL, original=Haiku, corrected=Sonnet).
  This is a known bug. Do NOT fix it in this sprint and do NOT add to
  it. It is the next sprint's job.
- 54 current note_terms rows; 28 needs_review; all routing_decision NULL.
- LiteLLM is hard-pinned at v1.96.2. DO NOT upgrade it, for any reason.
- apps/api/scripts/litellm_diagnose.py exists because the LiteLLM master
  key was once resolved as role=internal_user instead of PROXY_ADMIN.
  Whether that is fixed is UNKNOWN.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue. The
exceptions are the explicit STOP/BLOCKED gates below.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that
a script finished. Never wait for one. Run every command synchronously
in the foreground and read its output directly. org_id never from a
request body. RLS policy in the same migration as the table. Never
print, log, or write any secret VALUE — names only (use
`doppler secrets --only-names`). Light theme, palette from tenant
config, no hardcoded hex. Do not run the verification script — write
it, make it runnable, and stop.

DELIBERATELY OUT OF SCOPE:
- Changing note_terms_extraction.py to READ the new selection. (Next
  sprint. This sprint builds the selection, catalog, and picker only.)
- Calling Jev for real scoring of note terms.
- Migrating or deleting the 29 mis-stored correction rows.
- Upgrading LiteLLM or changing its deployment.
- Any change to 2nd Act's existing ai.model.* settings.


=== TASK 1: DISCOVER — report, then continue ===

1a. How does apps/api/services/note_terms_extraction.py call models
    today? Quote the actual call sites. Through call_claude_json /
    LiteLLM, or the Anthropic SDK directly? Which credential does a
    GLOBAL job (no org) use?

1b. LiteLLM admin access. Using LITELLM_BASE_URL and LITELLM_MASTER_KEY
    from the environment (never print the key), call GET /key/info and
    GET /model/info read-only. Report the key's role.
    *** BLOCKED GATE *** If /model/info cannot be read, STOP and report
    BLOCKED. Do not build a catalog from a hardcoded list.

1c. From /model/info, report every model group, its underlying exact
    model version, and provider. Then find the platform's existing
    "three-state model availability" mechanism (built in the LiteLLM
    phases) — quote where it lives and its three states. Reuse it.
    Do not invent a second availability vocabulary.

1d. Jev / judgment-model feasibility ON THE INSTALLED v1.96.2 ONLY.
    Report, with evidence from the installed package or its config:
      (i)  Is a custom provider (CustomLLM / custom_provider_map)
           supported, and could it return Jev's probability output
           without flattening it into chat text?
      (ii) Are pass-through endpoints supported (credential + request
           logging only)?
    Then report whether any Jev / TypeSafe credential NAME exists in
    Doppler (`doppler secrets --only-names`). State your conclusion
    plainly: can Jev be a selectable LiteLLM model with its output
    intact — yes or no, and why. If no, the design below uses a
    separate judgment-model registry, with LiteLLM pass-through for
    key custody only if 1d(ii) is supported.

1e. Can org_settings hold a platform-level row (org_id NULL) under its
    current RLS? Quote its policies. Do NOT alter them. This sprint
    stores the selection in a new table (Task 2) either way; report
    this so the decision is recorded.

1f. Does LiteLLM v1.96.2 honor a per-request "no fallback" instruction?
    Report the exact parameter/mechanism and prove it with one real
    request against a cheap model. If there is no reliable per-request
    disable, report that as a FINDING — the next sprint must then
    call ensemble models in a way that cannot fall back.


=== TASK 2: BUILD — catalog, versioned selection, picker ===

Schema (apply via Supabase MCP, verify with a follow-up query):

public.ai_judgment_models — scoring models that are not chat models.
  id uuid pk, key text unique (e.g. 'typesafe-jev'), display_name,
  provider, model_version text NOT NULL, credential_name text (the
  Doppler NAME, never a value), availability (the three states found
  in 1c), last_verified_at timestamptz, notes, created_at.
  Seed Jev as UNAVAILABLE unless a real call succeeded in this sprint.
  Never mark anything available on the strength of a credential
  existing.

public.ai_ensemble_configs — immutable, versioned selections.
  id uuid pk, task_key text NOT NULL (seed value: 'note_terms_hazard'),
  review_model_1 text NOT NULL, review_model_2 text NOT NULL,
  comparison_model text NOT NULL,
  comparison_kind text NOT NULL CHECK IN ('llm','judgment'),
  is_active boolean NOT NULL default false,
  created_by uuid, created_at, activated_at, retired_at, notes.
  CHECK review_model_1 <> review_model_2
  CHECK comparison_model NOT IN (review_model_1, review_model_2)
  Partial UNIQUE (task_key) WHERE is_active.
  Trigger: after a row is created, the ONLY permitted update is
  retiring it (is_active true->false, set retired_at). Any other
  change raises. A new selection is a new row.

Both tables: global (no org_id), four separate RLS policies copied
verbatim from an existing global table (SELECT USING (true);
INSERT/UPDATE/DELETE gated on app.is_super_admin) — not a single
FOR ALL.

Service: apps/api/services/ai_model_catalog.py
  get_catalog() -> one list unioning LiteLLM chat models (from
  /model/info, with exact versions) and ai_judgment_models, each tagged
  kind ('llm'|'judgment'), provider, exact version, availability.
  Review models may only be kind 'llm'. The comparison model may be
  either.
  activate_ensemble(task_key, m1, m2, comparison, created_by) — server
  validation mirrors the CHECKs, refuses any model whose availability
  is not the "available" state, retires the current active row and
  activates the new one in ONE transaction. Warns (does not block) when
  both review models share a provider family; include the warning in
  the response.

Endpoints, super_admin only, under the existing /api/v1/admin/ prefix
(confirm the real prefix — do not assume):
  GET  /api/v1/admin/ai/model-catalog
  GET  /api/v1/admin/ai/ensembles?task_key=note_terms_hazard
  POST /api/v1/admin/ai/ensembles   body: {task_key, review_model_1,
       review_model_2, comparison_model, notes}

UI: on the existing note-terms review queue page, add an "Ensemble"
panel with three selects labeled exactly "Review model 1", "Review
model 2", "Comparison model". Each option shows the exact version and
availability; unavailable options are visible but disabled, with the
reason. Show the active selection and its history. Above the save
button, state plainly: "Changing models creates a new ensemble version.
Existing rows keep the models that produced them."


=== TASK 3: UPDATE PROJECT STATUS ===

Update docs/PROJECT_STATUS.md: the 1d conclusion on Jev and LiteLLM,
the 1f fallback finding, the master-key role from 1b, the tables and
endpoints added, and that extraction does not yet read the selection.


=== VERIFICATION: apps/api/scripts/verify_ensemblemodels.py ===

Write it. Do NOT run it. Pass/fail only, no prompts, idempotent,
teardown at start AND end. Hydrate its own secrets from Doppler (do not
rely on ambient env). Connect for RLS checks via APP_SERVICE_DATABASE_URL
and FAIL LOUDLY if it cannot connect — never fall back to another role.

  [ ] Catalog lists every model group from /model/info with an EXACT
      version, not only an alias (count matches /model/info)
  [ ] Jev is present with availability not equal to "available" unless
      a real call succeeded and last_verified_at is set
  [ ] review_model_1 = review_model_2 is rejected by the API AND by the
      table CHECK (assert both, by constraint name)
  [ ] comparison_model equal to either review model is rejected (both
      layers)
  [ ] An unavailable model cannot be activated
  [ ] A judgment model cannot be selected as a review model
  [ ] Activating config B retires config A: exactly one active row for
      the task_key, and A's other columns are byte-for-byte unchanged
  [ ] Any update to a created config other than retiring it raises
  [ ] POST /ensembles returns 403 for a non-super-admin; under
      app_service without is_super_admin, a direct INSERT is rejected
      by RLS
  [ ] Global read of both tables works under app_service with no org
      context
  [ ] Installed LiteLLM version is still 1.96.2
  [ ] No secret value appears in any file this sprint wrote (grep for
      key prefixes such as sk-ant-, and the Doppler values' first chars
      are NOT used — only grep for known key prefixes)
