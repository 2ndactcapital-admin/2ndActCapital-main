PORTENTRY2 FIX 1 — make verify_portentry2.py pass and make its regression
gate trustworthy. 1 FAIL (a wrong expectation) + 1 hidden defect found while
reading the log (the nested-regression result is parsed from the wrong line in
12 verify scripts). 4 tasks. Branch portentry2-work. Decide code vs verify
with evidence; never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_portentry2 run, 2026-11-05
~06:05 ET: TOTAL 147 PASS, 1 FAIL; everything else — entity members on an
existing household, entity account owners, account fields, DVP required
fields, permission-true UI + server refusal, ownership views, picker, dates,
staff security search, demo rows untouched — PASSED; keep it):
- FAIL: "owner candidates = the legal entities a member owns that are not
  members (llc2 and own1, both owned by the trust)" — got {…550036 (llc2),
  …550040 (child), …550051 (own1)}. The seed makes the LP (a member, added
  just above) own `child` 60% (verify_portentry2.py ~:501). By the rule the
  check itself states, `child` IS a candidate. VERIFY defect: derive the
  expected set from the seeded ownership rows (every legal entity owned by
  any member that is not itself a member), not a hand-written pair; keep a
  negative case (an entity owned only by a NON-member is not a candidate —
  add one if the seed has none).
- HIDDEN DEFECT: the nested regression line printed
  "[PASS] verify_portentry1.py: 0 FAIL — TOTAL: 23 PASS, 0 FAIL", but a full
  verify_portentry1 run is 167 checks. portentry1's output CONTAINS other
  scripts' totals in its detail lines (its _verify_self section prints
  "verify_perms1.py --self-checks-only: 0 FAIL — TOTAL: 23 PASS, 0 FAIL"),
  and the regression parser uses re.search(r"TOTAL: (\d+) PASS, (\d+) FAIL",
  out) — the FIRST match anywhere, not the script's own final line. So the
  nested gate can pass while the nested script failed. The same parser is in
  12 scripts: verify_authz1, bugfix1, bugfix2, crmdetail1, goldset, notefix1,
  notefix2, perms1, portentry1, portentry2, staffroles1, usermgmt1 (and check
  scripts/run_regressions.sh). VERIFY defect: parse the LAST line that
  STARTS with "TOTAL: " (anchored ^TOTAL: , multiline), require the process
  exit code to agree, and fail if no such line exists — one shared helper in
  apps/api/scripts/ used by all of them.
- WSL memory is limited: do NOT run verify_portentry2.py,
  verify_portentry1.py or npm run build; --self-checks-only and one-off
  scripts MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never touch a
demo-olivo or real row. Commit on portentry2-work with message
"portentry2 fix 1: <one line per fix>".

=== TASK 1: DISCOVER === confirm both facts (the seed rows; every script and
the shell runner using a first-match TOTAL parse).
=== TASK 2: FIX === the expectation (derived + negative case) and the shared
TOTAL parser in all scripts that parse a nested TOTAL.
=== TASK 3: PROVE === one-off: owner_candidates on the fixture equals the
derived set and excludes the non-member-owned entity; the parser, fed a
sample output whose detail lines contain other totals, returns the FINAL
line's numbers and fails when the final TOTAL is missing or the exit code
disagrees; every changed script's --self-checks-only → 0 FAIL (except
previewperf / previewperffix1 / vhk2's known pre-existing FAILs — unchanged).
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under portentry2; a
rule in docs/VERIFY_RULES.md: "parse a nested script's own final TOTAL line,
never the first match".
