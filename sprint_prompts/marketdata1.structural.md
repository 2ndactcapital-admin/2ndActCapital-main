MARKETDATA1 — a vendor-neutral market-data layer for securities: one
canonical store for end-of-day prices, corporate actions, fund holdings
snapshots, index levels and option quotes; a provider registry with ranked
sources per data type, licence rights on every value, call budgets and
data-quality checks; adapters for the sources that cost nothing today; every
"needs prices" reader switched to the layer. No paid vendor is added. 9 tasks
+ verification.

Context: Joe (2026-10-28): do not architect around one vendor and do not pay
for one (FMP or other) until a real use case needs it; any vendor must be a
plug-in behind the same interface, switched on by configuration. Today every
priced figure in Replication Studio reads "not estimated — needs prices"
(tracking error, harvest value of switching, after-tax performance, X-ray
switching benefit, viability Q3) or depends on rep8's adviser-entered quotes.
Once real accounts flow from the custodian, the custodian's own daily
valuations price everything members hold — that is the first free source.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main after the rep8 merge; Task 1
confirms exact signatures):
- services/market_data/ (mkt01–mkt03) is the PLATFORM-GLOBAL indicator
  history package: registry.py (indicator_series seed), fred.py (FRED HTTP
  client, injectable transport, throttle, retry, scrub), adapters.py
  (provider-adapter registry: fred, yahoo), yahoo.py (personal-use only),
  ingest.py (one write path, Rule 3 valid-axis restatement, race-safe),
  nightly.py (run_nightly, per-series isolation, summary row), staleness.py,
  read side with access.py as THE read gate. Design doc
  docs/MARKET_DATA_DESIGN_V1.md says it is separate from security pricing on
  purpose. Reuse its adapter / ingest / staleness PATTERNS (and FRED for
  Treasury curves); do not merge the two stores.
- portfolio.securities_global_prices (id, global_security_id, price_date,
  price, currency_code, price_type, source, valid_from, valid_to,
  system_from, system_to) — 0 rows. Readers: services/harvest.py,
  portfolio_securities.py, rebalance_proposals.py, replica_viability.py,
  securities_global.py, xray.py (Task 1 lists exactly what each reads).
- portfolio.adviser_quotes (rep8: id, org_id, global_security_id, kind
  premium | index_level | underlying_price | implied_vol, value, source,
  as_of, entered_by, …; insert-only by trigger).
- portfolio.replica_holdings_sources (key, issuer, url_template, parser,
  format, active, match_tickers, priority, terms_of_use_note,
  confirmation_note, confirmed_on, owner_scope, org_id, …) — the rep1
  holdings sources (State Street SPY works; Invesco 406; iShares bot-gated;
  N-PORT fallback months stale). Its parser CHECK is narrow.
- Account valuations: portfolio.positions market_value / quantity as of
  as_of_date (rep5 calls this price source 'account_position'); held-away
  positions carry source + as-of (rep7).
- The live Render scheduler skips is_test orgs; the market-data nightly
  service is hollisworks-market-data-nightly.
- Verify cleanup: scripts/_verify_cleanup.py (fast, scoped; --cleanup-only).
- Regression: this sprint changes what harvest / proposals / X-ray /
  viability read for prices → re-run verify_rep6.py ONCE, flat, after
  teardown, VERIFY_NESTED=1, 2700 s.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never poll in a loop, never start a
script in the background; run every command synchronously in the foreground
and read its output directly. Write the verify script but DO NOT RUN IT, and
do not run npm run build. No network calls in the sprint or the verify
(adapters take an injectable transport; tests use recorded fixtures). Never
print a secret. Column names from the live DB. Bi-temporal rule 3 for every
new table; RLS policies for EVERY operation each new table will take (org
data org-scoped; licensed reference data platform-scoped with entitlement
rows). Decimal everywhere. Never invent a price: a figure with no source of
the required rights reads "not priced" and names what is missing. No vendor
name appears in any reader's code — readers ask the layer for a data type.
Labels and vocabularies from the API envelope. Light/cream UI with the
existing tokens. Commit ALL sprint files (excluding sprint_prompts/logs) on
the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Every reader of a price, index level, holdings weight, dividend /
      split or option quote today (file:line), what it reads and what it
      does when nothing is there.
  1b. services/market_data's adapter, ingest, throttle, staleness and nightly
      patterns — which parts can be shared as a small common module without
      changing mkt01–03 behaviour.
  1c. How rep1's holdings sources / parsers and rep8's adviser quotes map
      onto a common "source" model.
  1d. What licence facts must be recorded per source (internal use only |
      member display allowed | redistribution; retention after cancellation;
      attribution text) and where a member-facing screen shows a price today.

=== TASK 2: SCHEMA ===
  - data_providers (platform): key, name, kind (custodian | regulator_filing
    | issuer_file | public_api | csv_upload | adviser_entry | vendor),
    status active | not_connected | retired, licence: internal_use,
    member_display, redistribution, retention_after_cancel, attribution,
    terms note + confirmed_on, daily call budget.
  - data_source_rank: per data type (eod_price | corporate_action |
    fund_holdings | index_level | option_quote | fx) an ordered provider list
    (platform default; org override).
  - Canonical stores: securities_global_prices (existing — add provider,
    as_of, quality flags, licence snapshot), corporate_actions (dividends
    with ex / record / pay dates, splits with ratio), index_levels, and
    fund holdings stay in replica_holdings (add provider + licence);
    adviser_quotes stays (provider 'adviser_entry').
  - data_quality_events: stale, gap, outlier (> setting × median daily
    move), cross-source disagreement, budget exhausted — per item and day.
  - provider_call_log: calls per provider per day (budget enforcement).
Apply with scripts/_apply_marketdata1.py through Doppler, synchronously;
VERIFY it landed; save docs/marketdata1_part1.sql.

=== TASK 3: THE LAYER (services/security_data/) ===
  - get(data_type, security / index, as_of, *, purpose internal |
    member_display) → value + provider + as_of + quality + licence, walking
    the rank; a source whose licence does not allow the purpose is skipped;
    nothing found → a "not priced" result naming what is missing.
  - Bulk forms for a basket and a date range; Decimal; no vendor names.
  - Staleness per data type (setting), cross-source disagreement flagged
    when two sources differ by more than a setting.
  - Provider interface (one module per provider): capabilities (data types),
    fetch with an injectable transport, throttle, retry, scrub, budget.

=== TASK 4: FREE ADAPTERS ===
  - custodian (account valuations: positions' market value ÷ quantity as of
    the position date — internal use; member display allowed for the
    member's own holdings only);
  - regulator_filing: SEC N-PORT holdings (existing rep1 path, re-homed);
  - issuer_file: the existing rep1 issuer parsers (State Street) re-homed;
  - public_api: FRED for Treasury curves / rates as index_level-type series
    (reuse fred.py; no new FRED keys);
  - csv_upload: prices / corporate actions / index levels / holdings from a
    file through rep3's mapping machinery, logged, maker-checker approval
    before it counts, licence entered by the uploader;
  - adviser_entry: rep8's quotes as a provider.
  - vendor slots: a stub adapter shape ("not connected", no network) so a
    vendor (FMP, Tiingo, EODHD, Polygon, Nasdaq Data Link …) is a new module
    + a registry row + a rank entry — nothing else changes. Write the
    5-step "add a vendor" checklist in the design doc.

=== TASK 5: SWITCH THE READERS ===
Every reader from 1a asks the layer. Where a value now exists from a free
source (e.g. custodian valuations for held securities), the "needs prices"
line becomes a figure labelled estimate with its source and as-of; where it
does not, the reader keeps its current message but names the missing data
type. Behaviour with no data anywhere is exactly today's.

=== TASK 6: DAILY JOB ===
A nightly security-data run (separate from the indicator nightly; skips
is_test orgs for org data): refresh ranked sources within budgets, compute
quality events, summary row; to-dos for stale / disagreeing items that feed
open proposals.

=== TASK 7: ADMIN SCREENS ===
Super Admin: providers (status, licence facts, budgets, call log), rank per
data type; org admin: rank overrides within the allowed providers; a
data-quality page; every priced figure in the app shows source + as-of on
hover. Routes gated, in AUTHZ1_ROUTES; envelopes with vocabularies.

=== TASK 8: REAL PROOF ===
  - Rank walk: first allowed source wins; a source whose licence forbids the
    purpose is skipped (member_display vs internal); nothing → "not priced"
    naming the type.
  - Custodian valuation prices a held security for internal use and for
    that member's own display, never for another member.
  - CSV upload: mapping reused, rejects with reasons, maker-checker, licence
    recorded, counts only after approval.
  - Quality: stale both edges (exactly at the setting vs one day over),
    gap, outlier, disagreement, budget exhausted → skips to the next source.
  - Readers: with no data anywhere every reader returns exactly today's
    message; with a custodian valuation, the harvest / X-ray / viability
    figure appears with source + as-of; no reader references a vendor name
    (static scan).
  - Vendor stub: registered "not connected", no network, adding it to a rank
    changes nothing until connected.
  - mkt01–03 indicator behaviour unchanged (their read gate and nightly).
  - Org isolation both directions (rolbypassrls = false); gates both ways;
    node tests show no admin controls without the flags.
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_rep6.py ONCE, flat, after teardown, VERIFY_NESTED=1,
    2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/SECURITY_DATA_DESIGN.md (the layer, ranks,
licence rule, quality rules, the add-a-vendor checklist), docs/
REPLICATION_STUDIO.md (which "needs prices" lines now fill from free
sources), add verify_marketdata1.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_marketdata1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. No network
calls (recorded fixtures through the injectable transports). Wrap every
section so an exception is a [FAIL]; use scripts/_verify_cleanup.py for
teardown at start and end (rows this run created, by fixture id); re-read
every touched table for its exact pre-test count. Compute every expected date
at the call. Fixture orgs and users is_test = true (assert it). Read UNIQUE /
CHECK / FK constraints from the catalog before seeding and check every seed
literal against its lookup or CHECK. Reserved fixture prefixes that match no
real row (assert it). Support --cleanup-only. The regression subprocess gets
VERIFY_NESTED=1, runs after teardown, 2700 s timeout; with VERIFY_NESTED=1
this script skips its regression section with one '[SKIP] nested run' line.
Route checks count THIS sprint's routes from its own diff and prove they
still exist — never "every route added since a base commit".

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool — wrap every
    `a and b` in bool(...).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Every import at the top of the file; never `x, y = (await f(a) for a …)`.
  - SQL parameters inside CASE / COALESCE get an explicit cast — in the code
    too.
  - A failing check's detail prints the response body / culprit rows.
  - Never touch real prices, holdings, positions, quotes, indicator series or
    users: count + checksum before and after.

Assertions:
  [Y] Task 1 findings 1a–1d
  [Y] Schema landed, RLS for every operation
  [Y] Rank walk + licence rule + "not priced"
  [Y] Free adapters: custodian, N-PORT, issuer file, FRED, CSV (maker-
      checker), adviser entry; vendor stub not connected
  [Y] Quality events, both stale edges, budget fallback
  [Y] Readers switched; no-data behaviour unchanged; no vendor names (scan)
  [Y] mkt01–03 indicators unchanged
  [Y] Org isolation both directions; gates both ways; node tests
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_rep6.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
