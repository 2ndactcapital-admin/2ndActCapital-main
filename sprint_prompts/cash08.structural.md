CASH08 — PAYEES, BANK-DETAIL CONTROLS, SLOAs and VENDOR INVOICES: a payee
registry for each household (vendors, charities, people, outside entities),
bank details held only as the custodian / adapter's reference + the last
four digits, a governed change-of-bank-details flow (the classic fraud
vector), Standing Letters of Authorization linked from Chancery, and
vendor-invoice intake through Chancery creating a cash obligation (cash01's
vendor_invoice type). THIRD-PARTY PAYMENTS STAY OFF: a payment instruction
to a payee is refused until an org-level switch is turned on, and that switch
cannot be turned on in this sprint (counsel first — Joe). 9 tasks +
verification. Branch cash08-work.

Design: docs/cash/ (ADDENDUM v1.1 §5–§6 payees, vendor invoices, SLOA;
CASH_TRACK_PLAN_v1.2 §2.4 floor duties create_payee / change bank details /
approve_payee / first payment to a new payee, §2.7 bank details, §2.10
Chancery; CASH_PAYMENTS.md, CASH_RULES.md, CASH07_NOTES.md) — READ THEM
FIRST; earlier decisions bind. DECIDED (Joe, 2026-11-08): reuse existing
constructs; the payee registry is the one new construct already justified
(v1.2 header); anything else new: named and justified in
docs/cash/CASH08_NOTES.md.

THE CONCEPTS (decided, do not re-decide):
- Payee: household, kind (vendor | charity | person | outside_entity),
  display name, legal name, tax id NOT stored (a W-9 / document in Chancery
  instead), contact, bank: bank name, account type, last four ONLY, the
  adapter / custodian payee reference (free text the operator copies from
  the custodian's standing instruction), SLOA document link (Chancery), and
  status draft / pending_approval / active / suspended / retired.
  Bi-temporal; RLS. NEVER a full account or routing number anywhere —
  inputs that look like one (> 4 digits in the last-four field, any 9-digit
  ABA-like string in notes / reference) are rejected with a reason.
- Floor duties (S30, no self-approval, Super Admin included, no org may
  relax): create_payee / approve_payee; change_bank_details /
  approve_bank_change (a change SUSPENDS the payee until approved and
  forces a call-back attestation: the approver records who they called, at
  which number ON FILE (not from the request), and when — a required
  field; the request's own contact details are never accepted as the
  call-back number); first_payment_to_new_payee (the first instruction to
  a payee needs a checker who is neither the payee's creator nor its
  approver).
- SLOA: a Chancery document (kind 'sloa', from S26 intake) linked to a
  payee + source account(s); expiry / revocation tracked; a payee whose
  SLOA is missing, expired or revoked is flagged and (when third-party
  payments are later enabled) blocks payment.
- Third-party switch: an org setting 'cash.third_party_payments' (default
  off); its change is a floor item type requiring TWO Super Admins and a
  recorded counsel sign-off document (Chancery) — this sprint builds the
  gate and proves it refuses; nobody turns it on. While off, cash07's
  same-owner rule keeps refusing any payee destination ("third-party
  payments are not enabled").
- Vendor invoices: an invoice uploaded to Chancery (S26 intake; the
  classifier's invoice type, or a manual tag) → a staff "record invoice"
  step creates a cash_obligations row (source_type vendor_invoice, amount,
  due date, payee link, document link) — firm certainty; it appears in
  coverage (cash01) immediately; duplicate detection (same payee + amount +
  invoice number) refuses a second record. No auto-extraction of amounts by
  a model in this sprint (no model call); amounts typed by staff.
- Disclosure: cash.third_party (default text on third-party payment risks
  and custody) — draft only, shown on the payee screen.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo 2026-11-09; Task 1 confirms
signatures):
- Chancery: services/chancery_intake.py, document_classifier.py,
  document_linkage.py, document_review.py, portfolio_documents.py,
  chancery_workflow_bridge.py.
- cash07 + cash06 merged before this sprint: payment_instructions,
  payment_events, payments.py, payment_adapters.py, cash rules; the
  payment_instruction and cash_rule floor item types. cash01:
  cash_obligations with source_type vendor_invoice already allowed.
- sod.py floor handling (S29b Task 0); human_tasks / bulk_approvals; the
  disclosure library; visibility1 / bugfix2.
- Memory / platform rule: never store card or bank account numbers.
- Demo rows (demo-olivo) are permanent: the sprint writes NO real or demo
  row. bugfix4: roles from user_roles only. Count audit_log, sod_decisions,
  payment_events and live-written tables BY FIXTURE IDS ONLY.
- Regression: verify_cash06.py and verify_cash01.py ONCE each, flat, after
  teardown, VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. No network. No model call. Never print a secret or a full
account / routing number. Never call a real custodian. Never turn on
third-party payments. Bi-temporal rule 3; RLS for every operation. Light /
cream UI. Commit ALL sprint files (excluding sprint_prompts/logs) on the
current branch at the end with message "cash08: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately. Write
docs/cash/CASH08_NOTES.md: Chancery document kinds (is there 'sloa' /
'invoice'? the add path), document_linkage for a payee / obligation target,
how cash07 decides same-owner (where the third-party switch plugs in), the
S30 path for a two-Super-Admin floor item, org settings storage; reuse
inventory.

=== TASK 2: TABLES === public.cash_payees (+ its RLS, bi-temporal, a CHECK
that last_four is exactly 4 digits, a trigger rejecting digit runs > 4 in
reference / notes), payee ↔ SLOA links via document_linkage (no new link
table unless justified), the org setting.

=== TASK 3: SERVICE === services/cash_payees.py — create / submit / approve,
change bank details (suspend → call-back attestation → approve), suspend /
retire, SLOA attach / expire / revoke, flags; floor gates through sod.py;
every action audited.

=== TASK 4: THIRD-PARTY GATE === the switch's floor item type (two Super
Admins + counsel document required); cash07's destination check consults
it; while off, a payee destination → refused with the reason; first-payment
rule implemented now (dormant while off) and unit-proved.

=== TASK 5: VENDOR INVOICES === "record invoice" from a Chancery document →
cash_obligations vendor_invoice (payee + document linked); duplicate
refusal; appears in coverage.

=== TASK 6: SCREENS === Treasury tab → Payees: list (status, SLOA state,
flags), detail (last four only, reference, SLOA, history), create, change
bank details with the call-back attestation form, approve in the inbox;
Invoices: Chancery invoices awaiting recording, record form. Members: their
family's payees read-only (name, kind, last four) with cash.third_party
text; never another family.

=== TASK 7: OPERATOR NOTES === docs/cash/CASH_PAYEES.md: payee onboarding,
the bank-change call-back procedure, SLOA handling, what enabling
third-party payments will require (counsel document, two Super Admins).

=== TASK 8: REAL PROOF ===
  - Payees: create → the creator cannot approve (refused, recorded); a
    second eligible person approves; Super Admin cannot self-approve.
  - Bank change: suspends the payee; approval without a call-back
    attestation → refused; a call-back number that matches the REQUEST's
    contact (not the one on file) → refused; the requester cannot approve;
    proper attestation by another person → active again; history kept
    bi-temporally.
  - Numbers: last_four with 5 digits, a 9-digit run in reference / notes, a
    full account number in any field → refused by the DB; no response,
    audit detail or log line contains a digit run > 4 from a fixture
    (scan).
  - SLOA: attach / expire / revoke flags the payee; expired SLOA flagged.
  - Third-party gate: off by default; a payment instruction to a payee →
    refused "third-party payments are not enabled"; turning it on with one
    Super Admin, or without the counsel document → refused; the switch is
    still off at the end (asserted).
  - First-payment rule: unit-proved on the dormant path (checker ≠ payee
    creator ≠ payee approver).
  - Invoices: record from a fixture Chancery invoice → obligation in
    coverage (cash01 read) with payee + document links; duplicate refused.
  - Screens: node render tests (payee list / detail / bank-change form /
    invoices / member read-only).
  - Members only their family; staff only visible families; org isolation
    both directions (rolbypassrls = false); real and demo rows unchanged;
    no network; no model call.
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_cash06.py and verify_cash01.py ONCE each, flat,
    after teardown, VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (cash08), docs/SEGREGATION_OF_DUTIES.md (the new
floor items), docs/cash/CASH_PAYEES.md, OUTSTANDING_TODO_LIST (publish
cash.third_party; counsel review before the switch); add verify_cash08.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_cash08.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure (TOTAL printed on every
exit path, with the traceback). Set AUTHZ_TEST_MODE=1 before importing the
app. Hydrate secrets from Doppler over HTTPS. No network (assert it).
Fixture orgs (privileged insert path, is_test = true — assert it); time
injected ("today" fixed). Wrap every section so an exception is a [FAIL];
teardown via scripts/_verify_cleanup.py at start and end, by fixture id
(every row a service creates on the verify's behalf, by the id it returns —
payees, links, documents, obligations, instructions, tasks, sod_decisions;
tables without org_id passed in the cleanup plan; pass EVERY teardown GUC a
guard on a touched table reads); re-read every touched table for its exact
pre-test count, counting audit_log, sod_decisions, payment_events and
live-written tables BY FIXTURE IDS ONLY. Roles via user_roles (assert the
app decides each). Read UNIQUE / CHECK / FK constraints and unique indexes
from the catalog before seeding (FAIL if none are read) and compare every
seeded literal in a CHECKed column to its CHECK. Every INSERT's columns
checked against docs/schema_snapshot.sql. Text ids compared as text, uuids
as uuid. Reserved fixture prefixes that match no real row (assert it).
Fixture "bank numbers" are obviously fake and never exceed 4 digits except
in the refusal checks. Support --self-checks-only and --cleanup-only.
Regression subprocesses get VERIFY_NESTED=1, after teardown, 2700 s; with
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
  [Y] Task 1 notes + reuse inventory
  [Y] Payee registry: last four + reference only; DB refuses longer digit
      runs; bi-temporal; RLS
  [Y] Floor duties: create / approve payee, bank change with call-back
      attestation, first-payment rule
  [Y] SLOA linkage + flags
  [Y] Third-party switch: off, two Super Admins + counsel doc required,
      still off at the end
  [Y] Vendor invoices → obligations in coverage; duplicates refused
  [Y] Screens + member read-only with library text
  [Y] Visibility, isolation, own routes, --check CURRENT, no number leaks,
      real + demo rows unchanged
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_cash06.py + verify_cash01.py ONCE each, flat, after
      teardown, VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
