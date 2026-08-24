PORTFOLIO PHASE C — ROLLUP INTO entity_holdings. 5 tasks +
verification. Design: docs/PORTFOLIO_REPORTING_DESIGN_V6.md §14.
Builds on A1, A2, and Phase B, all merged.

WHY THIS MATTERS: services/allocation_lens.py reads entity_
holdings directly (line ~142) to drive the Sprint 21 sunburst.
That table has been empty since S21 shipped — the sunburst has
never rendered real data. This sprint is the first thing that
populates it.

THERE IS NO HUMAN AVAILABLE. Reporting discovery findings means
"report, then immediately continue in the same response" — never
stop and wait. If uncertain whether to continue, continue.

CONTEXT — Part 1 SQL ALREADY APPLIED directly:
  entity_holdings gained a real UNIQUE constraint:
  (org_id, entity_id, taxonomy_key, as_of_date, source) — it had
  NONE before, which would have made a rollup re-run insert
  duplicate buckets instead of updating them. Do not re-add it.

STANDING RULES: org_id never from request body; Decimal for any
monetary value; no interactive prompts; schema-qualify every
portfolio.* reference (portfolio is NOT on search_path — do not
rediscover this, confirmed in A1/A2/B).

DELIBERATELY OUT OF SCOPE — DO NOT BUILD:
  * Any change to services/allocation_lens.py or the sunburst UI
    itself — it already reads entity_holdings correctly; this
    sprint only needs to populate the table it already reads
  * SPV derivation view — Phase D
  * Cash modeling, corporate actions, commitments, UDFs
  * A live trigger firing the rollup on every position write —
    see Task 2's reasoning for why this is a deliberate,
    callable-batch design instead

=== TASK 1: DISCOVER — do not assume ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Re-read the REAL current services/allocation_lens.py query
      against entity_holdings — confirm exactly which columns it
      reads and in what grain, so the rollup's output genuinely
      matches what the sunburst expects (not just "a plausible
      shape").
  1b. Confirm the REAL current resolve_entity_set / entity
      look-through mechanism (used by the Ownership Tree Graph
      and staff visibility) — the rollup must attribute an
      asset's value to EVERY entity in its ownership chain that
      the graph resolves, not just the direct owner_entity_id on
      the position. Report its real signature.
  1c. Confirm the real current source_system values on positions
      (from Phase B: reporting_tool_import, altruist,
      spv_subscriptions, chancery, manual — altruist unconfirmed
      live) and confirm the rollup reads whichever position ROW
      WON precedence resolution (superseded_by_source IS NULL) —
      never a superseded/losing row.
  1d. Confirm entity_holdings' real, deployed columns and the
      new UNIQUE constraint exactly as described in CONTEXT.

=== TASK 2: THE ROLLUP SERVICE — callable, not trigger-fired ===
Build apps/api/services/portfolio_rollup.py:
  rollup_entity_holdings(conn, org_id, as_of_date) ->
  Groups CURRENT, NON-SUPERSEDED positions
  (system_to IS NULL, valid_to IS NULL, superseded_by_source IS
  NULL) by (entity_id, taxonomy_key) — but "entity_id" here means
  EVERY entity the ownership graph (Task 1b) resolves the
  position's owner up to, not only the direct owner. A position
  held by an account owned by a trust owned 50% by an individual
  must roll up into the sunburst for the individual too, correctly
  attributed through the graph — not just the account or the
  trust.
  Writes to entity_holdings with source='portfolio', UPSERTing on
  the real constraint from Task 1d (ON CONFLICT DO UPDATE, never
  a blind INSERT that would violate the constraint on a re-run).
  Deliberately NOT trigger-fired on every position write:
  positions arrive in batches (a file import, an eventual
  Altruist sync), and rolling up mid-batch would produce
  transient, misleading partial buckets a user could see between
  individual position writes. Callable explicitly after a batch
  completes, or on a schedule — this sprint builds the callable
  function; wiring it to fire automatically after Phase B's
  import completes is a reasonable, small addition if it fits
  cleanly, but is not the sprint's core deliverable.

=== TASK 3: HANDLE VALUE ATTRIBUTION FOR PERCENT-BASIS AND
FRACTIONAL OWNERSHIP ===
A position with ownership_basis='percent' represents partial
ownership of an entity, not a dollar figure on its own — and
look-through ownership percentages compound (50% of a trust that
owns 60% of an LLC's position attributes 30% of that position's
value to the individual, not 50% or 60%). Use the REAL percentage
data resolve_entity_set (Task 1b) already carries for exactly
this purpose — do not build a second ownership-percentage
calculation.

=== TASK 4: A MINIMAL TRIGGER ENDPOINT ===
A simple, real endpoint (e.g. POST /api/v1/portfolio/rollup) that
calls Task 2's function for the caller's org and a given
as_of_date, org-admin-gated using the same real permission
pattern already established elsewhere in this codebase (do not
invent new gating).

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md in the same commit: record Phase C
as built, and — this is the notable one — that the S21 sunburst
now renders REAL data for the first time since it shipped, given
a real rollup has run. Note Phase D (SPV derivation view) is
next.

=== VERIFICATION: apps/api/scripts/verify_portfolioc.py ===
Pass/fail only. No interactive prompts. Idempotent. Teardown at
start AND end — exact before/after count match on every table
touched, not an unconditional TRUNCATE (per the established
discipline; entity_holdings and portfolio.* tables may hold real
rows from other tracks).

Assertions:
  [Y] Report Task 1's four findings explicitly
  [Y] A real position for a directly-owned entity rolls up into
      entity_holdings with the correct taxonomy_key and market_
      value
  [Y] LOOK-THROUGH: a position held by an entity two levels down
      an ownership chain (e.g. an account owned by a trust owned
      by an individual) correctly rolls up into the INDIVIDUAL's
      entity_holdings too, not only the direct owner's — this is
      the assertion that actually proves the graph-attribution
      requirement, not just "some row got created"
  [Y] FRACTIONAL OWNERSHIP: an individual owning 50% of a trust
      that owns 60% of an LLC holding a $100,000 position sees
      exactly $30,000 attributed in their own entity_holdings
      bucket — the exact compounded figure, not 50% or 60% alone
  [Y] A SUPERSEDED (losing) position is EXCLUDED from the rollup
      — only the precedence winner is counted, never both, never
      the loser
  [Y] Re-running the rollup for the same org and as_of_date
      UPDATES the existing entity_holdings row (via the real
      UNIQUE constraint) rather than creating a duplicate — prove
      via row count before and after a second run
  [Y] A position's value changing between two rollup runs (e.g. a
      new valuation superseding an old one) is correctly reflected
      on the SECOND run — the rollup reads current state, not a
      cached figure
  [Y] Cross-org isolation: an org's rollup never includes another
      org's positions, tested against the real app_service
      connection
  [Y] The rollup endpoint is correctly rejected for a non-admin
      caller
  [Y] Teardown: exact before/after count match on entity_holdings
      and every portfolio.* table touched
