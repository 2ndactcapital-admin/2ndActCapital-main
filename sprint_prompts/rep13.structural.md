REP13 — Replication Studio phase 4 begins: the tax-aware LONG/SHORT
extension (130/30, 150/50) on any custodian that declares margin and short
selling. A model can carry negative target weights; an account opts in with
an extension level and limits; proposals build the long and short books
together; borrow cost sits in the cost comparison; harvesting works on both
sides; a hard block stops a constructive sale. Manual execution always
available. 10 tasks + verification. Branch rep13-work.

Spec: "Replication Studio — ETF & Fund-to-SMA Spec" (Joe, 2026-10-22),
section "Long/short and options overlays"; sprint plan: "Phase 4 — rep13
long/short extension on any custodian that declares shorting; rep14 Section
351 exit; rep15 platform-published models and tenant subscriptions".
docs/REPLICATION_STUDIO.md describes rep1–rep12 as built.

FROM THE SPEC (Joe's design, do not re-decide):
- $100 of capital holds $130 long and $30 short (150/50: $150 / $50). The
  shorts are the names the signal likes least. Losses come from both sides,
  so harvesting stays productive after long-only harvesting dries up.
- Settings per account: extension level (130/30, 150/50), tracking-error
  limit, maximum short per name, borrow-cost ceiling, exclusions.
- Signal: Hollisworks has NO stock-selection model. Recommended (spec):
  a sub-adviser's signal delivered as TARGET WEIGHTS. This sprint takes
  target weights through the existing model management (rep3 upload, upload
  log, library) — a long/short model is a model whose weights may be
  negative and sum to the extension's net exposure (100 %), gross = 130 /
  160 / 200 % as declared. Building our own factor signal is NOT in scope
  (open question for Joe; record it).
- Needs: a margin account and a stock-borrow source; borrow fees and short
  rebates in the cost comparison; short gains treated as short-term; a HARD
  BLOCK on shorting a stock the household holds long at a gain (a
  constructive sale taxes the gain at once — IRC §1259).
- Suitability: long/short needs a margin agreement recorded against the
  member's suitability profile before a proposal can be approved.
- Risks to disclose on every long/short proposal: pre-tax
  underperformance, varying tax benefit, lower future rates, unused losses,
  larger tax at redemption, rule changes or IRS challenge (AQR's list).
- Custodian: multi-custodian adapters; long/short runs on whichever
  connected custodian declares margin AND short selling. Altruist has not
  said it supports short selling — so today no live custodian qualifies; the
  path is proven on a fixture custodian capability, and the manual route
  (printed order sheet, manual fills) is always offered (Joe, 2026-10-28).
- Fees: overlays carry their own bps (spec "Overlay add-ons"); borrow cost
  is a line in the proposal's cost comparison ("Borrow cost (long/short
  only) — estimated").

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (Task 1 confirms signatures):
- rep2: public.custodian_capabilities (platform matrix: what each custodian
  adapter declares), services/custody/trading.py trade adapters, execution
  instructions, human tasks. rep3: portfolio.models one registry, upload +
  upload log, library, composite sleeves; validation already says "no
  negative weights unless the model is long/short". rep5: accounts join
  models; transition and proposals; maker-checker. rep6: the tax engine
  (tax profiles, carryforwards, lot-level, harvest). lotmethod: lot-relief
  method (household / account / proposal). rep7 / rep7b: held-away, X-ray,
  account authority and custody (read_only / advise / manage). rep9: block
  trading, allocation, DVP, manual-first. rep10: tilts, completion,
  consolidation, gifting, backtest.
- prices1 (merging now): EOD prices through the security-data layer
  (Yahoo chart, internal use only — member display not allowed); tracking
  error and backtests compute when a series exists, else "needs prices".
- bugfix4: every role, Super Admin included, is decided from user_roles
  (rbac.grant_named_role / grant_org_admin); a role STRING alone is not a
  role. Seed fixtures that way.
- The nightly portfolio refresh (~07:00 UTC) and real users write
  audit_log and other tables at any time: count them BY FIXTURE IDS ONLY.
- Regression: this changes proposals, the tax engine's harvest and the cost
  comparison → re-run verify_rep6.py ONCE, flat, after teardown,
  VERIFY_NESTED=1, 2700 s. verify_rep6 is on the known list of verifies that
  (a) count the whole audit_log table and may (b) seed roles by string —
  BEFORE relying on it, fix both in verify_rep6.py the way visibility1 fix 1
  (audit_log by fixture ids) and secmaster2 fix 1 (user_roles grants) did,
  and say what you changed.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. Never print a secret. Decimal everywhere. Bi-temporal
rule 3; RLS for every operation; maker-checker on every approval. Model names
always prefixed "Holdings of" (Joe). No Claude model anywhere in this sprint;
no model call at all. Light / cream UI. Commit ALL sprint files (excluding
sprint_prompts/logs) on the current branch at the end with message "rep13:
<one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. custodian_capabilities: the columns; whether margin / short selling /
      stock borrow are declarable today; what Altruist, Schwab, Fidelity,
      Pershing, IBKR rows declare (no network — what is stored).
  1b. Models: where weights are validated; how a negative weight is refused
      today; the "long/short" exception the rep3 text names — real or
      placeholder.
  1c. Proposals (rep5) and the rebalance engine: how target weights become
      trades; what a SELL of a share not held does today; the cost
      comparison's lines.
  1d. Tax engine (rep6) + lotmethod: how gains are classified; where
      harvest candidates come from; what knows household-level holdings
      (for the constructive-sale block — every account of the household,
      held-away included where its authority allows reading).
  1e. Suitability profile: where a margin agreement / options level would be
      recorded today (or nothing).
  1f. Execution (rep2 / rep9): side vocabulary (buy / sell / short /
      cover?), the manual order sheet, fills.

=== TASK 2: CAPABILITIES AND SUITABILITY ===
custodian_capabilities gains margin, short_sell, stock_borrow (declared per
custodian, platform, Super Admin edits with history); every stored
custodian defaults to NOT declared. An account is long/short-eligible only
when its custodian declares margin AND short_sell, its authority is
'manage' (rep7b), and the member's suitability profile records a margin
agreement (date, document link) — a new, audited field, maker-checker on
the record. Each refusal names the missing piece.

=== TASK 3: LONG/SHORT MODELS ===
A model declares kind long_short with gross long / gross short (130/30 or
150/50, or a custom pair within limits) and net 100 %. Upload / edit
accepts negative weights only on such a model; validation: longs sum to the
gross long, shorts to the gross short (± tolerance), no name both long and
short, every short name a listed equity in the master, exclusions honoured.
Upload log and library show the kind. The signal source is recorded on the
model (sub-adviser name, as-of of the weights). Names "Holdings of …".

=== TASK 4: ACCOUNT SETTINGS ===
Per account (on an eligible account only): extension level, tracking-error
limit, maximum short per name (% of capital), borrow-cost ceiling (bps /
year), exclusions (names never shorted). Draft → saved, audited, history.

=== TASK 5: PROPOSALS ===
The proposal engine builds both books toward the long/short model within the
account's settings: shorts are SELL SHORT orders (and COVER to close), never
a sale of shares not held under a long-only account. Every short line
carries an estimated borrow fee (from an entered borrow-rate table: Super
Admin / org-entered rates per security with as-of — no live borrow feed
exists; a name without a rate is "borrow rate needed" and blocks approval)
and the short rebate where entered. A name over the borrow-cost ceiling is
skipped with the reason. Tracking error vs the model is computed when
prices exist (prices1), else "needs prices" — never invented. The cost
comparison gains "Borrow cost (long/short only)" and "Short rebate".

=== TASK 6: TAX ===
Short positions' gains and losses are SHORT-TERM regardless of holding
period (state the rule and cite the code section in the doc). Harvest
candidates come from BOTH books (a short with an unrealised loss is closed
and re-established in a substitute name after the wash-sale check rep6
already applies). HARD BLOCK (§1259 constructive sale): no proposal may
short a security — or one the engine treats as substantially identical
(same issuer's share classes; state the rule) — that ANY account of the
household holds long at an unrealised GAIN, held-away accounts included when
their positions are readable; the block names the account and lot; it can
never be overridden in this sprint. The X-ray / tax estimate shows the
long/short account's estimated tax benefit with "estimate".

=== TASK 7: DISCLOSURES AND APPROVAL ===
Every long/short proposal shows the spec's six risks, the borrow-cost
estimate, the margin requirement (initial / maintenance as declared by the
custodian, else "custodian to confirm") and a member-facing summary; the
adviser must acknowledge them before submit; the checker (a different
person) approves; an approval is refused if any eligibility piece (Task 2)
lapsed since the proposal was built.

=== TASK 8: EXECUTION ===
Side vocabulary gains short and cover end to end (instructions, blocks,
allocation, fills, manual order sheet — "SELL SHORT" / "BUY TO COVER"
printed plainly); a custodian adapter that does not declare short_sell
refuses a short instruction at submit; the manual route is always offered.
Fills update the short position (negative quantity) and its lots.

=== TASK 9: REAL PROOF ===
  - Capabilities: an undeclared custodian refuses; a fixture custodian
    declaring margin + short_sell + borrow allows; suitability without a
    margin agreement refuses; authority read_only / advise refuses.
  - Models: a 130/30 fixture uploads; a long-only model with a negative
    weight is refused; gross mismatch, a name both long and short, an
    excluded short, a non-equity short — each refused with its reason.
  - Proposal: a fixture account on the 130/30 model produces the exact
    hand-computed long and short trades; borrow cost per line and in the
    comparison equals the hand computation; a name over the ceiling is
    skipped; a name with no borrow rate blocks approval; tracking error says
    "needs prices" without a series and computes the hand value with a
    fixture series.
  - Tax: a short closed at a gain after 400 days is short-term; a short with
    a loss is a harvest candidate and its re-establishment respects the wash
    rule; the §1259 block fires when another household account (including a
    readable held-away one) holds the same security — and a second share
    class of the same issuer — at a gain, names it, and cannot be bypassed;
    the same name held at a LOSS does not block.
  - Disclosures shown; submit refused without acknowledgement; maker cannot
    approve; approval refused after the margin agreement is removed.
  - Execution: short and cover through the manual order sheet and a fixture
    adapter; a non-declaring adapter refuses at submit; fills give a
    negative position with lots.
  - No model call, no network call (stubs at CURRENT signatures; assert).
  - Org isolation both directions (rolbypassrls = false); real and demo-olivo
    rows unchanged (counted by fixture ids where real activity writes).
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_rep6.py ONCE, flat, after teardown, VERIFY_NESTED=1,
    2700 s (after its audit_log and role-seeding fixes).

=== TASK 10: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md and docs/REPLICATION_STUDIO.md (rep13 section: rules,
settings, the §1259 rule, borrow-rate entry, what no custodian supports yet);
the open decisions for Joe (signal source — sub-adviser weights assumed;
which custodian first; default limits for 2nd Act's members; overlay bps);
add verify_rep13.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep13.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
(assert it). Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id; re-read every
touched table for its exact pre-test count, counting audit_log and any table
real activity writes to BY FIXTURE IDS ONLY. Fixture orgs and users is_test =
true (assert it); fixture roles granted through user_roles (assert the app
decides each as intended). Read UNIQUE / CHECK / FK constraints and unique
indexes from the catalog before seeding (FAIL if none are read). Reserved
fixture prefixes that match no real row (assert it). Support
--self-checks-only and --cleanup-only. The regression subprocess gets
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip it with
one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read (the same org context the route sets);
    rolbypassrls = false asserted; an empty list never passes a check.
  - A refusal check sends a VALID body, so only the gate can refuse.
  - A recursive CTE has ONE anchor and ONE recursive term.
  - A test stub must match the CURRENT signature of what it replaces; every
    name a reader calls through a package must be one the package exports.
  - Never seed a state the database forbids (read the unique indexes first).
  - Key a real table's fingerprint by its real key; exclude only columns
    real activity updates, and name them.
  - No check frozen against live state.
  - Parse a nested script's OWN final ^TOTAL line (shared helper).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1f
  [Y] Capabilities + suitability + authority gate long/short, each refusal named
  [Y] Long/short models validated; long-only negative refused
  [Y] Account settings versioned
  [Y] Proposals: both books exact; borrow cost; ceiling; rate needed; TE
  [Y] Tax: short-term rule; two-sided harvest + wash; §1259 hard block
  [Y] Disclosures, acknowledgement, maker-checker, lapsed eligibility
  [Y] Execution: short / cover, manual sheet, non-declaring adapter refuses
  [Y] No model / network call; isolation; own routes; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rep6.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
