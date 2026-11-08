WFTEMPLATES1 FIX 1 — make verify_wftemplates1.py pass. 2 FAILs: a verify
bug that aborted the whole browse section (so its later checks never ran),
and 3 FAILs inside the nested verify_tiergating.py after this sprint
modernised its fixture seeding. 4 tasks. Branch wftemplates1-work. Decide code
vs verify with evidence; never weaken an assertion.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (verify_wftemplates1 run, 2026-11-08
~14:25 ET: TOTAL 134 PASS, 2 FAIL; library, validator, the shipped templates,
use / copy / provenance, org templates, maker-checker publish, permissions,
isolation and own routes PASSED — keep them):
- FAIL 1 "browse raised — AssertionError: check() got [{'help': None,
  'kind': 'choice', 'name': 'notify_channel', …}] (list)" at
  verify_wftemplates1.py line ~928 in y_browse: a check(...) condition is an
  `a and b and <list>` expression, so it evaluates to the list, not a bool.
  VERIFY defect: wrap the condition in bool(...) (and assert what the list
  must contain, explicitly). Grep the file for every other check(...) whose
  condition can evaluate to a non-bool and fix them the same way. EVERY
  check after line 928 in y_browse never ran — run that section one-off
  after the fix and fix whatever it finds (code or verify), this sprint.
- FAIL 2 nested verify_tiergating.py (own final TOTAL): 36 PASS, 3 FAIL,
  2 FIND (this sprint rewrote its seeding to user_roles):
  a. "every fixture role is a user_roles grant the app decides as meant
     (maker + reviewer 1 + reviewer B hold review_agent_proposals, the
     trigger creator nothing, nobody Super Admin; users.role is its 'member'
     mirror; the maker's run-console view_workflow_runs comes from its
     Profile) — [('…a70a0011', ['TIERGATING Verify Reviewer A'], 'member',
     ['review_agent_proposals'])]" — one fixture user is reported as not
     matching what the check expects;
  b. "the API reports effective_tier=1 … alongside autonomy_tier=3 —
     status=403, step_out=None" — the run-console call is now refused;
  c. "permissions.can_review is true for the maker … — permissions=None"
     (follows from b).
  The maker's view_workflow_runs is meant to come from its PROFILE (SOC
  phase 1 profiles / permission sets), not a role. After the reseed, either
  the profile assignment is no longer seeded, or the route's permission
  check does not honour profile grants. Find which, with evidence: if the
  app's permission resolver (rbac / authz Principal) ignores profile /
  permission-set grants for view_workflow_runs, that is a CODE defect (state
  every route affected); if the fixture lost its profile, VERIFY. For (a),
  print exactly what the check expected vs found per fixture user and fix
  the side that is wrong.
- WSL memory is limited: do NOT run the full verify_wftemplates1.py,
  verify_tiergating.py or npm run build. --self-checks-only, --cleanup-only
  and one-off sections MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground. No network. Never print a secret. Fixtures only; never write a
real row. Commit on wftemplates1-work with message "wftemplates1 fix 1: <one
line per fix, code vs verify>".

=== TASK 1: DISCOVER === 1 (the expression; other non-bool conditions); 2
(a: expected vs found; b: the 403's gate and how the maker's
view_workflow_runs is granted / resolved).
=== TASK 2: FIX === per the evidence.
=== TASK 3: PROVE === y_browse one-off on fixtures: every check runs and
passes; tiergating one-off on fixtures: the three checks pass (the maker sees
the run console via its profile; effective_tier 1 vs autonomy_tier 3;
can_review true); verify_wftemplates1 --self-checks-only and
verify_tiergating --self-checks-only → 0 FAIL.
=== TASK 4: UPDATE PROJECT STATUS === one UPDATE line under wftemplates1.
