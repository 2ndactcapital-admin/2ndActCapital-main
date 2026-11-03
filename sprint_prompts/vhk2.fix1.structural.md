VHK2 FIX 1 — make verify_vhk2.py pass on main. Its run on main (after
"merge vhk2" 9dbfee0, local, not pushed): TOTAL 49 PASS, 5 FAIL. Nine of the
ten full re-runs passed (authz1 122, dealroom1 176, bulkfetchui3 113,
edgarpipelinea 105, rep1 136, rep5 168, lotmethod 143, marketdata1 115;
dealroom1 --cleanup-only → zero rows incl. deal_room_activity). Keep all of
it. 4 tasks. Branch vhk2-fix1 from local main (9dbfee0). Decide code vs
verify for each FAIL with evidence; never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (from
sprint_prompts/logs/vhk2_verify_main.out):
- FAILS A–C (scope): "Task 2: no real defect was proven, and VHK2 changed no
  application code, route record or schema … 265f15c → vhk2's own merge
  9dbfee0 on main ('merge vhk2'); 55 files; outside scope:
  ['apps/api/routers/ai_en…" ; "docs/AUTHZ1_ROUTES.json is byte-identical
  across VHK2" ; "apps/web/lib/menuVisibility.mjs is byte-identical across
  VHK2". Cause: aibudget1 was merged to main BEFORE vhk2, so the diff from
  vhk2's base (265f15c) to the vhk2 merge commit includes aibudget1's
  changes (routers/ai_ensembles.py, AUTHZ1_ROUTES.json, menuVisibility.mjs).
  VERIFY defect in how the end point / range is chosen: a sprint's own
  change set is its merge commit's SECOND parent (the branch tip) against
  the merge base — `git diff <merge>^1...<merge>^2` (three-dot) or
  `merge-base(<merge>^1, <merge>^2)..<merge>^2` — never first-parent
  history. Fix it in apps/api/scripts/_verify_self.py so EVERY script using
  sprint_end_ref / own_lines / own_routes gets it (check vhk1's helpers for
  the same flaw and say which are affected), and make verify_vhk2's scope
  checks use it.
- FAIL D (verify_navregroup in full → 145 PASS, failing lines): "every nav /
  sub / non-nav page has a traced server gate … ['/admin/ai-jobs']";
  "super_admin: … refused ['/admin/ai-jobs']; untraced ['/admin/ai-jobs']";
  "super_admin: every visible href has a traced server gate —
  ['/admin/ai-jobs']"; "no ADDED href is refused by its server";
  "no href is refused that was not already refused before"; "every real
  role: each visible href has a traced gate that admits …". /admin/ai-jobs
  is aibudget1's new daily-run screen (apps/web/app/admin/ai-jobs/page.js).
  Find what API it calls and whether that route is in AUTHZ1_ROUTES.json
  with a gate that admits super_admin: if the page calls an API that is
  missing from the route record or refuses super_admin, that is a CODE
  defect from aibudget1 (fix the route record / gate / the page's API
  path); if it is only missing from navregroup's hand table, add its row.
  Say which.
- FAIL E: "before verify_bulkfetchui: no laptop bulk worker heartbeating in
  the last 120 s — live=None; … asyncpg.exceptions.InvalidPasswordError:
  password aut…": verify_vhk2.py:313 connects with
  os.environ["DATABASE_URL"] — from apps/api/.env, which run_sprint's Step 3
  sources; that value is stale (the other verifies hydrate secrets from
  Doppler over HTTPS). VERIFY defect: hydrate the DSN the same way the other
  verifies do (or call run_regressions.sh's worker check), never read a
  stale .env value; then verify_bulkfetchui actually runs in full. Report
  (names only) which .env keys differ from Doppler's so the operator can
  refresh .env — never print values.
- WSL memory is limited: do NOT run verify_vhk2.py or npm run build; you MAY
  run verify_navregroup.py in full ONCE (it is ~3 min) and any script's
  --self-checks-only.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts; run every command synchronously in the
foreground; no network beyond the Doppler hydration the verifies already do;
never print a secret. Commit on vhk2-fix1 with message "vhk2 fix 1: <one line
per fix, code vs verify>".

=== TASK 1: DISCOVER === A–E: cause, code or verify, evidence (for D: the
page's API calls and their route records).
=== TASK 2: FIX === _verify_self.py's range rule (and every caller);
verify_vhk2's scope checks; /admin/ai-jobs per D; the worker check's DSN.
=== TASK 3: PROVE === --self-checks-only for every regression-set script →
0 FAIL; verify_navregroup.py in full → 0 FAIL; a one-off check that the
fixed range for vhk2 lists ONLY vhk2's files (aibudget1's three absent) and
for aibudget1 lists only aibudget1's; the worker check returns a count (0)
against the live DB.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under vhk2; add the
range rule to docs/VERIFY_RULES.md ("a sprint's own change set = its merge's
second parent against the merge base").
