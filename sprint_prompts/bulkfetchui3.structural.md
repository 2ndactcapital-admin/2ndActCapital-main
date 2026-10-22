BULKFETCHUI3 — EDGAR Bulk download tab: a sortable, full-height Institutions
table with Downloaded and Stored columns and a Refresh button; Refresh index
moved into the Institutions box; a weekday automatic index refresh; a fix for
Goldman Sachs finals misread as 'other' plus a reclassify command over stored
text; a regression runner script. 9 tasks + verification.

Context: the Bulk download tab (/admin/edgar-pipeline, Bulk download) was built
by bulkfetchui / bulkfetchui2 / bugfix1 (all merged; main @ 416a763). Joe's
requests (2026-10-22): sort the Institutions columns; make the table fill the
Institutions box; add "Downloaded" and "Stored" columns; a Refresh button for
the counts; move "Refresh index" next to "Select none". The filing index has
never been refreshed on its own (last indexed 2026-10-01; zero refresh_index
runs). Separately, Goldman Sachs final pricing supplements whose cover is an
image were classified 'other' and set not_pricing_supplement.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main @ 416a763, live DB 2026-10-22):
- Front end: apps/web/components/admin/EdgarBulkDownload.jsx (BulkDownloadTab)
  renders <InstitutionPicker rows selected onToggle onAll onNone/> from
  apps/web/components/admin/InstitutionPicker.jsx (a <table> inside
  "max-h-80 overflow-y-auto", sticky thead, columns: select, Institution,
  Entities, last 3 years, Total; expandable filer sub-rows; search box, Select
  all, Select none). The Time frame card holds
  "<span>Index loaded through {lastIndexed}</span>" + a "Refresh index" button
  shown only when `writable = canWrite(inst)`, calling
  queueRefreshIndexAction({ to: null, target }); refreshOutcome renders below.
  Node test bugfix1.test.mjs renders InstitutionPicker — keep it passing.
- API: GET /admin/edgar/bulk/institutions (routers/edgar_pipeline_admin.py,
  super admin) → services/edgar_bulk_queue.institutions(conn, use_cache=True):
  per filer CIK and year, count of portfolio.edgar_index_filings with
  pipeline_status = 'selected' (still to fetch) for the source's form types;
  cached 60 s (_INSTITUTIONS_CACHE) because live runs dirty the visibility map.
- portfolio.edgar_index_filings.pipeline_status today: selected 522,166;
  not_selected 180,114; not_pricing_supplement 14,432; ready_for_extraction
  5,366; prefilter_skipped 2,047; fetched 200; fetch_failed 1. Columns include
  document_kind, status_reason, fetched_at, primary_issuer_cik,
  reference_filing_id, attempt_count, next_attempt_at.
  not_pricing_supplement by kind: preliminary 10,953, other 3,243, term_sheet
  147, product_supplement 80, underlying_supplement 9.
- Runs: portfolio.edgar_pipeline_runs (run_kind bulk_fetch | refresh_index,
  target laptop | render, trigger_source, requested_by, details, progress,
  heartbeat_at …). services/edgar_bulk.refresh_index(conn, client, through=…)
  reads EDGAR daily index files from the last loaded date (inclusive, ON
  CONFLICT DO NOTHING) through yesterday UTC and applies the active selection
  policy to the new rows. It is light: one small file per weekday.
- Scheduler: Render `type: cron` service 2ndactcapital-workflow-scheduler
  (render.yaml, every 5 min, UTC only, apps/api/workflow_scheduler_tick.py →
  services/workflow_scheduler.py). Whether it is actually deployed on Render
  is NOT known — Task 1 finds out from code/config, and the report says what
  the operator must do.
- Classifier: edgar_pipeline.classify_document via edgar_bulk.CLASSIFIERS
  ["structured_note_cover"]; 'other' = "no title phrase in the opening".
  Example miss: 0001193125-26-410827 (GS Finance Corp., CIK 1419828), a final
  ("Pricing Supplement No. 26,690", Leveraged Buffered S&P 500 Index-Linked
  Notes due 2029) whose cover is an image. Stored text is read with
  edgar_bulk.stored_text(row) (DB column, else gzipped text in R2, else the
  document) — no SEC request. edgar_bulk.reprocess_fetched is the existing
  pattern for re-deciding rows from stored text.
- authz1: every route has an explicit gate and an AUTHZ1_ROUTES.json entry
  (apps/api/scripts/authz1_routes.py --check must say CURRENT); never change
  an existing gate.
- Verify scripts: 194 apps/api/scripts/verify_*.py exist. Set
  AUTHZ_TEST_MODE=1 before importing the app; VERIFY_NESTED=1 means "skip your
  own regression section". bulkfetchui tests refuse to run while a laptop
  worker is heartbeating.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any verify script.
- Regression policy changed 2026-10-22 (Task 8 writes it into the repo copy of
  the standard): no full regression in a sprint verify; one directly-affected
  prior script only when shared code changes. This sprint changes the shared
  classifier, so it re-runs verify_bulkfetch.py ONCE.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never poll in a loop, never start a
script in the background; run every command synchronously in the foreground
and read its output directly. Write the verify script but DO NOT RUN IT, and
do not run npm run build. Never make an SEC request in tests (mock the HTTP
client). Never change a real edgar_index_filings row from the sprint session:
the reclassify command is written and tested on fixtures; the operator runs it
on real rows. Never print a secret. Column names from the live DB
(docs/schema_snapshot.sql + information_schema). Never change an existing
route's gate. Labels and vocabularies come from the API envelope, not the
client. Light/cream UI with the existing tokens. Commit ALL sprint files (new
and modified, excluding sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Where "stored in R2" is recorded for an index filing (the reference
      filing row, r2_key / text_r2_key, which table) and how a run decides to
      keep or discard by --kinds. Give the exact SQL that counts, per filer
      CIK, (i) Downloaded = every filing the bulk fetch has handled — peeked,
      fetched, decided or failed (state the status set and why), and
      (ii) Stored = filings whose document is kept in R2. Run both against
      the live DB read-only and report totals (Wells Fargo, Goldman Sachs,
      Jefferies and all). Check the existing index can serve them; if not,
      say what index is needed.
  1b. How the 3,243 'other' / not_pricing_supplement rows break down by
      issuer, how many have stored text, and what their openings look like:
      read the stored text of 10 GS samples (read-only) including
      0001193125-26-410827, and 5 'other' rows from other banks. State the
      rule that separates a final from a true 'other' (e.g. "Pricing
      Supplement No." / "Pricing Supplement dated" within the first N
      characters of the text, an issuer filing-number pattern, the filename)
      and its result on the samples.
  1c. Whether the workflow scheduler's Render cron service is deployed
      (render.yaml, any deploy notes in docs/PROJECT_STATUS.md /
      WORKFLOW_SCHEDULER_DESIGN_V1.md), what credentials and network it has,
      and which of these fits the weekday index refresh: (A) a step in the
      existing tick that runs refresh_index in-process once per weekday after
      10:30 UTC, guarded so only one tick does it per day; (B) a new small
      Render cron service; (C) queue a refresh_index run for the laptop
      worker. Prefer A if the service is deployed and can reach SEC; say what
      the operator must do in every case.
  1d. How the zero-byte download (1 fetch_failed: "the main document
      downloaded as zero bytes") is retried today, if at all.

=== TASK 2: INSTITUTIONS — DOWNLOADED AND STORED COLUMNS, REFRESH ===
Extend the institutions payload: per institution and per filer, `downloaded`
and `stored` (definitions from 1a, all years — the same scope as Total), plus
`as_of` (when the numbers were computed). Column labels and their one-line
explanations come from the envelope's vocabularies (e.g. "Downloaded —
handled by a bulk download: fetched, peeked or failed"; "Stored — kept in
storage, by the document kinds chosen at the time"). Add a fresh read:
GET /admin/edgar/bulk/institutions?fresh=1 bypasses the 60 s cache (same
gate: super admin; no new route unless a new path is unavoidable — if one is
added it gets a gate and an AUTHZ1_ROUTES entry).

=== TASK 3: INSTITUTIONS TABLE — SORT, HEIGHT, REFRESH, REFRESH INDEX ===
In InstitutionPicker:
  - Every column header sortable (click toggles ascending/descending; numeric
    sort for counts, case-insensitive for names; aria-sort set; a small
    arrow on the sorted column). Default: Institution ascending. Filer
    sub-rows stay under their institution and sort the same way among
    themselves.
  - New right-hand columns Downloaded and Stored after Total.
  - Fill the Institutions box: on wide screens the card is as tall as the
    right-hand column (Time frame, Document kinds, Run on …) and the table
    scrolls inside it with the header row sticky; below the xl breakpoint
    the table scrolls within max 70vh. No fixed max-h-80.
  - Header row of the card, left to right: "Institutions" title, search box,
    Select all, Select none, Refresh (re-reads the counts with ?fresh=1;
    shows "as of h:mm" beside it), then — only when the envelope is writable
    — "Refresh index", with "Index loaded through <date>." as small muted
    text next to it. The Refresh index result message (and its command, as
    today) shows under the header. Remove both from the Time frame card.
    If a refresh_index run is queued or running, the button reads "Index
    refresh queued" / "Index refreshing" and is disabled.
  - While a bulk run is queued or running, the counts re-read every 60 s
    (alongside the existing 10 s runs refresh); not otherwise.
  - The wrap works at narrow widths (buttons wrap; nothing overflows).

=== TASK 4: WEEKDAY AUTOMATIC INDEX REFRESH ===
Implement the option chosen in 1c. Every weekday, once, after 10:30 UTC
(~6:30 am ET): refresh the index through yesterday and record a
portfolio.edgar_pipeline_runs row (run_kind refresh_index, trigger_source
'schedule', the counts in details). Idempotent per day (a second tick or
restart the same day does nothing). A failure is recorded on the run row and
does not block the next day. The Bulk download runs table shows these rows
like any other run. Add the setting EDGAR_INDEX_AUTO_REFRESH (default on) to
turn it off. If the chosen option needs the operator (deploy, env var, sync),
the report lists the exact steps.

=== TASK 5: CLASSIFIER FIX AND RECLASSIFY COMMAND ===
  - Change classify_document (or the source's classifier) so a final whose
    cover is an image is a pricing_supplement by the rule from 1b. It must not
    turn a preliminary, product, underlying or term-sheet document into a
    final: keep every existing classifier test passing and add cases for the
    GS sample and for each true 'other' sample.
  - New `run_bulk_fetch.py --reclassify-other` (and a service function next
    to reprocess_fetched): for rows with document_kind 'other' (optional
    --issuer-cik, --accessions, --from/--to), read the stored text (no SEC
    request — the function has no HTTP client), re-classify, and move each
    changed row to the status the normal decision gives it (with the active
    policy version, as reprocess_fetched does), noting
    status_reason 'reclassified:<old kind>→<new kind>'. Rows with no stored
    text are listed and left alone. Default is a DRY RUN that prints counts by
    issuer and old→new kind and 10 example accessions; --apply writes.
    Idempotent: a second --apply changes nothing.
  - Zero-byte download (1d): treat it as a retryable fetch failure (attempt
    count + next_attempt_at) so the next run tries it again, unless 1d shows
    this already happens; say which.

=== TASK 6: REGRESSION RUNNER ===
scripts/run_regressions.sh: runs every script listed in
scripts/regression_set.txt (one verify_*.py name per line, # comments
allowed), one after another in the foreground, each as
`doppler run -p hollisworks -c prd -- apps/api/venv/bin/python -u
apps/api/scripts/<name>` with VERIFY_NESTED=1 and a 1200 s timeout, output
to sprint_prompts/logs/regressions/<YYYY-MM-DD>/<name>.out. It prints one line
per script — name, PASS n, FAIL m, seconds (or TIMEOUT / CRASH) — and a final
summary, and exits non-zero if any failed. Before starting it warns and stops
if a laptop bulk worker is heartbeating (the bulkfetchui tests refuse). Seed
regression_set.txt with: verify_authz1.py, verify_usermgmt1.py,
verify_navregroup.py, verify_bugfix1.py, verify_dealroom1.py,
verify_dealroom2.py, verify_bulkfetch.py, verify_bulkfetchui.py,
verify_bulkfetchui2.py, verify_bulkfetchui3.py, verify_edgarpipelinea.py.
Do NOT run it.

=== TASK 7: REAL PROOF ===
  - Downloaded / Stored: on fixture filings for a fixture institution with a
    known mix of statuses and stored/not-stored rows, the payload's numbers
    equal the 1a SQL run directly, per institution and per filer; a status
    outside the Downloaded set is excluded (negative case); ?fresh=1 returns
    new numbers after a fixture change while the cached call within 60 s
    returns the old ones.
  - Gate: a non-super-admin gets 403 on the identical fresh and cached
    request; super admin 200.
  - Sort / height / buttons (node tests on the real component): each header
    sorts both ways with the right comparator; sub-rows follow their
    institution; Downloaded and Stored render; Refresh index and its
    "Index loaded through" note render in the Institutions header after
    Select none and NOT in the Time frame card; with a non-writable envelope
    no Refresh index button renders; an active refresh_index run disables it
    with the right label; no max-h-80 remains.
  - Scheduled refresh: with a mocked SEC client and fixture index rows, the
    first eligible tick of a weekday refreshes and writes one run row with
    trigger_source 'schedule'; a second tick the same day writes nothing; a
    weekend or before-10:30-UTC tick does nothing; the off switch stops it; a
    mocked failure is recorded on the run row (re-read each time).
  - Classifier: the GS image-cover sample text becomes pricing_supplement;
    every existing classifier case keeps its kind; each true-'other' sample
    stays 'other'.
  - Reclassify: on fixture rows (mocked R2), the dry run writes nothing
    (before/after equal); --apply moves exactly the expected rows with the
    status_reason note; rows without stored text are untouched; a second
    --apply changes nothing; it never constructs an HTTP client.
  - Zero-byte: a mocked zero-byte document leaves the row retryable (or the
    report states the existing behavior, and a test proves it).
  - run_regressions.sh: `bash -n` passes; with a stub list of two tiny fake
    scripts (one passing, one failing) in a temp dir it prints both lines and
    exits non-zero — run it ONLY in this stub mode.
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_bulkfetch.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 1200 s timeout (the classifier is shared).

=== TASK 8: STANDARD — REGRESSION POLICY ===
In docs/SPRINT_WORKFLOW_STANDARD.md (the repo copy — edit it, do not replace
it), replace the skeleton's "- Regression check: re-run every PRIOR sprint's
own verify script …" bullet (through "… (see "regression-chain nesting"
below).") with:

  - Regression: NOT part of a normal sprint's verify (Joe, 2026-10-22).
    Every verify instead runs the cheap guards: its own checks plus
    `authz1_routes.py --check` (every route gated, no gate changed).
    EXCEPTION: a sprint that changes something shared — auth/authz,
    RLS, a table or service other features read — re-runs the ONE
    most directly affected prior verify, EXACTLY ONCE, flat, after
    teardown, with VERIFY_NESTED=1 and a 1200 s timeout. Name that
    script in the prompt; never "every prior script".
    The full suite runs separately, on the operator's schedule
    (weekly / before a big push): scripts/run_regressions.sh, every
    verify_*.py one after another, one summary line each. Failures
    go to the backlog.

and append to the "Regression-chain nesting" gotcha: "Since 2026-10-22
regressions are out of per-sprint verifies by default (see REAL PROOF in the
skeleton); VERIFY_NESTED=1 stays the guard for the one allowed exception."
Change nothing else in that file.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md: the Bulk download changes, the scheduled index
refresh (and any operator step), the classifier rule and the reclassify
command with the exact dry-run / --apply commands, and run_regressions.sh.

=== VERIFICATION: apps/api/scripts/verify_bulkfetchui3.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. Mock R2 and
every SEC request. Refuse to start (one clear line, exit 2) if a laptop bulk
worker is heartbeating. Wrap every section so an exception is a [FAIL];
teardown at start and end in FK child-before-parent order from pg_constraint
(audit_log, assistant_activities and agent_proposals by fixture id before
fixture users), then re-read every touched table for its exact pre-test count.
Fixture users created explicitly with the auth0_sub the mocked token carries,
is_test = true. Read every UNIQUE (including partial unique indexes), CHECK
and FK constraint on the touched tables from the catalog before seeding and
seed to satisfy them. Fixture filings use accession numbers and CIKs that
cannot exist at SEC (a reserved test prefix) and a fixture source/institution.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Exclude this run's own fixtures from any "no leftovers" check.
  - Never spawn a background process. The regression subprocess gets
    VERIFY_NESTED=1, runs after teardown, 1200 s timeout; with
    VERIFY_NESTED=1 this script skips its regression section with one
    '[SKIP] nested run' line.
  - Never touch real edgar_index_filings, edgar_pipeline_runs, documents or
    users rows: checksum the real rows of edgar_index_filings for the three
    real banks (count + checksum of status, kind) before and after.

Assertions:
  [Y] Task 1 findings 1a–1d reported with files, SQL and live totals
  [Y] Downloaded / Stored equal the 1a SQL per institution and per filer;
      excluded statuses excluded; fresh vs cached proven
  [Y] Institutions gate: non-super-admin 403, super admin 200, identical
      request
  [Y] node tests: sorting both ways on every column, sub-rows follow, new
      columns, Refresh with "as of", Refresh index + note in the Institutions
      header and absent from Time frame, hidden when not writable, disabled
      while a refresh_index run is active, no max-h-80
  [Y] Scheduled refresh: once per weekday after 10:30 UTC, idempotent, off
      switch, failure recorded, trigger_source 'schedule' (mocked SEC)
  [Y] Classifier: GS image-cover final → pricing_supplement; all existing
      cases unchanged; true 'other' samples stay 'other'
  [Y] Reclassify: dry run writes nothing; --apply moves exactly the expected
      rows with the note; no-text rows untouched; second --apply no-op; no
      HTTP client
  [Y] Zero-byte download retryable (or existing behavior proven)
  [Y] run_regressions.sh: syntax OK; stub mode prints one line per script and
      exits non-zero on a failure
  [Y] docs/SPRINT_WORKFLOW_STANDARD.md: the new regression bullet present, the
      old "re-run every PRIOR sprint" text gone, the rest of the file
      byte-identical to main (diff limited to the two edits)
  [Y] Every new route (if any) gated and in AUTHZ1_ROUTES.json; --check
      CURRENT; no existing gate changed
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script,
      not during the sprint)
  [Y] Regression: verify_bulkfetch.py ONCE, flat, after teardown,
      VERIFY_NESTED=1; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real filings' count + checksum unchanged
