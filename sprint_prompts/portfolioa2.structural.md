PORTFOLIO A2 — TENANT ASSETS, POSITIONS, TRANSACTIONS. 5 tasks +
verification. Design: docs/PORTFOLIO_REPORTING_DESIGN_V6.md
(commit it to the repo in this sprint if it is not already
there — A1's own sprint reported it missing).

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

CONTEXT — Part 1 SQL ALREADY APPLIED directly via Supabase MCP.
Do NOT re-create any of this — verify it matches, then build
against it:

  Six new portfolio.* tables, RLS enabled, ONE org-isolation
  policy each (org_id = current_org_id OR is_super_admin — the
  direct-scoped shape, NOT A1's four-policy global-read shape):
    assets, asset_identifiers, positions, valuations,
    transactions, external_references

  Three changes to public:
    entity_type enum gained 'account'
    transaction_types gained a nullable market column
      (CHECK IN 'public','private','both') — EXISTING 16 ROWS
      HAVE market = NULL, not yet backfilled
    fx_rates gained rate_type (NOT NULL DEFAULT 'spot') and
      bitemporal columns (valid_from/valid_to/system_from/
      system_to)

CRITICAL, from A1's own findings (docs/PROJECT_STATUS.md §7h):
  * portfolio is NOT on search_path. app_service inherits
    "$user", public. EVERY query must schema-qualify
    (portfolio.assets, not assets) or it raises UndefinedTableError
    under the real production role while appearing to work in an
    interactive session with a different search_path. A1's verify
    script enforces this by AST-parsing the module for bare
    FROM/INTO/UPDATE — replicate that check here.
  * RLS GUCs (app.current_org_id, app.is_super_admin) are
    connection-level SET LOCAL, not schema-scoped — this already
    works correctly across schemas, confirmed in A1.
  * A CHECK constraint can silently never fire if a BEFORE trigger
    with its own RAISE intercepts the same condition first. Where
    this sprint adds any trigger alongside a CHECK constraint
    covering related ground, test them SEPARATELY — drive one
    path through the trigger, a distinct path through the
    constraint directly — not one test that only ever reaches
    whichever fires first.

DELIBERATELY OUT OF SCOPE — DO NOT BUILD:
  * Any real ingestion (Altruist, reporting-tool import, Chancery
    consumption) — that is Phase B
  * Source precedence resolution / superseded_by_source LOGIC
    (the column exists; populating it via real precedence rules
    is Phase B)
  * The S21 sunburst rollup into entity_holdings — Phase C
  * SPV derivation view — Phase D
  * Cash modeling, corporate actions, commitments, UDFs — later
    phases
  * Any UI

STANDING RULES: org_id never from request body; Decimal for any
monetary value crossing an API boundary; no interactive prompts;
bitemporal columns are valid_from/valid_to/system_from/system_to.

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Confirm the six new tables, their columns, CHECK
      constraints, and single RLS policy each, against the
      snapshot above. Report any drift.
  1b. Confirm the three public changes landed as described.
      Report the actual current market values for all 16
      existing transaction_types rows (expected: all NULL).
  1c. Read the REAL current services/securities_global.py from
      A1 — confirm its schema-qualification pattern and its
      Super-Admin-gate pattern. This sprint's service layer must
      follow the SAME conventions, not invent new ones.
  1d. Confirm whether any existing code already queries `entities`
      assuming entity_type is one of the pre-'account' values
      (e.g. a hardcoded IN-list for CRM display) that would now
      incorrectly include accounts. Report every call site found.

=== TASK 2: BACKFILL transaction_types.market ===
Classify the 16 existing rows: the 4 ILPA capital-call types + 5
distribution types + adjustment/fee_expense/valuation types that
are private-markets-specific -> 'private'; buy/sell/dividend/
interest -> 'public'; anything genuinely applicable to both ->
'both'. Use judgment; report the classification given for every
row.

=== TASK 3: SERVICE LAYER ===
Build apps/api/services/portfolio_assets.py, following A1's
established conventions exactly (Task 1c):
  * create_asset / add_identifier — org-scoped, standard
    org-write permission, NOT super-admin-gated (this is tenant
    data, unlike A1's global layer)
  * create_position(conn, org_id, owner_entity_id, asset_id,
    as_of_date, ...) -> the edge. Validate ownership_basis
    against whichever of quantity/ownership_pct/market_value was
    actually supplied — reject a call that supplies quantity for
    a 'value'-basis position or vice versa.
  * record_transaction(conn, org_id, position_id,
    transaction_type_code, ...) -> validates transaction_type_code
    exists AND that its market (Task 2) is compatible with the
    position's asset_type where that distinction is meaningful
    (do not over-engineer this — a reasonable, real check, not an
    exhaustive rules engine).
  * record_valuation(conn, org_id, asset_id, ..., status,
    supersedes_valuation_id) -> if supersedes_valuation_id is
    provided, do NOT update the prior row — insert new, verify
    the prior row is untouched.
  * A helper resolving an asset's CURRENT market_value: latest
    valuation by valuation_date where status is the highest-
    priority available (audited > final > preliminary >
    estimated > restated-superseded), or NULL with a clear reason
    if none exists — never silently return zero.

=== TASK 4: THE ACCOUNT NODE ===
Confirm (or build, if Task 1d found gaps) that a real entity with
entity_type='account' can be created via the existing entity-
creation path, and that Task 1d's identified call sites correctly
EXCLUDE accounts from CRM-facing entity lists (EntityPicker, dupe-
check, search) while still allowing accounts to appear as
owner_entity_id on a position.

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md in the same commit: record A2 as
built, what it contains, that Phase B (ingestion) is next, and
any Task 1 finding worth keeping. If
docs/PORTFOLIO_REPORTING_DESIGN_V6.md was missing from the repo,
confirm it now exists.

=== VERIFICATION: apps/api/scripts/verify_portfolioa2.py ===
Pass/fail only. No interactive prompts. Idempotent. Teardown at
start AND end — CHECK FOR EXISTING PRODUCTION-ROOTED DATA FIRST
(A1's sprint found live corpus rows already present; apply the
same before/after-count discipline rather than an unconditional
TRUNCATE if any of these six tables are non-empty at start).

  [ ] Report Task 1's four findings explicitly, including the
      real market classification for all 16 transaction_types
      rows
  [ ] Schema-qualification enforced: AST-check (or equivalent)
      proves no bare table reference in the new service module
  [ ] A real entity with entity_type='account' can be created
  [ ] That account is CORRECTLY ABSENT from at least one real
      CRM-facing list/search call, and CORRECTLY PRESENT as a
      valid owner_entity_id on a position — both proven, not one
      assumed from the other
  [ ] THREE ownership bases each round-trip correctly: a units-
      basis position with quantity, a percent-basis position with
      ownership_pct, a value-basis position with only market_value
      — and each REJECTS being created with the wrong field
      populated instead
  [ ] A position can reference an owner_entity_id that is NOT an
      account (e.g. a trust) with NO account node in between —
      proves accounts are genuinely optional, not required
  [ ] VALUATION HISTORY: record two valuations for one asset,
      second with supersedes_valuation_id pointing at the first;
      assert the FIRST ROW IS UNCHANGED (query it directly,
      compare to its original values) and both remain independently
      queryable
  [ ] The current-value resolver picks 'audited' over 'estimated'
      when both exist for the same asset and date range; returns
      NULL with a clear reason when neither exists — not zero
  [ ] Cross-org isolation on at least TWO of the six new tables,
      tested against the real app_service connection
  [ ] RLS policy count confirmed as ONE per table (not A1's four)
      — a wrong copy-paste of A1's global-read policy onto a
      tenant table would be a real, silent cross-org read; assert
      this explicitly
  [ ] Teardown: exact before/after count match on all six tables
      (per the note above — do not assume they start empty)
