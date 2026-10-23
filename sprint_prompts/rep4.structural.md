REP4 — Replication Studio, sprint 4 of 15: build a model from a fund —
"Holdings of <TICKER>" models built from rep1's approved terms and live
holdings into rep3's registry, the five-question viability check, the
fund-versus-replica cost comparison, the registry "join the existing model"
offer, and holdings/terms monitoring that drafts new model versions for
review. No accounts, proposals, tax engine or trades yet. 9 tasks +
verification.

Context: spec "Replication Studio — ETF & Fund-to-SMA Spec" (Joe,
2026-10-22): sections "What can be replicated" (viability tree: 1 rules-based
and public? 2 rebuildable with stocks or listed options? 3 account big enough
at the target tracking error? 4 custodian allows every leg? 5 beats the fund
after fees and tax?), "Workflow" steps 2, 5, 6, "Portfolio construction",
"Fees". Decisions (Joe): every fund-built model is named "Holdings of
<TICKER>" (the prefix is reserved to source fund_replica in rep3); the
registry offers an EXISTING model first ("run in the same fund"); personal
settings live on the account (rep5), so a new model is built only when the
weights or construction differ; every human action is a human task; custody
cliff — nothing here trades. Honesty rule from the spec: a viability answer
that needs data we do not have says "unknown — needs <data>" and never
guesses; the tax benefit is estimated by the tax engine (rep6), not here.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (main @ 34b3691, merge rep3; Task 1
confirms exact signatures):
- rep1: services/replica_terms.py (create_entry, load_entry, terms versions,
  display_name_for(ticker) → "Holdings of <TICKER>", disclosure_for(ticker),
  ensure_approval_task — now on human tasks after rep3),
  services/replica_holdings.py (latest_snapshot(conn, fund_id, source_key,
  live_only), snapshot_rows, compute_diff, validate, ingest, choose_source),
  services/replica_schedule.py (weekday holdings refresh after 11:00 UTC from
  workflow_scheduler_tick.py). Tables portfolio.replica_securities,
  replica_terms_versions (term_values jsonb with per-field origin;
  departures), replica_holdings_snapshots / rows (weight fraction, shares,
  asset_type equity|cash|future|other, resolution_state, constituent global
  security), replica_term_readings. Terms fields include expense_ratio_pct
  (gross / net), index_name (disclosure only), weighting_method,
  rebalance_frequency, reconstitution_schedule, holdings_count_target,
  distribution_frequency, securities_lending.
- rep2: services/human_tasks.py — open_task(pool, *, org_id, kind,
  subject_type, subject_id, maker_id, eligible_permission, title, payload,
  action_path, actor_kind) [+ system_open for review kinds], complete_task,
  cancel_task, list_tasks, get_task; kinds react via on_complete callbacks.
  services/custodian_capabilities.py — capability(custodian_code, key) with
  'unknown' = NOT available; Altruist: all 13 capabilities 'unknown'.
  services/execution.py (instructions), custody trade-adapter map.
- rep3: services/models.py — create_model(conn, *, org_id, actor_id, name,
  source, …), check_name(name, source), new_draft, live_approved,
  open_version, positions_of, look_through, _create_version (origin,
  maker_ids), set_sleeves, update_model (default_fee_schedule_id validated
  APPROVED + same org), publish(EventRef); services/model_upload.py;
  versions draft → approved via human task kind model_version_approval
  (approve_models), 'model.version_approved' domain event with the diff;
  review kind for system-opened drafts (maker = system import); approved
  versions immutable; unresolved positions block approval; models.source
  includes fund_replica and replica_security_id.
- Fee module: fee_schedules (APPROVED rows never mutated) with tiers;
  services/fee_calc.py apply_tiers(...) computes a tiered fee — reuse it for
  the illustrative annual fee; never write a bill.
- Prices: portfolio.securities_global_prices (global_security_id, price_date,
  price, price_type, source). There is NO licensed price feed yet (FMP not
  contracted); Task 1 reports what prices exist for constituents.
- authz1 rules, permission envelope pattern, org_id only from the session,
  extra='forbid'. Permissions to use: view_replication / manage_models /
  approve_models (no new permissions unless Task 1 shows a gap).
- Regression policy: this sprint changes rep3's shared model services →
  re-run verify_rep3.py ONCE. Scripts print 'TOTAL: N PASS, M FAIL'; accept
  'RESULT:'.
- WSL memory on the operator's laptop is limited: the sprint must NOT run npm
  run build or any verify script.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one, never poll in a loop, never start a
script in the background; run every command synchronously in the foreground
and read its output directly. Write the verify script but DO NOT RUN IT, and
do not run npm run build. No network calls (mock any HTTP). Never print a
secret. Column names from the live DB. Bi-temporal rule 3 for every new
table; RLS policies for EVERY operation each new table will take. Decimal
for every weight, price, fee and cost (never float). Never guess: a value
that needs missing data is reported as unknown with what is missing. Labels
and vocabularies from the API envelope. Light/cream UI with the existing
tokens. Commit ALL sprint files (new and modified, excluding
sprint_prompts/logs) on the current branch at the end.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Exact signatures in services/models.py, replica_terms.py,
      replica_holdings.py, human_tasks.py and custodian_capabilities.py that
      this sprint calls; how a system-opened review draft is created in rep3
      (the subscription / composite path) so holdings monitoring reuses it.
  1b. What prices exist in securities_global_prices for constituents of any
      live snapshot (counts, sources, latest dates), and whether holdings
      files carry market value or price (SSGA: weight + shares; N-PORT:
      valUSD + balance). State exactly which viability numbers can be
      computed today and which must say "unknown — needs prices".
  1c. How rep1's daily refresh signals a changed snapshot (event? return
      value? review todo?) and the cleanest hook for "a new live snapshot of
      fund F exists" → model monitoring.
  1d. How to compute an illustrative annual strategy fee for an account
      size from a model's default fee schedule with fee_calc.apply_tiers,
      without creating fee runs or invoices.
  1e. Whether any live replica entries / models exist in real data (counts
      only) — the verify must not touch them.

=== TASK 2: SCHEMA ===
Design from Task 1 (names indicative; org-scoped unless stated):
  - replica_model_specs: the construction recipe of a fund-built model —
    model_id, replica_security_id, terms_version_id it was built from,
    holdings snapshot id, custodian_code it is built for, method
    (full | top_n), top_n (nullable), min_weight (positions under it are
    dropped and their weight redistributed pro rata — a DELIBERATE choice,
    recorded as a departure), cash_target, unresolved_policy (block |
    exclude_and_reweight — exclude is a recorded departure), rebalance
    frequency (from terms or a deliberate change), drift_threshold,
    turnover_review_threshold, created_by. Versioned with the model version
    (one spec per model version).
  - replica_registry_key: unique live (org, replica_security_id,
    custodian_code, method, top_n) → model_id — the "find or build" key.
  - replica_viability_reports: entry, custodian, account size, the five
    answers each with status (yes | no | unknown), reason, inputs and
    missing data; cost comparison lines; computed_at; immutable.
Apply with scripts/_apply_rep4.py (rep1–rep3 pattern) through Doppler,
synchronously; VERIFY it landed; save docs/rep4_part1.sql.

=== TASK 3: VIABILITY CHECK (services/replica_viability.py) ===
For a registry entry + custodian + illustrative account size (default from
an org setting, e.g. $250,000):
  Q1 rules-based and public: from instrument_class (index_etf /
     index_mutual_fund → yes; anything else → no with the spec's reason).
  Q2 rebuildable: from the live snapshot's asset types — share of weight in
     equity vs future / other; any future or other above a threshold → no
     (or "core only" note); N-PORT staleness shown (as-of date and age in
     days) and > 45 days flagged.
  Q3 account size: if every constituent has a price ≤ 5 days old, compute
     the smallest account that holds every position within the tracking
     target given whole shares (and fractional when the custodian's
     fractional_shares is 'yes'); otherwise "unknown — needs prices for N of
     M constituents". Never estimate prices.
  Q4 custodian legs: capabilities for the chosen custodian (equity trading
     API, fractional shares) — 'unknown' is not available; answer is "yes,
     via execution instructions" when trading API is not available (manual
     path), with the reason.
  Q5 cost: fund expense ratio (approved terms; missing → unknown) vs the
     model's strategy fee at the account size (default fee schedule via
     apply_tiers; none set → unknown) + estimated trading cost (turnover
     from the last N snapshot diffs × an org-setting spread in bps); the tax
     benefit line reads "estimated by the tax engine (rep6)". Answer: yes /
     no / unknown with the numbers.
Every answer carries its inputs and its missing data. Reports are stored
(immutable) and re-computable.

=== TASK 4: BUILD OR JOIN ===
  - Find: given entry + custodian + method (+ top_n), return the existing
    live model (registry key) with its live version, positions count and
    how many accounts use it (0 until rep5). The screen offers "Use this
    model" first.
  - Build: from the entry's APPROVED terms version and its latest LIVE
    holdings snapshot: positions = resolved equity rows (weights normalised
    to 1 − cash_target); full = every row; top_n = the N largest by weight,
    renormalised; min_weight drop + pro-rata redistribution; unresolved rows
    per unresolved_policy (block → the draft cannot be approved until
    resolved, as rep3 enforces; exclude_and_reweight → recorded departure).
    Creates (or reuses) the model "Holdings of <TICKER>" — variants suffix
    the method ("Holdings of QQQ — Top 50") — source fund_replica,
    replica_security_id, the spec, and a DRAFT version through rep3's
    services with origin 'fund_build'; approval is rep3's human task
    (approve_models; maker ≠ checker). The disclosure line from rep1 is
    stored on the model.
  - No draft without an approved terms version and a live snapshot (409
    naming which is missing).

=== TASK 5: MONITORING ===
Hooked per 1c:
  - New live snapshot of fund F: for every fund_replica model of F, build
    the would-be version from the new snapshot with the model's spec; if
    turnover vs the live version ≥ the spec's turnover_review_threshold (or
    any constituent added/removed), create a system-opened DRAFT version +
    review task (rep3 review kind) with the diff; below threshold → log
    "within tolerance", no draft. Idempotent per snapshot.
  - New approved terms version of F with a change in a critical field
    (expense ratio, weighting method, rebalance frequency, index name): a
    review task on each model of F naming the changed fields (no automatic
    version).
  - All monitoring results are logged as model events + domain events.

=== TASK 6: API AND PERMISSIONS ===
Routes under /api/v1/replication/… (entry-scoped) — viability (GET compute
/ list reports, view_replication), registry find (view_replication), build
(manage_models + view_replication), model spec read (view_models). Each
gated and in AUTHZ1_ROUTES; envelopes with permissions and vocabularies
(answer statuses, methods, unresolved policies, the five question labels).

=== TASK 7: SCREENS ===
On the Replication entry page add a "Model" tab:
  - Viability: the five questions as a vertical checklist (yes / no /
    unknown with plain-language reasons and what is missing), custodian and
    account-size pickers, "as of" stamp, N-PORT staleness warning.
  - Cost comparison table (fund vs replica; tax line "estimated in a later
    release"; every unknown shown as unknown).
  - Registry: "An approved model already exists: Holdings of QQQ (v3,
    built for altruist, full) — Use this model" first; otherwise Build
    model with method, top-N, min weight, cash target, unresolved policy,
    custodian, and a preview of positions vs the fund's holdings (weights,
    dropped rows, departures) before creating the draft.
  - Link to the model in the Models tab; monitoring events listed.

=== TASK 8: REAL PROOF ===
  - Build (fixture fund, approved terms, live snapshot): full method
    positions equal the snapshot's resolved equity weights normalised
    (exact Decimal); top_n renormalised exactly; min_weight redistribution
    exact and recorded as a departure; cash target honoured; name "Holdings
    of ZZ…" (and the variant suffix); source fund_replica; spec stored;
    draft created through rep3 (origin fund_build); approval by a checker,
    maker refused on the identical request.
  - Preconditions: no approved terms → 409; no live snapshot → 409; a
    needs_review snapshot is never used.
  - Unresolved: block → draft cannot be approved (rep3 rule); exclude →
    departure recorded and weights renormalised.
  - Registry: a second build with the same key returns the existing model
    (no duplicate); a different method or custodian builds a new one.
  - Viability: Q1 no for a non-index class; Q2 no with a futures-heavy
    snapshot; Q3 "unknown — needs prices" when any price is missing and an
    exact minimum when fixture prices exist (whole vs fractional); Q4 uses
    capabilities ('unknown' → manual path); Q5 numbers exact with apply_tiers
    and "unknown" when the fee schedule or expense ratio is missing; the tax
    line never shows a number; reports immutable.
  - Monitoring: a new snapshot over threshold → one system draft + review
    task with the exact diff; under threshold → logged, no draft; the same
    snapshot again → nothing new; a critical terms change → review task
    naming the fields.
  - Org isolation both directions (rolbypassrls = false); gates both ways on
    identical requests; node tests: no Build / Use controls without
    manage_models.
  - Routes: no existing gate changed; authz1_routes.py --check CURRENT.
  - Regression: verify_rep3.py ONCE, flat, after teardown, VERIFY_NESTED=1,
    1200 s timeout.

=== TASK 9: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md, docs/REPLICATION_STUDIO.md (build, registry key,
viability rules and their data needs, monitoring), and add verify_rep4.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_rep4.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. Mock R2
and every HTTP call. Wrap every section so an exception is a [FAIL];
teardown at start and end in FK child-before-parent order from pg_constraint,
deleting ONLY rows this run created, by fixture id (never walk the FK graph
from a broad parent); clear audit_log, domain_events, member_todos, workflow
rows created by this run before fixture users; reconnect once and re-count
if a connection drops; then re-read every touched table for its exact
pre-test count. Fixture users created explicitly with the auth0_sub the
mocked token carries, is_test = true, using the users-RLS pattern from
verify_rep1–3. Read every UNIQUE (including partial unique indexes), CHECK
and FK constraint on the touched tables from the catalog before seeding and
seed to satisfy them. Fixture funds/securities use a reserved prefix that
matches no real row (assert it); fixture prices only on fixture securities.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool (wrap any
    `a and b` that could return a list in bool(...)).
  - Never derive a unique value from a fixture-UUID slice.
  - RLS context on every read; app_service with rolbypassrls = false asserted
    for isolation checks; an empty list never passes a check.
  - Exclude this run's own fixtures from any "no leftovers" check; ignore
    fixture-org roles in any grant check.
  - Never spawn a background process. The regression subprocess gets
    VERIFY_NESTED=1, runs after teardown, 1200 s timeout; with
    VERIFY_NESTED=1 this script skips its regression section with one
    '[SKIP] nested run' line.
  - Every import at the top of the file.
  - Never touch real replica entries, models, prices, fee schedules or users:
    count + checksum before and after.

Assertions:
  [Y] Task 1 findings 1a–1e reported with signatures, price availability and
      the monitoring hook
  [Y] Schema landed: tables, constraints, partial uniques, RLS with a policy
      for every operation
  [Y] Build: exact weights for full / top_n / min_weight / cash; departures
      recorded; "Holdings of" name; draft via rep3; maker-checker on the
      identical request
  [Y] Preconditions and unresolved policies enforced
  [Y] Registry find-or-build: no duplicates; different key → new model
  [Y] Viability: each question's yes / no / unknown with reasons; unknown
      when data is missing (never guessed); exact cost numbers; no tax number
  [Y] Monitoring: threshold drafts + review tasks; idempotent; critical terms
      changes raise review tasks
  [Y] Org isolation both directions (rolbypassrls = false); gates both ways;
      node tests show no Build / Use controls without the flag
  [Y] Every new route gated and in AUTHZ1_ROUTES.json; --check CURRENT; no
      existing gate changed
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script,
      not during the sprint)
  [Y] Regression: verify_rep3.py ONCE, flat, after teardown,
      VERIFY_NESTED=1; 0 FAIL
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test
      count; real rows unchanged
