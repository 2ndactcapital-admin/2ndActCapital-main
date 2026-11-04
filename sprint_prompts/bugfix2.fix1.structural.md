BUGFIX2 FIX 1 — make verify_bugfix2.py pass. 5 FAILs; one of them may mean
the member-isolation Critical is not fully closed, so it comes first. 5
tasks. Branch bugfix2-work (bugfix2 = 55ca32e, + 5077d6d). For EVERY FAIL
decide code vs verify with evidence and say which; fix the code where the
code is wrong; never weaken an assertion; never widen a gate.

Context: verify_bugfix2.py ran (run_sprint Step 3, 2026-11-03 ~20:56 ET):
TOTAL 154 PASS, 5 FAIL. The route classification (24 staff-only, 3
staff-or-org-admin, 18 member-scoped …), the Critical reproduced before and
gone after on the main entity routes, the menu, draft deals, permission sets
and most Muse fixes passed. Keep all of it.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (from that run's output):
- FAIL A (FIRST — security): regression verify_authz1.py → 124 PASS, 1
  FAIL: "[FAIL] through the API: each org's user gets 404 for the other
  org's entity; org A staff 200, an unlinked org A member 404 (BUGFIX2) —
  404/404/200/200". The fourth value is the UNLINKED ORG-A MEMBER and it got
  200 — a member reading an entity of their own org they are not linked to.
  This is either (a) a code gap: the route authz1 calls is not the one
  verify_bugfix2 covered, or the member-scope filter admits a member to
  every entity of the org on that path — CODE defect, fix it (fail closed:
  404); or (b) the authz1 fixture "member" is not a member in the new
  Principal (test-mode claims granting a staff role, a staff permission
  set, is_staff) — then fix the fixture so it is a true member and show the
  same request now returns 404. Show the request, the Principal
  (roles / permissions / is_staff) and the code path taken. Do not edit
  authz1's expectation.
- FAIL B: "every reclassified route: the fixture MEMBER is refused (403, no
  data) or scoped" — 4 ledger routes (/api/v1/ledger/entries, /trial-balance,
  /capital-accounts, /templates) → 422 "missing query vehicle_id": request
  validation ran before the gate, so the gate was never exercised (no data
  leaked). Fix the verify to send a valid vehicle_id (a fixture vehicle) and
  assert 403 for the member and 200 for staff; and make the gate run BEFORE
  parameter validation if FastAPI allows it here (router-level dependency) —
  a member should learn nothing about a staff route's parameters. Say which.
- FAIL C: staff admitted on the identical request → 500 Internal Server
  Error on /api/v1/entities/{entity_id}/documents/{doc_id}/download and
  /api/v1/entity-groups/{group_id}. A 500 is a CODE defect regardless of the
  fixture (missing row → 404; a real row → 200): find the traceback (run the
  request in a one-off script against your fixture), fix it, and make the
  verify seed the rows those routes need so staff gets 200.
- FAIL D: "WF-01: GET /admin/workflows/{id} returns a drawable diagram —
  HTTP 409". Read the 409 body: a refused state (draft / no version /
  generator stored no diagram) — fix the code so a generated workflow has a
  drawable current version (WF-01), or the verify's fixture if it built the
  workflow without the generator's diagram. Say which.
- FAIL E: "real public.users unchanged (count + checksum by primary key) —
  19 → 19 rows": same count, checksum differs. Real users signed in during
  the run (the operator was testing; touch_last_login updates last_login
  columns on every sign-in). Determine which columns changed for which ids
  (print column names and ids only, no personal data). If only
  login-activity columns: VERIFY defect — checksum the real users table
  excluding columns real activity updates (name them), still asserting no
  row added / removed / role / status / org / permission change. If any
  other column changed: CODE defect (the sprint or verify wrote to real
  users) — find and fix it.
- WSL memory is limited: do NOT run verify_bugfix2.py or npm run build; you
  MAY run verify_authz1.py's single failing section via a one-off script and
  any --self-checks-only.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts; run every command synchronously in the
foreground; no network beyond Doppler hydration; never print a secret or a
real user's personal data. A one-off script may use your own is_test fixture
org and must delete every row it creates (by id) and confirm zero remain.
Commit on bugfix2-work with message "bugfix2 fix 1: <one line per fix, code
vs verify>".

=== TASK 1: DISCOVER === for each FAIL A–E: cause, code or verify, evidence
(A first, with the request, Principal and code path).
=== TASK 2: FIX THE CODE (any FAIL Task 1 proves is code) ===
=== TASK 3: FIX verify_bugfix2.py (vehicle_id, seeded rows, users checksum
columns) — no assertion weakened ===
=== TASK 4: PROVE A WITHOUT A FULL RUN === a one-off script: an unlinked
member of org A requests every entity / entity-group / document route that
authz1 and bugfix2 exercise for another org-A family → 404 every time;
staff → 200; print the table.
=== TASK 5: UPDATE PROJECT STATUS === one UPDATE line under bugfix2.
