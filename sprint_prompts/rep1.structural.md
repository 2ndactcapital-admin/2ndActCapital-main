REP1 — Replication Studio, sprint 1 of 14: security registry, fund holdings
ingestion, fund terms extraction, and the fix/edit terms screen for index
ETFs and index mutual funds. No models, accounts or trades yet. 9 tasks +
verification.

Context: Replication Studio (spec: "Replication Studio — ETF & Fund-to-SMA
Spec", Joe, 2026-10-22) turns a fund into a separately managed account the
member owns directly. Phase 1 is index equity ETFs and index mutual funds.
This sprint builds the first two steps of the workflow: (1) pick a security
and find it in the registry, (2) read its documents into terms that staff fix
and edit, where a CORRECTION (the read was wrong) is kept apart from a
DELIBERATE CHANGE (a departure from the fund, saved as a new terms version).
Later sprints: rep2 custodian adapters + execution instructions + workflow
tasks, rep3 model builder, rep4 accounts/proposals, rep5 tax overlay, rep6
held-away accounts.

Decisions already made (Joe, 2026-10-22):
  - Every tenant (org) runs its own instance: terms versions, edits and
    approvals are org-scoped. Public source data (holdings files, fund
    documents, extracted readings) is platform-scoped and shared, fetched
    once.
  - The Hollisworks platform org may later publish models as a sub-adviser —
    keep the platform/org split clean so that is possible.
  - Display names for anything built from a fund start with "Holdings of"
    (e.g. "Holdings of QQQ"). Never present a licensed index name as our
    product's name; the index name is stored as a disclosed term only.
  - Every human action (correct, change, approve, reject) is logged as a
    workflow task/event (Task 1e decides the mechanism) as well as in
    audit_log.
  - Extraction does NOT use Claude models. Use the platform's configured
    extraction ensemble path (LiteLLM), as the structured-note extraction
    does.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main after bulkfetchui3, 2026-10-22):
- Security master: portfolio.securities_global (id, name, short_name,
  security_type, currency_code, price_coverage, merged_into_id, canonical_id,
  valid/system axes), portfolio.securities_global_identifiers (id_type,
  id_value, is_primary, …), portfolio.securities_global_prices,
  portfolio.securities_global_relationships (from/to global security, weight,
  link_state, proposal_* columns, …). Shared by the portfolio module and the
  note pipeline — reuse its existing creation/resolution services (Task 1a),
  never write a second resolver.
- Note-extraction pattern to mirror (not reuse blindly):
  portfolio.note_terms_field_registry (field_key, display_label, data_type,
  section, unit, enum_values, synonyms, trap_rule, extraction_method,
  is_critical, …), portfolio.note_term_readings (field_key, value, source,
  origin, source_quote, quote_verified, value_in_quote, raw_char_start/end,
  provider_model, ensemble_config_id, cost_usd, …), and
  document_field_corrections (field_name, original_value, corrected_value,
  notes, corrected_by, target_type, target_id).
- EDGAR access, rate limiting and the SEC User-Agent already exist in
  services/edgar_* (bulkfetch). Reuse them for fund documents.
- portfolio.tax_lots, accounts, workflow_definitions / workflow_runs /
  workflow_run_steps / workflow_versions / workflow_triggers and member_todos
  exist (not used by this sprint except Task 1e / Task 6).
- Menu: apps/web/lib/menuVisibility.mjs is the single menu definition; the
  "Portfolio" group (landing /portfolio) is where the Replication tab goes.
- authz1: every route has an explicit gate and an AUTHZ1_ROUTES.json entry
  (apps/api/scripts/authz1_routes.py --check must say CURRENT); never change
  an existing gate; nothing grants on an absent role; Super Admin first.
- Permission envelope pattern (CLAUDE.md): can_read / can_write and
  vocabularies from the server; write controls render only inside the
  envelope's flag, no fallback.
- Regression policy (docs/SPRINT_WORKFLOW_STANDARD.md, 2026-10-22): no full
  regression. This sprint writes to the shared security master, so it re-runs
  verify_underlyingresolution.py ONCE.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never poll in a loop, never start a
script in the background; run every command synchronously in the foreground
and read its output directly. Write the verify script but DO NOT RUN IT, and
do not run npm run build. Task 1 may make a FEW read-only live requests (one
holdings file per issuer, one EDGAR fund lookup) to confirm formats — nothing
else hits the network; tests mock every HTTP call and every model call. Never
print a secret. Column names from the live DB (docs/schema_snapshot.sql +
information_schema). Bi-temporal rule 3 for every new table; RLS policies for
EVERY operation each new table will take (SELECT/INSERT/UPDATE/DELETE as
applicable); platform rows use owner scope 'platform' with org_id NULL.
org_id only from the session. Labels and vocabularies from the API envelope.
Light/cream UI with the existing tokens. Commit ALL sprint files (new and
modified, excluding sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The security master's create/resolve services (file, function names):
      how a ticker / CUSIP / ISIN resolves to a global security, how a new
      common stock is created, and how unresolved matches are queued (the
      relationships proposal_* pattern). State exactly which functions this
      sprint will call.
  1b. Holdings sources. Make ONE read-only request each to confirm format,
      columns and as-of date for: Invesco (QQQ), State Street (SPY), iShares
      (IVV). Report URL pattern, format, identifier columns (ticker / CUSIP /
      SEDOL / ISIN), weight and shares columns, and anything in the site's
      terms of use about automated access or redistribution. Then SEC:
      confirm https://www.sec.gov/files/company_tickers_mf.json maps a fund
      ticker to CIK / series id / class id (one request), and how to find a
      series' latest N-PORT-P holdings and latest prospectus (485BPOS / 497K)
      through the existing EDGAR client. N-PORT is the fallback for any fund
      without an issuer parser (report its lag honestly).
  1c. The note-extraction ensemble path: how note_term_readings are produced
      (service, ensemble config, LiteLLM call helper, quote verification) and
      what can be reused for fund terms with a different field registry.
      Confirm no Claude model is configured on that path.
  1d. How document_field_corrections is written today (target_type values,
      who calls it) and whether target_type 'replica_terms' can be added
      without breaking existing readers.
  1e. The workflow engine: is there a service to create a task/run step for
      a human action and close it (maker, checker, outcome)? If yes, use it
      for every human action in this sprint. If only member_todos fits, use
      that for "approval needed" and record completed actions as
      workflow events in the closest existing table. State the choice.
  1f. Which scheduler path bulkfetchui3 used for its weekday index refresh,
      so Task 5's daily holdings refresh uses the same one.

=== TASK 2: SCHEMA ===
Design from Task 1's findings (names indicative):
  - replica_holdings_sources (PLATFORM): key, issuer, url template, parser
    name, format, active, terms-of-use note. Parsers are code; which source a
    fund uses is data.
  - replica_securities (ORG): the registry entry — global_security_id,
    instrument class (vocabulary: index_etf, index_mutual_fund for now;
    later classes listed but disabled), ticker, CUSIP, issuer, SEC CIK /
    series / class ids, holdings source key, status (draft, active,
    retired), "Holdings of <ticker>" display name. Unique live entry per
    (org, global security).
  - replica_holdings_snapshots (PLATFORM, immutable): fund global security,
    as_of date, source, file SHA-256, R2 key of the raw file, row count,
    total weight, fetched_at; unique (security, as_of, source, sha256).
  - replica_holdings_rows (PLATFORM, immutable): snapshot, raw identifiers
    and name as published, resolved constituent global security (nullable),
    resolution state, weight (decimal fraction), shares, market value, asset
    type (equity, cash, future, other).
  - replica_terms_field_registry (PLATFORM): fund terms fields, same shape as
    note_terms_field_registry. Seed: fund_name, ticker, issuer,
    instrument_class, index_name (disclosure only), investment_objective,
    expense_ratio_pct (gross and net), rebalance_frequency,
    reconstitution_schedule, weighting_method, holdings_count_target,
    distribution_frequency, securities_lending (yes/no), inception_date,
    prospectus_date. Mark is_critical on expense ratio, index name,
    weighting method, rebalance frequency.
  - replica_term_readings (PLATFORM): extraction readings per field with
    quote, verification, model, cost — mirroring note_term_readings.
  - replica_terms_versions (ORG, immutable once approved): per registry
    entry, version n, status (draft, approved, superseded, rejected),
    values jsonb with per-field origin (extracted | corrected | deliberate)
    and the reading id it came from, departures (list of deliberate changes
    with reason), maker, checker, approved_at. One approved version live per
    entry (partial unique index).
Apply the DDL the way dealroom2 did (a scripts/_apply_rep1.py run through
Doppler, synchronously), then VERIFY it landed with a follow-up query (columns, constraints, indexes, RLS enabled, every policy).
Save it as docs/rep1_part1.sql. Grants for app_service as existing tables
have them.

=== TASK 3: INGEST HOLDINGS ===
services/replica_holdings.py + scripts/replica_ingest.py:
  - Resolve a ticker or CUSIP → global security (create via the 1a service
    if missing) → SEC ids (company_tickers_mf.json, cached) → holdings
    source.
  - Fetch the holdings file (issuer parser from 1b, else N-PORT), store the
    raw bytes in R2 (gzipped, under replica/holdings/<ticker>/<as_of>/…),
    parse into a snapshot + rows. Same file SHA-256 again = no new snapshot.
  - Resolve each row's constituent through the 1a service; unresolved rows
    are queued (proposal pattern), never guessed.
  - Validation, recorded on the snapshot: weights sum to 1 ± 0.5% excluding
    cash; row count within ±20% of the previous snapshot; no duplicate
    constituent; as_of not older than the previous one. A failed check
    marks the snapshot 'needs_review', not live.
  - Diff against the previous snapshot: added, removed, weight changes over
    a threshold — stored for the UI.
  - CLI: --ticker QQQ [--dry-run] (dry run reports what it would write, with
    no writes); --all-active (every active registry entry).

=== TASK 4: EXTRACT TERMS ===
services/replica_terms.py:
  - Pull the fund's latest prospectus (485BPOS / 497K summary section) via
    the EDGAR client; store it as the source document; run the extraction
    ensemble from 1c over the replica field registry; write readings with
    source quotes and quote verification. No Claude models.
  - Build a DRAFT terms version from the readings (origin 'extracted'),
    flagging fields with no verified quote or with ensemble disagreement as
    "needs review".
  - Deterministic fields that need no model (ticker, issuer, SEC ids,
    holdings count from the latest snapshot) are filled from data with
    origin 'extracted' and source 'data'.

=== TASK 5: DAILY HOLDINGS REFRESH ===
On the same scheduler path bulkfetchui3 used (1f): every weekday after
11:00 UTC, ingest holdings for every active registry entry (once per day,
idempotent). Failures recorded per fund; a fund whose holdings changed by
more than the diff threshold opens a review task (Task 1e mechanism). Setting
REPLICA_HOLDINGS_AUTO_REFRESH (default on). Report any operator step.

=== TASK 6: API AND PERMISSIONS ===
New permissions: view_replication, manage_replication, approve_replication
(create them; grant to super admin implicitly via Super Admin first; grant
to org_admin by default; no other role gets them by default). Routes under
/api/v1/replication/…, each with an explicit gate and an AUTHZ1_ROUTES
entry:
  - GET registry list (view) — envelope with permissions + vocabularies.
  - POST registry entry by ticker/CUSIP (manage) — creates the entry, runs
    ingestion and extraction (synchronously with a time cap, or queued with
    status — choose and say why).
  - GET one entry: security, latest snapshot summary, holdings rows
    (paged, sortable), diff vs previous, terms versions, current draft with
    per-field readings and quotes (view).
  - POST correction to a field of the DRAFT (manage): writes
    document_field_corrections (target_type 'replica_terms') and updates the
    draft value with origin 'corrected'.
  - POST deliberate change to a field (manage): requires a reason; creates
    or updates the draft with origin 'deliberate' and adds it to
    departures.
  - POST approve / reject a draft (approve_replication): the approver must
    not be the person who made any correction or change in that draft
    (maker-checker); approving supersedes the previous approved version.
    Approved versions are never edited.
  - POST refresh holdings now (manage).
Every human action also creates/closes its workflow task or event (1e) and
writes audit_log.

=== TASK 7: SCREENS ===
Portfolio group → new tab "Replication" (menuVisibility.mjs, gated on
view_replication), light/cream:
  - Registry list: display name ("Holdings of QQQ"), class, issuer, latest
    holdings as-of, constituents (resolved / total), terms status (draft
    needs review / approved vN), last refreshed. Add button (manage only).
  - Add security: ticker/CUSIP search → confirm the resolved fund → run.
  - Entry page, three tabs:
      Terms — each field: value, origin chip (extracted / corrected /
      deliberate), source quote with a link to the document, confidence,
      "needs review" marker; actions Correct (wrong read) and Change
      (deliberate, reason required) shown only with can_write; departures
      listed at the top in plain words ("Departs from the fund: …");
      Approve / Reject only with approve_replication and hidden for the
      draft's maker; version history.
      Holdings — snapshot picker (as-of), sortable table (name, ticker,
      CUSIP, weight, shares, resolved?), unresolved rows highlighted, diff
      vs previous snapshot (added / removed / weight moves).
      Sources — documents and files used, with dates and hashes.
  - Copy uses "Holdings of <ticker>" and the disclosure line "Replicates the
    published holdings of <ticker>; not affiliated with or endorsed by the
    fund's sponsor or index provider."

=== TASK 8: REAL PROOF ===
  - Ingestion (mocked HTTP, fixture files shaped exactly like the 1b
    samples): a QQQ-shaped file creates one snapshot with the right row
    count and weights; the same bytes again create nothing; a changed file
    creates a second snapshot and a correct diff; a file whose weights sum
    to 0.9 is 'needs_review', not live; unresolved constituents are queued
    and never guessed; N-PORT fallback parses a fixture N-PORT-P.
  - Dry run writes nothing (before/after counts equal).
  - Extraction (mocked ensemble): readings written with quotes; a reading
    whose quote is not in the document is unverified and its field marked
    needs review; no Claude model id appears in any call (assert the
    configured models).
  - Correction vs change: a correction writes document_field_corrections
    and origin 'corrected' and NO departure; a change requires a reason,
    writes origin 'deliberate' and a departure; neither edits an approved
    version (409), both edit only the draft.
  - Maker-checker: the maker's approve is refused (403/409 naming the
    rule) on the identical request a different approver succeeds with;
    approval supersedes the prior version; one approved version live
    (re-read).
  - Org isolation: org B cannot see or edit org A's registry entry or terms
    versions (404), proven both directions under app_service with
    rolbypassrls = false; platform snapshots are readable by both.
  - Gates: member and staff without the permissions get 403 naming the
    permission on the identical requests org_admin succeeds with; envelope
    can_write false → node test renders no Correct / Change / Approve /
    Add controls.
  - Workflow: each human action created/closed its task or event (re-read).
  - Daily refresh: once per weekday after 11:00 UTC, idempotent, off switch,
    failure recorded (mocked).
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_underlyingresolution.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 1200 s timeout.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md (Replication Studio — rep1), a new
docs/REPLICATION_STUDIO.md (what exists: tables, services, sources, the
correction/change split, maker-checker, how to add an issuer parser, the
CLI), and add verify_rep1.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep1.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. Mock R2,
every HTTP request and every model call. Wrap every section so an exception
is a [FAIL]; teardown at start and end in FK child-before-parent order from
pg_constraint (audit_log, assistant_activities, agent_proposals, workflow
rows and member_todos by fixture id before fixture users), then re-read every
touched table for its exact pre-test count. Fixture users created explicitly
with the auth0_sub the mocked token carries, is_test = true. Read every
UNIQUE (including partial unique indexes), CHECK and FK constraint on the
touched tables from the catalog before seeding and seed to satisfy them.
Fixture funds and constituents use tickers/CUSIPs that cannot exist (a
reserved test prefix) so no real security-master row is matched or changed.

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
  - Never touch real securities_global rows: count + checksum of real rows
    in securities_global and securities_global_identifiers before and
    after.

Assertions:
  [Y] Task 1 findings 1a–1f reported with files, URLs, formats, terms-of-use
      notes and the chosen workflow mechanism
  [Y] Schema landed: tables, constraints, partial unique indexes, RLS
      enabled with a policy for every operation each table takes
  [Y] Ingestion: snapshot + rows correct; same bytes = no new snapshot;
      diff correct; bad weights → needs_review; unresolved queued; N-PORT
      fallback parses; dry run writes nothing
  [Y] Extraction: readings with verified quotes; unverified → needs review;
      no Claude model used
  [Y] Correction vs deliberate change kept apart (corrections table vs
      departures); approved versions immutable
  [Y] Maker-checker on approval, identical request; one approved version
      live
  [Y] Org isolation both directions (rolbypassrls = false); platform
      snapshots shared
  [Y] Permission gates both ways on identical requests; node tests show no
      write controls without can_write / approve_replication
  [Y] Every human action logged as a workflow task/event and in audit_log
  [Y] Daily refresh once per weekday, idempotent, off switch, failure
      recorded
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no
      existing gate changed
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script,
      not during the sprint)
  [Y] Regression: verify_underlyingresolution.py ONCE, flat, after
      teardown, VERIFY_NESTED=1; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real security-master rows unchanged
