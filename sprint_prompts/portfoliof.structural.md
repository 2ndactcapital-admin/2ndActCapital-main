PORTFOLIO PHASE F — CORPORATE ACTIONS. 6 tasks + verification.
Design: docs/PORTFOLIO_REPORTING_DESIGN_V6.md §10. Builds on A1,
A2, B, C, D, E — all merged.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

DESIGN CORRECTION FROM V6 §10, APPLIED IN PART 1 SQL ALREADY:
  §10's original sketch keyed corporate_actions to asset_id
  (tenant-scoped). This was WRONG for the same reason A1 keeps
  prices/identifiers global: a split is one real-world event
  about one security, not a fact that should be recorded once
  per tenant that happens to hold it. CORRECTED:
    portfolio.securities_global_corporate_actions — GLOBAL scope
    (global_security_id, resulting_global_security_id nullable,
    action_type, ex_date, record_date, pay_date, terms jsonb,
    source_system, applied_at, bitemporal). RLS: global-read,
    Super-Admin-write — identical shape to A1's other global
    tables (4 policies).
  ALSO FIXED: portfolio.transactions.corporate_action_id existed
  as a BARE uuid with no FK since A2 (this table did not exist
  yet when A2 shipped) — it now has a real FK to the table above.
  transactions gained is_corporate_action_adjustment (boolean,
  default false) so an adjustment transaction is never
  misreadable as an ordinary trade.
  RECORDING and APPLYING are therefore two separate steps:
  recording a corporate action is GLOBAL and Super-Admin-gated
  (one fact, once). APPLYING it to a given org's own assets and
  positions is tenant-scoped and is what this sprint's real work
  is — every org holding the affected security applies the SAME
  recorded event to its own rows independently.

  CONSUMED, NOT COMPUTED, per the design: this sprint records and
  applies published corporate-action terms. It does NOT derive a
  split ratio or a spinoff cost-basis allocation independently —
  those numbers come from the source (a custodian feed, a market-
  data provider) as already-published terms.

STANDING RULES: org_id never from request body; Decimal for any
monetary or ratio value; no interactive prompts; schema-qualify
every portfolio.* reference (portfolio is NOT on search_path —
confirmed in every prior phase, do not rediscover this).

DELIBERATELY OUT OF SCOPE — DO NOT BUILD:
  * UDFs (Phase G)
  * The reconciliation engine, performance calculations, or
    cross-client analysis (Phase H — designed for, not built)
  * Deriving a split ratio or spinoff allocation independently —
    terms are always supplied, never computed
  * Merger, tender, and delisting handling beyond recording the
    action_type — split/reverse_split (primary proof) and
    spinoff (secondary proof, creating a resulting position) are
    this sprint's real scope; the other action_types exist in
    the CHECK constraint for future use but need no application
    logic here

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Confirm the corporate-actions table and the transactions
      FK/column exactly as described above, live.
  1b. Re-read A1's real create_security/add_price/add_relationship
      pattern (Super-Admin-gated, global) — this sprint's
      record_corporate_action must compose the SAME conventions,
      not invent new ones.
  1c. Re-read A2's real create_position/record_transaction — this
      sprint's apply function must use these to WRITE the
      adjustment, not bypass them with a direct UPDATE. Confirm
      whether record_transaction currently accepts arbitrary
      transaction_type_code values that could represent "this
      position's quantity changed due to a corporate action" —
      report the real, current transaction_types vocabulary
      candidates (adjustment already exists per Phase B/E's own
      findings) and which one is the honest fit.
  1d. Confirm the real, current mechanism for finding EVERY
      tenant asset tied to a given global_security_id (A2's
      assets.global_security_id FK) — this is how apply finds
      which orgs and which of their own assets are affected.

=== TASK 2: RECORD — global, Super-Admin-gated ===
Build apps/api/services/portfolio_corporate_actions.py:
  record_corporate_action(conn, *, global_security_id,
  resulting_global_security_id=None, action_type, ex_date,
  terms, ...) -> Super-Admin-gated per Task 1b's pattern. terms
  is the published data (e.g. {"ratio": "2:1"} for a split,
  {"cash_in_lieu_per_share": "0.00", "distribution_ratio":
  "1:4"} for a spinoff) — store as supplied, do not validate its
  internal shape beyond it being present and being valid JSON.

=== TASK 3: APPLY — split / reverse split, per org ===
apply_split(conn, org_id, corporate_action_id) ->
  For every CURRENT position in this org on any asset tied
  (via Task 1d) to the action's global_security_id: rewrite
  quantity (multiplied by the ratio) and unit cost_basis (divided
  by the ratio, so total cost basis is unchanged) — using A2's
  REAL create_position/record_transaction functions, not a
  direct UPDATE. Record a transaction with
  is_corporate_action_adjustment=true and
  corporate_action_id set, using Task 1c's identified honest
  transaction_type_code. This transaction must NOT register as a
  gain or a trade — prove this explicitly (Task's verify
  assertions cover this).
  MUST BE IDEMPOTENT: applying the SAME corporate_action_id to
  the SAME org twice must not double-adjust — check for an
  existing adjustment transaction with that corporate_action_id
  for that org before applying again.

=== TASK 4: APPLY — spinoff, creating a resulting position ===
apply_spinoff(conn, org_id, corporate_action_id) ->
  For every affected position: adjust the ORIGINAL asset's
  position per the terms (a spinoff typically also revises cost
  basis on the original per a published allocation ratio), AND
  create a NEW position on the resulting_global_security_id
  (creating the corresponding tenant asset first if this org
  does not already have one referencing that global security —
  reuse A2's create_asset). Both writes happen atomically — an
  org must never end up with only one side of a spinoff applied.
  Same idempotency requirement as Task 3.

=== TASK 5: A REAL ADJUSTMENT vs. A REAL TRADE, DISTINGUISHED ===
Prove a position's transaction history correctly distinguishes a
corporate-action adjustment from an ordinary buy/sell — a report
or ledger reading transactions must be able to exclude
adjustments from realized-gain calculations by filtering on
is_corporate_action_adjustment, without needing to know the
corporate-action machinery exists at all.

=== TASK 6: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md in the same commit: record Phase F
as built, the §10 global-vs-tenant correction and why it was
necessary, and that Phase G (UDFs) is next.

=== VERIFICATION: apps/api/scripts/verify_portfoliof.py ===
Pass/fail only. No interactive prompts. Idempotent. Teardown at
start AND end — exact before/after count match on every table
touched, not an unconditional TRUNCATE.

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] A corporate action can be recorded globally by a Super-
      Admin context and is REJECTED for a non-super-admin caller
  [Y] SPLIT: a real position with quantity=100 and cost_basis=
      $5,000 subject to a real 2:1 split correctly becomes
      quantity=200, cost_basis STILL $5,000 (unit cost halved,
      total cost basis unchanged) — exact Decimal assertions
  [Y] SPLIT: the recorded transaction carries
      is_corporate_action_adjustment=true and the real
      corporate_action_id — and a transaction-history query
      filtering WHERE is_corporate_action_adjustment=false
      correctly EXCLUDES it
  [Y] SPLIT IDEMPOTENCY: applying the same split to the same org
      twice leaves quantity/cost_basis at their POST-split values,
      not double-adjusted — assert this explicitly, not just "no
      error"
  [Y] SPINOFF: an original position is adjusted per terms AND a
      NEW position on the resulting security exists for the SAME
      owner_entity_id, in the SAME atomic operation
  [Y] SPINOFF: if a resulting tenant asset did not already exist
      for this org, one was created correctly linked to the
      resulting_global_security_id
  [Y] A DIFFERENT org holding the SAME global security is
      COMPLETELY UNAFFECTED by another org's apply call — real
      cross-org proof, not inferred from RLS alone
  [Y] Applying a corporate action to an org that holds NONE of
      the affected security does nothing and reports zero
      positions affected, cleanly — not an error
  [Y] Cross-org isolation on the apply functions and the
      recorded action's global visibility, tested against the
      real app_service connection
  [Y] Teardown: exact before/after count match on every table
      touched, including the new global table
