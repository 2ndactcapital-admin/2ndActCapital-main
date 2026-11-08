# S29B discovery — Workflow Manager Wave 4 (unattended write steps)

Repo and live database, 2026-11-08 (branch s29b-work, base `5f460af` merge s30). Read-only probes; the effect report
for Task 0 was printed by `apps/api/scripts/_apply_s29b.py --effect` BEFORE the schema was applied.

## Task 0 — the self-grant floor, live effect (read-only, before applying)

- Live before: platform `policy:role_grant` **v1**, `enforce_floor = false` (S30 shipped it off). After: **v2**,
  `enforce_floor = true`, v1 retired and kept; audited `sod.platform_rule_versioned` (Hollisworks platform org).
- Every grant path S30 fix 1 routed through `sod_admin.check_role_grant` / `check_bundle_edit` is covered: role assign
  (`PUT /admin/users/{id}/role`), approve a pending login (`POST /admin/users/{id}/approve`), permission-set assign,
  profile assign, adding a permission to a profile / set the caller holds. (Staff provisioning goes through the same
  role-assign path.)
- Who can no longer change their OWN access (rows holding `manage_members` / `manage_roles`, or Super Admin):
  - **2nd Act Capital** — five rows, five *persons* to the rule: Joe Larizza `06fe93ed` (Super Admin, no linked
    login), Joe Larizza `f46eb620` (Super Admin, 1 linked login), joe smith `a35f8681` (org admin), jpl991723
    `73011742` (org admin), jpl991723@gmail.com placeholder `9460cacb` (org admin). Each can still be changed by
    any of the others or a Super Admin who is a different person.
  - **Hollisworks** — Joe Larizza `be8165db` (Super Admin). The two 2nd Act Super Admin rows can change it.
- **[FIND] Joe's rows are three persons, not one.** `06fe93ed`, `f46eb620` and `be8165db` are separate ACTIVE
  `users` rows with no `user_person_links` row and no duplicate-merge link (that rule only covers RETIRED rows). The
  floor compares persons, so today one of Joe's rows CAN grant another of Joe's rows a role. The prompt assumed
  "Joe's two logins are one person until Auth0 is unlinked"; in the database they are not. Fix (an operator decision,
  not taken here): merge the duplicate rows in People & Access (link the second sign-in to one row, retire the rest)
  — then the floor binds across them. The same applies to the three 2nd Act org-admin rows if they are one human.
- An organization cannot switch it off: `sod_admin.draft_rule` refuses the relaxation (403), and
  `sod.Context.policy()` now ORs the platform `enforce_floor` into any org policy row, so even an org row written past
  the API cannot relax it.
- Every refusal: a `sod_decisions` row (append-only) **and**, new in S29B, an `audit_log` row `sod.refused`
  (`sod.record_refusal`, every site) — shown in Admin → Segregation of duties → refused checks.
- `verify_s30.py`'s three role-grant expectations that assumed the floor was off are updated (pre-S30 replay,
  platform policy shape, org B self-grant).

## 1a Every registered verb — the autonomy eligibility list

26 verbs registered (`services.assistant_actions.register_all`). Money movement in this app is the
`/assistant/confirm` → `assistant_activities` path and `services.trading_authority`, neither of which is a registered
workflow verb. No registered verb submits a trade or changes roles / permissions; one changes ownership
(`entity.link_ownership`); two move money (`spv.record_transaction`, `spv.subscribe`). There is no
`eligible_with_cap` verb today (no registered bulk verb) — the class exists, with its cap, for the first one.

| verb | tier | duty (S30) | permission | workflow-invocable | writes | money | custody / authority | trades | contacts a member / outside party | platform job | class |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `ai.drift_monitor` | 2 | write | run_platform_jobs | yes | ai_drift_alerts (opens / closes drift alerts) + alert to-dos | no | no | no | no | yes | eligible |
| `crm.draft_note` | 2 | propose | — | no | a CRM note draft (crm notes) | no | no | no | no | no | eligible |
| `edgar.launch_pipeline_job` | 2 | write | author_workflows | yes | edgar_pipeline_runs; launches a Render job that fetches public SEC filings | no | no | no | no | yes | eligible |
| `entities.count` | 3 | read | — | no | — | no | no | no | no | no | eligible |
| `entity.link_ownership` | 1 | write | manage_deals | no | entity_relationships (ownership percentages) | no | yes | no | no | no | never |
| `entity_graph.show_hierarchy` | 3 | read | — | no | — | no | no | no | no | no | eligible |
| `investments.count` | 3 | read | — | no | — | no | no | no | no | no | eligible |
| `litellm.reload_model_cost_map` | 2 | write | author_workflows | yes | the LiteLLM proxy's in-memory cost map (no database row) | no | no | no | no | yes | eligible |
| `marketplace.show_new_deals` | 3 | read | — | no | — | no | no | no | no | no | eligible |
| `portfolio.find_my_investment` | 3 | read | — | no | — | no | no | no | no | no | eligible |
| `portfolio.show_allocation` | 3 | read | — | no | — | no | no | no | no | no | eligible |
| `propose` | 2 | propose | — | no | agent_proposals (a draft for human review — nothing executes) | no | no | no | no | no | eligible |
| `security_data.nightly` | 2 | write | run_platform_jobs | yes | security data refresh tables | no | no | no | no | yes | eligible |
| `security_data.prices` | 2 | write | run_platform_jobs | yes | end-of-day prices | no | no | no | no | yes | eligible |
| `security_master.attributes` | 2 | write | run_platform_jobs | yes | security master attributes | no | no | no | no | yes | eligible |
| `security_master.mutual_funds` | 2 | write | run_platform_jobs | yes | mutual-fund share classes | no | no | no | no | yes | eligible |
| `security_master.openfigi_new` | 2 | write | run_platform_jobs | yes | FIGI mappings | no | no | no | no | yes | eligible |
| `security_master.sec13f` | 2 | write | run_platform_jobs | yes | the 13(f) list + CUSIP maps | no | no | no | no | yes | eligible |
| `security_master.universe` | 2 | write | run_platform_jobs | yes | the listed universe | no | no | no | no | yes | eligible |
| `spv.list_open` | 3 | read | — | no | — | no | no | no | no | no | eligible |
| `spv.record_transaction` | 1 | write | manage_deals | no | spv_transactions (capital calls, distributions, fees) — money | yes | no | no | no | no | never |
| `spv.show_captable` | 3 | read | manage_deals | no | — | no | no | no | no | no | eligible |
| `spv.show_ledger` | 3 | read | manage_deals | no | — | no | no | no | no | no | eligible |
| `spv.subscribe` | 1 | write | indicate_interest | no | a co-investment subscription (a capital commitment) | yes | no | no | yes | no | never |
| `spv_carry.propose_from_realization` | 1 | propose | manage_billing | yes | a carry run proposal (spv carry runs, status proposed) | no | no | no | no | no | eligible |
| `tasks.my_todos` | 3 | read | — | no | — | no | no | no | no | no | eligible |

Classes live on the verb (`AssistantAction.autonomy_class`, filled at `register()` from
`services.workflow_autonomy.VERB_AUTONOMY`). An unlisted WRITE verb is `never` (fail closed); a read is `eligible`
(nothing to gate).

**[FIND] Ten write verbs already ran unattended before S29B.** Every `workflow_invocable` Tier-2 verb (the eight
security-master / prices platform jobs, `ai.drift_monitor`, `edgar.launch_pipeline_job`,
`litellm.reload_model_cost_map`) executed the moment a scheduled run reached it. The prompt's "no write verb executes
unattended" holds only for client orgs. All live triggers with write steps are system workflows in the Hollisworks
platform org, owned by a Super Admin, all INACTIVE today (bugfix4 switch). S29B keeps them as they were: a verb marked
`platform_job`, in the platform org, with a Super Admin owner, runs under its own switch (rule 5 of
`decide_step`). Anywhere else it is an ordinary `eligible` verb under every S29B gate, and the kill switches stop it
too.

## 1b The engine path for a non-Tier-1 write step

- Before: `workflow_engine._drive` suspended a step only when it was `workflow_invocable` and its effective tier was 1.
  Every other invocable step called `_execute_service_task` at once, as the run's `started_by`, with the
  permission re-check (`_assert_action_permission`). No switch, no shadow, no limit.
- Where autonomy plugs in: one call in `_drive`, after the Tier-1 check and before execution —
  `_run_service_step(step_key, action)` → `workflow_autonomy.decide_step(...)`. A person-started run, a read, or a
  non-invocable step returns `attended` and runs exactly as before. Suspension reuses `_suspend_step` (the same
  `agent_proposals` row, the same S30 checker rule through `resolve_tier_approval`), with the reason in the proposal
  payload and the step's `error_detail`. A hold reuses `start_workflow_run`'s hold-and-alert path.

## 1c The trigger model

- `workflow_triggers.trigger_type` ∈ {`scheduled`, `event`} (CHECK). Scheduled: `workflow_scheduler._fire` starts the
  run with `started_by = created_by` and context `{trigger_id, trigger_type: 'scheduled', scheduled_occurrence}`.
  Event: `domain_events.publish_event` (13 emitters: human tasks, held-away, authority, models, blocks, execution,
  …) and the older `chancery_workflow_bridge` (`document_confirmed`), context carrying `trigger_id`.
  S29B adds `trigger_type: 'event'` to publish_event's context.
- The owner: S30 made `workflow_triggers.created_by` required to configure a trigger, and it IS the maker of every run
  the trigger fires (`workflow_engine.run_trigger_owner`). An event run's `started_by` is the event's own creator when
  known, else the owner. S29B executes unattended writes as the OWNER's delegate, whoever started the run.
- The router accepted only `document_confirmed` for event triggers. S29B opens the closed list
  `workflow_autonomy.TRIGGER_EVENT_TYPES`: `document_confirmed`, `human_task.opened`, `human_task.completed` (a task
  completed), `held_away.import_loaded`, `held_away.import_approved` (a held-away import approved).
- Live (read-only): 9 triggers in real orgs, all in Hollisworks. The 8 scheduled platform-job / EDGAR triggers are inactive.
  One active event trigger (`WFMGR4 OtherOrg`, `deal.created`) is a leaked fixture with no Service Task.

## 1d Switches, limits and hold-and-alert to reuse

- Hold and alert: `workflow_engine._hold_run` → `workflow_todos.create_held_run_alerts` (starter + org admins,
  member_todos). Reused, plus `workflow_autonomy.hold_alerts` (owner + org admins, source
  `workflow_autonomy_hold`), through the same `_upsert_todo`.
- Platform job switch (bugfix4): the job's trigger `is_active`, flipped by a Super Admin, audited. Reused unchanged
  for platform jobs.
- S28 drift alerts: `create_ai_drift_alert_todos` (member_todos). The digest uses the same channel.
- Org settings: `org_settings` is a flat key/value row updated IN PLACE, with no history and no maker / checker
  pair. It cannot carry a maker-checker switch under Rule 3, so the switches live in a new versioned table
  (`workflow_autonomy_settings`; see docs/WORKFLOW_AUTONOMY.md). Maker-checker is services.sod with two new item types,
  not a second rule.
- Idempotency precedent: the scheduler's single-statement claim (`UPDATE … WHERE last_fired_at < $occ`) — the same
  shape as the run-step claim (`UPDATE … WHERE idempotency_key IS NULL`, plus a unique index).
- Event de-duplication precedent: `domain_events_source_dedupe_uq` + the application-level DELIVERED guard. S29B adds
  a claim row with a unique index per (event, workflow version).
