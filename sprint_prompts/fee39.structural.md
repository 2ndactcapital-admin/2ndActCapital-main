FEE MODULE — SPRINT fee39 (profitability views). 3 tasks +
verification. Part 1 SQL (revenue_events, the cost_events FK fee37
deliberately deferred, v_profitability_events) is already applied by
Joe directly via Supabase MCP — confirm it live before writing any
code.

CONTEXT, settled: revenue_events and cost_events share the same
dimensional keys (account_id, household_id, billing_group_id,
advisor_id, product_type) on purpose, so every requested cut — account,
arbitrary account set, household, arbitrary household set, billing
group, advisor, product, firm — is a GROUP BY against
v_profitability_events, not a bespoke query per cut. Do not build
separate aggregate tables per cut; the standing rule is materialize
only if performance actually demands it, and nothing has demonstrated
that yet.

fee37/fee38 caveats this sprint inherits:
  - cost_schedules/provider_benefit_schedules rates are UNVERIFIED
    (fee37 F6). Any profitability number derived from a MARKUP/
    PASS_PARTIAL cost_pass_through_policy inherits that same caveat.
  - cost_events' dedupe index is incomplete for scope combinations
    with NULL account/household/billing_group (fee37 F4, only
    partially closed). Do not assume cost_events is duplicate-free;
    check for it if this sprint's rollups would be visibly wrong in
    its presence.
  - fee_run_lines (fee36) is the only correct source for advisory-fee
    revenue. Do not recompute a fee from balances here — read the
    already-posted, already-verified net_fee.

OUT OF SCOPE: any UI beyond a functional dashboard proving the numbers
are right (a polished screen is a later pass). SPV carry (still
deferred per the design doc — event-driven, not this sprint). Any
Altruist-API-shaped work.

STANDING RULES: org_id never from request bodies. Decimal everywhere.
No interactive prompts. Standard output line order is fixed and must
not be reordered by any implementation: gross revenue, direct costs,
contribution margin (direct), advisor comp/service cost, contribution
margin (after service), allocated overhead, net profit — showing
margin BEFORE allocation is as important as showing it after, since a
margin number with overhead already baked in invites an argument
rather than a decision.

=== TASK 1: Discover, don't assume ===
Query live: revenue_events, the new cost_events FK, and
v_profitability_events exactly as deployed. Confirm which
product_type/revenue_type values fee36's fee_run_lines actually
produce today (query real posted fee_runs from fee36's verify fixtures
if nothing else exists) so the emission logic in Task 2 maps onto real
values, not assumed ones.

=== TASK 2: Revenue emission from fee_run_lines ===
On a fee_run reaching POSTED (fee36), emit one revenue_events row per
fee_run_line: revenue_type='ADVISORY_FEE' (or the correct mapping for
other product_types fee34/36 support — SPV, STRUCTURED_INVESTMENT,
PLANNING, CLUB_DUES, TRANSACTION each need their own revenue_type
mapping, do not collapse them all to ADVISORY_FEE), source_type=
'FEE_RUN_LINE', source_id=the fee_run_line's id, amount=net_fee,
recognition='ACCRUAL'. This must be idempotent — re-processing an
already-emitted POSTED run must not create duplicate revenue_events
(the dedupe unique index enforces this; give it a clean error/no-op
behavior rather than surfacing a raw constraint violation). A
REVERSAL run's lines emit their own (negative) revenue_events the same
way — do not special-case reversals into a different code path.

=== TASK 3: Rollup queries + dashboard ===
Build the query layer implementing the standard P&L line order above,
parameterized by any of the eight cuts (a single account_id, an
arbitrary list of account_ids, a single household_id, an arbitrary
list of household_ids, a billing_group_id, an advisor_id, a
product_type, or the whole firm with no filter). Include the specific
metric the design doc calls out as the one that changes behavior:
households ranked by margin, worst first — not just a total. A minimal
functional dashboard screen is in scope if time allows; the query
layer working correctly is not optional.

=== VERIFICATION ===
Write scripts/verify_fee39.py — pass/fail only, app_service for RLS,
teardown discipline.
Assert:
  1. revenue_events deployed, RLS on, expected constraint shape;
     cost_events_linked_revenue_event_fkey exists and is enforced;
     v_profitability_events returns the expected UNION shape (revenue
     positive, cost negative) on a small fixture.
  2. Posting a fee_run (fee36) emits exactly one revenue_events row
     per fee_run_line, with amounts matching net_fee exactly.
  3. Re-processing the same POSTED run a second time does not create
     duplicate revenue_events rows.
  4. A REVERSAL run's lines emit correctly-negative revenue_events,
     and the profitability view for that account nets to the correct
     total across both the original and reversal.
  5. Each of the eight cuts, given a realistic multi-account,
     multi-household fixture with matching cost_events on some of
     them, produces the correct gross revenue, direct cost,
     contribution margin, and net profit numbers — hand-computed, not
     derived from the code being tested.
  6. The worst-margin-first household ranking is actually sorted
     correctly on a fixture with at least three households of
     different profitability.
  7. Cross-org isolation on revenue_events via app_service (the view
     inherits its RLS behavior from the two underlying tables — verify
     this is actually true rather than assumed, since a view does not
     automatically re-enforce RLS the same way in every Postgres
     configuration).
  8. No table's row count differs from its pre-test count after the
     script exits.
Report actual results, then stop.
