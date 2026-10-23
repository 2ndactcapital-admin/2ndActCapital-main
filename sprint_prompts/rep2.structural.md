REP2 — Replication Studio, sprint 2 of 15: custodian capabilities and trade
adapters on the existing custody registry, governed human tasks on the
workflow engine, and execution instructions (the path for every trade that
cannot be sent automatically). No models, proposals or tax logic yet. 9 tasks
+ verification.

Context: spec "Replication Studio — ETF & Fund-to-SMA Spec" (Joe, 2026-10-22),
section "Custodians, held-away accounts and manual execution". Decisions:
  - Multi-custodian from the start: one adapter per custodian, each declaring
    its capabilities; a missing capability downgrades the plan (core only, or
    a manual instruction) — it never blocks the whole replica.
  - Order of adapters: Altruist first, a FIX route later (rep9), and a
    "manual" adapter from day one that turns every trade into an execution
    instruction.
  - Every human action is a task in the WORKFLOW ENGINE and is logged there
    (rep1 found the engine opens User Tasks only from a published BPMN
    version and fell back to member_todos + domain_events; this sprint builds
    the proper, reusable path).
  - Custody cliff: nothing here places a trade or moves money. Altruist has
    no write access today (no live credentials; integration sprints 1-6 ran
    against mocks), so EVERY trade in this sprint ends as an execution
    instruction carried out by a person.
Later: rep3 model management (and moves rep1's terms approvals onto this
sprint's human-task path), rep4 model builder, rep5 accounts/proposals (calls
this sprint's instruction service), rep6 tax overlay, rep7 held-away accounts.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main @ 14200f8, merge rep1):
- Custody adapter registry (sprint fee31): apps/api/services/custody/
  (base.py: CustodyAdapter, AccountNumber [repr/str/format/to_json all return
  the MASK; .reveal() only in hasher and masker], AccountRecord with
  is_held_away, BalanceRecord, FlowRecord; registry.py: CustodyProfile
  (custodian_code, adapter_key, label, source_system, column_map, is_default),
  register_adapter, build_adapter, load_profiles, resolve_profile;
  csv_adapter.py; importer.py). Adapters register as an import side effect in
  custody/__init__.py. EXTEND this registry; never build a second one, and
  never change how existing CSV imports behave.
- public.accounts: id, org_id, account_number_masked, account_number_hash,
  custodian_code, custodian_account_id, registration_type, tax_status,
  primary_entity_id, household_id, advisor_of_record_id, service_model,
  is_billable, is_discretionary, is_held_away, opened_on, closed_on,
  base_currency, bi-temporal columns. portfolio.tax_lots exists.
- Altruist: services/altruist_oauth.py, altruist_identity.py,
  altruist_positions_sync.py, altruist_webhook.py, altruist_one.py,
  portfolio_altruist.py; tables altruist_connections, altruist_oauth_states,
  altruist_webhook_events, altruist_one_evaluations. All built against mocks;
  no live Altruist call has ever been made; no trade/write endpoint exists.
- Workflow engine: services/workflow_engine.py (SpiffWorkflow) —
  start_workflow_run, complete_user_task(pool, workflow_run_step_id,
  completed_by, result), resolve_tier_approval, MakerCheckerError,
  compute_effective_tier; tables workflow_definitions, workflow_versions,
  workflow_steps, workflow_runs, workflow_run_steps, workflow_triggers;
  member_todos; domain_events (org_id, event_type, source_type, source_id,
  payload, occurred_at, created_by). Script tasks are refused; user tasks
  suspend runs.
- rep1 (merged): replica_* tables, permissions view/manage/approve_replication,
  replica_terms_actions + domain_events 'replication.<action>' + member_todos
  for approvals; reportlab + pypdf are pinned (dealroom2) for PDFs.
- Menu: apps/web/lib/menuVisibility.mjs — Portfolio group (landing
  /portfolio, has the Replication tab) and the org_admin "Billing & Custody"
  group (landing /admin/billing).
- authz1: every route has an explicit gate and an AUTHZ1_ROUTES.json entry
  (authz1_routes.py --check CURRENT); never change an existing gate; Super
  Admin first; nothing grants on an absent role. Permission envelope pattern
  with no client fallback. org_id only from the session; models with
  extra='forbid'.
- Regression policy: one directly affected prior verify. This sprint touches
  the workflow engine's shared machinery → verify_workflowmgr5.py ONCE.
- Verify scripts must print 'TOTAL: N PASS, M FAIL'. Older scripts print
  'RESULT: …' — the regression parser must accept both.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never poll in a loop, never start a
script in the background; run every command synchronously in the foreground
and read its output directly. Write the verify script but DO NOT RUN IT, and
do not run npm run build. No network calls (no Altruist, no SEC). Never print
a secret or a full account number (AccountNumber only). Column names from the
live DB. Bi-temporal rule 3 for every new table; RLS policies for EVERY
operation each new table will take; platform rows owner_scope 'platform' with
org_id NULL. Labels and vocabularies from the API envelope. Light/cream UI
with the existing tokens. Commit ALL sprint files (new and modified, excluding
sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The custody registry: how a profile resolves for an org and a
      custodian_code, which adapters exist, and where a capabilities record
      and a trade-submission interface can be added without changing the CSV
      import path. List the custodian codes in use in public.accounts today
      (counts only).
  1b. The workflow engine: can a SYSTEM workflow definition (one user task,
      maker-checker) be seeded per org and started programmatically for an
      arbitrary subject, with the eligible approvers given by a permission?
      How complete_user_task enforces maker ≠ checker (MakerCheckerError),
      where the run/step records the subject, and how member_todos /
      notifications attach to an open user task today. If the engine needs a
      small extension (e.g. a permission-based assignee, a subject reference
      on the run), state the exact change and its effect on existing runs.
  1c. portfolio.tax_lots columns, and how a lot ties to an account and a
      security (needed for lot-level instructions).
  1d. Whether the Altruist services expose anything about the account's
      capabilities (options level, margin) in their mocked payloads; record
      what is evidence and what is unknown. Nothing is assumed true.

=== TASK 2: GOVERNED HUMAN TASKS (services/human_tasks.py) ===
One reusable path for "a person must act / approve", built on the workflow
engine (per 1b):
  - open_task(org, kind, subject_type, subject_id, maker_id,
    eligible_permission, title, payload) → starts a run of a seeded system
    definition (versioned, per org, created idempotently), records the
    subject, notifies eligible holders (member_todos as today), writes
    domain_events + audit_log.
  - complete_task(task, actor, outcome approve|reject|done, note) → completes
    the user task through complete_user_task; maker-checker refused with a
    message naming the rule; closes the todos; logs events + audit.
  - list_tasks(org, filters) for the screens. A task's history is readable
    from the engine's run tables, not only from a side table.
  - Tier: these tasks are approvals of HUMAN-initiated work; no agent can
    open or complete them (assert the actor is a user).
Do not change existing workflows' behaviour; any engine change is additive.

=== TASK 3: CUSTODIAN CAPABILITIES ===
Platform table custodian_capabilities (versioned, platform-scoped): per
custodian_code, each capability with value (yes / no / unknown), evidence
(document, URL or "assumed"), as-of date, recorded_by. Capabilities:
fractional_shares, equity_trading_api, model_api, trade_list_upload,
options_level (0-4 or unknown), flex_options, index_options, margin,
short_selling, in_kind_transfers, custodian_tax_loss_harvesting,
lot_level_cost_basis_feed, held_away_aggregation.
Seed: 'altruist' with everything 'unknown' except what 1d evidences (cite);
'manual' (all trading = instruction; trade APIs no). Super Admin edits
(every edit versioned, logged); orgs read. A helper
capability(custodian_code, key) → (value, evidence, as_of) for later sprints;
'unknown' is treated as NOT available by every caller.

=== TASK 4: TRADE ADAPTER INTERFACE ===
Alongside (not inside) the CSV ingestion interface: TradeAdapter with
submit(trade_list) / status(submission) / cancel(submission), registered per
custodian in the same registry. Implement:
  - ManualTradeAdapter: submit() turns each trade into a DRAFT execution
    instruction (Task 5) and returns their ids; nothing leaves the system.
  - AltruistTradeAdapter: declares equity_trading_api per the capabilities
    table; while that is not 'yes' it routes to the manual adapter and says
    so in the result. No HTTP code for trading is written in this sprint.
  - route_trade_list(account, trades): picks the adapter from the account's
    custodian_code + capabilities; held-away accounts always go manual.
    A trade list is a typed structure (security, side, quantity or notional,
    lot instructions, price guidance) with extra='forbid'.

=== TASK 5: EXECUTION INSTRUCTIONS ===
Tables (org-scoped; names indicative): execution_instructions,
execution_instruction_lots, execution_fills.
  - Instruction: account, security (global security), side, quantity or
    notional, price guidance (limit, or acceptable range against a reference
    price + its time), tax-lot instructions (specific lots from tax_lots,
    sell side), wash-sale notes (free text for now; rep6 fills them),
    executor type (custodian_desk, broker, member, other_adviser, trader) +
    name/contact, execute_by and if-not-filled action (cancel, carry to next
    day, ask), source (manual or a later proposal id), status.
  - Status flow: draft → approved (human task, maker ≠ checker) → sent
    (marked by a person, with channel) → partially_filled / filled (fills
    entered: quantity, price, time, lots, entered_by) → reconciled
    (matched to custodian data or confirmed manually with a note) → closed;
    cancelled / expired from any open state with a reason. Every transition
    is a human task or a logged human action; no transition is automatic
    except expiry at execute_by (logged).
  - Letter of instruction: for executor type member / other_adviser (held-
    away accounts), generate a PDF letter (reportlab) from the instruction —
    on demand, never stored as the source of truth. Account numbers appear
    MASKED in the letter unless the user explicitly chooses to include the
    full number, which is logged.
  - Pending effect: an account's open instructions are queryable as
    "pending differences" for later sprints.

=== TASK 6: API AND PERMISSIONS ===
New permissions: view_execution, manage_execution (create, edit draft, mark
sent, enter fills, reconcile, cancel), approve_execution. Grant to org_admin
by default AND extend rbac.ensure_org_admin_role so a NEW org's first
org_admin also gets them — and the three replication permissions rep1 added
(this closes the rep1 gap). Capabilities: read = any staff; write = Super
Admin only. Routes under /api/v1/execution/… and /api/v1/custodians/…, each
gated and in AUTHZ1_ROUTES; envelopes with permissions and vocabularies
(statuses, executor types, capability keys and labels from the server).

=== TASK 7: SCREENS ===
  - Portfolio group → new tab "Instructions" (gated view_execution): list
    with filters (status, account, executor, due); New instruction form
    (manage only) with security search, lot picker from the account's lots,
    price guidance and executor; detail page with the status timeline, fills
    entry, reconcile, letter-of-instruction download, and the human-task
    panel (Approve / Reject only for approve_execution and never for the
    maker).
  - A "My tasks" panel on the dashboard (or the existing to-do area) listing
    open human tasks from Task 2 for the signed-in user.
  - Billing & Custody group → tab "Custodian capabilities": matrix of
    custodians × capabilities with value, evidence and as-of; Super Admin
    sees Edit; everyone else read-only. "unknown" shown plainly.

=== TASK 8: REAL PROOF ===
  - Human tasks: open → notified holders only; maker's approve refused on
    the identical request a different approver succeeds with; reject path;
    events + audit + engine run/step records re-read; an agent actor is
    refused; an existing workflow's run behaves exactly as before.
  - Capabilities: 'unknown' is treated as unavailable; Super Admin edit
    creates a new version (old kept); org admin edit 403; read by staff.
  - Routing: an Altruist account with equity_trading_api unknown → manual
    instructions; a held-away account → manual regardless; a trade list with
    an extra field is refused (422).
  - Instructions: full happy path draft → approved → sent → partial → filled
    → reconciled → closed with every transition logged; illegal transitions
    refused (e.g. fill before approval, approve by maker, edit after
    approval); cancel and expiry with reasons; lot instructions only for
    lots of that account and security (negative case); quantities and prices
    are Decimal.
  - Letter: PDF generated on demand; masked account number by default (no
    full number anywhere in the PDF text); the explicit full-number option
    is logged; refused for callers who cannot view the instruction.
  - Org isolation both directions under app_service with rolbypassrls =
    false (instructions, tasks); platform capabilities readable by both.
  - Gates both ways on identical requests; envelope without can_write →
    node tests render no New / Approve / Fill / Edit controls.
  - New-org grant: creating an org's first org_admin grants the six
    permissions (re-read).
  - CSV custody import unchanged: one existing importer path runs identically
    (fixture file, same result as main).
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_workflowmgr5.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 1200 s timeout; accept 'TOTAL:' or 'RESULT:' summaries.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (adapters, capabilities,
human tasks, instructions), a short docs/HUMAN_TASKS.md (how any module opens
and completes a governed task), and add verify_rep2.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep2.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
calls (mock anything external). Wrap every section so an exception is a
[FAIL]; teardown at start and end in FK child-before-parent order from
pg_constraint, deleting ONLY rows this run created (by fixture ids — never
walk the whole FK graph from a broad parent), with audit_log,
assistant_activities, agent_proposals, domain_events, member_todos and
workflow run/step rows cleared by fixture id before fixture users; then
re-read every touched table for its exact pre-test count. Fixture users
created explicitly with the auth0_sub the mocked token carries,
is_test = true, using the users-RLS-compatible pattern from verify_rep1.py.
Read every UNIQUE (including partial unique indexes), CHECK and FK constraint
on the touched tables from the catalog before seeding and seed to satisfy
them. Fixture accounts use test custodian codes and masked numbers only.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Exclude this run's own fixtures from any "no leftovers" check.
  - Never spawn a background process. The regression subprocess gets
    VERIFY_NESTED=1, runs after teardown, 1200 s timeout; with
    VERIFY_NESTED=1 this script skips its regression section with one
    '[SKIP] nested run' line.
  - Never touch real accounts, tax lots, workflow definitions or runs:
    count + checksum of real rows before and after.

Assertions:
  [Y] Task 1 findings 1a–1d reported with files and the engine change (if
      any) and its effect on existing runs
  [Y] Human tasks: open/notify/complete; maker-checker on identical request;
      reject; engine records + events + audit; agent refused; existing
      workflows unchanged
  [Y] Capabilities: versioned, Super Admin only edits, 'unknown' =
      unavailable, evidence recorded
  [Y] Trade routing: Altruist (unknown API) and held-away → manual; extra
      fields refused
  [Y] Instructions: full lifecycle logged; illegal transitions refused; lot
      checks; Decimal throughout
  [Y] Letter of instruction: on demand, masked by default, full number only
      by explicit logged choice, gated
  [Y] Org isolation both directions (rolbypassrls = false)
  [Y] Gates both ways on identical requests; node tests show no write or
      approve controls without the flags
  [Y] New org's first org_admin gets the six permissions
  [Y] CSV custody import path unchanged
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no
      existing gate changed
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script,
      not during the sprint)
  [Y] Regression: verify_workflowmgr5.py ONCE, flat, after teardown,
      VERIFY_NESTED=1; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
