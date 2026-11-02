REP9 — Replication Studio, sprint 9 of 15: block trading, allocation and
DVP settlement, manual-first — approved trades across accounts aggregated
into blocks under a written allocation policy fixed before execution; the
block executed by a broker / desk of the adviser's choice (always a manual
"print an order execution sheet" route; a FIX adapter interface for later);
fills booked at the average price with pro-rata partials; each account's
allocation settled DVP to its own custodian; reconciliation; trade-error
corrections. Nothing is sent electronically. 10 tasks + verification.

Context: Joe (2026-10-26): decision 6 was "DVP into held-away accounts" —
trade a block in an omnibus account and settle allocations DVP into member
accounts at the same or another custodian; block trading / allocation /
automated DVP belong in rep9. Joe (2026-10-28): there must ALWAYS be a manual
option in the UX — even when a custodian API or FIX route exists, the adviser
can print an order execution sheet and give it to a broker of choice, then
confirm fills by hand. The spec's roadmap names rep9 "FIX adapter (Flyer or
similar) with block trading in an omnibus account, pre-trade allocation,
average-price booking and DVP settlement". No FIX router is contracted:
build the adapter INTERFACE ("not connected", no network) and the full
manual flow.

Compliance items stay open for Joe and counsel (show them as notes, do not
decide them): the written aggregation and allocation policy and its ADV
disclosure; best-execution review; trade-away commissions; receiving-
custodian DVP agreements; whether Altruist accepts DVP in. The policy text is
an org setting the firm writes; the code enforces its mechanics.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main after the marketdata1 merge;
Task 1 confirms exact signatures):
- rep2 execution instructions: executor_type custodian_desk | broker |
  member | other_adviser | trader; price_kind market | limit | range;
  statuses draft → pending_approval → approved → sent → partially_filled →
  filled → reconciled → closed | cancelled | expired; maker-checker CHECKs;
  execution_instruction_lots (specific lots); execution_fills;
  reconcile_method custodian_data | manual; services/execution.py
  (create_instructions, letter), execution_letter.py (letter of instruction
  PDF for member / other_adviser, mask unless the full number is explicitly
  supplied and checked against the hash).
- rep5: rebalance_proposals.execute routes an approved proposal's trades to
  instructions (system proposals → drafts for a second person; person-made →
  approved via upstream=); custody/trading.route_trade_list; custodian
  adapters (Altruist, manual); capability() 'unknown' = not available (all
  Altruist capabilities 'unknown').
- rep7b: authority (read_only | advise | managed) × custody (integrated |
  other_custodian | outside) via services/authority.py; managed +
  other_custodian trades produce manual DVP settlement instructions:
  instruction_settlements (receiving custodian, participant number, agent
  bank, settlement date T+1 weekends only — no holiday calendar, stated) and
  services/settlement_letter.py. Advise accounts execute themselves (letter
  of instruction) and are NEVER in a block.
- lotmethod: lots chosen per account by its method; specific lots on each
  instruction.
- marketdata1: services/security_data get(data_type, …, purpose) — a
  reference price for price guidance comes from the layer (source + as-of)
  or the adviser types one; never invented.
- Human tasks: services/human_tasks.py (open_task, register_kind,
  register_precheck). Scheduler sweeps skip is_test orgs.
- Verify cleanup: scripts/_verify_cleanup.py (fast, scoped; --cleanup-only).
- authz1 rules; envelope pattern; org_id only from the session;
  extra='forbid'; Super Admin first.
- Regression: rep9 changes the instruction / settlement path → re-run
  verify_rep7b.py ONCE, flat, after teardown, VERIFY_NESTED=1 (it then skips
  its own nested verify_rep7), 2700 s.
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
account number (masked only, except the existing explicit full-number path).
Column names from the live DB. Bi-temporal rule 3 for every new table; RLS
policies for EVERY operation each new table will take. Decimal everywhere;
share quantities obey each account's fractional setting. An allocation, once
fixed, changes only through a recorded correction with a reason and a second
person. Every human action is a human task. Labels and vocabularies from the
API envelope. Light/cream UI with the existing tokens. Commit ALL sprint files
(excluding sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The life of an approved trade today: proposal → instruction(s) →
      fills → lots → reconcile; which tables and functions; where a block
      sits (parent of several account instructions) without breaking rep2's
      flow for single-account trades.
  1b. Which instructions are block-eligible: managed authority,
      discretionary, same security, same side, executor broker / trader /
      custodian_desk, same execute-by window; advise and read_only never;
      how rep5's advice_only links fit.
  1c. The omnibus account: how to represent the firm's executing / omnibus
      account at a broker (an org-level record, never a member account) and
      the receiving side per allocation (integrated custodian vs other
      custodian via rep7b's settlement details).
  1d. Rounding: whole-share vs fractional accounts, how pro-rata partials
      and residual shares are allocated deterministically (e.g. largest
      remainder, then account id), and the minimum-allocation rule.
  1e. What the order execution sheet must carry for a broker (no member
      names; account references masked; the allocation table; settlement
      instructions per allocation; price guidance with its source and as-of;
      execute-by; firm contact) and what rep2's letter already renders.

=== TASK 2: SCHEMA ===
Design from Task 1 (org-scoped; names indicative):
  - execution_accounts: the firm's omnibus / executing accounts at brokers
    (broker name, account reference masked, DVP capable yes/no, notes).
  - allocation_policies (versioned, approved by a second person): method
    pro_rata_requested (default) | others the firm adds later; minimum
    allocation; rounding rule; partial-fill rule; text of the written policy
    (the firm's words) and its ADV reference; status draft | approved.
  - trade_blocks: security, side, total quantity, executor (broker / desk /
    trader) and executing account, route manual | fix (fix = "not
    connected"), price guidance (kind, limit / range, reference price +
    provider + as-of), execute-by, policy version, status draft →
    pending_approval → approved → sent → partially_filled → filled →
    allocated → settled → closed | cancelled; maker-checker.
  - block_allocations: block → account instruction, requested quantity,
    allocated quantity (pre-trade, fixed at approval), final quantity
    (after fills), average price, settlement link (rep7b
    instruction_settlements or integrated), status.
  - block_fills: fill quantity, price, time, venue, entered_by (manual) —
    average price computed, never entered.
  - allocation_corrections: allocation, before / after, reason, maker,
    checker (human task), at.
  - commissions / fees per block (adviser-entered; trade-away noted).
Apply with scripts/_apply_rep9.py through Doppler, synchronously; VERIFY it
landed; save docs/rep9_part1.sql.

=== TASK 3: BUILD A BLOCK ===
  - From approved account instructions (Task 1b) the adviser selects
    eligible ones (or the system suggests groupings by security / side /
    executor / window); ineligible ones are listed with the plain reason.
  - Opposite sides of the same security are never netted across accounts;
    a buy and a sell block of the same security in the same window are
    flagged (cross-trade risk) and need a reason.
  - The pre-trade allocation is computed under the approved policy and
    FIXED when the block is approved (maker-checker); each account's lots
    stay its own (lotmethod), carried on its instruction.
  - Route: manual (always available, default while no FIX router) or fix
    ("not connected" — refused with the reason).

=== TASK 4: ORDER EXECUTION SHEET (manual route — ALWAYS offered) ===
  - A PDF per block (reportlab, never stored, generated on demand like
    rep2's letter): firm, executing account (masked), security (CUSIP /
    ticker), side, total quantity, price guidance with its source and
    as-of, execute-by, what to do if not filled, the allocation table
    (account reference masked, quantity, receiving custodian, DVP
    instructions per allocation), contact, block id; no member names.
  - Also for a single account instruction with executor broker / desk /
    trader: the same sheet with one allocation, so "manual" is available on
    every trade the platform proposes, even where a custodian API exists
    (record the adviser's choice and reason when an automated route was
    available).
  - Marking the block "sent" records channel and who.

=== TASK 5: FILLS, AVERAGE PRICE, PARTIALS ===
  - Fills entered against the block (quantity, price, time, venue); the
    average price is computed (Decimal, stated rounding).
  - Full fill → every allocation gets its fixed quantity at the average
    price. Partial fill → pro rata per the policy's rule with deterministic
    rounding (1d), whole shares for whole-share accounts, residual by the
    stated rule, minimum allocation respected (an allocation below it gets
    zero and the reason).
  - Allocated → each account instruction gets its fill at the average price;
    lots booked per account (rep2 / lotmethod path); unfilled remainder
    handled by the block's if-not-filled rule (cancel | carry | ask → a
    to-do).
  - Corrections after allocation only through allocation_corrections
    (reason + second person); the original stays readable.

=== TASK 6: DVP SETTLEMENT AND RECONCILIATION ===
  - For each allocation: integrated custodian → the custodian receives the
    allocation (manual instruction to the custodian desk, as today);
    other custodian → rep7b's DVP settlement instruction (receiving
    custodian, participant number, agent bank, settlement date T+1
    weekends-only stated) generated from the allocation.
  - Settlement status per allocation (pending | affirmed | settled | failed
    with reason), entered manually or matched from the custodian feed /
    held-away import (rep7 records) — the block closes when every allocation
    is settled or cancelled.
  - Fails → a to-do with the allocation and the counterparty.

=== TASK 7: FIX ADAPTER INTERFACE ===
A provider-neutral execution-router interface (submit block, cancel, receive
execution reports, allocation instructions) with a registry of routers, none
connected ("not connected"); no client code, no network; the "add a router"
checklist in the design doc. Choosing route fix is refused with the reason.

=== TASK 8: API, PERMISSIONS, SCREENS ===
New permissions view_blocks / manage_blocks / approve_blocks (org_admin by
default and in rbac.ensure_org_admin_role) — or reuse approve_proposals where
Task 1 shows it fits; say which and why. Routes under /api/v1/execution/
blocks/…, gated, in AUTHZ1_ROUTES; envelopes with vocabularies. Screens:
Execution → Blocks (suggested groupings, build, allocation preview under the
policy, approve, print sheet, enter fills, allocate, settlement status,
corrections); Settings → Allocation policy (versions, approve) and Executing
accounts; every account instruction shows "Print order execution sheet"
where its executor is a broker / desk / trader.

=== TASK 9: REAL PROOF ===
  - Eligibility: advise / read_only / non-discretionary refused with the
    reason; opposite sides never netted; same-window buy + sell flagged.
  - Allocation fixed at approval (maker refused, checker approves); later
    change only via a correction with reason + second person; original kept.
  - Average price exact over three fills; full fill exact per account.
  - Partial fill: pro rata exact with deterministic rounding (whole-share
    accounts integers; residual by the rule; fractional account to its
    precision); minimum allocation → zero with the reason; totals equal the
    filled quantity exactly.
  - Lots booked per account at the average price, each account's chosen lots
    relieved for sells.
  - DVP: an other-custodian allocation gets a settlement instruction with
    the receiving details and T+1 (weekends only, stated); integrated gets
    the custodian desk instruction; settlement statuses; a fail opens a
    to-do; the block closes only when all allocations settle / cancel.
  - Order execution sheet: %PDF, masked references, no member names,
    allocation table and DVP details present, price guidance shows source +
    as-of; available for a single-account broker instruction too; the
    choice of manual over an available automated route is recorded.
  - FIX: registry "not connected"; route fix refused; no network.
  - Org isolation both directions (rolbypassrls = false); gates both ways on
    identical requests; node tests show no approve / allocate / correct
    controls without the flags.
  - Routes: count THIS sprint's routes from its own diff and prove they
    still exist — never "every route added since a base commit"; no
    existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_rep7b.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 10: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (block lifecycle,
allocation math, the always-manual rule, DVP, FIX interface and checklist,
open compliance items), add verify_rep9.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep9.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
calls. Wrap every section so an exception is a [FAIL]; use
scripts/_verify_cleanup.py for teardown at start and end (rows this run
created, by fixture id); re-read every touched table for its exact pre-test
count. Compute every expected date at the call. Fixture orgs and users
is_test = true (assert it); users-RLS pattern from earlier verifies. Read
UNIQUE / CHECK / FK constraints from the catalog before seeding and check
every seed literal against its lookup or CHECK (executor_type, instruction /
block statuses, authority / custody, policy method). Reserved fixture
prefixes that match no real row (assert it). Support --cleanup-only. The
regression subprocess gets VERIFY_NESTED=1, runs after teardown, 2700 s
timeout; with VERIFY_NESTED=1 this script skips its regression section with
one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool — wrap every
    `a and b` in bool(...).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Every import at the top of the file; never `x, y = (await f(a) for a …)`.
  - SQL parameters inside CASE / COALESCE get an explicit cast — in the code
    too.
  - Unique "one live row per X per day" rules: count live rows, not rows of
    one run.
  - A failing check's detail prints the response body / culprit rows.
  - Never touch real accounts, positions, lots, proposals, instructions,
    settlements or users: count + checksum before and after.

Assertions:
  [Y] Task 1 findings 1a–1e
  [Y] Schema landed, RLS for every operation
  [Y] Eligibility rules; no netting; cross flag
  [Y] Allocation fixed at approval; corrections with reason + second person
  [Y] Average price; full and partial allocation exact; minimum allocation
  [Y] Lots per account at the average price
  [Y] DVP settlement per allocation; statuses; fails; block close rule
  [Y] Order execution sheet (block and single); manual always offered and
      recorded
  [Y] FIX interface not connected; route fix refused
  [Y] Org isolation both directions; gates both ways; node tests
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rep7b.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
