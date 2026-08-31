FEE MODULE — SPRINT fee42 (SPV fee terms). 3 tasks + verification.
Part 1 SQL (spv_fee_terms, spv_fee_side_letters) is already applied by
Joe directly via Supabase MCP — confirm it live before writing any
code.

CONTEXT, settled:
- spvs.mgmt_fee_pct/carry_pct are flat scalars, exactly as the
  original design doc flagged as inadequate. This sprint is ADDITIVE —
  the old columns stay in place, untouched, not dropped or renamed.
- spvs.class_label already exists (S23). spv_fee_terms is per-class
  (class_label nullable = whole-fund terms), because a fund's classes
  can carry different economics.
- spv_transaction_allocations already exists and is fee36's CONFIRMED
  SPV credit basis source for SPV_MGMT_FEE_OFFSET credits. This
  sprint's job is to make spv_fee_terms the source of TRUTH for what
  the management fee and carry terms actually are; fee36's existing
  basis-resolution logic should come to READ spv_fee_terms instead of
  the flat spvs columns wherever it currently reads the latter — but
  confirm exactly what fee36 reads today (Task 1) before changing it,
  do not assume.
- Carry itself (the waterfall calculation, catchup, clawback
  execution) is explicitly OUT OF SCOPE — carry is event-driven off
  realizations, not calendar-driven like everything else in this fee
  module, and belongs in its own later sprint. This sprint stores
  carry TERMS (rate, hurdle, catchup, basis) so a future waterfall
  sprint has real data to read, but does not compute a carry
  distribution.
- offsets_advisory_fee is the double-dip switch fee34's fee_credits
  table already has a slot for (SPV_MGMT_FEE_OFFSET). Wire this
  sprint's terms into that existing mechanism, do not build a second
  offset mechanism.

OUT OF SCOPE: the carry/distribution waterfall engine itself. Any
Altruist-API-shaped work. Migrating every historical SPV's flat
mgmt_fee_pct/carry_pct into spv_fee_terms automatically — do this only
for SPVs Task 1 finds are still ACTIVE/currently billing, and report
which ones were skipped and why, rather than silently backfilling
every historical row.

STANDING RULES: org_id never from request bodies. Decimal everywhere.
No interactive prompts. Additive-first — nothing about this sprint
should require touching spvs.mgmt_fee_pct/carry_pct's existing
readers in a way that breaks them before this sprint's own migration
is complete and verified.

=== TASK 1: Discover, don't assume ===
Query live: spv_fee_terms/spv_fee_side_letters exactly as deployed.
Confirm EXACTLY what fee36's SPV_MGMT_FEE_OFFSET credit-basis
resolution currently reads (spvs.mgmt_fee_pct directly, or something
else — check services/fee_runs.py or wherever it actually lives).
Confirm which SPVs are currently active/billing (spv_status) versus
historical/closed, since only active ones need real spv_fee_terms rows
in this sprint. Confirm spv_transaction_allocations' real shape for
computing mgmt_fee_basis correctly (COMMITTED vs FUNDED vs NAV vs
INVESTED_COST each need different source data — report which are
actually computable from what exists today and which are not).

=== TASK 2: Backfill active SPVs + step-down/term-limit logic ===
For every ACTIVE spv (per Task 1's finding), create a spv_fee_terms
row from its existing flat mgmt_fee_pct/carry_pct, with
mgmt_fee_basis/frequency/hurdle_type set to the most defensible
inferred default given what's actually known about that SPV (report
per-SPV what was inferred versus genuinely known — do not silently
guess a hurdle_type of NONE when it might be unknown). Build the pure
calculation logic (no DB access needed for the calculation itself,
same discipline as fee35) for: applying mgmt_fee_step_down at the
correct year boundary, and refusing to accrue past mgmt_fee_term_years
if set. This does NOT need to be wired into fee36's run cycle in this
sprint if that would require touching fee36's already-verified code in
a risky way — report the wiring gap explicitly if you decide to defer
it rather than force it in.

=== TASK 3: Side letters + offset wiring ===
spv_fee_side_letters resolution: given an entity's subscription to an
SPV, apply any active side letter's overrides on top of the
class/whole-fund spv_fee_terms — a partial override, not a full
replacement (only the keys present in `overrides` change; everything
else comes from the base terms). Wire offsets_advisory_fee into
fee34's fee_credits mechanism: when true, confirm (do not just assume)
that creating a fee_credits row with credit_source=
'SPV_MGMT_FEE_OFFSET' for the relevant account/household is the
correct, already-existing path — reuse it if so, report a gap if the
two don't actually connect cleanly today.

=== VERIFICATION ===
Write scripts/verify_fee42.py — pass/fail only, app_service for RLS,
teardown discipline.
Assert:
  1. Both tables deployed, RLS on, expected constraint/policy shape;
     the active-terms-per-class uniqueness (including the NULLS NOT
     DISTINCT whole-fund case) is genuinely enforced.
  2. Every ACTIVE spv (per Task 1) has a corresponding spv_fee_terms
     row after backfill; a closed/historical SPV does not get one
     unless Task 1 found a real reason to include it.
  3. mgmt_fee_step_down applies at the correct year boundary on a
     fixture spanning the boundary, and mgmt_fee_term_years correctly
     stops accrual past its limit.
  4. A side letter's partial override changes only the overridden
     fields; every other field still resolves from the base
     class/whole-fund terms.
  5. carry_pct without hurdle_type is refused by the database
     (the CHECK constraint), and the application layer gives a clean
     error naming the field rather than a raw constraint violation.
  6. offsets_advisory_fee=true produces a real, correctly-scoped
     fee_credits row via the existing mechanism — or, if that
     connection doesn't already exist cleanly, this is reported as an
     explicit gap rather than faked.
  7. spvs.mgmt_fee_pct/carry_pct are unchanged and still readable
     exactly as before this sprint — additive-first is proven, not
     just claimed.
  8. Cross-org isolation on both tables via app_service.
  9. No table's row count differs from its pre-test count after the
     script exits.
Report actual results, including every inferred-vs-known distinction
from Task 2's backfill, then stop. Do not proceed to fee43 in this
same run.
