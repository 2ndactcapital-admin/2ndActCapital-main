# Cross-Project Status Reconciliation — Live Database Discovery

**Date of discovery**: this session. **Method**: direct live-schema query against `mmgwmcinimzuhargsazs` via Supabase MCP — not summarized from any chat's own claims, including this one's.

**Why this exists**: multiple parallel chat threads working on this same project have made conflicting or stale claims about what's actually built. This document reconciles those claims against the real, live database as ground truth, and should be re-run periodically as new threads accumulate more independent work.

---

## Live schema summary

Three real schemas in the deployed database:

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
- **Fee/Billing module** — substantial real schema: `fee_assignments/credits/discounts/exclusions/invoices/narratives/receipts/run_lines/runs/schedule_tiers/schedules`, `chart_of_accounts`, `journal_entries/lines`, `ledger_books`, `posting_templates`, `revenue_events`, `v_capital_accounts`, `v_profitability_events`, `v_trial_balance`. fee31 → fee43 shipped per that thread's own reports.
- **LiteLLM Phase A + B** — proxy deployed, application code routes through it, rollback proven. **No usable tool/UX exists yet** — this is plumbing only.
- **Account layer + household precedence override** — the RFC exchange between this thread and the fee-module thread resolved cleanly: `account_id` added nullable to `positions`; `portfolio_precedence_household_overrides` table exists, confirming the recommendation (extend the existing org-level precedence mechanism to household granularity) was implemented rather than a duplicate `data_source_precedence` table being built. Currently empty (0 rows) — not yet exercised against real data.

---

## Genuinely surprising finding — flagged to the Altruist/OAuth thread

**`altruist_connections`, `altruist_oauth_states`, `altruist_one_evaluations`, `altruist_webhook_events` all exist in the live schema — and are all genuinely empty (0 rows).**

This means either these tables were scaffolded ahead of that thread's own Sprint 1 (a real Part 1-style migration applied by someone, at some point), or something started and never actually connected. **Action needed**: that thread's Sprint 1 Task 1 discovery must check this directly before assuming a clean slate — building a second, parallel table for the same purpose would be a real schema collision.

---

## Distinction worth being precise about — the "agentic layer"

`assistant_action_catalog` has 16 real, live rows — but this is the **S11 action-registry infrastructure** (the `agents propose, deterministic code disposes` mechanism), **not** the ~6-8 dedicated agents/Desks layer referenced elsewhere as unbuilt. Both are real; they are different tiers. The registry mechanism exists and is populated; the agents built on top of it do not exist yet. Table existence does not imply the higher-level feature is complete — the same caution applies to `deal_scores`/`deal_ai_summaries` (schema exists, the AI-generation layer populating them is the documented gap) and to Structured Investments generally (see below).

---

## Confirmed NOT done

- Workflow Manager Wave 2 (NL-authored, editable BPMN) + the NL-to-workflow-template library.
- **Agentic layer / Desks** (~6-8 agents) — gated on Wave 2, per the platform's own sequencing.
- **LiteLLM Phases C–J** — Voyage routing, model picker, task assignment, budget UX, reporting, recommender, voice. The actual tool + UX a person would touch.
- Deal Diligence Engine AI wiring (schema exists, generation layer does not), Pipeline A / member-acquisition funnel, Chancery/Document Vault, TaskRouter, correspondence tracking, voice onboarding, MCP connector registry, retention policy.
- Custodial Flat Files ingestion, Altruist OAuth integration (Sprint 1 drafted, not yet run, real schema pre-exists per the finding above).

---

## Structured Investments — unresolved, needs direct follow-up

No table in the live schema is literally named for this. The closest candidate is `portfolio.securities_global_note_terms` + `note_terms_field_registry` + `note_terms_stp_policy` — structured-*note* infrastructure, one real category of structured investment, but coverage of the full intended scope is **not confirmed** by this discovery. Needs a direct, targeted follow-up rather than being marked done or not-done on the strength of this pass alone.

---

## Financial / Cash-Flow Planning module — corrected status

**Previously logged as "not yet scoped." This is wrong — it is scoped in detail, zero sprints executed.**

Real, existing design on record:
- Sprint sequence: `acct00`–`acct02`, then `cash00`–`cash12`.
- **Prerequisite, not started**: read-only Altruist integration (account list, balances, positions, entity mapping) — connects directly to the Altruist finding above; the prerequisite tables exist but are empty, so this hasn't actually begun.
- **Real, unresolved open questions with Altruist**: write-endpoint sandbox access, brokered CD availability, the actual Altruist One cash rate (negotiated vs. scheduled), whether Altruist takes a spread on cash (needed regardless, for ADV Item 5 disclosure).
- **Design decision already made**: instrument selection driven by account tax treatment — T-bills dominate CDs on after-tax yield for NY-domiciled taxable members.
- **The differentiated product**: obligation-matched laddering.
- **A flagged, unresolved dependency, now newly actionable**: the original planning note explicitly flags a *potential overlap between the obligation ledger and existing SPV commitment tracking*, to be resolved before `cash00`. **A real obligation ledger was built tonight** — TA Model Sprint 4, `GET /modeling/ta/obligations/{commitment_id}`, genuinely computed at read time, real 36-month visibility, proven with two commitments producing genuinely different real output. This is very likely the exact thing that planning note anticipated needing reconciliation against — worth a direct discovery sprint confirming whether tonight's work resolves this dependency before scoping `cash00` further.

**Corrected status: 🔄 scoped, zero sprints executed. Prerequisite (Altruist read-only) not started. One real, newly-relevant dependency to resolve before `cash00`.**

---

## Recommended next actions

1. Send the Altruist-tables-already-exist finding to that thread directly, before their Sprint 1 runs.
2. Confirm the fee-module thread's `data_source_precedence` decision is fully wired (the household-override table exists but is empty — worth confirming intent to actually populate/exercise it).
3. Run a direct discovery sprint on Structured Investments' real scope before marking it done or not-done.
4. Run a direct discovery sprint on whether TA Model's new obligation ledger resolves the cash-planning module's flagged SPV-overlap dependency, before scoping `cash00`.
5. Re-run this kind of live-schema discovery periodically as more threads accumulate independent work — chat-level summaries alone have already proven unreliable multiple times in one session (the Workflow Scheduler was independently reported as "not built" by a different thread despite five real, merged sprints).

---

## Undeveloped specs — real design work not yet tracked as a sprint or TODO

Populated by running the two-part review prompt (see `docs/SPRINT_WORKFLOW_STANDARD.md`'s own review process) against each chat thread in the Project. Every thread reports here regardless of whether it has ever run a real sprint — this section exists specifically to surface architecture and design work sitting in a chat that hasn't become tracked work anywhere else.

**Format per entry**: `Thread` — one-line description — enough scoping detail for a discovery sprint to start from.

*(Entries added below as each thread's review comes back. Once an entry becomes a real sprint or a tracked `OUTSTANDING_TODO_LIST.md` item, move it out of this section and note where it landed, rather than deleting it — this section doubles as a record of where each piece of design work actually originated.)*

- **Implementing agentic methods at Hollisworks** — architecture-only thread, no sprints run. Real design work discussed: agent design, tool boundaries, "desks," context assembly, guardrails — the ~6-8 dedicated agents/Desks layer referenced elsewhere as gated on Workflow Manager Wave 2. Not yet scoped as a sprint sequence anywhere. *(Awaiting that thread's own detailed Part 2 response for full scoping detail — this entry recorded on the strength of this session's own knowledge that this layer exists as a real, named architectural concept, not yet a tracked sprint plan.)*

*(Additional entries pending as each remaining thread's review completes.)*

---

## Also found and fixed this pass

- **Two contradictory copies of `SPRINT_WORKFLOW_STANDARD.md` existed simultaneously** — `sprint_prompts/SPRINT_WORKFLOW_STANDARD.md` (stale ancestor, missing 5 real sections including known gotchas, DB-contamination recovery, and the regression-check caveat) vs. the root copy (live, correct). Root copy confirmed canonical; the `sprint_prompts/` copy replaced with a one-line pointer rather than left as a second, independently-editable version — found by the "Implementing agentic methods at Hollisworks" thread during its own review pass, a genuinely valuable finding despite that thread never having run a sprint.
