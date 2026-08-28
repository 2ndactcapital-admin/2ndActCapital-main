FEE MODULE — SPRINT fee34 (fee schedule catalog). 3 tasks + verification.
Part 1 SQL (fee_schedules, fee_schedule_tiers, fee_assignments,
fee_exclusions, fee_discounts, fee_credits) is already applied by Joe
directly via Supabase MCP — confirm it live before writing any code,
do not re-create it.

CONTEXT, settled, do not re-derive: fee_assignments.scope_type covers
ACCOUNT (fee31), BILLING_GROUP (fee33), HOUSEHOLD (pre-existing),
ENTITY, and ORG_DEFAULT. Precedence resolves most-specific first:
ACCOUNT < BILLING_GROUP < HOUSEHOLD < ENTITY < ORG_DEFAULT. This
mirrors the precedence pattern portfolio_precedence.py already uses
for household-vs-org source resolution (fee32) — same shape, applied
here to schedule assignment instead of data-source resolution.

OUT OF SCOPE: the calculation engine (fee35), fee runs (fee36),
anything that actually computes a dollar amount. This sprint builds
the catalog and its validation only — a schedule can be created,
versioned, and assigned, but nothing reads one to produce a bill yet.
No Altruist-API-shaped work.

STANDING RULES: org_id never from request bodies. Decimal for money
(numeric columns are already Decimal-safe; keep application code the
same way — no float anywhere fee amounts pass through, including
intermediate calculations in the validation service). No interactive
prompts. Additive-first migrations (this sprint's Part 1 already is).
Light theme, 2nd Act Signature palette from org_settings for any UI.

=== TASK 1: Discover, don't assume ===
Query the live, deployed shape of all six new tables — columns,
CHECK constraints, RLS policies — exactly as applied. Confirm
documents(id) is real and fee_assignments.agreement_document_id
resolves against it. Confirm accounts, households, and billing_groups
(fee31/fee33) are queryable for scope validation. Report findings,
including anything that conflicts with this prompt's description of
the tables, before writing any code.

=== TASK 2: Validation service (pure, no I/O) ===
Write the validation module fee_schedules will be checked against
before any status transition to APPROVED. Rules, all must be checked,
all must produce a clear, typed error naming the specific field/tier
at fault (not a generic ValueError):
  - fee_schedule_tiers for a given schedule are contiguous and
    non-overlapping (each tier's lower_bound equals the previous
    tier's upper_bound; exactly one tier has a NULL upper_bound and
    it must be the highest tier_seq).
  - minimum_fee requires minimum_fee_scope (DB constraint already
    enforces this — the service should give a better error message
    pointing at the field, not surface a raw constraint violation).
  - REDUCED_RATE exclusions require alt_fee_schedule_id (same: DB
    already enforces, service should give a clean error).
  - FLAT exclusions require flat_amount (same).
  - Every fee_exclusion requires a non-empty reason (DB enforces
    NOT NULL; service should reject empty-string, which NOT NULL
    does not catch).
  - Every fee_discount requires approved_by (DB enforces NOT NULL
    already; nothing further needed unless you find a gap).
  - fee_schedules.ordering_policy, if customized away from the
    default, must be a permutation of exactly
    ["EXCLUSIONS","TIERS","DISCOUNTS","CREDITS","MINIMUM","MAXIMUM"]
    — no missing steps, no duplicates, no invented steps.
This module must be callable with zero database access (pass it a
schedule + its tiers as plain data, get a list of validation errors
back) so it can be unit-tested with fixtures and later reused by the
calculation engine (fee35) without re-implementing the same checks.

=== TASK 3: Schedule CRUD + versioning ===
Endpoints/service functions to create a schedule (status=DRAFT),
edit a DRAFT schedule in place, and submit for approval (runs Task
2's validation; only an all-clear transitions to APPROVED). Editing
an APPROVED schedule must NOT mutate it — it creates version N+1 as a
new DRAFT row (same code, version+1), leaving the APPROVED version N
row untouched and still resolvable by any existing fee_assignment
that points at it. A RETIRED schedule cannot be edited or newly
assigned, but existing assignments pointing at it are not disturbed.
Assignment: create/end a fee_assignment for a given scope, enforcing
the scope_id_required constraint's intent in a clean error message,
and — the one cross-scope integrity check worth adding here — when
scope_type='BILLING_GROUP', confirm the referenced billing_groups.id
actually exists and is not itself closed (system_to IS NULL), same
pattern as fee32's AccountLinkError for a stale/foreign account_id.

=== VERIFICATION ===
Write scripts/verify_fee34.py — pass/fail only, no interactive
prompts, app_service for RLS checks, teardown discipline (exact
before/after row counts on every table touched).
Assert:
  1. All six tables deployed, RLS on, exactly the expected policy
     shape, all CHECK constraints present and matching this prompt.
  2. Tier contiguity validation: a correct tier set passes; a gap, an
     overlap, and a schedule with two NULL-upper-bound tiers all fail
     with clear, distinct, typed errors.
  3. Editing a DRAFT schedule mutates it in place (same id); editing
     an APPROVED schedule creates a new row at version+1 and leaves
     the original row and its existing fee_assignments untouched.
  4. Submitting a schedule with a validation failure (any one of
     Task 2's rules) is refused and stays DRAFT; fixing the one
     flagged issue and resubmitting succeeds.
  5. Assigning a schedule to a BILLING_GROUP scope_id that doesn't
     exist, or that's closed, is refused with a clear error; a real,
     open billing_groups.id succeeds.
  6. Precedence resolves correctly: an ACCOUNT-level assignment wins
     over a HOUSEHOLD-level assignment for the same account, which
     wins over ORG_DEFAULT — prove with three assignments in place at
     once, not just two.
  7. Cross-org isolation on all six tables via app_service.
  8. No table's row count differs from its pre-test count after the
     script exits.
Report actual results, then stop. Do not proceed to fee35 in this
same run.
