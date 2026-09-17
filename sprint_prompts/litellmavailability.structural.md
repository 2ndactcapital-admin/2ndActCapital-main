LITELLM — THREE-STATE MODEL AVAILABILITY.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

Phases D and E are complete and merged. Hollisworks curates a
platform model catalog, orgs select from it with real call-path
enforcement, and orgs assign models and effort per task.

Spec: docs/LITELLM_D2_E_SPEC.md §3.

THE GAP: D2 shipped no availability concept at all — presence in
platform_model_catalog IS availability, and removing a model means
DELETING the row. That is too blunt: deleting a model an org
actively uses silently breaks that org with no warning and no
migration path.

PART 1 SQL IS ALREADY APPLIED AND VERIFIED LIVE. Do not re-apply:

  ALTER TABLE platform_model_catalog
    ADD COLUMN availability text NOT NULL DEFAULT 'available',
    ADD CONSTRAINT platform_model_catalog_availability_chk
      CHECK (availability IN ('available','deprecated','disabled'));

All three existing rows (claude-sonnet, claude-haiku, voyage-3.5)
are 'available'. Nothing changes behaviourally until a state is set.

THE THREE STATES, settled:

  available   — in the org picker, usable
  deprecated  — hidden from the picker, EXISTING selections still
                work, orgs using it are ALERTED
  disabled    — unusable; selections fall back to the org safe
                model, orgs using it are ALERTED

Why three and not a boolean: 'deprecated' is the off-ramp. It lets
Hollisworks stop offering a model while orgs already on it migrate,
with a real alert rather than silent removal from a screen.
'disabled' is the decisive stop.

Facts that change decisions:
- ALERTS REUSE THE EXISTING PATH. services/workflow_todos.py has
  create_credential_failure_alerts (D1c) as the established sibling
  pattern: manage_org_settings holders, _upsert_todo, and
  _record_undelivered_alert for the zero-recipient case. Add a
  sibling; do NOT build a third notification mechanism.
- The real Hollisworks org genuinely has ZERO manage_org_settings
  holders — a live case for the zero-recipient path.
- A 'disabled' model falls back via the two-tier safe-model
  hierarchy, consistent with how model-unavailability already
  behaves (D1c proved that path is structurally distinct from
  credential failure, which fails loudly instead).
- Reads open to any org member; writes on the catalog are
  super_admin only (D2's established gate). Do not change either.
- app_service cannot read the litellm schema. Use the proxy's
  admin API.
- Deleting a catalog row must remain possible for a model NO org
  has ever selected — the states are for models in real use.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Every real read of platform_model_catalog — which ones must
      now filter by availability, and which must deliberately NOT
      (an org's existing selection of a 'deprecated' model must
      still resolve).
  1b. The real call-path enforcement D2 built (resolve_authorized_
      models and its callers) and exactly where a 'disabled' model
      must stop resolving.
  1c. create_credential_failure_alerts' real signature and the
      shared private helpers, so the new sibling matches rather
      than diverges.

=== TASK 2: THE STATE TRANSITIONS ===
A Hollisworks super-admin can set a catalog model's availability.
Setting 'deprecated' or 'disabled' alerts every org that currently
has that model selected — not every org, only affected ones.
Zero-recipient orgs leave the audit_log record.

=== TASK 3: ENFORCE THE STATES ===
- 'available': unchanged from today.
- 'deprecated': absent from the picker for orgs that have NOT
  selected it; existing selections still resolve and still work.
- 'disabled': does not resolve; affected tasks fall back to the org
  safe model.

=== TASK 4: PROOF ===
  - Report Task 1's three findings.
  - All three seeded models are 'available' and behave exactly as
    before — no regression. This is the state every model is in.
  - 'deprecated': hidden from a non-selecting org's picker, AND an
    org that already selected it still resolves and still makes a
    real successful call. Prove both halves.
  - 'disabled': a real call for an org that had it selected falls
    back to the org safe model and succeeds — prove from
    ai_decision_log's model_used, not from config.
  - Deprecating a model alerts ONLY orgs that selected it — prove
    a non-selecting org gets nothing.
  - The zero-recipient path leaves the audit_log row — prove
    against the real Hollisworks org.
  - An invalid availability value is refused by the CHECK
    constraint.
  - Org admin gets 403 setting availability; super_admin gets 200
    on the identical request.
  - Cross-org: org A's alerts do not reach org B.

=== TASK 5: STATUS ===
Update docs/PROJECT_STATUS.md, docs/LITELLM_D2_E_SPEC.md and
docs/LITELLM_INTEGRATION_DESIGN_V1.md.

=== VERIFY: apps/api/scripts/verify_litellmavailability.py ===
Pass/fail only. MUST print a 'TOTAL: N PASS, M FAIL' line and exit
non-zero on failure. Must hydrate its own secrets from Doppler over
HTTPS at startup (verify_litellmphasee.py is the pattern —
run_sprint.sh's Step 3 does not use doppler run --). Never print a
credential value.

  [Y] Task 1's three findings reported
  [Y] All three seeded models 'available', no regression
  [Y] 'deprecated' hidden from a non-selector's picker
  [Y] 'deprecated' still resolves and works for an existing
      selector — a real successful call
  [Y] 'disabled' falls back to the org safe model, proven from
      ai_decision_log
  [Y] Deprecation alerts only affected orgs
  [Y] Zero-recipient org leaves the audit_log row
  [Y] An invalid availability value is refused
  [Y] org_admin 403 / super_admin 200 on the identical request
  [Y] Cross-org isolation on alerts
  [Y] Teardown: zero leftover rows, all three seeded models back to
      'available', proxy deployment set unchanged
