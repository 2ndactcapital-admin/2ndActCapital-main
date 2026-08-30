FEE MODULE — SPRINT fee38 (Altruist One evaluator). 3 tasks +
verification. Part 1 SQL (provider_benefit_schedules,
altruist_one_evaluations) is already applied by Joe directly via
Supabase MCP — confirm it live before writing any code.

CONTEXT, settled: benefit rates (sweep/HY cash uplift, model-
marketplace discount) get their own table, symmetric to fee37's
cost_schedules, rather than being inlined as bare constants — same
provenance fields (source_url, source_verified_on), same UNVERIFIED
caveat as fee37's F6 finding. Do not present these numbers as
confirmed-accurate; they carry the identical research limitation.

fee37 findings this sprint inherits and must respect:
  F3 (fixed post-merge): approved_by/disclosure_acknowledged_by FK to
     users — this sprint's decided_by column already has the FK from
     Part 1, learn from that fix rather than repeating the gap.
  F4 (fixed post-merge): cost_events has a dedupe unique index, but
     it is acknowledged incomplete (NULL scope columns can still
     collide-miss). Do not assume this evaluator's own reads from
     cost_events are duplicate-free without checking.
  F6: seeded cost_schedules rates are UNVERIFIED. Anything this
     evaluator reads from cost_schedules or provider_benefit_schedules
     inherits that same caveat and must say so wherever a
     recommendation is displayed, not just in a code comment.

OUT OF SCOPE: any Altruist-API-shaped work (this evaluator computes
against the firm's own account/balance data plus the seeded rate
card — it does not call Altruist). Actual enrollment/write-back
(fee45, gated on API docs + partner access). revenue_events/cost
rollups beyond what this evaluator itself needs to compute (fee39).

STANDING RULES: org_id never from request bodies. Decimal everywhere.
No interactive prompts. TLH tax alpha is explicitly EXCLUDED from the
default recommendation calculation (per the design doc) — compute it
if you have the inputs, but keep it out of the ENROLL/DO_NOT_ENROLL/
MARGINAL threshold logic, surfaced separately and labeled estimated.

=== TASK 1: Discover, don't assume ===
Query live: both new tables exactly as deployed. Confirm
cost_providers/cost_schedules from fee37 (ALTRUIST row, its 10 rate-
card schedules) are queryable and note which specific schedules this
evaluator needs (direct indexing rate, model marketplace discount,
margin spread tiers, the Altruist One subscription cost itself).
Confirm what account-level data is actually available to compute
against: sweep cash balance, HY cash balance, model-allocated AUM,
trade count, margin balance — check account_balances_daily,
account_positions_daily equivalents (portfolio.positions), and
account_flows for what's really there versus what the formula ideally
wants. Report gaps rather than inventing fields.

=== TASK 2: Breakeven calculator ===
Pure calculation (may read the DB for rates/balances, but the
arithmetic itself should be a clean, testable function): for a given
household and evaluation date, compute:
  annual_cost = max(0.0012 * household_value, 12 * account_count)
    — per the design doc's own flagged ambiguity (fee37's rate-card
    seeding), confirm which reading (max() vs additive) fee37 actually
    seeded and use that reading consistently; if fee37 seeded both as
    separate rows, this task must pick one explicitly and say why.
  Benefit components, each computed separately and named in
  benefit_breakdown: sweep cash uplift, HY cash uplift, model
  marketplace discount (capped at what's actually being paid, never
  more), margin savings (only where margin is actually drawn), ticket
  savings (only if a real trade-count figure exists — do not invent
  one). TLH tax alpha computed and shown separately, excluded from the
  recommendation threshold as stated above.
  recommendation: ENROLL if net_benefit is clearly positive,
  DO_NOT_ENROLL if clearly negative, MARGINAL for a defensible band
  near zero — define the band explicitly rather than a bare >0/<0
  split, and say what the band is in the output.

=== TASK 3: Decision recording + override ===
Persist every evaluation as a row, never overwrite one. Recording a
decision that matches the recommendation needs no reason. Recording
one that diverges REQUIRES override_reason and decided_by (the DB
constraint already enforces this — give it a clean error, matching
the fee34 pattern of naming the field rather than surfacing a raw
constraint violation). Support re-evaluation on a schedule (accept a
next_review_on) and note, but do not build, the actual Workflow
Manager trigger for it — that's a fee-module-external dependency on
S29b landing, per the standing sequencing decision; a hook or a clear
TODO is enough here.

=== VERIFICATION ===
Write scripts/verify_fee38.py — pass/fail only, app_service for RLS,
teardown discipline.
Assert:
  1. Both tables deployed, RLS on, expected constraint/policy shape.
  2. A household with 10%+ of assets in sweep cash recommends ENROLL
     on a realistic fixture.
  3. A household of several small accounts averaging near the
     per-account minimum threshold recommends DO_NOT_ENROLL, proving
     the account-count term actually binds, not just the AUM term.
  4. A household near the breakeven boundary lands in MARGINAL, and
     the band's definition is visible in the output.
  5. Recording a decision equal to the recommendation requires no
     reason and succeeds with override_reason/decided_by both NULL.
  6. Recording a decision that diverges from the recommendation
     WITHOUT a reason is refused with a clear, field-naming error;
     the same divergent decision WITH a reason and a decider succeeds.
  7. TLH tax alpha appears in the output labeled estimated and does
     NOT change which of ENROLL/DO_NOT_ENROLL/MARGINAL was chosen —
     prove this by toggling a large synthetic TLH input and showing
     the recommendation is unchanged.
  8. Every dollar figure in benefit_breakdown traces to a real seeded
     cost_schedules/provider_benefit_schedules row — no hardcoded rate
     anywhere in the calculation code.
  9. Cross-org isolation on both tables via app_service.
  10. No table's row count differs from its pre-test count after the
      script exits.
Report actual results, then stop. Do not proceed to fee39 in this
same run.
