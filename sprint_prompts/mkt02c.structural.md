MKT02C — LONG HISTORY: S&P 500 SPLICE, CREDIT SPREAD, NOTE UNDERLYINGS, YAHOO UNITS. 6 tasks + verification.

mkt01 to mkt03 (merged, verified) built the registry, the FRED and Yahoo
adapters, the nightly refresh, and the read API. Reading real data exposed a
gap: some key series do not reach back far enough for the key-dates chart.
fred.sp500 starts 2016-10-03 because FRED keeps only about ten years of it. The
two ICE BofA credit spreads start in October 2023. Five index securities that
underlie the structured notes have no price series at all.

This sprint closes those gaps and tidies two loose ends:
  1. Splice S&P 500 history from Yahoo (^GSPC) into the SAME series, fred.sp500,
     for dates before FRED's first observation. FRED stays the source of record
     and the nightly source; Yahoo supplies only the older history.
  2. Add a long-history credit spread from FRED (Moody's Baa vs 10-year
     Treasury).
  3. Add five Yahoo index series that are underlyings of the structured notes,
     and link each to its index security.
  4. Make Yahoo units say 'index points' for index instruments instead of 'USD'.
  5. Correct stale status text in the docs.

OUT OF SCOPE (do not build): any UI or Next.js route; key dates or saved views;
structured-note price history; TOPIX (its Yahoo ticker is not confirmed);
changes to the nightly orchestrator's behavior; changes to the read API.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- Data state at drafting: 75 registry series, 63 active (57 fred, 6 yahoo),
  12 not active. 313,156 active observations. fred.sp500 first observation
  2016-10-03, 2,514 rows, linked to the "S&P 500 Index" security. Prior
  baselines: verify_mkt01 --live 57 passed, verify_mkt02 --live 47 passed,
  verify_mkt03 --live 68 passed, all 0 failed.
- Part 1 is already applied and verified live (migration
  mkt02c_observation_source_provider): market_data.indicator_observations has a
  new nullable text column source_provider with CHECK source_provider IS NULL OR
  IN ('fred','yahoo','shiller','worldbank','imf','bis','oecd','manual').
  NULL means "the series' own source_provider". All 313,156 existing rows are
  NULL. Policies on the table are unchanged (4).
- Unique partial index uq_indicator_series_security allows at most one series
  per security. The three existing links are fred.sp500, fred.nasdaq100 and
  yahoo.rut.
- Index securities to link (exact names, active, security_type 'index'):
    Dow Jones Industrial Average      11d8d8dd-d712-4e65-adc3-235c24310548
    FTSE 100 Index                    96a1895f-0f6f-40b8-9442-cb61663fa448
    EURO STOXX 50 Index               dc89001d-e250-4abc-9265-b4d2e6c52009
    Swiss Market Index                803df7c0-56eb-4ed3-98a6-1649d69dfcc4
    S&P/ASX 200 Index                 0ccebe34-b939-4179-85f7-08c234a00ed7
- Yahoo is used under the owner's personal/internal-use assumption (design doc
  decision 13). Anything sourced from Yahoo, including the spliced S&P history,
  falls under the Yahoo launch blocker.
- Registry rows are configuration, edited in place. Observations follow Rule 3:
  value unchanged -> no write; changed -> close the active row and insert a new
  one. The loader upserts definition fields only and never overwrites
  ingest_status, units, seasonal_adjustment, last_validated_at,
  last_observation_date or last_error on an existing row, and never deletes a
  row absent from the seed.

DECISIONS ALREADY MADE, do not relitigate:
- ONE series for S&P 500 (fred.sp500), not two. Consumers see one continuous
  line. Provenance lives in the new per-row source_provider column.
- The splice only ever INSERTS rows dated strictly before the first
  observation of the series' own (NULL-provider) rows. It never revises or
  deletes a FRED row.
- Yahoo ^GSPC is fetched through the existing Yahoo adapter with a synthetic
  series row; no separate ^GSPC registry series is created.
- Additions are loaded from a NEW seed file, not by editing the v1 seed, so the
  mkt01 verify's structural checks on v1 stay valid.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue. TWO exceptions: (1) if you have no
database access in this environment, say so explicitly and STOP; (2) if Task
1b finds that the existing write path would close or delete active rows that
are absent from a fetch, STOP and report it, because the splice would be
erased by the nightly refresh.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every script SYNCHRONOUSLY in the
foreground and read its output directly.
Task-specific rules:
- Decimal-only arithmetic; Decimal(str) at every parse boundary; never float.
- statement_cache_size=0 on EVERY asyncpg connection and pool.
- Schema-qualify everything. All writes inside platform_scope(). No org_id.
- No DDL. Part 1 is done. No writes to portfolio.* (securities_global and its
  prices are read-only here; linking sets market_data.indicator_series
  .security_global_id only).
- NEVER print or log a secret or a request URL; scrub exceptions.
- Do NOT run the splice script, the ingest script, the nightly script, or the
  verify script, and make no live external calls from this session. Write them
  and STOP; the operator runs them. Read-only database discovery is fine.
- Do not edit CLAUDE.md, render.yaml, any UI, or the read API modules.
- Do not edit prior verify scripts except as Task 1d allows.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Query the live database and confirm CONFIRMED REAL FACTS: counts, the
      source_provider column and constraint, 0 rows with it set, the three
      links, and that the five securities above exist with exactly those ids,
      names and 'index' type and are not linked. Report any difference.
  1b. Read the existing observation write path (mkt01 ingest, mkt02 nightly).
      Report in plain words: does it EVER close, delete or touch an active row
      whose obs_date is absent from the fetched data? The splice relies on the
      answer being "no". If the answer is yes, STOP (see above).
  1c. Read the loader (the `load` subcommand): its seed-file handling, the exact
      list of fields it upserts, how new rows get ingest_status, and whether it
      can set security_global_id today (expected: no).
  1d. Read verify_mkt01.py, verify_mkt02.py and verify_mkt03.py and list every
      assertion that encodes today's data shape: counts of series (75, 63, 57,
      12), the three links, "every other security is unselectable", the S&P 500
      anchor check (the mkt03 Phase B check floats because fred.sp500 starts in
      2016; after the splice it will not float), observation count baselines,
      and anything about units. For each assertion that this sprint would make
      fail, give file, line, and the smallest change that preserves what it
      proves (read the value at runtime instead of hardcoding it, or use a
      >= baseline). You MAY make exactly those minimal edits, and each one must
      be recorded as a [FIND] with before and after. You may NOT weaken what an
      assertion proves.
  1e. Read the Yahoo adapter: how fetch_series is called and which fields of
      the series row it needs, and how validate_series sets units. Report where
      Yahoo's meta instrumentType is available in the parsed payload.

=== TASK 2: LOADER ADDITIONS AND THE NEW SEED FILE ===
  2a. Add a `--seed <path>` option to the `load` subcommand of
      apps/api/scripts/market_data_ingest.py. The DEFAULT stays the v1 seed, so
      existing behavior and the mkt01 verify are unchanged.
  2b. Link support. A seed row may carry `security_global_id` and
      `security_name`. After upserting the row, set the series'
      security_global_id ONLY IF all hold: the series currently has no link; a
      portfolio.securities_global row exists with that id, that exact name,
      security_type 'index', valid_to and system_to NULL and merged_into_id
      NULL; and the unique index would not be violated. Otherwise leave the link
      NULL and print a [FIND] naming the series and the reason. Never overwrite
      an existing link. Never write to portfolio.*.
  2c. Write docs/market_data/market_indicator_registry_additions_v2.json with
      exactly these six rows (structure {"version": 2, "series": [...]} like v1,
      plus the two link fields on the Yahoo rows):
        - series_key fred.baa10y; name "Baa corporate spread (Moody's, vs
          10-year Treasury)"; category "Rates & credit"; region "US";
          frequency "daily"; best_view "Level vs long-run average";
          default_transform "level"; cost_tier "free"; cost_note "Free";
          license_class "unreviewed"; source_provider "fred"; source_code
          "BAA10Y"; source_url null; notes "Long-history credit-stress proxy;
          the ICE BofA spreads on FRED start in Oct 2023"; sort_order 760;
          ingest_status "pending".
        - five Yahoo rows, all with category "Equities", frequency "daily",
          best_view "1m / 3m / YTD / 1y return", default_transform
          "rebase_100", cost_tier "free", cost_note "Free (Yahoo, unofficial)",
          license_class "third_party_licensed", source_provider "yahoo",
          notes "Underlying of structured notes", ingest_status "deferred":
            yahoo.dji      "Dow Jones Industrial Average" region US  code ^DJI
                           url https://finance.yahoo.com/quote/%5EDJI  sort 770
                           security 11d8d8dd-d712-4e65-adc3-235c24310548
                           security_name "Dow Jones Industrial Average"
            yahoo.ftse     "FTSE 100"        region GB  code ^FTSE
                           url https://finance.yahoo.com/quote/%5EFTSE sort 780
                           security 96a1895f-0f6f-40b8-9442-cb61663fa448
                           security_name "FTSE 100 Index"
            yahoo.stoxx50e "EURO STOXX 50"   region EU  code ^STOXX50E
                           url https://finance.yahoo.com/quote/%5ESTOXX50E sort 790
                           security dc89001d-e250-4abc-9265-b4d2e6c52009
                           security_name "EURO STOXX 50 Index"
            yahoo.ssmi     "Swiss Market Index" region CH code ^SSMI
                           url https://finance.yahoo.com/quote/%5ESSMI sort 800
                           security 803df7c0-56eb-4ed3-98a6-1649d69dfcc4
                           security_name "Swiss Market Index"
            yahoo.axjo     "S&P/ASX 200"     region AU  code ^AXJO
                           url https://finance.yahoo.com/quote/%5EAXJO sort 810
                           security 0ccebe34-b939-4179-85f7-08c234a00ed7
                           security_name "S&P/ASX 200 Index"
      Do not edit the v1 seed file.

=== TASK 3: YAHOO UNITS ===
Change the Yahoo adapter's validate_series so that when Yahoo's meta
instrumentType is 'INDEX' the stored units are 'index points'; for any other
instrumentType, or if it is missing, keep today's behavior (the currency code).
No other validate behavior changes. The operator re-runs `validate --provider
yahoo` afterwards, which refreshes units on all Yahoo series.

=== TASK 4: S&P 500 HISTORY SPLICE ===
Write apps/api/scripts/market_data_splice_history.py with arguments
`--series` (default fred.sp500), `--yahoo-symbol` (default ^GSPC) and
`--dry-run`, and put the logic in a service module so it is testable with a
fake transport. Behavior:
  4a. Boundary F0 = the minimum obs_date among the series' ACTIVE rows whose
      source_provider IS NULL (its own source). Never use rows from another
      provider to define it, so re-runs are stable.
  4b. Fetch the symbol's full daily history through the existing Yahoo adapter
      with a synthetic series row. Same Decimal-only parsing, 4 dp
      quantization, raw close, and partial-day exclusion as the adapter.
  4c. OVERLAP GATE. For every date >= F0 present in both the fetched data and
      the series' own rows, compare values. Require that at least 99.0% of the
      overlapping days differ by no more than 0.02 (FRED stores 2 decimals,
      Yahoo 4) AND that no overlapping day differs by more than 1.00. If the
      gate fails, write NOTHING, print the worst ten differing days as [FIND],
      and exit 1. This protects against splicing a different index or a
      differently-scaled series.
  4d. Splice. For every fetched date strictly < F0: if no active row exists for
      that date, INSERT it with source_provider 'yahoo'. If an active row
      exists with source_provider 'yahoo' and the value differs, apply the Rule
      3 revision to that row only (close it, insert the new one, keep
      source_provider 'yahoo'). A row with source_provider NULL or any other
      provider is NEVER revised or closed by this script. Dates >= F0 are never
      written.
  4e. One transaction inside platform_scope(). After writing: append one
      sentence to the series' notes ("History before <F0> spliced from Yahoo
      <symbol>; see docs/MARKET_DATA_DESIGN_V1.md") only if that sentence is not
      already present; insert one indicator_ingest_runs row for the series with
      run_trigger 'manual' and the counts. Do not change ingest_status.
  4f. Output (never a URL or secret): points fetched, candidate pre-F0 points,
      overlap days compared, percent within tolerance, worst difference, rows
      inserted / revised / unchanged, the earliest date now present, and the
      line "DRY RUN — nothing written" when --dry-run is set (the gate still
      runs and reports in a dry run). Exit 0 on success, 1 when the gate fails
      or the fetch fails, 2 on a configuration error.
  4g. Re-running is a no-op: zero inserts, zero revisions.

=== TASK 5: REAL PROOF (written into the verify script, NOT executed by you) ===
Phase A is self-contained and always runs. It uses fixture series whose
series_key starts with 'verify.mkt02c.' and fake transports only, and must
never touch real series or call Yahoo.
  - Splice insert: fixture series with NULL-provider rows from boundary F0 and a
    canned Yahoo payload covering dates before and after F0: exactly the dates
    strictly before F0 are inserted, all with source_provider 'yahoo'; no date
    >= F0 is written.
  - FRED rows untouched: ids, values and source_provider of every NULL-provider
    row are identical before and after (before/after comparison).
  - Overlap gate, both ways on the same fixture: a payload within tolerance
    passes; a payload where more than 1% of overlap days differ by more than
    0.02 refuses with zero writes and exit 1; a payload with one day off by more
    than 1.00 refuses with zero writes.
  - Idempotency: a second identical run writes nothing; a changed Yahoo value
    for a Yahoo-sourced date revises exactly that one row (old row closed, one
    active row for the date, two rows of history); a changed value for a date
    where the active row is NULL-provider is never touched.
  - Dry run: the gate runs and counts are reported, nothing is written (row
    counts identical).
  - Nightly survival: run the production nightly orchestrator (run_nightly)
    with a fake adapter that returns only the recent points over the spliced
    fixture. Every pre-F0 Yahoo-sourced row stays active and unchanged; zero
    revisions; the batch succeeds. This is the proof that the refresh does not
    erase the splice.
  - Provenance constraint: inserting an observation with source_provider
    'bogus' is rejected by the CHECK; NULL and 'yahoo' are accepted.
  - Downstream: the production read function used by the mkt03 series endpoint
    returns the spliced fixture as ONE ordered series whose first point is the
    earliest spliced date.
  - Loader --seed: the default (no flag) still reads the v1 seed (static
    check); loading the additions file is idempotent (second load changes
    nothing); every value in the additions file passes the live CHECK
    constraints; the six keys are unique and absent from v1; the five Yahoo rows
    carry the exact ids and names listed in Task 2c.
  - Loader linking, four cases on fixture rows: a matching id+name+type links;
    a wrong name does not link and prints a [FIND]; an already-linked series is
    not overwritten; a link that would violate the unique index is skipped
    with a [FIND], not a crash.
  - Yahoo units: canned payloads: instrumentType INDEX -> 'index points'; ETF
    -> currency code; instrumentType missing -> currency code.
  - Existing tables untouched: counts of portfolio.securities_global,
    portfolio.securities_global_prices and public.fx_rates equal before and
    after; non-fixture counts in the three market_data tables equal before and
    after.
  - Teardown deletes fixtures in strict FK order (observations, then
    indicator_ingest_runs by fixture series_id and by any batch_id this script
    created, then series), confirmed against information_schema, and re-reads
    every table to confirm non-fixture counts are exactly as before. Fail loudly
    with SystemExit(2) if any fixture row remains. Never TRUNCATE.
Phase B runs ONLY with `--live`, after the operator has run the steps printed
at the end.
  - fred.sp500: earliest active observation is before 1990-01-01 (report the
    actual date); no gap longer than 10 days after 1960-01-01 (report the
    longest gap as [FIND] either way); every row dated before the earliest
    NULL-provider row has source_provider 'yahoo' and no row on or after that
    date does; the count of NULL-provider rows is at least 2,514.
  - Using the production read functions: fred.sp500 with anchor 2000-03-10 in
    index mode does NOT float, has value exactly 100 at the anchor, and its
    latest value equals 100 * latest / anchor computed independently in SQL.
  - fred.baa10y is 'active' with first observation before 1990-01-01 and more
    than 8,000 rows, or reported as a [FIND] with its last_error (reporting,
    not failure, if FRED says the code does not exist).
  - Each of the five new Yahoo series is 'active' with observations and linked
    to its security, or reported as a [FIND] with last_error (reporting, not
    failure: the endpoint is unofficial). Any that is active must satisfy the
    usual continuity checks.
  - Units: every Yahoo series whose meta says INDEX shows 'index points'
    (^RUT, DX-Y.NYB and the five new ones); report any exception as [FIND].
  - The mkt03 catalog (production function) lists the new links as selectable
    with their series_key.
  - Registry size: 81 series; print counts by ingest_status.

=== TASK 6: DOCS ===
Update docs/PROJECT_STATUS.md: add an mkt02c entry; under the existing mkt02
and mkt03 entries append `UPDATE 2026-10-03` lines stating their final verify
results (mkt02 47 passed, mkt03 68 passed, mkt01 57 passed, all 0 failed) so the
older "verify WRITTEN, not yet run" statements are corrected without rewriting
them.
Update docs/MARKET_DATA_DESIGN_V1.md: a new section on long history covering
the FRED rolling window (about ten years for S&P 500, October 2023 for the ICE
BofA spreads); the splice design and its rules (one series, insert-only before
F0, overlap gate with its thresholds, FRED rows never touched); row-level
provenance in observations.source_provider; the finding that the nightly
refresh never closes rows absent from a fetch (state it only if Task 1b
confirmed it); the credit-spread substitute and its license_class
'unreviewed' pending review; the five underlyings and their links; the Yahoo
units rule; and an addition to the Launch blockers: the S&P 500 history before
F0 is Yahoo-sourced, so the Yahoo blocker covers it, and mkt04's per-tenant
gate must be able to hide Yahoo-sourced ROWS, which is why provenance is
recorded per row. Note that TOPIX and the remaining unlinked index securities
(MSCI EAFE, the two Nasdaq-100 variants, S&P 500 Futures Excess Return) still
have no price source. Do not edit CLAUDE.md; list any lines you think it should
gain at the end of PROJECT_STATUS.

=== VERIFICATION: apps/api/scripts/verify_mkt02c.py ===
Pass/fail only. No interactive prompts. WRITE it and make it runnable; do NOT
run it. Hydrate secrets internally using the same helper the earlier verify
scripts use. Output lines begin with [PASS], [FAIL], [FIND], or [SKIP]; the
last line is `TOTAL: <passed> passed, <failed> failed`. Exit non-zero on any
[FAIL]. Every assertion states why it matters, not just what it checked. One
assertion per bullet in Task 5, Phase A always, Phase B behind --live. Do not
chain the other verify scripts; the operator re-runs them standalone.

When finished, STOP and print exactly these operator commands, in this order:
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_ingest.py load --seed docs/market_data/market_indicator_registry_additions_v2.json
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_ingest.py validate
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_ingest.py backfill --series fred.baa10y
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_ingest.py validate --provider yahoo
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_ingest.py backfill --provider yahoo
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_splice_history.py --dry-run
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_splice_history.py
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_nightly.py
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02c.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt03.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt01.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
