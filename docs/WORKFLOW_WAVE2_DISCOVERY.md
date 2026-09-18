# Workflow Manager Wave 2 — Discovery Findings

**Sprint**: wave2discovery.lowrisk — read-only, no code/schema changes.
**Reconciled against**: live source at HEAD of `wave2discovery-work` (branched
from `main`), which includes `workflowmgr1`–`workflowmgr5.structural` and
`workflowbpmnfix.structural`, all already merged to `main`.

## Headline correction to existing status docs

`docs/CROSS_PROJECT_STATUS_RECONCILIATION.md` (2026-09-10) lists "Workflow
Manager Wave 2 (NL-authored, editable BPMN)" under **Confirmed NOT done**.
`docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md` (2026-09-12), §5.3, largely agrees
and lists the gating engine, palette restriction, and script engine as "none
scoped as sprints." Neither is accurate as of this sprint's read of the repo:

- Five structural sprints — `workflowmgr1` through `workflowmgr5` — are
  already committed to `main` (`0ce8b78`, `dfd080e`/`b3490db`, `7de5853`,
  `40ff227`/`53a7617`, `76f4861`), spanning 2026-07-29 onward, plus a later
  fix (`workflowbpmnfix.structural`, `72d70e2`, 2026-08-26). None of these six
  sprints appear anywhere in `docs/PROJECT_STATUS.md` either — the omission
  runs in both directions: PROJECT_STATUS.md is silent on Wave 2, and the two
  reconciliation docs assert it doesn't exist. **The NL generator, the
  execution engine, the diagram editor, the run console, and a granular
  permission model are all real, merged, and are the majority of what a
  "Wave 2" scope would have asked for.**
- What genuinely is NOT built, and where the reconciliation docs are right:
  the **verb-tier gating engine** (a Service Task actually suspending on
  Tier-1), the **NL-to-workflow-template library** (reusable templates, as
  distinct from the definitions-list "library" screen that does exist), and
  **RestrictedPython / any script-task sandboxing**. See Tasks 3 and 5 below
  — these are real, and one of them (Task 5) is more serious than "unbuilt."

---

## TASK 1 — What is actually built

### 1a. The BPMN generator

`apps/api/services/workflow_nl_generator.py`, public entry point
`generate_workflow(pool, *, org_id, description, created_by, name=None)`.

- Calls `call_claude_text` (the central LiteLLM-routed helper — no direct
  Anthropic SDK use) with a system prompt (`_build_system_prompt`) that hands
  the model the org's **real** Profile UUIDs and **real** action-registry
  keys as closed reference lists, and a mapping table restricted to
  `userTask` / `serviceTask` / `sendTask` / `exclusiveGateway` /
  start/end events. `scriptTask` and `businessRuleTask` are not offered to
  the model as options (though `businessRuleTask` is still parseable if hand
  authored — see 1d).
- Output is extracted (`_extract_xml`), sanitized for unescaped `&`/`<`/`>`
  the model copied verbatim from the member's own wording
  (`services/bpmn_xml.py::sanitize_model_bpmn`), and validated
  (`_validate`) by actually parsing it as an executable SpiffWorkflow spec
  and rejecting any `actionRegistryKey` / `assignedRoleProfileId` that isn't
  a real, verbatim match against the org's own data. One error-correction
  retry is allowed; a still-invalid result raises `WorkflowGenerationError`
  and writes nothing.
- **The output is persisted, not transient.** Only after validation passes,
  one transaction inserts a `workflow_definitions` row, its
  `workflow_versions` v1 row (`bpmn_xml`, `is_current=true`), and the
  `workflow_steps` rows derived from that XML
  (`workflow_steps_deriver.derive_and_store_steps`).
- **Consumers of the stored `bpmn_xml`**: `services/workflow_engine.py`
  (`parse_bpmn` / `_new_workflow`, to actually run it),
  `apps/web/components/admin/WorkflowDiagramEditor.jsx` (renders it in
  bpmn-js for editing), and `services/workflow_editor.py::save_new_version`
  (re-validates a hand-edited copy and stores it as a new version).
- **Generation is INSERT-only by construction**: `generate_workflow`'s
  signature has no `workflow_definition_id` parameter and the function body
  contains no `UPDATE` of `workflow_versions.bpmn_xml` — there is no code
  path that can regenerate/overwrite an existing definition. The only way to
  produce a new version of an existing definition is the separate manual-edit
  path (`workflow_editor.py`, Task 2).

### 1b. Is SpiffWorkflow actually imported and used?

Yes, for real BPMN execution — not merely installed (`SpiffWorkflow==3.1.2`
is in `apps/api/requirements.txt` and the exact version is present in the
venv). `services/workflow_engine.py` imports directly from
`SpiffWorkflow.bpmn.parser.BpmnParser`, `SpiffWorkflow.bpmn.workflow`,
`SpiffWorkflow.bpmn.serializer.*`, and `SpiffWorkflow.bpmn.specs.defaults`.
It:

- Parses stored BPMN into a runnable spec (`parse_bpmn` / `_new_workflow`).
- Drives it (`workflow.do_engine_steps()`, `_drive`) — SpiffWorkflow owns all
  actual token-passing/gateway semantics; this codebase deliberately does not
  reimplement BPMN.
- Serializes/deserializes in-flight state
  (`BpmnWorkflowSerializer`) into `workflow_runs.spiff_serialized_state`
  (jsonb) so a run can pause at a User Task and resume later.
- `workflow_steps_deriver.py` also imports SpiffWorkflow's parser
  (`parse_bpmn`) to prove derivability, and re-registers a custom
  `TaskParser` (`_BusinessRuleTaskParser`) so `bpmn:businessRuleTask`
  elements parse (as an inert `NoneTask`) even without a DMN table attached —
  SpiffWorkflow's stock parser otherwise refuses that element outright.

### 1c. workflow_definitions / workflow_steps schema and BPMN-vs-steps relationship

Live schema (per `docs/schema_snapshot.sql`):

- `workflow_definitions` — the named process (org_id, name, description,
  created_by).
- `workflow_versions` — one row per revision (`workflow_definition_id`,
  `version_number`, `bpmn_xml`, `is_current`, unique on
  `(workflow_definition_id, version_number)`).
- `workflow_steps` — one row per governed BPMN element
  (`workflow_version_id`, `step_key`, `step_type`, `autonomy_tier`,
  `assigned_role_profile_id`, `action_registry_key`, `display_name`), unique
  on `(workflow_version_id, step_key)`.
- `workflow_runs` / `workflow_run_steps` — the per-run audit trail, holding
  SpiffWorkflow's own serialized state.
- `workflow_triggers` — scheduled/event firing (`workflow_definition_id` →
  resolved to the current version at fire time).

**BPMN is canonical; `workflow_steps` is derived, not independently
maintained.** `workflow_steps_deriver.derive_steps` walks the raw XML on
every generate or save and is the *only* writer of `workflow_steps`. A
`step_key` is required to equal the BPMN element's own `id` attribute
(enforced by using it directly, and by a `DeriverError` on a duplicate or
missing id). Governance metadata
(`actionRegistryKey`/`assignedRoleProfileId`/`autonomyTier`) is read from a
`<bpmn:extensionElements><twoa:governance .../></bpmn:extensionElements>`
block under a private namespace (`http://2ndactcapital.com/bpmn/ext`) — the
standard BPMN vendor-extension mechanism, readable by both SpiffWorkflow (via
raw XML, since the SpiffWorkflow spec object itself discards vendor
extensions) and bpmn-js (via a registered moddle extension, Task 2). There is
no independent, hand-maintained `workflow_steps` authoring path — editing a
definition always means editing BPMN and re-deriving.

Only four BPMN element types produce a `workflow_steps` row at all —
`serviceTask`, `userTask`, `sendTask`, `businessRuleTask`
(`ACTIONABLE_TYPES`). Gateways, events, sequence flows, and — notably —
**`scriptTask`** produce no row and are silently skipped by the deriver (see
Task 3).

### 1d. The real execution engine

`services/workflow_engine.py`. `start_workflow_run` persists the
`workflow_runs` + one `workflow_run_steps` row per governed step on an
independent connection *before* driving the engine (a real, documented,
previously-measured bug: driving on the RLS-wrapped pool's own transaction
would make a "committed, so a later failure is recordable as held" run
silently vanish on rollback instead — `_independent_acquire`'s docstring
gives the measured before/after). It then instantiates the SpiffWorkflow
spec and calls `_drive`, which loops `workflow.do_engine_steps()` and
explicitly completes any task whose spec class is `ServiceTask` by calling
`_execute_service_task(step_key)` — i.e., **a Service Task does not
auto-complete inside SpiffWorkflow itself; this codebase's `_drive` loop is
the only thing that finishes it**, which is exactly the hook point the
`action_registry_key` handler invocation lives in.

`_execute_service_task`:
1. Looks up the step's `action_registry_key`, resolves it against
   `services.action_registry.REGISTRY`.
2. If the resolved action's `workflow_invocable` flag (opt-in, defaults to
   `False`) is not set, it stops there — records that the key resolved, but
   does not call the handler. This is still the default for the large
   majority of the 17 registered verbs.
3. If `workflow_invocable=True`, it re-checks `required_permission` against
   the member who **started the run** (`_assert_action_permission`;
   super-admin bypasses via the one shared `is_super_admin` helper) and then
   actually calls `resolved.handler(...)`. Any exception here propagates and
   the whole run is transitioned to `held` with `error_detail`
   (`_hold_run`) — a failed invocation is never silently swallowed.

A **User Task** is the only thing that actually pauses a run. `_drive`
returns to the caller once `_ready_user_task` finds a READY User Task;
`start_workflow_run` marks that step `active`, stamps `proposed_by =
started_by`, serializes state, and creates a `member_todos` row for whoever
holds the assigned role Profile (`workflow_todos.sync_user_task_todos`).
`complete_user_task` is the resume/approve path, and it enforces
maker-checker **at the application layer** (approver must differ from
`proposed_by`, raising `MakerCheckerError` before any write) *and* the
database independently backstops the same rule via a CHECK constraint on
`workflow_run_steps` — this is a real, currently-enforced maker-checker
gate, but it exists only because User Tasks are SpiffWorkflow's own natural
pause point, not because anything reads `autonomy_tier`. **See Task 5**:
Tier-1 suspension for a Service Task is a completely different, and
currently absent, mechanism.

---

## TASK 2 — The frontend

### 2a. bpmn-js in apps/web

Installed (`bpmn-js@18.22.1`, `bpmn-js-properties-panel@5.63.0`,
`@bpmn-io/properties-panel@3.48.0`) **and rendered**, not merely a
dependency. `apps/web/components/admin/WorkflowDiagramEditor.jsx` is a real
canvas editor:

- Dynamically imports `bpmn-js/lib/Modeler` + the properties-panel packages
  client-side only (avoids touching `window`/`document` during SSR).
- Registers the same `twoa` governance moddle extension the backend deriver
  reads/writes, so a loaded diagram's governance survives a round trip.
- Adds a custom properties-panel group ("Governance (2nd Act)") with three
  real, wired entries for a Service/User/Send/BusinessRule task: an
  action-registry-key picker (populated from the real registry, Service Task
  only), a Profile picker (real Profiles, User Task only), and an
  **"Autonomy tier" dropdown offering "Tier 1 — approval required" / "Tier 2
  — confirm & log" / "Tier 3 — fully autonomous"** on every governed element
  type, defaulting to the value `workflow_steps_deriver` would compute.
- "Save new version" serializes the diagram back to XML
  (`modeler.saveXML`) and posts it through `saveWorkflowVersionAction` →
  `workflow_editor.py::save_new_version`, which re-validates it exactly like
  generation and stores it as a new, immutable version (old version's steps
  untouched).
- **This UI's own "Tier 1 — approval required" label is not accurate for a
  Service Task today** — see Task 5. Selecting it changes only what is
  stored in the XML/`workflow_steps` row; it does not change what the engine
  does when that Service Task runs.
- The modeler is not palette-restricted: no custom `paletteProvider` module
  is registered, only the properties-panel governance provider. bpmn-js's
  stock task palette (Task/User/Service/Manual/Business
  Rule/Script/Send/Receive) applies as-is — an author can drop a Script Task
  onto the canvas today with no UI-level warning (see Task 3).

### 2b. Real workflow UI beyond triggers/run-console

- `/admin/workflows` — the definitions "library": lists an org's
  definitions with current version + step/tier summary
  (`approval_step_count` = count of steps with `autonomy_tier <= 2`), a
  natural-language "New Workflow" form (Phase 2 generation), and links into:
- `/admin/workflows/[id]/edit` — the diagram editor above.
- `/admin/workflows/[id]/versions` — version history (list of past
  `workflow_versions`, read-only).
- `/admin/workflows/runs` (+ a redirect-only `/runs/[runId]` deep link) — the
  known run console, now with an inline detail pane rather than a second
  parallel renderer (a prior duplicate was deliberately collapsed).
- `/admin/workflows/triggers` — the known scheduler/routines screen.
- Access to all of the above is gated by three real, granular permissions
  (`author_workflows`, `view_workflow_runs`, `configure_workflow_triggers`)
  added in `workflowmgr5.structural`, replacing an earlier blanket
  `can_manage_org_settings` check — proven granular (a user with only one of
  the three reaches only its own surface) and granted to zero seeded
  Profiles by default.
- **What does not exist**: any reusable "template library" (pick a canned
  process and customize it) distinct from the definitions list above — the
  NL generator only ever produces a brand-new definition from a free-text
  description, one at a time.

---

## TASK 3 — The script engine

### 3a/3b. RestrictedPython — not installed, not used. Nothing governs a script task, and SpiffWorkflow's own default WOULD run one unrestricted.

`RestrictedPython` does not appear anywhere in `apps/api/requirements.txt`,
in any `import`, or in the venv's installed packages — it is fully absent,
not merely unused.

**Nothing in this codebase executes, sandboxes, or specially-handles a BPMN
Script Task.** `workflow_steps_deriver.ACTIONABLE_TYPES` does not include
`scriptTask`, so a Script Task element gets no `workflow_steps` row, no
governance, and — critically — is not rejected either; `derive_steps` simply
skips any element type it doesn't recognize (`continue` on
`step_type is None`). `_validate` therefore reports no error for BPMN
containing a Script Task, so `save_new_version` / `generate_workflow` would
happily persist one.

This matters more than "the feature is unbuilt," because
**SpiffWorkflow's own default behavior fills the gap, and does so with no
restriction at all.** Confirmed directly against the installed
`SpiffWorkflow==3.1.2` package (not inferred):

- `BpmnParser`'s *default* `OVERRIDE_PARSER_CLASSES` (not something this
  codebase adds) already maps `bpmn:scriptTask` to a working
  `ScriptTaskParser` → `ScriptTask` task spec — it parses and runs
  out-of-the-box, no code change required on this side.
- `ScriptTask._execute` calls `task.workflow.script_engine.execute(task,
  self.script)`, and `BpmnWorkflow.__init__`'s default (used verbatim by this
  codebase's `_new_workflow`, which never passes a `script_engine=`
  argument) is `SpiffWorkflow.bpmn.script_engine.python_engine
  .PythonScriptEngine` — whose own docstring states plainly: *"If you are
  uncomfortable with the use of `eval()` and `exec`, then you should provide
  a specialised subclass."* No such subclass exists in this repo.
- `TaskSpec.manual` defaults to `False`, and `ScriptTask` never overrides
  it, so a Script Task is **not** a Service-Task-style "parks until our code
  completes it" element — SpiffWorkflow's own `do_engine_steps()` runs it to
  completion automatically, the same engine-step loop that advances
  gateways. It never reaches `_drive`'s `ServiceTask`-only auto-complete
  hook, so none of this codebase's permission re-check, action-registry
  resolution, or hold-on-exception logic ever sees it.

**Net finding**: no BPMN generated by the NL path can contain a Script Task
(the model is never offered it), but the hand-edit path has no such
restriction — an Org Admin authoring or editing a definition in the diagram
editor can add a Script Task from bpmn-js's stock palette, write arbitrary
Python into it, save it (validation does not object), and the very next run
of that definition executes that Python via unrestricted `eval`/`exec`,
completely outside the action registry, permission checks, and
audit/hold behavior every Service Task gets. This is a live capability of
the already-merged Phase 1–5 code, not a hypothetical of some future
script-engine sprint.

---

## TASK 4 — The sequencing dependency

The claim under review, verbatim from `docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md`
§5.3: *"Sequencing dependency not reflected anywhere: workflow steps target
entities/series, so this should land after the Investment/Series
restructure."*

**Both halves checked directly against the live code and schema:**

1. **The restructure itself is unstarted**, and it is not the vague
   "Investment/Series restructure" the note implies — it is a specific,
   named, still-unwired SPV/GL item (§5.6 of the same doc): "Two-hop
   pro-rata allocation (Investment Series → subscription ledger → Member
   Series → member) — `vehicle_type`/`master_entity_id` added as an
   **additive seam, explicitly not wired**." A "Series"→"Class" rename has
   been applied only in verify-script comments, nowhere in schema or code.
   So: not done, not partially done in any load-bearing sense — a column
   seam exists and nothing reads it yet.
2. **The premise doesn't match what's built.** `workflow_steps`' actual,
   deployed columns are `workflow_version_id`, `step_key`, `step_type`,
   `autonomy_tier`, `assigned_role_profile_id`, `action_registry_key`,
   `display_name` — there is no `entity_id`, `series_id`, `investment_id`,
   or anything resembling one. A direct grep of every workflow module
   (`workflow_engine.py`, `workflow_steps_deriver.py`,
   `workflow_nl_generator.py`, `workflow_editor.py`, `routers/workflows.py`)
   for `series_id`/`investment_id` returns nothing. A `workflow_steps` row
   governs a BPMN element (an action + a role + an autonomy tier) — it has
   no portfolio-object scope of any kind today, so it cannot "target" an
   entity or series in the sense the dependency note assumes.

**Conclusion**: the sequencing dependency as stated does not hold against
the code that actually exists. It may describe an aspiration for a *future*
step type (e.g. a Service Task that reads or writes a specific
entity/series, which would indeed want the restructure done first), but
nothing in the currently-built Wave 2 surface has that shape, so nothing in
Wave 2 as it stands today is blocked by the SPV/GL restructure. This is a
premise correction, not a "still blocked, just for a different reason"
finding.

---

## TASK 5 — The verb-tier gating gap

**This is the most important finding in this sprint. A Tier-1 registry verb
invoked from a Service Task does not suspend for approval — it simply
executes, exactly like a Tier-3 verb, the moment the engine reaches it.**

Two distinct "tier" concepts exist in this codebase and only one of them is
ever read by the execution engine:

1. **`assistant_action_catalog.tier`** (`AssistantAction.tier` in
   `services/action_registry.py`) — the stakes classification added in
   `actionregistryfix.structural`: 1 = highest stakes (moves money, creates
   an obligation, produces a third-party artifact, or mutates ownership/
   economic terms/a posted ledger line), 3 = lowest. This is a real, live
   column with real values on every one of the 17 registered verbs.
2. **`workflow_steps.autonomy_tier`** — a *separate*, per-BPMN-element value
   computed by `workflow_steps_deriver._default_tier` from `step_type` and
   the action's `access_type` (`read`/`write`) **only** — it never reads
   `AssistantAction.tier` at all. Its defaults: User Task and Send Task are
   always 1; a read-only Service Task is 3; a write-capable or unresolved
   Service Task is 2; Business Rule Task is 3. An author can override this
   per element via the BPMN governance extension (`autonomyTier="1"`), and
   the diagram editor's properties panel exposes exactly that override
   (Task 2a).

**Neither value is ever consulted by the part of `workflow_engine.py` that
decides whether to run a Service Task.** `_execute_service_task` reads only
`action_registry_key` from the step row it's handed
(`_SERVICE_STEP_MAP`) — `autonomy_tier` is fetched by `_load_steps` and
`complete_user_task`'s query, but it is used **only** for display
(`routers/workflows.py`'s `approval_step_count` summary on the library
screen) and is never branched on inside `_drive` /
`_execute_service_task`. A Service Task runs automatically the instant
`workflow.do_engine_steps()` parks it in `STARTED`, regardless of what its
`autonomy_tier` says — there is no suspension path for a Service Task at
all; only a User Task can pause a run, and that pausing is a side effect of
SpiffWorkflow's own task-type semantics, not of any tier check.

**This is not a hypothetical mismatch.** A real, already-registered,
`workflow_invocable=True` action demonstrates it concretely:
`spv_carry.propose_from_realization` (`services/spv_carry_runs.py`) is
registered with `tier=1` — the module's own comment says why: *"proposes,
but carry economics are test 3 — the GP relies on this proposal"* — and
`workflow_invocable=True`, meaning a BPMN Service Task is explicitly allowed
to invoke it. Because `_default_tier` only looks at `access_type` (this
action is `write`), a Service Task wrapping it defaults to
`workflow_steps.autonomy_tier = 2`, not 1 — and even if an author
explicitly set `autonomyTier="1"` on that element through the diagram
editor's own "Tier 1 — approval required" option, the run would still not
pause, because nothing downstream of `derive_steps` ever reads it for
Service Tasks. The action's own handler is written defensively (it only
proposes a DRAFT `spv_carry_run`, never posts, never moves money — consistent
with the custody cliff), so this specific case is not itself a
custody-cliff violation. But the *mechanism* that a UI already presents as
"Tier 1 — approval required" is not a real guarantee for any Service Task,
present or future, and nothing else in the registry currently stops a less
careful `workflow_invocable=True` action from being both Tier 1 and
silently unattended.

**Only two actions are `workflow_invocable=True` today**
(`spv_carry.propose_from_realization`, tier 1; `litellm.reload_model_cost_map`,
tier 2) — every other registered verb, including the other three explicit
Tier-1 verbs (`spv.subscribe`, `spv.record_transaction`,
`entity.link_ownership`), is not reachable from a Service Task at all yet.
So the live blast radius of this gap is small today, but it is structural,
not incidental: the next `workflow_invocable=True` opt-in on a Tier-1 verb
inherits the same silent-execution behavior with no additional code change
required to trigger it.
