REP5 — Replication Studio, sprint 5 of 15: accounts join models (with
account-level settings and exclusions), the transition analysis for what an
account already holds (lot-level, gain budget), rebalance PROPOSALS from four
triggers, proposal approval on human tasks, and approved proposals turned
into trade lists / execution instructions through rep2. The tax engine
(harvesting, full household wash-sale control) is rep6 — this sprint applies
only lot selection, a gain budget, a short-term guard and a basic 61-day
wash-sale FLAG. 10 tasks + verification.

Context: spec "Replication Studio — ETF & Fund-to-SMA Spec" (Joe,
2026-10-22): "Workflow" steps 2 and 7, "Portfolio construction" (rebalance
triggers: drift, model change, deposit/withdrawal; every trigger produces a
proposal, never a trade), "Fees" (account override; no double charging while
a replicated fund is still held), "Custodians…" (manual execution
instructions). Decisions (Joe): personal settings live on the ACCOUNT, not
the model (exclusions, cash floor, tax settings, fee); a new account joins
an existing model rather than forking it; every human action is a human task;
custody cliff — nothing trades: an approved proposal becomes execution
instructions (Altruist's trading API is 'unknown' → manual path).

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main @ 0b2f7ff, merge rep4; Task 1
confirms exact signatures):
- Accounts: public.accounts (id, org_id, account_number_masked/hash,
  custodian_code, custodian_account_id, registration_type, tax_status,
  primary_entity_id, household_id, is_billable, is_discretionary,
  is_held_away, opened_on, closed_on, base_currency, bi-temporal). Real
  accounts today: 0 rows.
- Holdings: portfolio.positions (owner_entity_id, asset_id, as_of_date,
  quantity, cost_basis, market_value, account_id, source_system,
  is_reconciled, …); portfolio.assets (global_security_id, asset_type, …);
  portfolio.tax_lots (position_id, acquired_date, quantity, original_cost,
  adjusted_cost, term, realized_gain_loss, status 'open'…); a lot reaches its
  account via positions.account_id and its security via positions.asset_id →
  assets.global_security_id. portfolio.transactions (position_id,
  transaction_type_code, trade_date, settle_date, quantity, price, …).
  account_balances_daily (total_market_value, cash_value, …).
- Prices: portfolio.securities_global_prices has 0 rows (no licensed feed).
  An account's OWN position market_value / quantity on its as_of_date is the
  custodian's valuation and may be used as that security's price for that
  account (state the as-of); a security the account does not hold and that
  has no fresh price is bought by NOTIONAL (dollar amount), never a guessed
  quantity.
- rep2: services/execution.py — create_instructions(pool, *, org_id,
  actor_id, account, trade_list, routed_via, …), submit, decide, account_lots
  (conn, org_id, account_id, global_security_id), _validate_lots; trade list
  typed (security, side, quantity or notional, lot instructions, price
  guidance; extra='forbid'); route_trade_list (Altruist unknown → manual;
  held-away → manual); instruction status flow draft → pending_approval →
  approved → sent → partially_filled / filled → reconciled → closed.
  services/human_tasks.py (open_task / complete_task / cancel_task /
  list_tasks; kinds react through on_complete; system_open for review
  kinds). services/custodian_capabilities.py capability() — 'unknown' = not
  available (includes custodian_tax_loss_harvesting).
- rep3/rep4: services/models.py (live_approved, positions_of, look_through,
  'model.version_approved' domain event with the diff), alternates on
  model_version_positions; services/replica_models.py (fund-built models,
  specs, monitoring) — the manual-custodian variant name currently reads
  "Holdings of X — Manual — execution instruction": change the label to
  "Holdings of X — Manual" (existing rows: none real).
- Fee module: services/fee_schedules.py create_assignment(...),
  end_assignment, resolve_assignment_for_account, list_assignments;
  assignments by scope (ACCOUNT most specific); exclusions by SECURITY /
  ASSET_CLASS / POSITION_TAG (Task 1 finds the exclusion write path).
- authz1 rules; envelope pattern; org_id only from the session;
  extra='forbid'; Super Admin first.
- .gitignore ignores sprint_prompts/logs/*.json, *.err, decision_log.jsonl,
  *.done — but NOT *.log / *.out, so run_sprint's merge step committed those
  files to main in rep4. Fix: ignore sprint_prompts/logs/ entirely and
  `git rm --cached` the tracked log files (they stay on disk).
- Regression policy: this sprint adds a path into rep2's instruction
  creation → re-run verify_rep2.py ONCE. Scripts print 'TOTAL: N PASS,
  M FAIL'; accept 'RESULT:'.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never poll in a loop, never start a
script in the background; run every command synchronously in the foreground
and read its output directly. Write the verify script but DO NOT RUN IT, and
do not run npm run build. No network calls. Never print a secret or a full
account number. Column names from the live DB. Bi-temporal rule 3 for every
new table; RLS policies for EVERY operation each new table will take.
Decimal everywhere (weights, quantities, prices, gains). Never guess a price
or a quantity. Labels and vocabularies from the API envelope. Light/cream UI
with the existing tokens. Commit ALL sprint files (new and modified,
excluding sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Exact signatures this sprint calls in execution.py, human_tasks.py,
      models.py, replica_models.py, fee_schedules.py (create_assignment,
      exclusions), custodian_capabilities.py.
  1b. How positions, lots and transactions are loaded for one account and
      for a household (household_id; owners via account_owners), which
      position as_of_date is "current", and how a position's price is
      derived from market_value / quantity. Report any account/position rows
      that exist (counts only).
  1c. How the 'model.version_approved' domain event can be consumed (domain
      event deliveries / a subscriber registry / a direct call from rep3's
      approval) — choose the mechanism and say why.
  1d. Whether rep2's instruction creation can accept instructions that are
      ALREADY approved by an upstream maker-checker (the proposal's), without
      weakening the instruction's own rules; state the exact change.
  1e. The fee module's ACCOUNT-scope assignment and SECURITY exclusion write
      paths, their approval/versioning rules, and whether creating them from
      here needs its own approval.

=== TASK 2: SCHEMA ===
Design from Task 1 (org-scoped; names indicative):
  - account_model_links: account → model (follow live), status (pending |
    active | ended), joined_at, ended_at, settings: cash_floor (fraction),
    drift_threshold (override; default from the model spec or an org
    setting), rebalance_frequency (override), exclusions (list of global
    security ids, each with a reason), exclusion_policy (pro_rata | to_cash
    | use_alternates), gain_budget (amount per calendar year, nullable =
    unlimited), avoid_short_term (bool), short_term_wait_days, harvesting
    engine switch (hollisworks | custodian — custodian only when that
    custodian's custodian_tax_loss_harvesting is 'yes'; rep6 uses it),
    fee_schedule_override (nullable). One active link per account (partial
    unique). Joining and every settings change = a versioned row + human
    task approval.
  - rebalance_proposals: account, link, model version, trigger (join |
    model_change | drift | cash_flow | manual), status (draft |
    pending_approval | approved | rejected | expired | superseded |
    executed), valuation as-of, account value, before/after weights,
    drift before/after, estimated realized gain/loss (ST/LT), warnings
    (jsonb), expires_at, maker (user or system), checker, task id.
  - rebalance_proposal_trades: proposal, security, side, quantity or
    notional, price used + its source and as-of, lots (sell side: lot id,
    quantity, adjusted cost, term, estimated gain), reason (plain words),
    wash_sale_flag (jsonb), instruction_id (after execution).
  - account_transition_plans: account, link, legacy holdings
    (keep | sell_now | sell_later with the year), total embedded gain,
    years to complete at the gain budget.
Apply with scripts/_apply_rep5.py through Doppler, synchronously; VERIFY it
landed; save docs/rep5_part1.sql.

=== TASK 3: JOIN AN ACCOUNT ===
services/account_models.py:
  - Find the model first: offer existing approved models (the registry),
    never create a model here.
  - Join: validates the account (not closed; discretionary required — a
    non-discretionary account can be linked but every proposal is
    "advice only" and its trades go to the member as instructions);
    held-away accounts may join (always manual execution).
  - Exclusions: listed securities are removed from the target and their
    weight handled by exclusion_policy (pro_rata exact; to_cash; use the
    model's alternates when present, otherwise pro_rata, stating it).
  - Fees: propose (as part of the same approval) the ACCOUNT-scope
    assignment of the model's default fee schedule (or the override) and,
    while the replicated fund (or any security the model replaces) is still
    held, a SECURITY exclusion so it is not billed twice — through the fee
    module's own services and rules (1e). If the fee module requires its own
    approval, open it and link it; never write fee rows directly.
  - Joining opens a human task (approve_proposals); approval activates the
    link and generates the transition proposal (Task 5 trigger 'join').

=== TASK 4: TRANSITION ANALYSIS ===
For a joining (or re-targeted) account: classify every current holding:
keep (in target), trim/add, sell (not in target or excluded-by-sale); for
sells pick lots by lowest tax per dollar raised (losses first, then
highest adjusted cost; long-term preferred over short-term at equal cost);
apply avoid_short_term with short_term_wait_days (lots turning long-term
within the window are deferred, with the date); cap realized net gains at
the gain budget for the calendar year (net of losses harvested in the same
proposal and of gains already realized this year from transactions); what
does not fit is "sell later" with the year, and the plan says how many years
the transition takes. Prices: the account's own position valuation (as-of
shown); no price → that holding is "cannot value — needs a price" and is
never sold by guess.

=== TASK 5: PROPOSALS ===
Generator (one function, four triggers):
  - join (after approval), model_change ('model.version_approved' for every
    account following the model live — consumer per 1c), drift (a daily
    scan on the scheduler tick after the holdings refresh: an account whose
    max |actual − target| exceeds its drift threshold, valued from its own
    positions), cash_flow (account_balances_daily cash above the cash floor
    by more than a threshold, or a withdrawal request entered by staff), and
    manual ("Rebalance now").
  - Output: trades to bring the account to target within the gain budget and
    cash floor; buys of unpriced securities as NOTIONAL; whole shares unless
    the custodian's fractional_shares is 'yes'; minimum trade size (org
    setting) to avoid dust; every trade with a plain-language reason; before
    / after weights and drift; estimated ST/LT gain/loss; warnings
    (unpriced, stale valuation > N days, capability unknown, held-away).
  - Basic wash-sale FLAG (rep6 replaces with full control): flag a buy of a
    security the household sold at a loss in the prior 30 days, and a loss
    sale of a security the household bought in the prior 30 days, from
    portfolio.transactions across the household's accounts (IRAs included).
    Flags show on the trade; they do not block (rep6 decides).
  - One open proposal per account (a newer one supersedes the older,
    logged); proposals expire after N days (org setting) or when the
    valuation date changes.
  - Submit → human task (approve_proposals; maker = the generating user, or
    system with system_open for system triggers; the checker is a human
    who is not the maker). Reject needs a note.

=== TASK 6: EXECUTE AN APPROVED PROPOSAL ===
On approval: build the trade list and route it through rep2
(route_trade_list): Altruist 'unknown' and held-away → execution
instructions. Instructions created from an approved proposal start in
'approved' (per 1d), carry the proposal id, its maker and checker, and the
lot instructions; mark sent / fills / reconcile follow rep2's flow unchanged.
The proposal moves to executed when every instruction is closed or
cancelled (with the reason). Nothing is sent anywhere automatically.

=== TASK 7: API, PERMISSIONS, NAMES, GITIGNORE ===
New permissions view_proposals, manage_proposals (join, settings, generate,
submit), approve_proposals — org_admin by default and in
rbac.ensure_org_admin_role. Routes under /api/v1/accounts/{id}/model…,
/api/v1/proposals/…, each gated and in AUTHZ1_ROUTES; envelopes with
permissions and vocabularies (triggers, statuses, exclusion policies,
warning codes, harvesting engine values). Rename the manual-custodian model
variant label to "Holdings of X — Manual". .gitignore: ignore
sprint_prompts/logs/ entirely; `git rm --cached -r sprint_prompts/logs` for
already-tracked log files (files stay on disk).

=== TASK 8: SCREENS ===
  - Portfolio group → new tab "Accounts" (gated view_proposals): accounts
    with their model, drift, last proposal, open instructions; filters.
  - Account page: Model panel (join / change model with the registry offer
    first; settings: exclusions with reasons, exclusion policy, cash floor,
    drift threshold, gain budget, short-term guard, harvesting engine switch
    — custodian option shown only when evidenced), Transition plan (keep /
    sell now / sell later by year), Proposals (list + detail: trades with
    reasons, lots, prices with source/as-of, before/after weights, gain/loss,
    warnings, wash-sale flags; Submit / Approve / Reject with maker-checker),
    links to the instructions an approved proposal created.

=== TASK 9: REAL PROOF ===
  - Join: opens a task; maker's approve refused on the identical request a
    checker succeeds with; link active; fee assignment + security exclusion
    created through the fee module's own path (re-read); a closed account
    refused; non-discretionary → advice-only proposals; one active link per
    account.
  - Exclusions: pro_rata exact; to_cash exact; use_alternates uses the
    model's alternate (and falls back to pro_rata with a stated reason).
  - Transition: lot choice order exact (loss lots first, then highest cost,
    LT before ST at equal cost); a lot turning long-term within the wait
    window deferred with its date; gain budget respected exactly, net of
    same-proposal losses and of gains already realized this year; remainder
    planned by year; an unpriced holding is never sold.
  - Proposals: each trigger produces a proposal (join, model_change via the
    event, drift over threshold — none under, cash_flow, manual); unpriced
    buys are notional; whole vs fractional by capability; dust trades
    suppressed; one open proposal per account (supersede logged); expiry.
  - Wash-sale flag: both directions across the household including an IRA;
    no flag outside 30 days; flags never block.
  - Execution: approval routes to manual instructions (Altruist unknown,
    held-away); instructions start approved with the proposal's maker and
    checker and lots; rep2's later transitions unchanged; proposal executed
    when all instructions are closed/cancelled.
  - Name: the manual variant reads "Holdings of X — Manual".
  - Org isolation both directions (rolbypassrls = false); gates both ways on
    identical requests; node tests show no Join / Generate / Approve controls
    without the flags.
  - New org's first org_admin gets the three proposal permissions.
  - .gitignore covers sprint_prompts/logs/; no log file is tracked
    (git ls-files sprint_prompts/logs is empty).
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_rep2.py ONCE, flat, after teardown, VERIFY_NESTED=1,
    1200 s timeout.

=== TASK 10: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (accounts, settings,
transition, proposals, execution path), and add verify_rep5.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep5.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
calls. Wrap every section so an exception is a [FAIL]; teardown at start and
end in FK child-before-parent order from pg_constraint, deleting ONLY rows
this run created, by fixture id (never walk the FK graph from a broad
parent); clear audit_log, domain_events, member_todos, workflow rows, fee
assignments/exclusions created by this run before fixture users; reconnect
once and re-count if a connection drops; then re-read every touched table for
its exact pre-test count. Fixture users created explicitly with the auth0_sub
the mocked token carries, is_test = true, using the users-RLS pattern from
verify_rep1–4. Read every UNIQUE (including partial unique indexes), CHECK
and FK constraint on the touched tables from the catalog before seeding and
seed to satisfy them. Fixture accounts (masked numbers only), positions,
lots, transactions and securities use reserved prefixes that match no real
row (assert it).

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool (wrap any
    `a and b` that could return a list in bool(...)).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Exclude this run's own fixtures from any "no leftovers" check; ignore
    fixture-org roles in any grant check.
  - Never spawn a background process. The regression subprocess gets
    VERIFY_NESTED=1, runs after teardown, 1200 s timeout; with
    VERIFY_NESTED=1 this script skips its regression section with one
    '[SKIP] nested run' line.
  - Every import at the top of the file.
  - Never touch real accounts, positions, lots, transactions, models, fee
    schedules/assignments or users: count + checksum before and after.

Assertions:
  [Y] Task 1 findings 1a–1e reported with signatures and chosen mechanisms
  [Y] Schema landed: tables, constraints, partial uniques, RLS with a policy
      for every operation
  [Y] Join: maker-checker on the identical request; fee assignment and
      security exclusion via the fee module; closed refused; advice-only for
      non-discretionary; one active link
  [Y] Exclusion policies exact
  [Y] Transition: lot order, short-term guard, exact gain budget, plan by
      year, unpriced never sold
  [Y] Proposals from all five triggers; notional for unpriced buys; whole vs
      fractional; dust suppressed; one open per account; expiry
  [Y] Wash-sale flag both directions across the household (IRA included);
      never blocks
  [Y] Execution: approved proposal → approved manual instructions with lots,
      maker and checker; rep2 flow unchanged; executed when all closed
  [Y] Manual variant name; .gitignore covers sprint_prompts/logs/ and no log
      is tracked
  [Y] Org isolation both directions (rolbypassrls = false); gates both ways;
      node tests show no controls without the flags
  [Y] New org's first org_admin gets the three proposal permissions
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no
      existing gate changed
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script,
      not during the sprint)
  [Y] Regression: verify_rep2.py ONCE, flat, after teardown,
      VERIFY_NESTED=1; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
