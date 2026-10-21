BULKFETCH UI — a "Bulk download" screen on the EDGAR Pipeline page: pick
institutions (with their legal entities), a date range and document kinds, see a
live preview (counts, time, storage cost), queue the run to a worker on the
LAPTOP (default) or on RENDER, and watch progress / stop it. Plus the worker
mode for run_bulk_fetch.py. 7 tasks + verification.

Context: the bulkfetch sprint (merged to main, verify_bulkfetch 106/106) built
apps/api/scripts/run_bulk_fetch.py — a CLI with --banks, --from/--to, --kinds,
--dry-run, --peek-test, --refresh-index, a shared 8/s SEC limiter, claim/resume
and a run record. Joe wants to pick institutions and time frames in the app
instead of typing flags. A run takes a day or more and the API is on Render's
FREE plan (spins down, kills long jobs), so the API never runs a download
itself: the screen QUEUES a run and a separate worker process executes it. The
worker can run on the laptop (tmux) — the default — or later as a Render
background worker, which is NOT set up yet; choosing Render must fail cleanly
with a clear message until it is.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (checked live 2026-10-18):
- portfolio.edgar_index_filings: 716,953 rows; pipeline_status counts:
  selected 537,027 · not_selected 178,868 · not_pricing_supplement 503 ·
  ready_for_extraction 275 · fetched 200 · prefilter_skipped 80 · discovered 0.
  Index loaded through 2026-10-01.
- Institutions are structured_note_issuers.issuer_group values; one group spans
  several filer CIKs (e.g. JPMorgan Chase & Co. + JPMorgan Chase Financial
  Company LLC). 2025/2026 selected 424B2 counts: JPMorgan 18,904/17,499 ·
  Morgan Stanley 14,585/12,415 · Goldman Sachs 9,077/12,416 · UBS
  11,094/10,381 · Bank of America 9,476/7,876 · Wells Fargo 277/798.
- run_bulk_fetch.py exists with the flags above; the bulkfetch sprint added
  edgar_index_filings.claimed_by / claimed_until and migration
  bulkfetch_sources_claims_supplements.sql. Whether the run record is
  edgar_pipeline_runs (it has run_kind, cohort_id) or a new edgar_fetch_runs
  table is for Task 1 to confirm.
- RENDER_API_KEY exists in Doppler and resolves the base API service
  crn-daln4obl550s73brmiq0 (edgarpipelinea). There is NO Render background
  worker service today.
- The EDGAR Pipeline screen (EdgarPipelineMonitor.jsx) and the Cohorts tab
  (EdgarCohorts.jsx) both use DataGrid with serverSide mode. EDGAR admin
  screens are super-admin only.
- The app auto-provisions unknown auth subs as 'member' — verify fixture users
  must be created explicitly (this leaked users before).
- note_gold_values holds Joe's real gold answers (79 rows) — never touch them.
- Baselines (do NOT re-run except as stated): verify_bulkfetch 106/106,
  verify_edgarpipelinea 102/102, verify_edgarcohorts 157/157, verify_notefix2
  108/108.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism — nothing will ever notify you that a script finished.
Never wait for one, never poll in a loop for a file, never start a script in the
background. Run every command synchronously in the foreground. Make NO real SEC
or R2 requests and do NOT create, deploy or modify any Render service — the
Render option is detected and refused when not configured; the operator sets
Render up later. Write the verify script but DO NOT RUN IT. Never print a secret
value. Column names come from the live database, not this prompt. UI: light
theme, whites and creams, matching the existing admin screens. No AI model
anywhere in this sprint.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. run_bulk_fetch.py's structure: which functions compute the dry-run scope
      (per institution / filer entity / year), ETA and storage cost, and run a
      fetch — and whether they can be imported by the API and by a worker loop
      without the CLI.
  1b. The run record table and its columns, status CHECK values and RLS; how a
      run's progress (done, remaining, by kind, failures, bytes) is stored.
  1c. How --refresh-index works and how long it takes, so it can be queued as a
      run of its own.
  1d. The EDGAR Pipeline page's tab structure, routing and super-admin gate
      (API dependency and front-end guard).
  1e. What the API can learn about Render without changing anything: whether
      a worker service id is configured anywhere (Doppler names only), and
      what a read-only Render API call needs.

=== TASK 2: QUEUED RUNS (SCHEMA) ===
Per 1b, extend the existing run table (or add edgar_fetch_runs if none fits)
with: run_kind ('bulk_fetch' | 'refresh_index'), target ('laptop' | 'render',
default 'laptop'), params jsonb (banks, from, to, kinds, peek, keep
preliminaries, concurrency, max_rps), requested_by, queued_at, started_at,
finished_at, worker_id, worker_host, heartbeat_at, stop_requested boolean,
progress jsonb, error. Status values include queued, running, succeeded,
failed, stopped, cancelled (read the live CHECK first; extend, never drop a
value in use). Migration file + live apply + RLS global pattern; a partial
index for queued runs by target. A run is claimed with FOR UPDATE SKIP LOCKED.

=== TASK 3: WORKER MODE ===
apps/api/scripts/run_bulk_fetch.py --worker [--target laptop|render]
[--poll-seconds 15] [--once]
  - Loop: heartbeat a worker row/field every 30 s; claim the oldest queued run
    for its target; execute it with the SAME code path as the CLI (no second
    implementation); write progress every 100 filings and on finish.
  - Honour stop_requested: finish in-flight filings, mark 'stopped'. Ctrl-C:
    same, then exit. A crash leaves the run 'running' with a stale heartbeat;
    the API shows it as "worker lost" and offers Requeue (the claim/resume
    logic means a requeued run continues where it stopped).
  - One run at a time per worker; the shared 8/s limit still applies across
    workers (two workers must not exceed 8/s combined — reuse the existing
    limiter design or refuse a second running run).
  - --target render is the entrypoint a future Render background worker will
    use; add a commented render.yaml worker block and the setup steps in
    PROJECT_STATUS, but create nothing on Render.

=== TASK 4: API ===
Super-admin only (same gate as EDGAR screens):
  - GET  institutions: issuer groups with their filer entities (name, CIK) and
    selected counts by year.
  - POST preview {banks, from, to, kinds, peek}: the dry-run result from 1a —
    counts per institution and year, per filer entity, total, ETA at the
    current rate, storage and monthly cost; refuses a To after the last
    indexed date with "Refresh the index first" and the last date. Read-only,
    under 2 s on the live 717K table (report the measured time; add an index if
    needed).
  - POST runs {params, target}: validates params, then by target:
      laptop: queue it; if no laptop worker heartbeat in the last 2 minutes,
              still queue it and return worker_online=false plus the exact
              command to start one.
      render: if no Render worker is configured or no Render worker heartbeat
              in the last 5 minutes, REFUSE (HTTP 409, nothing queued) with
              "Render worker is not set up — run on Laptop, or see setup steps"
              and a link/anchor to the steps. If configured and alive, queue it.
  - POST runs/refresh-index {to?, target}: queue a refresh_index run.
  - GET  runs (paged) and GET runs/{id}: status, params, target, worker, live
    progress, ETA, failures; derived "worker lost" when running with a stale
    heartbeat.
  - POST runs/{id}/stop, /cancel (queued only), /requeue (stopped, failed or
    worker lost).
  - GET  workers: last heartbeat per target, host, current run.

=== TASK 5: THE SCREEN ===
A "Bulk download" tab on the EDGAR Pipeline page:
  - Institutions: multi-select list with search, each row expandable to show
    its legal entities and CIKs with counts; Select all / none.
  - Time frame: From / To date pickers plus presets (2025–2026, Last 12
    months, Year to date, Custom); shows the last indexed date and a "Refresh
    index" button that queues a refresh run.
  - Document kinds: checkboxes — Final pricing supplements (default on),
    Product supplements, Preliminaries, Other; "Skip preliminaries by peeking"
    toggle (default on).
  - Run on: radio — Laptop (default) / Render. Beside it a status dot from GET
    workers: Laptop "worker running on <host>" or "no worker — start it with
    <command>" with a Copy button; Render "not set up" (greyed, still
    selectable so the refusal message is shown) or "online".
  - Advanced (collapsed): concurrency, max requests/second (capped at 8).
  - Preview panel, updating as choices change (debounced): totals, a small
    table by institution × year, entities included, ETA, storage and cost.
  - Queue run button with a confirm dialog repeating the totals and target.
    Render refusal shows inline, nothing queued.
  - Runs table below (DataGrid): started, institutions, dates, kinds, target,
    status, progress bar with done/total, rate, ETA, failures; row actions
    Stop / Cancel / Requeue; auto-refresh every 10 s while a run is active.
  Light theme, whites and creams, matching existing admin screens.

=== TASK 6: REAL PROOF ===
  - Preview counts equal a direct SQL count on fixtures and on the live table
    for one real institution/year (read-only).
  - Laptop queue + a worker run --once processes the fixture run end-to-end
    with mocked SEC/R2; progress and final status persist (independent re-read).
  - Render refused when not configured: 409, zero rows queued (count before and
    after).
  - Stop mid-run → 'stopped', in-flight filings finished, requeue resumes with
    no filing fetched twice.
  - Permission gate: org admin refused, super-admin admitted, identical request.

=== TASK 7: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md: the screen, the worker command for the laptop (tmux),
the queued-run lifecycle, the Render worker setup steps (service type, start
command, Doppler config, cost ~$7/month) for later, and measured preview time.

=== VERIFICATION: apps/api/scripts/verify_bulkfetchui.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Hydrate secrets from
Doppler over HTTPS. Mock SEC, R2 and Render entirely (no network). Wrap setup,
teardown and every section so an exception is a [FAIL], never a crash; teardown
at start and end in FK child-before-parent order read from pg_constraint, then
re-read every touched table for its exact pre-test count. Fixture users are
created explicitly with the auth0_sub the mocked token carries; teardown finds
them by auth0_sub and email pattern too.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Every fixture value satisfies live CHECK and FK constraints (pg_constraint,
    contype read as ::text). Never invent a status, kind or role.
  - Never derive a unique value from a slice of a fixture UUID.
  - RLS context on every read. Expected counts from what the test created. An
    empty list never passes a check.
  - Regex checks against docs match the doc's real formatting; print the match.
  - Workers in the test run in-process with --once semantics; never spawn a
    background process.
  - Nested regressions run only after this script's own teardown, 600 s timeout.
  - Never touch real note_gold_values rows or real edgar_index_filings rows.

Assertions:
  [Y] Task 1's five findings reported (functions reused, run table, refresh
      timing, page gate, Render detection)
  [Y] Schema: new columns/values present live and declared in the migration;
      existing status values still accepted; RLS on
  [Y] Institutions endpoint lists each fixture group with its filer entities
      and per-year counts equal to SQL
  [Y] Preview: counts per institution/year/entity equal SQL on fixtures; a To
      past the last indexed date is refused with the date; preview writes
      nothing; live preview for one real group/year returns under 2 s (measured,
      printed)
  [Y] Queue on laptop with no worker heartbeat: run queued, worker_online
      false, the start command returned
  [Y] Render not configured: 409 with the message, zero runs queued
      (before/after count); with a mocked configured + alive Render worker the
      same request queues with target 'render'
  [Y] Worker --once claims only its own target's run, executes via the CLI
      code path (mock records the same function called), writes progress and
      'succeeded'; independent re-read of the row
  [Y] Two workers: one queued run is claimed by exactly one (SKIP LOCKED);
      combined SEC requests never exceed 8 in any 1-second window
  [Y] Stop: stop_requested mid-run → 'stopped' with in-flight finished;
      requeue resumes and no fixture filing is fetched twice
  [Y] Stale heartbeat shows 'worker lost' and requeue is allowed; cancel works
      only on queued runs
  [Y] refresh_index run can be queued and executes with mocked index files
  [Y] Front end: the Bulk download tab, the Run on radio with Laptop default,
      the Render refusal text and the worker status are present in the built
      component; npm run build exits 0
  [Y] Permission gate on every new endpoint: org admin refused, super-admin
      admitted, identical request
  [Y] No AI model calls (mocked client records none); no Render write calls
      (mocked Render client records only reads)
  [Y] Regression: verify_bulkfetch.py, verify_edgarpipelinea.py and
      verify_edgarcohorts.py each run ONCE as flat subprocesses AFTER teardown;
      all 0 FAIL
  [Y] Existing note_gold_values unchanged (count and checksum before/after)
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test count
