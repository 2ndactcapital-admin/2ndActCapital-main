MKT01 — MARKET DATA FOUNDATION: INDICATOR REGISTRY, FRED ADAPTER, HISTORICAL BACKFILL. 5 tasks + verification.

Hollisworks needs platform-wide market and macro indicator history (S&P 500,
Treasury yields, payrolls, CPI, FX, oil, housing, and so on) to power a later
base-100 trend chart with security overlay, and a grid view. This sprint builds
the foundation only: a registry loaded from a reviewed seed file, a FRED adapter
that validates every series code against FRED itself, and a full historical
backfill. Later sprints (NOT this one): mkt02 = nightly Render cron + non-FRED
adapters; mkt03 = API, chart/grid UX, sec-master links for benchmark series.

This is PLATFORM-GLOBAL reference data: no org_id column, same RLS shape as
portfolio.securities_global. It is deliberately separate from security pricing
(portfolio.securities_global_prices) — do not write indicator data there.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- Part 1 DDL is already applied and verified live (migration
  mkt01_market_data_schema). Schema `market_data` has three tables:
  indicator_series, indicator_observations, indicator_ingest_runs. RLS is
  enabled on all three.
- Policies: series and observations = global SELECT (using true); INSERT /
  UPDATE / DELETE require NULLIF(current_setting('app.is_super_admin', true),'')
  = 'true'. indicator_ingest_runs = super-admin only for every command (a
  non-platform caller reads zero rows). app_service has SELECT/INSERT/UPDATE/
  DELETE on all three; anon/authenticated/PUBLIC have nothing.
- indicator_series.security_global_id is a nullable FK to
  portfolio.securities_global(id), unused until mkt03. Leave it NULL.
- Active-row predicate everywhere: valid_to IS NULL AND system_to IS NULL.
  Partial unique index uq_indicator_obs_point (series_id, obs_date) on that
  predicate — same pattern as portfolio.securities_global_prices.
- app_service has rolbypassrls = false. At drafting time:
  portfolio.securities_global = 67 rows, portfolio.securities_global_prices =
  0, public.fx_rates = 5. This sprint must not modify any existing table, view,
  function, trigger, or policy.
- Seed file: docs/market_data/market_indicator_registry_v1.json — 75 series:
  57 with source_provider 'fred' (ingest_status 'pending'), 12 'deferred' (no
  adapter until mkt02), 6 'deferred_paid'. The FRED codes in it were written
  from memory and are UNVALIDATED. Validating them against FRED is Task 3.
- Write rule (CLAUDE.md Rule 3, valid-axis restatement) for observations:
  value unchanged -> write nothing; value changed -> UPDATE the active row SET
  valid_to = now(), then INSERT a new row with valid_from = now().
- Registry rows are configuration, edited in place with updated_at. Their
  operational columns (ingest_status, units, seasonal_adjustment,
  last_validated_at, last_observation_date, last_error) are maintained by the
  ingest code, NOT by the seed loader.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue. ONE exception: if you have no database
access in this environment, say so explicitly and STOP — never report
completion from an environment that cannot prove anything.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every script SYNCHRONOUSLY in the
foreground and read its output directly.
Task-specific rules:
- Decimal-only arithmetic. FRED values arrive as strings: parse with
  Decimal(str); never float at any boundary.
- statement_cache_size=0 on EVERY asyncpg connection and pool (PgBouncer).
- Schema-qualify everything: market_data.indicator_series, never bare names.
- All writes happen inside services/database.py's platform_scope() — never a
  session-level SET. No org_id appears anywhere in this feature; code that
  accepts or reads an org_id here is a bug.
- NEVER print or log a request URL, a params dict, or the FRED API key. FRED
  takes the key as a query-string parameter, so any logged URL leaks it.
  Exceptions must be scrubbed before they are printed or stored in last_error.
- Do NOT run the ingest script and do NOT run the verify script. Write them and
  STOP. The operator runs both. Do not make live calls to FRED from this
  session. Read-only database discovery is fine.
- If Task 1 finds the live DDL differs from CONFIRMED REAL FACTS, report it as
  [FIND] and STOP. Do not patch DDL yourself.
- Do not edit CLAUDE.md. Do not touch the dark theme / branding / frontend —
  there is no UI in this sprint.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Query the live database and confirm the market_data DDL matches
      CONFIRMED REAL FACTS exactly: columns and types of all three tables,
      CHECK constraint bodies (pg_get_constraintdef), policies (pg_policy),
      grants, indexes. Report any difference. docs/schema_snapshot.sql cannot
      show CHECK bodies or policies and will not contain market_data until
      /refresh-schema runs — the live database is authoritative.
  1b. Read services/database.py: the real signature and behavior of
      platform_scope(), and how connections/pools are created. Reuse them.
  1c. Find how existing verify scripts hydrate Doppler secrets internally over
      HTTPS (the pattern in verify_rlscutover.py and verify_tamodel1.py,
      announcing "[INFO] hydrated N secrets from Doppler over HTTPS"). Reuse
      that exact helper in both new scripts. run_sprint.sh's verify step does
      NOT wrap scripts in `doppler run --`, so every new script must hydrate
      its own secrets.
  1d. Determine which HTTP client library is already in requirements and use
      it. Add no new dependency unless none exists.
  1e. Locate the existing EDGAR index loader (portfolio.edgar_index_filings /
      edgar_index_filing_filers) and note how it batches inserts. Mirror its
      conventions where sensible.
  1f. Check whether FRED_API_KEY exists in Doppler — NAMES ONLY
      (`doppler secrets --only-names`). Never print values. Report present or
      absent; absence does not block writing code.
  1g. Read the seed file. Confirm 75 series, unique series_key, and that every
      frequency / default_transform / cost_tier / license_class / ingest_status
      value in it is allowed by the LIVE CHECK constraints from 1a.
  1h. Decide, from 1b-1e, where the new code lives (suggested:
      apps/api/services/market_data/ with registry.py, fred.py, ingest.py; and
      one operator script apps/api/scripts/market_data_ingest.py). Follow the
      repo's real layout if it differs.

=== TASK 2: REGISTRY LOADER ===
  2a. Idempotent loader: upsert each seed series on series_key. On conflict
      update ONLY the definition fields (name, category, region, frequency,
      best_view, default_transform, cost_tier, cost_note, license_class,
      source_provider, source_code, source_url, notes, sort_order,
      updated_at). NEVER overwrite ingest_status, units, seasonal_adjustment,
      last_validated_at, last_observation_date, or last_error on an existing
      row. Never delete a registry row that is absent from the seed.
  2b. New rows take ingest_status from the seed file.
  2c. Returns inserted / updated / unchanged counts. Runs in platform_scope().

=== TASK 3: FRED ADAPTER, VALIDATION, BACKFILL ===
  3a. FRED client. Series metadata: GET /fred/series (file_type=json).
      Observations: GET /fred/series/observations (file_type=json), paging by
      offset/limit. Throttle to stay under FRED's rate limit (about 120
      requests/minute). Retry 429/5xx/timeouts up to 3 times with backoff. Take
      the transport as an injectable dependency so tests can use a fake.
  3b. `validate`: for every series with source_provider='fred' and
      ingest_status in ('pending','invalid_code','active'), fetch metadata.
        - Found: set ingest_status='active', units, seasonal_adjustment,
          last_validated_at=now(), last_error=NULL. FRED is authoritative for
          frequency: map D->daily, W->weekly, M->monthly, Q->quarterly,
          SA->semiannual, anything else -> irregular. If it differs from the
          seed, update it and print a [FIND] line naming the series, seed
          value, and FRED value.
        - FRED says the series does not exist (HTTP 400/404 with an error
          message): ingest_status='invalid_code', last_error set to a scrubbed
          message.
        - TRANSIENT failure (5xx, timeout, 429 after retries): leave status
          unchanged, set last_error. A transient failure must NEVER mark a
          series invalid_code.
        - If FRED's notes text mentions copyright and the seed says
          license_class='public_domain', print a [FIND] line for review. Do not
          change license_class automatically.
      Log one indicator_ingest_runs row per series (run_trigger='validate').
  3c. `backfill`: for every series with ingest_status='active', fetch the full
      observation history and write it:
        - Skip FRED's missing marker "." and count it as skipped.
        - Reject non-numeric / non-finite values. Parse with Decimal(str).
        - Store obs_date exactly as FRED gives it; do not shift monthly or
          quarterly dates.
        - One transaction per series inside platform_scope(). Load the active
          rows for the series, classify each fetched point as insert / revise /
          unchanged (compare Decimals numerically), and write in chunks.
        - Apply the Rule 3 write rule above for revisions.
        - After each series: update last_observation_date = max(obs_date),
          updated_at, clear last_error; insert an indicator_ingest_runs row
          (run_trigger='backfill', status success|partial|failed, counts).
        - One series failing must not abort the others.
  3d. Operator script apps/api/scripts/market_data_ingest.py with subcommands
      `load`, `validate`, `backfill`, and `all` (runs the three in order),
      plus optional `--series <series_key>` to target one series. Output is a
      compact summary: per-series line (key, rows, first/last date, status) and
      totals. It must exit with a clear one-line message if FRED_API_KEY is
      absent from the environment.

=== TASK 4: REAL PROOF (written into the verify script, NOT executed by you) ===
Phase A is self-contained and always runs. It uses fixture series whose
series_key starts with 'verify.mkt01.' and never touches real rows.
  - Schema facts: to_regclass for all three tables; relrowsecurity true;
    policy counts; app_service grants; and, as its own explicit assertion,
    rolbypassrls = false on the connection role (otherwise every isolation
    check below proves nothing).
  - Permission gate on the IDENTICAL request, both ways: INSERT into
    indicator_series and indicator_observations is REFUSED outside
    platform_scope() (row-level-security error, row count unchanged) and
    ADMITTED inside it. UPDATE and DELETE outside platform_scope() leave the
    fixture rows unchanged (re-read to prove it); inside it they succeed.
  - Read gate on the identical query: series and observations are readable
    WITHOUT platform scope; indicator_ingest_runs returns zero rows without
    platform scope and the fixture rows with it.
  - Bitemporal write rule: first load of N fixture observations = N inserts;
    reloading identical data = zero writes (table row count unchanged); reloading
    with ONE changed value = exactly one revision: the old row now has valid_to
    set, a single active row exists for that date, and history for the date has
    two rows. Re-read from an independent connection to prove persistence.
  - uq_indicator_obs_point enforced: inserting a second active row for the
    same (series, date) raises a unique violation (rolled back, count
    unchanged).
  - Decimal exactness: '4.123456789' round-trips as an equal Decimal, no float
    drift; '.' is skipped and never stored; a non-numeric string is rejected.
  - Adapter behavior with a FAKE transport, no network: metadata -> frequency
    mapping for D/W/M/Q/SA/other; a "series does not exist" response yields
    invalid_code; a 5xx / timeout leaves status unchanged (transient is never
    invalid).
  - Loader idempotency: loading the seed twice leaves identical row counts, and
    a fixture row whose ingest_status/units were already set is NOT overwritten
    by a reload. Seed file structural checks (75 series, unique keys, enums
    valid against live CHECKs).
  - No-secret proof: run the adapter against the fake transport using a
    sentinel API key and assert the sentinel string appears nowhere in captured
    stdout, stderr, stored last_error values, or exception text. Also a static
    check that no print/log call in the new modules formats a URL or params.
  - Existing tables untouched: row counts of portfolio.securities_global,
    portfolio.securities_global_prices, and public.fx_rates are captured at
    start and equal at end.
  - Teardown deletes fixture rows in strict FK child-before-parent order
    (observations, then ingest_runs, then series), confirmed against
    information_schema rather than assumed, and then independently re-reads all
    three tables to confirm real (non-fixture) row counts are exactly as
    before. Fail loudly (SystemExit(2)) if any fixture row remains. Never
    TRUNCATE.
Phase B runs ONLY with the `--live` flag, after the operator has run load,
validate, and backfill. Without the flag, print one line saying Phase B was not
run.
  - Every source_provider='fred' row is 'active' or 'invalid_code'; none are
    'pending'.
  - Every 'active' series has at least one active observation, and its
    last_observation_date equals max(obs_date) re-read independently.
  - No (series_id, obs_date) has more than one active row.
  - Every 'active' series has units populated and a success row in
    indicator_ingest_runs.
  - Every invalid_code series is printed as a [FIND] with its last_error; this
    is reporting, not a failure.
  - Spot check DGS10, UNRATE, and CPIAUCSL: re-fetch the latest observation
    from FRED and assert it equals the stored value.
  - Print counts by ingest_status.

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md with an mkt01 entry. Add a short
docs/MARKET_DATA_DESIGN_V1.md recording these decisions:
  - Separate from security pricing, platform-global, no org_id.
  - Sec-master rule: macro indicators never go in the security master; only
    investable benchmark series (S&P 500, Nasdaq 100, Russell 2000, EFA, EEM,
    gold) link via security_global_id, in mkt03.
  - default_transform semantics: rebase_100 only for price-like series; rates,
    spreads, and anything that can be zero or negative stay 'level'; price
    indexes display as yoy_pct. Mixed frequencies render as step lines.
  - license_class meaning and the unresolved licensing question for
    third-party series shown inside a multi-tenant product.
  - Roadmap: mkt02 (nightly cron, non-FRED adapters), mkt03 (API, chart, grid).
Do not edit CLAUDE.md; list any lines you think it should gain at the end of
PROJECT_STATUS for operator review.

=== VERIFICATION: apps/api/scripts/verify_mkt01.py ===
Pass/fail only. No interactive prompts. WRITE it and make it runnable; do NOT
run it. Hydrate secrets internally (see 1c). Output lines must begin with
[PASS], [FAIL], [FIND], or [SKIP]; the last line is
`TOTAL: <passed> passed, <failed> failed`. Exit non-zero on any [FAIL]. Every
assertion states why it matters, not just what it checked. Assertions: one per
bullet in Task 4, Phase A always, Phase B behind --live. Teardown rules are
those in Task 4.

When finished, STOP and print exactly these operator commands:
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_ingest.py all
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt01.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
