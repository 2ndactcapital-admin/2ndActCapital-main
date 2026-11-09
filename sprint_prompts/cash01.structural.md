CASH01 — the cash track begins: confirm the design against the live app
(cash00, folded in as Task 1) and build the OBLIGATIONS LEDGER and CASH
COVERAGE — every known future outflow per entity, how far the liquid cash
covers them (months, and "covered through <date>" with the next gap), per
entity and per household, with flags. Read-only on money: no transfers, no
instructions, no sleeves yet (cash02+). 9 tasks + verification. Branch
cash01-work.

Design: project docs CASH_TREASURY_DESIGN_v1.md (2026-11-04),
CASH_TREASURY_ADDENDUM_v1.1_EXECUTION_ADAPTERS.md and CASH_TRACK_PLAN_v1.2.md
(2026-11-08) — mirrored in the repo at docs/cash/ by this sprint (Task 9).
DECIDED (Joe, 2026-11-08): the v1.2 §2 resolutions are accepted; standing
rule: USE THE APP'S EXISTING CONSTRUCTS and create a new one only where an
existing one cannot carry it, named and justified in the discovery doc.

THE CONCEPTS (from v1 §2, do not re-decide):
- Obligation = a known future outflow with an owner ENTITY (the payer), a
  date or recurrence, an amount, a certainty (firm / estimated), a source,
  an owner (person) and an as-of; rolls up to the household; bi-temporal.
- Source types: capital_call_projected (TA model on portfolio.commitments —
  computed at read time, NEVER stored as obligation rows), capital_call_
  noticed (manual / Chancery now; supersedes the projection for the same
  commitment, nearest date — the projection kept for history, never counted
  twice; an unmatched notice raises a task), debt_service (from liabilities:
  payment schedule, or an interest-only estimate from rate × balance),
  tax_estimate (manual first), insurance_premium, trust_payment (GRAT
  annuity, CRUT unitrust, required distributions), charitable_payout
  (foundation minimum distribution, pledges), property_carrying,
  tuition, lifestyle_recurring, one_off, vendor_invoice (v1.1; linked to a
  Chancery document; payee linkage comes with cash08).
- Coverage: months of coverage = liquid balance ÷ average monthly gross
  outflow (next 12 months of obligations); covered through = walk the dated
  schedule forward drawing down liquid balance until it runs out; show the
  first uncovered obligation ("next gap: <name>, ~$X, est. <date>").
  Per-entity coverage is the point: a family can be covered while the ILIT
  cannot pay its premium; flag every entity below its minimum
  (min_coverage_months, default 6, editable per entity / household).
- Liquid balance = the entity's Cash & Equivalents holdings (positions
  keyed taxonomy_mc_8_1 / taxonomy_sub_8_1_x — cash, money-market, SGOV-type
  T-bill funds) through the net-worth core (rollup2) — never a separate
  formula. An approved securities-backed line's UNDRAWN amount is shown
  separately, never mixed in.
- All money Decimal; every figure carries source and as-of; anything without
  data reads "not estimated — needs <x>".

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo + live DB 2026-11-08; Task 1
confirms signatures):
- services/portfolio_cash.py: cash is a POSITION like any other asset (no
  cash_balances table; ensure_cash_asset / record_cash_balance compose
  portfolio_assets writers). Keep that rule — liquid balance is read from
  positions.
- services/networth.py (rollup2): the one net-worth core; household_total;
  the taxonomy keys above.
- services/liabilities.py (crmdetail1): liabilities with kind, amount,
  terms (rate text), owner entity; the Olivo demo has $52.8M of mortgages and
  lines (docs/demo/olivo/data/liabilities.csv: e.g. E5 Goldman securities-
  backed line $20M "SOFR + 1.10%", E21 Coastal Bank mortgage $3.5M "5.35%
  fixed, 2049").
- services/portfolio_commitments.py + the TA model (ta_model / tamodel1–4)
  project capital calls per commitment; the SPV services
  (portfolio_spv, spv_events, spv_allocation, …) record SPV capital calls /
  distributions. The OVERLAP is open (v1 §2.1, v1.2 §2.9): Task 1 names ONE
  source of truth for commitments and calls; obligations READ it.
- Human tasks, workflow_todos, Chancery (documents), the disclosure library
  (estimate / tax labels), visibility1 (staff see only visible families),
  bugfix2 member scoping — all reused.
- Demo rows (demo-olivo) are permanent demo data: the sprint writes NO real
  or demo row. Loading the Olivo obligations is an operator script with
  --dry-run (Task 7).
- bugfix4: roles from user_roles only. The nightly refresh (~07:00 UTC) and
  real users write audit_log etc.: count BY FIXTURE IDS ONLY.
- Regression: coverage reads the net-worth core → re-run verify_rollup2.py
  ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s.
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
"cash01: <one line per change>".

=== TASK 1: DISCOVER + CONFIRM THE DESIGN (cash00) === Report findings, then
continue immediately. Write docs/cash/CASH00_DISCOVERY.md:
  1a. Commitments / calls: portfolio.commitments vs the SPV ledger — which
      is the source of truth for (i) a commitment and its unfunded amount,
      (ii) a noticed call, (iii) a paid call. Decide, with evidence; if both
      must stay, state the join and which wins. The TA projection's API.
  1b. Liabilities: what schedule data exists (rate, maturity, payment
      amount / frequency, interest-only flag); what debt_service can be
      computed today vs needs input.
  1c. Liquid balance: the net-worth core's Cash & Equivalents read per
      entity and household; held-away cash; demo coverage today.
  1d. Trust / charitable / insurance data in CRM (crmdetail1 trust fields:
      GRAT terms, CRUT %, ILIT premium, foundation) that can seed
      trust_payment / charitable_payout / insurance_premium obligations.
  1e. Reuse inventory: for each piece of the cash design, the existing
      construct it uses (table / service / screen) or why a new one is
      needed — the v1.2 §2 decisions confirmed against the code
      (payment_instructions vs execution_instructions; lifecycle names;
      trading_authority + rep7b authority; custodian_capabilities +
      PaymentAdapter; bank details stored only as adapter references + last
      four). These later decisions are RECORDED here; nothing for payments
      is built in this sprint.
  1f. Altruist cash questions (v1 §9.1) — what the read integration already
      returns (cash rate, sweep, MMF menu) vs what must be asked.

=== TASK 2: THE LEDGER ===
public.cash_obligations (new — no existing table carries dated future
outflows; justify in 1e): entity, household (derived), source_type,
amount (or amount rule), currency, due_date or recurrence (RRULE), end date,
certainty, source_ref (commitment / liability / document / CRM record),
owner person, notes, min fields per type; bi-temporal; RLS. Recurrences are
expanded at read time over a horizon (default 24 months), never stored as
rows. Derived obligations (projected calls, debt service from liabilities,
trust payments from CRM terms) are computed at read time from their source —
NOT copied into the table — and shown as "derived from <source>"; only
manual / noticed / invoice items are stored.

=== TASK 3: DERIVED SOURCES ===
services/cash_obligations.py — one read: for an entity / household and a
horizon, the merged schedule (stored + derived): projected calls from the TA
model (superseded by a matching noticed call), debt service from liabilities
(schedule if known, else interest-only estimate with "estimated — rate from
terms"; unparseable terms → "needs payment terms"), trust payments from CRM
trust terms where present, recurring items expanded. Each line: date,
amount, certainty, source, payer entity, as-of, explanation.

=== TASK 4: COVERAGE ===
services/cash_coverage.py — per entity and household: liquid balance (core),
monthly gross outflow (next 12 months / 12), months of coverage, covered-
through date and the first uncovered obligation (a forward walk, per entity
on that entity's own liquid balance; household on the household total), the
undrawn securities-backed line shown separately, flags (below minimum;
uncovered within 90 days; obligation with no payer cash at all). Pure
functions + one read per entity; deterministic; Decimal.

=== TASK 5: SCREENS ===
Household → Treasury (new tab, light / cream): coverage card (months,
covered-through, next gap), per-entity coverage table with flags,
obligations calendar + list (filter entity / certainty / source; derived
items marked with their source and why), add / edit / end a manual
obligation (author: a staff permission reused from the CRM / portfolio set —
name it; maker-checker not required for an obligation record, audited),
record a capital-call notice (matches a commitment; supersedes the
projection). Member view: their family's coverage, read-only, estimate
labels from the disclosure library (tax.estimate_label / a new
cash.coverage_estimate key, default text "have counsel review"); never
another family (visibility1 / bugfix2).

=== TASK 6: TASKS AND ALERTS ===
An unmatched capital-call notice, an entity below its minimum, and an
uncovered obligation within 90 days each raise ONE human task / to-do
(existing workflow_todos / human tasks), de-duplicated while open, closed
when resolved.

=== TASK 7: OLIVO DEMO (OPERATOR) ===
scripts/load_olivo_obligations.py — from docs/demo/olivo data (+ a new
docs/demo/olivo/data/obligations.csv you write from the v1 §1 list:
quarterly estimated taxes, property carrying costs per property, ILIT
premium, GRAT annuity payments through 2027, foundation 5% payout, tuition,
lifestyle) creates the MANUAL obligations for the demo family (tag
demo-olivo); --dry-run lists every row and the resulting coverage per entity;
the real run writes as the operator; idempotent. The sprint runs ONLY
--dry-run and records the coverage it would show.

=== TASK 8: REAL PROOF ===
  - Ledger: manual / noticed / invoice rows stored bi-temporally; a
    recurrence expands correctly over 24 months (incl. month-end and leap
    day); an ended recurrence stops.
  - Derived: a fixture commitment's TA projection appears as projected; a
    matching notice supersedes it (counted once), an unmatched notice raises
    one task; debt service from a fixture liability with a schedule, with
    only a rate (interest-only estimate, labelled), with unparseable terms
    ("needs payment terms"); a fixture GRAT's annuity from CRM terms.
  - Coverage: hand-computed months and covered-through for a fixture
    household with two entities — one covered, one (an ILIT-like entity)
    uncovered while the household is covered; the next gap named; the
    undrawn line shown separately and NOT counted; flags raised once.
  - Liquid balance equals the net-worth core's Cash & Equivalents for the
    entity (independent SQL), never a second formula.
  - Screens: node render tests (coverage card, entity table, calendar,
    member read-only with estimate labels).
  - Member sees only their family; staff only visible families (visibility1
    ON on a fixture org); org isolation both directions (rolbypassrls =
    false); real and demo rows unchanged (counted by fixture ids where real
    activity writes); the Olivo loader --dry-run writes nothing.
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_rollup2.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (cash01; the cash00 decisions), docs/cash/ (copies of
the three design docs + CASH00_DISCOVERY.md + CASH_OBLIGATIONS.md: sources,
derivation rules, coverage formulas), operator step (Olivo loader --dry-run
→ real); add verify_cash01.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_cash01.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure (TOTAL printed on every
exit path, with the traceback). Set AUTHZ_TEST_MODE=1 before importing the
app. Hydrate secrets from Doppler over HTTPS. No network (assert it). Fixture
orgs (privileged insert path, is_test = true — assert it); time injected
("today" fixed). Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id (every row a
service creates on the verify's behalf, by the id it returns); re-read every
touched table for its exact pre-test count, counting audit_log and
live-written tables BY FIXTURE IDS ONLY. Roles via user_roles (assert the app
decides each). Read UNIQUE / CHECK / FK constraints and unique indexes from
the catalog before seeding (FAIL if none are read). Reserved fixture
prefixes that match no real row (assert it). Support --self-checks-only and
--cleanup-only. The regression subprocess gets VERIFY_NESTED=1, after
teardown, 2700 s; with VERIFY_NESTED=1 skip it with one '[SKIP] nested run'
line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool (wrap
    and-chains in bool()).
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context set ON the connection inside its transaction (a contextvar
    alone is not an RLS context); rolbypassrls = false asserted; an empty
    list never passes a check.
  - Every query's placeholders match its arguments; a parameter used in two
    contexts gets an explicit cast (s30 fix 1).
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
  [Y] Task 1 (cash00) decisions 1a–1f recorded, with the reuse inventory
  [Y] Ledger: stored vs derived, recurrences, bi-temporal
  [Y] Derived sources: projected / noticed supersession / debt service /
      trust payments, each labelled
  [Y] Coverage: months, covered-through, next gap, per entity, flags;
      undrawn line separate
  [Y] Liquid balance == net-worth core (no second formula)
  [Y] Screens + member read-only with library labels
  [Y] Tasks de-duplicated; Olivo loader dry run
  [Y] Visibility, isolation, own routes, --check CURRENT, real + demo rows
      unchanged
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rollup2.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
