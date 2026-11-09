CASH04 — from PLAN to ORDERS: turn an active cash policy's ladder and
instrument plan (cash02 + cash03) into a CASH PROPOSAL per account (buys /
sells of the sleeve and ladder securities needed to reach the targets), and
an approved proposal into EXECUTION INSTRUCTIONS through Replication
Studio's existing path (these are trades of securities — CASH_TRACK_PLAN
v1.2 §2.1: same table, same routers, same maker-checker). Add the MONTHLY
MATURITY WATERFALL as a scheduled job on the workflow engine that produces a
proposal, never a trade. No money movement between accounts (that is
cash07). 9 tasks + verification. Branch cash04-work.

Design: docs/cash/ (CASH_TREASURY_DESIGN_v1 §2.4 waterfall, §2.5
withdrawal order, §4 stage A, §7; CASH_TRACK_PLAN_v1.2 §1 cash04, §2.1,
§2.3, §2.4) + CASH00_DISCOVERY.md, CASH_OBLIGATIONS.md, CASH_POLICY.md,
CASH_LADDER.md and the CASH0x_NOTES — READ THEM FIRST; earlier decisions
bind. DECIDED (Joe, 2026-11-08): use the app's existing constructs; a new one
only where none can carry it (named and justified in
docs/cash/CASH04_NOTES.md).

THE CONCEPTS (from v1, do not re-decide):
- Cash proposal = for one account, the trade list that moves today's cash /
  sleeve / ladder holdings toward the targets of the ACTIVE policy version:
  buy the chosen instrument per sleeve (cash03 plan), buy ladder rungs
  (par, maturity, CUSIP from the security master), sell what is over target
  in withdrawal order (§2.5: Near-term first, then Reserve, then Long-term
  — the rung closest to maturity first, with the "may lose principal"
  warning on any early sale of a bill / note). Never sells non-cash
  holdings; never buys beyond the account's available cash (shortfall shown,
  not borrowed). Each line: side, security, quantity or notional, price
  kind (market / limit as the instrument requires — MMF by notional,
  bills / notes by par with a limit), reason (sleeve / rung / obligation
  served), estimated after-tax yield.
- Reuse: the proposal lifecycle and screens Replication Studio already uses
  for rebalance proposals (services/rebalance_proposals.py and
  rebalance_engine.py) — a cash proposal is a proposal KIND (or source) on
  that construct, not a new table, unless Task 1 proves it cannot carry it.
  Approved proposals become instructions through services/execution.py
  create_instructions(… routed_via …) and submit(...), routed by custodian
  through services/execution_routers.py registry() (Manual / Altruist),
  with route_refusal respected. Maker-checker through services/sod.py as
  every proposal / instruction already is (S30); account authority (rep7b
  'manage') and trading authority (SOC5) checked by the existing execution
  path — not re-implemented.
- Maturity waterfall (monthly, and on any rung maturity): (1) top up any
  sleeve below target, Near-term first; (2) reinvest the remainder as a new
  rung at the ladder end — or on the next uncovered obligation date; (3)
  earnings (dividends, interest) reinvested per the target allocation.
  Runs as a scheduled job on the workflow engine (services/
  workflow_schedule.py / workflow_scheduler.py, the way secmaster1's jobs
  run), SHIPPED SWITCHED OFF; output = a draft cash proposal + one human task
  per household (de-duplicated while open). S29b autonomy: the job's write
  verb is classed so it can only DRAFT a proposal — never submit or route.
- All money Decimal; every figure carries source and as-of; bi-temporal.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo 2026-11-09; Task 1 confirms
signatures):
- services/rebalance_proposals.py (target_for, drift_threshold,
  account_lock, model_change_for, realized_ytd …) and rebalance_engine.py;
  services/execution.py (create_instructions at ~:420, submit at ~:571);
  services/execution_routers.py (ExecutionRouter ABC, NotConnectedRouter,
  registry(), routers(), any_connected(), route_refusal()); execution_letter.py
  (the manual instruction sheet); custodian_capabilities (rep2/rep13).
- execution_instructions CHECKs: side ∈ buy / sell / sell_short /
  buy_to_cover, exactly one of quantity / notional > 0, price_kind market /
  limit / range. Cash proposals use only buy / sell — never loosen a
  constraint.
- services/workflow_schedule.py, workflow_scheduler.py; services/
  workflow_autonomy.py (S29b VERB_AUTONOMY, decide_step); human_tasks.py.
- cash01–cash03 merged: obligations, coverage, policies, ladder, instrument
  plan, Treasury tab. Extend; do not duplicate.
- Demo rows (demo-olivo) are permanent: the sprint writes NO real or demo
  row, and never routes anything. bugfix4: roles from user_roles only. Count
  audit_log, sod_decisions and live-written tables BY FIXTURE IDS ONLY.
- Regression: verify_cash03.py AND verify_rep13.py (the execution path)
  ONCE each, flat, after teardown, VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. No model call. Never print a secret. Never route an
order to a real custodian (fixtures route only to the Manual router). Bi-
temporal rule 3; RLS for every operation. Light / cream UI. Commit ALL sprint
files (excluding sprint_prompts/logs) on the current branch at the end with
message "cash04: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately. Write
docs/cash/CASH04_NOTES.md: how a rebalance proposal is stored, approved and
turned into instructions (functions, tables, task kind, SoD item type);
whether a cash proposal fits as a kind / source (decide, with evidence);
how a bill / note order is expressed today (par vs quantity, limit price);
the scheduler job registration path (secmaster1's jobs as the model); the
S29b verb class for the waterfall's write; reuse inventory.

=== TASK 2: PROPOSAL BUILDER ===
services/cash_proposals.py — pure trade-list builder (holdings, available
cash, active policy, ladder, instrument plan → lines with reasons), the
withdrawal order, early-sale warnings, shortfall; then the thin write that
creates the proposal through the existing proposal construct.

=== TASK 3: APPROVAL → INSTRUCTIONS ===
Approve through the existing proposal approval (sod.py gate, a different
eligible person); on approval, create_instructions per account with
routed_via from the custodian (registry / capabilities), the manual sheet
via execution_letter; refusals from route_refusal or authority shown with
the reason; nothing bypasses submit().

=== TASK 4: WATERFALL JOB ===
The monthly / on-maturity job on the workflow scheduler, switched off; per
household: detect maturities and earnings since the last run, build a draft
cash proposal, raise one task; idempotent per (household, period); S29b
classes the write as draft-only; run history and holds via the engine.

=== TASK 5: SCREENS ===
Household → Treasury tab: "Proposed trades" panel (lines grouped by sleeve /
rung with reasons, early-sale warnings, shortfall, after-tax yield effect),
link into the existing proposal / instruction screens for approve / route /
sheet; waterfall run history. Member view: unchanged (no trade detail
beyond what members already see for proposals).

=== TASK 6: PERMISSIONS / AUDIT ===
Reuse the existing proposal / instruction permissions (name them); job
switch-on via the existing workflow admin; every proposal and job run
audited.

=== TASK 7: OPERATOR STEPS ===
scripts/olivo_cash_proposal.py --dry-run: the demo family's cash proposal
from its active policy (requires the operator to have approved it — cash02's
step); prints lines and the instruction sheet it WOULD create; never routes.
The sprint runs ONLY --dry-run (if no active demo policy, it prints that and
exits 0).

=== TASK 8: REAL PROOF ===
  - Builder: hand-computed trade lists for fixture accounts (under-target
    Near-term → MMF buy by notional; ladder rungs by par with limit; an
    over-target Reserve → sell in withdrawal order with the warning;
    insufficient cash → shortfall, no borrow; non-cash holdings never sold);
    Decimal as Decimal.
  - Approval: the maker cannot approve (refused, recorded); a second
    eligible fixture person approves → instructions created only through
    create_instructions, routed to Manual, sheet rendered; an account not
    'manage' or without trading authority → refused by the existing path
    with the reason; route_refusal honoured; every instruction satisfies the
    table's CHECKs.
  - Waterfall: a fixture rung maturing → draft proposal tops up Near-term
    first then reinvests at the ladder end / next uncovered obligation;
    earnings reinvested; re-run same period → no duplicate; switched off by
    default; S29b refuses a submit / route from the job.
  - Screens: node render tests (proposed trades, warnings, run history).
  - Staff only visible families; org isolation both directions
    (rolbypassrls = false); real and demo rows unchanged; nothing routed to
    a real custodian (asserted); loader --dry-run writes nothing.
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_cash03.py and verify_rep13.py ONCE each, flat, after
    teardown, VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (cash04), docs/cash/CASH_PROPOSALS.md (builder
rules, withdrawal order, approval → instruction path, waterfall), operator
steps (switch the waterfall job on; Olivo proposal dry-run); add
verify_cash04.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_cash04.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure (TOTAL printed on every
exit path, with the traceback). Set AUTHZ_TEST_MODE=1 before importing the
app. Hydrate secrets from Doppler over HTTPS. No network (assert it).
Fixture orgs (privileged insert path, is_test = true — assert it); time
injected ("today" fixed). Wrap every section so an exception is a [FAIL];
teardown via scripts/_verify_cleanup.py at start and end, by fixture id
(every row a service creates on the verify's behalf, by the id it returns —
proposals, instructions, tasks, sod_decisions, job runs; tables without
org_id passed in the cleanup plan); re-read every touched table for its
exact pre-test count, counting audit_log, sod_decisions and live-written
tables BY FIXTURE IDS ONLY. Roles via user_roles (assert the app decides
each). Read UNIQUE / CHECK / FK constraints and unique indexes from the
catalog before seeding (FAIL if none are read). Every INSERT's columns
checked against docs/schema_snapshot.sql. Reserved fixture prefixes that
match no real row (assert it). Support --self-checks-only and
--cleanup-only. Regression subprocesses get VERIFY_NESTED=1, after teardown,
2700 s; with VERIFY_NESTED=1 skip them with one '[SKIP] nested run' line. If
the seed fails, every fixture section is a [FAIL], never skipped silently.

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
  [Y] Builder: targets, withdrawal order, warnings, shortfall, never sells
      non-cash (hand-computed)
  [Y] Approval through sod.py; instructions only via create_instructions /
      submit; authority + route_refusal honoured; CHECKs unchanged
  [Y] Waterfall job: correct, idempotent, off by default, draft-only under
      S29b
  [Y] Screens; Olivo proposal dry run; nothing routed to a real custodian
  [Y] Visibility, isolation, own routes, --check CURRENT, real + demo rows
      unchanged
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_cash03.py + verify_rep13.py ONCE each, flat, after
      teardown, VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
