CASH08 FIX 1 — make verify_cash08.py pass. 12 FAILs, three causes: (A) the
bank-detail change request was REFUSED at creation because its contact phone
number tripped the "nine or more digits look like a routing / account
number" guard — so no change was ever pending and nine dependent checks
answered 409; (B) docs/cash/CASH_PAYEES.md is missing required sections;
(C) teardown refused by the custodian_capabilities guard. 4 tasks. Branch
cash08-work. Never weaken an assertion — the no-account-number rule and the
bank-change floor both stay exactly as strict for BANK fields.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_cash08 run, 2026-11-10
~12:56–13:19 UTC: TOTAL 151 PASS, 12 FAIL; payee create / approve, the
floor on payees, numbers refused in bank fields, SLOA, third-party gate,
vendor invoices, screens and the nested regressions PASSED — keep them; read
the end of sprint_prompts/logs/cash08_run.out for anything not listed):
- FAIL A (line 153) "a bank-detail change SUSPENDS the payee … — 422
  bank_change_request_contact: nine or more digits (separators ignored) look
  like a routing or account number — never stored active". The request's
  contact is a PHONE NUMBER (10 digits with separators). The digit-run guard
  was specified for bank fields (last four, payee reference, notes) — a
  phone number in a CONTACT / CALL-BACK field is not a bank number, and the
  call-back procedure REQUIRES phone numbers (the one on file and the one in
  the request, which must not match). CODE defect (scope of the guard):
    - keep the guard (DB trigger + service) on every bank-detail field, the
      reference, notes and free text exactly as strict;
    - phone / contact fields (payee contact on file, the change request's
      contact, the attestation's dialled number) are validated as phone
      numbers instead (NANP / E.164: 10–15 digits, + and separators allowed,
      nothing else), stored as phone numbers, and still never accepted in a
      bank field;
    - a "phone" that is really an account number (e.g. 12+ digits with no
      phone shape, or labelled as an account) stays refused — define the
      rule, document it, test both sides.
  Lines 154–161 and 165 (approval without attestation; call-back equal to
  the request's number; number not on file; refusals audited; requester
  cannot approve; generic inbox cannot approve — "HumanTaskConflict: this
  task is completed, not open"; proper attestation by another person;
  bi-temporal history; rejected change) all follow from A: no pending change
  existed. After the fix EVERY one must pass on a real pending change. For
  159 also confirm which task the check used: it must be the BANK-CHANGE
  approval task, open (the payee-approval task from earlier is legitimately
  completed) — fix the side that is wrong.
- FAIL B (line 268) "docs/cash/CASH_PAYEES.md: onboarding, the call-back
  procedure, SLOAs, what enabling third-party payments requires" — write /
  complete the runbook sections (the check reads them by heading — match
  what it looks for, and make the content real: steps, who does what, which
  floor applies, the two-Super-Admin + counsel-document gate).
- FAIL C (line 284) "teardown raised — InsufficientPrivilegeError: custodian
  capability versions are never deleted" — the custodian_capabilities guard
  (rep2) allows fixture deletes only with its documented teardown GUC (read
  the guard function; verifies that seed capabilities — verify_cash07 —
  pass it). VERIFY: pass that GUC in cleanup (start and end), fixture
  custodian codes only. Because teardown raised, fixture rows may be LEFT
  NOW: first count them by fixture ids / prefixes, then --cleanup-only after
  the fix → zero; real capability rows unchanged (count + checksum).
- WSL memory is limited: do NOT run the full verify_cash08.py,
  verify_cash06.py, verify_cash01.py or npm run build. --self-checks-only,
  --cleanup-only and one-off sections MAY be run (`timeout 600` each; COMMIT
  after each finished section — the runner kills a leg at 90 minutes).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret or a full account / routing
number (fixture phone numbers use the reserved 555-01xx range). Fixtures
only; never write a real row; never turn on third-party payments. Final
commit on cash08-work with message "cash08 fix 1: <one line per fix, code vs
verify>".

=== TASK 1: DISCOVER === leftover fixture rows; the guard's call sites and
which fields it covers; the phone fields in the payee / change / attestation
model; which task check 159 used; the custodian_capabilities teardown GUC.
=== TASK 2: FIX === A (code: guard scope + phone validation, DB and
service), B (docs), C (verify teardown).
=== TASK 3: PROVE === --cleanup-only → zero fixture rows; the bank-change
section one-off → every check passes (list with PASS count); new checks: a
phone in a contact field accepted; the same digits in a bank field, the
reference or notes refused; a 12+-digit non-phone in a contact field
refused; the docs check → PASS; verify_cash08 --self-checks-only → 0 FAIL;
third-party switch still off; real rows unchanged.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under cash08 listing
every fix (code vs verify).
