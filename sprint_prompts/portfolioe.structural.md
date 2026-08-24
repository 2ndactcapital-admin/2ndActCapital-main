PORTFOLIO PHASE E — CHANCERY-SOURCED POSITIONS, COMMITMENTS,
TAX-DOCUMENT TRACKING. 6 tasks + verification. Design: docs/
PORTFOLIO_REPORTING_DESIGN_V6.md §12, §13. Builds on A1, A2, B,
C, D — all merged.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

CONTEXT — Part 1 SQL ALREADY APPLIED directly:
  portfolio.commitments exists: id, org_id, position_id,
  commitment_amount, commitment_date, called_to_date,
  distributed_to_date, recallable_amount, unfunded, vintage_year,
  liquidity_terms jsonb, tax_doc_expected, tax_year,
  tax_doc_status (CHECK: not_expected|awaiting|received|amended),
  bitemporal columns. RLS enabled, one org-isolation policy.
  Do not re-create.

  transaction_types' affects_paid_in / affects_unfunded /
  is_recallable are CONFIRMED live and correct: the 4 call_*
  codes carry affects_paid_in=1, affects_unfunded=-1;
  dist_recallable carries affects_paid_in=-1, affects_unfunded=1,
  is_recallable=true; every other code is 0/0/false. The
  called_to_date/distributed_to_date/recallable_amount derivation
  in Task 2 must use these EXACT values, not re-derive them
  heuristically.

STANDING RULES: org_id never from request body; Decimal for any
monetary value; no interactive prompts; schema-qualify every
portfolio.* reference (portfolio is NOT on search_path — do not
rediscover this, confirmed in every prior phase).

DELIBERATELY OUT OF SCOPE — DO NOT BUILD:
  * Corporate actions (Phase F)
  * UDFs (Phase G)
  * The reconciliation engine, performance calculations, or
    cross-client analysis (Phase H — designed for, not built)
  * Any change to Phase D's SPV derivation view or Phase C's
    rollup

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Re-read the REAL current Chancery document-confirm hook
      (Phase 6's confirm_document, referenced by Phase 7's
      workflow bridge and Phase D's record_valuation_from_
      document) — confirm the exact, current, real hook point
      where "this document represents a position" could
      plausibly fire, and what data is available there (org_id,
      confirmed extraction fields, document_id).
  1b. Confirm the REAL current Chancery narrative-extraction
      output shape (Phase 11a) for a capital-account-statement-
      like document — what fields would plausibly map to
      commitment_amount, called/distributed figures, or an
      asset's identifying name. Report honestly if this requires
      a NEW extraction template rather than reusing an existing
      one — do not force-fit narrative extraction's existing
      output if it genuinely does not carry the right fields.
  1c. Confirm Phase D's established position-creation +
      document-linking composition (portfolio_assets.create_
      asset/create_position + link_portfolio_document) — this
      phase's Chancery-sourced creation function must compose
      these exactly, not reinvent them.
  1d. Confirm portfolio.assets' asset_class/include_in_
      performance real deployed defaults (asset_class default
      'financial', include_in_performance default true per A2)
      — Task 4 needs to correctly override BOTH for a hard asset.

=== TASK 2: COMMITMENT DERIVATION ===
Build apps/api/services/portfolio_commitments.py:
  create_commitment(conn, *, org_id, position_id,
  commitment_amount, commitment_date, vintage_year=None,
  liquidity_terms=None, tax_doc_expected=False, tax_year=None) ->
  creates the row; called_to_date/distributed_to_date/
  recallable_amount/unfunded start at their defaults (0/0/0/NULL)
  since no transactions exist yet.
  recompute_commitment(conn, org_id, commitment_id) -> sums the
  position's REAL transactions using Task's CONFIRMED affects_
  paid_in/affects_unfunded values (called_to_date +=
  amount * affects_paid_in where affects_paid_in=1;
  recallable_amount tracks distributions with is_recallable=true
  specifically; unfunded = commitment_amount - called_to_date +
  recallable_amount, per the design's exact semantics) and
  UPDATES the commitment row. This is NOT a live trigger — same
  reasoning as Phase C's rollup: called explicitly after a
  transaction batch, not fired per-write, to avoid transient
  partial figures mid-batch.

=== TASK 3: CHANCERY-SOURCED POSITION CREATION ===
Build a real function creating an asset + position pair FROM a
confirmed Chancery document, per Task 1a/1b's REAL findings —
authority='stated', source_system='chancery'. Compose Task 1c's
established pattern exactly. Link the created asset AND position
to the source document via Phase D's link_portfolio_document
(record_type='portfolio_asset' and 'portfolio_position'
respectively) so the drill-through works immediately. If Task 1b
found narrative extraction's current output does not carry
sufficient fields, build the minimal REAL mapping from whatever
fields genuinely exist — do not fabricate fields extraction does
not produce.

=== TASK 4: HARD ASSET, PROVEN END TO END ===
Using Task 3's Chancery-sourced creation for a real hard-asset
example (a house or art piece confirmed via a Chancery document):
prove asset_class='hard_asset' and include_in_performance=false
are correctly set (overriding A2's defaults from Task 1d), and
prove TWO valuations with DIFFERENT purpose values ('insurance'
and 'net_worth') coexist for the SAME asset without conflict —
using A2's real valuations table and its purpose CHECK
constraint, already deployed.

=== TASK 5: TAX-DOCUMENT CHASE LIST ===
Build a real query/endpoint returning every commitment where
tax_doc_expected=true and tax_doc_status is NOT 'received', for a
given org and tax_year — the "who is missing a K-1" list. Reuse
the real index already applied in Part 1
(idx_commitments_tax_chase) — confirm the query actually uses it
(EXPLAIN), do not write a query the index cannot serve.

=== TASK 6: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md in the same commit: record Phase E
as built, Task 1a/1b's real findings on the Chancery hook point
and extraction-field mapping (or the gap, if one was found), and
that Phase F (corporate actions) is next.

=== VERIFICATION: apps/api/scripts/verify_portfolioe.py ===
Pass/fail only. No interactive prompts. Idempotent. Teardown at
start AND end — exact before/after count match on every table
touched, not an unconditional TRUNCATE (per the established
discipline across every prior phase).

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] A commitment can be created with a real position_id and
      the correct initial defaults
  [Y] RECOMPUTE, using REAL transactions: a capital call of
      $50,000 against a commitment correctly increases
      called_to_date by exactly $50,000 and decreases unfunded
      by exactly $50,000 — exact Decimal assertion
  [Y] RECOMPUTE, recallable distribution: a $10,000
      dist_recallable correctly DECREASES called_to_date and
      INCREASES both distributed_to_date and recallable_amount —
      prove the recallable case is handled distinctly from a
      normal distribution (dist_income), which must NOT affect
      called_to_date or unfunded at all
  [Y] A Chancery-sourced position is created with authority=
      'stated', source_system='chancery', and is correctly linked
      via document_record_links to its source document (both the
      asset AND the position, queryable via Phase 9's real
      DocumentsPanel lookup)
  [Y] HARD ASSET: asset_class='hard_asset' and
      include_in_performance=false are set correctly, overriding
      A2's real defaults — assert the actual override happened,
      not just that the desired final state exists
  [Y] HARD ASSET: an 'insurance' valuation and a 'net_worth'
      valuation for the SAME asset both exist simultaneously with
      DIFFERENT values, and reading each by purpose returns the
      correct one — not the most recent regardless of purpose
  [Y] TAX CHASE LIST: a commitment with tax_doc_expected=true and
      tax_doc_status='awaiting' for the target tax_year appears;
      one with tax_doc_status='received' does NOT; one with
      tax_doc_expected=false does NOT — all three cases proven
      distinctly
  [Y] The chase-list query uses the real deployed index (EXPLAIN
      shows an index scan, not a sequential scan)
  [Y] Cross-org isolation on commitments and the Chancery-sourced
      creation function, tested against the real app_service
      connection
  [Y] Teardown: exact before/after count match on every table
      touched, including portfolio.commitments
