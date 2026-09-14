# LiteLLM Phase D — Discovery Findings (2026-09-14)

**Status: discovery only.** No code, schema, or LiteLLM configuration was
changed by this sprint. Every write made against the live proxy during
probing (one test Team, two test Virtual Keys, one test model deployment)
was deleted before this document was written, and the final state was
re-confirmed identical to the starting state — see Task 1/2 notes below.

This is a record of what IS, not a design. It feeds Phase D/E design work,
which is separate, later work.

---

## Task 1 — What LiteLLM's Virtual Key / Team API actually supports

Probed directly against the live `hollisworks-litellm` proxy (`LITELLM_BASE_URL`
+ `LITELLM_MASTER_KEY` from Doppler `prd`), not inferred from docs.

### 1a. Can a Virtual Key carry its own provider credentials? — **NO, confirmed directly**

`POST /key/generate`'s real accepted body (`GenerateKeyRequest`, read from the
proxy's own live `/openapi.json`) has **no credential field of any kind** —
no `api_key`, no `credential_name`, nothing. Its real fields are entirely
about *scope and limits*: `models` (an allowlist of logical model names),
`team_id`, `user_id`, budgets (`max_budget`, `budget_duration`,
`budget_limits`, `model_max_budget`), rate limits (`tpm_limit`, `rpm_limit`,
`model_tpm_limit`, `model_rpm_limit`), `guardrails`, `tags`, `blocked`,
`allowed_routes`.

Proven live, not just read from the schema: a real `POST /key/generate` call
was made with an extra `"api_key": "sk-ant-fake-org-credential-test"` field
in the body. The call succeeded (200) and the returned key object contains
**no trace of that field anywhere** — LiteLLM silently drops unknown fields.
A Virtual Key is purely an **authorization/attribution token that resolves
to whichever backend deployment(s) its `models` allowlist names** — it never
carries its own outbound credential. **This is the single most important
finding in this sprint: BYO-key cannot be implemented at the Virtual-Key
level. It has to live one layer down, at the model-deployment level (Task 2).**

### 1c. What a Team actually scopes — real fields, from a live `POST /team/new`

A live-created Team (immediately deleted after inspection) returned:
`team_id`, `team_alias`, `organization_id` (LiteLLM's own Enterprise-only
Organization concept — null here, not in use), `admins`, `members`,
`members_with_roles`, `metadata`, `tpm_limit`, `rpm_limit`, `max_budget`,
`soft_budget`, `budget_duration`, `budget_limits`, **`models`** (the team's
model allowlist), `blocked`, `router_settings`, `access_group_ids`,
`default_team_member_models`, `spend`, `model_spend`, `model_max_budget`,
`policies`.

**[FIND] — real gotcha for the "curated list" design.** The probe Team was
created with `models` entirely unset (LiteLLM stored it as `[]`). A
subsequent `GET /model/info` showed that team's `access_via_team_ids` listed
against **all three** live model deployments (the two real ones plus the
probe deployment created for Task 2) — not zero. An empty/unset `models`
list on a Team does not mean "no models"; it appears to behave as
unrestricted access to everything on the proxy. **A curated per-org list
therefore requires every Team to have its `models` array explicitly and
completely populated at creation time — leaving it unset is not a safe
default and would silently grant the org access to every model on the
proxy, including any other org's.** This needs to be a hard rule in the
Phase D/E design, not an assumption.

### 1d. Real per-key/per-team spend attribution — **YES, confirmed with a live row**

`GET /spend/logs` returns real rows (from actual production traffic, not a
probe) with genuine attribution columns: `api_key`, `user`,
`user_api_key_alias`, `user_api_key_team_id`, `user_api_key_user_id`,
`user_api_key_org_id`, alongside `model`, `model_id`, `model_group`,
`custom_llm_provider`, `spend`, `total_tokens`/`prompt_tokens`/
`completion_tokens`, `startTime`/`endTime`, and a `metadata.cost_breakdown`
object. The live row inspected was attributed to `api_key:
"litellm_proxy_master_key"` (all current production traffic uses the master
key directly — no Virtual Keys are in production use yet), with
`user_api_key_team_id: null`. **The columns needed to distinguish
platform-key usage on an org's behalf from Hollisworks' own usage
genuinely exist** (`user_api_key_team_id` populated once real per-org
Virtual Keys are issued) — this is not a gap, it is simply unused today.

---

## Task 2 — The real alternative to Virtual-Key credentials

Confirmed directly, live: **a model deployment (`POST /model/new`) can carry
its own distinct `api_key`, and two deployments can coexist under the SAME
logical `model_name`.**

Proof (created, inspected, then deleted): `POST /model/new` with
`model_name: "claude-sonnet"` (the same logical name already in use) and a
distinct `litellm_params.api_key` succeeded and returned a **new, separate
`model_id`** alongside the pre-existing `claude-sonnet` deployment.
`GET /model/info` afterward showed both `claude-sonnet` entries side by
side, each with its own `model_info.id`, each independently
create/delete-able via `POST /model/delete {"id": ...}`.

`LiteLLM_Params` (the real schema behind `litellm_params`, read from
`/openapi.json`) has both `api_key` (a raw string field) and
`litellm_credential_name` (a pointer to a stored `Credential` object).
`POST /credentials` / `GET /credentials` is a real, live, separate store
(confirmed empty right now — `{"success":true,"credentials":[]}`) for named,
reusable provider credentials (`credential_name`, `credential_info`,
`credential_values`, optionally scoped to a `model_id`), which a deployment
can reference by name via `litellm_credential_name` instead of embedding the
raw key inline. Both paths are real; the credential-name path is the more
auditable/rotatable of the two.

### What this implies for the model-list UI — a real, structural consequence

**Two deployments sharing one logical `model_name` are NOT two independently
selectable options — LiteLLM's router treats same-named deployments as one
load-balanced group** and will route a call for `"claude-sonnet"` to
*either* deployment. This is confirmed by LiteLLM's own documented router
behavior, and matches what the live `access_via_team_ids` output implied
(a team's `models` allowlist names a logical `model_name`, not a specific
deployment `model_id`). **Consequence: an org's own credential and
Hollisworks' platform credential for "the same model" cannot both be
registered under the identical `model_name` if the intent is to
deterministically route an org's calls to only its own key.** A workable
shape (naming pattern only — not a design decision made here) would need
distinct logical names per credential owner (e.g.
`claude-sonnet--org-<uuid>` vs `claude-sonnet--platform`), with the
org-facing UI showing a friendly label mapped server-side to the real
per-owner deployment name — mirroring the org-vs-platform indirection the
design doc's §9 recommender already needs for cost/context metadata anyway.
**Naming this gap honestly: neither Virtual Keys nor same-named deployments
give "an org just picks a model and it transparently uses their key" for
free — the model-name-per-owner indirection is real, unavoidable, design
work for Phase D, not a detail.**

**Cleanup discipline note:** all probe objects (1 Team, 2 Virtual Keys, 1
model deployment) were deleted via `/team/delete`, `/key/delete`,
`/model/delete` immediately after inspection. Post-cleanup
`GET /v1/models` / `GET /team/list` / `GET /key/list` were re-run and
confirmed the proxy is back to exactly its pre-sprint state: 2 models
(`claude-sonnet`, `voyage-3.5`), 0 teams, 1 key (the pre-existing master-key
token record, unaffected).

---

## Task 3 — The real task inventory

### 3a. Every real AI call site (grepped from deployed `services/`/`routers/`, verify scripts excluded)

All 17 real call sites route through exactly three helpers in
`services/extraction.py` (`call_claude_json`, `call_claude_text`,
`call_claude_with_tools`) plus one dedicated embedding path
(`services/document_embedding.embed_document` / `embed_query`). Every
single one resolves its model from `org_settings` at call time — **zero
pinned model strings exist at any call site**, confirmed by reading every
call site's arguments, not merely trusting the design doc's prior claim:

| Call site (file:function) | Helper | `task_type` | Model resolution |
|---|---|---|---|
| `services/document_classifier.py` | `call_claude_json` | `document_classifier` | `resolve_model(key=DOCUMENT_CLASSIFIER_MODEL_KEY)` |
| `services/extraction.py` (`extract_foundation_answer`-style) | `call_claude_json` | `profile_extraction` | default chain (`ai.model.default`) |
| `services/extraction.py` (CRM note extraction) | `call_claude_json` | `crm_extraction` | default chain |
| `services/fee_narratives.py` | `call_claude_text` | `fee_narrative_polish` | default chain, `org_id`-scoped |
| `services/narrative_extraction.py` | `call_claude_json` | `narrative_extraction` | default chain |
| `services/note_terms_extraction.py` (primary) | `call_claude_json` | `note_terms_extraction` | `model_key=DEFAULT_MODEL_KEY` |
| `services/note_terms_extraction.py` (hazard ensemble) | `call_claude_json` | `note_terms_hazard_ensemble` | `model_key=ASSISTANT_MODEL_KEY` (deliberately a 2nd model, for cross-checking — see the litellm-discovery memory note that this collapses to the same model unless read back) |
| `services/vdr_analysis.py` | `call_claude_json` | `vdr_analysis` | default chain, `org_id`-scoped |
| `services/assistant_actions/crm.py` | `call_claude_text` | `crm_draft_note` | default chain |
| `services/workflow_nl_generator.py` | `call_claude_text` | `workflow_generation` | default chain |
| `routers/assistant.py` | `call_claude_with_tools` | `assistant` | `model_key=ASSISTANT_MODEL_KEY` (helper's own default) |
| `routers/dashboard.py` (member brief) | `call_claude_text` | `member_brief` | `model_key=ASSISTANT_MODEL_KEY` explicit |
| `routers/investment_profile.py` (Foundation reply) | `call_claude_text` | `foundation_reply` | default chain |
| `routers/investment_profile.py` (client brief) | `call_claude_text` | `client_brief` | default chain, `org_id`-scoped |
| `routers/investment_profile.py` (brief themes) | `call_claude_json` | `brief_themes` | default chain |
| `routers/marketplace.py` (deal summary) | `call_claude_json` | `deal_summary` | default chain, `org_id`-scoped |
| `services/document_embedding.py` (`embed_document`) | dedicated embedding path (routes through LiteLLM `/v1/embeddings`, not `call_claude_*`) | `embedding_document` | `ai.embedding.model` / `ai.embedding.fallback_chain` |
| `services/document_embedding.py` (`embed_query`) | same | `embedding_query` | same |

Cross-checked against live `ai_decision_log` data (real production rows, not
fixtures): `document_classifier` (92 rows), `note_terms_extraction` (54),
`note_terms_hazard_ensemble` (54), `member_brief` (41), `workflow_generation`
(34), `assistant` (3). The other `task_type`s in the table above have not
yet fired in this environment (zero rows) — a real, honest finding, not
evidence the call site is unreachable.

### 3b. Org-selectable vs platform-pinned, and the Textract question

Every task above is a genuine candidate for org-level model *assignment*
(Phase E) since all are already config-resolved per-org. None is
structurally forced platform-only by the code today — `ai.model.assistant`,
`ai.model.default`, `ai.model.document_classifier`,
`ai.embedding.model`/`fallback_chain` are all already org-settable keys
(Task 4 covers what's actually stored).

**Textract, confirmed NOT an LLM and NOT routed through LiteLLM.**
`services/textract.py` and `services/textract_extraction.py` call AWS
Textract directly (OCR — bounding boxes / text extraction from scanned
documents), with zero references to `call_claude_*`, `resolve_model`,
`org_settings`, or LiteLLM anywhere in either file. It has its own entirely
separate credential path (AWS IAM, per the SES-blocker memory — the same
Textract IAM user was found to have no SES permission, confirming it is a
distinct, narrowly-scoped credential, not routed through any AI-provider
abstraction). **It would be wrong for a model picker sourced from LiteLLM's
`/model/info` to list it — it isn't a LiteLLM deployment and never will be
under the current architecture.** No other non-LLM AI service was found in
the same position — grepped explicitly for AWS Polly/Transcribe/Rekognition/
Comprehend and found none (consistent with the design doc's "zero voice call
sites exist" finding); Textract is the only non-LLM AI dependency in the
codebase today.

### 3c. Real, current `ai.model.fallback_chain` / `ai.embedding.*`, per org, as actually stored

Queried live (`org_settings`, via `platform_scope`, both real orgs):

- **2nd Act Capital** (`00000000-0000-0000-0000-000000000001`) — the only
  org with ANY explicit `ai.*` rows, and only 6, all from the original
  mini-bedrock seed: `ai.model.assistant` = `"claude-sonnet-4-6"`,
  `ai.model.default` = `"claude-haiku-4-5-20251001"`,
  `ai.model.document_classifier` = `"claude-haiku-4-5-20251001"`,
  `ai.model.fallback` = `"claude-haiku-4-5-20251001"` (the dead key —
  confirmed still present as a stored row even though nothing reads it),
  `ai.model.fallback_chain` = `["claude-haiku-4-5-20251001"]`,
  `ai.model.provider` = `"anthropic"` (also dead).
- **Hollisworks** (`bb347258-8f28-4f49-8cc9-e29ccad82884`) — **zero**
  `org_settings` rows of any kind under `ai.*`. Every AI-related read for
  this org resolves entirely from `DEFAULT_SETTINGS` in code.
- **No org, anywhere, has ever written an `ai.embedding.*` row.** Every
  org's embedding provider/model/dimensions/fallback-chain is on the
  code-level default (`voyage`, `voyage-3.5`, `1024`,
  `["voyage-3.5"]`) — live-confirmed by the same query returning zero
  `ai.embedding.%` rows.

---

## Task 4 — What `org_settings` can already express

### 4a. Real current shape + fallback resolution

`org_settings` is `(id, org_id NOT NULL, setting_key, setting_value jsonb
NOT NULL, category, is_public, updated_at, updated_by, created_at)`, unique
on `(org_id, setting_key)`. Resolution (`get_setting`) is a single-row
lookup by `(org_id, key)`, falling back to `DEFAULT_SETTINGS[key]` (a plain
Python dict literal in `services/org_settings.py`) when no row exists —
confirmed live by Task 3c above: Hollisworks resolves every `ai.*` value
purely from that in-code dict, with no DB row backing any of them.
`ai.*` keys currently defined in `DEFAULT_SETTINGS`: `ai.model.default`,
`ai.model.provider` (dead), `ai.model.fallback` (dead),
`ai.model.fallback_chain`, `ai.model.assistant`,
`ai.model.document_classifier`, `ai.embedding.provider`,
`ai.embedding.model`, `ai.embedding.dimensions`,
`ai.embedding.fallback_chain`. `modeling.*` keys are the 4 TA-model
defaults only (`modeling.ta.*` — strategy params, horizon, periods/year,
calibration-min-years); no `modeling.*` keys exist for anything
LiteLLM/model-related. There is currently no `ai.credential_source.*` or
equivalent key of any kind.

### 4b. Could a per-provider credential-source setting fit without schema change? — **Yes, mechanically**

`org_settings.setting_value` is unconstrained `jsonb`, and the write path
(`_validate_setting`) already has a precedent for exactly this shape: the
`ai.embedding.provider` key is validated against an enum
(`ENABLED_EMBEDDING_PROVIDERS`) at write time, rejecting disallowed values
server-side even though the frontend may display more options. A
`ai.credential_source.<provider>` key (e.g.
`ai.credential_source.anthropic` = `"org"` | `"platform"`) would slot into
the identical `category = "ai"` grouping with an identical validation
pattern — **no schema change required.** The one real constraint: every new
key needs an entry in `DEFAULT_SETTINGS` (per this module's own stated
invariant that a key with no per-org row must still resolve to *something*,
never `None`), which itself needs a real product decision (does a newly
created org default to `"platform"` or `"org"` per provider?) — a Phase D/E
design question, not a technical blocker.

### 4c. The real platform-scope mechanism — and the real gap for a curated model list

**`org_settings` has no `owner_scope` column and no platform-scope ROW is
possible** — confirmed both by the schema (`org_id` is `NOT NULL` with an FK
to `organizations`, no `owner_scope` column exists) and by the module's own
docstring, which states this explicitly: *"there is no platform-scope ROW to
seed — the platform default is the fallback in \[DEFAULT_SETTINGS\], and an
org_settings row is an org's OVERRIDE of it."* The only genuinely real
`owner_scope: 'platform' | 'org' | 'team' | 'user'` mechanism in this
codebase lives in a **different table entirely** —
`portfolio_udf` definitions (`services/portfolio_udf.py`,
`routers/udf.py`), which has its own `owner_scope` column purpose-built for
that subsystem.

**Consequence, stated plainly: a Hollisworks-curated model list cannot live
in `org_settings` as a genuine platform-editable-via-UI row today.** The
only two real options right now are (a) a code-level constant (exactly what
`DEFAULT_SETTINGS` already is — edits require a deploy, not an admin
screen) or (b) a new table/mechanism modeled after `portfolio_udf`'s
`owner_scope` pattern — which is schema work, correctly out of scope for
this discovery sprint. This is a genuine, confirmed gap for Phase D design
to resolve, not an assumption.

---

## Task 5 — The alert path, confirmed real

### 5a. `create_held_run_alerts` — real signature, real recipients, reusable as-is

`services/workflow_todos.py:142` —
`async def create_held_run_alerts(conn, *, org_id, run_id, started_by, error_detail: str | None) -> list`.
Recipients: the run's `started_by` (if any) **plus every
`users` row in that org with `role = 'org_admin'`** — the exact fan-out rule
CLAUDE.md's confirmed decision names. It calls the shared `_upsert_todo`
helper, which is generic (keyed on `org_id, user_id, source, related_type,
related_id`) — **a credential-failure alert does not need a sibling
function**; it can call `_upsert_todo` directly with its own `source`/
`related_type`/`action_key`, exactly as `create_trigger_expiring_alerts`
already does alongside `create_held_run_alerts` in the same file (both call
the same underlying primitive, neither duplicates the recipient-fan-out
logic — a real, already-proven reuse pattern in this exact file).

### 5b. Real `source`/`related_type`/`action_key` conventions

From the three existing alert producers in `workflow_todos.py` and
`todo_generators.py`:
- `source` is a short, stable, machine-only string unique to the alert kind
  (e.g. `workflow_run_held`, `workflow_trigger_expiring`,
  `pending_subscriptions`, `unsigned_documents`) — used as part of the
  idempotency key since `member_todos` has **no unique constraint beyond its
  own `id`** (confirmed in this file's own docstring), so every producer
  does an explicit SELECT-then-INSERT/UPDATE on
  `(user_id, org_id, source, related_type, related_id)`.
- `related_type` names the kind of thing the alert is about
  (`workflow_run`, `workflow_trigger`, `spv_subscription`,
  `spv_document`) — a credential-failure alert would need its own, e.g.
  `org_provider_credential` or similar, paired with a `related_id`
  identifying which provider/org-credential failed.
- `action_key` is a **frontend route path** the todo deep-links to
  (`/admin/workflows/runs`, `/admin/workflows/triggers`, `/spvs`) — not a
  permission key or an assistant-action key (that is a different, unrelated
  use of the same column name elsewhere in the codebase, e.g.
  `services/action_registry.py` and `services/fee_runs.py`, which are a
  genuinely separate convention on a differently-typed table). A
  credential-failure alert's `action_key` should point at wherever the
  org's AI/provider settings actually live in the admin UI.
- `category` groups alerts for the settings/dashboard UI (`workflow` for
  the three above); a credential-failure alert would want its own, e.g.
  `ai` or `credentials`, to match `org_settings`' own `category` convention
  for the `ai.*` namespace.
- `kind = 'actual'` (vs `'anticipated'`) for every alert producer found —
  a credential failure is a real, current problem, so `'actual'` is the
  correct value, not a new third kind.
- `priority`: `create_held_run_alerts` uses `5`, the highest of any producer
  in the codebase (`create_trigger_expiring_alerts` uses `4` specifically to
  sit just below it — see that function's own docstring reasoning). A
  credential failure that is actively blocking every AI call for an org
  is at least as urgent as a held workflow run; `5` or higher is the
  precedent to follow.

### 5c. What an org_admin actually sees today — real endpoint, confirmed live

`GET /dashboard/todos` (`routers/dashboard.py`) — any authenticated user of
the org, org_admin or not, reads their own `member_todos` rows
(`WHERE user_id = $1 AND org_id = $2 AND status = 'open'`), split into
`actual`/`anticipated` and sorted `priority DESC, created_at DESC`. There is
no separate "admin alerts" screen — an org_admin sees a credential-failure
alert **in the exact same personal todo list every member sees**, just
addressed to their own `user_id` (since `create_held_run_alerts`-style
fan-out inserts one row per org_admin `user_id`, not one shared row). This
is a real, currently-read screen (not a table nobody queries) —
`PATCH /dashboard/todos/{id}` lets the admin dismiss or complete it from
there. No further plumbing is needed for a credential-failure alert to be
seen; reusing the existing producer pattern is sufficient.

---

## Summary of confirmed gaps for Phase D/E design to resolve

1. **BYO-key must be implemented at the model-deployment layer, not the
   Virtual-Key layer** (Task 1a) — Virtual Keys cannot carry credentials at
   all, confirmed by a live probe, not inferred.
2. **Same-`model_name` deployments load-balance rather than route
   deterministically by owner** (Task 2) — a naming/indirection scheme
   (distinct logical deployment names per credential owner, mapped to a
   friendly org-facing label) is required design work, not a detail.
3. **A Team's `models` allowlist must be explicitly and completely
   populated — an empty/unset list does not mean "no access," it appears to
   mean "all models"** (Task 1c) — a real safety rule for the curated-list
   design.
4. **There is no real platform-scope row mechanism in `org_settings`**
   (Task 4c) — a Hollisworks-curated model list needs either a code
   constant (current pattern, no admin UI) or new schema modeled on
   `portfolio_udf`'s `owner_scope` — both are open Phase D decisions.
5. **A per-provider credential-source setting fits `org_settings` with no
   schema change**, following the `ai.embedding.provider` enum-validation
   precedent exactly (Task 4b) — a real, low-risk path forward.
6. **The credential-failure alert needs no new notification mechanism** —
   `_upsert_todo` plus the same org_admin fan-out `create_held_run_alerts`
   already uses is sufficient; it needs its own `source`/`related_type`/
   `action_key`/`category` values only (Task 5a/5b), and lands on the same
   real, already-read `GET /dashboard/todos` screen every org_admin already
   uses (Task 5c).
