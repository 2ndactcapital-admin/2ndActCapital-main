LOTMETHOD — two parts in one sprint. Part A: a lot-relief method the adviser
chooses (household default, account override, per-proposal override) and a
truer tax-optimized ranking, used by every sale (rebalance, transition,
harvest) and by the X-ray. Part B: bugfix2 — fast, scoped verify cleanup
(the slow FK-walk start-up is timing out every nested regression) plus small
backlog fixes. 9 tasks + verification.

Context: today one fixed lot rule (services/rebalance_engine.lot_order_key:
losses first, then highest cost, long-term before short-term at equal cost,
then oldest; unknown cost never sold) serves rebalance, transition and harvest;
every sale names its lots on the instruction (specific identification). Joe
(2026-10-26) asked for the method as an option. Separately, verify_rep6 /
verify_rep7's pre-run cleanup has spent ~25 min in FK-walk deletes three runs
running, so their 2700 s nested regressions time out before their checks.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main after the rep7b merge; Task 1
confirms exact signatures):
- rebalance_engine: Lot, Holding, term_of, long_term_from, lot_order_key(lot,
  price, on), SaleSettings(today, gain_budget, realized_ytd,
  avoid_short_term, short_term_wait_days, fractional, wash_hold), LotSale,
  plan_sales. rebalance_proposals.generate / compute / transition_preview;
  harvest.scan_account (rep6, chooses loss lots itself — confirm whether it
  goes through plan_sales); xray._fund_analysis embedded gain / transition
  tax (rep7); account_models.settings_of (account settings incl.
  avoid_short_term, short_term_wait_days); rep7b authority:
  services/authority.py, household_authority / account_authority_settings
  (household default + account override pattern to copy for the method).
- rep6 tax profiles: tax_profiles.combined_rates(profile) → st / lt combined
  rates, as_of (text), profile_id, tax_year; tax_profiles.profile_for_account.
- Instructions carry specific lots (execution_instruction_lots); the
  custodian's own default cost-basis method is not stored anywhere today.
- Verify cleanup: verify_rep6/rep7/rep7b define sweep_fixture_orgs(conn,
  orgs, skip, log) (verify_rep7.py:383) — per org table, DELETE … WHERE
  org_id = ANY(fixture orgs), falling back to delete_tree (an FK walk through
  every dependent table with nested IN-subqueries) when fk_order raises; a
  users "(sweep)" step does the same for fixture users. That fallback walks
  deal / SPV / agreement tables (deal_signatures, spv_carry_run_lines,
  cost_events …) even when they hold no fixture rows. verify_rep2 / rep5 have
  their own leftovers() / teardown. Shared helpers live in
  scripts/_authz1_common.py (imported as C).
- Leftover fixture orgs from killed runs: REP2 99000000-…e2001/2/3 (since
  2026-10-26 19:15 UTC). verify_rep2's own pre-teardown removes them.
- Backlog items in scope for Part B (from claude/BUGFIX1_BACKLOG.md):
  B2 verify_rep5.py nested verify_rep2 timeout 1200 → 2700 s;
  B3 rbac.ensure_org_admin_role grants only manage_org_settings to a NEW
     org's first org_admin — it must also grant view_replication /
     manage_replication / approve_replication (and every permission later
     sprints granted to existing org_admins: tax, held-away, authority —
     Task 1 lists them from the catalog);
  B4 expired sign-in session: apps/web lib/api.js authHeaders() silently
     returns no header when getAccessToken fails → pages show "Missing or
     malformed Authorization header"; redirect to login instead;
  B5 verify_workflowmgr5.py stale assertion "three workflow permissions
     granted to NO seeded profile" (workflowpermsfix granted them to Adviser,
     CSA / Ops, Org Admin) — update it; verify_underlyingresolution.py:
     exclude apps/api/scripts from its code scan, print
     'TOTAL: N PASS, M FAIL';
  B6 un-archive restores the prior deal stage by matching history rows on
     creation time + creator — add the deal id to the history row and match
     on it.
- Regression: Part A changes plan_sales (rep5 / rep6 / rep7 share it) → re-run
  verify_rep6.py ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s (with
  Part B's fast cleanup it should no longer time out).
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
and read its output directly. Write the verify script but DO NOT RUN IT (the
--cleanup-only mode of other scripts included), and do not run npm run build.
No network calls. Never print a secret or a full account number. Column names
from the live DB. Bi-temporal rule 3 for every new table; RLS policies for
EVERY operation each new table will take. Decimal everywhere. Never guess a
tax rate or a price. The default method is today's rule, so no existing
proposal, plan or X-ray figure changes unless an adviser picks another
method. Labels and vocabularies from the API envelope. Light/cream UI with the
existing tokens. Commit ALL sprint files (excluding sprint_prompts/logs) on the
current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Every caller of lot_order_key / plan_sales and every place a lot is
      chosen without them (harvest, transition, X-ray, instruction lots).
  1b. Where the method setting lives: copy rep7b's household default +
      account override pattern (versioned, maker-checker only if Task 1 finds
      a reason — a method change is an investment-policy setting; say which
      and why) plus a per-proposal override.
  1c. Mutual funds and other average-cost holdings: how the security master
      marks them; what the engine must do (average cost, no lot choice).
  1d. Part B: time the existing cleanup paths by reading them — which step
      walks deal / SPV tables and why (fk_order cycle? users sweep?); the
      fastest correct scoped design.
  1e. The full list of permissions org_admin roles hold today that
      ensure_org_admin_role does not grant to a new org (B3).

=== TASK 2: SCHEMA ===
  - Method setting: household default + account override (versioned), values
    tax_optimized (default) | hifo | fifo | lifo | lowest_cost |
    highest_cost | specific (adviser picks lots on each proposal); gain
    preference prefer_long_term | st_losses_first | realize_gains (with an
    optional target amount, e.g. to fill the 0% LT bracket — the adviser
    enters the amount; never computed from income we do not hold).
  - custodian_cost_basis_method on the account (as reported by the custodian
    or entered by the adviser; 'unknown' default).
  - Per-proposal: method used + any adviser-picked lots recorded on the
    proposal.
  - B6: deal id on the deal stage-history row (backfill where it is
    unambiguous; leave others null and fall back to today's match, logged).
Apply with scripts/_apply_lotmethod.py through Doppler, synchronously; VERIFY
it landed; save docs/lotmethod_part1.sql.

=== TASK 3: THE ENGINE (Part A) ===
  - lot_order_key becomes method-aware; tax_optimized ranks by estimated tax
    per dollar raised using the rep6 profile's combined rates (a short-term
    loss ahead of a long-term loss; a long-term gain ahead of a short-term
    gain); with no tax profile it falls back to today's rule exactly and says
    so. hifo / fifo / lifo / lowest / highest follow their names, still
    respecting the gain budget, the short-term guard and wash-sale holds
    (a method never overrides a wash-sale hold or a hard block).
  - specific: the adviser's picked lots are validated (exist, open, enough
    quantity, not wash-held) and used as given.
  - realize_gains: picks gain lots up to the adviser's target, LT first,
    stated on the proposal.
  - Average-cost holdings: no lot choice; stated.
  - Every sale line states the method used and why each lot was chosen.
  - Harvest, transition and X-ray transition tax use the account's method.
  - custodian mismatch: when the account's method differs from
    custodian_cost_basis_method, the proposal and instruction carry a warning
    that specific lots must be named at the custodian (they already are).

=== TASK 4: API + SCREENS (Part A) ===
Household and account settings: method + gain preference (+ history);
proposal view: method used per sale, override control for a draft, lot picker
for 'specific'. Gate with existing proposal / settings permissions (Task 1
names them); routes in AUTHZ1_ROUTES; envelope vocabularies.

=== TASK 5: FAST SCOPED CLEANUP (Part B1) ===
  - One shared helper (scripts/_verify_cleanup.py) used by verify_rep2, rep5,
    rep6, rep7 and rep7b for the pre-run and final cleanup: delete only rows
    keyed by THIS script's fixture ids (orgs, users, securities, households,
    documents), child-before-parent from pg_constraint; before any FK walk,
    check which tables actually hold fixture rows and walk only those; never
    issue a DELETE … IN (subquery) against a table with no fixture rows; a
    cycle in fk_order is broken by an explicit per-script order, not by
    delete_tree.
  - Each of those scripts gets a --cleanup-only flag (cleanup, leftover
    check, exit; no seeding, no checks) and prints how long cleanup took.
  - Behaviour otherwise unchanged; do not touch any assertion.

=== TASK 6: SMALL FIXES (Part B2–B6) ===
B2–B6 as listed in CONFIRMED FACTS. B4: one place in lib/api.js; a node test
proves the redirect.

=== TASK 7: REAL PROOF ===
  - Default method = today's rule: on identical fixtures, the default plan's
    lots equal the pre-sprint lot_order_key's choice (prove against a frozen
    copy of the old function inside the verify).
  - Each method picks exactly the expected lots on one fixture with lots of
    differing cost, age and term; tax_optimized with a profile ranks a ST
    loss before a LT loss and a LT gain before a ST gain with exact
    estimated tax per dollar; no profile → today's rule, stated.
  - Gain budget, short-term guard and wash-sale holds still win over every
    method; specific lots validated (wash-held / closed / too small refused);
    realize_gains stops at the target; average-cost holding → no lot choice.
  - Account override beats household; per-proposal override recorded;
    custodian-mismatch warning present when they differ.
  - Harvest, transition and X-ray transition tax follow the method.
  - B1: each of verify_rep2 / rep5 / rep6 / rep7 / rep7b --cleanup-only runs
    (subprocess, synchronously, from THIS verify) and finishes with zero
    fixture rows; each prints its cleanup time; verify_rep2 --cleanup-only
    removes the REP2 e2001-3 leftovers; the total cleanup time of all five
    is printed (target: minutes, not tens of minutes — a [FIND] either way).
  - B2: verify_rep5's nested timeout is 2700. B3: a new org's first org_admin
    holds every permission existing org_admins hold (Task 1's list). B4: node
    test. B5: verify_underlyingresolution prints TOTAL; its scan excludes
    scripts; verify_workflowmgr5's assertion matches workflowpermsfix. B6:
    un-archive of two deals created in the same transaction by the same
    person restores each to its own prior stage.
  - Org isolation both directions (rolbypassrls = false); gates both ways.
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_rep6.py ONCE, flat, after teardown, VERIFY_NESTED=1,
    2700 s.

=== TASK 8: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (methods, ranking,
precedence: wash-sale > guards > method), claude backlog items B1–B6 marked
done in docs, verify_lotmethod.py added to scripts/regression_set.txt.

=== TASK 9: DON'T BREAK THE CHAIN ===
Run python -m py_compile on every changed verify script and node --test
tests/ (allowed); nothing else.

=== VERIFICATION: apps/api/scripts/verify_lotmethod.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
calls. Wrap every section so an exception is a [FAIL]; use the new shared
cleanup helper for its own teardown (start and end), deleting ONLY rows this
run created; re-read every touched table for its exact pre-test count.
Compute every expected date at the call (never a TODAY fixed at start).
Fixture orgs and users is_test = true (assert it); users-RLS pattern from
verify_rep1–7b. Read UNIQUE / CHECK / FK constraints from the catalog before
seeding and check every seed literal against its lookup or CHECK. Reserved
fixture prefixes that match no real row (assert it). Subprocesses
(--cleanup-only runs and the regression) run synchronously with timeouts
(cleanup-only 900 s each; regression 2700 s); with VERIFY_NESTED=1 this script
skips its regression section with one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Every import at the top of the file; never `x, y = (await f(a) for a …)`.
  - A failing check's detail names the culprit rows / response bodies.
  - Never touch real accounts, positions, lots, proposals, instructions,
    deals or users: count + checksum before and after.

Assertions:
  [Y] Task 1 findings 1a–1e
  [Y] Schema landed (method settings, custodian method, proposal method, B6
      deal id), RLS for every operation
  [Y] Default method reproduces today's rule exactly
  [Y] Each method's lots exact; tax_optimized by estimated tax per dollar;
      no profile → today's rule, stated
  [Y] Precedence: wash-sale holds / blocks and guards beat every method;
      specific validated; realize_gains target; average cost
  [Y] Overrides and custodian-mismatch warning; harvest / transition / X-ray
      follow the method
  [Y] B1: five --cleanup-only runs, zero fixture rows, times printed, REP2
      leftovers gone
  [Y] B2–B6 each proven
  [Y] Org isolation both directions; gates both ways; node tests
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rep6.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
