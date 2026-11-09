CASH02 — cash POLICIES and the THREE SLEEVES: for each household (and any
entity that needs its own), a versioned policy — Near-term / Reserve /
Long-term sleeve boundaries in months of obligations, rung count, objective,
minimum coverage — with sleeve targets DERIVED from the cash01 obligation
schedule (months → $ → %), presets (Stay liquid / Balanced / Recommended /
Custom) with an estimated net yield, the 3-step recommendation
questionnaire, and maker-checker on every save through S30. Still read-only
on money: a policy is a target, not a trade (proposals / orders are cash04).
9 tasks + verification. Branch cash02-work.

Design: docs/cash/ (CASH_TREASURY_DESIGN_v1 §2.3, §2.7, §3.5–3.6, §5, §7;
CASH_TRACK_PLAN_v1.2 §2.4) + docs/cash/CASH00_DISCOVERY.md and
CASH_OBLIGATIONS.md written by cash01 — READ THEM FIRST; cash01's decisions
bind this sprint. DECIDED (Joe, 2026-11-08): use the app's existing constructs
and create a new one only where an existing one cannot carry it (named and
justified in docs/cash/CASH02_NOTES.md).

THE CONCEPTS (from v1, do not re-decide):
- Sleeves are defined in MONTHS OF OBLIGATIONS: Near-term = months 0..N
  (default 3, + buffer), Reserve = N..R (default 12), Long-term = R..horizon
  (default 24). Two boundary controls (N, R) and a rung count (default 8);
  sleeve sizes are DERIVED from the obligation schedule (sum of obligations
  falling in each window, from services/cash_obligations), never typed.
  Shown as months, $ and % of the liquid total.
- Policy scope: household default; an entity override where an entity pays
  its own obligations (cash01's per-entity coverage). min_coverage_months
  moves here from cash01's per-entity setting (one home; cash01's coverage
  reads it — migrate, don't duplicate).
- Presets: Stay liquid (everything Near-term), Balanced (everything
  Reserve), Recommended (from the questionnaire), Custom (the boundaries).
  Each shows an ESTIMATED net yield per sleeve on its basis — Near-term:
  3-month T-bill proxy; Reserve: 6-month / 1-year; Long-term: 2-year — from
  the FRED series already in services/market_data (fred.py; read via
  read_service), labelled "estimate — Treasury curve proxy, as of <date>";
  missing series → "not estimated — needs <series>". After-tax yield: shown
  only where the account owner has a tax profile (services/tax_profiles.py,
  rep6) using Treasury state-tax exemption only, labelled; else "after-tax:
  needs a tax profile". Instrument-level after-tax selection is cash03 — not
  here.
- Questionnaire (3 steps): (1) Outflows — prefilled from the obligation
  schedule (next 12 months), with trailing 3-month actual outflows as a
  cross-check where transaction data exists (warn if the entered figure is
  well below; "no outflow history" otherwise); (2) Objective — stability vs
  yield (a 5-point scale that positions N and R within documented bounds);
  (3) Recommendation — the editor view with the derived sleeves and estimated
  yields. Applying creates a POLICY DRAFT — never an active policy.
- Maker-checker: saving / applying creates a draft; activation needs a
  DIFFERENT eligible person through services/sod.py (a new item type
  'cash_policy' in sod_duties with its duty, the default rule set like the
  other item types; the human-task kind with maker_checker=True and the same
  approval route as everything else). Self-approval follows the org's SoD
  rule for this item type (it is NOT money movement — money movement floor
  duties come with cash07); the floor rules S29b turned on stay on.
- All money Decimal; bi-temporal (rule 3); every figure carries source and
  as-of.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo 2026-11-08; Task 1 confirms
signatures):
- services/sod.py: load / can_check / eligible_checkers / enforce / gate /
  record_decision / raise_self_approval_todo; services/sod_admin.py (the SoD
  admin page reads sod_duties / sod_rules); services/human_tasks.py and
  bulk_approvals.py (the approval inbox — a cash_policy task must appear and
  be approvable there like any other kind).
- services/market_data/fred.py + read_service.py + registry.py (FRED is a
  registered provider); services/tax_profiles.py (rep6).
- cash01 (merged before this sprint): cash_obligations + services/
  cash_obligations.py + services/cash_coverage.py + the Household → Treasury
  tab. Extend that tab; do not add a second one.
- Demo rows (demo-olivo) are permanent: the sprint writes NO real or demo
  row. The Olivo demo policy is an operator script (Task 7).
- bugfix4: roles from user_roles only. Count audit_log, sod_decisions and
  live-written tables BY FIXTURE IDS ONLY.
- Regression: verify_cash01.py ONCE, flat, after teardown, VERIFY_NESTED=1,
  2700 s (coverage now reads min_coverage_months from the policy).
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
"cash02: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately. Write
docs/cash/CASH02_NOTES.md: the cash01 decisions that bind this sprint; the
SoD item-type registration path (how S30 / S29b added theirs); the approval
inbox path for a new task kind; the FRED series available and their as-of;
tax_profiles' read; where min_coverage_months lives today and the migration;
reuse inventory (existing construct per piece, or why new).

=== TASK 2: TABLES ===
public.cash_policies (scope household | entity, boundaries N / R, horizon,
rung_count, objective, preset, min_coverage_months, status draft /
pending_approval / active / retired, maker_ids, approved_by, approval_task_id,
version; one active per scope enforced by a unique index; approved_by ∉
maker_ids enforced in the DB — the execution-instruction constraint style);
bi-temporal; RLS every operation. Sleeve targets are DERIVED at read time
from (policy, obligation schedule) — not stored, unless Task 1 shows a stored
snapshot is needed for the approval record (then a snapshot on approval only,
named and justified).

=== TASK 3: THE CALCULATION ===
services/cash_policy.py — pure functions: sleeve windows from (N, R,
horizon); sleeve $ targets from the obligation schedule; % of the liquid
total; preset → boundaries; questionnaire → recommended N / R (documented,
deterministic); estimated yield per sleeve (FRED proxy) and after-tax where a
tax profile exists; coverage vs min_coverage_months. Decimal; deterministic;
every output with source and as-of.

=== TASK 4: MAKER-CHECKER ===
Draft → submit (creates the human task, kind cash_policy, maker_checker
True) → approve (sod.gate: a different eligible person; the decision
recorded in sod_decisions) → active (the prior active version retired,
bi-temporally) | reject (reason). Edits to an active policy create a new
draft version. Refusals are audited (sod.refused). The task appears in the
approval inbox.

=== TASK 5: SCREENS ===
Household → Treasury tab (cash01's), new panels, light / cream: policy
editor (two boundary sliders, rung count, preset chips, live derived sleeves
in months / $ / %, estimated yields with their labels, "draft — not saved"
until submit, diff vs active), questionnaire (3 steps), approval status and
history. Member view: their family's active policy and sleeve targets,
read-only, labels from the disclosure library (cash.coverage_estimate from
cash01; a new key cash.yield_estimate, default text "estimates use the
Treasury curve; actual yields differ; have counsel review"); never another
family (visibility1 / bugfix2).

=== TASK 6: PERMISSIONS ===
Author and approve permissions: reuse existing permission names where one
fits (name it in Task 1), else add 'manage_cash_policy' to the permission
catalogue the way S30 added its own; Super Admin / org admin / advisor
mapping documented.

=== TASK 7: OLIVO DEMO (OPERATOR) ===
scripts/load_olivo_cash_policy.py — a Recommended policy for the demo family
(tag demo-olivo) as a DRAFT submitted for approval (the operator approves it
in the app as a second person); --dry-run prints the derived sleeves,
estimated yields and coverage vs minimum; idempotent. The sprint runs ONLY
--dry-run and records what it would show.

=== TASK 8: REAL PROOF ===
  - Calculation: hand-computed sleeves for a fixture schedule (incl. an
    obligation exactly on a boundary, an empty Reserve window, horizon
    shorter than R); presets map as documented; questionnaire endpoints
    (most stable / most yield) produce the documented N / R; Decimal
    compared as Decimal.
  - Yields: FRED proxy read through read_service with as-of; a missing
    series reads "not estimated — needs <series>"; after-tax only with a
    fixture tax profile, else the label.
  - Maker-checker: maker cannot approve (refused, recorded in
    sod_decisions, nothing changed); a different eligible fixture person
    approves → active, prior retired; a second active per scope is
    impossible (the unique index refuses a direct insert); approved_by ∈
    makers refused by the DB itself; the task appears in the inbox and
    bulk-approve respects the gate.
  - min_coverage_months: cash01's coverage flags read it from the policy;
    no second copy remains.
  - Screens: node render tests (editor, questionnaire, member read-only
    with library labels).
  - Member sees only their family; staff only visible families (visibility1
    ON on a fixture org); org isolation both directions (rolbypassrls =
    false); real and demo rows unchanged (counted by fixture ids where real
    activity writes); the Olivo loader --dry-run writes nothing.
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_cash01.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (cash02), docs/cash/CASH_POLICY.md (sleeve windows,
presets, questionnaire mapping, yield proxies and labels, approval flow),
docs/SEGREGATION_OF_DUTIES.md (the cash_policy item type), operator step
(Olivo policy loader --dry-run → real → approve as a second person); add
verify_cash02.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_cash02.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure (TOTAL printed on every
exit path, with the traceback). Set AUTHZ_TEST_MODE=1 before importing the
app. Hydrate secrets from Doppler over HTTPS. No network (assert it). Fixture
orgs (privileged insert path, is_test = true — assert it); time injected
("today" fixed); FRED reads from fixture series rows, never live. Wrap every
section so an exception is a [FAIL]; teardown via scripts/_verify_cleanup.py
at start and end, by fixture id (every row a service creates on the verify's
behalf, by the id it returns — incl. sod_decisions and human tasks; tables
without org_id passed in the cleanup plan); re-read every touched table for
its exact pre-test count, counting audit_log, sod_decisions and live-written
tables BY FIXTURE IDS ONLY. Roles via user_roles (assert the app decides
each). Read UNIQUE / CHECK / FK constraints and unique indexes from the
catalog before seeding (FAIL if none are read). Every INSERT's columns
checked against docs/schema_snapshot.sql (s29b fix 1). Reserved fixture
prefixes that match no real row (assert it). Support --self-checks-only and
--cleanup-only. The regression subprocess gets VERIFY_NESTED=1, after
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
  [Y] Task 1 notes, reuse inventory, cash01 decisions honoured
  [Y] Policy table: versioned, one active per scope, approved_by ∉ makers in
      the DB, bi-temporal, RLS
  [Y] Sleeves derived from the obligation schedule (hand-computed)
  [Y] Presets + questionnaire mapping deterministic and documented
  [Y] Estimated yields (FRED proxy) and after-tax only with a profile,
      labelled
  [Y] Maker-checker through sod.py; refusals recorded; inbox + bulk-approve
      respect the gate
  [Y] min_coverage_months has one home; cash01 coverage reads it
  [Y] Screens + member read-only with library labels; Olivo loader dry run
  [Y] Visibility, isolation, own routes, --check CURRENT, real + demo rows
      unchanged
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_cash01.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
