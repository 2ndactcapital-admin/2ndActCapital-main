# Workflow template library (WFTEMPLATES1)

Ready-made workflows for an RIA / family office. Each template is a **plain-English description** plus a **BPMN
diagram**. A firm browses the catalog (Workflows → Templates), previews a template, and **copies** it into the firm as
a NEW draft workflow that it then owns and edits in the existing diagram editor. Wave 2 rules hold throughout: BPMN
is canonical, the firm owns every edit after the copy, runs pin to a version, and nothing writes autonomously.

Code: `services/workflow_templates.py` (rules), `routers/workflow_templates.py` (routes), `docs/wftemplates1_part1.sql`
(schema), `scripts/load_workflow_templates.py` (operator loader). Web: `/admin/workflows/templates`,
`/admin/workflows/templates/[id]`, `/admin/platform/workflow-templates`. Discovery: `docs/WFTEMPLATES1_DISCOVERY.md`.

## Two kinds of template

| | Platform template | Org template |
|---|---|---|
| Owner | the Hollisworks platform org (`bb347258-…`) | the firm that saved it |
| Who writes | a Super Admin drafts; a **different** Super Admin publishes (maker-checker, also a DB CHECK) | anyone in the firm holding `author_workflows`, from one of the firm's own workflows |
| Who reads | every client firm, once a version is **published** | that firm only (another firm gets a 404) |
| States | draft → published / rejected; published → retired | saved published; published → retired |

A template **version is immutable** once inserted (a trigger freezes the title, category, description, BPMN, derived
steps, parameters and prerequisites); a published version may only be retired. A status change archives the
outgoing state on the system axis (the live row keeps its id — copies reference it).

## Using a template

`POST /admin/workflow-templates/{id}/use` (`author_workflows`): the caller fills the parameters; the BPMN is
substituted, **re-validated by the shared validator against the firm's own Profiles**, laid out, and ONE transaction
writes a new `workflow_definitions` row, its version 1, the derived steps, and a `workflow_template_copies`
provenance row (which template, which version, the values used).

- **The copy is a draft.** There is no other activation state on a definition: nothing runs a copy until someone
  with `configure_workflow_triggers` adds a schedule or event trigger on the existing Triggers screen.
- **The firm owns it.** Editing the copy never touches the template. A new template version never changes the copy:
  the firm sees "a newer version exists" with the step diff (Templates page and `GET …/copies`) and may start a new
  copy. Nothing is overwritten.
- **Retiring a version keeps copies working** — a copy is the firm's own definition and never reads the template
  again. A retired version is no longer offered for copying.

Browsing (`GET` catalog / template / copies) is open to anyone holding **any** of `author_workflows`,
`view_workflow_runs`, `configure_workflow_triggers`; a member holds none and is refused. Every read publishes the
permission envelope; `vocabularies.editable` is empty for a view-only caller.

## Authoring rules (what a template may contain)

1. **One validator.** Every stored template version and every copy passes `services.workflow_nl_generator._validate`
   — the same function the NL generator and the diagram editor use: a complete document, **no `bpmn:scriptTask`**,
   a SpiffWorkflow parse, every service task names a **registered verb**, every `assignedRoleProfileId` is a real
   Profile of the org (a platform template may name none — use a `profile` parameter instead).
2. **Wave-2 safe (platform templates).** A service task may name a verb only if, in a run, it either only resolves
   (not `workflow_invocable` — no handler runs) or stops for a second person (invocable at effective Tier 1). A
   non-invocable Tier-1 write (money, an obligation, ownership: `spv.record_transaction`, `spv.subscribe`,
   `entity.link_ownership`) is refused — make that step a user task a person does. Platform-operations verbs
   (security master, EDGAR, LiteLLM, AI drift) run unattended and are refused.
3. **Tiers are computed, never typed.** Each version stores its steps with the verb's registry tier, access type,
   required permission and invocability, and the EFFECTIVE tier the engine will use
   (`workflow_engine.compute_effective_tier`). "Needs approval" = invocable at effective Tier 1.
4. **No gateway conditions.** Sequence-flow `conditionExpression`s are evaluated by SpiffWorkflow's in-process
   Python engine (the same hole scriptTask refusal closed). The shared validator does not yet refuse them (a
   follow-up); templates are linear, and a decision is a user task ("approve or return with comments").
5. **Parameters** are `{{name}}` placeholders, each declared in the template's `.md` JSON block: `text` (≤ 120
   characters, XML-escaped), `integer` (min / max), `choice` (one of the declared keys), `profile` (one of the
   copying firm's Profiles, substituted into `assignedRoleProfileId`; empty = unassigned). Every placeholder is
   declared and every declared parameter is used. Each non-profile parameter has a default; the template is
   validated with the defaults.
6. **Files.** `docs/workflow_templates/<key>.bpmn` (the diagram) + `<key>.md` (a ```` ```json template ```` block —
   key, title, category, parameters — then the plain-English description). Categories: onboarding, reviews &
   compliance, trading & rebalancing, cash & transfers, documents, alternatives / capital calls, tax, client service.

## The shipped catalog (12)

| key | title | category | automated steps (verb → governance) |
|---|---|---|---|
| `new_member_onboarding` | New member onboarding checklist | onboarding | `entity_graph.show_hierarchy` → reference |
| `custodian_account_opening` | Account opening at the custodian | onboarding | `entity_graph.show_hierarchy` → reference |
| `annual_client_review` | Annual client review | reviews & compliance | `portfolio.show_allocation`, `portfolio.find_my_investment`, `crm.draft_note` → reference |
| `outbound_communication_review` | Compliance review of outbound communication | reviews & compliance | `crm.draft_note` → reference |
| `quarterly_rebalance_review` | Quarterly rebalance review | trading & rebalancing | `portfolio.show_allocation` → reference |
| `distribution_received` | Distribution received | cash & transfers | `spv.list_open` → reference; **`spv_carry.propose_from_realization` → needs approval (Tier 1)** |
| `document_intake` | Document intake | documents | `tasks.my_todos` → reference |
| `capital_call_received` | Capital call received | alternatives / capital calls | `spv.list_open` → reference |
| `tax_loss_harvesting_sweep` | Tax-loss harvesting sweep | tax | `portfolio.show_allocation` → reference |
| `rmd_reminder` | Required minimum distribution reminder | tax | `portfolio.show_allocation` → reference |
| `held_away_statement_refresh` | Held-away statement refresh | client service | `portfolio.show_allocation` → reference |
| `beneficiary_estate_review` | Beneficiary and estate document review | client service | `entity_graph.show_hierarchy` → reference |

Every other step is a **user task — a person does it**. The registry has no verb for drift checks, rebalance or
harvest proposals, capital calls, RMDs, held-away import, compliance review, KYC or custodian forms (discovery 1b);
those steps become automated only when such verbs are registered (and, for a write, only as a suspended Tier-1 step).
"Distribution received" adds a carry-proposal step the brief did not list: it is the one Wave-2 verb that writes,
and it shows the Tier-1 suspension in a real template (on approval it records a DRAFT carry run; it never posts).
None of the brief's twelve was dropped.

## Operator steps

1. **Schema** (done by the sprint): `python3 apps/api/scripts/_apply_wftemplates1.py --verify` → `SCHEMA OK`.
2. **Dry run** (offline, no database): `python3 apps/api/scripts/load_workflow_templates.py --dry-run` → `DRY RUN: 12
   template(s) validated, 0 failed`.
3. **Load as drafts** into the real platform org, as one Super Admin:
   `doppler run -- python3 apps/api/scripts/load_workflow_templates.py --real --drafter <that Super Admin's users.id>`.
   Idempotent (a template whose latest draft / published version has the same BPMN checksum is skipped).
4. **Publish**: a **different** Super Admin opens Platform → Workflow templates (signed in to the Hollisworks
   tenant, so the org is the platform org) and publishes each draft (or rejects it with a note).
5. Usage appears on the same screen as counts only (copies, organisations) — never which firm.

## Verify

`apps/api/scripts/verify_wftemplates1.py` (in `scripts/regression_set.txt`): validator refusals named and the three
writers on one validator, the shipped catalog in `--dry-run` with registry tiers, maker-checker publish, browse /
preview / prerequisites, use with parameters + provenance + an engine run that suspends at the Tier-1 step, the edit
and newer-version guarantees, org templates isolated, usage counts only, RLS both ways, real rows unchanged; the
regression is `verify_tiergating.py` once, flat, after teardown.
