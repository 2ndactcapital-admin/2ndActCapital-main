PRICES1 — end-of-day price history through the vendor-neutral security-data
layer: make the provider "receptor" a documented plug-in contract that any
vendor (free or paid) fills by adding one adapter + one registry row, ship the
first FREE end-of-day adapter behind it, load the history the platform needs,
and keep it current with a platform job. 9 tasks + verification.

Context: Joe (2026-11-06): "a generic receptor that we can plug in — for now,
likely a free source." No paid vendor yet (FMP ~$139/mo deferred until a
concrete use; no architecture tied to any vendor). Today every listed
security has price_coverage 'unknown' and portfolio.securities_global_prices
holds 0 rows, so tracking error, backtests, after-tax performance, harvest
value, drift, the X-ray's "needs prices" lines and (later) the treasury
sleeves cannot be computed.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo + live DB 2026-11-06; Task 1
confirms signatures):
- marketdata1 built the layer: services/security_data/ — layer.py
  (get, get_bulk, history, _plan by rank), registry.py, quality.py,
  writes.py, nightly.py, settings.py, types.py, uploads.py;
  providers/base.py (the provider contract), providers/vendor_stub.py (the
  "not connected" vendor slot), custodian.py, csv_upload.py, fred.py,
  platform_entry.py, adviser_entry.py, sec_nport.py, issuer_file.py,
  store.py; providers/__init__.py build_providers / code_registry.
  docs/SECURITY_DATA_DESIGN.md has the 5-step "add a vendor" checklist.
- portfolio.data_providers (key, kind, status, capabilities[], licence
  facts lic_internal_use / lic_member_display / lic_redistribution,
  retention_after_cancel, attribution, terms_note, confirmed_on,
  daily_call_budget, requires_entitlement); portfolio.data_source_rank
  (rank per data type); provider_call_log; security_data_runs;
  quality events.
- portfolio.securities_global_prices columns: id, global_security_id,
  price_date, price, currency_code, price_type, source, provider_key, as_of,
  quality_flags, licence, org_id, upload_id + bitemporal. 0 rows.
  securities_global.price_coverage CHECK: has_series | no_public_source |
  unknown — all 15,970 'unknown'.
- services/market_data/yahoo.py exists for the market-indicators module
  (internal-use assumption, Joe 2026-10-03); FRED is a provider.
- secmaster1: the security master (15,959 securities, tickers / CUSIPs /
  FIGIs) and platform jobs as system workflows in the Hollisworks org;
  bugfix4 added the Super Admin job on / off switch (jobs default OFF).
- bugfix4: the roll-up values an unpriced listed position at its entered
  value ("entered value"); a priced position uses the price.
- Regression: this fills the price store every price reader uses →
  re-run verify_marketdata1.py ONCE, flat, after teardown, VERIFY_NESTED=1,
  2700 s.
- WSL memory is limited: the sprint must NOT run npm run build, any full
  verify script, or the backfill. Batches stream.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. Network: ONLY the candidate free sources in Task 1, small samples
(a few symbols, a short range). Never print a secret. No vendor-specific code
outside its adapter file; the rest of the platform reads prices only through
the layer. Licence facts are recorded per provider and ENFORCED: a value
whose licence does not allow member display is never shown to a member
(labelled "internal" for staff). Decimal everywhere. Bi-temporal rule 3; RLS
for every operation. Do not switch on any job for a real org. Light / cream
UI. Commit ALL sprint files (excluding sprint_prompts/logs) on the current
branch at the end with message "prices1: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. providers/base.py's contract today (methods, capabilities, budget /
      call-log hooks, the transport injection used by verifies) and what an
      EOD price adapter must implement; gaps for history backfill.
  1b. Candidate FREE end-of-day sources — for each: coverage (US stocks,
      ETFs, mutual funds / money-market NAVs), history depth, rate limits,
      key needed?, bulk download?, adjusted vs unadjusted, and the TERMS
      (personal / non-commercial only? attribution?). At least: Stooq (bulk
      daily files), Yahoo (the existing indicators adapter), Tiingo free
      tier, Alpha Vantage free tier, Polygon free tier, Nasdaq Data Link
      free sets. Small sample request each where no key is needed; check
      Doppler by NAME only for keys (TIINGO_API_KEY, POLYGON_API_KEY,
      ALPHAVANTAGE_API_KEY). Recommend one primary + one fallback; state the
      licence facts to record.
  1c. Who needs prices: every security held in a live position, in a model /
      replica / tilt, in a watch list, plus benchmarks — the "coverage set"
      (count it today; never "all 16k").
  1d. Every price reader (roll-up, X-ray, tracking error, backtest, harvest,
      drift, options payoff, notes) and how it asks the layer.

=== TASK 2: THE RECEPTOR (PLUG-IN CONTRACT) ===
Formalise the EOD price adapter contract in providers/base.py (or a sibling
module): fetch_eod(symbols, start, end) → rows (date, open?, high?, low?,
close, adjusted_close?, volume?, currency), declared capabilities, rate
limit / budget, identifier it keys by (ticker / FIGI / CUSIP via the master),
licence facts, and a transport injection point for tests. Adding a vendor =
one adapter file + one data_providers row + its rank, nothing else — prove it
by turning vendor_stub into a "paid vendor template" that passes the contract
tests with a fake transport. docs/SECURITY_DATA_DESIGN.md: the contract and
the updated checklist.

=== TASK 3: THE FIRST FREE ADAPTER ===
The Task 1 primary (and fallback if cheap), behind the contract; licence
facts from 1b (internal use; member display only if the terms allow it, else
not_allowed); attribution string; budget per day; quality checks (stale,
gap, spike, zero / negative, currency mismatch → quality events, never
silently stored); unadjusted close stored, adjustment factor kept where the
source gives it.

=== TASK 4: STORE AND COVERAGE ===
Prices into securities_global_prices through writes.py (provider_key,
licence, as_of); idempotent by (security, date, provider); a restated price
is a new system version. price_coverage set to has_series when a usable
series exists, no_public_source when the source has none for it — with the
reason recorded.

=== TASK 5: BACKFILL (OPERATOR) ===
scripts/prices_backfill.py — the coverage set (1c), N years (default 5,
flag), --dry-run (counts, estimated calls / time), --resume (checkpointed),
streaming batches, a progress line, a summary per provider, a run row in the
job history. The operator runs it.

=== TASK 6: NIGHTLY JOB ===
A platform system workflow "Security data — prices" (weekdays after the
source's daily update, UTC per 1b): the coverage set's last few days (gap
fill), new members of the coverage set get their history, within budget,
resuming next night; failure HOLDS and alerts Super Admin; shown on the job
switch screen, OFF until the operator turns it on.

=== TASK 7: READERS ===
The 1d readers get prices through the layer as now; with a series present
they compute (tracking error, backtest, harvest value, X-ray, drift) instead
of "needs prices"; every number shows source, as-of and "estimate" where it
is one; licence-restricted values never reach a member. The roll-up uses
the price when a series exists, else bugfix4's entered value.

=== TASK 8: REAL PROOF ===
  - Contract: the template vendor adapter + a fake transport passes the same
    contract tests as the free adapter (plug-in proven without code changes
    elsewhere).
  - Adapter parses recorded sample files / responses (fixtures; no network
    in the verify); quality checks raise events on a stale, gapped, spiked
    and zero fixture series.
  - Store: idempotent re-run writes nothing; a restatement is a new system
    version; coverage flags set with reasons.
  - Backfill: --dry-run counts; resume after a forced stop continues without
    duplicates (fixture).
  - Job: fires through the scheduler (injected instant), holds + alerts on a
    forced failure, OFF for real orgs.
  - Readers: a fixture portfolio with a fixture series computes tracking
    error / backtest / X-ray values exactly (hand-computed); without a series
    they still say "needs prices"; the roll-up switches from entered value to
    price.
  - Licence: a not_allowed provider's price is hidden from a member,
    labelled internal for staff — identical request both ways.
  - Org isolation both directions (rolbypassrls = false); real rows
    unchanged except the platform price store fixtures (count + checksum).
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_marketdata1.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md and docs/SECURITY_DATA_DESIGN.md (the contract, the
chosen free source and its licence facts, how to plug FMP or any vendor in
later, operator steps: backfill --dry-run → real run → switch the nightly job
on); add verify_prices1.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_prices1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. NO network
to any data source: recorded fixtures only. Wrap every section so an
exception is a [FAIL]; teardown via scripts/_verify_cleanup.py at start and
end, by fixture id (fixture securities, prices, quality events, runs,
workflow runs / steps / triggers / todos, providers, ranks); re-read every
touched table for its exact pre-test count. Fixture orgs and users is_test =
true (assert it). Read UNIQUE / CHECK / FK constraints from the catalog
before seeding. Reserved fixture prefixes that match no real row (assert it).
Support --self-checks-only and --cleanup-only. The regression subprocess gets
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip it with
one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read (the same org context the route sets);
    rolbypassrls = false asserted; an empty list never passes a check.
  - A refusal check sends a VALID body, so only the gate can refuse.
  - A recursive CTE has ONE anchor and ONE recursive term.
  - A test stub must match the CURRENT signature of what it replaces.
  - Key a real table's fingerprint by its real key; exclude only columns
    real activity updates, and name them.
  - Parse a nested script's OWN final ^TOTAL line (shared helper).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1d and the source recommendation with licence facts
  [Y] Plug-in contract proven with the template vendor (no other code changes)
  [Y] Free adapter: parse, quality events, licence facts
  [Y] Store idempotent; restatement; coverage flags with reasons
  [Y] Backfill dry run + resume; nightly job fires / holds / OFF
  [Y] Readers compute with a series, "needs prices" without; roll-up switch
  [Y] Licence enforced for members; isolation; own routes; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_marketdata1.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
