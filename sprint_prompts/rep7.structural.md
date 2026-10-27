REP7 — Replication Studio, sprint 7 of 15: held-away accounts and the
portfolio X-ray — read-only positions and lots for accounts held elsewhere
(manual entry, file upload, Chancery statements, and an aggregator adapter
interface), with source and as-of on every position; held-away data feeding
household wash-sale control, the gain budget and the X-ray; a stale-data
confirmation task; and the X-ray that looks through every fund a member holds,
marks the replicable ones and opens a join request in one click. Nothing is
traded in a held-away account. 9 tasks + verification.

Context: spec "Replication Studio — ETF & Fund-to-SMA Spec" (Joe,
2026-10-22), sections "Custodians, held-away accounts and manual execution"
and "Other features worth building" (Portfolio X-ray, phase 1), roadmap line
"rep7: held-away accounts (aggregator and statement sources), portfolio
X-ray". Decisions (Joe): read-only visibility into held-away accounts; sources
best first — a direct custodian feed, an aggregator (e.g. ByAllAccounts,
Plaid), statements filed in Chancery and extracted as the fallback; each
position records its source and as-of date; uses — household wash-sale
control (IRAs held elsewhere included), the X-ray, overlap with the replica,
gain budgets that count gains realized elsewhere; stale data is visible: a
proposal that depends on held-away data older than a set number of days shows
the date and needs an adviser to confirm it (a human task); every human action
is a human task; no Claude models for extraction.

What this sprint does NOT do: no aggregator is contracted — build the
provider-neutral adapter interface and a "not connected" state, no provider
client and no network calls. Completion portfolios (replica built around
held-away holdings) and overlap consolidation are rep10. Any X-ray figure that
needs a price history (tracking error, harvest value of switching, after-tax
performance) reads "not estimated — needs prices" (securities_global_prices
has 0 rows).

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main @ ead5a7f, merge rep6; Task 1
confirms exact signatures):
- public.accounts already has is_held_away (read by account_models.py:144,
  execution.py:160/202/951). account_models.harvesting_options /
  available(cap) already excludes held-away accounts from custodian actions
  (account_models.py:219). Real accounts: 0.
- Holdings: portfolio.positions (account_id, asset_id, quantity, market_value,
  as_of_date), portfolio.tax_lots (position_id, acquired_date, quantity,
  original_cost, adjusted_cost, term, realized_gain_loss, status),
  portfolio.transactions (position_id, transaction_type_code, trade_date,
  quantity, price, …), households, account_owners.
- rep6: services/wash_sale.py (household window, rules a–d, IRA hard block,
  declared_outside_trades count), harvest.py, loss_bank.py (sources
  lot_close | fill | carryforward | declared_outside; unique per source id),
  tax_profiles.py, substitutions.py; services/orgs.is_test_org /
  not_test_org_sql — every scheduler sweep skips is_test orgs.
- rep5: account_models (load_account, active_link, settings_of, load_holdings,
  propose_change / decide), rebalance_engine (plan_sales, gain budget,
  short-term guard), rebalance_proposals (generate, realized_ytd,
  household_accounts, household_transactions, submit / decide / execute,
  org_tick, account_lock).
- rep4: replica_models / replica_viability — the five viability questions;
  RM.term_value(src.terms, "expense_ratio_net_pct"); "Holdings of …" models
  live in rep3's registry (services/models.py).
- rep1: replica registry and holdings ingestion (SPY from State Street; QQQ /
  IVV from N-PORT, months stale — show the holdings as-of).
- rep3: model upload with column mapping and a permanent upload log — reuse
  its parser / mapping machinery for held-away position files; do not build a
  second one.
- rep2: human_tasks (open_task / complete_task / cancel_task / list_tasks,
  register_kind with on_complete / system_open), execution instructions.
- Chancery: services/chancery_intake.py, portfolio_chancery.py,
  ai_ensembles.py, extraction.py (the ensemble refuses Claude models). No
  ai_ensemble_configs row exists yet for statement extraction.
- Fees: services/fee_schedules.py, fee_calc.py (rep5 joins assign fees through
  the fee module).
- authz1 rules; envelope pattern; org_id only from the session;
  extra='forbid'; Super Admin first. sprint_prompts/logs/ is gitignored.
- Regression policy: this sprint changes wash-sale control and the gain
  budget inputs (shared by rep5 / rep6) → re-run verify_rep6.py ONCE, flat,
  after teardown, VERIFY_NESTED=1, timeout 2700 s (verify_rep6 takes ~30 min;
  1200 s killed a nested run on 2026-10-26). Scripts print 'TOTAL: N PASS,
  M FAIL'; accept 'RESULT:'.
- The live Render scheduler (main) acts every 5 minutes; it skips is_test
  orgs. Every fixture org this verify creates MUST be is_test = true.
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
account number (masked numbers only). Column names from the live DB.
Bi-temporal rule 3 for every new table; RLS policies for EVERY operation each
new table will take. Decimal everywhere. Never guess a price, a weight, a fee
or a tax rate; never infer a held-away position. Held-away accounts are never
traded: no rebalance, harvest or custodian action ever targets one. Every
estimate is labelled "estimate" with its inputs and as-of. Labels and
vocabularies from the API envelope. Light/cream UI with the existing tokens.
Commit ALL sprint files (new and modified, excluding sprint_prompts/logs) on
the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. How held-away accounts exist today: who sets is_held_away, which
      custodian_code / institution fields a held-away account carries, and
      every place that reads is_held_away (incl. proposals, wash_sale,
      harvest, loss_bank, X-ray candidates). List where a held-away account
      could today slip into a trade path.
  1b. Whether held-away positions / lots / transactions should live in
      portfolio.positions / tax_lots / transactions with a source + as-of
      (preferred: one place for every reader) or in separate tables. Choose,
      and say what source / as-of columns are missing.
  1c. rep6's wash_sale.py and rep5's realized_ytd / household_accounts:
      exactly how they gather the household's trades and realized gains, so
      held-away lots and transactions join them with no double count against
      declared_outside_trades (a declared trade later matched by a feed must
      not count twice).
  1d. rep3's upload parser / column mapping / upload log: signatures and how
      to reuse them for a positions-and-lots file. Chancery: how a statement
      document is filed and linked to an account, and what extraction.py /
      ai_ensembles.py need for a 'statement_positions' purpose (refuses
      Claude; no config row → the path says "no extraction models
      configured").
  1e. rep1 / rep4: how a held fund is matched to the replica registry
      (CUSIP / ticker / ISIN), where its holdings and holdings as-of come
      from, where expense_ratio_net_pct and the viability verdict are read,
      and how sector / industry is known for a constituent (security master
      field or "unclassified").

=== TASK 2: SCHEMA ===
Design from Task 1 (org-scoped; names indicative):
  - held_away_sources: account, kind (manual | file | statement | aggregator
    | custodian_feed), provider (null for manual/file/statement), status
    (active | not_connected | retired), last_as_of, stale_after_days
    (setting; org default), notes.
  - Position / lot / transaction provenance: source id + as_of on every row a
    held-away source writes (columns or a link table per 1b).
  - held_away_imports: one row per load — source, file / document reference,
    mapping used, rows read / accepted / rejected with reasons, status
    (draft | approved | rejected), entered_by, approved_by (human task). Only
    an APPROVED import changes positions.
  - stale_confirmations: account, as_of confirmed, confirmed_by, for which
    proposal / purpose, expires (human task).
  - xray_runs / xray_lines: per subject (household) and run — holding (account,
    security, market value, as-of, source), look-through constituent, weight,
    weight as-of, exposure (estimate), sector; per fund: matched registry
    fund, replicable yes/no with the viability verdict, estimated annual fee
    difference, embedded gain and estimated transition tax, break-even, and
    status lines ("not estimated — needs prices" / "no tax profile").
Apply with scripts/_apply_rep7.py through Doppler, synchronously; VERIFY it
landed; save docs/rep7_part1.sql.

=== TASK 3: HELD-AWAY SOURCES AND IMPORTS ===
  - Manual: an adviser keys positions and lots for a held-away account (with
    the statement date as as-of and an optional Chancery document link).
  - File: positions + lots from xlsx / xls / csv through rep3's mapping
    machinery; every load logged in held_away_imports; rejected rows listed
    with reasons; nothing changes until a second person approves (human task,
    maker ≠ checker).
  - Statement: a Chancery document linked to a held-away account; extraction
    through the ensemble for purpose 'statement_positions' (never Claude);
    the result is a DRAFT import the adviser reviews and a second person
    approves; with no ensemble configured, the screen says so and offers
    manual entry from the document.
  - Aggregator: a provider-neutral adapter interface (accounts, positions,
    lots, transactions, as-of) with a registry of providers, none connected;
    status "not connected"; no client code, no network.
  - An approved import replaces that account's held-away positions as of its
    date (earlier as-of versions kept); lots and transactions keep their
    source.

=== TASK 4: HELD-AWAY IN TAX AND PROPOSALS ===
  - Wash-sale control (rep6) counts held-away lots and transactions in the
    household window — IRAs held elsewhere included, so rule (c) blocks a
    substantially identical buy into ANY household IRA, held away or not. A
    declared outside trade matched by a held-away feed counts once (per 1c).
  - Gain budget and realized YTD count gains realized in held-away accounts
    of the same subject (stated in the proposal as "includes N realized
    elsewhere").
  - Loss bank: held-away realized losses enter with source 'held_away' (new
    source value), idempotent.
  - Staleness: a proposal whose wash-sale or gain-budget result depends on a
    held-away account whose as-of is older than its stale_after_days shows the
    date and cannot be approved until an adviser confirms the data (human
    task 'confirm_held_away_data'; maker ≠ the proposal's checker is NOT
    required, but the confirmation is logged with who and which as-of).
    Confirmation expires when newer data arrives or after stale_after_days.
  - Guard: every trade path (rebalance, transition, harvest, instruction
    creation) refuses a held-away account with a plain-words reason; add the
    guard in one place per path and prove it.

=== TASK 5: PORTFOLIO X-RAY (services/xray.py) ===
Per subject (household), on demand and on the daily scheduler sweep (skip
is_test orgs):
  - Holdings: every account of the subject, managed and held-away, with
    source and as-of per line.
  - Look-through: each fund holding matched to the replica registry (per 1e)
    is decomposed by its holdings file (weight × the position's market
    value), labelled with the holdings as-of; unmatched funds stay as one
    line "no look-through"; single stocks pass through.
  - Aggregates: exposure by security and by sector across the household,
    including concentration flags (setting: single-name % threshold),
    overlap between funds (shared constituents), and overlap with any replica
    the household already follows.
  - Replicable funds: for each matched fund — viability verdict from rep4
    (or "no model yet"); estimated annual fee difference = market value ×
    (expense_ratio_net_pct − the replica's all-in fee from the fee module for
    that account; none assigned → "no fee schedule"); embedded gain from the
    fund's lots and estimated transition tax at the rep6 profile's combined
    rates (no profile → "not estimated — no tax profile"); break-even years =
    transition tax ÷ annual fee difference; harvest benefit and tracking
    error "not estimated — needs prices". All labelled estimate.
  - One click: for a MANAGED account holding a replicable fund with an
    approved "Holdings of …" model, "Open join request" calls rep5's
    propose_change for that account and model (the person clicking is the
    maker; rep5's approval flow follows). For a held-away holding the button
    is absent and the line reads "held away — moving the assets comes first".

=== TASK 6: API, PERMISSIONS, SCREENS ===
Permissions: reuse view_tax / manage_tax for imports and confirmations where
they fit, or add view_held_away / manage_held_away / approve_held_away (Task 1
chooses and says why) — granted to org_admin by default and in
rbac.ensure_org_admin_role. X-ray reads need view_replication plus access to
the household. Routes under /api/v1/held-away/… and /api/v1/xray/…, gated, in
AUTHZ1_ROUTES; envelopes with permissions and vocabularies (source kinds,
import statuses, reject reasons, X-ray status lines).
Screens (light/cream):
  - Account page (held-away account): Sources panel (kind, status, last
    as-of, stale badge), Imports (log, draft review, approve), positions and
    lots with source and as-of; no trade controls at all.
  - Household → tab "X-ray": exposure by security and sector (table + simple
    bars), funds with look-through as-of, overlap, concentration flags, and
    the replicable-funds table with fee difference, transition tax,
    break-even and the "Open join request" button where allowed.
  - Proposal view (rep5): stale held-away badge and the confirm task link.
  - Every estimate labelled with inputs and as-of on hover.

=== TASK 7: REAL PROOF ===
  - Held-away guard: every trade path refuses a held-away account (rebalance,
    transition, harvest, instruction) on the identical request that succeeds
    for a managed account.
  - Imports: file through rep3's mapping; rejected rows with reasons; draft
    changes nothing; maker refused, second person approves; positions
    replaced as of the date, earlier version kept; statement extraction with
    no ensemble → "no extraction models configured" (and with a MOCKED
    ensemble result → a draft import; never Claude; no network).
  - Aggregator: interface present, providers listed "not connected", no
    network.
  - Wash-sale: a loss sale in a managed account + a held-away buy of the same
    security inside the window → the held-away buy counts; a held-away IRA
    makes rule (c) block a substantially identical buy; a declared outside
    trade matched by a held-away transaction counts once; both window edges.
  - Gain budget counts held-away realized gains (exact numbers); loss bank
    source 'held_away' idempotent.
  - Staleness: as-of older than stale_after_days → proposal shows the date
    and approval refused until the confirm task completes; newer data expires
    the confirmation; exactly at the threshold day vs one day over.
  - X-ray: look-through exposure exact (weights × market value) with the
    holdings as-of; unmatched fund stays one line; sector totals exact;
    concentration flag at the threshold; fee difference, embedded gain,
    transition tax and break-even exact from fixture inputs; no profile → not
    estimated; needs-prices lines present; "Open join request" opens rep5's
    join for a managed account and is absent / refused for a held-away one.
  - Org isolation both directions (rolbypassrls = false); household access
    enforced; gates both ways on identical requests; node tests show no
    import / approve / join controls without the flags and no trade controls
    on a held-away account.
  - New org's first org_admin gets the new permissions (if any were added).
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_rep6.py ONCE, flat, after teardown, VERIFY_NESTED=1,
    2700 s timeout.

=== TASK 8: SCHEDULER ===
The daily X-ray sweep and the staleness check join rep5's org_tick path after
the holdings refresh; both skip is_test orgs via services/orgs; neither places
a trade or opens a proposal (the X-ray only records a run).

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (held-away sources and
their order, imports and approval, staleness rule, how held-away data enters
wash-sale / gain budget / loss bank, the X-ray method and every "needs
prices" line), and add verify_rep7.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep7.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
calls (mock the extraction ensemble). Wrap every section so an exception is a
[FAIL]; teardown at start and end in FK child-before-parent order from
pg_constraint, deleting ONLY rows this run created, by fixture id (never walk
the FK graph from a broad parent); clear audit_log, domain_events,
member_todos, workflow rows created by this run before fixture users;
reconnect once and re-count if a connection drops; then re-read every touched
table for its exact pre-test count. Fixture orgs is_test = true (the live
scheduler skips them — assert it). Fixture users created explicitly with the
auth0_sub the mocked token carries, is_test = true, using the users-RLS
pattern from verify_rep1–6. Read every UNIQUE (including partial unique
indexes), CHECK and FK constraint on the touched tables from the catalog
before seeding, and check every seed literal against its lookup table or
CHECK (instrument classes, custodian codes, registration_type, tax_status,
transaction_type_code, asset_type, model source/origin, proposal
trigger/status, loss-bank source) before inserting. Fixture households,
accounts (masked numbers only), positions, lots, transactions, documents and
securities use reserved prefixes that match no real row (assert it).

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool (wrap any
    `a and b` that could return a list in bool(...)).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Exclude this run's own fixtures from any "no leftovers" check; ignore
    fixture-org roles in any grant check.
  - Never spawn a background process. The regression subprocess gets
    VERIFY_NESTED=1, runs after teardown, 2700 s timeout; with
    VERIFY_NESTED=1 this script skips its regression section with one
    '[SKIP] nested run' line.
  - Every import at the top of the file.
  - Never touch real accounts, positions, lots, transactions, proposals,
    instructions, documents or users: count + checksum before and after.
  - A failing check's detail names the culprit rows (ids, trigger / source,
    maker, created_at), so an outside writer can be told apart from the code.

Assertions:
  [Y] Task 1 findings 1a–1e reported with signatures and chosen designs
  [Y] Schema landed: tables, constraints, partial uniques, RLS with a policy
      for every operation
  [Y] Held-away accounts refused by every trade path; no trade controls
  [Y] Imports: mapping reused, rejects with reasons, draft changes nothing,
      maker-checker, as-of versions kept; statement extraction never Claude,
      "no extraction models configured" when none; aggregator not connected
  [Y] Wash-sale with held-away lots / transactions incl. held-away IRA block;
      declared trade matched once; both window edges
  [Y] Gain budget and loss bank count held-away realized results exactly,
      idempotent
  [Y] Staleness: threshold edge, approval refused until confirmed,
      confirmation expiry
  [Y] X-ray: look-through exact with as-of, unmatched funds, sectors,
      concentration, overlap, fee difference / transition tax / break-even
      exact, not-estimated lines, join button managed-only
  [Y] Org isolation both directions (rolbypassrls = false); household access;
      gates both ways; node tests show no controls without the flags
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no
      existing gate changed
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script,
      not during the sprint)
  [Y] Regression: verify_rep6.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
