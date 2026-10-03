EDGAR COHORTS + TEMPLATE STUDY — named, frozen sets of filings built on the
Filings tab (by filter, by hand, or by sampling), runs that target a cohort, and
an inventory pass that lists every data element a set of filings actually
contains, so the schema can be settled once, from evidence.

YOU ARE THE SPRINT. Do this work yourself, now, in this session. Do NOT run
run_sprint.sh. Do NOT launch anything in the background. Run every command
synchronously in the foreground and read its output. Nothing will ever notify
you that something finished.

WRITE THE VERIFY SCRIPT BUT DO NOT RUN IT. The operator runs it. Do not run the
inventory pass on real data either: build it with a dry-run mode and stop.

=== CONFIRMED REAL FACTS — do not re-derive ===

From edgarpipelinea (merged, live):
- portfolio.edgar_index_filings: 716,953+ filings, with the status lifecycle,
  document_kind, detected_cusip, primary_issuer_cik, selection_policy_version,
  reference_filing_id. Policy v1 has marked 537,698 424B2s 'selected' and
  179,012 'not_selected' (FWPs and unlisted filers).
- portfolio.edgar_selection_policies (versioned, immutable),
  portfolio.edgar_pipeline_runs, portfolio.edgar_pipeline_lease.
- The job runner (apps/api/scripts/edgar_pipeline_job.py) runs discover ->
  select -> fetch as a Render one-off job. Its select stage ALREADY accepts an
  explicit list of accession numbers (the edgarpipelinea verify used it).
- The EDGAR Pipeline admin page (super-admin only, strict gate) has Filings
  (server-side filter/sort/paging), Progress, and Issuers tabs. "Run now" with a
  fetch cap currently sits on the Progress tab and always runs the default
  policy: newest first, going backward.

From noteextractb1 (merged):
- Readings, staging-results and gold tables; the proxy helper (separate calls,
  no fallback, provider-reported model recorded); the evaluation harness and
  pilot runner, each with its own sampler, a spending cap and a dry-run mode.
- Candidate models are registered only once their provider key exists in
  Doppler prd_lite_llm and a real call succeeds. Joe has ruled out Claude
  models for this work. No OpenRouter. No direct provider calls from app code.

=== WHAT JOE ASKED FOR ===

1. Choosing what to run belongs on the Filings tab, not as a bare number on the
   Progress tab. The default "newest first, going backward" is only one way to
   work. Joe wants to apply filters (certain issuers, certain document types,
   dates, statuses) or hand-pick filings — for fetching now, and for
   extraction later.
2. Before settling the schema, study a small cohort — a few filings per issuer,
   spread across product types and years — and list everything they contain,
   so fields are not added one surprise at a time.

=== DECISIONS (Joe) — do not re-litigate ===

1. A COHORT is a named, frozen list of filings. Tables: cohorts (name, purpose,
   created_by, created_at, the definition that built it as JSON — filters,
   sampling method, parameters, random seed) and cohort members (cohort,
   accession_number, position, stratum label). Global, four RLS policies,
   super-admin writes. Once created a cohort NEVER changes; "copy and edit"
   creates a new one. Members are DEDUPLICATED BY ACCESSION NUMBER (co-listed
   filings appear under several filers). A size limit (50,000) prevents
   accidents.

2. BUILDING A COHORT, on the Filings tab: use the existing filters, then
   either tick rows by hand (ticks survive paging), or take everything matching
   the filter. Sampling: all, newest N, oldest N, random N (seed recorded, same
   seed gives the same members), or STRATIFIED (N per issuer group per year, or
   per issuer group per era: 2019-2021, 2022-2023, 2024-2026). Show a preview —
   the count, and a table of members per stratum — before saving.

3. RUNNING A COHORT. "Fetch this cohort", with an optional cap, runs the same
   Render job against exactly the cohort's members, in cohort order. A cohort
   may contain filings the default policy did not select (an FWP, a filing from
   an unlisted filer): the operator chose them deliberately, so they ARE
   fetched, and each records that it was selected by cohort, with the cohort id
   — never confused with a policy decision. Cohort runs share the same lease,
   rate limit and runtime cap. Each run records its cohort id.

4. THE PAGE. Add a Cohorts tab: each cohort with its definition, member counts
   by status, its runs, and a members grid. Keep a clearly labeled "Run default
   policy now (newest first)" on the Progress tab for the nightly behavior;
   everything targeted moves to the Filings and Cohorts tabs. Extraction
   actions are NOT added here yet, but the run API takes a run kind ('fetch'
   now, 'extract' reserved for B2) so extraction plugs in without rework.

5. EXTRACTION TOOLS TAKE A COHORT. B1's pilot runner and evaluation harness
   accept --cohort <id> as an alternative to their own samplers; the gold-set
   sampler can propose candidates from a cohort.

6. THE TEMPLATE STUDY. A "Build template study" preset on the Cohorts tab:
   for every issuer with include_status 'yes', sample across the three eras,
   OVERSAMPLING (about 8 per issuer per era) because document kind is unknown
   until fetched. After fetching, the inventory pass draws on the members
   classified pricing_supplement — aiming for 4-6 per issuer, chosen for
   VARIETY: use the keyword-filter hits (autocall, buffer, barrier, digital /
   fixed payment, contingent coupon, participation / leverage) to spread across
   product families — plus 1-2 product supplements per issuer, because notes
   lean on them for definitions.

7. THE INVENTORY PASS — inventory, NOT extraction:
   - Input: each document's terms pages only — from the start through the
     payout examples, stopping before risk factors, index methodology, license
     text and tax. Record the token count.
   - The model lists EVERY distinct data element it finds: the label exactly as
     written, the value as written, a short EXACT quote, the section, and which
     existing schema field it corresponds to — or NEW. Give it the current field
     list (generated from note_terms_field_registry) for that mapping. Ask it to
     flag elements whose label is misleading about their meaning — for example
     a "Redemption Barrier" that is really the threshold for a fixed payout and
     protects nothing.
   - Also per document: product family as the document describes it, the
     program / product supplement it cites, whether it has a hypothetical payout
     table, and the issue size.
   - Every quote is checked against the filing text; an item whose quote is not
     found is rejected and counted.
   - Results go to an inventory-items table with full provenance (model,
     provider-reported version, cost).
   - Aggregation: group items into CONCEPTS — first by normalised label, then
     one model-assisted grouping over the list of unique labels — keeping every
     original label and quote attached. Per concept: its labels (synonyms), the
     issuers that use it, frequency, example values, the mapped existing field
     or a proposed new one, and any misleading-label flags.
   - Output: a super-admin grid on the Cohorts tab, docs/TEMPLATE_STUDY.md, and
     a per-issuer label dictionary (issuer -> label -> concept) for rules.
   - The model is chosen at run time from platform_model_catalog entries that
     are 'available' and not Claude; if none is available, report BLOCKED.
   - Spending cap and dry-run, as in B1.

OUT OF SCOPE: changing the extraction schema (that is the next sprint, informed
by this study); extraction runs; the nightly default policy's behavior.

=== FACTS THAT CHANGE DECISIONS ===
- RLS policies are per operation and need their context set; both a missing
  policy and missing context look like empty results or "row not found".
- Writes to global tables need SET LOCAL app.is_super_admin = 'true' inside a
  transaction (platform_scope()); never a session-level SET under the pooler.
- Anything that samples or counts filings deduplicates by accession number.
- Column names come from the live database, not from this prompt.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. How the job runner and the select stage take an explicit accession list
      today, and what changing a not_selected filing to selected requires.
  1b. The Filings tab's filter state and server endpoint, and how to add row
      selection that survives paging.
  1c. Which non-Claude models are 'available' in platform_model_catalog now.
  1d. Where B1's samplers live, to accept --cohort.
=== TASK 2: SCHEMA — cohorts, members, run columns, inventory tables ===
Apply via MCP, verify each with a follow-up query, four RLS policies per table.
=== TASK 3: COHORT BUILDING AND TARGETED FETCH (API and job runner) ===
=== TASK 4: THE PAGE — Filings-tab selection, Cohorts tab, relabeled default run ===
=== TASK 5: --cohort FOR B1'S TOOLS ===
=== TASK 6: TEMPLATE STUDY PRESET AND THE INVENTORY PASS ===
=== TASK 7: STATUS === update docs/PROJECT_STATUS.md.

=== VERIFY: apps/api/scripts/verify_edgarcohorts.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only. MUST print 'TOTAL: N PASS, M FAIL'
and exit non-zero on failure. Hydrate secrets from Doppler over HTTPS at
startup. Mock model responses for the inventory pass; total real spend $0.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Never derive any unique value from a slice of a fixture UUID.
  - Set RLS context on EVERY read.
  - Clear audit_log, assistant_activities and agent_proposals BEFORE deleting
    fixture users.
  - Expected counts are DERIVED from what the test created, never hard-coded.
  - Samples of real data EXCLUDE the test's own fixtures, and are deduplicated
    by accession number.
  - Call the app on the SAME event loop (httpx.AsyncClient + ASGITransport).
  - The real manifest must end exactly as it started: snapshot every row the
    test touches and restore it.

Assertions:
  [Y] Task 1's findings reported
  [Y] A cohort built from a filter, one built by hand, and one stratified each
      freeze exactly the expected members; co-listed filings appear once
  [Y] Random sampling with the same seed yields the same members; a different
      seed yields different ones
  [Y] The stratified preview's per-stratum counts equal the saved members
  [Y] A saved cohort cannot be edited; "copy and edit" creates a new one
  [Y] The size limit refuses an oversized cohort
  [Y] Super-admin 200; org admin and member 403 on the IDENTICAL request, for
      create, read and run
  [Y] Fetching a cohort targets exactly its members in cohort order; a member
      the default policy did not select is fetched and records selection by
      cohort with the cohort id; filings outside the cohort are untouched
  [Y] A cohort run is refused while another job holds the lease
  [Y] The run records its cohort id; the Cohorts tab's counts by status match
      the database
  [Y] The default policy's behavior is unchanged
  [Y] B1's pilot and harness in dry-run with --cohort plan exactly the cohort's
      ready members
  [Y] The template-study preset covers every 'yes' issuer and all three eras
  [Y] Inventory dry-run makes zero model calls; with mocked responses, items are
      stored with provenance; a fabricated quote is rejected and counted; terms
      pages stop before risk factors
  [Y] Concept grouping keeps every original label and quote attached, and the
      per-issuer label dictionary is produced
  [Y] The spending cap stops a run cleanly
  [Y] No Claude model, OpenRouter route, provider key or direct provider call in
      application code
  [Y] Teardown: fixtures gone; manifest rows restored exactly
  [Y] npm run build exits 0
