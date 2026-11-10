# CASH06 — cash rules, stage A: discovery, decisions, reuse inventory (2026-11-09)

Repo at `1f459a9` (merge cash07) and the live database, read 2026-11-09. Standing rule (Joe, 2026-11-08): use the app's
existing constructs — the workflow engine, triggers, templates, S29b autonomy, S30 SoD, cash07 payments; a new one only
where none can carry it, named and justified here. Money movement is excluded from autonomy until counsel signs off:
nothing here approves, sends or moves money. A rule's firing DRAFTS payment instructions and SUBMITS each for a person.

## 1. Source documents and findings

- `CASH_TREASURY_DESIGN_v1.md` §4 (rule types, frequencies, stages A / B / C) and §7 (`cash_rules` listed as a table)
  are in `docs/cash/`; `CASH07_NOTES.md` and `CASH_PAYMENTS.md` (cash07) bind.
- **[FIND]** `CASH_TRACK_PLAN_v1.2.md` is still not in the repo or in Downloads (cash00 and cash07 recorded the same).
  §1 (cash06) and §2.4 ("a rule's maker is the rule / trigger owner (S30 Task 8), so its checker must be someone else")
  are taken as the sprint prompt states them. Nothing was invented from the plan.
- **[FIND]** `workflow_triggers` has NO configuration column. Its 15 columns are the schedule (`schedule_cron`,
  `timezone`, `start_date`, `end_date`, `max_occurrences`), the event (`trigger_type`, `event_type`), the owner
  (`created_by`), `is_active` and the scheduler's bookkeeping (`occurrence_count`, `last_fired_at`, updated IN PLACE
  by the claim). A rule definition cannot live there (§4).
- **[FIND]** No "incoming credit" domain event existed. The 13 publishers (`payment.*`, `held_away.*`, `human_task.*`,
  …) never publish one, and `workflow_autonomy.TRIGGER_EVENT_TYPES` had 5 types, none about cash. The transaction feed
  has no deposit / transfer type (cash07 §1). An INCOMING CREDIT is therefore a transaction booked on a CASH asset
  whose `cash_activity.TYPE_MAP` sign is +1 (money into the holding — the one sign table cash07 reconciles on), amount
  > 0. A minimal emitter was added (§3).
- **[FIND]** The Altruist sync (`sync_resolved_accounts`) runs on a caller's connection INSIDE the RLS pool's
  transaction (`_RLSAcquireContext` commits at block exit). An event published from inside the sync would start runs
  that re-read a transaction not yet committed, and the delivery would still count as DELIVERED, so the credit would be
  lost for good. The emitter therefore runs at the two call sites (`routers/altruist_connection` sync + auto-sync,
  `routers/altruist_webhook`) AFTER their connection commits. The sync only collects the new transaction ids
  (`SyncResult.new_transaction_ids`).
- **[FIND]** Every registered action is offered to the assistant. `ActionRegistry.list_for_user` filters by permission
  only, and `/assistant/confirm` calls any registered WRITE handler with caller-supplied kwargs, `run_context` and
  `workflow_run_id` included. A rule's firing verb must not be reachable that way (the custody cliff), so two defences
  were added:
  `REGISTRY.register(..., workflow_only=True)` keeps it out of `list_for_user` and makes `/assistant/confirm` refuse it
  (403), and `cash_rules.fire` refuses any call that is not the engine running the rule's OWN trigger. It reads the run
  row as STORED (status running, this org, `context.trigger_id` = the rule's trigger) and requires the step's actor to
  be the rule owner.
- **[FIND]** The engine's service context is MODULE-GLOBAL (`workflow_engine._SERVICE_CONTEXT` / `_SERVICE_STEP_MAP`).
  A rule's draft opens its approval task through `human_tasks.open_task` → `start_workflow_run`, a NESTED run whose
  `finally` resets both globals. The outer step has already read what it needs (cash04's waterfall opens review tasks
  the same way, which is the precedent), but `fire` restores both globals after every nested call, as a defensive
  measure.
- **[FIND]** No per-ACCOUNT cash balance read existed. `portfolio_cash.get_cash_balance` reads per OWNER entity, and
  one owner holds several accounts. `cash_rules.account_balances` reads per account with the SAME predicates (live,
  not superseded by source precedence, `asset_type = 'cash'`, the latest as-of date ≤ the firing day).
- **[FIND]** Two S29b behaviours an operator must know: they are unchanged, and documented in CASH_RULES.md.
  1. A trigger-started write runs unattended only when the org switch `workflows.autonomy` is on AND that workflow
     VERSION is opted in (after its shadow runs, decided by a different person). Until then every firing SUSPENDS for
     a person. The scheduler counts a non-terminal run as "in progress" (`_workflow_in_progress`), so a suspended run
     blocks that rule's later firings.
  2. A kill switch leaves the runs it suspended in place. After the switch is cleared, those runs must be cancelled
     (Admin → Workflows → runs) before the rules fire again.

- **[FIND]** A rule's own trigger is an ordinary `workflow_triggers` row, so the generic trigger screen
  (`routers/workflows.py`, `configure_workflow_triggers`) can switch it on or move its schedule. That is harmless by
  construction: `cash_rules.fire` reads the rule's OWN status on every firing (a paused or retired rule drafts
  nothing), claims its own period, and the amounts come from the approved version, never from the trigger. Hiding
  `system:cash_rule:` definitions from that screen is a follow-up (OUTSTANDING_TODO_LIST).
- **[FIND]** verify_s29b's two "the 1a table lists every verb" checks were already failing before this sprint.
  `docs/S29B_DISCOVERY.md` 1a never gained cash04's `cash.maturity_waterfall` / `cash.proposal_submit` /
  `cash.proposal_route` or cash07's four `cash.payment_*` verbs. Rows for all of them (and for `cash.rule_fire`) were
  added, so the regression can be 0 FAIL.

## 2. How a trigger + workflow definition is stored and versioned

- `workflow_definitions` (org, name) → `workflow_versions` (`version_number`, `bpmn_xml`, `is_current` — the ONLY
  definition → version resolver, domain_events) → `workflow_steps` (per version: `step_key`, `step_type`
  service / user, `autonomy_tier`, `action_registry_key`).
- `workflow_triggers` points at the DEFINITION (not a version); a run takes the current version.
- `workflow_runs` (context jsonb, `started_by`, `autonomy_mode`) + `workflow_run_steps` (status, result, the S29b claim
  `idempotency_key` / `delegate_for`).
- System definitions are seeded through these tables directly by `security_master_jobs.ensure_platform_jobs` and
  `human_tasks.ensure_definition` (an advisory lock, a single-step BPMN, then `workflow_steps`). wftemplates1's
  templates (`workflow_templates`, maker-checker publish) are a CATALOG a person instantiates into a definition; they
  carry BPMN + typed parameters, not per-instance data. A rule is not a template a person instantiates: it is
  instance data (accounts, %, caps), so a template adds nothing here and is not used.
- **Decision:** each rule OWNS one definition `system:cash_rule:<rule_id>`, version 1, with one Service Task
  (`cash_rule_fire`, action `cash.rule_fire`, diagram tier 3), and one trigger owned by the rule OWNER (`created_by` —
  the S30 trigger owner, the maker of every run). The trigger stays INACTIVE until a version is approved. A rule's
  VERSIONS are rows in `cash_rules`; the BPMN never changes, so the workflow version (and its S29b opt-in) survives a
  rule edit. An approved edit takes effect at the next firing; its own maker-checker is the review.
  One definition per rule rather than one per org: the scheduler's overlap guard, S29b's opt-in and its event rate
  limit are all per definition / version, so a shared definition would let one held run block every rule in the org.

## 3. Schedule vs event triggers

- **Scheduled** (`trigger_type 'scheduled'`, cron in `America/New_York`, 10:00): daily `0 10 * * *`, business days
  `0 10 * * 1-5`, weekly `0 10 * * <1–5>`, monthly `0 10 <1–28> * *`. The firing day is the occurrence's Eastern
  date. The cron cannot express holidays, so the EVALUATOR applies the calendar (`us_business_days`): a daily /
  business-day rule SKIPS a non-business day; a weekly / monthly rule drafts for the next business day and says so in
  the instruction's purpose. That is never silent: the rule's definition states it.
- **Event** (`trigger_type 'event'`, `event_type 'cash.incoming_credit'`, added to `TRIGGER_EVENT_TYPES`):
  `cash_rules.publish_incoming_credits(org, transaction_ids)` publishes ONE event per new incoming credit to an account
  that is the source of an active on-incoming rule (source_type `portfolio_transaction`, source_id = the transaction:
  the deployed `domain_events_source_dedupe_uq` makes it once per transaction, forever; S29b adds one run per event per
  version and the hourly rate limit). Every on-incoming trigger in the org hears the event; a rule whose source is
  another account returns without a firing record.

## 4. Where the rule definition lives — `public.cash_rules` (+ `public.cash_rule_firings`)

Decided: a table. The trigger cannot carry the definition (§1: no config column; updated in place by the scheduler).
Neither can the workflow version: its BPMN is shared by every version of the rule, and `bpmn:extensionElements` is
governance metadata, not money terms. A rule needs:
- versions, where an edit is a new DRAFT version and the active one keeps running (`version_no`, the partial unique
  indexes `cash_rules_running_uq` / `cash_rules_open_uq`);
- maker-checker in the database (`cash_rules_owner_maker_chk`, `cash_rules_maker_checker_chk`,
  `cash_rules_resume_checker_chk`);
- same owner enforced in the database (`cash_rules_same_owner_trg`, the cash07 rule extended to every destination and
  the supporting account);
- frozen terms once submitted and a status graph (`cash_rules_guard_trg`);
- RLS on every operation;
- the system-axis archive (`services.bitemporal.archive_live_row`), because firings, the approval task, sod_decisions
  and every drafted instruction (`purpose_ref_id`) point at it.
The design doc's own §7 lists `cash_rules`.

`public.cash_rule_firings` is the IDEMPOTENCY CLAIM per (rule, period | incoming transaction). It is committed BEFORE
any draft and finished by ONE update to its outcome (`drafted / held / partly_held / skipped`); a claim left `firing`
(the process died) is never retried. It is also the run history (firing → engine run → instruction ids, or the holds
with their reasons). No existing key carries this:
- the scheduler's claim is per trigger OCCURRENCE (a manual start of the same period is a new run);
- `workflow_run_steps.idempotency_key` is per run STEP;
- an event delivery is per event per workflow VERSION.
Teardown GUC `cash_rules.teardown`.

## 5. S29b caps and limits — per verb and per trigger

- Per VERB: `VERB_AUTONOMY["cash.rule_fire"]` = `eligible_with_cap`, `draft_only`, `submits_for_approval`, and
  `quantity` = 5 (the most instructions one firing can draft — its destinations) against the org's `cap_per_run`.
  Approve / send / reconcile stay `never` (cash07's four payment verbs, unregistered). Duty `propose`
  (`sod.VERB_DUTIES`). Action tier 2: Tier 1 would suspend every firing for a checker BEFORE drafting, a second
  approval of the same money. The instruction's own floor approval is the gate.
- Per ORG (S29b, unchanged): `runs_per_day`, `writes_per_run`, `writes_per_day`, `cap_per_run`, `event_runs_per_hour`,
  the org switch, both kill switches, the per-version opt-in after shadow runs.
- Per RULE (this sprint, inside the run): `cap_per_firing` ($) and `max_per_day` (instructions), each ≤ the org
  ceiling — `cash.rules.max_amount_per_firing` (default 250,000.00) and `cash.rules.max_instructions_per_day`
  (default 5). They are checked when the rule is saved / submitted / approved and applied again at every firing
  (min(rule, ceiling now)). The excess is HELD with an alert through `workflow_autonomy.hold_alerts` (the trigger
  owner + every org admin).

## 6. The cash07 service verb

Added: `services.payments.draft_and_submit(pool, *, org_id, actor_id, household_id, data)`. It runs `_validate` as the
OWNER (same owner, the declared rail, a business-day value date, both authority checks), opens the approval task FIRST
(the engine, its own connections) for an id chosen here, then INSERTs the draft and moves it to `pending_approval` in
ONE database transaction: the DB writes the `create` and `submit` events, and the instruction is never seen as a lone
draft. On failure the task is withdrawn. `maker_ids = [owner]`, so the cash07 floor refuses the owner as checker.
`purpose_ref_type 'cash_rule'` (CHECK widened in docs/cash06_part1.sql); a PERSON's draft / edit may not set it
(`payments.RULE_ONLY_REF`).

## 7. Governance — S30 item type `cash_rule` (a FLOOR)

`cash_rule`, floor class `transfer`: a rule that drafts money movement IS money-movement configuration. It uses
cash07's duties `initiate_payment` (make) / `approve_payment` (check). Platform `floor:` + `policy:` rules (never;
enforce_floor true). Task kind `cash_rule_approval` (maker-checker) for ACTIVATE and RESUME. Makers = every drafter /
editor / submitter of the version + the OWNER (always) + a resume's requester. Sites: `cash_rules.decide` →
`human_tasks.complete_task` → `cash_rules._precheck` (every maker, the definition re-validated) → `cash_rules._apply`
(inner gate). Pause and retire are tightening, so they apply at once (as S29b's kill switch does).

## 8. Decisions

- D1 Same owner = the cash07 rule (D1 there) for every destination and the supporting account, in the service and in
  the database.
- D2 Rounding: % legs round DOWN to the cent; a target draw rounds UP (it reaches the target), a sweep rounds DOWN (it
  never sweeps below the target); under $0.01 drafts nothing. Shortfall on a paying account holds ALL of that
  account's legs (no partial overdraft). The cap can split a leg (the excess is held); the per-day count holds whole
  legs.
- D3 Stale: a balance more than 1 business day before the firing day (or none) holds the firing.
- D4 Reject of an activation returns the version to DRAFT (as cash07 D2); reject of a resume leaves it PAUSED.
- D5 The owner is the drafter of version 1 and never changes; an editor of a later version joins its makers and the
  owner stays a maker. A different person approves the rule. Each instruction the rule drafts has the OWNER as its only
  maker, so any eligible `approve_payment` holder other than the owner may approve it (the rule's own approver
  included).
- D6 Members see their family's ACTIVE rules only, read-only, with `cash.rules` (new disclosure key, DRAFT default
  "Rules prepare payment instructions for approval; no money moves without a person approving it.").
- D7 No rule is created for the demo family by script; no real or demo row is written by the sprint.

## 9. Reuse inventory

| Need | Construct |
|---|---|
| run history, holds, alerts | the workflow engine (`start_workflow_run`, `_hold_run`), `workflow_autonomy.hold_alerts` |
| schedules | `workflow_scheduler.run_scheduler_tick` (claim per occurrence, overlap), `workflow_schedule` (cron, next run) |
| events | `domain_events.publish_event` (dedupe index, per-version claim, rate limit) |
| autonomy | `workflow_autonomy.decide_step` (classes, switches, kill switches, shadow, limits, delegate claim) |
| maker-checker | `services.sod` (`gate`, the floor), the S30 catalog (new item type on cash07's duties) |
| approval + inbox | `services.human_tasks` (`open_task` / `complete_task` / `cancel_task`, precheck) |
| drafting money | `services.payments.draft_and_submit` (NEW, cash07's own checks: `_validate`, `same_owner_refusal`, `rail_refusal`, `authority_refusals`) |
| business days | `services.us_business_days` (via `payments.business_days_between`) |
| feed direction | `cash_activity.TYPE_MAP` |
| balances | the `portfolio_cash.get_cash_balance` predicates, per account (`cash_rules.account_balances`) |
| archive | `services.bitemporal.archive_live_row` |
| visibility | `routers.payments._open` (the member path / VISIBILITY1), imported, never copied |
| disclosures | `services.disclosures` (+ `cash.rules`; `cash.payments` shown on the editor) |
| screens | the existing Treasury tab (`TreasuryDaily` → `TreasuryRules`, beside `TreasuryPayments`) |
| system definitions | the `security_master_jobs.ensure_platform_jobs` pattern (advisory lock, v1, steps, trigger) |
