EDGAR REFERENCE CORPUS — fetcher, storage, and HTML extraction.
5 tasks + verification.

WHAT THIS IS: a harvester that pulls 424B2 and FWP filings from SEC
EDGAR into a GLOBAL (non-org-scoped) reference corpus. This is public
reference data, not tenant documents. It does NOT go into Chancery's
`documents` table and does NOT use the Chancery drop path.

WHAT THIS IS NOT — DO NOT BUILD THESE:
  - Term extraction. No payoff fields, no barriers, no caps. That is a
    later sprint. This sprint stores raw filings and plain text ONLY.
  - Any write to portfolio.securities_global or its child tables.
  - Any Chancery classifier call. Form type is known from EDGAR
    metadata; classification adds nothing.
  - Any entity linkage to members or orgs.
  - A full historical backfill. This sprint proves the pipeline on a
    SMALL bounded sample (see Task 4).

STANDING RULES: org_id never from request body; Decimal for money; RLS
policy in the same migration as the table; no hardcoded hex or brand
strings; light theme; verify scripts are pass/fail only with no
interactive prompts.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never stop
and wait. If uncertain whether to continue, continue. The exceptions
are the explicit STOP/BLOCKED gates below, which are honest gates.


=== TASK 1: DISCOVER — do not assume ===

Read the real current code, report, THEN CONTINUE IMMEDIATELY.

  1a. R2 access from this environment. Read apps/api/services/storage.py
      and report the exact env var names it uses and whether they are
      present in apps/api/.env.

      *** HARD GATE ***
      Attempt a REAL round-trip: write a small test object to
      hollisworks-docs under reference/_selftest/, read it back, delete
      it. If this fails for ANY reason, STOP and report BLOCKED.
      DO NOT mock, simulate, or proceed with a stubbed storage layer.
      A prior sprint proceeded past exactly this failure and produced
      an empty bucket that looked like success.

  2a. Existing R2 key prefixes are deals/, entity-docs/, spvs/. There
      is NO non-org-scoped prefix convention yet. Report what
      services/storage.py assumes about key structure, and whether it
      hardcodes an org_id segment anywhere.

  3a. HTML handling. chancery_intake.py has _strip_html at ~line 529 —
      report whether it is a general HTML-to-text extractor or an
      email-body helper only. Report whether beautifulsoup4, lxml, or
      selectolax are already in apps/api/requirements.txt.

  4a. Report whether httpx or aiohttp is already a dependency. Prefer
      whichever exists; do not add a second HTTP client.

  5a. Confirm portfolio schema exists with securities_global and its
      three child tables (it does). Report their names only — this
      sprint does not write to them.


=== TASK 2: SCHEMA (Part 1 — apply via Supabase MCP) ===

Create `portfolio.reference_filings`. GLOBAL reference data: NO org_id.

Columns:
  id                    uuid pk default uuid_generate_v4()
  cik                   text not null
  filer_name            text not null
  form_type             text not null      -- '424B2' | 'FWP'
  accession_number      text not null
  filing_date           date not null
  file_number           text               -- 333-xxxxx shelf linkage
  primary_document      text not null      -- filename within the filing
  source_url            text not null      -- canonical EDGAR URL
  r2_key                text               -- raw bytes location
  content_hash          text               -- sha256 of raw bytes
  byte_size             bigint
  extracted_text        text               -- plain text, no term parsing
  extraction_status     text not null default 'pending'
  extraction_error      text
  retention_classification text not null default 'public_reference'
  created_at            timestamptz not null default now()
  updated_at            timestamptz not null default now()

CHECK constraints:
  form_type IN ('424B2','FWP')
  extraction_status IN ('pending','fetched','extracted','failed','skipped')
  retention_classification = 'public_reference'
    -- deliberate explicit value; the retention system when built must
    -- find an intentional classification, never a NULL to guess at

Unique:
  UNIQUE (accession_number, primary_document)

Indexes:
  (cik, filing_date DESC)
  (form_type, filing_date DESC)
  (extraction_status) WHERE extraction_status <> 'extracted'
  (content_hash) WHERE content_hash IS NOT NULL

RLS — enable, and use the FOUR-POLICY global shape copied from
public.permissions. NOT a single FOR ALL:
  reference_filings_global_read   FOR SELECT USING (true)
  reference_filings_super_admin_insert FOR INSERT
    WITH CHECK (current_setting('app.is_super_admin', true) = 'true')
  reference_filings_super_admin_update FOR UPDATE
    USING (same) WITH CHECK (same)
  reference_filings_super_admin_delete FOR DELETE USING (same)

Apply via the Supabase MCP apply_migration tool directly. Verify it
landed with a follow-up execute_sql. Do not hand SQL to the user.


=== TASK 3: FETCHER SERVICE ===

New file: apps/api/services/edgar_fetch.py

SEC compliance — non-negotiable, SEC blocks violators:
  - Declared User-Agent on EVERY request, format:
    "Hollisworks/1.0 (joe@2ndactcapital.com)"
    Read the contact from an env var EDGAR_USER_AGENT with a loud
    failure if unset. Do NOT default silently.
  - Hard rate limit of 10 requests/second, enforced in code with a
    real sleep, not best-effort.
  - Retry with backoff on 429 and 5xx. Never hammer.

Use the QUARTERLY FULL-INDEX files, not the full-text search API:
  https://www.sec.gov/Archives/edgar/full-index/{YYYY}/QTR{1-4}/master.idx
Pipe-delimited: CIK|Company Name|Form Type|Date Filed|Filename.
Filter rows where form type is 424B2 or FWP. ~30 index files cover
2019-present, versus 250k individual search calls.

Functions:
  fetch_index(year, quarter) -> list of filing metadata dicts
  fetch_filing(accession, primary_doc) -> raw bytes
  store_filing(pool, meta, raw_bytes) -> uuid
    - writes bytes to R2 under reference/edgar/{cik}/{accession}/{doc}
    - the reference/ prefix is NEW and deliberately non-org-scoped
    - computes sha256, sets content_hash and byte_size
    - upserts on (accession_number, primary_document) — IDEMPOTENT,
      re-running must not duplicate rows or re-upload identical bytes

IMPORTANT: 424B2 is the prospectus supplement form for ANY shelf
takedown — plain vanilla notes, MTNs, preferred, covered bonds all use
it. Form type over-selects heavily. Apply a cheap deterministic
keyword prefilter on extracted text for: barrier, buffer, autocall,
contingent coupon, participation rate, initial level, underlying.
Rows failing the prefilter get extraction_status = 'skipped' and are
RETAINED, not deleted — the negative set matters for later precision
measurement.


=== TASK 4: HTML EXTRACTION + BOUNDED SAMPLE RUN ===

424B2 filings are HTML, not scanned PDFs. Textract is NOT involved.

Add HTML extraction. If Task 3a found no general HTML extractor, add
one using whichever of selectolax / lxml / beautifulsoup4 is already
present, or add selectolax (fastest for this volume).

Requirements:
  - Preserve CHARACTER OFFSETS into the stored raw HTML. A later sprint
    must be able to point at the exact span a term was read from. This
    is the traceability property; do not discard positional data the
    way Textract Geometry currently is.
  - Store plain text in extracted_text.
  - Do NOT parse terms. No barriers, no caps, no dates.

Then run a BOUNDED sample: ONE quarter, capped at 200 filings.
Do not attempt a full backfill in this sprint.

Report actual numbers:
  - index rows found, 424B2 vs FWP split
  - filings fetched, bytes stored
  - passed vs skipped by keyword prefilter
  - extraction failures with reasons


=== TASK 5: UPDATE PROJECT STATUS ===

Update docs/PROJECT_STATUS.md in the same commit: what was built, the
sample-run numbers, and any BLOCKED outcome recorded honestly.


=== VERIFICATION: apps/api/scripts/verify_edgarcorpus.py ===

Pass/fail only. No prompts. Idempotent. Teardown at start AND end.
Assert real values, not existence.

  [ ] portfolio.reference_filings exists, RLS enabled, exactly 4
      policies, and NO org_id column (assert the column is absent)
  [ ] Cross-check the four policy commands are SELECT/INSERT/UPDATE/
      DELETE — not a single FOR ALL
  [ ] Global read works under the app_service (non-bypass) connection
      with NO org context set — a global table must be readable without
      an org, which is the whole point. Use APP_SERVICE_DATABASE_URL.
  [ ] NEGATIVE: insert under app_service WITHOUT is_super_admin is
      REJECTED. Assert the rejection, not just that a insert happened.
  [ ] IDEMPOTENCY: store_filing called twice with identical input
      produces exactly ONE row and does not change content_hash
  [ ] R2 round-trip through services/storage.py: object written by the
      sample run is fetchable by its stored r2_key and byte length
      equals byte_size on the row
  [ ] Report the ACTUAL count of rows created by the sample run and
      the passed/skipped split. If zero rows, FAIL — a corpus sprint
      that stored nothing is not a pass.
  [ ] Assert extracted_text is non-empty for at least one row with
      extraction_status='extracted'
  [ ] Assert every row has retention_classification='public_reference'
      (never NULL)
