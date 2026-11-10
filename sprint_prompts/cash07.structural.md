CASH07 — PAYMENT INSTRUCTIONS (stage A, manual): the governed way to move
cash between a family's OWN accounts — a payment_instructions table on the
SAME governance spine as execution instructions (maker-checker through S30,
sent discipline, reconciliation, cancel / expire), the money lifecycle from
CASH_TRACK_PLAN_v1.2 §2.2, a PaymentAdapter interface beside the trade
routers with ONE adapter — `manual` (a printable payment sheet the operator
sends to the custodian) — an append-only payment_events log, and
reconciliation against the custodian transaction feed. Same-owner
destinations ONLY (third parties / payees are cash08; automatic sending is
cash09+). 9 tasks + verification. Branch cash07-work.

Design: docs/cash/ (CASH_TREASURY_DESIGN_v1 §4; ADDENDUM v1.1 §3–§4, §6.1,
§7; CASH_TRACK_PLAN_v1.2 §2.1–§2.7, §2.10) + every CASH0x_NOTES — READ THEM
FIRST; cash00's 1e decisions bind. DECIDED (Joe, 2026-11-08): the v1.2 §2
resolutions; use existing constructs — the ONE new table justified up front
is payment_instructions (+ its event log), because execution_instructions'
CHECKs protect every trade (§2.1; rep14's section351_transfers is the
precedent). Anything else new: named and justified in
docs/cash/CASH07_NOTES.md.

THE CONCEPTS (decided, do not re-decide):
- A payment instruction: org, household, source account, destination
  account (BOTH accounts of entities in the SAME household with the same
  beneficial owner per cash00 1e — refuse anything else with "third-party
  payments come later"), amount (Decimal > 0), currency USD, rail (ach |
  wire | journal | check — only rails the source custodian declares in
  custodian_capabilities), value date (business day, us_business_days),
  purpose (free text + optional link to a cash obligation or a cash
  proposal), maker_ids, approved_by, approval_task_id, sent_by / sent_at /
  sent_channel, external_ref, reconcile_method (custodian_data | manual)
  + reference / note, cancel / expire reason. Bi-temporal; RLS.
- Lifecycle (one vocabulary with trades, §2.2): draft → pending_approval →
  approved → sent → awaiting_client_consent (only where the rail needs
  member e-consent) → acknowledged → settled (ACH: provisional until the
  return window passes) → reconciled → closed; plus rejected, returned (ACH
  return code, after settled), cancelled, expired. Transitions enforced in
  the DB (a trigger like execution_instructions'); every transition writes
  a payment_events row (append-only, guard + documented teardown GUC).
- Approval: money movement is an S30 FLOOR duty — new item type
  'payment_instruction' with duties initiate_payment / approve_payment;
  floor: no self-approval, ever, no org may relax it, Super Admin included
  (§2.4). approved_by ∉ maker_ids enforced in the DB too. 2nd Act today has
  one real org-admin checker → a payment waits for a second eligible
  person: by design — document it; do NOT add a bypass.
- Authority (§2.3): BOTH checks in the one payment path — trading_authority
  (SOC5; same-owner = 'limited' suffices, never 'inquiry') for the custody
  question AND rep7b account authority (the source account 'manage' with
  authority to instruct). Reuse services/trading_authority.py and the rep7b
  service; never copy their logic.
- Adapters (§2.5): a PaymentAdapter ABC in a module beside
  services/execution_routers.py (the same shape: name, capabilities,
  send(instruction) → result, status(ref)); registry() picks account →
  custodian → enabled adapter for the org → else manual. Only `manual`
  ships; it "sends" by rendering the payment sheet (an execution_letter
  variant) and moves the instruction to sent when the operator confirms
  the channel. custodian_capabilities gains keys ach / wire / journal /
  check / payment_status_feed per custodian (Altruist: as cash00 1f found;
  Manual: all false except the sheet).
- Bank details: NONE stored — destinations are accounts already in the
  platform (last four only on screens and sheets). Never a full account
  number in any response, log, sheet or AI context (asserted).
- Reconciliation: settled → reconciled by matching the custodian feed
  (services/portfolio_transactions.py: amount, date window, account,
  direction) with reconcile_method custodian_data; else manual with a
  reason and the reconciler (a different person from the sender). Unmatched
  after N business days (setting, default 3) raises one task.
- Disclosures (§2.10): new library keys cash.payments (ACH return risk,
  provisional settlement) — shown on the instruction screen and sheet; and
  cash.sweep_rate (reserved; Item 5 text, default "the cash sweep rate and
  any spread are disclosed in our Form ADV") shown on the Treasury overview
  (cash05) for members. Drafts only — publishing needs a second Super Admin
  (disclosures1).
- Agentic layer: none. The assistant may DRAFT a payment instruction at most
  (S29b class: never submit / approve / send) — register the verbs so.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo 2026-11-09; Task 1 confirms
signatures):
- services/execution.py (create_instructions ~:420, submit ~:571, the sent
  / reconcile / cancel / expire code), execution_routers.py (ABC, registry,
  route_refusal), execution_letter.py (instruction sheet), human_tasks.py,
  bulk_approvals.py, sod.py (gate / enforce / record_decision; floor duty
  handling as S29b Task 0 left it), workflow_autonomy.py (VERB_AUTONOMY),
  trading_authority.py (SOC5), rep7b household_authority /
  account_authority_settings, portfolio_transactions.py, us_business_days.py
  (cash03), the disclosure library (disclosures1).
- cash01–cash05 merged (or merging): obligations, coverage, policies,
  ladder, instrument plan, cash proposals, Treasury screens. Extend the
  Treasury tab; do not add a second one.
- Money movement is excluded from autonomy until counsel signs off (Joe):
  nothing in this sprint sends money by itself.
- Demo rows (demo-olivo) are permanent: the sprint writes NO real or demo
  row. bugfix4: roles from user_roles only. Count audit_log, sod_decisions,
  payment_events and live-written tables BY FIXTURE IDS ONLY.
- Regression: verify_cash05.py and verify_s30.py ONCE each, flat, after
  teardown, VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. No model call. Never print a secret. Never print a
full account number. Never call a real custodian. Bi-temporal rule 3; RLS for
every operation (policy in the same migration as each table). Light / cream
UI. Commit ALL sprint files (excluding sprint_prompts/logs) on the current
branch at the end with message "cash07: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately. Write
docs/cash/CASH07_NOTES.md: execution_instructions' governance pieces to
mirror (constraints, transition trigger, sent / reconcile / cancel code) and
which are SHARED helpers vs copied (prefer extracting a shared helper over
copying); how a floor duty is registered (S29b Task 0); trading_authority
and rep7b reads for an account; custodian_capabilities keys today; the feed
fields for matching; the disclosure key path; reuse inventory.

=== TASK 2: TABLES === payment_instructions + payment_events (append-only
guard, teardown GUC documented), transitions trigger, DB-level approved_by ∉
maker_ids, same-household / same-owner CHECK via trigger, rail CHECK, RLS
every operation; custodian_capabilities rows for the new keys.

=== TASK 3: SERVICE === services/payments.py — draft, submit (task kind
payment_instruction_approval, maker_checker True), approve (sod.gate floor),
send via adapter, consent, acknowledge, settle, return, reconcile (feed |
manual), cancel, expire (a scheduler sweep, switched off). Every transition
audited + payment_events.

=== TASK 4: ADAPTERS === services/payment_adapters.py — PaymentAdapter ABC,
ManualPaymentAdapter (sheet via execution_letter variant), registry by
custodian + org enablement; a NotConnected adapter refusing with reason for
any other custodian.

=== TASK 5: SCREENS === Treasury tab → Payments: list / filters, new
instruction (source / destination pickers limited to same-owner accounts,
rail limited by capabilities, business-day value date, link to an
obligation / proposal), detail with the lifecycle timeline (payment_events),
approve in the existing inbox, sheet download, reconcile (matched feed rows
or manual with reason), cancel. Members: their family's payments read-only
(last four only) with cash.payments text; never another family.

=== TASK 6: PERMISSIONS / AUDIT / AUTONOMY === initiate_payment /
approve_payment mapped to permissions (reuse names where one fits, else add
'initiate_payment' / 'approve_payment'); S29b: assistant verbs draft-only;
every action audited; refusals recorded in sod_decisions.

=== TASK 7: OPERATOR NOTES === docs/cash/CASH_PAYMENTS.md runbook: how the
manual sheet is sent, how to confirm sent / acknowledged / settled, how to
reconcile, what waits for a second checker at 2nd Act. No loader: payments
are never seeded for the demo family by script.

=== TASK 8: REAL PROOF ===
  - Lifecycle: every allowed transition works; every disallowed one is
    refused by the DB trigger (a direct UPDATE), each writing exactly one
    payment_events row; payment_events refuses UPDATE / DELETE without the
    teardown GUC.
  - Approval: maker cannot approve (refused, recorded, nothing changed);
    the floor holds for an org that turned self-approval on for other item
    types and for a Super Admin; a second eligible fixture person approves;
    approved_by ∈ makers refused by the DB itself; inbox + bulk-approve
    respect the floor.
  - Authority: inquiry-only trading authority → refused; source account not
    'manage' → refused; both reasons shown; a destination in another
    household or another owner → refused "third-party payments come later".
  - Rails: a rail the custodian does not declare → refused; value date on a
    holiday → moved / refused per documented rule.
  - Adapter: manual renders a sheet (last four only — scan for any full
    number), confirm → sent; a non-manual custodian → NotConnected with
    reason; no network call (asserted).
  - Reconcile: a fixture feed transaction matches → reconciled
    (custodian_data); none → manual requires a reason and a different
    person than the sender; unmatched after 3 business days → one task.
  - ACH: settled stays provisional through the return window; a return code
    after settled → returned, event recorded.
  - Disclosure: cash.payments and cash.sweep_rate exist as drafts, shown
    where specified.
  - Screens: node render tests (list, new form limits, detail timeline,
    sheet, member read-only).
  - Members only their family; staff only visible families; org isolation
    both directions (rolbypassrls = false); no full account number anywhere
    (responses, sheet, audit details); real and demo rows unchanged.
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_cash05.py and verify_s30.py ONCE each, flat, after
    teardown, VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (cash07), docs/SEGREGATION_OF_DUTIES.md (the floor
item type), docs/cash/CASH_PAYMENTS.md, OUTSTANDING_TODO_LIST (publish the
two disclosure drafts; a second eligible money-movement checker at 2nd
Act); add verify_cash07.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_cash07.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure (TOTAL printed on every
exit path, with the traceback). Set AUTHZ_TEST_MODE=1 before importing the
app. Hydrate secrets from Doppler over HTTPS. No network (assert it).
Fixture orgs (privileged insert path, is_test = true — assert it); time
injected ("today" fixed). Wrap every section so an exception is a [FAIL];
teardown via scripts/_verify_cleanup.py at start and end, by fixture id
(every row a service creates on the verify's behalf, by the id it returns;
tables without org_id passed in the cleanup plan; pass EVERY teardown GUC a
guard on a touched table reads — the new payment_events guard,
sod.teardown, execution.teardown — cash02 fix 1); re-read every touched
table for its exact pre-test count, counting audit_log, sod_decisions,
payment_events and live-written tables BY FIXTURE IDS ONLY. Roles via
user_roles (assert the app decides each). Read UNIQUE / CHECK / FK
constraints and unique indexes from the catalog before seeding (FAIL if none
are read) and compare every seeded literal in a CHECKed column to its CHECK.
Every INSERT's columns checked against docs/schema_snapshot.sql. Reserved
fixture prefixes that match no real row (assert it). Support
--self-checks-only and --cleanup-only. Regression subprocesses get
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip them with
one '[SKIP] nested run' line. If the seed fails, every fixture section is a
[FAIL], never skipped silently. Every one-off section must finish in < 10
minutes (cash03 fix 1 hit the runner's 90-minute cap).

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
  [Y] Task 1 notes + reuse inventory; shared helpers preferred over copies
  [Y] Tables, transition trigger, event log guard, DB-level maker ≠
      checker, same-owner rule, RLS
  [Y] Floor duty: no self-approval for anyone; inbox + bulk respect it
  [Y] Both authority checks; rails by capability; business days
  [Y] Manual adapter + sheet (last four only); others NotConnected; no
      network
  [Y] Reconciliation (feed + manual with reason, different person), ACH
      provisional + returns, unmatched task
  [Y] Disclosure drafts; screens + member read-only
  [Y] Visibility, isolation, own routes, --check CURRENT, no full account
      number, real + demo rows unchanged
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_cash05.py + verify_s30.py ONCE each, flat, after
      teardown, VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
