# Cross-Project Status Reconciliation — Live Database Discovery

**Date of discovery**: this session. **Method**: direct live-schema query against `mmgwmcinimzuhargsazs` via Supabase MCP — not summarized from any chat's own claims, including this one's.

**Why this exists**: multiple parallel chat threads working on this same project have made conflicting or stale claims about what's actually built. This document reconciles those claims against the real, live database as ground truth, and should be re-run periodically as new threads accumulate more independent work.

---

## RLS enforcement cutover — 2026-09-10, live in Doppler, Render restart still unconfirmed

Full detail in `docs/PROJECT_STATUS.md`. Summary for cross-thread visibility:

- **Real, confirmed finding**: the deployed application's `DATABASE_URL` had
  been connecting as the `postgres` role (`rolbypassrls=true`) since before
  this session — every RLS policy across public + portfolio was unenforced
  by the running app. As of this entry, `DATABASE_URL` in Doppler
  (`hollisworks`/`prd`) genuinely connects as `app_service`
  (`rolbypassrls=false`) — confirmed by a real connection immediately before
  every proof below.
- **Discovery**: live-queried all 171 base tables in public+portfolio — 100%
  have RLS enabled with ≥1 policy; 213 policies inspected individually, no
  coverage gaps found. `app_service`'s GRANT set is fully sufficient (684
  individual privilege checks, zero missing).
- **Two real code bugs found by testing, both fixed and both the SAME root
  shape** — a code path relying on the `postgres` bypass with no RLS context
  set, invisible until `app_service` actually enforced it:
  1. `workflow_scheduler_tick.py`'s platform-wide trigger scan (raw
     connection, no context at all) — fixed via
     `services.database.platform_scope`, proven via
     `apps/api/scripts/verify_schedulerappservicefix.py` (15/15 PASS).
  2. `main.py`'s FastAPI startup hook (`sync_catalog`, seeding
     `assistant_action_catalog`) — went through the normal RLS-aware pool
     but never called `set_rls_context`. Found live by this sprint's own
     smoke test starting a real `TestClient`. Fixed the same way.
  **Grepping for raw `asyncpg.connect()` calls (the method used to scope
  bug #1) does NOT catch bug #2's shape** — it's a missing
  `set_rls_context`, not a bypassed pool. Any other startup-time or
  system-triggered write in this codebase should be checked for the same
  gap before being trusted post-cutover.
- **Real proof, live**: `apps/api/scripts/verify_rlscutover.py`, 21/21 PASS —
  reads across portfolio/workflow/fee/TA-model/UDF, a real UDF write
  (independently re-read to confirm persistence), cross-org isolation
  proven through the real app (a 2nd Act `org_admin` reads their own org's
  fixture, HTTP 200; the identical query against a Hollisworks fixture,
  HTTP 404 — contrasted with the old bypass, which would have returned it),
  and a real scheduler tick firing TWO orgs' due triggers in one pass with
  `DATABASE_URL` not overridden.
- **What's still NOT confirmed**: whether Render's two live services
  (`2ndactcapital-api`, `2ndactcapital-workflow-scheduler`) have actually
  restarted to pick up the new `DATABASE_URL`, or even deployed the two code
  fixes above — this environment has no Render API/CLI/MCP access (a
  standing gap, re-confirmed this session). Do not assume either has
  happened without checking the Render dashboard directly.
- **If another thread picks this up**: the database-and-code-level cutover
  is real and proven; what's unproven is whether the LIVE deployed
  containers reflect it. Check Render directly before assuming either way.

---

## Live schema summary

| Schema | Table count | What it is |
|---|---|---|
| `public` | 145 | Core application tables |
| `portfolio` | 30 | Portfolio Reporting Layer + TA Model + CRM UDF module |
| `litellm` | 77 | LiteLLM's own internal tables (not application data) |

---

## Confirmed done (real schema + real work, cross-checked)

- **SPV Manager** — real, substantial: `spvs`, `spv_subscriptions`, `spv_transactions`, `spv_transaction_allocations`, `spv_carry_runs`/`spv_carry_run_lines`, `spv_fee_terms`, `spv_fee_side_letters`, `spv_documents`, `spv_status_history`, `portfolio.spv_derived_positions`.
- **Portfolio Reporting Layer + UX** — Positions/Transactions/Securities, all phases, permissions retrofit.
- **TA Model integration** — `portfolio.ta_model_params`, `portfolio.ta_calibration_results` confirmed live; all 4 sprints.
- **CRM UDF module** — `portfolio.udf_definitions`, `udf_values`, `udf_layouts`, `udf_layout_sections`, `udf_layout_items`, `udf_field_permissions`, `udf_tab_permissions`, `udf_tabs`, `udf_tag_assignments`, `udf_definition_audit` all confirmed live; 6 sprints, 462 assertions.
- **Workflow Scheduler** — `workflow_definitions`, `workflow_runs`, `workflow_run_steps`, `workflow_steps`, `workflow_triggers`, `workflow_versions` confirmed live; all 5 sprints.
- **Fee/Billing module** — substantial real schema (fee assignments/invoices/schedules, chart_of_accounts, journal_entries/lines, ledger_books, posting_templates, revenue_events, GL views). fee31 → fee43 shipped per that thread's own reports.
- **LiteLLM Phase A + B** — proxy deployed, application code routes through it, rollback proven. **No usable tool/UX exists yet** — this is plumbing only.
- **Account layer + household precedence override** — RFC exchange resolved: `account_id` nullable on `positions`; `portfolio_precedence_household_overrides` exists, confirming the recommended extension (not a duplicate `data_source_precedence` table) was implemented. Currently empty — not yet exercised against real data.

---

## Genuinely surprising finding — flagged to the Altruist/OAuth thread

**`altruist_connections`, `altruist_oauth_states`, `altruist_one_evaluations`, `altruist_webhook_events` all exist in the live schema — and are all genuinely empty (0 rows).**

Either scaffolded ahead of that thread's own Sprint 1, or something started and never connected. **Action needed**: that thread's Sprint 1 Task 1 discovery must check this directly before assuming a clean slate.

---

## Distinction worth being precise about — the "agentic layer"

`assistant_action_catalog` has 16 real, live rows — the **S11 action-registry infrastructure**, **not** the ~6-8 dedicated agents/Desks layer (see the full spec below — this layer is real design work, genuinely not built). Table existence does not imply the higher-level feature is complete.

---

## Confirmed NOT done

- Workflow Manager Wave 2 (NL-authored, editable BPMN) + the NL-to-workflow-template library.
- **Agentic layer / Desks** — gated on Wave 2. See the full, real spec below — 14 real design decisions exist, zero are built.
- **LiteLLM Phases C–J** — Voyage routing, model picker, task assignment, budget UX, reporting, recommender, voice.
- Deal Diligence Engine AI wiring, Pipeline A, Chancery/Document Vault, TaskRouter, correspondence tracking, voice onboarding, MCP connector registry, retention policy.
- Custodial Flat Files ingestion, Altruist OAuth integration.

---

## Structured Investments — unresolved, needs direct follow-up

No table literally named for this. Closest candidate: `portfolio.securities_global_note_terms` + `note_terms_field_registry` + `note_terms_stp_policy` — structured-*note* infrastructure, coverage of full intended scope **not confirmed**.

---

## Financial / Cash-Flow Planning module — corrected status

**Scoped in detail (`acct00`–`acct02`, `cash00`–`cash12`), zero sprints executed.**

- **Prerequisite, not started**: read-only Altruist integration — tables exist but empty, matching the Altruist finding above.
- **Real, unresolved Altruist questions**: write-endpoint sandbox access, brokered CD availability, actual Altruist One cash rate, whether Altruist takes a spread on cash (needed for ADV Item 5 disclosure).
- **Design decision made**: instrument selection driven by account tax treatment — T-bills dominate CDs on after-tax yield for NY-domiciled taxable members.
- **Differentiated product**: obligation-matched laddering.
- **Newly-actionable dependency**: the plan flags a potential overlap between the obligation ledger and SPV commitment tracking, to resolve before `cash00`. TA Model Sprint 4 built a real obligation ledger tonight (`GET /modeling/ta/obligations/{commitment_id}`, read-time, 36-month visibility) — very likely the resolution. Worth a direct discovery sprint confirming this before scoping `cash00`.

---

## Recommended next actions

1. Send the Altruist-tables-already-exist finding to that thread before their Sprint 1 runs.
2. Confirm the fee-module thread intends to actually populate/exercise `portfolio_precedence_household_overrides`.
3. Run a direct discovery sprint on Structured Investments' real scope.
4. Run a direct discovery sprint on whether TA Model's obligation ledger resolves the cash-planning module's SPV-overlap dependency before scoping `cash00`.
5. **New, from the agentic-methods review**: items #3 and #5 below (capability annotation on the action registry; `review_role` on the proposal queue) get more expensive to retrofit every sprint they wait — worth scoping before more Workflow Manager Wave 2 / TaskRouter (S27) work proceeds, since #2 and #10 below are described as literally "the substance of S27."
6. Re-run this kind of live-schema discovery periodically — chat-level summaries alone have already proven unreliable multiple times in one session.

---

## Undeveloped specs — real design work not yet tracked as a sprint or TODO

Populated by running the two-part review prompt (`docs/SPRINT_WORKFLOW_STANDARD.md`) against each chat thread. Entries stay here until they become a real sprint or tracked TODO item — move, don't delete, at that point, so this remains a record of where each piece of design work originated.

### Implementing agentic methods at Hollisworks

Architecture-only thread, no sprints run. **14 real, specific design decisions, none tracked as a sprint or TODO anywhere else.** Full detail below — this is substantial enough to warrant its own future discovery-sprint sequence, not a single line item.

**Suggested priority, per that thread's own sequencing note**: #3 and #5 are schema decisions that get more expensive the longer they wait — do these before more S27/Workflow-Manager work proceeds. #13 is roughly an afternoon of work. #2 and #10 ARE the substance of S27 — scope them together with whatever picks up TaskRouter. #6, #9, #14 are product work that can run in parallel with the engineering track.

1. **Agents-propose / code-disposes as a platform invariant.** No agent writes to a domain table; all agent output lands in Tier-1 proposal rows. *Scope*: confirm the proposed-state table can carry every object type an agent would produce (documents, adjustments, memos, scores, obligations), or whether it needs a generic payload + object_type shape.

2. **Agent contract belongs in S27, not S29a.** Six constraints — principal-as-user, workflow-instance execution, bounded loop, per-step decision log, idempotency, eval gate — land before the first real agent. *Scope*: whether S27's TaskRouter schema already covers per-step logging or needs new `agent_runs`/`agent_run_steps` tables.

3. **Capability annotation on the action registry.** Each verb carries capability (SOC vocabulary) + tier + idempotent, so `tools_for(allowlist ∩ principal ∩ ceiling)` filters at the registry layer, not per-endpoint. *Flagged in-thread as cheap now, expensive to retrofit across S26–S29a — highest-urgency item on this list.*

4. **Eight agents, boundary = tool allowlist × reviewer role.** Chancery, Custodial Ops, Portfolio & Suitability, Deal & SPV, Fund Admin & Billing, Compliance Analyst, Hollis (member, read-only), Authoring (internal). *Scope*: map the ~85-verb taxonomy onto the eight allowlists; find verbs belonging to none or several.

5. **`review_role` on the proposal queue.** Routing by reviewer from day one, not a global queue segmented later — direct consequence of #4's boundary rule. *Scope*: column + routing rules + queue-per-role UI.

6. **Desks as the user-facing object.** Presentation layer between agents (8) and skills (dozens); 15–20 desks, each a config row: name, scope, agent, task keys, queue, human owner. Two rules: no human first names; every desk renders its owner. White-label-native. *Scope*: desks table, resolution from task key → desk → agent, and the naming convention as a written standard.

7. **Compliance Analyst ≠ Compliance Officer.** Agent emits findings-with-evidence, no disposition field; the CCO's disposition is a separate human-principal write. Agent runs are 204-2 records; prompt versions are supervised documents; eval results are 206(4)-7 testing evidence; the Compliance agent cannot surveil its own runs (real S30 SOC-matrix constraint). *Scope*: schema shape that makes a disposition field structurally impossible, plus the self-supervision exclusion rule.

8. **Progressive context loading with a manifest.** Stage 0 cached prefix → stage 1 manifest (what exists, not contents) → stage 2 agent-requested reads → stage 3 retrieval. Read tools return `{value, source_ref, as_of}`. *Scope*: manifest generation per entity; token/accuracy comparison vs. stuffing.

9. **Three-depth progressive disclosure in review UI.** L1 claim / L2 reasoning + evidence / L3 full trace. L2 assembles from `source_refs` in the step log, not agent prose. *Scope*: the review console — L2 is the piece most systems skip, and the reason approval doesn't become rubber-stamping.

10. **`entity_context(entity_id, as_of, principal)` as the single shared assembler.** Seven fields; one-hop graph by default; mandatory `as_of` with no default-to-now; built through `resolve_entity_set`; flags rendered first. *Flagged in-thread as the sleeper dependency — if each agent assembles context its own way, you get eight subtly different visibility bugs.*

11. **Workflow context: variables cross step boundaries, reasoning does not.** Prior steps' structured outputs are readable; their chain-of-thought is not. One `as_of` stamped for the whole run. *Scope*: what the workflow engine currently passes between steps.

12. **Guardrails: four layers, only the top one is a prompt.** Tool surface / write boundary / budget are non-bypassable; instructions are for quality only. Plus input isolation (third-party PDF text is data, not instructions — real vector once Chancery ingests K-1s) and no self-escalation (an agent can't write `agent_defs` or widen its own allowlist).

13. **Fixed escalation-reason enum.** `budget | max_steps | tool_error | low_confidence | refused | ambiguous`. Free-text makes operating metrics useless within a month. *Trivial to add now.*

14. **Acceptance rate as the primary production quality metric.** Per agent, per org; a drop is an incident. Shadow mode (agent runs on real traffic, proposals nobody acts on, compare to what humans did) as the promotion gate before Tier 1. *Scope*: metric definition, shadow-mode plumbing, thresholds.

**Custody cliff** — not a work item, a permanent standing rule, now also recorded in `CLAUDE.md`: trade execution, money movement, filing submission, and GL posting never get an agent. Permanently, not phase-two.

*(Additional thread entries pending as remaining reviews complete.)*

---

## Also found and fixed this pass

- **Two contradictory copies of `SPRINT_WORKFLOW_STANDARD.md` existed simultaneously** — the `sprint_prompts/` copy was a stale ancestor missing 5 real sections (heredoc save method, Part-1 apply-before-handoff timing, the 8-entry gotchas section, DB-contamination recovery, the regression-check stuck-transaction caveat, FK teardown ordering). Root copy confirmed canonical; `sprint_prompts/` copy replaced with a one-line pointer. Found independently by the "Implementing agentic methods at Hollisworks" thread during its own review pass.
- **`litellm_diagnose.py` removed from Project Context** — was a one-off debugging script for the now-resolved `PROXY_ADMIN` role-resolution bug; sitting as ambient context in every chat with nothing left to diagnose. Kept in the repo for historical reference; no longer loaded into every thread's context window.
