REP6 — Replication Studio, sprint 6 of 15: the tax engine — member tax
profiles, an approved substitution table, daily loss harvesting as
proposals, FULL household wash-sale control (replacing rep5's flag), the
loss bank and tax-alpha ledger, declared outside trades, the custodian-
harvest switch, and the harvest dashboard. No trades are placed; every
harvest is a rep5 proposal a person approves. 10 tasks + verification.

Context: spec "Replication Studio — ETF & Fund-to-SMA Spec" (Joe,
2026-10-22), section "Tax overlay" (phase-1 rows): lot-level accounting,
loss harvesting, household wash-sale control (spouse and IRAs included —
an IRA buy permanently forfeits the loss, Rev. Rul. 2008-5), substitution
table as a versioned approved compliance artifact (same-index funds never
pair), gain budget and transition (rep5 built), holding-period guard (rep5
built), loss bank and tax-alpha ledger, after-tax reporting; guardrails: tax
inputs come from the adviser with a date and are never guessed, every tax
figure is labelled an estimate, benefit decay shown honestly. Decisions
(Joe): Hollisworks runs its own lot-level engine with a per-account switch to
the custodian's engine (only when that custodian's
custodian_tax_loss_harvesting is evidenced 'yes'; Altruist: 'unknown');
every human action is a human task; custody cliff — nothing trades.

What this sprint does NOT do (data not available): tracking-error cost of a
harvest and pre-tax/after-tax PERFORMANCE need a licensed price history (none
yet — securities_global_prices has 0 rows); say "not estimated — needs
prices" wherever they would appear. Dividend holding-period guard,
charitable gifting, step-up flags and the Section 351 exit are later.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main @ 2667e7e, merge rep5; Task 1
confirms exact signatures):
- rep5: services/account_models.py — load_account, active_link, settings_of
  (settings include gain_budget, avoid_short_term, short_term_wait_days,
  harvesting_engine 'hollisworks' | 'custodian', exclusions,
  exclusion_policy, cash_floor, drift_threshold), harvesting_options
  (custodian only when custodian_tax_loss_harvesting is 'yes'),
  load_holdings, propose_change / decide (join approval on human tasks).
  services/rebalance_engine.py — Lot, Holding, term_of, long_term_from,
  lot_order_key, plan_sales (lot choice, gain budget, short-term guard),
  build, wash_sale_flags (basic 61-day FLAG, does not block — REPLACE with
  rep6's control). services/rebalance_proposals.py — generate(pool, *,
  org_id, account_id, trigger, maker_kind, maker_id, auto_submit,
  trigger_ref, withdrawal), TRIGGER_LABELS, compute, realized_ytd,
  household_accounts, household_transactions, submit / decide / execute
  (system proposals → rep2 instructions as drafts for a second person;
  person-made proposals → instructions start approved), org_tick (scheduler
  sweep), on_model_version_approved. Prices: an account's own position
  market_value / quantity (source 'account_position'); unpriced buys by
  notional.
- rep2: execution.create_instructions(…, upstream=), human_tasks
  (open_task / complete_task / cancel_task / list_tasks, on_complete
  callbacks, system_open review kinds), custodian_capabilities.capability.
- Holdings data: portfolio.positions (account_id, asset_id, quantity,
  market_value, as_of_date), portfolio.tax_lots (position_id, acquired_date,
  quantity, original_cost, adjusted_cost, term, realized_gain_loss, status),
  portfolio.transactions (position_id, transaction_type_code, trade_date,
  quantity, price, …), public.accounts (household_id, registration_type,
  tax_status, is_held_away), households, account_owners. Real accounts: 0.
- No tax-rate / tax-profile data exists anywhere in the app today.
- rep3: model_version_positions.alternates (jsonb identifiers) — a starting
  point for substitution candidates, never an approved pair by itself.
- authz1 rules; envelope pattern; org_id only from the session;
  extra='forbid'; Super Admin first. .gitignore now ignores
  sprint_prompts/logs/.
- Regression policy: this sprint replaces rep5's wash-sale flag and adds a
  proposal trigger → re-run verify_rep5.py ONCE (update ONLY its wash-sale
  checks if they assert the old flag-only behaviour, and say which lines).
  Scripts print 'TOTAL: N PASS, M FAIL'; accept 'RESULT:'.
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
Decimal everywhere. Never guess a tax rate, a price or a correlation. Every
tax number in the UI and API carries the label "estimate" and the rates and
as-of it used. Labels and vocabularies from the API envelope. Light/cream UI
with the existing tokens. Commit ALL sprint files (new and modified,
excluding sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Exact signatures this sprint calls in rebalance_engine.py,
      rebalance_proposals.py, account_models.py, execution.py,
      human_tasks.py, custodian_capabilities.py; where wash_sale_flags is
      called and every place its output is read (proposals, UI, verify_rep5
      lines).
  1b. How a household is resolved (household_id, primary entity, shared
      account_owners) and which entity a tax profile should hang on
      (household? tax-filing entity?). Choose and say why (a married couple
      filing jointly = one profile covering both spouses' accounts).
  1c. How realized gains/losses are known today: tax_lots.realized_gain_loss
      on closed lots, transactions, rep2 fills on instructions. Define the
      loss-bank ledger's sources so nothing is counted twice.
  1d. How open (not yet filled) execution instructions and pending proposals
      should count in wash-sale windows (a pending buy is a future buy).
  1e. Where the daily harvest scan hooks in (rep5's org_tick on the
      scheduler path) and its ordering relative to the holdings refresh and
      the drift scan.

=== TASK 2: SCHEMA ===
Design from Task 1 (org-scoped; names indicative):
  - tax_profiles: subject (per 1b), tax year, filing status, federal
    ordinary/short-term rate, federal long-term rate, net investment income
    tax applies (3.8%) yes/no, state, state rate, city, city rate, notes,
    as_of, entered_by, source ('adviser' only), approved (human task).
    Versioned; one live per subject and tax year. Combined ST and LT rates
    are computed, never stored.
  - loss_carryforwards: subject, tax year, short-term and long-term opening
    carryforward amounts, source document reference, entered_by, approved.
  - loss_bank_entries: subject, tax year, account, date, character (ST/LT),
    amount (negative = loss), source (lot_close | fill | carryforward |
    declared_outside), source id; unique per source id (no double count).
  - substitution_sets (versioned, approved compliance artifact): security →
    replacement candidates (ordered), each with reason text and evidence
    (adviser-entered; "same industry", "different index provider"), status
    draft | approved | retired; and substitution_denials: pairs that are
    substantially identical and may NEVER pair (seed rule: two funds
    tracking the same index; share classes of the same issuer; a security
    and itself). Approval via human task (approve_tax); maker ≠ checker.
  - declared_outside_trades: subject, account description (outside the
    platform), security, side, trade date, quantity, loss yes/no/unknown,
    declared_by, source note — so outside accounts count in wash windows.
  - harvest_runs / harvest_candidates: per account and run date — lot,
    unrealized loss, term, estimated tax value (loss × rate, labelled
    estimate), threshold test results, replacement chosen (or none + why),
    wash-sale result, outcome (proposed | skipped_reason).
Apply with scripts/_apply_rep6.py through Doppler, synchronously; VERIFY it
landed; save docs/rep6_part1.sql.

=== TASK 3: TAX PROFILES AND CARRYFORWARDS ===
Adviser enters a profile per subject and tax year (rates as numbers with the
as-of and source note); approval by a second person (human task,
approve_tax). Combined rates: ST = federal ordinary + NIIT (if applies) +
state + city; LT = federal LT + NIIT + state + city (state/city treat LT as
ordinary unless the adviser enters a separate LT rate — add optional
state_lt_rate / city_lt_rate). No profile → every tax value reads "not
estimated — no tax profile" (never a default rate). Carryforwards entered
from the prior return, approved the same way.

=== TASK 4: HOUSEHOLD WASH-SALE CONTROL (services/wash_sale.py) ===
Replace rep5's flag with control, used by every proposal (rebalance,
transition, harvest):
  - Window: 30 days before and after a loss sale (61 days), across every
    account of the subject's household (spouse and IRAs/Roth included),
    open execution instructions and pending approved proposals (per 1d),
    and declared outside trades.
  - "Substantially identical" = the same security, OR a pair in
    substitution_denials (either direction). Never anything inferred.
  - Rules: (a) a BUY of S within 30 days after a household loss sale of S
    or a denied pair → replace with the next approved substitute that is
    itself clear, or defer the buy to the first clear date (stated), or keep
    the cash (setting); (b) a LOSS SALE of S when S (or a denied pair) was
    bought in the prior 30 days in the household → defer the sale to the
    first clear date or sell non-matching lots instead (stated); (c) any
    trade that would put a substantially identical BUY into an IRA/Roth
    inside a window of a taxable loss sale → hard block (the loss is lost
    permanently), named in plain words; (d) a planned loss sale also
    reserves its forward 30 days: later proposals see it.
  - Output per trade: clear | replaced (by X) | deferred (until date) |
    blocked (reason), with the matches that caused it. Proposals carry the
    results; a blocked trade never reaches an instruction.
  - Update verify_rep5.py ONLY where it asserts flag-only behaviour.

=== TASK 5: SUBSTITUTION TABLE ===
Screens and services to propose replacement sets per security (candidates
may be seeded from the model's alternates but each needs a reason and
approval); denials seeded from obvious rules (same security; securities
whose terms carry the same index_name in rep1's approved terms — only when
both are in the registry; share classes the security master links) plus
adviser-entered pairs; approval on human tasks (approve_tax); every version
kept; only APPROVED sets are used by the engine.

=== TASK 6: HARVESTING (services/harvest.py) ===
Daily scan (hook per 1e) for every active link with harvesting_engine =
'hollisworks':
  - Candidates: open lots whose unrealized loss (account valuation price −
    adjusted cost) × quantity exceeds BOTH the account's % threshold and $
    threshold (settings; org defaults), net of an estimated trading cost
    (org bps setting × 2 sides); unpriced lots are skipped ("needs a
    price").
  - Value: loss × the lot's combined rate (ST if the lot is short-term on
    the sale date, else LT) from the approved profile — "estimate"; no
    profile → candidate listed but value "not estimated".
  - Replacement: the first approved substitute that wash-sale control
    clears; none → skip with reason (or harvest to cash if the account
    setting allows, stated); also a "swap back" option: after 31 days a
    proposal to return to the original security when it is clear (setting).
  - Respect the gain budget and short-term guard already in rep5.
  - Output: ONE proposal per account per day with trigger 'harvest' through
    rep5's generate (system maker → review task → a human approves); every
    candidate (taken or skipped with its reason) recorded in
    harvest_candidates.
  - harvesting_engine = 'custodian': no harvest proposals; realized losses
    still arrive through transactions into the loss bank; wash-sale control
    still applies to Hollisworks' own proposals.

=== TASK 7: LOSS BANK AND TAX-ALPHA LEDGER ===
loss_bank_entries filled from sources per 1c (idempotent); per subject and
tax year: realized ST/LT gains and losses, netting in the IRS order (ST vs
ST, LT vs LT, then cross-net; up to $3,000 against ordinary income — $1,500
married filing separately; remainder carried forward with its character),
carryforward in/out, and the estimated tax value of harvested losses at the
profile's rates (estimate). A per-account and per-household view; "after-tax
performance: not estimated — needs prices".

=== TASK 8: API, PERMISSIONS, SCREENS ===
New permissions view_tax, manage_tax (profiles, carryforwards, declared
trades, substitution proposals), approve_tax — org_admin by default and in
rbac.ensure_org_admin_role. Harvest proposals keep rep5's
approve_proposals. Routes under /api/v1/tax/… and /api/v1/harvest/…, gated,
in AUTHZ1_ROUTES; envelopes with permissions and vocabularies (filing
statuses, wash-sale outcomes, candidate skip reasons, ledger sources).
Screens:
  - Portfolio group → tab "Tax" (gated view_tax): Harvest dashboard across
    households (harvestable losses today with estimated value, candidates
    skipped and why, gain budget used, open wash-sale windows with end
    dates, loss bank by member); Substitutions (sets and denials, versions,
    approve); Profiles (per household and year, approve); Declared outside
    trades.
  - Account page (rep5): a Tax panel (profile summary, loss bank, open
    windows, last harvest run); proposal trades show their wash-sale outcome
    (clear / replaced / deferred / blocked) with the matches.
  - Every tax number labelled "estimate" with rates and as-of on hover.

=== TASK 9: REAL PROOF ===
  - Profiles: entered and approved by different people (maker refused on the
    identical request); combined ST/LT rates exact incl. NIIT and state/city;
    no profile → "not estimated" everywhere (never a default rate).
  - Wash-sale: (a) buy after a household loss sale (another account) →
    replaced by the approved substitute; with no clear substitute →
    deferred to the exact first clear date; (b) loss sale after a recent
    household buy → deferred or other lots, stated; (c) IRA buy inside a
    taxable loss window → blocked with the plain-words reason; (d) a pending
    approved proposal's planned loss sale blocks a later proposal's buy;
    denied pairs count, approved substitutes do not; declared outside trades
    count; day 31 is clear, day 30 is not (both edges, before and after).
  - Substitutions: only approved sets used; a denial overrides a set; maker-
    checker on approval; versions kept.
  - Harvest: thresholds (% and $, net of trading cost) exact; ST/LT value
    exact from the profile; unpriced lots skipped with reason; replacement
    chosen through wash-sale control; one proposal per account per day with
    trigger 'harvest', approved by a human; every candidate recorded;
    custodian engine → no harvest proposals; swap-back after 31 days.
  - Loss bank: sources idempotent (re-run adds nothing); IRS netting order
    exact; $3,000 / $1,500 limit; carryforward character kept; tax value
    labelled estimate.
  - rep5 regression behaviour: proposals now carry wash-sale outcomes instead
    of flags; verify_rep5 updated only where it asserted flags.
  - Org isolation both directions (rolbypassrls = false); gates both ways on
    identical requests; node tests show no Approve / Edit controls without
    the flags; tax data never visible without view_tax.
  - New org's first org_admin gets the three tax permissions.
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_rep5.py ONCE, flat, after teardown, VERIFY_NESTED=1,
    1200 s timeout.

=== TASK 10: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (tax engine: profiles,
wash-sale rules with the window edges, substitutions, harvesting, loss bank,
what needs prices), and add verify_rep6.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep6.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
calls. Wrap every section so an exception is a [FAIL]; teardown at start and
end in FK child-before-parent order from pg_constraint, deleting ONLY rows
this run created, by fixture id (never walk the FK graph from a broad
parent); clear audit_log, domain_events, member_todos, workflow rows created
by this run before fixture users; reconnect once and re-count if a
connection drops; then re-read every touched table for its exact pre-test
count. Fixture users created explicitly with the auth0_sub the mocked token
carries, is_test = true, using the users-RLS pattern from verify_rep1–5.
Read every UNIQUE (including partial unique indexes), CHECK and FK constraint
on the touched tables from the catalog before seeding, and check every seed
literal against its lookup table or CHECK (instrument classes, custodian
codes, registration_type, tax_status, transaction_type_code, asset_type,
model source/origin, proposal trigger/status) before inserting — the rep5
seed failed on exactly this. Fixture households, accounts (masked numbers
only), positions, lots, transactions and securities use reserved prefixes
that match no real row (assert it).

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
  - Never touch real accounts, positions, lots, transactions, proposals,
    instructions or users: count + checksum before and after.

Assertions:
  [Y] Task 1 findings 1a–1e reported with signatures and chosen definitions
  [Y] Schema landed: tables, constraints, partial uniques, RLS with a policy
      for every operation
  [Y] Profiles and carryforwards: maker-checker; exact combined rates; no
      profile → not estimated
  [Y] Wash-sale control: replace / defer / block rules, IRA hard block,
      pending trades and declared outside trades count, both window edges
  [Y] Substitutions: approved-only, denials override, maker-checker,
      versions kept
  [Y] Harvesting: thresholds, exact estimated values, unpriced skipped,
      replacement via wash-sale control, one human-approved proposal per
      account per day, candidates recorded, custodian engine respected,
      swap-back
  [Y] Loss bank: idempotent sources, IRS netting order, $3,000 / $1,500
      limit, carryforward character, estimates labelled
  [Y] rep5 proposals carry wash-sale outcomes; verify_rep5 changed only
      where it asserted flags
  [Y] Org isolation both directions (rolbypassrls = false); gates both ways;
      node tests show no controls without the flags
  [Y] New org's first org_admin gets the three tax permissions
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no
      existing gate changed
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script,
      not during the sprint)
  [Y] Regression: verify_rep5.py ONCE, flat, after teardown,
      VERIFY_NESTED=1; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
