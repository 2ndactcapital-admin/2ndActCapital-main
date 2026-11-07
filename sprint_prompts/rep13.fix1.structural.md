REP13 FIX 1 — make verify_rep13.py pass. 10 FAILs; the evidence points to
three verify defects (one of them behind 6 FAILs) — but one root must be
PROVEN verify-side, not assumed, because if it is wrong it is a code defect
in five services. 4 tasks. Branch rep13-work. Decide code vs verify with
evidence; never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_rep13 run, 2026-11-07
~13:10 ET: TOTAL 175 PASS, 10 FAIL; regression verify_rep6.py 188 PASS,
0 FAIL (own final TOTAL) — its audit_log / role-seeding fixes held; the
long/short model upload + stored signed weights, approval refused on a
missing borrow rate and on §1259, the manual SELL SHORT chokepoint, the
declaring adapter routing 4 instructions, a cover larger than the open short
refused, the two-sided harvest with substitute XS, node tests — all PASSED;
keep them):

ROOT A — direct service calls on a raw pool connection (6 FAILs). Each of
these opens `(await get_pool()).acquire()` and calls a service inside
`with _ctx(ORG_A):`:
  - 1172 LS.borrow_rates_for(...) → "[FAIL] org A reads its OWN SC rate
    (never org B's 999), the platform SD rate, and NO rate for SE — [(SD,
    300, 'platform')]": the PLATFORM rate is visible, org A's own SC rate
    (50 bps, entered 201 through the API) is not.
  - 1363 AM.load_account(pc, ORG_A, ACC_LS2) → AccountModelNotFound
    ("proposal gates raised").
  - 1487 BK.instruction_sheet_dict(pc, ORG_A, sc.id) → execution.load →
    ExecutionNotFound ("execution raised"), for an instruction the API had
    just APPROVED.
  - 1579 y_tax: pos[str(SC)] KeyError — the positions dict read the same way
    is empty; and "covered 40 at 30 after 400 days … SHORT-TERM — {}".
  Pattern: platform rows visible, every ORG row invisible = the connection
  carries no org RLS context. Hypothesis: `_ctx` sets an application
  contextvar, not the database GUCs (app.current_org_id etc.) that the
  routes set in a transaction — VERIFY defect (VERIFY_RULES: "RLS context on
  every read — the same org context the route sets"; the script's own
  `scoped()` helper does it). PROVE IT before fixing: show what `_ctx` sets,
  and run one of these calls under the route's real context (the scoped
  helper / the same transaction + set_config the router uses) — the rows
  must appear. If they still do not appear, or the services are documented
  to set their own org context and do not, it is CODE — fix the services
  and say which. Then rewrite every direct-service read in the verify to use
  the route's context, and re-check the dependent checks (gates, execution
  order sheet "SELL SHORT" / "OPENING a short sale", the 400-day cover
  short-term, the harvest re-establishment lots).

ROOT B — upload duplicate detection (4 FAILs): "[FAIL] a LONG-ONLY model
with a negative weight is refused (weight_negative, with its row) — 409
{"detail": "this upload is duplicate — upload the file again to
continue"}", the same 409 on "an excluded short" and "a long/short model is
named 'Holdings of …'", and "every refused upload is logged 'rejected' —
rep13_bad2.csv 'duplicate'". The verify uploads M1_ROWS — the same rows as
the valid 130/30 model it uploaded first — in rep13_neg.csv, in the
"excluded short" case and in rep13_name.csv, so rep3's duplicate-content
check answers 409 before validation runs. VERIFY defect (the fixture tests
a state the duplicate guard intercepts): give each of those files distinct
content that still exercises exactly its rule (e.g. vary one long weight
within tolerance, or add a harmless distinct name), so validation is what
refuses; keep every expected code and the 'rejected' log assertion. State
in the discovery doc that duplicate detection precedes validation (by
design, rep3) — do not change that order.

ROOT C — render-test count (1 FAIL): "[FAIL] the 8 rep13 render tests ran
and passed — 12". The regex at verify_rep13.py ~1657 matches test titles
by prefix ("the panel", "a view-only", …) across the WHOLE node suite, so
other files' tests with the same prefixes are counted. VERIFY defect:
count the tests of the rep13 test file only (run it by path, or match its
file / suite name), and still assert exactly 8 and no "not ok".

- WSL memory is limited: do NOT run verify_rep13.py, verify_rep6.py or npm
  run build; --self-checks-only and one-off scripts MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never write a
real or demo-olivo row. Commit on rep13-work with message "rep13 fix 1:
<one line per fix, code vs verify>".

=== TASK 1: DISCOVER === A (what _ctx sets; the same call under the route's
context — before / after row counts), B (the duplicate guard's key and
order), C (the matched titles).
=== TASK 2: FIX === per the evidence: the verify's context for every direct
service read (or the services, if A proves CODE); B's distinct fixtures; C's
file-scoped count.
=== TASK 3: PROVE === one-off on fixtures: org A's own SC rate (50, org) and
the platform SD rate, never org B's 999, no SE; load_account finds ACC_LS2
and the §1259 read covers the held-away account; the order sheet prints SELL
SHORT / OPENING a short sale; a cover after 400 days is short-term with the
hand-computed $400; the harvest re-establishment lots; the three uploads
refused 422 with their codes and logged 'rejected'; exactly 8 rep13 render
tests. verify_rep13 --self-checks-only → 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under rep13.
