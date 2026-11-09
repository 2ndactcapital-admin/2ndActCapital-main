CASH04 FIX 1 — make verify_cash04.py pass. At least 5 FAILs: a verify crash
that stopped the approval section (so no fixture instruction was ever
created — three later checks then saw nothing), and a 500 on org B's own
household (a CODE defect: a 500 is never valid). 4 tasks. Branch
cash04-work. Decide code vs verify with evidence; never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_cash04 run, 2026-11-09 ~12:20–
12:40 UTC; the builder, waterfall, screens / render tests and most other
sections PASSED — keep them). FIRST read the final lines of
sprint_prompts/logs/cash04_run.out: if the nested regressions
(verify_cash03.py, verify_rep13.py) or any section after line 274 also
failed, add those FAILs to this fix with the same code-vs-verify rule.
- FAIL A (line 198) "approval raised — UndefinedFunctionError: operator does
  not exist: text = uuid" at verify_cash04.py:1272 in y_approval:
  `SELECT count(*) FROM sod_decisions WHERE item_id = $1::uuid AND outcome =
  'refused'`. sod_decisions.item_id is TEXT (S30). VERIFY defect: compare as
  text ($1::text, passing str(pid)); grep the verify for every other
  sod_decisions / audit / payment-style query casting item_id or other text
  ids to uuid and fix them the same way (the s30 fix 1 rule: a parameter
  used in two contexts gets an explicit cast of the RIGHT type). Everything
  after line 1272 in y_approval never ran: run it one-off after the fix and
  fix whatever it finds (code or verify), this sprint.
- FAILs B–D follow from A unless the one-off proves otherwise:
  B (line 266) "lines carry sleeve / reason / warning, a LINK to the
    existing proposal screen and to each instruction" — no instruction
    existed to link to;
  C (line 273) "RLS isolation BOTH directions … — A→B 0 B→A 0 own 6/0
    instr 0" — org B had 0 own proposals and nobody had instructions: the
    "each reads its own" half cannot pass on empty sets (correct: an empty
    list never passes);
  D (line 274) "NOTHING was routed to a real custodian … — [] []" — empty
    lists, same rule.
  After A is fixed, each must pass on REAL fixture rows; if one still fails,
  find the code or verify cause and fix it.
- FAIL E (line 272) "org B cannot reach org A's household (404); org B's own
  household gets its proposal — 404 500 {"raw": "Internal Server Error"}".
  The cross-org half is right (404); org B's OWN household answered 500.
  CODE defect: reproduce the call as org B's fixture user, print the server
  traceback, fix the cause (likely a household with no active policy /
  plan / proposal, or an org-scoped read missing its RLS context) — the
  route must answer 200 with the proposal, or 200 with a clear empty state
  ("no active cash policy — nothing to propose"), never 500. Grep every
  cash route (routers/cash.py, cash01–cash04) for the same pattern and
  fix them alike; add a check that each cash GET answers 200 for a fixture
  household with no policy.
- WSL memory is limited: do NOT run the full verify_cash04.py,
  verify_cash03.py, verify_rep13.py or npm run build. --self-checks-only,
  --cleanup-only and one-off sections MAY be run (give each `timeout 600`;
  the runner kills a leg at 90 minutes — cash03 fix 1).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never write a
real row; never route to a real custodian. Commit on cash04-work with message
"cash04 fix 1: <one line per fix, code vs verify>".

=== TASK 1: DISCOVER === A (every wrong cast); E (the traceback, the cause,
every cash route with the same pattern); any extra FAILs from the end of the
run log.
=== TASK 2: FIX === per the evidence.
=== TASK 3: PROVE === y_approval one-off → every check runs and passes
(maker refused + recorded, second person approves, instructions only via
create_instructions / submit, routed manual, sheet); then the sections with
B, C, D, E one-off → PASS on non-empty fixture data (list each with PASS
count); the no-policy household → 200 empty state on every cash GET;
--cleanup-only → zero fixture rows; verify_cash04 --self-checks-only →
0 FAIL; nothing routed to a real custodian.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under cash04 listing
every fix (code vs verify).
