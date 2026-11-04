BUGFIX2 — member data isolation first, then the HollisMuse round-3
findings: (1) every READ route that is gated by nothing beyond sign-in is
classified and gated — staff-only data refused to members, member data
scoped to the member's own family, truly public reference data left open;
(2) the member menu shows only member screens; (3) draft deals hidden from
members; (4) staff-only permission sets cannot be held by a member; then the
High / Medium defects Muse found. 9 tasks + verification.

Context: HollisMuse ran the v3 test plan against production on 2026-11-03
(report in the project: HollisMuse final report). Three CRITICALS, all one
root cause: a MEMBER (jpl991721) pasted a staff URL and read another
family's full CRM record (/crm/10000000-0000-0000-0000-000000000001,
Hargrove Family Trust: legal details, ownership 60%, all tabs, an Edit
button) and its Compliance tab (KYC, OFAC, AML, accreditation, PEP); the
member's Investment Profile "Investment Entity" dropdown lists all 34 tenant
entities; the member's menu shows CRM, SPV Manager, Billing & Custody. The
member portfolio scope itself is clean.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (live DB + repo, 2026-11-03 evening;
Task 1 confirms):
- docs/AUTHZ1_ROUTES.json: 73 non-public READ routes have after = "none
  beyond auth" — any signed-in user of the org passes. By prefix: /entities
  14, /deals 7, /portfolio 5, /spvs 5, /ledger 5, /market 4, /dashboard 3,
  /deal-room 3, /notifications 3, /assistant 2, /entity-groups 2, /admin 2,
  and one each under /documents, /records, /document-search,
  /document-link-proposals, /semantic-search, /investment-profile, /config,
  /taxonomy, /transaction-types, /me, /reference, /fx-rates, /users,
  /allocation-lens, /theme, /modeling, /replication, /human-tasks.
- routers/entities.py: GET /entities (list), /entities/{entity_id},
  /entities/{entity_id}/full, /entities/{entity_id}/notes … carry no gate;
  the POST is "authz manage_members".
- services/authz.py: Principal.is_staff = super admin OR a STAFF_ROLE_NAMES
  role; helpers at ~244/253/283 short-circuit for staff.
- Test accounts as they really are in the DB (NOT what the test plan
  assumed): jpl991721, jpl991722 and jpl991723 all have users.role =
  'member' and only the 'member' role (10 permissions incl. view_portfolio);
  jpl991721 ALSO holds the permission set "Compliance Override" (a member
  holding a staff override — likely why the Compliance tab opened, besides
  the missing route gate); jpl99172 is org_admin (27 permissions, none named
  *crm*/*entit*/manage_members). jlarizza@culmina.io and jlarizza@gmail.com:
  super admin per Muse. So Muse's "org admin 2" (991723) and "support staff"
  (991722) were members — and 991723, a MEMBER, loaded
  /admin/hypothetical-policy and the price-sources page (Muse NAV-03/06/07)
  while 991722 was refused on the same URLs: find why they differ.
- Other Muse findings to fix or decide (IDs from the report):
  NAV-02 org admin /admin/users → "Could not load users: Request failed
  (500)" every load. DEAL-01 no deal-creation control for org admin.
  DEAL-02 a draft deal ("Verify12 Test Deal") is visible to members in the
  deals table and dashboard "NEW DEALS". REP-01 org admin cannot create an
  entity/household: "Permission required: manage_members". REP-05 a model
  upload of AAPL/MSFT/GOOG has 3 unresolved rows (not in the security
  master, portfolio.securities_global has ~67 rows) — approval correctly
  refused but the screen does not say how to resolve. REP-09 the "Open"
  button on an approval task card goes to a page the checker cannot open.
  BT-01 backtest run → bare "Request failed" twice, no reason. AI-08
  /admin/model-catalog sometimes renders "0 models … No models on the
  platform catalog yet." next to (or instead of) the 10 models. WF-01 the
  workflow editor shows "Could not render diagram: no diagram to display"
  for a workflow just generated ("2 steps, 2 require approval"). WF-03 the
  ownership graph for Hargrove Capital LLC shows only the root node; the
  owners (Hargrove Family Trust 60%, James Hargrove 40%) are missing though
  the detail page lists them. TILT-02 a member can open the tilts URL
  directly (empty state, no data) while tax / backtest URLs are gated.
  BT-02 cosmetic: "Hypothetical performance. no hypothetical-performance
  policy approved" (missing capital / sentence break). Workflows have no
  delete control (MUSE test rows cannot be removed in the UI).
- Leftover from previewperf: verify_bulkfetchui.py's "live preview returns
  under 2 s" measures the laptop's round trips to the database (2.07–2.2 s
  from a laptop; server-side 151–598 ms; production runs next to the DB).
- HollisMuse test rows to remove (by id, nothing else): model "MUSE – Model"
  v1 /portfolio/models/a9458f13-c360-4e30-a4db-11eb0366c6cd with its upload
  log entry and its open approval human task; workflow "MUSE – workflow" v1
  8935770a-2cb6-4892-aef9-26a643d75810 (no runs).
- vhk1/vhk2: apps/api/scripts/_verify_self.py (own_routes, still_gated,
  when_empty); docs/VERIFY_RULES.md; every verify supports
  --self-checks-only and --cleanup-only.
- Regression: this changes route gates and the menu → re-run
  verify_authz1.py ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s
  (its route matrix is the gate record's own proof).
- WSL memory is limited: the sprint must NOT run npm run build or any full
  verify script; --self-checks-only and --cleanup-only MAY be run.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never start a script in the background,
never poll; run every command synchronously in the foreground. Write the
verify script but DO NOT RUN IT. No network calls. Never print a secret. Fail
CLOSED: when unsure whether a read route may serve a member, gate it to staff
and list it. Never widen any existing gate. org_id only from the session.
Member scope = the entities linked to the member's own user (find the link
in Task 1); never "all entities of the org". Do not change real users' roles
or permission sets in data — fix the code paths; report the test-account
corrections for the operator (Task 8). Light/cream UI with the existing
tokens. Commit ALL sprint files (excluding sprint_prompts/logs) on the
current branch at the end with message "bugfix2: <one line per fix>".

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The 73 ungated read routes: a table — route, what it returns, and a
      class: PUBLIC-TO-ORG (reference data any signed-in user may read:
      config, theme, me, taxonomy, reference, fx-rates, transaction-types,
      market indicators), MEMBER-SCOPED (a member may read only their own
      family's rows: dashboard, notifications, investment-profile,
      portfolio, published deals, their own documents / tasks), STAFF-ONLY
      (entities, entity-groups, ledger, spvs, records, document search,
      semantic search, admin, users, modeling, allocation-lens of other
      families …). Justify each class in a few words.
  1b. How a member is linked to their own family today (user ↔ entity link
      table / column), and which existing service already scopes a member
      (the clean portfolio path) — reuse it.
  1c. Why member jpl991723 loaded /admin/hypothetical-policy and price
      sources while member jpl991722 was refused: the gate on those pages /
      routes, and what differs between the two users (profile, permission
      set, staff assignment, delegate grant …).
  1d. Permission sets / profiles: which are staff-only (e.g. "Compliance
      Override"), and whether assignment refuses them for a member user.
  1e. The member menu: where menu visibility is decided
      (lib/menuVisibility.mjs) and why a member sees CRM, SPV Manager,
      Billing & Custody.
  1f. For each other Muse ID listed above: cause, code or data or plan,
      evidence.

=== TASK 2: GATE THE READ ROUTES ===
Apply 1a: STAFF-ONLY routes require staff (authz helper, same pattern as
existing staff gates) — a member gets 403 with no data; MEMBER-SCOPED routes
return only the member's own family's rows (filtered in SQL, not in the
browser) and 404 for another family's id (do not reveal existence);
PUBLIC-TO-ORG unchanged. Update docs/AUTHZ1_ROUTES.json for every changed
route (after = the new gate) and authz1_routes.py --check passes.

=== TASK 3: THE MEMBER MENU AND PAGES ===
Members see only member screens (menu and direct URL): no CRM, SPV Manager,
Billing & Custody, admin, tilts or any staff page — each refused cleanly on a
direct URL. Investment Profile's entity dropdown lists only the member's own
entities (ROUTE-01).

=== TASK 4: DEALS AND PERMISSION SETS ===
Draft / under-review deals never reach members (list, detail, dashboard "NEW
DEALS"); members see only published deals. A staff-only permission set or
profile cannot be assigned to a member user (refused with the reason) and,
if a member already holds one, it grants nothing on staff-only routes
(fail closed in the Principal) — list current holders for the operator.

=== TASK 5: HIGH / MEDIUM DEFECTS ===
NAV-02 /admin/users 500 (fix); REP-01 / DEAL-01: decide from the code and
docs whether org admins should create entities/households and deals — if a
permission for it exists and org_admin is meant to hold it (docs, rbac
defaults, spec), grant it in rbac.ensure_org_admin_role and the migration;
otherwise report "super-admin only by design" with the evidence; REP-05: the
unresolved-rows message says how to resolve (link to security lookup / add
security); REP-09 task "Open" goes to the task's own page; BT-01 a failed
backtest shows its reason; AI-08 the catalog never shows "0 models" while
loading (loading state, then the real count); WF-01 a generated workflow's
diagram renders (or the generator stores the diagram it reports); WF-03 the
ownership graph shows owners and edges matching the detail page; TILT-02
gated like tax / backtest; BT-02 wording; workflows get a delete for a
workflow with no runs (maker-checker not required for a never-run draft —
state the rule).

=== TASK 6: THE LAPTOP-LATENCY CHECK ===
verify_bulkfetchui.py's preview check asserts the SERVER-SIDE elapsed_ms is
under 2 s (best of 3) and prints the end-to-end time as INFO with the round
trip count — the limit is not raised; say so in docs/VERIFY_RULES.md
("time the server, not the operator's network").

=== TASK 7: REMOVE THE HOLLISMUSE TEST ROWS ===
A one-off script through Doppler deletes exactly the MUSE rows listed in the
facts (model + its version rows + upload log entry + its open human task;
the workflow + its version rows), by id, after printing them; confirm zero
remain; nothing else touched (count + checksum of each table before / after).

=== TASK 8: REAL PROOF ===
  - Reproduce the Critical BEFORE the fix in the verify's own fixture org: a
    fixture member reads another fixture family's entity / full / notes /
    compliance → shown; AFTER: 403 / 404 with no data. For EVERY route
    reclassified in Task 2: a fixture member refused (or scoped) and a
    fixture staff user admitted, on the identical request (both ways).
  - Member scope: a member sees only their own family on every
    MEMBER-SCOPED route; another family's id → 404.
  - Menu: node tests — a member's visible menu contains no staff item; a
    staff user's is unchanged.
  - Deals: a draft deal is invisible to a member everywhere; published
    visible.
  - Permission sets: assigning a staff-only set to a member refused; a member
    holding one gains nothing on staff-only routes.
  - Each Task 5 fix proven (500 gone; resolve message; task link; backtest
    reason; catalog loading; diagram renders; graph nodes; tilts gate;
    workflow delete only without runs).
  - MUSE rows gone; real rows unchanged.
  - Org isolation both directions (rolbypassrls = false).
  - Routes: own route changes via _verify_self; authz1_routes.py --check
    CURRENT.
  - Operator list printed: the test-account corrections needed (991723 →
    org admin, 991722 → a staff role, remove "Compliance Override" from
    991721) and any other member holding a staff-only set.
  - Regression: verify_authz1.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (bugfix2 entry with the 1a table summary);
docs/VERIFY_RULES.md (the server-time rule); add verify_bugfix2.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_bugfix2.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
calls. Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id; re-read every
touched table for its exact pre-test count. Fixture orgs and users is_test =
true (assert it); a fixture member, a fixture staff user and two fixture
families. Compute every expected date at the call. Read UNIQUE / CHECK / FK
constraints from the catalog before seeding. Support --self-checks-only and
--cleanup-only. Real-data checks use _verify_self.when_empty. The
regression subprocess gets VERIFY_NESTED=1, runs after teardown, 2700 s;
with VERIFY_NESTED=1 this script skips its regression section with one
'[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read; app_service rolbypassrls = false asserted for
    isolation checks; an empty list never passes a check.
  - A test stub must match the CURRENT signature of what it replaces.
  - Key a real table's fingerprint by its real key from the catalog.
  - A failing check's detail prints the response body / culprit rows.
  - Never touch real users, entities, deals, permission sets or roles:
    count + checksum before and after (the MUSE rows are the only real rows
    removed, by Task 7's script, not by the verify).

Assertions:
  [Y] Task 1 findings 1a–1f (the 73-route table)
  [Y] Critical reproduced before, gone after; every reclassified route both
      ways
  [Y] Member scope on member routes; another family → 404
  [Y] Member menu and direct URLs; Investment Profile dropdown own-only
  [Y] Draft deals hidden from members
  [Y] Staff-only permission sets: refused for members, grant nothing
  [Y] Task 5 fixes each proven
  [Y] bulkfetchui check times the server
  [Y] MUSE rows removed; real rows unchanged
  [Y] Org isolation both directions; own routes gated; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_authz1.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
