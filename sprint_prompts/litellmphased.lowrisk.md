LITELLM PHASE D — DISCOVERY ONLY. Read-only investigation, NO CODE
CHANGES, NO SCHEMA CHANGES. 6 tasks, findings-only.

Establishes the real facts needed to build the model-authorization
and task-assignment layer (Phases D and E). Several decisions are
already made and are NOT open questions — see CONFIRMED DECISIONS
below. This sprint finds out what the platform can actually
support today, not what it should do.

CONFIRMED DECISIONS, DO NOT RE-LITIGATE:
- BRING-YOUR-OWN-KEY is the model. An org supplies its own
  provider credentials. Hollisworks-level keys exist as a
  deliberate, explicitly-configured fallback per provider — never
  as an error-handling path.
- PER-PROVIDER, not per-model and not Anthropic-specific. For each
  provider (Anthropic, Voyage, and any future one), an org either
  has its own key or is explicitly configured to use the platform
  key. One mechanism, not a growing pile of per-provider toggles.
- A BROKEN ORG KEY FAILS LOUDLY. Expired, revoked, or invalid org
  credentials produce a specific, actionable error naming the
  provider and the org's own key. It NEVER silently falls back to
  the Hollisworks platform key — an org's credential problem must
  never become Hollisworks' bill.
- MODEL UNAVAILABILITY IS DIFFERENT AND STILL FALLS BACK. The
  existing two-tier safe-model hierarchy (task model -> org safe
  model -> Hollis safe model) stays. Credential failure is a
  configuration problem the org must fix; model unavailability is
  a routing problem the platform routes around. Do not conflate
  them.
- ORG KEYS ARE STORED IN LITELLM'S OWN ENCRYPTED TABLES via its
  Virtual Key / Team mechanism — NOT in Doppler. Doppler stays for
  platform-level secrets set by humans and changed rarely.
  LITELLM_SALT_KEY is what protects the stored credentials.
- A FAILED ORG KEY ALERTS THAT ORG'S ADMINS via the existing
  member_todos mechanism (the same fan-out create_held_run_alerts
  uses: every users.role='org_admin' in that org). Not a new
  notification system.
- THE ORG PICKS FROM A HOLLISWORKS-CURATED LIST, not from every
  model LiteLLM has ever heard of. Two tiers: Hollisworks decides
  what is supportable, the org picks from that.

THERE IS NO HUMAN AVAILABLE. Report each task's findings, THEN
CONTINUE IMMEDIATELY to the next in the same response.

STANDING RULES: no interactive prompts. There is NO
background-process notification mechanism in this tool — nothing
will ever notify you that a script finished. Never wait for one.
Run every script SYNCHRONOUSLY in the foreground and read its
output directly. Never print any API key or secret value.

DO NOT: write code, modify schema, change any LiteLLM
configuration, create any file other than the findings document in
Task 6.

=== TASK 1: WHAT LITELLM'S VIRTUAL KEY / TEAM API ACTUALLY
SUPPORTS ===
Probe the LIVE proxy's real admin API. Report what is genuinely
available, not what the docs describe generically.
  1a. Can a Virtual Key carry PROVIDER CREDENTIALS of its own
      (an org's own Anthropic/Voyage key), or does it only carry
      an allowlist of models that all resolve to the PLATFORM's
      credentials? This is the single most important finding in
      this sprint — the BYO-key design depends on it. Probe it
      directly; do not infer from documentation.
  1c. What does a Team actually scope? Report the real fields
      available on team creation and what each controls.
  1d. Is there a real per-key or per-team spend attribution in
      LiteLLM's own spend log, such that platform-key usage on
      behalf of an org would be distinguishable from Hollisworks'
      own usage? Report the real columns available.

=== TASK 2: IF 1a SAYS NO — WHAT IS THE REAL ALTERNATIVE ===
Only if Task 1a finds Virtual Keys cannot carry an org's own
provider credentials: report what the real options actually are.
Probe, do not speculate: can a model DEPLOYMENT (POST /model/new)
carry a distinct api_key per deployment, such that
'anthropic-org-<uuid>' and 'anthropic-platform' could coexist as
separate deployments with different credentials? If so, report
exactly what that would imply for the model-list UI (an org would
see logical model names, not deployment names). Report honestly if
neither mechanism supports BYO cleanly — that is a real, valuable
finding that changes the design.

=== TASK 3: THE REAL TASK INVENTORY ===
  3a. Enumerate EVERY real place in deployed code that makes an
      AI call. For each: the real task name/identifier, which
      helper it goes through (call_claude_text / call_claude_json
      / call_claude_with_tools / the embedding path), and whether
      its model is resolved from config or pinned in code.
  3b. For each task found, report whether it is genuinely
      org-selectable or must stay platform-pinned, and why.
      NOTE: AWS Textract is OCR, NOT an LLM and NOT routed
      through LiteLLM — confirm whether it (or anything like it)
      would wrongly appear in a model picker, and report any
      other non-LLM AI service in the same position.
  3c. Report the real current ai.model.fallback_chain and
      ai.embedding.* values, per org, as actually stored.

=== TASK 4: WHAT org_settings CAN ALREADY EXPRESS ===
  4a. Report the real current shape of every ai.* and
      modeling.* org_settings key, and how DEFAULT_SETTINGS
      fallback resolution actually works for them.
  4b. Could a per-provider credential-source setting
      ('org' | 'platform') be expressed in the existing
      org_settings shape without schema change? Report what it
      would look like concretely, and any real constraint that
      would block it.
  4c. Confirm the real mechanism for a PLATFORM-scoped setting
      (owner_scope='platform', NULL org_id per CLAUDE.md Rule 6)
      and whether the curated model list could live there.

=== TASK 5: THE ALERT PATH, CONFIRMED REAL ===
  5a. Confirm create_held_run_alerts' real signature and its real
      recipient rule, and whether it can be reused directly for a
      credential-failure alert or needs a sibling function.
  5b. Report the real source/related_type/action_key conventions
      member_todos uses today, so a new credential-failure alert
      follows the established shape rather than inventing one.
  5c. Confirm what an org_admin actually SEES today — which real
      endpoint/screen surfaces member_todos to them — so a
      credential alert lands somewhere real rather than in a
      table nobody reads.

=== TASK 6: WRITE FINDINGS ONLY ===
Write docs/LITELLM_PHASE_D_DISCOVERY.md — a structured report of
Tasks 1-5. This is a discovery record, not a design document:
report facts and name gaps, do NOT propose schema or write
designs. Commit this file. No other file should be created or
modified.

=== VERIFICATION ===
No verify script — this is discovery-only and the report is the
deliverable. run_sprint.sh will emit "FATAL: expected verify
script not found" at Step 3; that is expected noise here, not a
failure. Confirm the real outcome from git.

Confirm docs/LITELLM_PHASE_D_DISCOVERY.md exists with real,
specific findings for all 5 investigation tasks, and confirm via
git diff that NO other file was created or changed, and that the
live proxy's configuration is unchanged (same model deployments
before and after).
