# WFTEMPLATES1 — Task 1 discovery (2026-11-08)

Read from the repo at `86515c7` (merge rep15) and the live database (read-only, as `app_service`). Every claim
below names the code that shows it.

## 1a. Definitions, versions, validation, the two writers, run pinning

- **A definition** is one `public.workflow_definitions` row (`id, org_id, name, description, is_active, created_by,
  created_at, updated_at`). No UNIQUE on `(org_id, name)` — two definitions may share a name.
- **A version** is one `public.workflow_versions` row (`bpmn_xml` text, `version_number`, `is_current`,
  `change_summary`); UNIQUE `(workflow_definition_id, version_number)`. Its steps are `public.workflow_steps` rows
  derived from the XML by `services.workflow_steps_deriver.derive_and_store_steps` (one row per user / service /
  send / business-rule task; UNIQUE `(workflow_version_id, step_key)`).
- **Writer 1 — the NL generator** (`services.workflow_nl_generator.generate_workflow`): INSERT-only by construction
  (no definition / version id parameter); model output → `_extract_xml` → **`_validate`** → one retry → refusal;
  then `ensure_bpmn_di` (layout) and ONE transaction: definition + version 1 (`is_current = true`) + steps.
- **Writer 2 — the diagram editor** (`services.workflow_editor.save_new_version`): `validate_workflow_bpmn(conn,
  org_id, xml)` — which loads the org's Profiles and the registry and calls the SAME **`_validate`** — then one
  transaction: flip the old current version off, insert version n+1, re-derive steps for the new version only.
  A version row is never updated in content.
- **The one validator** is `workflow_nl_generator._validate(xml, valid_action_keys, valid_profile_ids)`:
  complete document → no `bpmn:scriptTask` (`find_script_tasks`, named per element) → SpiffWorkflow parse +
  step derivation (`derive_steps` → `workflow_engine.parse_bpmn`) → every service task has an `actionRegistryKey`
  → every key is a registered verb → every `assignedRoleProfileId` is one of the org's Profiles.
- **Tiers.** The deriver's default tier (user / send task 1, read verb 3, write or unknown verb 2, explicit
  `autonomyTier` wins) is stored on `workflow_steps.autonomy_tier`. At execution the engine uses
  `compute_effective_tier(step, action) = min(diagram tier, registry tier)` (1 is the dangerous end). A service
  task **suspends** for maker-checker approval (`agent_proposals`) only when its verb is `workflow_invocable` AND
  the effective tier is 1 (`workflow_engine._drive`); a non-invocable verb is only resolved — no handler runs.
- **Run pinning.** `workflow_engine.start_workflow_run(pool, workflow_version_id, org_id, context, started_by)`
  takes a VERSION id; the run row stores `workflow_version_id`. Triggers point at a DEFINITION and the scheduler
  resolves `is_current` at fire time — a run then stays on that version.
- **[FIND] There is no draft / active state on a definition.** `workflow_definitions.is_active` exists (default
  true) but no route writes it and no run path reads it (the scheduler's `is_active` is the TRIGGER's). Nothing
  starts a definition except a trigger (scheduled / event, `configure_workflow_triggers`), the Chancery bridge, or
  a human task. So "a copy starts as a draft" means: a new definition with NO trigger — nothing runs it until
  someone with `configure_workflow_triggers` adds one through the existing Triggers screen. This sprint invents
  no activation flag.
- **[FIND] `workflow_steps.assigned_permission` is not derivable from BPMN.** Only `services.human_tasks` writes
  it. A template's "reviewer" parameter is therefore a **Profile** (`assignedRoleProfileId`), the governance field
  the validator already checks against the org's own Profiles.
- **[FIND] Sequence-flow `conditionExpression` is evaluated by SpiffWorkflow's PythonScriptEngine** — the same
  in-process `eval` that made scriptTask refusal necessary. The shared validator does not refuse it. Not fixed
  here (changing the shared validator changes all three writers' behaviour); the shipped templates use no
  gateway conditions, and docs/WORKFLOW_TEMPLATES.md forbids them for templates. Owed: a follow-up sprint.

## 1b. The registered verbs (the vocabulary a template may use)

`services.assistant_actions.register_all()` registers 26 verbs (read live from `REGISTRY.all()`):

| verb | module | tier | access | required permission | workflow_invocable | Wave-2 template use |
|---|---|---|---|---|---|---|
| `entities.count` | entity | 3 | read | — | no | yes — resolved only |
| `entity_graph.show_hierarchy` | entity | 3 | read | — | no | yes — resolved only |
| `investments.count` | investments | 3 | read | — | no | yes — resolved only |
| `marketplace.show_new_deals` | marketplace | 3 | read | — | no | yes — resolved only |
| `portfolio.find_my_investment` | portfolio | 3 | read | — | no | yes — resolved only |
| `portfolio.show_allocation` | portfolio | 3 | read | — | no | yes — resolved only |
| `spv.list_open` | spv | 3 | read | — | no | yes — resolved only |
| `spv.show_captable` | spv | 3 | read | manage_deals | no | yes — resolved only |
| `spv.show_ledger` | spv | 3 | read | manage_deals | no | yes — resolved only |
| `tasks.my_todos` | tasks | 3 | read | — | no | yes — resolved only |
| `crm.draft_note` | crm | 2 | write (draft) | — | no | yes — resolved only (writes nothing in a run) |
| `propose` | propose | 2 | write (draft) | — | no | yes — resolved only |
| `spv_carry.propose_from_realization` | spv_carry | **1** | write (draft) | manage_billing | **yes** | yes — **suspends for approval**; on approval it records a DRAFT carry run (never posts, moves no money) |
| `spv.record_transaction` | spv | 1 | write | manage_deals | no | **no** — names a money movement |
| `spv.subscribe` | spv | 1 | write | indicate_interest | no | **no** — creates an obligation |
| `entity.link_ownership` | entity | 1 | write | manage_deals | no | **no** — mutates ownership |
| `edgar.launch_pipeline_job` | edgar | 2 | write | author_workflows | yes | **no** — platform operations job |
| `litellm.reload_model_cost_map` | litellm | 2 | write | author_workflows | yes | **no** — platform operations job |
| `ai.drift_monitor` | ai_drift | 2 | write | run_platform_jobs | yes | **no** — platform job |
| `security_data.nightly` / `.prices` | security_master | 2 | write | run_platform_jobs | yes | **no** — platform jobs |
| `security_master.attributes` / `.mutual_funds` / `.openfigi_new` / `.sec13f` / `.universe` | security_master | 2 | write | run_platform_jobs | yes | **no** — platform jobs |

Wave-2 safe = the 12 read / draft verbs that only resolve in a run, plus `spv_carry.propose_from_realization`,
whose run always stops for a second person. The three Tier-1 money / obligation / ownership verbs are excluded
even though they are not invocable (a template naming "record transaction" would read as if it did one).

**[FIND] The vocabulary is thin for an RIA catalog.** There is no registered verb for drift checks, rebalance
proposals (rep5 has the service but no verb), tax-loss harvesting, capital calls, RMDs, held-away refresh,
compliance review, KYC, custodian forms or document intake (Chancery runs through its own bridge, not a verb).
Every such step in the shipped templates is a **user task — a person does it** — and each template's page says
so. Registering those verbs is a separate sprint.

## 1c. The definitions-list screen and the editor's entry points

- `/admin/workflows` (`app/admin/workflows/page.js` → `components/admin/WorkflowLibraryManager.jsx`): the org's
  definitions + a natural-language "New Workflow" form; each row links to `/admin/workflows/[id]/edit`
  (`WorkflowDiagramEditor.jsx`, bpmn-js Modeler + properties panel) and `/versions`.
- The Workflows group (`lib/menuVisibility.mjs`, org-admin section) has tabs Workflows (author_workflows), Runs
  (view_workflow_runs), Triggers (view_workflow_runs | configure_workflow_triggers).
- **"Start from a template" fits** as a fourth tab, **Templates** (`/admin/workflows/templates`, readable by any
  of the three workflow keys), a template page (`/admin/workflows/templates/[id]`, bpmn-js read-only
  NavigatedViewer), and a "Start from a template" link beside "New Workflow" on the library screen. A copy lands
  in the existing library and opens in the existing editor; triggers stay on the existing Triggers screen.
- The platform side (draft / publish / retire, usage counts) is a Super Admin tab under Platform → Controls,
  beside rep15's "Published models": `/admin/platform/workflow-templates`.

## 1d. How a tenant reads published platform rows

rep15 is merged (`86515c7`). Its pattern (docs/rep15_part1.sql): a SECURITY DEFINER predicate
(`portfolio.provider_tenant_allowed` — a different org, the provider is the real Hollisworks platform org or an
`is_test` org, and the two orgs' `is_test` flags match), and a SELECT policy `provider_org_id = own OR super admin
OR (status IN ('published', 'retired') AND visible(provider))`; writes are owner-or-super-admin; a guard trigger
freezes published content and lets a DELETE through only on the documented fixture-teardown GUC. So a fixture
platform org can never serve a real tenant, and a real platform template is never readable by a fixture org.

The workflow tables (`public.workflow_*`) carry one `ALL` policy each: `org_id = NULLIF(app.current_org_id) OR
app.is_super_admin = 'true'`. The template tables follow rep15's per-operation policies with their own
SECURITY DEFINER functions in `public` (`workflow_template_platform_org_allowed`, `workflow_template_visible`).
