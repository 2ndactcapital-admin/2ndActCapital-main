PORTFOLIO A1 — GLOBAL SECURITY LAYER. 4 tasks + verification.
Schema and services only. NO ingestion, NO UI, NO tenant-scoped
tables. Design: docs/PORTFOLIO_REPORTING_DESIGN_V6.md.

WHY THIS EXISTS: this is the global master layer both the
portfolio track and the structured-investments corpus build on.
A1 unblocks the corpus track; A2 (tenant assets, positions,
valuations, transactions) follows separately and is NOT in scope.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

CONTEXT — Part 1 SQL ALREADY APPLIED directly via Supabase MCP.
Schema `portfolio` exists with four tables, RLS enabled, four
policies each (global-read / super-admin insert-update-delete,
matching the public.permissions shape):
  securities_global               -- no org_id, deliberately global
  securities_global_identifiers
  securities_global_prices
  securities_global_relationships
Do NOT re-create these. Verify they match the snapshot, then
build against them.

DELIBERATELY OUT OF SCOPE — DO NOT BUILD:
  * portfolio.assets, positions, valuations, transactions,
    external_references  (that is A2)
  * securities_global_note_terms or any extension table
    (that is step 4, SI-owned; A1 need only not preclude it)
  * `account` in the entity_type enum (A2)
  * `market` on transaction_types (A2)
  * rate_type / bitemporal on fx_rates (A2)
  * Any ingestion, EDGAR fetching, or price feed
  * Any UI

STANDING RULES: no interactive prompts; Decimal for any numeric
money handling; bitemporal columns are valid_from / valid_to /
system_from / system_to, matching entity_relationships.

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Confirm the four portfolio.* tables exist with the columns
      and CHECK constraints described above, and that each has
      RLS enabled with exactly 4 policies. Report the actual
      policy names.
  1b. Confirm services/database.py sets app.current_org_id and
      app.is_super_admin as CONNECTION-LEVEL GUCs (not schema-
      scoped). This is what makes RLS work in a non-public
      schema. Report the real mechanism.
  1c. Confirm whether the app's DB connection has a search_path
      that includes `portfolio`, or whether queries must
      schema-qualify. Report which, and follow it consistently.
  1d. Report whether app_service can SELECT from the new tables
      and is correctly BLOCKED from INSERT without super-admin
      context. Test both, live.

=== TASK 2: SERVICE LAYER ===
Build apps/api/services/securities_global.py:
  * get_by_identifier(conn, id_type, id_value) -> resolves an
    identifier to a security, FOLLOWING THE MERGE CHAIN (§3.6).
    Use the materialized canonical_id column; do NOT walk
    merged_into_id row by row. At corpus scale that is an N+1.
  * create_security / add_identifier / add_price /
    add_relationship — all requiring super-admin context.
  * resolve_scoreability(conn, global_security_id) -> derived,
    NOT stored: a security is scoreable when every one of its
    relationships has link_state='resolved' AND every target
    carries price_coverage='has_series'. Return the reason when
    it is not, naming the specific unresolved or uncovered
    underlying — an honest gap is more useful than a silent
    exclusion.
  * A helper that maintains canonical_id when merged_into_id is
    set, so reads stay cheap.

=== TASK 3: THE UNDERLYINGS-ONLY PRICING RULE ===
Enforce in code, not by convention: add_price MUST REJECT an
attempt to write a price for a security whose security_type is
'structured_note'. A note's secondary prices are sporadic TRACE
prints, not a daily series; the natural implementation ("for each
security, fetch prices") is wrong here and must fail loudly
rather than silently create 250k useless rows.
Raise a clear, specific error. Do not log-and-continue.

=== TASK 4: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md in the same commit: record that
Portfolio A1 is built, what it contains, that A2 is not, and any
Task 1 finding worth keeping (especially 1c, the search_path
answer — A2 and the SI track both need it).

=== VERIFICATION: apps/api/scripts/verify_portfolioa1.py ===
Pass/fail only. No interactive prompts. Idempotent. Teardown at
start AND end. Real database, real rows.

  [ ] Report Task 1's four findings explicitly
  [ ] All four portfolio.* tables exist, RLS enabled, 4 policies
      each — query pg_class/pg_policy directly
  [ ] GLOBAL READ: a NON-super-admin connection (real
      app_service, org context set) CAN read securities_global
  [ ] SUPER-ADMIN WRITE GATE: that same non-super-admin
      connection CANNOT insert — assert the write is actually
      rejected, not that it "didn't error"
  [ ] A super-admin-context connection CAN insert
  [ ] MERGE CHAIN: create A, create B, merge B into A, then
      resolve B's identifier and assert it returns A. Assert the
      resolution does NOT require a row-by-row walk (use
      canonical_id)
  [ ] NULLABLE TARGET: an unresolved relationship inserts
      successfully with to_global_security_id NULL and
      raw_underlying_text populated — this is the case v5 got
      wrong and must be proven
  [ ] CHECK CONSTRAINT: a relationship with link_state='resolved'
      and NULL to_global_security_id is REJECTED
  [ ] PRICING RULE: add_price against a security_type=
      'structured_note' raises a clear error; the same call
      against an 'index' or 'equity' succeeds
  [ ] SCOREABILITY: a note with one resolved underlying carrying
      price_coverage='has_series' is scoreable; the same note
      with an unresolved edge is NOT, and the returned reason
      names the specific raw_underlying_text
  [ ] Teardown leaves zero rows in all four tables
