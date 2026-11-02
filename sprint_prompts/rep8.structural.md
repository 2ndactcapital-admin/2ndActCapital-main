REP8 — Replication Studio, sprint 8 of 15 (phase 2 starts): the options
sleeve — buffer (defined-outcome) ETFs and covered-call / income ETFs
replicated as the underlying plus an options package; option contracts in the
security master; the TAX GATE every options proposal must pass; the payoff
lab; outcome-period rolls; minimum-account sizing. Execution is manual
instructions only. 10 tasks + verification.

Context: spec "Replication Studio — ETF & Fund-to-SMA Spec", sections
"Options sleeve (buffer ETFs and notes)", "Options overlays" (tax-treatment
table + tax gate), "Terms captured per class" (buffer ETF and income ETF
rows), "Other features worth building" (payoff lab), roadmap line "rep8
options sleeve (buffer and income ETFs), tax gate, payoff lab, rolls".
Decisions (Joe): instrument order index ETFs → defined-outcome options sleeve
→ notes → long/short; every human action is a human task; custody cliff —
nothing trades; execution instructions wherever a trade cannot go
automatically.

PHASE-2 GATES ARE NOT MET — build honestly around them:
- Altruist's options level / FLEX access is unconfirmed: every Altruist
  options capability is 'unknown' (= not available). So EVERY options trade
  becomes a manual execution instruction (custodian desk / broker /
  trader), and the viability check says why. No order is ever sent.
- No options data source and no licensed price vendor (securities_global_
  prices has 0 rows): option premiums, index levels and implied vols come
  ONLY from adviser-entered quotes (value, source text, as-of timestamp,
  entered_by), shown with their age; nothing is fetched, guessed or
  defaulted. Anything that needs a missing input reads "not priced — enter a
  quote". At-expiry payoffs need no prices (strikes only) and are always
  available.
- Counsel question open: whether index options written against a full stock
  basket are offsetting positions under the straddle rules (26 U.S.C. 1092).
  Until counsel answers, the gate IDENTIFIES them as straddles and treats
  them conservatively (loss deferral flagged; holding-period suspension
  noted), labelled "pending counsel review".

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main @ 5681d30; Task 1 confirms exact
signatures):
- portfolio.replica_instrument_classes rows: index_etf, index_mutual_fund
  (enabled); active_etf, active_mutual_fund, closed_end_fund,
  fixed_income_fund (disabled, "Later phase"). No buffer / income class yet.
- custodian_capabilities: keys include options_level ("Options level (0–4)",
  OPTIONS_LEVEL_VALUES; available when level >= at_least) and flex_options
  (yes / no / unknown); capability() treats 'unknown' as not available; all
  13 Altruist capabilities are 'unknown'.
- rep1: services/replica_terms.py (terms versions, CRITICAL_TERMS in
  replica_models.py:108 incl. expense_ratio_net_pct, approval on human
  tasks, extraction ensemble refuses Claude), replica_holdings.py (snapshots
  / rows), replica_parsers.py (holdings file parsers; State Street SPY;
  N-PORT fallback).
- rep4: replica_models / replica_viability (five questions; Q3 "unknown —
  needs prices"; Q4 reads the capabilities matrix); "Holdings of …" models in
  rep3's registry (services/models.py) with sleeves (multi-sleeve models).
- rep5 / rep6 / lotmethod: rebalance_engine.plan_sales (method-aware lot
  choice, gain budget, short-term guard), rebalance_proposals.generate /
  submit / decide / execute, services/wash_sale.py (household window, rules
  a–d, substitution denials), harvest.py, loss_bank.py, tax_profiles
  (combined_rates).
- rep2: execution instructions (executor_type custodian_desk | broker |
  member | other_adviser | trader; price_kind market | limit | range;
  execution_instruction_lots; maker-checker CHECKs), letters (execution_
  letter.py). rep7b authority: services/authority.py — options trades follow
  the same authority × custody matrix (read_only / advise never get an
  options sleeve; managed only).
- Human tasks: services/human_tasks.py (open_task, register_kind,
  register_precheck). Scheduler: rebalance_proposals.org_tick; every sweep
  skips is_test orgs (services/orgs).
- Verify cleanup: scripts/_verify_cleanup.py (fast, scoped; --cleanup-only).
- authz1 rules; envelope pattern; org_id only from the session;
  extra='forbid'; Super Admin first.
- Regression: rep8 adds a sleeve type and gates options proposals inside
  rep5's proposal path and rep6's wash-sale control → re-run verify_rep6.py
  ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s.
- The live Render scheduler skips is_test orgs; every fixture org MUST be
  is_test = true.
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
Decimal everywhere. Never guess a price, a volatility, an index level or a
tax rate. Every priced figure is labelled "estimate" with its quotes and
their as-of. Tax-gate rules that depend on the counsel question are labelled
"pending counsel review". Labels and vocabularies from the API envelope.
Light/cream UI with the existing tokens. Commit ALL sprint files (excluding
sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. How a security is represented (portfolio.securities_global / assets /
      identifiers) and the cleanest way to add option contracts (underlying,
      call/put, strike, expiry, exercise style european/american, settlement
      cash/physical, FLEX yes/no, multiplier, OCC symbol when listed) so
      positions, lots, instructions and wash-sale control can all reference
      them.
  1b. How rep1's terms and holdings model a fund today, and what a buffer
      ETF's terms (underlying, outcome period start/end, cap, buffer, floor,
      each leg) and an income ETF's terms (underlying, call-writing rule:
      strike as % OTM or delta, tenor, coverage %) need; can a buffer ETF's
      holdings file show its option legs (parser work, or adviser entry).
  1c. Where an options sleeve sits in a model (rep3 sleeves) and in a
      proposal (rep5 trades with option legs), and how execution
      instructions carry an option leg (fields; contracts not shares).
  1d. Every place wash_sale.py decides "substantially identical" — an option
      on the same issuer / index counts for the gate (Task 5).
  1e. Index-level / underlying-price inputs available today (none licensed)
      and where adviser-entered quotes should live (a quotes table keyed by
      instrument and as-of).

=== TASK 2: SCHEMA ===
Design from Task 1 (org-scoped where data is the org's; platform where
reference; names indicative):
  - replica_instrument_classes: add buffer_etf and income_etf (enabled).
  - option contracts in the security master (per 1a) with a CHECK on every
    vocabulary; broad-based index flag (drives Section 1256).
  - option_packages / option_package_legs: a sleeve's legs (role
    underlying | long_put_atm | short_put_buffer_end | short_call_cap |
    short_call_income | long_put_tail; contract; ratio; strike as level and
    as % of start; expiry), outcome period, cap / buffer / floor computed
    and stored, status draft | approved | rolled | expired; versioned.
  - adviser_quotes: instrument, kind (premium | index_level | underlying_price
    | implied_vol), value, source text, as_of, entered_by; never updated in
    place (new row per quote).
  - options_tax_gate_results: per proposal and leg — rule, outcome (pass |
    block | flag), plain-words reason, pending_counsel yes/no.
Apply with scripts/_apply_rep8.py through Doppler, synchronously; VERIFY it
landed; save docs/rep8_part1.sql.

=== TASK 3: TERMS AND THE BUILDER ===
  - Terms capture for buffer_etf and income_etf (rep1 pattern: extracted by
    the non-Claude ensemble where configured, else adviser entry; approved by
    a second person; CRITICAL_TERMS extended: outcome period, cap, buffer,
    each leg).
  - "Holdings of …" builder (rep4 pattern) for a buffer ETF: core sleeve =
    the underlying basket (from the index ETF replica where one exists) or
    the ETF itself; options sleeve = the four-leg package with FLEX index
    options as the default leg (European, cash-settled, Section 1256), a
    fresh outcome period starting on the funding date allowed; for an income
    ETF: core + written calls per the rule.
  - Minimum account: contract multiplier × index level (adviser quote) →
    the smallest account that holds the sleeve within a hedge-ratio
    tolerance (setting); mini-index contracts considered where listed (flag
    in the contract); "not priced — enter an index level" without a quote.
  - Basis risk reported (basket vs index): tracking error "not estimated —
    needs prices", stated.
  - Viability Q4 for options: reads options_level / flex_options; 'unknown'
    → "manual execution only — Altruist options access unconfirmed", never a
    block on the core.

=== TASK 4: PAYOFF LAB ===
  - At-expiry payoff of the replica's package vs the buffer ETF's stated
    outcome (cap / buffer / floor) across underlying returns −50% … +50%
    (step setting), exact from strikes and ratios, always available.
  - Today's mark-to-market and the cap a new package would set (re-pricing
    at today's quotes): only from adviser quotes, labelled estimate with
    their as-of; missing → "not priced".
  - A structured-note comparison slot (note terms from the 424B2 pipeline)
    marked "phase 3" and empty.
  - Light/cream chart; an accessible table of the same points.

=== TASK 5: THE TAX GATE (services/options_tax_gate.py) ===
Every options proposal runs it before approval (register_precheck on the
proposal's approval task); failures BLOCK and name the rule; flags show on
the proposal:
  - Section 1256 classification (broad-based index options → 60/40 and
    year-end mark-to-market noted; single-stock / narrow options → not 1256).
  - Wash sale: an option on the same issuer / index exposure counts as
    substantially identical in the household window (extend wash_sale.py's
    identity check — never weaken it); a cash-secured put on a stock sold at
    a loss within 30 days is a wash-sale buy → block.
  - Straddles: offsetting positions identified (index options vs a full
    stock basket → straddle, pending counsel); loss deferral and
    holding-period suspension flagged; a proposal that would realize a loss
    on one leg while the offset stays open → block with the rule.
  - Constructive sale: a collar on an appreciated position whose band is
    narrower than a setting (e.g. 15% between strikes) → block.
  - Qualified covered call (single-stock calls): exchange-traded, more than
    30 days to expiry, not deep in the money (needs an underlying price
    quote; missing → block "cannot qualify without a price").
  - Every result stored in options_tax_gate_results with the inputs.

=== TASK 6: PROPOSALS, INSTRUCTIONS, ROLLS ===
  - Options proposals through rep5's generate / submit / decide (maker-
    checker; authority managed only, rep7b); the gate runs at approval.
  - Approved options trades → manual execution instructions per leg
    (contracts, limit or range price from an adviser quote with its as-of,
    expiry, FLEX terms when FLEX; executor custodian_desk / broker / trader);
    never routed to an adapter API.
  - Fills entered manually (or reconciled later); option lots booked.
  - Rolls: the daily sweep (skips is_test orgs) opens a to-do N days (setting)
    before an outcome period ends with a DRAFT next package (same buffer, cap
    "not priced" until quotes are entered); a person prices and proposes it.
  - Expiry: a package past expiry with no roll is marked expired and a to-do
    opened; cash-settled index options produce a settlement entry for the
    adviser to confirm.

=== TASK 7: API, PERMISSIONS, SCREENS ===
Reuse view / manage / approve_replication for terms, packages and quotes;
approve_proposals for options proposals. Routes under /api/v1/replication/
options/…, gated, in AUTHZ1_ROUTES; envelopes with vocabularies (leg roles,
gate rules and outcomes, quote kinds). Screens: model page → Options sleeve
(legs, outcome period, cap / buffer / floor, minimum account, quotes with
age, viability note); Payoff lab; proposal view → tax-gate results per leg;
Quotes entry (instrument, kind, value, source, as-of).

=== TASK 8: REAL PROOF ===
  - Payoff exact: for a fixture buffer package (start 100, buffer 10%, cap
    15%), the at-expiry replica payoff equals the stated outcome at −50, −15,
    −10, −5, 0, +5, +15, +30 (both edges of the buffer and the cap).
  - Minimum account exact from multiplier × index-level quote; no quote →
    "not priced".
  - Quotes: never updated in place; age shown; a priced figure lists its
    quotes; nothing priced without a quote.
  - Tax gate: 1256 classification; wash-sale block for an option on a
    household loss-sale issuer (both window edges); cash-secured put wash
    block; straddle identified with pending-counsel label and loss-on-one-leg
    block; constructive-sale collar block at / inside the band setting;
    qualified covered call (31 days pass / 30 days fail; deep-ITM fail;
    no price → block).
  - Approval refused while any gate rule blocks (409 with the rule); a
    passing proposal → manual instructions per leg with contract fields;
    never an adapter call; authority read_only / advise refused.
  - Rolls: to-do N days before expiry with a draft next package; expired
    package → to-do + settlement entry; is_test skipped by the live sweep.
  - Viability: Altruist 'unknown' → manual-only note; core unaffected.
  - Org isolation both directions (rolbypassrls = false); gates both ways on
    identical requests; node tests show no approve / roll controls without
    the flags.
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_rep6.py ONCE, flat, after teardown, VERIFY_NESTED=1,
    2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (options sleeve, payoff
math, tax-gate rules with the pending-counsel items, what needs quotes,
phase-2 gates still open), add verify_rep8.py to scripts/regression_set.txt.

=== TASK 10: SAFETY NET ===
python -m py_compile on every changed script; node --test tests/ (allowed);
nothing else.

=== VERIFICATION: apps/api/scripts/verify_rep8.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
calls. Wrap every section so an exception is a [FAIL]; use
scripts/_verify_cleanup.py for teardown at start and end (rows this run
created, by fixture id); re-read every touched table for its exact pre-test
count. Compute every expected date at the call. Fixture orgs and users
is_test = true (assert it); users-RLS pattern from earlier verifies. Read
UNIQUE / CHECK / FK constraints from the catalog before seeding and check
every seed literal against its lookup or CHECK (instrument classes, option
vocabularies, executor_type, proposal trigger / status, quote kinds).
Reserved fixture prefixes that match no real row (assert it). Support
--cleanup-only. The regression subprocess gets VERIFY_NESTED=1, runs after
teardown, 2700 s timeout; with VERIFY_NESTED=1 this script skips its
regression section with one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool — wrap every
    `a and b` in bool(...).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Every import at the top of the file; never `x, y = (await f(a) for a …)`.
  - SQL parameters inside CASE / COALESCE get an explicit cast ($4::uuid) —
    in the code too (a missing cast 500'd dealroom3).
  - A failing check's detail prints the response body / culprit rows.
  - Never touch real accounts, positions, lots, proposals, instructions,
    securities or users: count + checksum before and after.

Assertions:
  [Y] Task 1 findings 1a–1e
  [Y] Schema landed (classes, option contracts, packages / legs, quotes, gate
      results), RLS for every operation
  [Y] Terms capture + approval for buffer / income ETFs; builder makes core +
      options sleeves; minimum account exact or "not priced"
  [Y] Payoff lab exact at every edge; mark-to-market only from quotes
  [Y] Tax gate: every rule, both edges where dated, pending-counsel labels,
      blocks name the rule
  [Y] Proposals → manual per-leg instructions; never an adapter call;
      authority respected
  [Y] Rolls and expiry to-dos; settlement entry; is_test skipped
  [Y] Viability: options 'unknown' → manual-only note; core unaffected
  [Y] Org isolation both directions; gates both ways; node tests
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rep6.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
