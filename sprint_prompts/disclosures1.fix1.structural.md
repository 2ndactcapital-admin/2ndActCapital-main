DISCLOSURES1 FIX 1 — verify_disclosures1.py crashed before testing anything
(TOTAL 2 PASS, 1 FAIL). Find and fix the crash, make every section run, and
fix whatever those sections then find. 4 tasks. Branch disclosures1-work.
Decide code vs verify with evidence; never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_disclosures1 run, 2026-11-08
~05:10 ET):
- Output: "[PASS] pre-run cleanup: zero fixture rows before seeding", then
  straight to teardown, then "[FAIL] the verify crashed: InterfaceError: the
  server expects 1 argument for this query, 2 were passed" from the
  top-level handler; TOTAL 2 PASS, 1 FAIL. No feature section ran.
- In main_async (~line 1348) the steps after the leftovers check that are
  NOT wrapped in run(...) are `pre = await counts(conn)` and
  `real = await real_prints(conn)` — the crash is in one of them (or a
  helper they call): a query executed with two parameters for one
  placeholder ($1). The exception escaped to the top-level handler, so no
  traceback was printed. VERIFY defect.
- WSL memory is limited: do NOT run the full verify_disclosures1.py,
  verify_rep13.py or npm run build. --self-checks-only, --cleanup-only and
  one-off scripts MAY be run — and here you SHOULD run the verify's own
  sections one-off against the live DB (fixtures only), because the full run
  never reached them.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network (R2 through the fake storage transport). Never print
a secret. Fixtures only; never write a real or demo row. Commit on
disclosures1-work with message "disclosures1 fix 1: <one line per fix, code
vs verify>".

=== TASK 1: DISCOVER === reproduce the crash (counts / real_prints / their
helpers) and print the offending query and its arguments.
=== TASK 2: FIX ===
  (a) the query's placeholders / arguments;
  (b) wrap the pre-run counts and real fingerprints in run(...) (or an
      equivalent) so any future failure there prints a traceback and a named
      [FAIL] — and if the pre-counts fail, every fixture section is still
      SKIPPED with a [FAIL] line, never silently;
  (c) the top-level handler prints the traceback too.
=== TASK 3: PROVE === run the verify's sections one-off on fixtures against
the live DB (seed → every y_* section → teardown) and report each section's
PASS / FAIL. Every FAIL they find: cause and fix (code or verify, with
evidence) in this same sprint — this is the first time those sections run.
Then verify_disclosures1 --self-checks-only and --cleanup-only → 0 FAIL, zero
fixture rows left, real library rows unchanged.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under disclosures1
listing every fix (code vs verify).
