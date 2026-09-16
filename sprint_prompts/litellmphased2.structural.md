LITELLM PHASE D2 — MODEL PICK-LIST UI.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

Phase D1 is complete and merged (D1a 58/58, D1b 54/54, D1c 30/30).
An org can store its own provider key, gets a dedicated encrypted
deployment, calls route to it, spend is attributed three ways, and
a bad org credential fails loudly and alerts that org's admins.

SCOPE: the screen where a Hollisworks admin curates the platform
model list, and where an org admin picks which of those models its
own org may use. Per-task model assignment is Phase E — do not
build it.

Facts that change decisions:
- TWO TIERS. Hollisworks curates what is supportable; an org picks
  from that curated list. An org must never see the raw LiteLLM
  catalogue.
- LOGICAL NAMES ONLY. The org sees "Claude Sonnet", never
  'org-anthropic-<uuid>' or any deployment name. D1a and D1b both
  proved this by grepping raw response text — keep that discipline.
- The existing credential screen (GET/PUT/DELETE
  .../ai-credentials) is the established pattern for this area:
  reads open to any org member, writes gated on
  manage_org_settings. Follow it; do not invent a different gate.
- Permission envelope pattern: the server publishes
  can_read/can_write and the editable list; the client renders write
  controls ONLY inside a can_write check with NO truthy fallback. A
  missing envelope must fail CLOSED.
- Platform-scoped settings use owner_scope='platform' with NULL
  org_id (CLAUDE.md Rule 6), not the default org id as a stand-in.
- Light theme only. Reuse DataGrid + right-pane, as every prior UX
  sprint did.
- app_service cannot read the litellm schema. Use the proxy's admin
  API for anything about real deployments.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Where the curated platform list should live — confirm whether
      a platform-scoped org_settings key can hold it with no schema
      change, and report the concrete shape.
  1b. What GET /model/info actually returns that is useful for a
      picker (context window, pricing, provider) — probe it live.
      Report which fields are genuinely available versus absent.
  1c. The real existing settings screen this should sit alongside,
      and its real permission envelope shape — reuse, do not
      reinvent.

=== TASK 2: THE CURATED PLATFORM LIST ===
Per 1a: a Hollisworks super-admin can add or remove a model from
the platform-supportable list. Only Hollisworks — an org admin must
never be able to edit it.

=== TASK 3: THE ORG PICKER ===
An org admin selects which curated models its org may use. Reads
open to any org member; writes gated on manage_org_settings. The
selection must be genuinely enforced, not merely recorded — a model
an org has not authorised must not be usable by that org.

=== TASK 4: PROOF ===
  - Report Task 1's three findings.
  - A Hollisworks super-admin can edit the platform list; an org
    admin gets 403 on the identical request.
  - An org admin can edit its own org's selection; a plain member
    gets 403 on the identical request.
  - The enforcement is real: a model the org has NOT authorised is
    genuinely refused for that org — prove at the call path, not
    just that the UI hides it.
  - An org with no explicit selection still works exactly as today
    — no regression. Every existing org is in this state.
  - Cross-org: org A's selection does not affect org B's.
  - No deployment name appears in any org-facing response — grep
    the raw payload text.
  - View-only: the UI renders no write control without can_write,
    AND the API refuses the write on a direct attempt. Both proven
    independently.
  - npm run build exits 0.

=== TASK 5: STATUS ===
Update docs/PROJECT_STATUS.md and
docs/LITELLM_INTEGRATION_DESIGN_V1.md. Next: Phase E (per-task
model assignment).

=== VERIFY: apps/api/scripts/verify_litellmphased2.py ===
Pass/fail only. Must hydrate its own secrets from Doppler over
HTTPS at startup (verify_litellmphased1c.py is the pattern —
run_sprint.sh's Step 3 does not use doppler run --). Never print a
credential value.

  [Y] Task 1's three findings reported
  [Y] Hollisworks super-admin edits the platform list; org admin
      403 on the identical request
  [Y] Org admin edits its own selection; plain member 403 on the
      identical request
  [Y] An unauthorised model is genuinely refused at the call path
  [Y] An org with no selection is unaffected — no regression
  [Y] Cross-org isolation on selections
  [Y] No deployment name in any org-facing response
  [Y] View-only: no write control rendered AND API refuses directly
  [Y] npm run build exits 0
  [Y] Teardown: zero leftover rows AND zero leftover test
      deployments on the live proxy
