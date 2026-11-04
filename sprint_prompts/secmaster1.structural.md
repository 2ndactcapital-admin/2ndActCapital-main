SECMASTER1 — fill the security master from free public sources: a one-time
load, then nightly refreshes that run as scheduled jobs on the workflow
engine so every run is visible, alerted and monitorable. Sources: Nasdaq
Trader symbol directory (the listed universe + ETF flag), SEC company
tickers (CIK + exchange), FinanceDatabase (sector / industry / country /
fund category), OpenFIGI (FIGI + security type, and ticker for a CUSIP), and
the SEC Official List of Section 13(f) Securities (CUSIPs). 9 tasks +
verification.

Context: the platform security master has 67 securities, 51 CUSIPs and 13
tickers. HollisMuse could not find AAPL / MSFT / GOOG. Every price, model,
replica, held-away and X-ray feature resolves through this master, so an
empty master caps the whole product. marketdata1 built the vendor-neutral
provider registry (licence facts, ranks per data type, budgets, quality
events); this sprint adds REFERENCE DATA providers to it. It does not add
prices.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (live DB + repo, 2026-11-04; Task 1
confirms signatures):
- Tables: portfolio.securities_global (id, name, short_name, security_type,
  currency_code, price_coverage, merged_into_id, canonical_id, valid_from,
  valid_to, system_from, system_to); security_type CHECK: equity,
  fixed_income, fund, private_fund, structured_note, index, real_asset,
  cash, other, option. portfolio.securities_global_identifiers (id,
  global_security_id, id_type, id_value, is_primary, + bitemporal); id_type
  CHECK: cusip, isin, ticker, sedol, figi, lei, internal. There is NO column
  anywhere for exchange, CIK, sector, industry, country, ETF flag or listing
  status. Also securities_global_prices / _relationships / _corporate_actions
  / _note_terms.
- services/securities_global.py: create_security, add_identifier,
  get_by_identifier, normalize_identifier_value, merge_securities,
  canonical_id_for, backfill_canonical_ids; Super Admin required for writes
  (_require_super_admin). Row-at-a-time — too slow for tens of thousands.
- marketdata1: portfolio.data_providers (key, name, kind, status,
  capabilities[], lic_internal_use, lic_member_display, lic_redistribution,
  retention_after_cancel, attribution, terms_note, confirmed_on,
  daily_call_budget, requires_entitlement, owner_scope, org_id, bitemporal);
  provider_call_log; security_data_runs (run_trigger, status, started_at,
  finished_at, summary, owner_scope, org_id); services/security_data/
  (registry, layer, quality, nightly, providers/). docs/SECURITY_DATA_DESIGN.md
  has the 5-step "add a vendor" checklist — follow it for each provider.
  marketdata1's own nightly (scripts/security_data_nightly.py) was NEVER
  scheduled (its PROJECT_STATUS entry asks the operator for a Render cron).
- The jobs tool: the workflow engine. Scheduled triggers
  (workflow_triggers.trigger_type='scheduled') are fired every 5 minutes by
  the Render cron 2ndactcapital-workflow-scheduler
  (apps/api/workflow_scheduler_tick.py → services/workflow_scheduler.py) via
  workflow_engine.start_workflow_run; a failing run is HELD and alerted by
  workflow_todos.create_held_run_alerts; a held run blocks re-firing.
  Runs are in public.workflow_runs (org_id NOT a platform scope) and
  workflow_run_steps; the Runs screen gates on view_workflow_runs. System
  workflows exist ("system:human_task:approve_models v1") and perms1 made
  them undeletable. services/action_registry.py is the action catalog.
- SEC access: services/edgar_fetch.py already sends the SEC-compliant
  User-Agent and rate limit — reuse it for every sec.gov request.
- CUSIP → ISIN is computable: "US" + CUSIP + Luhn-style ISIN check digit.
- Regression: this adds providers and data types to marketdata1's registry
  and writes the shared security master → re-run verify_marketdata1.py
  ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: the sprint must NOT run npm run build, any full
  verify script, or the full load. Batches stream; never hold a whole file
  set in memory.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. Network: ONLY the five sources above, and in Task 1 only small
sample requests (one page / first 200 lines each) to confirm formats; no full
download in the sprint. Never print a secret. Do not overwrite or merge an
existing real security automatically: a conflict becomes a review item.
Never delete a security or identifier: retire it (valid_to). Licence facts
recorded per provider (CUSIP and FinanceDatabase rights are NOT redistribution
rights: lic_redistribution = false, member display of CUSIPs = own holdings
only, terms_note says so). Light/cream UI with the existing tokens. Commit ALL
sprint files (excluding sprint_prompts/logs) on the current branch at the end
with message "secmaster1: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. Formats, from a small sample of each: nasdaqlisted.txt and
      otherlisted.txt (nasdaqtrader.com SymDir: columns, ETF flag, Test
      Issue, footer line); sec.gov/files/company_tickers_exchange.json;
      FinanceDatabase (the JetBrains/GitHub-hosted CSVs for equities and ETFs:
      columns, size, update cadence, licence file); OpenFIGI v3 /mapping
      (request/response, rate limits with and without OPENFIGI_API_KEY —
      check Doppler by NAME only); the SEC 13(f) Official List (current
      quarter's text file: fixed-width layout, status flags ADDED / DELETED,
      the option / "*" marker, how to detect a new quarter's file).
  1b. The existing 67 securities: how many match each source by CUSIP /
      ticker; any that would conflict (same ticker, different CUSIP).
  1c. How a platform-level (not org) job should run on the workflow engine:
      can a workflow / trigger / run be platform-scoped; if not, the
      smallest safe way (a platform system org, or owner_scope on the run)
      so 2nd Act's org admins do NOT see platform jobs and Super Admin does.
      How a service-task step calls a Python action; how long a step may run
      inside the 5-minute cron tick (the initial load must not run there).
  1d. Symbol normalisation across sources (BRK.B / BRK-B / BRK/B; share
      classes; preferreds, warrants, units, rights) — pick one canonical form.
  1e. Where search reads the master (security pickers, the securities grid,
      Muse's failing lookup) and whether it needs an index (trigram on name,
      ticker) at ~30k rows.

=== TASK 2: SCHEMA ===
From Task 1: a bitemporal reference-attributes store (per security, per
field: exchange, cik, sector, industry, industry_group, country,
is_etf, fund_category, listing_status (listed | delisted | unknown),
first_seen, last_seen; each value with provider_key and as_of) read through
marketdata1's rank per data type (new data type 'reference'); a 13(f) list
table (quarter, cusip, issuer, description, status, is_option); a review
queue for conflicts (source A vs B vs existing row). RLS for every
operation; platform rows readable by every org, written only by the job role
/ Super Admin. Indexes from 1e. Apply with scripts/_apply_secmaster1.py
through Doppler, synchronously; VERIFY it landed; save
docs/secmaster1_part1.sql. Five provider rows with real licence facts.

=== TASK 3: PROVIDERS ===
One adapter per source in services/security_data/providers/, each:
streaming parse, normalise (1d), return rows; budget-checked and logged in
provider_call_log. OpenFIGI: batched mapping, honours its rate limit, uses
OPENFIGI_API_KEY if present, else the keyless limit; only identifiers not
mapped in the last 30 days.

=== TASK 4: THE MATCHER / UPSERT ===
services/security_master_load.py: match by CUSIP → FIGI → (ticker +
exchange) → none; create new securities in batches (one transaction per
batch, executemany / COPY, not row-at-a-time); add identifiers (ISIN derived
from CUSIP); write attributes with provenance; a security absent from the
listed universe for 3 consecutive runs → listing_status delisted (never
deleted); a ticker that moves to a new CUSIP → old identifier retired
(valid_to), new one added; any disagreement with an existing row → review
queue, nothing overwritten. Idempotent: a second run on the same files
writes nothing.

=== TASK 5: 13F CUSIPS ===
Load the current quarter's Official List; map each non-option CUSIP through
OpenFIGI (ticker, exchange, security type, FIGI) and to an existing / new
security; unmapped CUSIPs stay in the 13(f) table with "not mapped — <why>".
DELETED-status rows retire nothing by themselves; they flag review. A new
quarter's file is detected and loaded once.

=== TASK 6: ONE-TIME LOAD ===
scripts/secmaster_initial_load.py — run by the OPERATOR, not the sprint:
--dry-run (counts only), --source <name>, --resume (checkpointed by
source + batch; safe after a crash), a progress line per batch, a summary
(new / matched / conflicts / unmapped per source), and a run row in the same
job history the nightly uses. Order: Nasdaq → SEC tickers → OpenFIGI for
those → 13F list → OpenFIGI for 13F CUSIPs → FinanceDatabase attributes.

=== TASK 7: NIGHTLY JOBS ON THE WORKFLOW ENGINE ===
Platform system workflows (undeletable, Super Admin only, per 1c), each with
a scheduled trigger and service-task steps calling registered actions:
  - "Security master — nightly" (weekdays, after US close + Nasdaq's file
    update, UTC from 1c): Nasdaq + SEC tickers delta → matcher → OpenFIGI
    for new identifiers only (within budget) → delisting check.
  - "Security master — 13F list" (daily check, acts only when a new quarter
    is published).
  - "Security master — attributes" (weekly): FinanceDatabase.
  - "Security data — nightly": marketdata1's existing unscheduled nightly,
    moved onto the same mechanism (its Render cron is no longer needed).
Each run's step output records counts and the review items it created; a
failure HOLDS the run and alerts Super Admin through the existing held-run
alert; a step stays inside the tick's limit (bounded batch, resumes next
night). A "Run now" from the Runs screen for Super Admin. A Security master
status panel (Super Admin): counts by type / source, last run per job and
its result, review-queue count, unmapped 13F count.

=== TASK 8: REAL PROOF ===
  - Each adapter parses a recorded sample fixture (no network in the
    verify), including the Nasdaq footer and Test Issue rows (excluded) and
    a 13F option line (excluded).
  - Matcher: CUSIP match, FIGI match, ticker+exchange match, new; a
    conflicting existing row → review item, existing row unchanged
    (checksum); second run writes nothing; ticker change retires the old
    identifier; 3-run absence → delisted, nothing deleted.
  - ISIN check digit correct for known CUSIPs (AAPL 037833100 →
    US0378331005).
  - 13F: new quarter loads once; DELETED flags review only; unmapped reason
    recorded.
  - Jobs: each workflow exists, is a system workflow, undeletable; its
    trigger fires through workflow_scheduler (injected instant) and the run
    appears in workflow_runs with step output; a forced step failure HOLDS
    the run and creates the Super Admin alert; a held run blocks re-fire;
    an org admin of 2nd Act does NOT see the platform runs, Super Admin
    does — identical requests.
  - Search: a fixture "AAPL"-shaped security is found by ticker and by name
    through the same route Muse used.
  - Licence facts on every new provider; member display of CUSIPs only for
    own holdings.
  - Real securities / identifiers unchanged except by the operator's load
    (count + checksum); fixture rows use reserved prefixes that match no real
    row (assert it).
  - Org isolation both directions (rolbypassrls = false); own routes via
    _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_marketdata1.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md and docs/SECURITY_DATA_DESIGN.md (the five
providers, their licence facts, the matcher rules, the jobs and their
schedules, operator steps: add OPENFIGI_API_KEY to Doppler (optional, faster),
run secmaster_initial_load.py --dry-run then the real load, work the review
queue, remove marketdata1's pending Render-cron step); add
verify_secmaster1.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_secmaster1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. NO network
to any data source: recorded fixtures only. Wrap every section so an
exception is a [FAIL]; teardown via scripts/_verify_cleanup.py at start and
end, by fixture id (fixture securities, identifiers, attributes, 13F rows,
review items, workflow runs / steps / triggers / todos); re-read every
touched table for its exact pre-test count. Fixture orgs and users is_test =
true (assert it). Read UNIQUE / CHECK / FK constraints from the catalog
before seeding. Support --self-checks-only and --cleanup-only. The
regression subprocess gets VERIFY_NESTED=1, after teardown, 2700 s; with
VERIFY_NESTED=1 skip it with one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool — wrap every
    `a and b` (and any list / dict value) in bool(...).
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read; app_service rolbypassrls = false asserted for
    isolation checks; an empty list never passes a check.
  - SQL parameters inside CASE / COALESCE get an explicit cast — in the code
    too.
  - A test stub must match the CURRENT signature of what it replaces.
  - Key a real table's fingerprint by its real key; exclude only columns
    real activity updates, and name them.
  - Immutability triggers: use the documented privileged path the existing
    cleanup uses, by fixture id; never weaken a trigger.
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1e
  [Y] Schema landed, RLS every operation, five providers with licence facts
  [Y] Adapters parse fixtures; exclusions correct
  [Y] Matcher: four match paths, conflicts to review, idempotent, retire
      never delete
  [Y] ISIN derivation; 13F load once per quarter, unmapped reasons
  [Y] Jobs: system workflows, scheduled fire, held + alert on failure,
      platform runs hidden from org admins, visible to Super Admin
  [Y] Search finds by ticker and name
  [Y] Real rows unchanged; org isolation; own routes gated; --check CURRENT
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_marketdata1.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
