CASH06 — CASH RULES, stage A: standing rules that keep a family's cash where
the policy wants it — distribute across accounts, maintain a target balance,
scheduled recurring transfer, redirect incoming funds — built as WORKFLOW
TRIGGERS on the existing engine (run history, holds, alerts come free). Each
firing produces a DRAFT PAYMENT INSTRUCTION (cash07) submitted for human
approval — a rule never approves, sends or moves money (stage A; stages B / C
are later and need counsel). Same-owner destinations only. 9 tasks +
verification. Branch cash06-work.

Design: docs/cash/ (CASH_TREASURY_DESIGN_v1 §4 rule types + frequencies;
CASH_TRACK_PLAN_v1.2 §1 cash06, §2.4 "a rule's maker is the rule / trigger
owner (S30 Task 8), so its checker must be someone else"; CASH_PAYMENTS.md
and CASH07_NOTES.md from cash07) — READ THEM FIRST; earlier decisions bind.
DECIDED (Joe, 2026-11-08): use the app's existing constructs (workflow
engine, triggers, templates, S29b autonomy, S30 SoD, cash07 payments); a new
one only where none can carry it (named and justified in
docs/cash/CASH06_NOTES.md). Money movement is excluded from autonomy until
counsel signs off (Joe): nothing here sends money.

THE CONCEPTS (decided, do not re-decide):
- Rule types (Mercury's, kept): (1) distribute across accounts — % of
  balance, fixed amount, or % of incoming, to one or more same-owner
  destinations; (2) maintain a target balance — when the account is below /
  above a target (condition), draw from / sweep to a supporting account;
  (3) scheduled recurring transfer — fixed amount on a schedule; (4)
  redirect incoming funds — sweep % of each incoming credit. Frequencies:
  daily, business days (us_business_days), weekly, monthly; event-driven
  for (4) (an incoming credit on the feed).
- A rule = a workflow trigger + a small rule definition (type, source,
  destinations with % / $, basis, condition, frequency, stage = 'A',
  status draft / active / paused / retired, owner person). Prefer the
  engine's existing trigger + workflow-template constructs (wftemplates1)
  for storage; a public.cash_rules table only if Task 1 shows the trigger
  config cannot carry the definition (justify).
- Governance: creating / editing / activating a rule is maker-checker
  through S30 (an item type 'cash_rule'; whether it is a FLOOR duty: yes —
  a rule that drafts money movement is money-movement configuration; no
  self-approval). The rule's OWNER is the maker of every instruction it
  drafts, so the instruction's checker must be a different eligible person
  (S30 Task 8 rule) — enforced by the cash07 floor, proved here.
- Firing: the engine evaluates the rule (balances from the net-worth core /
  feed; amounts Decimal, rounded to the cent, never below $0.01, never more
  than the source's available cash — shortfall → hold + alert, no partial
  overdraft) and calls ONE service verb: payments.draft_and_submit (cash07)
  with purpose "rule <name>" and the rule id. S29b classes that verb
  eligible_with_cap for DRAFT + SUBMIT only (caps: per-firing $ and per-day
  count from the rule, with an org ceiling); approve / send / reconcile stay
  'never'. Idempotent per (rule, period / incoming txn). A paused rule never
  fires; kill switches (S29b) stop all rules.
- Disclosure: cash.payments (cash07) shown on the rule editor; a new key
  cash.rules (default text "rules prepare payment instructions for approval;
  no money moves without a person approving it") — draft only.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo 2026-11-09; Task 1 confirms
signatures):
- Workflow engine: services/workflow_engine.py, workflow_schedule.py,
  workflow_scheduler.py, domain_events (S29b: one run per event per
  version, per-version hourly rate limit), workflow_autonomy.py
  (VERB_AUTONOMY, decide_step, kill switches, limits), wftemplates1
  templates + maker-checker publish; human_tasks.py; sod.py.
- cash07 (merged before this sprint): payment_instructions, payment_events,
  services/payments.py, payment_adapters.py (manual only), the
  payment_instruction floor item type. Extend; do not duplicate.
- cash01–cash05: obligations, coverage, policies, ladder, proposals,
  Treasury screens (Payments panel from cash07).
- Demo rows (demo-olivo) are permanent: the sprint writes NO real or demo
  row; no rule is created for the demo family by script. bugfix4: roles
  from user_roles only. Count audit_log, sod_decisions, payment_events,
  workflow runs and live-written tables BY FIXTURE IDS ONLY.
- Regression: verify_cash07.py and verify_s29b.py ONCE each, flat, after
  teardown, VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. No model call. Never print a secret or a full account
number. Never call a real custodian. Bi-temporal rule 3; RLS for every
operation. Light / cream UI. Commit ALL sprint files (excluding
sprint_prompts/logs) on the current branch at the end with message "cash06:
<one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately. Write
docs/cash/CASH06_NOTES.md: how a trigger + workflow definition is stored and
versioned; schedule vs event triggers (an incoming feed credit as a domain
event — does one exist? if not, the minimal emitter on the feed import);
how S29b caps / limits are declared per verb and per trigger; where the
rule definition lives (trigger config vs a table — decide); the cash07
service verb to call; reuse inventory.

=== TASK 2: DEFINITION + STORAGE === per Task 1; validation (destinations
same-owner, % sum ≤ 100, amounts > 0, rails allowed by capability, caps ≤
org ceiling); versioned; RLS.

=== TASK 3: EVALUATOR === services/cash_rules.py — pure: (rule, balances,
incoming txns, today) → zero or more instruction drafts with amounts and
reasons, or a hold with a reason (shortfall, cap, paused, stale balance >
1 business day). Deterministic; Decimal; cent rounding documented.

=== TASK 4: ENGINE WIRING === the trigger step calls
payments.draft_and_submit (add it to cash07's service if missing: draft +
submit in one transaction, maker = rule owner); S29b VERB_AUTONOMY entry
(eligible_with_cap, draft + submit only); idempotency per (rule, period |
txn); holds + alerts through the engine; kill switch honoured.

=== TASK 5: GOVERNANCE === S30 item type cash_rule (floor, no
self-approval); activate / edit / pause / retire flows; an edit creates a
new version (draft) — the active version keeps running until the new one is
approved.

=== TASK 6: SCREENS === Treasury tab → Rules: list (status, last run, next
run, holds), editor per type (destinations with % / $, condition, frequency,
caps, preview of what it WOULD draft today), run history (engine runs →
drafted instructions, links into Payments), pause / resume. Members: their
family's active rules read-only with cash.rules text.

=== TASK 7: OPERATOR NOTES === docs/cash/CASH_RULES.md: rule types, caps,
what a firing does, who approves, how to pause everything (kill switch).

=== TASK 8: REAL PROOF ===
  - Evaluator: hand-computed drafts for each rule type (% of balance with
    rounding; fixed; % of incoming on a fixture credit; maintain-target
    above and below; recurring on business days skipping a holiday);
    shortfall → hold, never a partial overdraft; stale balance → hold.
  - Engine: a scheduled firing drafts + submits exactly one instruction
    (maker = rule owner); the same period again → none (idempotent); an
    incoming credit fires (4) once per txn; caps hold the excess with an
    alert; paused rule and kill switch → nothing; S29b refuses approve /
    send from the rule path (attempt it).
  - Governance: the rule's maker cannot activate it; a second eligible
    person does; the rule OWNER cannot approve an instruction the rule
    drafted (cash07 floor, recorded in sod_decisions); a different person
    can; an edit runs the old version until the new one is approved.
  - Validation: third-party destination, % > 100, a rail the custodian lacks
    → refused with reasons.
  - Screens: node render tests (list, editor per type with preview, run
    history, member read-only).
  - Nothing sent: every drafted instruction stays pending_approval; no
    adapter send was called; no network (asserted).
  - Members only their family; staff only visible families; org isolation
    both directions (rolbypassrls = false); no full account number; real
    and demo rows unchanged.
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_cash07.py and verify_s29b.py ONCE each, flat, after
    teardown, VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (cash06), docs/SEGREGATION_OF_DUTIES.md (cash_rule),
docs/WORKFLOW_AUTONOMY.md (the new verb class), docs/cash/CASH_RULES.md,
OUTSTANDING_TODO_LIST (publish cash.rules); add verify_cash06.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_cash06.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure (TOTAL printed on every
exit path, with the traceback). Set AUTHZ_TEST_MODE=1 before importing the
app. Hydrate secrets from Doppler over HTTPS. No network (assert it).
Fixture orgs (privileged insert path, is_test = true — assert it); time
injected ("today" fixed; the engine's clock injected too). Wrap every
section so an exception is a [FAIL]; teardown via scripts/_verify_cleanup.py
at start and end, by fixture id (every row a service creates on the
verify's behalf, by the id it returns — rules, triggers, runs, instructions,
payment_events, tasks, sod_decisions; tables without org_id passed in the
cleanup plan; pass EVERY teardown GUC a guard on a touched table reads —
cash02 fix 1); re-read every touched table for its exact pre-test count,
counting audit_log, sod_decisions, payment_events, workflow runs and
live-written tables BY FIXTURE IDS ONLY. Roles via user_roles (assert the
app decides each). Read UNIQUE / CHECK / FK constraints and unique indexes
from the catalog before seeding (FAIL if none are read) and compare every
seeded literal in a CHECKed column to its CHECK. Every INSERT's columns
checked against docs/schema_snapshot.sql. Text ids compared as text, uuids
as uuid (cash04 fix 1). Reserved fixture prefixes that match no real row
(assert it). Support --self-checks-only and --cleanup-only. Regression
subprocesses get VERIFY_NESTED=1, after teardown, 2700 s; with
VERIFY_NESTED=1 skip them with one '[SKIP] nested run' line. If the seed
fails, every fixture section is a [FAIL], never skipped silently. Every
one-off section must finish in < 10 minutes.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool (wrap
    and-chains in bool()).
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context set ON the connection inside its transaction (a contextvar
    alone is not an RLS context); rolbypassrls = false asserted; an empty
    list never passes a check.
  - Every query's placeholders match its arguments; a parameter used in two
    contexts gets an explicit cast of the right type.
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
  [Y] Task 1 notes + reuse inventory; storage decision justified
  [Y] Evaluator per rule type, rounding, holds (shortfall / cap / stale)
  [Y] Engine: drafts + submits only, idempotent, caps, pause, kill switch;
      S29b refuses approve / send
  [Y] Governance: cash_rule floor; owner ≠ instruction checker; versioned
      edits
  [Y] Validation refusals; nothing sent; no network
  [Y] Screens + member read-only with library text
  [Y] Visibility, isolation, own routes, --check CURRENT, no full account
      number, real + demo rows unchanged
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_cash07.py + verify_s29b.py ONCE each, flat, after
      teardown, VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
