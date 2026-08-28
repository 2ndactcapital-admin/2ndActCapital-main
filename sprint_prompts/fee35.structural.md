FEE MODULE — SPRINT fee35 (calculation engine). 2 tasks + golden-case
verification. NO PART 1 SQL — this sprint is deliberately pure Python
with zero database access. Nothing to apply before starting.

CONTEXT, settled, do not re-derive:
- fee_schedules / fee_schedule_tiers / fee_exclusions / fee_discounts /
  fee_credits (fee34) define the RULE. This sprint builds the thing
  that READS a rule plus a set of balances/positions/flows and
  produces a number, with a full audit trail. It does not persist
  anything — fee_runs/fee_run_lines (fee36) is where results get
  written down. This sprint has no tables of its own.
- fee_exclusions has NO fee_schedule_id (fee34 finding [2k]) — a
  schedule's applicable exclusions are looked up by scope
  (account/billing_group/household), not by a join to the schedule.
  The engine's caller resolves which exclusions/discounts/credits
  apply to a given account for a given period BEFORE calling the
  engine; the engine itself takes them as plain input, it does not
  query for them.
- Decimal only, everywhere, including every intermediate value. A
  float anywhere in this module is a bug, not a style violation —
  fee34's own validator refused a float tier bound for exactly this
  reason (finding [2g]); this sprint must hold the same line all the
  way through the arithmetic, not just at the boundary.
- ordering_policy on fee_schedules (default
  ["EXCLUSIONS","TIERS","DISCOUNTS","CREDITS","MINIMUM","MAXIMUM"],
  validated as a permutation by fee34) is not decorative — the engine
  MUST apply steps in exactly the sequence a schedule specifies, and
  a schedule with a non-default ordering must produce a different
  number than one with the default, on a fixture built to prove it.

OUT OF SCOPE: fee_runs, fee_run_lines, anything that writes to the
database, anything that resolves WHICH schedule/exclusions/discounts/
credits apply to an account (that resolution already exists from
fee32's precedence work and fee34's assignment resolver — this sprint
consumes their output, it doesn't reimplement resolution). No
Altruist-API-shaped work. No SPV carry/waterfall (explicitly deferred
to its own later sprint per the original design doc).

STANDING RULES: Decimal only. No interactive prompts. This module
must be importable and testable with ZERO database connection — pass
it plain dataclasses/pydantic models mirroring the real row shapes,
get a result object back. If it needs to open a database connection
to run its own tests, that is a design failure, not a test detail.

=== TASK 1: Input contracts + engine core ===
Discover the REAL, live column shapes of fee_schedules,
fee_schedule_tiers, fee_exclusions, fee_discounts, fee_credits
(fee34), accounts/account_balances_daily/account_flows (fee31), and
portfolio.positions (fee32) before defining input types — mirror
what's actually deployed, not this prompt's paraphrase of it.
Define plain-data input types (no ORM objects, no DB session) for:
schedule + its tiers, a resolved list of applicable exclusions,
discounts, credits (already scope-resolved by the caller), and the
account's balances/positions/flows for the period being billed.
Build the engine as a pipeline, each stage independently testable:
  1. Billable value resolution — apply exclusions (EXCLUDE/
     REDUCED_RATE/FLAT per basis_type), cash_treatment
     (INCLUDE/EXCLUDE/EXCLUDE_ABOVE_PCT), margin_treatment
     (IGNORE/REDUCE_BILLABLE).
  2. Valuation — PERIOD_END/PERIOD_START/AVG_DAILY/AVG_MONTH_END
     against the account's balance history for the period.
  3. Day-weighted flow adjustment — flows above
     day_weight_threshold get weighted by days remaining in the
     period; flows below it are ignored, when day_weight_flows is
     true. When false, flows are not weighted at all.
  4. Tiering — GRADUATED (each slice at its own rate),
     CLIFF (whole balance at the reached tier's rate),
     BLENDED_PUBLISHED (treat as GRADUATED unless the schedule
     specifies otherwise — flag this assumption explicitly rather
     than silently picking one, since the design doc does not fully
     specify BLENDED_PUBLISHED's mechanics).
  5. Discounts, then credits, then minimum, then maximum — but ONLY
     in the sequence ordering_policy actually specifies; do not
     hardcode this sequence.
  6. Proration — CALENDAR_DAYS/BUSINESS_DAYS/NONE for a partial
     period (inception or termination mid-period). BUSINESS_DAYS
     needs a holiday calendar; if none exists yet in this codebase,
     use a plain Mon-Fri calendar and flag this as a known
     simplification rather than blocking on building a full holiday
     calendar in this sprint.
Every stage must emit its contribution to calc_detail — a
human-readable, JSON-serializable audit trail sufficient to answer
"why is this fee this number" without re-running anything. This is
not optional decoration; treat it as a first-class output, not a
debug log.

=== TASK 2: Golden-case suite ===
Hand-computed test cases, verified to the cent by arithmetic done
independently of the code (i.e., compute the expected answer on paper
or in a spreadsheet first, then assert the engine matches it — do not
derive the expected value FROM the engine's own output). At minimum:
  1. Graduated tiering on a clean balance, no exclusions/flows.
  2. Cliff tiering on the SAME balance/schedule shape as #1, proving
     the two methods produce genuinely different numbers.
  3. Mid-quarter inception, calendar-day proration (e.g. 47 of 91
     days).
  4. A flow of $30,000 on day 47 of the period, above
     day_weight_threshold — correctly weighted by remaining days.
  5. A flow of $2,000 on day 47 with day_weight_threshold=$10,000 —
     correctly ignored (no weighting applied at all).
  6. minimum_fee that bites AFTER a 20% PCT_OFF discount is applied
     — proves ordering_policy's sequence, not just its presence.
  7. The SAME schedule/balance as #6, but with a customized
     ordering_policy that applies MINIMUM before DISCOUNTS — must
     produce a DIFFERENT final number than #6, proving the sequence
     is actually read from the schedule and not hardcoded.
  8. An excluded concentrated security position reduces billable
     value below total account value (basis_type='SECURITY').
  9. A REDUCED_RATE exclusion — part of the account bills on the
     primary schedule, part on alt_fee_schedule_id, and the two
     amounts sum correctly to the reported total.
  10. An SPV_MGMT_FEE_OFFSET credit reduces the advisory fee by
      exactly offset_pct of the credit's stated basis.
  11. Termination mid-period, billed in ADVANCE — proves the
      refund/negative-proration case, not just the inception case.
  12. A household-level minimum_fee (minimum_fee_scope='HOUSEHOLD')
      correctly considers a SECOND account's fee before applying the
      minimum to either — proves minimum scope isn't silently
      account-only regardless of what minimum_fee_scope says.
Each case: assert the exact Decimal result AND assert calc_detail
contains a specific, checkable trace of the calculation (not just
that the file field is non-empty) — e.g., for case #1, assert the
tier slice amounts in calc_detail sum to the tiered result, so a
future refactor that gets the right total via wrong internal math
still fails.

=== VERIFICATION ===
No database, so no app_service/RLS checks apply here — this is a
pure unit-test suite (scripts/verify_fee35.py or a proper pytest
module, whichever this codebase's convention prefers; check how
fee34's fee_validation.py tests were structured, if any exist, and
match that). Every golden case from Task 2 is itself a verification
assertion. Additionally assert:
  - The engine never imports anything DB-related (no asyncpg,
    no connection object in any function signature) — grep for it
    if there's no cleaner way to assert it.
  - A float passed anywhere a Decimal is expected raises or is
    rejected at the boundary, not silently coerced.
  - Calling the engine twice with identical inputs produces byte-
    identical output (pure function property — no hidden clock/
    randomness dependency).
Report actual results, then stop. Do not proceed to fee36 in this
same run.
