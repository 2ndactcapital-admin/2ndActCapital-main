MKT02 — NIGHTLY MARKET DATA REFRESH: ADAPTER REGISTRY, YAHOO ADAPTER, NIGHTLY ORCHESTRATOR, STALENESS REPORT. 5 tasks + verification.

mkt01 (merged, verified) built the market_data registry, the FRED adapter, and a
full historical backfill: 57 FRED series, 265,737 active observations. This
sprint makes the data stay current. It adds a provider-adapter registry, a
nightly orchestrator that refreshes every active series and records the run, a
staleness report, and a Render cron entrypoint plus setup instructions for the
operator. It adds ONE non-FRED adapter, Yahoo, for the 6 Yahoo-sourced series
(see CONFIRMED REAL FACTS), does NOT build the other non-FRED adapters, and does
NOT create any Render service.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- mkt01 verify result: `verify_mkt01.py --live` = 57 passed, 0 failed. All 57
  FRED codes validated (none invalid_code). Backfill: 265,737 rows inserted, 0
  revised, 12,373 FRED "." missing markers skipped, 0 rejected. Live registry
  now: 57 fred/active; 12 deferred (yahoo 6: ^RUT, EFA, EEM, ACWX, GC=F,
  DX-Y.NYB; shiller 1; worldbank 1; imf 1; bis 1; oecd 1; none 1); 6
  none/deferred_paid. indicator_ingest_runs holds 114 rows (57 validate + 57
  backfill), all with batch_id NULL.
- Part 1 DDL for THIS sprint is already applied and verified live (migration
  mkt02_ingest_runs_batch_id): market_data.indicator_ingest_runs gained a
  nullable `batch_id uuid` plus partial index idx_indicator_runs_batch (where
  batch_id is not null). Nothing else changed. No trigger, view, or function
  references market_data.* (checked before applying).
- Policies, grants, active-row predicate, uq_indicator_obs_point, Rule 3 write
  rule, and "registry operational columns are maintained by ingest code" are
  exactly as stated in mkt01: series and observations = global read, super-admin
  write; indicator_ingest_runs = super-admin only for every command.
  app_service has rolbypassrls = false.
- Existing tables must stay untouched: portfolio.securities_global (67 rows),
  portfolio.securities_global_prices (0), public.fx_rates (5) at drafting time.
- DECISIONS ALREADY MADE, do not relitigate in code:
  * EIA series (WTI, Brent, Henry Hub) are already covered through FRED; there
    is NO EIA adapter.
  * The 6 Yahoo series (^RUT, EFA, EEM, ACWX, GC=F, DX-Y.NYB) ARE built in this
    sprint. Yahoo's endpoint is unofficial and, as far as we know, licensed for
    personal use only. The owner has decided to assume personal/internal use for
    now: there are no paid customers and none are expected for a while. This is
    a recorded assumption, not a resolved licensing question; Task 5 records it
    as a launch blocker.
  * Shiller, World Bank, IMF, BIS, OECD adapters are a later sprint (mkt02b),
    after this cron is proven. They stay 'deferred'.
  * Nightly refresh re-pulls the FULL history of every active series and
    diffs it, rather than a recent window. Reason: annual benchmark revisions
    (payrolls, GDP, price indexes) reach back years, and one FRED request per
    series makes full pulls cheap. Revisions are captured by the existing
    Rule 3 write rule.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue. ONE exception: if you have no database
access in this environment, say so explicitly and STOP — never report
completion from an environment that cannot prove anything.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every script SYNCHRONOUSLY in the
foreground and read its output directly.
Task-specific rules:
- Decimal-only arithmetic; Decimal(str) at every parse boundary; never float.
- statement_cache_size=0 on EVERY asyncpg connection and pool (PgBouncer).
- Schema-qualify everything (market_data.*). All writes inside
  services/database.py's platform_scope(), never a session-level SET. No
  org_id anywhere in this feature.
- NEVER print or log a request URL, a params dict, or the FRED API key; scrub
  exceptions before printing or storing them in `error`/`last_error`.
- Do NOT run the nightly script or the verify script, and make no live FRED
  calls from this session. Write them and STOP; the operator runs both.
  Read-only database discovery is fine.
- Do NOT modify render.yaml or any Render/infra configuration, and do not try
  to create a Render service. The operator creates the service from your
  setup document.
- Do NOT write a Shiller, World Bank, IMF, BIS, or OECD adapter. Yahoo is the
  only new adapter.
- Do NOT build a new alerting system. See Task 1g.
- Do not modify any existing table, view, function, trigger, or policy; no
  further DDL. If live DDL differs from CONFIRMED REAL FACTS, report [FIND] and
  STOP.
- Do not edit CLAUDE.md.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Query the live database and confirm: batch_id column and its index exist;
      the registry counts in CONFIRMED REAL FACTS; 265,737 active observations
      (a larger number is fine if something has legitimately run since — report
      it); indicator_ingest_runs policy and grants unchanged. Report any
      difference.
  1b. Read the mkt01 code you are extending (suggested: services/market_data/
      and apps/api/scripts/market_data_ingest.py): the FRED client, the
      classify/write function, the ingest_runs writer. The nightly orchestrator
      must REUSE the existing backfill write path for diffing and writing, not
      reimplement it. Report the real function names and signatures.
  1c. Find how the API's other scheduled or batch work is actually run today
      (Render cron? the Workflow Scheduler? a worker?), and the exact
      build/start commands the API service uses on Render. Report them; the
      setup document in Task 3 must match reality.
  1d. Determine which environment variable names services/database.py reads to
      build its connection (names only). The setup document must list every
      variable the cron service needs.
  1e. Check whether render.yaml exists. Report only; do not edit it.
  1f. Read docs/PROJECT_STATUS.md and docs/MARKET_DATA_DESIGN_V1.md as they
      stand, so the Task 5 updates follow the `UPDATE <date>` convention.
  1h. Confirm the 6 Yahoo registry rows are as stated (source_provider 'yahoo',
      source_code is the ticker, ingest_status 'deferred', frequency 'daily')
      and report license_class and default_transform for each. Report how
      mkt01's validate/backfill code selects series today (hardcoded to
      provider 'fred'?) so Task 3 can generalize it without changing FRED
      behavior.
  1g. Find whether the platform already has an alert or notification mechanism
      usable for platform-level (non-org) events (CLAUDE.md mentions an
      orphaned-alert sweep). Report what exists. If something fits, the
      nightly run raises one alert per failed batch through it. If nothing
      fits, raise nothing: the run log plus a non-zero exit code is the
      mechanism, and say so in the design doc.

=== TASK 2: ADAPTER REGISTRY, ORCHESTRATOR, STALENESS ===
  2a. Provider-adapter registry: a mapping from source_provider to an adapter
      exposing `fetch_series(series_row) -> list of (obs_date, Decimal)` and
      `validate_series(series_row) -> metadata or a classified failure`, with
      missing/invalid points already filtered and counted. Register 'fred'
      (wrapping the mkt01 client) and 'yahoo' (Task 2d). The orchestrator takes the registry
      and the series selection as INJECTABLE arguments so tests can pass fakes
      and fixture series.
  2b. Orchestrator `run_nightly(registry, series_selection, trigger='nightly')`:
        - Generate one batch_id (uuid4).
        - Select series with ingest_status='active' (or the injected
          selection), ordered by sort_order.
        - Series whose source_provider has no registered adapter are SKIPPED
          and counted as skipped_no_adapter; they are not failures.
        - For each series: fetch, then diff and write through the existing
          mkt01 write path, one transaction per series inside
          platform_scope(). Insert one indicator_ingest_runs row per series
          with run_trigger, batch_id, status success|partial|failed, counts,
          scrubbed error, started_at, finished_at. Update the series'
          last_observation_date, updated_at, and clear last_error on success;
          set last_error on failure and leave its data untouched.
        - One series failing never aborts the others.
        - Revision race handling: the UPDATE that closes an active row must
          include `valid_to IS NULL AND system_to IS NULL` and check the row
          count. If it affected 0 rows (another run got there first), skip the
          insert for that point and count it as unchanged. No lock is used;
          correctness comes from the unique index plus diff-based writes.
        - At the end insert ONE summary row (series_id NULL, same batch_id,
          run_trigger) with batch totals: series attempted, succeeded, failed,
          skipped_no_adapter, rows inserted/revised/unchanged. Its status is
          success if no series failed, otherwise partial (or failed if every
          attempted series failed).
        - Return a result object with those totals and the list of failed
          series_keys.
  2c. Staleness: a pure function `stale_series(rows, today)` returning series
      whose last_observation_date is older than a per-frequency limit, in days:
      daily 10, weekly 21, monthly 120, quarterly 200, semiannual 400;
      per_meeting and irregular are never flagged; a NULL last_observation_date
      on an active series is always flagged. The limits are named constants
      with a comment saying they are first-pass values to tune after the first
      live report. Staleness is a report, never a failure.

  2d. Yahoo adapter (services/market_data/yahoo.py). Use the already-present
      HTTP client; add no dependency (no yfinance). Endpoint: Yahoo's v8 chart
      API, daily interval, maximum range, symbol URL-encoded with
      urllib.parse.quote(symbol, safe='') so '^RUT' and 'GC=F' are safe. Send a
      realistic browser-style User-Agent header. Throttle to about one request
      per second; retry 429/5xx/timeouts up to 3 times with backoff.
        - The transport is injectable so tests use canned payloads.
        - Parse the JSON with parse_float=Decimal so no float ever exists in the
          pipeline. Yahoo's numbers carry binary noise (for example
          2180.969970703125), and the noise can differ between calls, which
          would cause pointless revisions. Quantize every value to 4 decimal
          places with ROUND_HALF_EVEN before diffing or storing.
        - Use the RAW close series, not adjusted close: adjusted close restates
          the entire history at every dividend, which would revise every row
          for the ETFs each quarter.
        - Convert each epoch timestamp to a calendar date using the payload's
          meta gmtoffset (exchange-local date).
        - Skip bars whose close is null and count them as skipped.
        - Drop any bar dated today or later in the exchange's local date: it is
          an incomplete intraday bar (futures trade nearly around the clock)
          and would be revised tomorrow.
        - If the payload repeats a date, keep the last bar for that date so the
          unique index is never violated.
        - validate_series: request a short range, read meta (currency,
          exchangeTimezoneName). Success sets ingest_status='active', units =
          the currency code, last_validated_at = now(), clears last_error.
          Yahoo's explicit "not found" error for the symbol sets
          'invalid_code' with a scrubbed message. HTTP 401/403/429, 5xx,
          timeouts, or an unparseable body are TRANSIENT: leave status
          unchanged and set last_error; they must never mark a series invalid.
        - Known data caveats to record in the design doc, not to "fix" in code:
          GC=F is a continuous front-month futures series that jumps at
          contract rolls; Yahoo may block datacenter IP addresses, so these
          series can work from a laptop and fail from Render. Failure
          isolation means a Yahoo failure never affects FRED series.

=== TASK 3: NIGHTLY ENTRYPOINT AND RENDER SETUP DOCUMENT ===
  3-pre. Generalize mkt01's apps/api/scripts/market_data_ingest.py `validate`
      and `backfill` subcommands to dispatch through the adapter registry and
      accept `--provider <name>`. The default stays 'fred' so existing
      behavior and the mkt01 verify script are unaffected. `validate
      --provider yahoo` selects Yahoo series with ingest_status in
      ('deferred','pending','invalid_code','active'); `backfill --provider
      yahoo` backfills the active ones through the same classify/write path and
      logs ingest_runs rows with run_trigger 'validate' / 'backfill'. The
      nightly only touches 'active' series, so this is how the Yahoo series
      become active.
  3a. apps/api/scripts/market_data_nightly.py: reads configuration from
      os.environ ONLY (on Render the values arrive through a Doppler sync; do
      not add Doppler hydration). If a required variable is missing, print a
      one-line message naming the missing variable NAMES (never values) and
      exit 2. Otherwise run the orchestrator with the real registry, print a
      compact summary (batch id, totals, failed series, then the stale-series
      report as `[STALE]` lines), and exit 0 if no series failed, 1 if any
      failed or the batch crashed. Exit non-zero is what makes Render mark the
      run failed.
  3b. If Task 1g found a fitting alert mechanism, raise one alert per failed
      batch through it, containing the batch id, failed series keys, and
      scrubbed errors. Otherwise skip this item.
  3c. docs/market_data/RENDER_CRON_SETUP.md for the operator, matching the real
      commands found in 1c/1d. Cover: service type Cron Job; schedule
      `15 11 * * *` (Render uses UTC; 11:15 UTC is about 7:15am New York in
      summer, 6:15am in winter, after FRED has posted the prior day's
      figures); the exact build command and start command for
      apps/api/scripts/market_data_nightly.py; the full list of environment
      variable NAMES the service needs; and that the cron service needs its
      OWN Doppler-to-Render sync (a new service does not inherit one, and a
      sync can overwrite values already set on a service, so set no variables
      by hand). Add a short caveat that the Yahoo series may fail from Render's
      IP addresses even though they work from the operator's laptop; this does
      not affect FRED series, and the first manual Render run is the test.
      Include the post-setup checklist: confirm the service received
      the variables by name only, trigger one manual run from the Render
      dashboard, and confirm in the database that a new batch with a summary
      row exists. State that this document does not create anything: the
      operator does.

=== TASK 4: REAL PROOF (written into the verify script, NOT executed by you) ===
Yahoo adapter proofs (Phase A, canned payloads, no network):
  - Parsing: epoch timestamps convert to exchange-local dates using gmtoffset
    (include a payload whose UTC date differs from its local date); null
    closes are skipped and counted; a bar dated today is dropped; a repeated
    date keeps the last bar; every value is a Decimal and no float exists
    anywhere in the path.
  - Noise: 2180.969970703125 and 2180.9700000001 both normalize to the identical
    stored value, so a re-run over noisy data revises nothing.
  - Raw vs adjusted: a payload with both close and adjclose stores the raw
    close only.
  - Symbol encoding: all six real symbols (^RUT, EFA, EEM, ACWX, GC=F,
    DX-Y.NYB) produce correctly encoded request paths, checked against the
    fake transport's captured request.
  - Classification: an explicit symbol-not-found payload yields invalid_code
    from validate_series; HTTP 401, 403, 429, 5xx, timeout, and a malformed
    body each leave ingest_status unchanged with last_error set (transient is
    never invalid); validate success sets active and units from meta currency.
  - Registry contract: the real registry contains exactly 'fred' and 'yahoo';
    the Yahoo adapter is exercised ONLY through fake transports in Phase A.
  - `--provider` dispatch: with no flag, validate/backfill still select only
    provider 'fred'; with `--provider yahoo` they select only Yahoo series,
    proven on fixture series with fake adapters.
Phase A is self-contained and always runs. It uses fixture series whose
series_key starts with 'verify.mkt02.' and a registry containing ONLY fake
adapters. It must assert that the registry it passes contains no real FRED
adapter, and must never run the orchestrator against real series.
  - Batch bookkeeping: a run over 3 fixture series yields 3 per-series rows
    plus exactly one summary row (series_id NULL), all sharing one batch_id,
    all with run_trigger='nightly'; the summary totals equal the sum of the
    per-series rows.
  - First run inserts, an identical second run writes nothing (row counts
    unchanged), and a run with one changed value revises exactly one point
    (old row has valid_to set, one active row for the date, two rows of
    history), re-read from an independent connection.
  - Failure isolation: one fake series raises mid-fetch; the others still
    complete and persist; the failed series' data is unchanged (before/after
    comparison); its last_error is set and scrubbed; the summary status is
    'partial'; run_nightly's result lists the failed series_key. The same run
    with every series failing gives status 'failed'.
  - A transient failure never changes ingest_status or deletes data.
  - Unregistered provider: a fixture series with an unregistered
    source_provider is skipped, counted in skipped_no_adapter, absent from the
    failed list, and does not turn the summary status to partial.
  - Overlap: two orchestrator runs executed concurrently over the same fixture
    series leave exactly one active row per (series, date), and any losing
    write is counted as unchanged or logged as a handled failure, never a
    crash and never a duplicate active row. Also test the lost-race path
    directly: close the active row between the read and the write and confirm
    no insert occurs.
  - Staleness boundaries per frequency: one day inside and one day outside
    each limit; NULL last_observation_date flagged; per_meeting and irregular
    never flagged. Pure function, no database.
  - Exit codes: the entrypoint's decision function returns 0 for all-success
    (including skipped_no_adapter), 1 for any failure, and the missing-env path
    returns 2 and names variable names only.
  - No-secret proof: run the orchestrator with a fake adapter that raises an
    exception containing a sentinel API key; assert the sentinel appears in no
    stdout, stderr, stored `error`/`last_error`, or result object. Static
    check that no print/log call in the new modules formats a URL or params.
  - Existing tables untouched: counts of portfolio.securities_global,
    portfolio.securities_global_prices, public.fx_rates equal before and
    after, and the count of NON-fixture rows in all three market_data tables
    is identical before and after.
  - Teardown deletes in strict FK child-before-parent order, confirmed against
    information_schema: observations, then ingest_runs (by fixture series_id
    AND by the batch_ids this script created, because summary rows have
    series_id NULL), then series. Re-read all three tables independently to
    confirm non-fixture counts are exactly as before; fail loudly with
    SystemExit(2) if any fixture row remains. Never TRUNCATE.
Phase B runs ONLY with `--live`, after the operator has run the nightly script
TWICE. Without the flag, print one line saying Phase B was not run.
  - The two most recent nightly batches each have exactly one summary row, and
    their per-series row count equals the number of active series with a
    registered adapter, read at runtime, not hardcoded. Every per-series row
    is success. A failure is reported with its scrubbed error and FAILS the
    assertion.
  - The second batch revised nothing (sum of rows_revised = 0) and inserted at
    most 2 rows per series: two runs minutes apart cannot legitimately differ
    more than that.
  - No (series_id, obs_date) has more than one active row.
  - For every active series, last_observation_date equals max(obs_date),
    re-read independently.
  - Total active observations >= 265,737: revisions replace rows one for one,
    so the active count must not fall below the mkt01 baseline.
  - Each of the 6 Yahoo series is either 'active' with at least one active
    observation and last_observation_date = max(obs_date), or reported as a
    [FIND] with its scrubbed last_error. A Yahoo series that is not active is
    reporting, not failure, because the endpoint is unofficial and outside this
    sprint's control. Any Yahoo series that IS active must satisfy every
    assertion above like a FRED series.
  - Print counts of stale series by frequency, and each stale series as a
    [FIND]; this is reporting, not failure.

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md with an mkt02 entry. Under the existing mkt01
entry append `UPDATE 2026-10-03` stating that the planned scope "mkt02 =
nightly cron + non-FRED adapters" was split: mkt02 = nightly refresh plus the
Yahoo adapter; the other non-FRED adapters (Shiller, World Bank, IMF, BIS,
OECD) moved to mkt02b; EIA was dropped because FRED covers it. Update
docs/MARKET_DATA_DESIGN_V1.md with: the adapter-registry contract, why the
nightly re-pulls full history, the overlap strategy (unique index plus
diff-based writes, no lock), the staleness thresholds and that they are
first-pass, the alerting finding from Task 1g, and the mkt02b / mkt03 roadmap.
Also record the Yahoo decision: the 6 Yahoo series are enabled under the
owner's personal/internal-use assumption, and a "Launch blockers" section lists
it: before any external customer sees this platform, the Yahoo series must be
replaced with a licensed source or removed, and the mkt03 API must expose
source_provider so it can be gated per tenant. Record the Yahoo data caveats
too: raw close is used (adjusted close would restate history at every
dividend), GC=F jumps at contract rolls, and Yahoo may block datacenter IPs.
Do not edit CLAUDE.md; list any lines you think it should gain at the end of
PROJECT_STATUS for the operator to review.

=== VERIFICATION: apps/api/scripts/verify_mkt02.py ===
Pass/fail only. No interactive prompts. WRITE it and make it runnable; do NOT
run it. Hydrate secrets internally using the same helper mkt01's verify script
uses. Output lines begin with [PASS], [FAIL], [FIND], or [SKIP]; the last line
is `TOTAL: <passed> passed, <failed> failed`. Exit non-zero on any [FAIL]. Every
assertion states why it matters, not just what it checked. One assertion per
bullet in Task 4, Phase A always, Phase B behind --live. Do NOT chain
verify_mkt01.py inside this script: its baseline is stated above as a
CONFIRMED REAL FACT, and the operator re-runs it standalone.

When finished, STOP and print exactly these operator commands:
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_ingest.py validate --provider yahoo
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_ingest.py backfill --provider yahoo
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_nightly.py
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_nightly.py
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt01.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
