REP7B FIX 1 — make verify_rep7b.py pass. Most of the 12 FAILs cascade from
three verify-script defects; two are verify_rep7 self-checks made stale by
rep7b's new routes. 5 tasks + verification. Branch rep7b-work (rep7b =
61b521f). Fix the code where the code is wrong; fix the verify where the
verify is wrong — and say which, with evidence, for every FAIL.

Context: verify_rep7b.py ran (run_sprint Step 3, 2026-10-27): TOTAL 104 PASS,
12 FAIL. Schema, gates, routes, node tests, npm build, isolation of the
authority tables, and teardown passed. Keep all of it.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (from that run's output):
- FAIL A "authority changes raised — TypeError: cannot unpack non-iterable
  async_generator object" at verify_rep7b.py:976
  `e_int, e_int2, e_adv = (await effective_of(a) for a in (…))` — a generator
  expression with await is an async generator; use sequential awaits or a
  list. The rest of y_changes never ran, so ACCT_DVP was never moved to
  managed + other_custodian (FAIL I shows …7b304 still read_only / outside).
  These FAILs are very likely cascades of A — re-check each after fixing A:
    F "managed at another custodian: the join's transition proposal …
      staleness applies" → 409 404 {}
    G "approval waits for the stale-data confirmation; … every trade is a
      manual instruction for a broker with its DVP settlement row" → 404 422
      404 [] 0
    H "each settlement carries the receiving custodian's details and settles
      T+1 (2026-10-28, weekends only)" → []
    "managed at another custodian raised — ExecutionNotFound: instruction not
      found" (y_dvp :1196, iid from an empty instruction list)
    I "four live cells set up through approved changes" → b304 ('read_only',
      'outside') where ('managed', 'other_custodian') was expected
    J "every path … allowed / refused exactly as the matrix" → every
      ('managed', 'other_custodian') path refused (cell never set up)
    K "RLS: each org sees its own authority / settlement rows (non-empty)" →
      instruction_settlements (0, 0) — no settlement row was ever written.
- FAIL B "submitted, the maker refused, a checker approves → one instruction
  for the other adviser …" → 200 403 409 [] (verify_rep7b.py ≈1071–1082): the
  CHECKER's approve returned 409 and no instruction exists; then "advise
  raised — ExecutionNotFound" (:1088) is the cascade (iid from []). The
  detail does not print the 409 body — print it. Likely cause: ACCT_ADV has
  custody 'outside' and NO approved held-away data, and rep7's rule is "no
  data is stale" → approval correctly waits for confirm_held_away_data. If
  so the CODE is right and the FIXTURE is wrong: give ACCT_ADV an approved
  held-away import dated inside stale_after_days (or complete the confirm
  task) before approving — never relax the staleness rule. If the 409 is
  something else, find it and decide code vs verify with evidence.
- FAIL C "_apply_rep7b.py --migrate: every existing account UNCHANGED on every
  path" → three lines, all 'UNCHANGED', all 'test org'. The migration itself
  is fine (Task 1 of the sprint found the 5 "real" accounts are in leftover
  REP2 test orgs; only 3 exist now). The check's expectation is wrong (it
  likely requires ≥1 non-test account or exactly 5). Correct it: every
  account the migration reports (test orgs included) is UNCHANGED, the count
  matches SELECT count(*) of accounts at that moment, and zero non-test
  accounts is reported as a [FIND], not a FAIL.
- FAIL D regression verify_rep7.py → 176 PASS, 2 FAIL, both verify_rep7
  SELF-checks made stale by rep7b:
    (a) "19 new routes, all under /held-away/… and /xray/…" → 30 20 —
        rep7b added /authority/… routes and POST
        /api/v1/xray/households/{household_id}/one-off;
    (b) "each names its permission …" → the new one-off route
        ('authz manage_proposals') is not in rep7's expected set.
  Fix verify_rep7.py ONLY in those two checks: count rep7's own 19 routes
  (exclude /authority/… and the …/one-off route added by rep7b) and keep
  each rep7 route's permission check exact. Its TODAY fix (line 1408) stays.
  Say which lines changed.
- Design stays as the sprint built it: authority × custody matrix in
  services/authority.py; advise separate from is_discretionary; new
  approve_authority permission; T+1 weekends only (stated); no billing.
- Regression for this fix: verify_rep7b.py nests verify_rep7.py
  (VERIFY_NESTED=1, 2700 s) — unchanged.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never poll in a loop, never start a
script in the background; run every command synchronously in the foreground
and read its output directly. Do NOT run verify_rep7b.py, verify_rep7.py or
npm run build. You MAY run short synchronous checks (python -c imports,
node --test tests/replication/, a one-off script with fake inputs — never on
real households or real accounts). No network calls. Never print a secret or
a full account number. Decimal everywhere. Never relax a staleness, maker-
checker or authority rule to make a check pass. Every failing check's detail
must print the response body / culprit rows. Commit ALL changed files
(excluding sprint_prompts/logs) on rep7b-work at the end with message
"rep7b fix 1: verify async unpack, advise fixture data, migration check,
verify_rep7 route self-checks scoped to rep7".

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. FAIL B: the exact 409 body of the checker's approve (reproduce with a
      one-off script against fake / fixture-free inputs, or read the code
      path: rebalance_proposals.decide → prechecks incl. staleness and the
      authority policy). Code or fixture?
  1b. FAILs F–K: after fixing A, walk y_changes → y_dvp by reading the code:
      will ACCT_DVP reach managed + other_custodian through approved
      changes, and will the DVP flow write instruction_settlements rows? Name
      any further code defect (e.g. route_trade_list / create_instructions
      not writing the settlement row, the join refusing managed +
      other_custodian) and fix it.
  1c. FAIL C: the current check and the migration's report lines.
  1d. FAIL D: the exact verify_rep7.py lines of both checks.

=== TASK 2: FIX verify_rep7b.py (A, B fixture, C, details printed) ===
=== TASK 3: FIX ANY CODE DEFECT 1a / 1b FOUND ===
=== TASK 4: FIX verify_rep7.py's two route self-checks (D) ===
=== TASK 5: UPDATE PROJECT STATUS ===
One UPDATE line under the rep7b entry in docs/PROJECT_STATUS.md naming the
fixes and whether any was a code defect.

=== VERIFICATION ===
verify_rep7b.py covers every fix. Do not weaken any assertion; change an
expectation only where Task 1 proves it wrong, with the evidence. The operator
runs verify_rep7b.py.
