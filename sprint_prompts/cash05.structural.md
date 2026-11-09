CASH05 — the TREASURY OVERVIEW and what a family sees day to day: balance
over time with transfer markers, blended net and after-tax yield, the
coverage card, HOLDINGS per sleeve (every ladder rung), an ACTIVITY ledger
(transfers, dividends, interest, maturities, fees, credits — each with a
detail drawer), an EARNINGS breakdown (dividends + ladder interest + cash
interest − advisory fee = total) and DOCUMENTS (statements, confirmations,
1099s via Chancery, dates in Eastern time). Read-only: no new writes to
money. 9 tasks + verification. Branch cash05-work.

Design: docs/cash/ (CASH_TREASURY_DESIGN_v1 §3.1, §3.3, §3.4, §3.8, §5
earnings / blended yield, §9.2 fee) + every CASH0x_NOTES and design doc
written by cash01–cash04 — READ THEM FIRST; earlier decisions bind. DECIDED
(Joe, 2026-11-08): use the app's existing constructs; a new one only where
none can carry it (named and justified in docs/cash/CASH05_NOTES.md). This
sprint is mostly READS over existing data — expect no new table; any new
one needs a stated reason.

THE CONCEPTS (from v1, do not re-decide):
- Overview (per household, filter per entity): balance-over-time chart
  (30d / 90d / 6m / YTD / 12m / all) of the liquid balance (cash01's
  definition: Cash & Equivalents through the net-worth core + the sleeves),
  transfer markers, tooltip (value, pending in, pending out); blended net
  yield with a method note (Σ value × yield ÷ total − advisory fee, each
  holding on its own basis — cash03); after-tax where profiles exist;
  cash01's coverage card and per-entity table; actions (documents, edit
  policy, proposed trades — links to existing screens).
- Holdings per sleeve: instrument, plain-language description, yield with
  basis and as-of, liquidity wording with cut-off ("same day if instructed
  by <cut-off> ET"), shares, value, months of obligations covered, prospectus
  link (where the security master holds one); ladder rows expand to every
  rung (maturity, type, par vs market value, YTM, the obligation it serves).
- Activity ledger from existing positions / transactions
  (services/portfolio_transactions.py) for the cash / sleeve / ladder
  holdings: date, description, change, pending marker with settlement
  guidance, type (transfer, dividend, interest, maturity, fee, credit); a
  DETAIL DRAWER per entry (source record, account, security, settlement
  date, linked instruction / proposal / document) — Mercury's demo had dead
  links; ours must not; clear empty state.
- Earnings (period selectable): dividends per fund + ladder interest (coupon
  + accreted discount, method stated) + cash interest − advisory fee (from
  the existing fee engine: services/fee_runs.py / fee_calc.py — the accrued
  share attributable to cash sleeves if the fee schedule charges on cash;
  "fee on cash: not charged" if it does not) = total. Reconciles to the
  activity ledger to the cent.
- Documents: statements, confirmations, 1099s from Chancery
  (services/portfolio_documents.py / portfolio_chancery.py / document_linkage)
  filtered to the cash accounts; all dates shown in America/New_York.
- All money Decimal; every figure with source and as-of; missing data reads
  "not estimated — needs <x>".

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo 2026-11-09; Task 1 confirms
signatures):
- services/networth.py (rollup2 core), portfolio_positions.py,
  portfolio_transactions.py, portfolio_cash.py, portfolio_refresh.py
  (history), portfolio_securities.py; fee_runs.py, fee_calc.py,
  fee_schedules.py; portfolio_documents.py, portfolio_chancery.py,
  document_linkage.py, chancery_intake.py.
- cash01–cash04 merged: obligations, coverage, policies, ladder, instrument
  plan, cash proposals / waterfall, the Household → Treasury tab. Extend
  that tab; do not add a second one.
- Demo rows (demo-olivo) are permanent: the sprint writes NO real or demo
  row. bugfix4: roles from user_roles only. Count audit_log and
  live-written tables BY FIXTURE IDS ONLY.
- Regression: verify_cash04.py ONCE, flat, after teardown, VERIFY_NESTED=1,
  2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. No model call. Never print a secret. Never print a
full account number (last four only). Bi-temporal rule 3; RLS for every
operation. Light / cream UI. Commit ALL sprint files (excluding
sprint_prompts/logs) on the current branch at the end with message "cash05:
<one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately. Write
docs/cash/CASH05_NOTES.md: where a liquid-balance HISTORY comes from
(position / valuation history per day — or whether a daily series must be
derived), transaction types available and their mapping to the six ledger
types, how pending vs settled is known, coupon / discount data for ladder
interest, the fee engine's read for "accrued fee attributable to cash",
the document types available per account, prospectus links in the master;
reuse inventory.

=== TASK 2: READ SERVICES ===
services/cash_overview.py (balance series by range, markers, blended
yields, coverage passthrough), cash_holdings.py (per sleeve + rungs),
cash_activity.py (ledger + drawer detail), cash_earnings.py (period
breakdown reconciling to activity). Pure where possible; one read per
screen; Decimal; deterministic for a fixed "today".

=== TASK 3: OVERVIEW SCREEN ===
Treasury tab → Overview panel: chart (light / cream, the app's chart
library), range chips, entity filter, markers + tooltip, yields with method
note, coverage card, actions.

=== TASK 4: HOLDINGS SCREEN === per sleeve with expandable ladder.
=== TASK 5: ACTIVITY + EARNINGS === ledger with filters, pending guidance,
drawer (every link resolves — tested), empty state; earnings dialog.
=== TASK 6: DOCUMENTS === filtered Chancery list, Eastern-time dates,
open / download through the existing document routes (no new storage).

=== TASK 7: MEMBER VIEW ===
Members see their family's overview, holdings, activity, earnings and
documents read-only, with labels from the disclosure library
(cash.coverage_estimate, cash.yield_estimate, cash.tax_estimate; a new
cash.sweep_rate key reserved for cash07 is NOT shown yet); never another
family (visibility1 / bugfix2); staff only visible families.

=== TASK 8: REAL PROOF ===
  - Balance series: hand-computed for a fixture household over 90 days
    (incl. a transfer marker, a pending item, a range boundary); equals the
    net-worth core's Cash & Equivalents + sleeves on every sampled day
    (independent SQL).
  - Holdings: sleeve totals equal the overview; rungs list par vs market
    and YTM with basis; liquidity wording per instrument type.
  - Activity: every fixture transaction maps to exactly one ledger type;
    pending vs settled correct; every drawer link resolves (200, right
    record); empty state for an account with none.
  - Earnings: dividends + interest + cash interest − fee = total, and equals
    the activity ledger for the period to the cent (Decimal as Decimal);
    "fee on cash: not charged" when the schedule excludes cash.
  - Documents: only the cash accounts' documents; dates in Eastern time
    (a UTC-midnight fixture shows the prior day ET).
  - Screens: node render tests (overview, holdings, activity + drawer,
    earnings, documents, member read-only with labels).
  - Member sees only their family; staff only visible families; org
    isolation both directions (rolbypassrls = false); no full account
    number in any response (asserted); real and demo rows unchanged.
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_cash04.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (cash05), docs/cash/CASH_TREASURY_SCREENS.md
(sources and formulas per screen, ledger type mapping, earnings method);
add verify_cash05.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_cash05.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure (TOTAL printed on every
exit path, with the traceback). Set AUTHZ_TEST_MODE=1 before importing the
app. Hydrate secrets from Doppler over HTTPS. No network (assert it).
Fixture orgs (privileged insert path, is_test = true — assert it); time
injected ("today" fixed). Wrap every section so an exception is a [FAIL];
teardown via scripts/_verify_cleanup.py at start and end, by fixture id
(every row a service creates on the verify's behalf, by the id it returns;
tables without org_id passed in the cleanup plan; pass every teardown GUC a
guard on a touched table reads — e.g. execution.teardown for rep6 tax
inputs, sod.teardown — cash02 fix 1); re-read every touched table for its
exact pre-test count, counting audit_log and live-written tables BY FIXTURE
IDS ONLY. Roles via user_roles (assert the app decides each). Read UNIQUE /
CHECK / FK constraints and unique indexes from the catalog before seeding
(FAIL if none are read) and compare every seeded literal in a CHECKed column
to its CHECK. Every INSERT's columns checked against docs/schema_snapshot.sql.
Reserved fixture prefixes that match no real row (assert it). Support
--self-checks-only and --cleanup-only. The regression subprocess gets
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip it with
one '[SKIP] nested run' line. If the seed fails, every fixture section is a
[FAIL], never skipped silently.

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
  [Y] Task 1 notes + reuse inventory; no new table without a reason
  [Y] Balance series == core + sleeves (independent SQL), markers, ranges
  [Y] Holdings + rungs consistent with the overview
  [Y] Activity mapping, pending, drawer links all resolve, empty state
  [Y] Earnings reconcile to activity to the cent; fee-on-cash rule
  [Y] Documents filtered, Eastern time
  [Y] Screens + member read-only with library labels
  [Y] Visibility, isolation, own routes, --check CURRENT, no full account
      number, real + demo rows unchanged
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_cash04.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
