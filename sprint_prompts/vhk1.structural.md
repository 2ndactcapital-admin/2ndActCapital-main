VHK1 — verify housekeeping: make every verify in scripts/regression_set.txt
immune to later sprints (route counts, changed-line guards and other
"since a base commit" checks end at the sprint's OWN merge commit, not the
working tree), empty-data checks report a [FIND] instead of failing,
expected dates are computed at the call, every regression-set script uses the
fast scoped cleanup with --cleanup-only, and a --self-checks-only mode lets
those git / route checks be proven in seconds without a database. No
application code changes. 7 tasks + verification.

Context: in two weeks, nine fix rounds were spent on checks that went stale
because a LATER sprint added routes or edited an older verify script, or
because test data was cleaned up — never because the checked feature broke:
verify_rep6's "31 new routes" (failed after rep7, rep7b, lotmethod,
dealroom3, rep8 — fixed on rep8-work by counting rep6's routes from its own
merge ead5a7f); verify_rep7's route checks (rep7b fix 1 patched by exclusion
list); verify_rep6's and verify_rep7b's changed-line guards on verify_rep5 /
verify_rep7; verify_rep7b's "existing accounts / held-away drafts" checks
that failed once leftovers were removed (fixed in rep9 fix 1 → [FIND]); a
TODAY fixed at script start (verify_rep7, midnight run). Joe wants this
class of failure gone.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main after the rep9 merge; Task 1
confirms):
- Scripts using BEFORE_REF: verify_authz1, bugfix1, dealroom1, dealroom2,
  dealroom3, lotmethod, marketdata1, rep1–rep9, rep7b, usermgmt1.
- Scripts with a "new routes" count: bugfix1, dealroom1–3, lotmethod,
  mkt04a, rep1–rep9, rep7b.
- Scripts with `TODAY = date.today()` at module level: verify_rep2, rep5,
  rep6, rep7 (rep7 already fixed at line 1408 for one check only).
- scripts/regression_set.txt lists 27 scripts; those NOT yet on
  scripts/_verify_cleanup.py: verify_authz1, usermgmt1, navregroup,
  bugfix1, dealroom1, bulkfetch, bulkfetchui, bulkfetchui2, bulkfetchui3,
  edgarpipelinea, rep1, rep3, rep4.
- verify_rep6's fixed pattern (rep8-work, 694ceb0): `rep6_done =
  gates(json.loads(git_show("ead5a7f", "docs/AUTHZ1_ROUTES.json")))`; new =
  routes in rep6_done not in BEFORE_REF; then prove each still exists today
  with the same gate.
- Merge commits on main name each sprint ("merge rep6", "merge rep7b",
  "merge lotmethod", …); `git log --merges --format='%h %s' main` lists them.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any full verify script (the new --self-checks-only and
  --cleanup-only modes MAY be run synchronously — they are quick and touch
  no data beyond each script's own fixtures).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one; run every command synchronously in the
foreground. No network calls. Never print a secret. Change ONLY verify
scripts, scripts/_verify_cleanup.py, a new shared helper, docs and
scripts/regression_set.txt — no application code, no schema. NEVER weaken an
assertion about the feature under test; a self-check may change only its
end point (the sprint's own merge) or its empty-data handling. Commit ALL
changed files on the current branch at the end with message "vhk1: verify
self-checks pinned to each sprint's own merge; empty-data FINDs; dates at the
call; fast cleanup + --self-checks-only across the regression set".

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Every check in every regression-set script (and the BEFORE_REF / new-
      routes lists above) that compares something against a base commit and
      the WORKING TREE: route counts, gate-unchanged checks, changed-line
      guards on other scripts, file-unchanged checks. Table: script, line,
      what it compares, which later sprint broke or will break it.
  1b. Each sprint's merge commit on main (git log --merges); for a sprint not
      yet merged when its script runs on its own branch, the end point is
      HEAD (see Task 2).
  1c. Checks that require non-empty REAL data (existing accounts, existing
      instructions, existing deals …) and fail when none exist.
  1d. Module-level TODAY / NOW used in expectations.

=== TASK 2: SHARED HELPER (scripts/_verify_self.py) ===
  - sprint_end_ref(sprint_key): the sprint's merge commit on main if it
    exists, else HEAD (the sprint is still on its branch); stated in the
    check's detail.
  - own_routes(before_ref, sprint_key): routes added between BEFORE_REF and
    the sprint's end ref, from docs/AUTHZ1_ROUTES.json at both; plus
    still_gated(routes): each exists today with the same gate.
  - own_lines(path, before_ref, sprint_key): a changed-line guard over
    another script compares BEFORE_REF → the sprint's end ref, never the
    working tree (later sprints may edit that script freely; their own
    guards cover their edits).
  - when_empty(rows, label): returns a [FIND] ("none exist today") instead
    of a FAIL when the real-data set is empty; asserts the rule for every row
    when rows exist.

=== TASK 3: PIN EVERY SELF-CHECK ===
Rewrite each check from 1a with the helper: same assertion about the
sprint's own routes / lines, end point = its own merge. Keep the existing
exclusion-list fixes only where the helper makes them unnecessary (remove
them, say which). Rewrite each 1c check with when_empty. Replace every 1d
module-level TODAY in an expectation with a call-time date.

=== TASK 4: --self-checks-only ===
Every regression-set script gains --self-checks-only: runs ONLY its git /
route / changed-line self-checks (no Doppler, no database, no network, no
npm), prints 'TOTAL: N PASS, M FAIL', exits non-zero on failure.

=== TASK 5: FAST CLEANUP ACROSS THE SET ===
The 13 regression-set scripts not on _verify_cleanup.py move their teardown
(and pre-run cleanup) onto it, scoped by their own fixture ids, each with
--cleanup-only. Behaviour otherwise unchanged; no assertion touched. Where a
script's teardown needs an immutability GUC or trigger handling, keep it.

=== TASK 6: PROOF YOU MAY RUN (synchronously, quick) ===
  - python -m py_compile on every changed script.
  - For EVERY regression-set script: --self-checks-only → 0 FAIL; record the
    output in docs/VHK1_SELF_CHECKS.md.
  - For each of the 13 moved scripts: --cleanup-only → exits 0, zero fixture
    rows, its time printed; record in the same doc.
  - A simulation, NEVER in the operator's working tree (it holds your
    uncommitted work): first commit your work, then `git worktree add` a
    temporary worktree at that commit, make a throwaway commit THERE that
    adds a fake route to docs/AUTHZ1_ROUTES.json and a line to
    verify_rep5.py, run --self-checks-only for every script from the
    worktree → still 0 FAIL (proving later sprints no longer break them);
    then `git worktree remove --force` it, delete its branch, and confirm
    `git status` in the real tree is unchanged. Never run git reset --hard.

=== TASK 7: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md; SPRINT_WORKFLOW_STANDARD-style rule added to
docs/VERIFY_RULES.md: "self-checks end at the sprint's own merge; empty real
data is a FIND; dates at the call; fast scoped cleanup; --self-checks-only
and --cleanup-only in every script"; add verify_vhk1.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_vhk1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. No network, no npm.
It: (1) statically scans every regression-set script — no "since BEFORE_REF"
comparison against the working tree remains (AST / text scan), no
module-level TODAY used in an expectation, every script supports
--self-checks-only and --cleanup-only, every script imports _verify_cleanup;
(2) runs every script's --self-checks-only as a synchronous subprocess
(timeout 300 s each) → 0 FAIL; (3) re-runs the Task 6 simulation in a
temporary git worktree (never the operator's working tree), then removes the
worktree; (4) runs every --cleanup-only (timeout 900 s each) → zero fixture
rows. With VERIFY_NESTED=1 it skips (2)–(4)'s subprocesses with one
'[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top of the file.
  - A failing check's detail prints the script, line and output.
  - Never touch real rows of any kind; the worktree is temporary and removed.

Assertions:
  [Y] Task 1 table 1a–1d
  [Y] No working-tree "since base" self-check remains in any regression-set
      script; every one ends at its sprint's own merge (or HEAD on branch)
  [Y] Empty real-data checks report a FIND; rules still asserted per row
  [Y] No module-level TODAY in an expectation
  [Y] --self-checks-only in every script → 0 FAIL
  [Y] Simulated later sprint (new route + edited older verify) → still 0 FAIL
  [Y] All 13 scripts moved to _verify_cleanup.py; --cleanup-only → zero rows
  [Y] No application code or schema changed (git diff --stat scope check)
