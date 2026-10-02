EDGAR PIPELINE A — status lifecycle, selection rules, incremental discovery,
fetch-to-R2, the nightly job, and the pipeline monitoring screen.

YOU ARE THE SPRINT. Do this work yourself, now, in this session. Do NOT run
run_sprint.sh. Do NOT launch anything in the background. Run every command
synchronously in the foreground and read its output. Nothing will ever notify
you that something finished.

WRITE THE VERIFY SCRIPT BUT DO NOT RUN IT. The operator runs it. Stop once it
is written and your other tasks are complete.

NO MODELS IN THIS SPRINT. Nothing here calls an LLM, Jev, or LiteLLM. Term
extraction is sprint B.

=== CONFIRMED REAL FACTS — do not re-derive (checked live 2026-10-01) ===

Discovery is already done, by hand:
- portfolio.edgar_index_filings: 716,953 rows, one per unique filing (424B2 +
  FWP) from EDGAR's quarterly master index, 2019Q1 through the first day of
  2026Q4. PK accession_number. All pipeline_status = 'discovered'. Its CHECK
  currently allows only 'discovered'.
- portfolio.edgar_index_filing_filers: every company listed on each filing,
  PK (accession_number, cik). A parent and its finance subsidiary are both
  listed on co-issued notes — that is why the index had ~38% duplicate rows.
- portfolio.structured_note_issuers: 33 rows, PK filer_cik, with issuer_group,
  filer_role ('issuer'|'guarantor'), credit_entity, include_status
  ('yes'|'review'|'no'). Covers 537,910 of 546,013 424B2s (98.5%).
- portfolio.v_edgar_filings_explorer: read-only browsing view
  (security_invoker = true). Filtering it by issuer_group is slow because the
  group is looked up per row.
- All four objects were created directly via MCP. THE REPO HAS NO MIGRATION
  FILE FOR THEM. All three tables carry four RLS policies copied from
  portfolio.reference_filings (SELECT USING true; INSERT/UPDATE/DELETE gated on
  current_setting('app.is_super_admin', true) = 'true') and grant
  SELECT/INSERT/UPDATE/DELETE to app_service. CIK is unpadded text.
- apps/api/scripts/load_edgar_index.py loaded them (may be uncommitted). It
  copies into a TEMP table then INSERTs, because Postgres refuses COPY FROM
  into a table with row-level security.

The older single-quarter corpus (built in the structured-notes thread):
- portfolio.reference_filings holds 201 fetched documents from 2025Q1 (raw
  HTML in R2 under reference/edgar/{cik}/{accession}/{doc}, plain text in
  extracted_text, extraction_status 'extracted'|'skipped').
  securities_global_note_terms.reference_filing_id points at it. One of the
  201 rows is a leftover test record, filer_name 'VERIFY FIXTURE'.
- apps/api/services/edgar_fetch.py already has fetch_index, fetch_filing and
  store_filing (declared User-Agent from EDGAR_USER_AGENT with a loud failure
  if unset, a hard 10 req/s limit, backoff on 429/5xx, idempotent upsert on
  (accession_number, primary_document)), plus an HTML-to-text extractor that
  preserves character offsets into the raw HTML. REUSE these; do not rewrite.

Infrastructure:
- The workflow scheduler is a Render cron (2ndactcapital-workflow-scheduler)
  ticking every 5 minutes. It fired its first production trigger at 09:00 UTC
  on 2026-10-01. Render stops any cron run after 12 hours and runs at most one
  run of a cron at a time.
- Render bills outbound traffic, INCLUDING uploads to R2, at $0.15/GB above
  the workspace allowance (5 GB on Hobby, 25 GB on Pro). Downloads from the SEC
  are inbound and free.
- SEC fair access: at most 10 requests/second PER USER across all machines,
  declared User-Agent required, excessive requests get IP blocks.

=== DECISIONS (Joe) — do not re-litigate ===

1. ONE DOCUMENT TABLE. edgar_index_filings is the manifest and pipeline state
   for every filing. portfolio.reference_filings stays the store for FETCHED
   documents (note terms already point at it). Link them by accession_number;
   do not create a third document table.

2. THE STATUS LIFECYCLE on edgar_index_filings. Replace the CHECK with the full
   set now, so sprint B does not have to alter it:
     discovered -> selected | not_selected
     selected -> fetched | fetch_failed
     fetched -> not_pricing_supplement | prefilter_skipped | ready_for_extraction
     (sprint B only) ready_for_extraction -> extraction_submitted ->
       extracted | needs_review | extraction_failed
   This sprint never writes a sprint-B status. Add: status_reason,
   document_kind ('pricing_supplement' | 'preliminary_pricing_supplement' |
   'product_supplement' | 'underlying_supplement' | 'term_sheet' | 'other'),
   selection_policy_version, attempt_count, last_attempt_at, next_attempt_at,
   reference_filing_id, detected_cusip, fetched_at.
   Also add primary_issuer_cik: the listed filer with role 'issuer' if one is
   listed, else any listed issuer, else NULL — computed at load, recomputed
   when the issuer table changes. This is what makes filtering by issuer fast.

3. SELECTION RULES ARE DATA. A new table of versioned, immutable selection
   policies (same pattern as ai_ensemble_configs: a new version is a new row,
   only retiring an old one is allowed). Policy v1: form 424B2 only; filings
   whose primary_issuer_cik is an issuer with include_status 'yes' or 'review';
   filing date from 2019-01-01; process newest first. Every selected or
   not_selected row records the policy version that decided it.

4. FETCH (stage 2), per selected filing, newest first:
   - find the main document from the filing's own index in its EDGAR folder
   - download it, gzip it, upload to R2 (compressing first is what keeps the
     upload inside Render's bandwidth allowance), record sha256 of the raw
     bytes, raw size and compressed size
   - extract text with the EXISTING extractor; store the text gzipped in R2,
     not in the database. reference_filings.extracted_text stays NULL for new
     rows (sprint B decides what trimmed text belongs in the database).
   - classify document_kind with free rules on the opening text. Preliminary
     pricing supplements ("Preliminary Pricing Supplement", "Subject to
     Completion") are NOT ready for extraction.
   - for pricing supplements only, run the existing keyword prefilter
   - detect a CUSIP by rule, and accept it only if its check digit validates
   - a failure on one filing marks THAT filing fetch_failed with a reason and a
     retry time, and the run carries on
   The 201 existing reference_filings rows are linked to their manifest rows and
   marked fetched WITHOUT re-downloading.

5. INCREMENTAL DISCOVERY. Each night, read EDGAR's DAILY index for every day
   since the newest filing_date already loaded, and insert new filings with
   the same dedupe logic. Move the parsing and loading logic out of
   load_edgar_index.py into a service module used by both the script and the
   nightly job. Filers of 424B2s who are NOT in the issuer table are surfaced on
   the monitoring screen, never auto-included.

6. THE NIGHTLY JOB. A scheduled workflow, built only from automated steps —
   NO human step, because a run waiting on a person holds open and overlap
   protection then skips every following night. The heavy work must NOT run
   inside the scheduler's 5-minute tick: the workflow's step launches a
   separate Render one-off job via the Render API (billed per second, same
   build) and returns immediately. Each job: incremental discovery, then
   selection, then fetch, within caps on filings fetched and on runtime (well
   under Render's 12 hours), throttled to 8 requests/second across ALL SEC
   traffic in the job. Only one job may run at a time — enforce with a lease
   row that expires, NOT a session-level advisory lock (the transaction pooler
   can hand the session to another client). Record every job in a pipeline-runs
   table: start, end, counts per stage, failures, bytes uploaded.
   If no Render API credential exists, report it, build everything else, and
   have the verify report this piece as BLOCKED.

7. THE MONITORING SCREEN — super-admin only. One admin page, three tabs:
   - Filings: the grid with SERVER-SIDE filter, sort and paging (717K rows
     cannot load into the browser). Filters: issuer group, form, status,
     document kind, filing date range, quarter. Each row links to the filing on
     sec.gov.
   - Progress: counts by status per quarter, and the recent pipeline runs.
   - Issuers: the issuer table; super-admin can change include_status,
     credit_entity and notes, and add an issuer — with the unlisted-filers list
     beside it so a new issuer can be promoted. Saving recomputes
     primary_issuer_cik for affected filings.
   Plus a super-admin "Run now" button with a fetch cap, which launches the
   same job.
   Reuse DataGrid. If it cannot do server-side paging, extend it without
   changing behavior for its existing users. Light theme, existing tokens.

OUT OF SCOPE: any model call; term extraction; fetching FWPs (the manifest keeps
them; policy v1 excludes them); pricing or comparisons.

=== FACTS THAT CHANGE DECISIONS ===

- RLS: every policy is per operation, and the connection must set the context
  the policy checks. A missing policy and missing context both look like
  "row not found" or silently empty reads — never "permission denied".
- Writes to these tables need SET LOCAL app.is_super_admin = 'true' inside a
  transaction (services/database.py's platform_scope() is the mechanism). Never
  a session-level SET under the transaction pooler.
- Column names come from the live database, not from this prompt.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The live DDL of the four objects created by hand — generate it from the
      database (pg_get_* functions), so the migration file matches reality.
  1b. edgar_fetch.py's actual signatures and how the existing extractor and
      keyword prefilter are called.
  1c. Whether a Render API credential exists in Doppler (names only), and which
      Render service builds apps/api with the Doppler environment — the base
      service a one-off job would use.
  1d. Whether DataGrid can do server-side paging, and what extending it would
      touch.
  1e. Whether anything references the VERIFY FIXTURE row in reference_filings.

=== TASK 2: MIGRATION FILE for the four hand-made objects (from 1a, exactly) ===
=== TASK 3: SCHEMA — lifecycle, new columns, policies table, runs table, lease ===
Apply via MCP and verify each landed with a follow-up query. Four RLS policies
on every new table, matching the existing pattern. Backfill primary_issuer_cik.
Link and mark the 201 existing documents. Delete the VERIFY FIXTURE row if
1e found nothing referencing it; otherwise report it.
=== TASK 4: SERVICES — discovery module, selection, fetch, job runner ===
=== TASK 5: THE NIGHTLY WORKFLOW AND THE RENDER JOB LAUNCH ===
=== TASK 6: THE MONITORING SCREEN ===
=== TASK 7: STATUS === update docs/PROJECT_STATUS.md, and commit
apps/api/scripts/load_edgar_index.py if it is uncommitted.

=== VERIFY: apps/api/scripts/verify_edgarpipelinea.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only. MUST print 'TOTAL: N PASS, M FAIL'
and exit non-zero on failure. Hydrate secrets from Doppler over HTTPS at
startup (verify_modelresearch.py is the pattern).

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; the helper asserts
    isinstance(passed, bool).
  - Never derive any unique value from a slice of a fixture UUID.
  - Set RLS context on EVERY read.
  - Clear audit_log, assistant_activities and agent_proposals BEFORE deleting
    fixture users.
  - Source-code checks must target the actual risky pattern, not a bare
    character such as "||" that legitimate guard clauses also use.
  - The real 716,953 manifest rows must not be changed by the verify, except
    where it restores exactly what it touched. Snapshot before, compare after.
  - Fetch tests use a small fixed sample (about 10 real filings across several
    issuers), write under a test R2 prefix, and delete those objects in
    teardown. Never run a large fetch from the verify.

Assertions:
  [Y] Task 1's five findings reported
  [Y] The migration file's definitions match the live objects
  [Y] The lifecycle CHECK accepts every listed status and refuses an unknown one
  [Y] primary_issuer_cik: a co-listed parent/subsidiary filing resolves to the
      subsidiary (role 'issuer'); filtering by issuer group on the full table
      returns in under one second
  [Y] Policy v1 selects exactly the expected set on a fixture sample, records
      its version on every decided row, and an edit to a stored policy is
      refused (only retiring is allowed)
  [Y] Incremental discovery over one fixed past day is idempotent: a second run
      inserts nothing, and co-listed duplicates collapse to one filing
  [Y] Fetch on the sample: each R2 object exists and is gzipped; sha256 of the
      decompressed bytes matches the recorded hash; sizes are recorded; a
      second run re-uploads nothing; statuses are 'fetched'
  [Y] A deliberately bad accession becomes fetch_failed with a reason, an
      attempt count and a retry time — and the run still finishes the others
  [Y] Classification rules on fixture openings: each document_kind; a
      preliminary pricing supplement is never ready_for_extraction
  [Y] CUSIP rule: a valid CUSIP is accepted; one with a wrong check digit is
      rejected
  [Y] Measured SEC request rate during the sample fetch never exceeds 8/second,
      and every request carried the declared User-Agent
  [Y] The lease: a second job started while one holds it is refused; an expired
      lease can be taken over
  [Y] The fetch cap is honored
  [Y] The nightly workflow has no human step, and its run completes
      immediately rather than waiting for the job; the job is recorded in the
      pipeline-runs table (or BLOCKED if no Render API credential)
  [Y] The 201 existing documents are linked and marked fetched, with zero new
      downloads for them
  [Y] The VERIFY FIXTURE row is gone, or a FIND explains why it was kept
  [Y] Monitoring API: super-admin 200; org admin and member 403 on the
      IDENTICAL request
  [Y] Server-side paging returns correct totals and pages; sorting and
      filtering work on the full table
  [Y] An issuer edit by super-admin persists (independent re-read) and
      recomputes primary_issuer_cik; the same edit by an org admin is refused
      and leaves the row unchanged
  [Y] Teardown: test R2 objects deleted; fixture rows gone; every manifest row
      the verify touched is restored exactly
  [Y] npm run build exits 0
