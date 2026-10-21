NAVREGROUP — regroup the navigation into a few master pages with tabs, driven
by ONE gated menu definition, with zero change to who can reach what (except
the hidden pages that become listed, each behind its real server gate); plus a
server-side fix so Document Search filters by the caller's own visibility
before Documents moves to the main navigation. 8 tasks + verification.

Context: the Admin section of the sidebar is 17 flat items in build order
(Admin, User Management, Staff Visibility, Profiles, Permission Sets,
Workflows, Organization, TA Model Defaults, Model Research, Restricted Access,
Trading Authority, Note Terms Review, EDGAR Pipeline, Note Gold Set,
Extraction Results, Volatility Surface, Platform), and seven admin pages are on
no menu at all (billing-groups, custody-import, model-catalog,
document-search, semantic-search, vdr-proposals, document-review/[id]). The
main navigation lists Investments, Positions, Securities, Commitments and
Portfolio Reporting separately, while /portfolio/transactions, /allocation and
/ownership-tree are on no menu. Joe approved: (1) group Admin into
Organization and Platform sections of master pages with tabs; (2) one
"Portfolio" master page with tabs in the main navigation; (3) "Documents" in
the main navigation for everyone — ONLY after Document Search filters results
by the caller's own visibility.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (read from the repo 2026-10-20):
- apps/web/lib/menuVisibility.mjs is documented as the single source of truth
  (MENU_ITEMS with gate per item; canAccess, canPerm, canPermStrict,
  visibleMenuItems, visibleAdminSections; GATE_MANAGE_MEMBERS,
  GATE_MANAGE_ORG_SETTINGS, GATE_MANAGE_ORG_SETTINGS_STRICT, GATE_SUPER_ADMIN)
  and is exercised by apps/api/scripts/menuvisibility_harness.mjs. The
  /admin index (app/admin/page.js) uses visibleAdminSections.
- BUT components/Sidebar.jsx does NOT render from MENU_ITEMS: it has its own
  hard-coded NAV_ITEMS and *_ITEM constants and calls can("manage_members")
  plus canAccess per block. The two lists have already drifted: Positions,
  Securities and Commitments are in the sidebar but not in MENU_ITEMS.
- canPerm keeps the documented "no roles assigned → default-allow" branch;
  canPermStrict is fail-closed. Super Admin is checked first everywhere.
- A hidden link is not a permission: every page's real gate is its API.
- GET /document-search (routers/document_links.py → services/
  document_linkage.search_documents(conn, org_id, q)) filters by org_id only:
  no per-user, staff-visibility or restricted-access filtering and no
  permission check. GET /semantic-search passes user_id and is_staff into
  document_embedding.semantic_search (per-user visibility).
- GET /vdr-proposals (routers/vdr.py list_vdr_proposals) has no permission
  check; approve/reject require manage_deals.
- Design tokens and brand rules in CLAUDE.md apply (light/cream only, Spectral
  headings, Hanken Grotesk body, white cards with #ece8dd hairline).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism — never wait for one, never poll in a loop for a file,
never start a script in the background; run every command synchronously in the
foreground. Write the verify script but DO NOT RUN IT. Never print a secret.
Column names come from docs/schema_snapshot.sql / the live database. Do not
change any existing page's URL or any existing API gate except the two
explicitly listed in Task 3. Labels come from the menu definition (Rule 1),
never duplicated in components.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Every route under app/admin/** and app/portfolio*/**, app/market,
      app/spvs etc.: for each, the API endpoint(s) it calls and that endpoint's
      REAL server gate (super admin / named permission / staff / any member /
      none). Mark [FIND] any page whose API has no gate.
  1b. The exact visible set today for each persona — computed from BOTH the
      sidebar's code and MENU_ITEMS — for: plain member (with roles), staff
      member, org admin with manage_members only, org admin with
      manage_org_settings, super admin, and a user with no roles (the
      default-allow branch). Record it as the baseline snapshot (JSON) in
      docs/NAVREGROUP_BASELINE.json.
  1c. How the EDGAR Pipeline page's existing tabs (monitor, cohorts, bulk
      download) and the Workflows sub-pages (runs, triggers, versions, edit)
      are routed today.
  1d. The visibility function semantic search uses (per user / staff /
      restricted), so Document Search can reuse it rather than re-implement it.

=== TASK 2: ONE MENU DEFINITION ===
Extend MENU_ITEMS (keep it dependency-free) so each item carries: section
('main' | 'org_admin' | 'platform'), group (the master page id, or null for a
standalone item), tab_order, label, href, gate, and adminIndex. Add a GROUPS
list: id, label, section, order, landing href, description. Add pure helpers:
visibleGroups(me, section), visibleTabs(me, groupId), landingFor(me, groupId)
(the first visible tab's href, or null). A group with no visible tab is not
shown and its landing route returns the same refusal as its strictest tab.
Gates for previously unlisted pages come from 1a's real server gate; if 1a
found NO server gate for a page, gate it GATE_SUPER_ADMIN in the menu and mark
it [FIND] (Task 3 or a later sprint fixes the server).

The target structure (labels are the menu definition's, not hard-coded):
  MAIN
    Dashboard · CRM · Marketplace
    Portfolio   tabs: Overview (/portfolio) · Positions · Transactions ·
                Securities · Commitments · Allocation · Ownership tree ·
                Reporting (/portfolio-reporting)
    Market Indicators · SPV Manager · Insurance · Community
    Documents   tabs: Search (/admin/document-search) · Semantic search
                (/admin/semantic-search)   — gate: any member, ONLY because
                Task 3 makes both filter by the caller's visibility
    Notifications
  ADMIN — ORGANIZATION
    People & Access  tabs: Users · Teams & Coverage (staff-visibility) ·
                     Profiles · Permission Sets
    Settings         tabs: Organization · TA Model Defaults
    Workflows        tabs: Workflows · Runs · Triggers (existing routes)
    Billing & Custody tabs: Billing Groups · Custody Import
    Deal Intake      tabs: VDR Proposals   (gate per Task 3: manage_deals)
  ADMIN — PLATFORM (super admin)
    Tenants          (/admin/platform)
    Controls         tabs: Restricted Access · Trading Authority
    AI Models        tabs: Model Catalog · Model Research
    Structured Notes tabs: Download (/admin/edgar-pipeline, keeps its own
                     inner tabs) · Review queue (note-terms-queue) · Gold
                     answers (note-extraction/gold) · Accuracy results
                     (note-extraction/results) · Volatility surface
  The "Admin" landing (/admin) becomes a grouped overview: one card per
  visible group with its description and its visible tabs as links.
  Keep each tab's own gate exactly as today (e.g. Users needs manage_members,
  Profiles needs manage_org_settings, Model Research keeps the STRICT gate).

=== TASK 3: SERVER FIXES BEFORE DOCUMENTS GOES MAIN-NAV ===
  - Document Search: search_documents takes the caller (user_id, is_staff)
    and applies the SAME visibility function semantic search uses (1d) plus
    restricted-access filtering — reuse, do not re-implement. A member sees
    only documents they can already see elsewhere; staff/super admin as today.
  - VDR proposals list: require the read permission that matches
    manage_deals (use the existing permission vocabulary; if only
    manage_deals exists, require it) — 403 otherwise.
  - Prove both with before/after on fixtures (reproduce the leak first).

=== TASK 4: SIDEBAR AND MASTER PAGES RENDER FROM THE DEFINITION ===
  - Sidebar.jsx renders sections and groups from MENU_ITEMS/GROUPS via the
    helpers — no hard-coded item list, no second gate check. Section headers
    (Admin — Organization, Admin — Platform) appear only when a group in that
    section is visible. A group shows as one item linking to landingFor; it is
    active when the current path is any of its tabs.
  - A shared TabbedSection component renders the tab bar on every page that is
    a tab of a group (from visibleTabs), so each tab stays its own route and
    its own page code is unchanged apart from being wrapped.
  - Group landing routes (e.g. /admin/people) redirect to landingFor on the
    server side after the same session check the pages use; with no visible
    tab they return the standard refusal, not a redirect loop.
  - Existing URLs keep working unchanged (bookmarks, links in emails).
  - Light theme, brand tokens, the existing sidebar look (collapsed icons,
    pin, active state).

=== TASK 5: LABELS ===
Renames live only in the definition: "Note Gold Set" → "Gold answers",
"Extraction Results" → "Accuracy results", "Note Terms Review" → "Review
queue", "Staff Visibility" → "Teams & Coverage", "Platform" → "Tenants",
"Investments" → "Portfolio" (group). Page <title>s and headings follow the
definition where they currently repeat the old label.

=== TASK 6: REAL PROOF ===
  - Persona snapshot: for every persona in 1b, the set of reachable hrefs
    after == baseline, EXCEPT the intended additions (previously unlisted
    pages now listed under their real gate), each listed explicitly; no href
    ever disappears for any persona; no persona gains a page whose server gate
    refuses them (cross-check every visible href against 1a's server gate).
  - Each group: shown iff ≥1 tab visible; landing goes to the first visible
    tab; a persona with no visible tab gets the refusal.
  - Document Search leak reproduced before the fix and closed after, on the
    same fixtures; VDR list 403 for a member, 200 for manage_deals.
  - Permission gate on the two changed endpoints proven both ways on the
    identical request.

=== TASK 7: TESTS IN THE HARNESS ===
Extend apps/api/scripts/menuvisibility_harness.mjs (node, imports the real
module) with the persona snapshot and group/tab rules, so the deployed rule
and the test cannot diverge. Add node tests that render the Sidebar's item
list from a fed /users/me payload per persona.

=== TASK 8: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md: the new structure, the single-definition rule
(Sidebar must never hard-code items again), the baseline/after snapshot, any
[FIND] pages with no server gate, and the Document Search / VDR fixes.

=== VERIFICATION: apps/api/scripts/verify_navregroup.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Hydrate secrets from
Doppler over HTTPS. Wrap every section so an exception is a [FAIL], never a
crash; teardown at start and end in FK child-before-parent order read from
pg_constraint, then re-read every touched table for its exact pre-test count.
Fixture users created explicitly with the auth0_sub the mocked token carries;
teardown finds them by auth0_sub and email pattern too.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Fixture values satisfy live CHECK and FK constraints (pg_constraint,
    contype ::text). Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read. An empty list never passes a check. Expected
    counts from what the test created.
  - Cross-org / visibility checks under app_service (rolbypassrls = false),
    asserted explicitly.
  - Never spawn a background process. Regression subprocesses get
    VERIFY_NESTED=1, run only after teardown, 1200 s timeout; when this script
    itself has VERIFY_NESTED=1 it skips its regression section with one
    '[SKIP] nested run' line.
  - Never touch real note_gold_values or real documents.

Assertions:
  [Y] Task 1 findings reported: every admin/portfolio route with its API and
      real server gate; [FIND] for any ungated page; baseline JSON written
  [Y] Sidebar.jsx contains no hard-coded menu item list and imports its items
      from menuVisibility.mjs (source check + node render test)
  [Y] Persona snapshot (node harness, real module): for each of the six
      personas, after == baseline plus only the listed intended additions; no
      href removed for any persona
  [Y] Every visible href for every persona is allowed by that page's real
      server gate from 1a (no menu item a server would refuse)
  [Y] Groups: shown iff ≥1 visible tab; landingFor = first visible tab;
      landing route with no visible tab returns the refusal (HTTP check), not a
      redirect loop
  [Y] Every pre-existing URL still returns the same status per persona as the
      baseline (sampled: member, org admin, super admin) — no URL changed
  [Y] Document Search: BEFORE the fix (reproduced via the old function on
      fixtures) a member sees a document outside their visibility; AFTER, the
      endpoint returns only documents the member can see, staff and super admin
      unchanged; same fixtures, independent re-read; restricted entity's
      document hidden from a non-allow-listed member
  [Y] VDR proposals list: member 403, manage_deals 200, identical request
  [Y] Labels come only from the definition (renamed labels absent as
      hard-coded strings in components); /admin landing shows grouped cards
  [Y] npm run build exits 0; node tests for the harness and Sidebar pass
  [Y] Regression: verify_bulkfetchui2.py and the menu-visibility harness's
      existing assertions each run ONCE as flat subprocesses AFTER teardown with
      VERIFY_NESTED=1; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test count
