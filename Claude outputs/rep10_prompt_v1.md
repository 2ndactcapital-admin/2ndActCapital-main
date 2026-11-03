REP10 — Replication Studio, sprint 10 of 15 (closes phase 2): tilts,
overlap consolidation and completion portfolios, charitable lot gifting, and
the backtest — "the index plus my views" on any replica with the cost shown;
a household's replica built AROUND what it already holds elsewhere and
overlapping funds consolidated into it; the highest-gain long-term lots given
in kind to a charity or donor-advised fund with the tax saved shown and an
optional rebuy; and a backtest of the replica and its harvesting against the
fund, labelled hypothetical and gated by the firm's hypothetical-performance
policy. Every price-dependent number reads "not estimated — needs prices"
when the data is not there; nothing is invented. Delivery of gifts is manual
(always). 10 tasks + verification.

Context: the spec's roadmap names rep10 "tilts, overlap consolidation,
backtest, charitable gifting". Spec rows: Tilt editor — "the index plus my
views": factor tilts or over- and underweights, with the tracking-error cost
shown live. Charitable lot gifting — picks the highest-gain long-term lots for
a gift or donor-advised fund, delivers in kind, rebuys; a proposal with the
tax saved shown. Backtest — replays the replica and its harvesting through
past markets (2008, 2020, 2022) against the fund. rep7 deferred "completion
portfolios and overlap consolidation" to rep10 (completion portfolios: the
replica is built to fill around what is held elsewhere). Spec compliance:
"backtests and tax-benefit estimates are hypothetical performance, shown only
under the firm's hypothetical-performance policy." Joe (2026-10-28): no paid
data vendor until a use case exists; vendors are plug-ins behind marketdata1's
layer. The backtest and tracking error ARE such use cases: build them on the
layer and let missing data say so — do not add a vendor.

Open items for Joe, counsel and the firm's CPA (show as notes in the design
doc and on the screens; do not decide them): the hypothetical-performance
policy (SEC Marketing Rule) and who may see a backtest; gifting AGI limits
(30% of AGI for appreciated long-term stock to a public charity or DAF, 20%
to a private foundation, 5-year carryover — shown as estimates), whether the
member itemizes, Form 8283 for non-cash gifts over $500, the donee's
acceptance of in-kind securities; factor-score data licences.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main after the vhk1 merge, bfdfe16;
Task 1 confirms exact signatures):
- Price data today: portfolio.securities_global_prices 0 rows,
  portfolio.index_levels 0 rows, portfolio.adviser_quotes 0 rows. There is
  NO price history in the database.
- marketdata1: services/security_data/ — layer.get(conn, org_id, data_type,
  subject, *, as_of, purpose), get_bulk, history(…, start, end, purpose);
  types.DATA_TYPES = ("eod_price", "corporate_action", "fund_holdings",
  "index_level", "option_quote", "fx"); providers with ranks per data type
  (platform default, org override), licence rights by purpose, call budgets,
  data_quality_events; uploads.py (CSV uploads with maker-checker); the
  not-connected vendor slot. Every price read carries source + as-of.
- rebalance_proposals.py:365 — target = rebalance_engine.apply_exclusions(
  target, excluded, link["exclusion_policy"], alternates, labels); the
  target is built per account from its followed replica model (account
  models); rep5 proposals, maker-checker, execute → instructions.
- rep6: tax_profiles (filing_status, federal_ordinary_rate, federal_lt_rate,
  niit, state / city rates, as_of, maker-checker versions) — NO AGI, no
  itemizing flag, no charitable carryover; loss_bank.py; harvest.py (loss
  threshold, wash-sale windows, household control); tax_overview.py.
- rep7: xray.py — compute / run per household; xray_runs, xray_lines
  (line_kind, account_id, global_security_id, fund_security_id, weight,
  exposure, sector, replicable, as_of, source); overlap between funds;
  embedded_by_character; transition_tax; break_even; open_one_off /
  open_join; held-away data with stale-data confirmation.
- rep7b: authority (read_only | advise | managed) × custody (integrated |
  other_custodian | outside) via services/authority.py; read_only is never
  traded; advise → one-off proposals as manual instructions / letter.
- lotmethod: services/lot_method.py — effective method per account
  (household default, account override, per-proposal), specific lots on
  each instruction; lot_options().
- rep9: the order execution sheet is always offered (manual route); blocks.
- Human tasks: services/human_tasks.py (open_task, register_kind,
  register_precheck). Scheduler sweeps skip is_test orgs.
- vhk1 (docs/VERIFY_RULES.md): apps/api/scripts/_verify_self.py —
  sprint_end_ref, own_routes(before_ref, sprint_key), still_gated,
  own_lines, when_empty; every verify supports --self-checks-only and
  --cleanup-only; teardown via scripts/_verify_cleanup.py.
- authz1 rules; envelope pattern; org_id only from the session;
  extra='forbid'; Super Admin first.
- Regression: rep10 changes the rebalance target and proposal path → re-run
  verify_rep5.py ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s.
- The live Render scheduler skips is_test orgs; every fixture org MUST be
  is_test = true.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any full verify script (--self-checks-only and --cleanup-only
  MAY be run; they are quick).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never poll in a loop, never start a
script in the background; run every command synchronously in the foreground
and read its output directly. Write the verify script but DO NOT RUN IT, and
do not run npm run build. No network calls; no new data vendor. Never print a
secret or a full account number. Column names from the live DB. Bi-temporal
rule 3 for every new table; RLS policies for EVERY operation each new table
will take. Decimal everywhere. A number that needs data the platform does not
have reads "not estimated — needs <what>" with the coverage found — never a
placeholder figure. Every tax number is "$X (estimate)" with the rates and
as-of. Backtests and projected tracking error are hypothetical: labelled so on
every screen, PDF and envelope, and never in a member-facing envelope unless
the firm's approved policy allows it. Every human action is a human task;
maker-checker on tilt sets, gift proposals and the policy. Labels and
vocabularies from the API envelope. Light/cream UI with the existing tokens.
SQL parameters inside CASE / COALESCE get explicit casts. Commit ALL sprint
files (excluding sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Where an account's target weights are built (model → account target →
      exclusions → trades): the exact function and the one place a tilt
      layer fits (after exclusions, before trades), so exclusions, lot
      method, gain budget and wash-sale control still apply on top.
  1b. What the X-ray already knows about overlap: every security's exposure
      across funds and accounts in a household, by authority / custody;
      which accounts' holdings a completion target may count (all) and
      which it may trade (managed only).
  1c. Lots and the gift path: unrealized gain and holding period per lot
      (embedded_by_character / lot_method); how a lot leaves an account
      without a sale today (transfer out? none?) and how an in-kind delivery
      instruction fits rep2 / rep9 instructions (executor, statuses) so the
      manual route covers it.
  1d. What price history security_data.history can return today (expect
      none) and how the CSV upload path stores eod_price history (platform
      vs org scope, maker-checker), so a backtest's coverage can be measured
      and a verify can load synthetic history into a FIXTURE scope only.
  1e. Whether anything resembling a hypothetical-performance or marketing
      policy, disclosure library or member-visibility flag already exists
      (grep docs, settings, compliance tables).

=== TASK 2: SCHEMA ===
Design from Task 1 (org-scoped; names indicative):
  - tilt_sets (per account, versioned, maker-checker): status draft →
    pending_approval → approved | archived; tilt_lines: kind security |
    sector | factor, subject, delta (bps or pct, signed), reason. Org
    settings for limits: max per-security tilt, max sector tilt, max total
    active weight, tracking-error budget (used only when estimable).
  - completion settings per account (on its replica link): count_elsewhere
    on/off, which authorities count, stale-data threshold.
  - charitable_donees (org-scoped): name, kind public_charity | daf |
    private_foundation, EIN (format-checked), receiving broker, DTC
    participant, account reference (masked + hash, as rep2), contact.
  - gift_proposals: household, member (subject), donee, target amount or
    lots, tax year, inputs used (AGI estimate, itemizes yes/no, prior
    carryforward — each with source and as-of, entered on the proposal),
    status draft → pending_approval → approved → sent → received →
    acknowledged | cancelled; gift_lots (account, lot, quantity, FMV at
    proposal and at receipt, gain, holding period); rebuy choice (none |
    same security | replica-consistent).
  - backtests: subject (replica model vs fund), window (preset 2008 | 2020
    | 2022 | custom), settings (harvest rules from rep6, rebalance cadence),
    coverage found, status computed | not_estimated, result (series,
    statistics), hypothetical = true always; created_by.
  - hypothetical_performance_policies (versioned, approved by a second
    person): the firm's text, who may see backtests (staff only | staff and
    members with the disclosure), required disclosure text, status.
  - data type 'factor_score' added to security_data (no provider; CSV upload
    and vendor slot only).
Apply with scripts/_apply_rep10.py through Doppler, synchronously; VERIFY it
landed; save docs/rep10_part1.sql.

=== TASK 3: TILTS ===
  - Security and sector over- / underweights applied to the account target
    after exclusions (1a); weights renormalised by a stated rule; a tilt on a
    security not in the model adds it (stated); limits enforced from org
    settings with the plain reason when refused.
  - Factor tilts only when factor scores exist for the model's constituents
    through the layer (coverage stated); otherwise refused "needs factor
    scores".
  - Always shown: active share, largest single-name and sector deviations
    versus the model. Tracking error estimated (ex-ante, covariance from
    eod_price history with the window and coverage stated) ONLY when history
    covers the stated threshold; otherwise "not estimated — needs prices
    (x% of weight covered)". Recomputed live on every edit (preview route,
    nothing written).
  - Approved tilt set feeds rep5 proposals; the proposal shows model weight,
    tilt, final weight per line.

=== TASK 4: COMPLETION PORTFOLIOS AND OVERLAP CONSOLIDATION ===
  - Completion: with count_elsewhere on, a managed account's replica target
    subtracts the household's exposure already held elsewhere (read_only,
    advise, outside accounts and look-through of other funds, from the
    X-ray) so the HOUSEHOLD total matches the model; never negative (the
    excess is reported as "over target elsewhere — not traded"); held-away
    data older than the threshold needs the adviser's confirmation (rep7).
  - Consolidation: for a household, the X-ray lists funds that overlap the
    replica (or each other) above a threshold; a consolidation proposal
    sells the overlapping funds in MANAGED accounts and moves the money into
    the replica (rep5 proposal, lot method, gain budget, transition tax and
    break-even from rep7); advise accounts get a one-off advice letter;
    read_only are listed, never traded.

=== TASK 5: CHARITABLE LOT GIFTING ===
  - Candidates: long-term lots with the highest unrealized gain per dollar,
    in managed (or advise → manual letter) accounts; short-term lots
    excluded (deduction limited to basis — stated); loss lots excluded with
    "sell and give cash instead" shown; lot method respected for the
    remaining position.
  - Comparison per proposal: gift in kind vs sell and give cash — capital
    gains tax avoided (gain × the profile's LT + NIIT + state rates),
    deduction value (FMV × ordinary rate, only if itemizes = yes; else
    "no deduction value — does not itemize"), AGI limit by donee kind with
    the excess carried forward (estimate, stated), all labelled estimates.
  - Rebuy option: same security right away (no wash-sale issue on a gain —
    stated; fresh basis at the rebuy price) or replica-consistent; produces
    a rep5 proposal for the rebuy.
  - Delivery is MANUAL: a transfer-of-securities letter PDF (reportlab,
    generated on demand, never stored) to the custodian: donee, receiving
    broker, DTC participant, masked account, lots, quantities; statuses
    sent → received (date the donee's account received the shares = the
    gift date, entered) → acknowledged (donee's receipt noted); reminders:
    Form 8283 above $500, year-end transfer lead time (a to-do when a
    December gift is not received by a date the firm sets).

=== TASK 6: BACKTEST AND THE HYPOTHETICAL-PERFORMANCE GATE ===
  - Engine: replays the replica (model weights at each rebalance, the
    stated cadence) and its harvesting (rep6 thresholds, wash-sale windows,
    loss bank) against the fund over a window, from eod_price history
    through the layer only. Output: cumulative return series for both,
    tracking error realised, maximum drawdown, harvested losses, estimated
    tax value of harvesting (rates stated), fees (fund expense ratio vs the
    firm's fee schedule).
  - Coverage first: if history covers less than the stated threshold of the
    replica's weight or days in the window, the backtest is stored as
    not_estimated with the coverage found and the missing securities — no
    partial figures.
  - Every result is hypothetical = true; its PDF and screen carry the
    approved policy's disclosure text. With no approved policy: staff only,
    with "no hypothetical-performance policy approved — staff use only".
    Member envelopes never carry a backtest unless the approved policy
    allows members.

=== TASK 7: SCHEDULES AND SWEEPS ===
Year-end gift reminder sweep and completion staleness check as scheduler
tasks (skip is_test orgs); nothing runs backtests automatically.

=== TASK 8: API, PERMISSIONS, SCREENS ===
Permissions: reuse view_replication / manage_replication / approve_replication
for tilts, completion and consolidation; view_tax / manage_tax / approve_tax
for gifting; a new manage_hypothetical_policy for the policy (org_admin by
default and in rbac.ensure_org_admin_role) — or say why another fits. Routes
under /api/v1/replication/… and /api/v1/tax/gifts/…, gated, in
AUTHZ1_ROUTES; envelopes with vocabularies. Screens: Replication → Tilts
(editor with live active share / TE or "not estimated"), account →
Completion, Household X-ray → Consolidate, Tax → Charitable gifts (donees,
candidates, comparison, approve, print transfer letter, receipt),
Replication → Backtest (run, coverage, result with disclosure), Settings →
Hypothetical-performance policy.

=== TASK 9: REAL PROOF ===
  - Tilts: target after exclusions + tilts exact on a fixture model;
    renormalisation exact; limits refused with the reason and nothing
    written; factor tilt refused without scores; active share exact; TE
    "not estimated" with zero history, and computed (exact against a
    hand-computed covariance) after synthetic history is loaded into the
    fixture scope; the rep5 proposal shows model / tilt / final weight.
  - Completion: household total equals the model within stated rounding;
    excess elsewhere reported, never a negative target; read_only never
    traded; stale held-away data needs confirmation.
  - Consolidation: overlapping funds listed; proposal sells only in managed
    accounts, advise gets a letter, read_only untouched; transition tax and
    break-even shown.
  - Gifting: only LT gain lots chosen, ordered by gain per dollar; ST and
    loss lots excluded with reasons; comparison figures exact for both
    itemizes yes and no and for each donee kind's AGI limit with carryover;
    transfer letter %PDF (pypdf text), masked account, no full number; gift
    date = received date; rebuy proposal created; maker refused, checker
    approves.
  - Backtest: with no history → not_estimated with coverage and missing
    list; with synthetic fixture history → series, TE, drawdown, harvested
    losses exact against a hand-computed small case; hypothetical label and
    disclosure present; with no approved policy a member caller gets no
    backtest; with a policy allowing members, the member sees it with the
    disclosure.
  - Org isolation both directions (rolbypassrls = false); gates both ways
    on identical requests; node tests show no approve / edit controls
    without the flags.
  - Routes: this sprint's own routes via _verify_self.own_routes(BEFORE_REF,
    "rep10") and still_gated — never "every route added since a base
    commit"; no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_rep5.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 10: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (tilt math, completion
rule, consolidation, gifting math and the open counsel / CPA items, backtest
method and coverage rule, the hypothetical-performance gate, "phase 2
complete"), add verify_rep10.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep10.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
calls. Wrap every section so an exception is a [FAIL]; use
scripts/_verify_cleanup.py for teardown at start and end (rows this run
created, by fixture id — including any synthetic price history, which must
live only in the fixture scope); re-read every touched table for its exact
pre-test count. Compute every expected date at the call. Fixture orgs and
users is_test = true (assert it); users-RLS pattern from earlier verifies.
Read UNIQUE / CHECK / FK constraints from the catalog before seeding and
check every seed literal against its lookup or CHECK. Reserved fixture
prefixes that match no real row (assert it). Support --self-checks-only and
--cleanup-only (docs/VERIFY_RULES.md). Real-data checks use
_verify_self.when_empty. The regression subprocess gets VERIFY_NESTED=1,
runs after teardown, 2700 s timeout; with VERIFY_NESTED=1 this script skips
its regression section with one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool — wrap every
    `a and b` in bool(...).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Every import at the top of the file; never `x, y = (await f(a) for a …)`.
  - SQL parameters inside CASE / COALESCE get an explicit cast — in the code
    too.
  - PDF text via pypdf, never a raw stream inflate.
  - A failing check's detail prints the response body / culprit rows.
  - Never touch real accounts, positions, lots, proposals, instructions,
    prices or users: count + checksum before and after.

Assertions:
  [Y] Task 1 findings 1a–1e
  [Y] Schema landed, RLS for every operation
  [Y] Tilts: target exact, limits, factor refusal, active share, TE
      not-estimated vs computed, proposal lines
  [Y] Completion: household total, no negative target, read_only untouched,
      stale confirmation
  [Y] Consolidation: managed sells only, advise letter, read_only listed
  [Y] Gifting: lot choice, exclusions, comparison math (itemizes yes / no,
      donee kinds), transfer letter, received date, rebuy, maker-checker
  [Y] Backtest: coverage refusal, exact small case, hypothetical label,
      policy gate for members both ways
  [Y] Org isolation both directions; gates both ways; node tests
  [Y] Own routes gated and in AUTHZ1_ROUTES.json; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rep5.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
