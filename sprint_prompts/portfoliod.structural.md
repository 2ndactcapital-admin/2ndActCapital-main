PORTFOLIO PHASE D — SPV DERIVATION VIEW + CASH + DOCUMENT
DRILL-THROUGH. 6 tasks + verification. Design: docs/
PORTFOLIO_REPORTING_DESIGN_V6.md §8, §9. Builds on A1, A2, B, C
— all merged.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

NO PART 1 SQL WAS APPLIED for this phase, deliberately. Unlike
A1/A2/C, this phase's schema was NOT pre-specified because the
correct SPV-valuation join is a real discovery question, not a
known shape — writing it blind risked getting it wrong. Task 1
discovers; Task 2 builds the view based on what Task 1 actually
finds, not on an assumption.

STANDING RULES: org_id never from request body; Decimal for any
monetary value; no interactive prompts; schema-qualify every
portfolio.* reference (portfolio is NOT on search_path — do not
rediscover this, confirmed in A1/A2/B/C).

DELIBERATELY OUT OF SCOPE — DO NOT BUILD:
  * Corporate actions, commitments-table population, tax-doc
    tracking — later phases
  * UDFs
  * Any change to entity_holdings or the rollup itself (Phase C,
    already built) — this phase's job is making SPV interests
    and cash VISIBLE to positions/rollup, not changing either
  * Fixing the allocation_lens subtree double-count found by
    Phase C's own verify script — that is its own, separate,
    already-flagged follow-up

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Read the REAL current spv_subscriptions columns (already
      confirmed: id, org_id, spv_id, entity_id,
      member_investment_id, commitment_amount, funded_amount,
      ownership_pct, subscription_status, signed_at, valid_from,
      valid_to, created_by, created_at — NOTE: only valid_from/
      valid_to, no system_from/system_to visible in this list —
      confirm whether system-time tracking exists elsewhere for
      this table, e.g. a separate audit table, or whether
      valid_to IS NULL alone represents "current").
  1b. Find where an SPV interest's CURRENT MARKET VALUE actually
      lives. commitment_amount and funded_amount are not a NAV.
      Check the Sprint 22 GL (capital account balances), the
      spvs table itself, and member_investments. Report the REAL
      query path to a current, per-subscription value — do not
      assume one exists cleanly; report honestly if this requires
      joining across multiple tables or if no current-value
      concept exists yet for some subscription states.
  1c. Confirm the real conventions already established across
      Phases A2/B/C for creating an asset + position pair (which
      function does what, in which order) — the cash-as-asset
      pattern in this phase must follow the SAME pattern, not
      invent a parallel one.
  1d. Confirm document_record_links' real current schema and its
      existing valid record_type values (Chancery Phase 5/9) —
      confirm adding new values requires no migration (it should
      not, if genuinely polymorphic text) before assuming so.

=== TASK 2: THE SPV DERIVATION VIEW ===
Build a real Postgres VIEW (in the portfolio schema) projecting
CURRENT spv_subscriptions rows into position shape:
  authority = 'internal', source_system = 'spv_subscriptions',
  owner_entity_id = the subscription's entity_id,
  ownership_basis = 'percent' where ownership_pct is genuinely
  the measure, or 'value' if Task 1b found a real current value
  to use instead — use your judgment based on what Task 1b
  actually found, do not force one shape if the real data does
  not support it.
This is a VIEW, not a table — nothing is stored twice. It must
NOT appear as an editable row through portfolio_assets.py's
write functions; corrections go to spv_subscriptions itself,
which remains the book of record. Confirm this by NOT building
any update path against the view.
An asset row (portfolio.assets, internal_spv_id populated) must
exist per real SPV for the view to project against — build
whatever minimal, real asset-creation step is needed (one asset
per spv, not per subscription) so the view has something to join.

=== TASK 3: CASH AS AN ASSET — the existing convention ===
Per the design: cash is a position, not a special case. Build a
helper that creates (or finds, idempotently) a cash asset per
(org, currency_code) — asset_type='cash', ownership_basis=
'value' — and a real function to record a cash position/balance
against it, reusing Task 1c's real asset+position creation
pattern exactly. Bank balances use the identical model: a bank
account is entity_type='account' holding a cash position via the
same mechanism, no special case.

=== TASK 4: DOCUMENT DRILL-THROUGH ===
Add the new document_record_links record_type values per the
design: 'portfolio_position', 'portfolio_valuation',
'portfolio_transaction', 'portfolio_asset'. Build the minimal
real linking calls at the natural points (e.g. when a valuation
or transaction is created FROM a Chancery-extracted document,
per Phase B/Chancery's existing hooks) — do not build new UI;
Chancery Phase 9's DocumentsPanel already renders linked
documents for a given record.

=== TASK 5: PROVE THE VIEW MATCHES A REAL SPV ===
Using a real, seeded spv_subscriptions row (not a fabricated
shape), prove the derivation view produces a position that:
correctly identifies as authority='internal', correctly resolves
Task 1b's value, and correctly EXCLUDES a subscription whose
valid_to is set (no longer current).

=== TASK 6: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md in the same commit: record Phase D
as built, Task 1b's real finding on where SPV value lives
(this matters for anyone touching this later), and that Phase E
(Chancery-sourced alts/hard assets, commitments, tax-doc
tracking) is next.

=== VERIFICATION: apps/api/scripts/verify_portfoliod.py ===
Pass/fail only. No interactive prompts. Idempotent. Teardown at
start AND end — exact before/after count match on every table
touched (spv_subscriptions, portfolio.*, document_record_links),
not an unconditional TRUNCATE.

Assertions:
  [Y] Report Task 1's four findings explicitly, including the
      REAL query path to an SPV interest's current value
  [Y] The derivation view produces a position for a real,
      current spv_subscriptions row with authority='internal'
      and source_system='spv_subscriptions'
  [Y] The resolved value matches Task 1b's real query path
      EXACTLY — not an approximation
  [Y] A subscription with valid_to SET (no longer current) does
      NOT appear via the view
  [Y] NO WRITE PATH EXISTS against the view — attempting to
      write through portfolio_assets.py's functions targeting a
      view-derived position id fails cleanly, proving corrections
      must go to spv_subscriptions itself
  [Y] Editing the underlying spv_subscriptions row changes what
      the view returns on the NEXT read — proves it is genuinely
      derived, not cached or duplicated
  [Y] A cash asset is created idempotently per (org, currency) —
      creating it twice does not duplicate it
  [Y] A cash position round-trips correctly using the SAME
      asset+position pattern as every other asset type — no
      parallel mechanism
  [Y] A new document_record_links record_type value can be
      written and is queryable via Chancery's real existing
      lookup path — no migration was needed for this
  [Y] Cross-org isolation on the view and the new functions,
      tested against the real app_service connection
  [Y] Teardown: exact before/after count match on every table
      touched
