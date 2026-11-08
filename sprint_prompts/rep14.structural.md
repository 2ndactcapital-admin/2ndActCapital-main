REP14 — Replication Studio phase 4: the SECTION 351 EXIT. When harvesting
has run dry, appreciated replica accounts can be pooled into a new ETF
without a taxable sale. This sprint builds the PLANNING side end to end — who
is a candidate, whether each account passes the diversification test (and
the fix-up proposal if not), the pool's control test, lot-level carryover
basis records, the consent and project tracker, and the in-kind transfer
instructions — all manual-first, nothing executed automatically. 10 tasks +
verification. Branch rep14-work.

Spec: "Replication Studio — ETF & Fund-to-SMA Spec" (Joe, 2026-10-22),
feature table row "Section 351 exit" and the phase plan ("rep14 Section 351
exit"); source: SEI — SMA-to-ETF Section 351 conversions.

FROM THE SPEC (Joe's design, do not re-decide):
- "When harvesting has run dry, pools appreciated replica accounts into a
  new ETF without a taxable sale. Each account passes the 25% / 50%
  diversification test; contributors own at least 80% of the ETF afterward;
  lot-level basis required; 6–9 months; one-time event." Phase 4.
- Tax lots from the custodian are REQUIRED ("average cost is not enough for
  harvesting or a 351 exchange").
- Guardrails (apply here): tax inputs come from the adviser, recorded with a
  date — the tool never guesses a bracket; every tax figure is labelled an
  estimate and the member's tax adviser confirms; benefit decay shown next
  to year 1.
- DECIDED (Joe, 2026-11-07 — best practice from Alpha Architect / ETF
  Architect's 351 Education Center and Cambria's Section 351 program):
  * Sponsor: a third-party ETF sponsor runs the new fund (2nd Act is not an
    ETF issuer). A plan names its sponsor from a SPONSOR PROFILE table
    (name, adviser / distributor, contact, launch windows, and its
    eligibility rules below), editable by org admin, versioned.
  * 2nd Act's role: the members' adviser — it identifies candidates, runs
    the tests, collects consents and basis records, and instructs the
    custodian; the sponsor runs the fund and its own final compliance review.
  * Eligibility rules (defaults on the sponsor profile, editable): U.S.
    persons only; individual, joint, trust and S-corp accounts eligible;
    partnerships / LLCs "needs sponsor review"; C-corps and non-U.S. not
    eligible; ineligible assets: restricted or illiquid securities,
    derivatives (options legs, shorts), crypto, physical commodities,
    commodity pools, grantor trusts, partnerships; cash per the sponsor's
    rule (default: not contributed, stays in the account).
  * ETF holdings are LOOKED THROUGH for the 25 % / 50 % test (the
    constituents' weights count, including positions held indirectly), as
    the sponsors apply it.
  * Disclosures come from the DISCLOSURE LIBRARY (disclosures1, merged
    before this sprint): key section351.exit, resolved for the account,
    acknowledged by version before the plan can be "ready" — never text in
    code.
THE RULES TO IMPLEMENT (state each in the doc with its citation; an
"estimate / confirm with counsel" label on every result):
- Investment-company exception: a transfer to an investment company is
  tax-free under §351 only if EACH transferor's contributed portfolio is
  already diversified — IRC §351(e)(1), Treas. Reg. §1.351-1(c)(6) applying
  §368(a)(2)(F)(ii): no more than 25 % of the value of the contributed assets
  in any one issuer, and no more than 50 % in five or fewer issuers
  (government securities excluded from the issuer count as the regulation
  provides; cash per the sponsor profile; ETF holdings looked through to
  their constituents).
- Control: the transferors together own at least 80 % of the new ETF
  immediately after the exchange (§351(a) / §368(c)).
- Carryover basis and tacked holding period per lot (§358 for the
  contributor's shares, §362 / §1223 for the fund) — so every contributed
  lot's basis and acquisition date are carried into the basis record.
- Boot (cash or other property received) is taxable — the plan receives
  ETF shares only; any cash leg is shown as taxable.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (Task 1 confirms signatures):
- rep1–rep13 built (docs/REPLICATION_STUDIO.md): models, accounts joined to
  models, proposals with maker-checker, the rep6 tax engine (lots, harvest,
  carryforwards, benefit decay), lotmethod, held-away + X-ray, authority /
  custody (rep7b), options sleeve (rep8), block trading / DVP manual-first
  (rep9), tilts / consolidation / gifting / backtest (rep10), notes
  (rep11–12), long/short with the §1259 constructive-sale block (rep13).
  Human tasks and execution instructions exist (rep2); the manual order /
  instruction sheet is always offered (Joe, 2026-10-28).
- prices1: EOD prices for the coverage set (Yahoo, internal use only — not
  for member display); valuations use the latest price where a series
  exists, else the entered value, always labelled.
- bugfix4: roles from user_roles only (rbac.grant_named_role /
  grant_org_admin). The nightly refresh (~07:00 UTC) and real users write
  audit_log etc.: count live-written tables BY FIXTURE IDS ONLY.
- disclosures1 (merged before this sprint) provides the disclosure library
  (resolver + acknowledgement by version, files in R2 per org / household /
  account).
- Regression: this reads the tax engine, lots, proposals and the
  long/short block → re-run verify_rep13.py ONCE, flat, after teardown,
  VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. Never print a secret. Decimal everywhere. Bi-temporal
rule 3; RLS for every operation; maker-checker on every approval. No model
call anywhere. Never write a real or demo-olivo row. Light / cream UI. Commit
ALL sprint files (excluding sprint_prompts/logs) on the current branch at the
end with message "rep14: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. Lots: where lot-level basis and acquisition dates live; which accounts
      have lot-level data vs average cost only (an average-cost account is
      NOT eligible — say how many such accounts exist, read-only).
  1b. "Harvesting has run dry": what rep6 already measures (harvestable
      loss remaining, benefit decay, years since inception) that can signal
      a candidate.
  1c. Issuer identity for the 25 / 50 test: how the master links a security
      to its issuer (CIK / LEI / issuer entity; share classes of one issuer
      grouped); government securities and cash identification; ETF
      constituents for the look-through (rep1 fund holdings / N-PORT, and
      their as-of).
  1f. The disclosure library (disclosures1): the resolver and the
      acknowledgement check to call.
  1d. Proposals: how a rebalance proposal can be built toward a constraint
      (rather than a model) for the "fix to pass the test" proposal.
  1e. Human tasks / workflow engine for a multi-month project tracker;
      document storage (Chancery) for the basis record and consents.

=== TASK 2: CANDIDATES ===
A candidate view (Replication → Section 351): every replica account with
lot-level data, showing appreciated value, unrealised gain, harvestable loss
remaining and the benefit-decay estimate, with a "harvesting has run dry"
flag by a stated rule (thresholds editable per org, versioned). Accounts with
average cost only, held-away accounts, or authority other than 'manage' are
listed as ineligible with the reason.

=== TASK 3: THE DIVERSIFICATION TEST ===
services/section351.py: for a proposed contribution (an account, or a set of
its lots, at a valuation date), compute per issuer the share of contributed
value; PASS when no issuer > 25 % AND the five largest ≤ 50 % (with the
regulation's treatment of government securities and cash as you state it).
ETF holdings are looked through to their constituents (with the holdings'
as-of shown; a fund with no holdings data FAILS closed with "holdings
needed"); ineligible assets per the sponsor profile are listed and excluded
from the contribution. Output: every issuer's weight, the top five, the
margin to each limit, the valuation source per line (price / entered value,
as-of), and the verdict.
Pure function + one read; Decimal; deterministic.

=== TASK 4: FIX-UP PROPOSAL ===
If an account fails, an optional proposal that makes it pass with the least
estimated tax (sell the lots costing least tax per dollar from the
over-weight issuers via the rep6 engine and lot method; buy diversifying
names from the account's model), showing the tax cost of the fix as an
estimate. It goes through the normal proposal maker-checker; nothing is
executed by this sprint's code paths automatically.

=== TASK 5: THE POOL ===
A 351 plan (org-scoped, versioned): name, target ETF (sponsor / trust
recorded as text — open question), valuation date, contributing accounts
(each with its test result at that date), outside seed capital if any.
Control test: contributors' share of the ETF immediately after ≥ 80 %
(contributed value ÷ total value including any non-contributor seed);
PASS / FAIL with the numbers. Any cash to contributors is shown as boot
(taxable). The plan cannot move to "ready" unless every account passes and
control passes.

=== TASK 6: BASIS RECORD ===
Eligible owners per the sponsor profile (individual / joint / trust /
S-corp; partnership "needs sponsor review"; others refused with the reason).
Per contributor: every contributed lot — security, quantity, basis,
acquisition date, holding period — and the resulting ETF-share basis
(carryover) with tacked holding period; a CSV / PDF export for the ETF's
transfer agent, stored in the document vault, versioned; totals reconcile to
the plan to the cent.

=== TASK 7: CONSENT AND PROJECT TRACKER ===
Each member's written consent (document link, date, signer), the member's
tax adviser confirmation (recorded, not inferred) and the acknowledgement of
the in-force section351.exit disclosure version are required per account
before "ready". A project tracker (human tasks on the workflow
engine) with the standard steps: counsel engaged, sponsor / trust selected,
custodian confirms in-kind transfer, valuation date set, consents, basis
record delivered, transfer, confirmation — dates, owners, status; a 6–9
month timeline view.

=== TASK 8: IN-KIND TRANSFER INSTRUCTIONS ===
When the plan is approved (maker-checker), it produces execution
instructions of a new kind "in-kind transfer out" per account and lot (to the
ETF's custodian account, recorded as text), with the manual instruction
sheet; a custodian adapter that does not declare in-kind transfer refuses at
submit (manual route always offered). No trade, no sale. After confirmation,
the account's lots are closed by transfer (not sale — no realised gain) and
the ETF shares recorded with the carryover basis.

=== TASK 9: REAL PROOF ===
  - Test: fixture contributions that pass exactly at 25.00 % / 50.00 %,
    fail at 25.01 % one issuer, fail at 50.01 % top five, two share classes
    of one issuer counted as one, a government security excluded per the
    stated rule, an ETF looked through so a name held directly AND inside
    the ETF is summed, a fund with no holdings data fails closed, an
    ineligible asset (an option leg, crypto) excluded and listed — each
    hand-computed.
  - Sponsor profile: eligible / review / refused owner types; editing is a
    new version; a plan records the profile version it used.
  - Disclosure: "ready" refused until the in-force section351.exit version
    is acknowledged; a newly published version requires re-acknowledgement.
  - Eligibility: average-cost, held-away and non-'manage' accounts are
    ineligible with their reasons; the "run dry" flag follows its thresholds.
  - Fix-up: a failing fixture account gets a proposal that passes the test
    after the hand-computed sales; its tax estimate equals the rep6 engine's;
    maker cannot approve.
  - Pool: control 80.00 % passes, 79.99 % fails; boot shown taxable; "ready"
    refused while any account fails, any consent or tax-adviser confirmation
    is missing.
  - Basis record: lots, carryover basis and tacked dates exact; totals to
    the cent; export stored and versioned.
  - Transfer: approval produces the in-kind instructions; a non-declaring
    adapter refuses; after confirmation the lots close by transfer with NO
    realised gain and the ETF position carries the basis.
  - No model / network call (stubs at CURRENT signatures; assert).
  - Org isolation both directions (rolbypassrls = false); real and demo rows
    unchanged (counted by fixture ids where real activity writes).
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_rep13.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 10: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md and docs/REPLICATION_STUDIO.md (rep14 section: the
rules with citations and your readings flagged for counsel, the eligibility
rules, the operator flow); the remaining items for counsel to review (the
default disclosure text; fees); add verify_rep14.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep14.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure (and print TOTAL on
every exit path, including a seeding exception). Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
(assert it). Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id; re-read every
touched table for its exact pre-test count, counting audit_log and any table
real activity writes to BY FIXTURE IDS ONLY. Fixture orgs and users is_test =
true (assert it), orgs inserted through the privileged path current verifies
use; fixture roles granted through user_roles (assert the app decides each as
intended). Read UNIQUE / CHECK / FK constraints and unique indexes from the
catalog before seeding (FAIL if none are read). Reserved fixture prefixes
that match no real row (assert it). Support --self-checks-only and
--cleanup-only. The regression subprocess gets VERIFY_NESTED=1, after
teardown, 2700 s; with VERIFY_NESTED=1 skip it with one '[SKIP] nested run'
line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read — the same org context the route sets, set ON
    the connection inside its transaction (a Python contextvar alone is not
    an RLS context); rolbypassrls = false asserted; an empty list never
    passes a check.
  - Compare Decimals as Decimals, never as text (s28 fix 1).
  - A refusal check sends a VALID body, so only the gate can refuse.
  - Each fixture upload / file has distinct content.
  - Count your own node tests by their file, not by title prefixes.
  - A test stub must match the CURRENT signature of what it replaces.
  - Never seed a state the database forbids (read the unique indexes first).
  - No check frozen against live state.
  - Parse a nested script's OWN final ^TOTAL line (shared helper).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1e
  [Y] Candidates and eligibility with reasons; "run dry" thresholds versioned
  [Y] 25 / 50 test exact at the edges; issuer grouping; government rule
  [Y] Fix-up proposal passes, tax estimate from rep6, maker-checker
  [Y] Pool control test at the edge; boot taxable; "ready" gates
  [Y] Basis record exact, exported, versioned
  [Y] Consent + tax-adviser confirmation + tracker
  [Y] In-kind transfer instructions; no realised gain; carryover on the ETF
  [Y] No model / network call; isolation; own routes; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rep13.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
