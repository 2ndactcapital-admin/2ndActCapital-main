FEE MODULE — SPRINT fee36 (fee runs & approvals). 3 tasks +
verification. Part 1 SQL (fee_runs, fee_run_lines, both immutability
triggers) is already applied by Joe directly via Supabase MCP —
confirm it live before writing any code, do not re-create it.

CONTEXT, settled, do not re-derive:
- Maker-checker is NOT a new table. public.assistant_activities
  (confirmed live: status, proposed_by, approved_by, related_type,
  related_id, payload, result jsonb, reversible, undo_token) is the
  real mechanism. A fee_run's approval lifecycle writes an
  assistant_activities row with related_type='fee_run',
  related_id=fee_runs.id, and fee_runs.status advances in lockstep
  with that row's own status — read assistant_activities' actual
  status vocabulary live before wiring this, this prompt does not
  know it precisely.
- fee35 (services/fee_calc.py, services/fee_calc_inputs.py) is the
  ONLY thing that computes a fee. This sprint calls it; it does not
  reimplement any part of the pipeline, tiering, or ordering_policy
  logic. If fee35's interface doesn't cleanly support what this
  sprint needs, that is a finding to report, not a reason to
  duplicate logic here.
- fee35 findings F1 and F4 are this sprint's to resolve:
    F1: fee_credits has no amount column. CreditInput.basis_amount
        is required by the engine. This sprint must decide, and
        implement, where that number actually comes from for each
        credit_source (e.g. SPV_MGMT_FEE_OFFSET's basis is presumably
        the SPV management fee amount for the same period — locate
        the real source of that number in the live schema, likely
        something in the spv_subscriptions/journal_entries family,
        before assuming a shape).
    F4: accounts has no billing_group_id. AccountInput.billing_group_id
        is caller-resolved. This sprint is the caller — resolve it via
        billing_group_members for the account being billed at the time
        of the run, and raise the engine's own GroupScopeMissingError
        (do not swallow it) when a HOUSEHOLD... no, when a
        BILLING_GROUP-scoped minimum applies and the account has no
        active breakpoint membership.
- calculation_snapshot_hash on fee_runs must be a real hash of the
  actual schedules/assignments/exclusions/discounts/credits/balances
  used to produce that run's numbers — not a placeholder. A future
  re-run of the same period must be checkable against this hash to
  confirm nothing upstream (a retroactively-corrected balance, a
  changed assignment) silently changed the inputs.

OUT OF SCOPE: invoices, receipts, reconciliation (fee42 in the
original design doc's numbering). GL posting — flag it as a decision
needed (which books RIA revenue posts to, per the design doc's open
question #3) but do not guess and wire posting_templates without an
answer; emit fee_run_lines regardless, leave the ledger-posting hook
as a clearly-marked stub if the question isn't answered before this
sprint starts. No Altruist-API-shaped work.

STANDING RULES: org_id never from request bodies. Decimal everywhere
(fee_run_lines' numeric columns are Decimal-safe already; keep every
intermediate value the same way, matching fee35's own discipline). No
interactive prompts. This sprint DOES touch the database and DOES
write real rows, unlike fee35 — but the arithmetic itself must still
route entirely through fee_calc, never be reimplemented inline.

=== TASK 1: Discover, don't assume ===
Query live: fee_runs/fee_run_lines exactly as deployed (columns,
constraints, both triggers, RLS). assistant_activities' real status
vocabulary and any existing CHECK constraint on it. billing_group_members
current shape (fee33) for resolving F4. Whatever table actually holds
an SPV's period management-fee amount (search live schema, do not
assume spv_subscriptions has it) for resolving F1's basis_amount.
Report all of this, and explicitly report your resolution plan for F1
and F4, before writing any code.

=== TASK 2: Run lifecycle + maker-checker wiring ===
Build the run lifecycle: DRAFT (created, no lines yet) → PREVIEW (runs
fee_calc for every in-scope account, writes fee_run_lines, computes
calculation_snapshot_hash, engine_version) → ADVISOR_APPROVED →
COMPLIANCE_APPROVED → POSTED (only transition that's blocked by the
DB trigger from here on). A PREVIEW can be re-run (replacing its
DRAFT-status lines) as many times as needed before approval — this is
NOT the immutable state, only POSTED and beyond are. Wire
ADVISOR_APPROVED/COMPLIANCE_APPROVED transitions through
assistant_activities rather than a bespoke approval flag on fee_runs
itself. Include a variance report: for a run in PREVIEW, compare each
line's net_fee against the prior period's POSTED line for the same
account (if one exists), sorted by absolute dollar change descending
— this is what an advisor actually reviews before approving, not the
raw line list.

=== TASK 3: Reversal + re-run reproducibility ===
Implement REVERSAL: a run_type='REVERSAL' fee_run whose lines negate
a target POSTED run's lines exactly (net_fee sign-flipped, same
account/schedule/period), referencing reverses_run_id. Reversing a
run does not delete or mutate the original — both remain, the
reversal is a new, separately-posted set of rows. Prove
reproducibility: re-running fee_calc today against the EXACT inputs
(schedules/assignments/balances as they existed) that produced an
already-POSTED run's numbers must reproduce those numbers to the
cent — this is what calculation_snapshot_hash exists to make
checkable, so implement the actual verification of the hash, not just
its storage.

=== VERIFICATION ===
Write scripts/verify_fee36.py — pass/fail only, no interactive
prompts, app_service for RLS checks, teardown discipline (exact
before/after row counts on every table touched, per fee31-35
precedent).
Assert:
  1. Both tables deployed, RLS on, expected policy shape, both
     immutability triggers present and firing (attempt an UPDATE on a
     POSTED fee_run and a POSTED-run's fee_run_line; both must be
     refused by the trigger, not by application code).
  2. A DRAFT run can be PREVIEWed repeatedly, replacing its lines each
     time, with no error and no orphaned old lines left behind.
  3. Every fee_run_line's net_fee, for a realistic multi-account
     PREVIEW, matches what calling fee_calc directly on the same
     inputs produces — this sprint's numbers ARE fee35's numbers, not
     a reimplementation that happens to agree on simple cases.
  4. The ADVISOR_APPROVED and COMPLIANCE_APPROVED transitions each
     correspond to a real assistant_activities row with the correct
     related_type/related_id and status, not a bespoke flag.
  5. F1's basis_amount resolution: an SPV_MGMT_FEE_OFFSET credit
     produces the correct credited dollar amount against whatever
     real basis source Task 1 identified — prove against a real
     fixture number, not a stub.
  6. F4's billing_group_id resolution: a BILLING_GROUP-scoped minimum
     resolves correctly for an account with an active breakpoint
     membership, and raises a clear, typed error (not a silent
     fallback to account-scoped) for one without.
  7. A POSTED fee_run and its lines genuinely cannot be UPDATEd or
     DELETEd — confirmed by attempting it directly against the
     database, not just through the application's own service layer.
  8. REVERSAL produces lines that sum to exactly zero against the
     original run's lines, account by account.
  9. calculation_snapshot_hash verification: re-running fee_calc
     against reconstructed inputs for an already-POSTED run reproduces
     the same hash and the same net_fee to the cent; a deliberately
     altered input (e.g. a changed exclusion) produces a DIFFERENT
     hash, proving the hash is sensitive to what it claims to cover.
  10. Cross-org isolation on both tables via app_service.
  11. No table's row count differs from its pre-test count after the
      script exits.
Report actual results, then stop. Do not proceed to fee37/fee42 in
this same run.
