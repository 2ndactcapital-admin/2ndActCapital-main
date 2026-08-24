PORTFOLIO PHASE B — INGESTION + SOURCE PRECEDENCE. 6 tasks +
verification. Design: docs/PORTFOLIO_REPORTING_DESIGN_V6.md §1.1,
§8. Builds on A1 + A2, both merged.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue. The
exception is the Altruist gate in Task 3 — an honest STOP
condition, not a checkpoint to work around.

CONTEXT — Part 1 SQL ALREADY APPLIED directly:
  portfolio.external_references' UNIQUE constraint is now
  (org_id, source_system, external_id, record_type) — was
  missing org_id, a real cross-tenant collision bug found by
  A2's own verify script. Do not re-widen; it is already correct.

  fx_rates' UNIQUE constraint is now (base_ccy, quote_ccy,
  as_of_date, rate_type) — was missing rate_type, which made a
  spot/period_end pair for the same date impossible and blocked
  any Rule-3 FX supersede. Also already correct.

  Both fixes were found by A2's verify script as real, Phase-B-
  blocking gaps and applied as this sprint's prerequisite, not
  discovered fresh here.

STANDING RULES: org_id never from request body; Decimal for any
monetary value crossing an API boundary; no interactive prompts;
schema-qualify every portfolio.* reference (portfolio is NOT on
search_path — confirmed in A1 and A2, do not rediscover this,
just follow it); bitemporal columns are valid_from/valid_to/
system_from/system_to.

DELIBERATELY OUT OF SCOPE — DO NOT BUILD:
  * The S21 sunburst rollup into entity_holdings — Phase C
  * SPV derivation view — Phase D
  * Cash modeling, corporate actions, commitments, UDFs — later
    phases
  * Any UI beyond a minimal file-upload endpoint
  * Real-time or intraday anything — daily is the maximum
    frequency per the design

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Read the REAL current services/portfolio_assets.py (A2) —
      confirm its exact function signatures for create_asset,
      create_position, record_transaction, record_valuation.
      This sprint calls these, does not duplicate them.
  1b. Confirm whether any Excel/CSV parsing convention already
      exists in this codebase (Chancery's XLSX ingestion path is
      the likely candidate — openpyxl is already a dependency).
      Reuse it; do not add a second parsing approach.
  1c. Confirm the real, current org_settings key-naming
      convention (ai.model.*, ai.embedding.* are known examples)
      — this sprint needs a new org-configurable precedence
      ordering and must follow the SAME convention, not invent
      one.
  1d. Confirm the real current shape of at least one Altruist-
      related reference in the codebase (env vars, a stub
      service file, or documentation only) — report honestly
      whether ANY code already exists for this integration or
      whether it is genuinely greenfield.
Report all four findings before proceeding.

=== TASK 2: SOURCE PRECEDENCE — org-configurable, per §1.1 ===
Build the precedence-ordering mechanism as DATA, not code, per
Task 1c's real convention. A reasonable shape: an ordered list of
source_system values per org, defaulting to the design's stated
order (reporting_tool_* > altruist > spv_subscriptions > chancery
> manual) when an org has not configured its own.
Build resolve_precedence(conn, org_id, position_candidates) ->
given multiple position rows for the same (owner_entity_id,
asset_id, as_of_date) from different source_system values,
return which one wins per the org's real configured order, and
mark every losing row's superseded_by_source with the winning
source_system value. Do NOT delete losing rows — they remain
visible for reconciliation, per the design.

=== TASK 3: ALTRUIST — real, honest gate ===
  (a) Check for real Altruist API credentials in the environment.
      If present, attempt a real, minimal authenticated call
      (whatever their API's lightest real endpoint is — a health
      check or account-list call). 
  *** HONEST GATE *** If credentials are absent, or the attempted
  call fails (including "partner access not yet approved" style
  responses), STOP this task and report BLOCKED with the exact
  reason. Do NOT mock, simulate, or fabricate Altruist data. Do
  NOT let this block Tasks 2, 4, 5, or 6 — continue past it.
  (b) If genuinely unblocked: build the minimal real ingestion
      call, mapping Altruist's response shape into
      create_position + external_references rows (idempotent —
      re-running against the same external_id must not duplicate).

=== TASK 4: FILE-BASED INGESTION — reporting-tool import ===
This does NOT depend on any external credential and must be
built regardless of Task 3's outcome. A minimal endpoint accepting
an uploaded CSV or XLSX (per Task 1b's real parsing convention)
representing a client's reporting-tool export (Black Diamond,
Addepar, Orion, APX all export in broadly similar tabular shapes
— asset name/identifier, quantity or value, as_of date, at
minimum). Map each row into create_asset (or match an existing
asset via asset_identifiers) + create_position, with
source_system = 'reporting_tool_import' and a real
external_references row per position using the file's own
row-identifying data (or a stable hash of the row if no natural
ID exists) so re-uploading the same file is idempotent, not
duplicative.
Handle a malformed row gracefully — skip and report it, do not
crash the whole import on one bad row.

=== TASK 5: PROVE PRECEDENCE WITH TWO REAL SOURCES ===
Using Task 4's file import for TWO simulated sources (e.g. upload
a "reporting tool" file AND separately create a manual-entry
position for the SAME owner_entity_id + asset_id + as_of_date),
prove Task 2's resolve_precedence genuinely picks the correct
winner per the org's real configured order, and that the losing
row's superseded_by_source is set correctly and the row itself
still exists and is queryable.

=== TASK 6: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md in the same commit: record Phase B
as built, the real Altruist gate outcome (blocked or working, do
not overstate either way), that file-based reporting-tool import
works independently of Altruist, and that Phase C (the sunburst
rollup) is next.

=== VERIFICATION: apps/api/scripts/verify_portfoliob.py ===
Pass/fail only. No interactive prompts. Idempotent. Teardown at
start AND end — CHECK FOR EXISTING DATA FIRST, exact before/after
count match, not an unconditional TRUNCATE (per A1 and A2's
established discipline; these tables may hold real rows from a
different track by the time this runs).

IF TASK 3 IS BLOCKED: report [BLOCKED] for its assertions with
the exact reason, same pattern as the Textract/Voyage/SES gates
this session. Every other task's assertions still run normally
and must still reach 100%.

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] Report Task 3's real gate outcome explicitly — credentials
      present or absent, real call attempted, actual result
  [Y] A file-based import of a real, generated CSV/XLSX creates
      real assets and positions with source_system=
      'reporting_tool_import' and real external_references rows
  [Y] Re-uploading the IDENTICAL file does NOT create duplicate
      positions — the idempotency proof, not just "it didn't
      error"
  [Y] A malformed row in an otherwise-valid file is skipped and
      reported; the rest of the file still imports successfully
  [Y] PRECEDENCE: two real position candidates for the same
      (owner, asset, as_of_date) from different source_system
      values resolve to the correct winner per the org's real
      configured order — assert the EXACT winning source_system
  [Y] The LOSING row still exists, is queryable, and its
      superseded_by_source is set to the winning source — not
      deleted
  [Y] An org with NO custom precedence configured falls back to
      the design's stated default order — test this explicitly,
      do not just test a configured case
  [Y] Cross-org isolation: an org cannot see another org's
      positions or external_references, tested against the real
      app_service connection
  [Y] Teardown: exact before/after count match on every table
      touched, including the new precedence-configuration table
