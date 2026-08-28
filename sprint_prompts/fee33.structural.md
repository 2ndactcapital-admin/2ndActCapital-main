FEE MODULE — SPRINT fee33 (billing groups). 2 tasks + verification.
Prerequisite for the fee schedule catalog (fee34), which needs a
BILLING_GROUP scope distinct from HOUSEHOLD to assign a schedule to.
Part 1 SQL (billing_groups, billing_group_members) is already applied
by Joe directly via Supabase MCP — confirm it live before writing any
code, do not re-create it.

CONTEXT: billing_groups is deliberately NOT households. household is
a CRM/relationship concept; a billing group is the breakpoint
aggregation unit (which accounts' values sum together to determine
tier). They diverge in real cases -- a trust reported with the family
but billed standalone; two households wanting one combined breakpoint.
Do not conflate them or shortcut by deriving billing groups from
households automatically for anything other than a sensible default.

OUT OF SCOPE: fee_schedules, fee_assignments, anything about actually
computing a fee. This sprint only builds the aggregation container and
its membership. No Altruist-API-shaped work.

STANDING RULES: org_id never from request bodies. No interactive
prompts. Additive-first. Light theme only, 2nd Act Signature palette
from org_settings, if any UI is touched (a "manage billing groups"
admin screen is in scope for task 2's UI half if time allows, but the
constraint enforcement in task 2's backend half is the part that must
not be skipped).

=== TASK 1: Discover, don't assume ===
Query the live, deployed shape of billing_groups and
billing_group_members exactly as applied (columns, constraints, RLS
policies) -- do not assume this prompt's Part 1 SQL is what's actually
live. Also check: does anything in the existing accounts/households
schema already imply a natural default billing group per household
(e.g. should creating a household auto-create a default BREAKPOINT
group containing its accounts)? Report findings before writing code.

=== TASK 2: Membership integrity + a minimal admin UI ===
Backend (required): enforce "an account may belong to at most one
ACTIVE group of group_type='BREAKPOINT' at a time" in application
code (a service function called on every insert/update to
billing_group_members), since this can't be a simple unique index --
it depends on the referenced billing_groups.group_type. STATEMENT and
PAYER type groups have no such restriction; an account can be in
multiple statement or payer groups simultaneously by design (a joint
account can appear on two different statement groupings). Write a
clear, typed error (not a generic exception) when the BREAKPOINT
constraint would be violated, naming the account and the existing
group it's already in.
Admin UI (best effort within the sprint's time budget, do not let it
crowd out the backend integrity requirement): a simple screen to
create a billing group, set its type, optionally link a household, and
add/remove member accounts, using the existing DataGrid component.

=== VERIFICATION ===
Write scripts/verify_fee33.py -- pass/fail only, no interactive
prompts, app_service connection for any RLS check, teardown discipline
(restore exact before/after row counts on every table touched,
following the fee31/fee32 precedent).
Assert:
  1. billing_groups / billing_group_members exist with RLS enabled and
     exactly the expected policy shape.
  2. An account can be added to a BREAKPOINT group; adding the SAME
     account to a SECOND BREAKPOINT group is rejected with a clear,
     typed error naming both groups.
  3. The SAME account CAN be added to a STATEMENT group and a PAYER
     group simultaneously, and to a BREAKPOINT group at the same time
     as those -- only BREAKPOINT-vs-BREAKPOINT is restricted.
  4. Removing an account from its BREAKPOINT group (setting
     valid_to/system_to, not a hard delete) correctly frees it to join
     a different BREAKPOINT group afterward.
  5. A billing group with household_id = NULL is valid and behaves
     identically to one with a household_id set, for every check
     above.
  6. Cross-org isolation on both new tables via app_service, same
     pattern as fee31 check 5 / fee32 check 7.
  7. No table's row count differs from its pre-test count after the
     script exits.
Report actual results, then stop. Do not proceed to fee34 in this
same run.
