CASH03 FIX 1 — make verify_cash03.py pass. 7 FAILs, ONE cause: the seed's
security-master check failed, so the six fixture sections (providers,
security master, ladder, after-tax, plan (db), app + visibility) were gated
off and never ran. Find whether the N-MFP → security master load is wrong
(CODE) or the check's expectation is wrong (VERIFY), with evidence; then run
all six sections one-off and fix whatever they find, this sprint. 4 tasks.
Branch cash03-work. Never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_cash03 run, 2026-11-09 ~03:15–
04:15 UTC: TOTAL 130 PASS, 7 FAIL; the loaders' --dry-run (signatures, parsed
output, nothing written), the catalog reads, every real-row-unchanged check
(security master 15,999 rows, index levels, tax_profiles,
account_balances_daily: count + checksum identical), teardown and the
regression verify_cash02.py 166 PASS 0 FAIL (own final TOTAL) all PASSED —
keep them):
- FAIL 1 (verify_cash03.py, the seed's security-master check): "the fixture
  Treasury issues (5) and N-MFP classes (4) enter the security master
  through Matcher; their yields (6 auction points, 4 class yields + WAM /
  WAL) through upsert_index_levels". Result: Treasury tf = {records 5, new
  5, identifiers_added 10, attributes_written 30} then 6 — the Treasury half
  looks right. N-MFP nm = {records 1, new 1, identifiers_added 2,
  attributes_written 3} then 10 — ONE security entered where the check
  expects FOUR share classes. The --dry-run output in the same run listed
  class-level rows (C990030002 "Government Cash Fund — Retail" 7-day net
  4.5000%, C990030003 "— Premier" 4.6000%, …) and series-level rows
  (S990030003 "Tax-Exempt Reserves", WAM 19 / WAL 22). Decide with
  evidence which is right for the platform:
    (a) a money-market fund is held and priced per SHARE CLASS (each class
        has its own CUSIP / ticker / 7-day yield) — then the extractor /
        Matcher feed must emit one security per class: CODE defect in the
        N-MFP → security master mapping (fix it; series facts — WAM / WAL —
        attach to every class of the series, or to a series record the
        classes link to, per the security master's existing model for fund
        families);
    (b) the security master already models funds at another level by
        design (show the existing rule, e.g. secmaster2's fund handling) —
        then the check's expectation is wrong: VERIFY defect, and state how
        the ladder / instrument plan then resolves a class's yield.
  Print, for the fixture filing: every class and series the parser emits,
  what reached Matcher, and what the master holds afterwards.
  Also confirm the "6" and "10" yield-point counts match what the check
  expects (6 auction points; 4 class yields + WAM / WAL per series) and say
  which number the check compared.
- FAILs 2–7 "<section>: not run — the schema read or the seed failed" —
  follow from FAIL 1. After the fix every one must run; run each one-off on
  fixtures until it passes and list them with their PASS counts.
- The fixture CUSIP / series prefixes (C99003…, S99003…) match no real row
  (already asserted) — keep it so; never write a real security.
- WSL memory is limited: do NOT run the full verify_cash03.py,
  verify_cash02.py or npm run build. --self-checks-only, --cleanup-only and
  one-off sections MAY be run — and here you SHOULD run each fixture section
  one-off on fixtures until it passes.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never write a
real row; providers stay switched off. Commit on cash03-work with message
"cash03 fix 1: <one line per fix, code vs verify>".

=== TASK 1: DISCOVER === parser output vs Matcher input vs master contents
for the fixture N-MFP filing; the security master's existing model for fund
share classes; the decision (a) or (b) with evidence.
=== TASK 2: FIX === per the evidence, plus anything the six sections find
(code or verify, each named).
=== TASK 3: PROVE === seed one-off → the security-master check PASSES;
each of the six sections one-off → PASS (list each with PASS count);
--cleanup-only → zero fixture rows (security master by fixture prefixes,
index levels, plans, audit by fixture ids); verify_cash03 --self-checks-only
→ 0 FAIL; real security master and index levels unchanged (count +
checksum).
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under cash03 listing
every fix (code vs verify).
