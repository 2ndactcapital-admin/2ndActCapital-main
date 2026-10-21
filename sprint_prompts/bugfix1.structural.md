BUGFIX1 — fix the defects HollisMuse found in rounds 1–2 and Joe's own notes:
SPV money math, one deal lifecycle, document search that actually searches
attachments, dead menu items, CRM and portfolio screens that disagree, the
EDGAR bulk-download preliminary skip, and a set of small polish items. No new
features. 10 tasks + verification.

Context: HollisMuse (an AI tester with browser control) ran two passes on
2ndactcapital.com on 2026-10-21 as staff/super admin. Findings are recorded in
the project doc claude/BUGFIX1_BACKLOG.md; IDs below match it. Round 2
confirmed ASSIST-01, MKT-02 and DOC-01 fixed — do not touch those. The member
pass has not run yet; do not change permission rules (authz1, merged at
f115737) — every fix keeps its endpoint's existing gate.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (live DB / main, 2026-10-21):
- SPV-01: SPV "My first SPV" (968ffaef-5d0d-434a-9600-d069d9959dfa) cap table
  shows "$1,340,000 committed" and "100% of $1,000,000 target" (true value
  134%); the SPV list card shows "$0 committed / 0% of $1,000,000" for the
  same SPV. Dashboard SPV ACTIVITY card renders "2soft subscriptionspending"
  and lists that SPV twice ("forming →open" and "forming") (DASH-03).
- MKT-01 / DASH-01: deals carry status badges Draft / Submitted / Under Review
  / Active / Archived while the Marketplace Stage column reads "sourced" for
  all of them; the Marketplace summary counts active/sourced/under_review, the
  dashboard counts Draft/Submitted/Under Review/Active — two vocabularies.
- MKT-03: Submit for Review now requires a name only. MKT-04: on a deal's
  Pipeline tab a member investment is shown as a truncated user id
  ("06fe93ed..."). R2-MKT-05: Archive Deal archives on one click with no
  confirmation and no un-archive; TEST-Submit Test
  (bed53061-fe10-4771-81fc-21f262d65caf) is archived. VOTE-01: a vote cannot
  be withdrawn.
- R2-DOC-03: Document Search (/admin/document-search, GET /document-search →
  services/document_linkage.search_documents) and semantic search query only
  public.documents (0 rows live) + document_extractions (0). Attachments live
  in public.deal_documents (1 row), public.entity_documents (1) and
  public.spv_documents (3) and are never searched. navregroup/authz1 added
  per-user visibility (document_visible, visible_entity_ids, Principal
  is_staff from the DB) — reuse it.
- Dead links: /community, /insurance and /portfolio-reporting (the Portfolio
  group's "Reporting" tab) return 404; /fee-schedules returns 404.
- CRM-02: CRM record James Hargrove (10000000-0000-0000-0000-000000000003)
  Overview shows Hargrove Capital LLC 40% + Hargrove Family Trust 60%; its
  Ownership tab says there is no ownership data. CRM-04: its Tax IDs tab shows
  duplicate-looking SSN / US / Primary rows (masked).
- PORT-01: Positions and Securities are empty (positions come from custody
  imports; none has run) while the assistant finds an investment (Stonegate,
  a member_investments row). PORT-02: /portfolio/commitments is an id lookup
  only ("Open a commitment's projected cash flows by id").
- DASH-02: dashboard "Complete profile" to-dos are not links. R2-ASSIST-03:
  the assistant transcript shows internal tool-usage narration as italic
  prose. FIRM-01: /firm-search is branded "Find your firm — Hollisworks" on
  the 2nd Act site.
- EDGAR: run e3fde9c0 (Wells Fargo 2025-01-01→2026-10-01, peek on) had
  peek_skipped 0; all 550 preliminaries were fully downloaded and stored in R2
  (doc + text); 3222 SEC requests for 1071 filings. Bulk download Institutions
  table has no header row and cramped right three columns. The laptop worker
  start command default in services/edgar_bulk_queue.py uses venv/bin/python;
  the working one is apps/api/venv/bin/python (Doppler
  EDGAR_BULK_LAPTOP_WORKER_COMMAND overrides it today).
- Brand: content areas light/cream/white; navy sidebar and top bar are
  intended. CLAUDE.md rules apply (Rule 1 labels from config/API, bi-temporal
  writes, permission envelope, statement_cache_size=0).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism — never wait for one, never poll in a loop, never start
a script in the background; run every command synchronously in the foreground.
Write the verify script but DO NOT RUN IT. No real SEC or R2 calls (mock).
Never print a secret. Column names from the live DB. Do not change any
endpoint's permission gate. Never modify real records except: restore
TEST-Submit Test to its pre-archive status through the new un-archive path,
and data repairs Task 1 proves are needed (report each, bi-temporal).

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. SPV totals: every place committed amount and % of target are computed
      (cap table, list card, dashboard card), the query each uses, and why they
      disagree (status filters? soft vs hard commitments? a hard-coded cap at
      100%?).
  1b. Deal lifecycle: every column and config category that holds a deal's
      stage or status (deal_stages config, deals columns), which screen reads
      which, and the transitions each action performs.
  1c. Attachment tables: columns of deal_documents, entity_documents,
      spv_documents vs documents; where their files live (R2 keys) and whether
      text is extracted for any of them.
  1d. CRM-02: where the Overview's 40/60 figures come from vs what the
      Ownership tab reads; CRM-04: whether the tax-ID rows are true duplicates
      (same type, country, value hash) or a display bug.
  1e. EDGAR peek: in services/edgar_bulk.py, the order of requests per filing
      (index.json, peek Range GET, full GET), what the peek returned for the
      e3fde9c0 run (206 vs 200, bytes) if logged, and why a preliminary was
      not skipped.
  1f. What /fee-schedules was meant to route to (app/fee-schedules exists).

=== TASK 2: SPV MONEY MATH ===
One function computes an SPV's committed total and percent of target, used by
the cap table, the SPV list card and the dashboard card. Percent is not capped
(134% shows as 134%, with an "over target" note). Soft vs hard commitments are
labelled consistently on all three. Dashboard SPV card: correct spacing and one
line per SPV with its current state.

=== TASK 3: ONE DEAL LIFECYCLE ===
Per 1b, one ordered lifecycle (from the deal_stages config, Rule 1) drives the
badge, the Stage column, the Marketplace summary and the dashboard snapshot.
Migrate existing deals bi-temporally if two fields hold it today (report each
deal before/after). Submit for Review validates the fields the review needs
(from config, with clear messages) and asks for confirmation. Archive asks for
confirmation and an Un-archive action restores the prior stage (audited);
restore TEST-Submit Test with it. Members' investments show the member's name
(not an id), subject to the existing visibility rules. A vote can be withdrawn
by its owner.

=== TASK 4: DOCUMENT SEARCH ACROSS ATTACHMENTS ===
Keyword search covers public.documents AND deal_documents, entity_documents,
spv_documents (file name, category/type, extracted text where it exists), each
result labelled with its source (vault / deal / entity / SPV) and linking to
where it lives. Visibility per result: the same rule the record itself uses
(a deal attachment is visible to whoever can see the deal; entity and SPV via
visible_entity_ids + restricted access); reuse, do not re-implement. Semantic
search: state in the UI when an item has no embedding yet ("not yet indexed
for meaning search") rather than silently omitting it; do not build new
embedding pipelines in this sprint. /documents lands on the Documents group
for everyone who can see it (no admin-only redirect).

=== TASK 5: DEAD LINKS ===
Hide Community, Insurance and Portfolio → Reporting from the menu definition
(menuVisibility.mjs) until their pages exist (a feature flag in the
definition, documented); their URLs return a friendly "Coming soon" page in
the brand style instead of a bare 404. /fee-schedules: route it to its page
per 1f, or remove links to it.

=== TASK 6: CRM AND PORTFOLIO AGREE ===
CRM-02: the Ownership tab shows the same ownership the Overview shows (one
source, per 1d). CRM-04: if true duplicates, collapse to one current row
bi-temporally (report each); if a display bug, fix the display. PORT-01: when
Positions or Securities are empty for a member who has investments, show an
explanation ("Holdings appear here once account data is imported from the
custodian") and link to their investments. PORT-02: Commitments shows a list
of the caller's visible commitments (name, fund, commitment, called,
remaining) with the existing projection opening from a row; the id lookup
stays as a secondary control.

=== TASK 7: EDGAR BULK DOWNLOAD ===
Per 1e, make the peek decide BEFORE the full download: a preliminary (or any
kind not in --kinds) is recorded with its document_kind and status and is not
fully downloaded or written to R2. If SEC ignores Range (200 with full body),
classify from the first 16 KB of that body and stop reading the stream (no
full read, no R2 write). Add a one-off cleanup command that deletes the R2
objects of already-stored preliminaries and clears their r2_key/text_r2_key
(dry-run first, prints count and bytes; the operator runs it). Institutions
table: header row (Institution, Entities, 2025, 2026 or the years shown,
Total) and aligned numeric columns. Fix the laptop worker command default to
apps/api/venv/bin/python.

=== TASK 8: POLISH ===
DASH-02: each dashboard to-do links to where it is resolved. R2-ASSIST-03:
the assistant never renders tool-usage or system narration to the user (strip
it server-side; keep it in the activity log if it is logged today). FIRM-01:
/firm-search shows the tenant's own brand (org branding settings) on the 2nd
Act host.

=== TASK 9: REAL PROOF ===
For every item: reproduce the defect first on fixtures (or show the old code
path producing it), then show it fixed; negative cases (a refused member stays
refused; a hidden menu item stays reachable only where it was; search never
returns a result outside the caller's visibility — member, staff, other org,
restricted entity); persistence by independent re-read.

=== TASK 10: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md: each fix with its ID, the one SPV-totals function, the
deal lifecycle source, the search coverage and its visibility rule, the
feature flag for unbuilt pages, and the preliminary R2 cleanup command.

=== VERIFICATION: apps/api/scripts/verify_bugfix1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. Mock SEC
and R2. Wrap every section so an exception is a [FAIL]; teardown at start and
end in FK child-before-parent order from pg_constraint, then re-read every
touched table for its exact pre-test count. Fixture users created explicitly
with the auth0_sub the mocked token carries and is_test = true if that column
exists.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Fixture values satisfy live CHECK and FK constraints (pg_constraint,
    contype ::text). Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; an empty list never passes a check; expected
    counts from what the test created.
  - Never spawn a background process. Regression subprocesses get
    VERIFY_NESTED=1, run after teardown, 1200 s timeout; with VERIFY_NESTED=1
    this script skips its regression section with one '[SKIP] nested run' line.
  - Never touch real records except the TEST-Submit Test restore and the
    Task 1-proven repairs (each reported).

Assertions:
  [Y] Task 1 findings reported (1a–1f), each with the file and query involved
  [Y] SPV: a fixture SPV with $1.34M committed vs $1M target shows 134% on the
      cap table, list card and dashboard card from the same function
      (reproduced as 100% / $0 on the old paths first)
  [Y] Dashboard SPV card: one line per SPV, correct spacing
  [Y] Deal lifecycle: badge, Stage column, Marketplace summary and dashboard
      snapshot agree for fixture deals in every stage; real deals' before/after
      reported and consistent
  [Y] Submit for Review refuses a fixture deal missing a required field with
      the field named; Archive needs confirmation; Un-archive restores the
      prior stage (audited); TEST-Submit Test restored (re-read)
  [Y] Member investment shows a name, not an id; vote withdraw works for the
      owner only
  [Y] Search finds a fixture deal attachment, entity attachment and SPV
      attachment by name and by text, each labelled with its source; a member
      never sees one outside their visibility; other-org and restricted
      fixtures never appear
  [Y] Hidden pages: Community, Insurance, Reporting absent from every
      persona's menu; their URLs show "Coming soon"; /fee-schedules resolved
  [Y] CRM-02 Ownership tab equals Overview for the fixture; CRM-04 duplicates
      collapsed or display fixed (per 1d)
  [Y] PORT-01 empty-state message shown when holdings are empty but
      investments exist; PORT-02 list shows the caller's commitments only
  [Y] EDGAR peek: a mocked preliminary (206 and 200 cases) is recorded without
      a full GET and without an R2 write; a final continues to full fetch;
      cleanup dry-run reports count and bytes and changes nothing
  [Y] Institutions table has a header row; worker default command uses
      apps/api/venv/bin/python
  [Y] Dashboard to-dos link; assistant transcript contains no tool narration;
      /firm-search shows the 2nd Act brand on the 2nd Act host
  [Y] Every changed endpoint keeps its gate: org admin / member / super admin
      outcomes unchanged on the identical request
  [Y] npm run build exits 0; node tests pass
  [Y] Regression: verify_authz1.py, verify_navregroup.py and
      verify_bulkfetchui2.py each run ONCE as flat subprocesses AFTER teardown
      with VERIFY_NESTED=1; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real note_gold_values unchanged (count and checksum)
