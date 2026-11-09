CASH03 — the OBLIGATION-MATCHED LADDER and AFTER-TAX INSTRUMENT SELECTION:
for an active cash policy (cash02), build the Long-term ladder (rungs on the
dates of known large obligations + monthly filler rungs, rung count from the
policy) and choose, per sleeve and per ACCOUNT, the instrument that is best
AFTER TAX for that account's owner (Treasury vs muni vs taxable MMF / bills /
notes; state-tax exemption; IRA / trust treatment). Adds two free data
providers through the market-data registry: Treasury Fiscal Data (T-bill /
note CUSIPs, maturities, auction results) and SEC N-MFP (money-market fund
7-day yields) through the EDGAR pipeline. Still read-only on money: this
produces a TARGET LADDER and an instrument plan — proposals and orders are
cash04. 9 tasks + verification. Branch cash03-work.

Design: docs/cash/ (CASH_TREASURY_DESIGN_v1 §2.3 ladder, §2.6 after-tax,
§3.4 holdings, §5, §6 data sources, §7; CASH_TRACK_PLAN_v1.2) +
CASH00_DISCOVERY.md, CASH_OBLIGATIONS.md (cash01), CASH02_NOTES.md,
CASH_POLICY.md (cash02) — READ THEM FIRST; earlier decisions bind. DECIDED
(Joe, 2026-11-08): use the app's existing constructs; new ones only where
none can carry it (named and justified in docs/cash/CASH03_NOTES.md). No
vendor lock-in: every source through the provider registry with licence
facts.

THE CONCEPTS (from v1, do not re-decide):
- Ladder: rungs sit on the dates of known LARGE obligations in the Long-term
  window (threshold documented — default: ≥ 10% of the Long-term sleeve or
  a firm obligation of any size ≥ $250k), sized to cover them; monthly
  filler rungs between, up to the policy's rung count; leftover sleeve $
  spread across fillers. Each rung: maturity (≤ the obligation date, on a
  real issue's maturity where one exists — business-day aware), instrument
  type (bill / note / muni), par, the obligation it targets (or "filler").
  Deterministic; Decimal.
- After-tax selection per account: inputs = the account's tax treatment
  (taxable / IRA / Roth / trust / foundation …, from the existing account
  data) and the owner's approved tax profile (services/tax_profiles.py
  profile_for_account / combined_rates, rep6). Treasuries and Treasury funds
  are exempt from state and local tax; munis exempt federally (and in-state);
  taxable instruments preferred where tax is irrelevant (IRA, foundation).
  After-tax yield = yield × (1 − applicable rates by exemption). No profile
  → net yield only, labelled "after-tax: needs a tax profile". Roadmap fact
  kept as a test case: T-bills beat CDs after tax for NY-domiciled taxable
  members (~133 bp hurdle at the profile's rates).
- Yields carry their BASIS and as-of: MMF 7-day SEC yield (N-MFP), bills /
  notes yield to maturity (Treasury Fiscal Data auction / FRED curve
  interpolation, labelled), muni yields: none free — show "muni yield: needs
  a source" unless a registered provider supplies one (never invented).
- Instruments resolve to the security master (secmaster: CUSIP / symbol);
  an instrument not in the master is added through the existing security
  master load path, never a side table.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo 2026-11-09; Task 1 confirms
signatures):
- services/market_data/: registry.py (providers + licence facts), fred.py,
  yahoo.py, ingest.py, nightly.py (the nightly job), read_service.py /
  read_repository.py, staleness.py, key_dates.py. New providers follow
  fred.py's shape and register in registry.py.
- EDGAR pipeline: services/edgar_fetch.py, edgar_index.py, edgar_pipeline.py
  (+ _admin, _bulk, _queue, _cohorts, _inventory, _index_schedule). N-MFP
  ingestion goes through it (form type N-MFP2 / N-MFP3), not a new fetcher.
- Security master: services/security_master_load.py / _sync.py / _symbols.py
  / _jobs.py / _summary.py.
- services/tax_profiles.py: approved_profile, profile_for_account,
  combined_rates, rates_out (rep6).
- cash01 + cash02 merged: cash_obligations, cash_coverage, cash_policies,
  services/cash_policy.py, the Household → Treasury tab. Extend; do not
  duplicate.
- No network in the sprint: providers are built and tested against recorded
  fixture payloads (stored under tests/fixtures/), and the first real fetch
  is an OPERATOR step (Task 7).
- Demo rows (demo-olivo) are permanent: the sprint writes NO real or demo
  row. bugfix4: roles from user_roles only. Count audit_log and
  live-written tables BY FIXTURE IDS ONLY.
- Regression: verify_cash02.py ONCE, flat, after teardown, VERIFY_NESTED=1,
  2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. No model call. Never print a secret. Bi-temporal rule
3; RLS for every operation (org_id server-side only; RLS policy in the same
migration as each new table). Light / cream UI. Commit ALL sprint files
(excluding sprint_prompts/logs) on the current branch at the end with message
"cash03: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately. Write
docs/cash/CASH03_NOTES.md: the provider registration shape (fred.py);
whether the EDGAR pipeline already knows N-MFP and what an N-MFP filing
yields (7-day yield, WAM, fund CUSIP / series id → security master link);
the Treasury Fiscal Data endpoints needed (auctions / securities) and their
licence (public domain); how account tax treatment is stored today; the
security master add path for bills / notes / MMFs; reuse inventory.

=== TASK 2: PROVIDERS ===
services/market_data/treasury_fiscal.py and an N-MFP extractor on the EDGAR
pipeline; both registered in registry.py with licence facts (US Government
public domain; SEC public), staleness rules, and their nightly / monthly
cadence wired to the existing nightly job but SHIPPED SWITCHED OFF (operator
turns them on). Parsed rows land in the existing market-data stores (or one
new table per source if none fits — justified in 1).

=== TASK 3: LADDER ===
services/cash_ladder.py — pure: (active policy, obligation schedule,
available issues) → rungs (maturity, type, par, target obligation | filler),
with the threshold rule, business-day handling, fallback to the FRED curve
when no matching issue exists (labelled "indicative maturity"). A ladder is
a DERIVED plan for a policy version; a stored snapshot only when the plan is
approved for cash04 (decide in Task 1; if stored: public.cash_ladder_rungs,
bi-temporal, RLS, linked to the policy version).

=== TASK 4: AFTER-TAX SELECTION ===
services/cash_instruments.py — per sleeve × account: candidate instruments
(gov / Treasury MMF, muni MMF, bills, notes, muni where a yield source
exists), net yield with basis + as-of, after-tax yield by the owner's
profile and exemptions, the chosen instrument and WHY (one sentence), the
runner-up and the bp difference. Blended net and after-tax yield per sleeve
and household (Σ value × yield ÷ total). Replaces cash02's FRED-proxy
estimate wherever a real instrument yield exists (cash02's labels stay for
the rest).

=== TASK 5: SCREENS ===
Household → Treasury tab: Ladder view (timeline of rungs against the
obligations they cover; filler vs targeted; indicative maturities marked),
Instrument plan per sleeve × account (chosen, why, net / after-tax yield,
basis, as-of, runner-up), blended yields on the policy editor. Member view:
their family's ladder and plan, read-only, labels from the disclosure
library (cash.yield_estimate; a new key cash.tax_estimate, default text
"after-tax figures use your tax profile and are estimates; have counsel
review"); never another family.

=== TASK 6: PERMISSIONS / AUDIT ===
Reuse cash02's cash-policy permissions to view / regenerate the plan;
providers' switch-on through the existing provider admin (Super Admin);
every regeneration audited.

=== TASK 7: OPERATOR STEPS ===
scripts/load_cash_market_data.py --dry-run | --real: one fetch of Treasury
Fiscal Data and the latest N-MFP filings for the funds the plan uses (the
sprint runs neither — no network; it runs the parser against fixtures).
scripts/load_olivo_ladder.py --dry-run: the demo family's ladder and
instrument plan from its active (or draft) policy — the sprint runs ONLY
--dry-run and records what it would show.

=== TASK 8: REAL PROOF ===
  - Providers: fixture payloads parse to the expected rows (CUSIP, maturity,
    auction high yield; MMF 7-day yield, as-of); bad / partial payloads are
    rejected with a reason; registry rows carry licence facts; switched off
    by default; staleness rules fire.
  - Ladder: hand-computed rungs for a fixture schedule (large obligations
    get targeted rungs; fillers monthly; rung count respected; a holiday
    maturity moves to the prior business day; no matching issue → indicative
    and labelled); Decimal as Decimal.
  - After-tax: CT / NY taxable owner → Treasury preferred over taxable MMF
    at equal yield; IRA → highest net yield regardless of exemption; no
    profile → net only, labelled; the ~133 bp T-bill vs CD hurdle reproduced
    for a NY fixture profile; muni without a yield source → "needs a
    source", never chosen.
  - Instruments resolve to the security master; none created outside its
    load path.
  - Screens: node render tests (ladder, plan, member read-only labels).
  - Member sees only their family; staff only visible families; org
    isolation both directions (rolbypassrls = false); real and demo rows
    unchanged; loaders --dry-run write nothing; no network call (asserted).
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_cash02.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (cash03), docs/cash/CASH_LADDER.md (ladder rules,
after-tax method, yield bases, providers and cadence), docs/DATA_PROVIDERS
(or the registry doc) with the two new sources, operator steps (switch the
providers on; load_cash_market_data --dry-run → --real; Olivo ladder
dry-run); add verify_cash03.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_cash03.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure (TOTAL printed on every
exit path, with the traceback). Set AUTHZ_TEST_MODE=1 before importing the
app. Hydrate secrets from Doppler over HTTPS. No network (assert it).
Fixture orgs (privileged insert path, is_test = true — assert it); time
injected ("today" fixed); market data from fixture payloads / rows only.
Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id (every row a
service creates on the verify's behalf, by the id it returns; tables without
org_id — market data, security master — passed in the cleanup plan by
fixture CUSIP / series prefixes that match no real row, asserted); re-read
every touched table for its exact pre-test count, counting audit_log and
live-written tables BY FIXTURE IDS ONLY. Roles via user_roles (assert the
app decides each). Read UNIQUE / CHECK / FK constraints and unique indexes
from the catalog before seeding (FAIL if none are read). Every INSERT's
columns checked against docs/schema_snapshot.sql. Support --self-checks-only
and --cleanup-only. The regression subprocess gets VERIFY_NESTED=1, after
teardown, 2700 s; with VERIFY_NESTED=1 skip it with one '[SKIP] nested run'
line. If the seed fails, every fixture section is a [FAIL], never skipped
silently.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool (wrap
    and-chains in bool()).
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context set ON the connection inside its transaction (a contextvar
    alone is not an RLS context); rolbypassrls = false asserted; an empty
    list never passes a check.
  - Every query's placeholders match its arguments; a parameter used in two
    contexts gets an explicit cast.
  - Compare Decimals as Decimals, never as text.
  - A refusal check sends a VALID body, so only the gate can refuse.
  - Each fixture file has distinct content.
  - Count your own node tests by their file, not by title prefixes.
  - A test stub must match the CURRENT signature of what it replaces.
  - Never seed a state the database forbids (read the unique indexes first).
  - No check frozen against live state; static markers match by name.
  - Parse a nested script's OWN final ^TOTAL line (shared helper).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 notes + reuse inventory; earlier cash decisions honoured
  [Y] Two providers registered with licence facts, switched off, parsed
      from fixtures, staleness
  [Y] Ladder rules hand-computed (targeted + filler, business days,
      indicative fallback)
  [Y] After-tax selection by account treatment + profile; labels where data
      is missing; the 133 bp case
  [Y] Instruments in the security master only
  [Y] Screens + member read-only with library labels; loaders dry run
  [Y] Visibility, isolation, own routes, --check CURRENT, real + demo rows
      unchanged, no network
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_cash02.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
