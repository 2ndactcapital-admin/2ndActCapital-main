SOC PHASE 5 — Trading authority tiers + maker-checker. 3 tasks
+ verification. Phase 5 of 6.

CONTEXT: trading_authority_grants exists (Part 1 SQL applied) —
per-entity, per-user tier assignment: 'inquiry' | 'limited' |
'full'. This phase does NOT build a new money-movement system —
it adds a HARD constraint to whatever ALREADY handles Altruist/
custodian write-back actions (the Tier-1 status enum:
proposed→approved→dispatched→awaiting-client-consent→
acknowledged-at-custodian→settled/rejected, referenced in prior
design work — find its ACTUAL current implementation first, do
not assume it matches that description exactly).

REGULATORY CONTEXT (do not deviate from this — it's a real
custody-rule distinction, not a style choice):
  - inquiry = view only
  - limited = can propose/initiate trades WITHIN an account, but
    CANNOT direct funds to a third party — does NOT trigger
    custody
  - full = can direct funds to ANY third party — TRIGGERS
    custody (surprise exams, heavier SEC scrutiny)
The maker-checker rule: for ANY action that moves money (however
it's currently represented in the codebase), the person who
PROPOSES/INITIATES and the person who APPROVES must be
DIFFERENT people — enforced as a hard check in code, not a UI
suggestion. This must hold regardless of any user's profile,
permission set, or trading_authority tier — even a 'full'
authority user cannot approve their own proposal.

STANDING RULES: org_id never from request body; Decimal for
money; no interactive prompts; light theme if UI is touched.

=== TASK 1: Discover the real money-movement implementation ===
Find the actual current code handling Altruist/custodian write-
back actions and their status enum. Report:
  - The real table/model name and its actual status column
    values (confirm they match, or differ from, the
    proposed/approved/dispatched/... description above)
  - Whether there is ALREADY any check preventing the same user
    from proposing and approving, or whether this is currently
    unenforced
  - Whether trading_authority_grants needs to be checked at the
    point of PROPOSING a money-movement action (i.e. does
    someone need 'limited' or 'full' tier to even propose one,
    while 'inquiry' cannot)

=== TASK 2: Enforce maker-checker as a hard constraint ===
Based on Task 1's findings, add the actual enforcement:
  - At the database level if a suitable column exists (e.g. a
    CHECK or trigger comparing a "proposed_by" column against an
    "approved_by" column, if both exist as real columns) OR at
    the application level in whatever service function currently
    handles the approval step — whichever is the correct fit
    given the REAL schema discovered in Task 1, not a guessed one
  - The check must reject an approval attempt where approved_by
    would equal proposed_by, regardless of that user's role,
    profile, or trading_authority tier
  - Also enforce: proposing a money-movement action that would
    require 'full' authority tier (third-party fund movement)
    is rejected for a user whose trading_authority_grants tier
    for that entity is 'inquiry' or 'limited'

=== TASK 3: Minimal admin UI for trading_authority_grants ===
A simple screen (or extension) for assigning a user's trading
authority tier per entity. Functional, not polished.

=== VERIFICATION ===
Write verify_soc5.py (apps/api/scripts/), same pattern as prior
verify scripts — pass/fail only, no interactive prompts,
idempotent, teardown-at-start and teardown-at-end.

Assertions to include:
  [Y] trading_authority_grants exists matching the snapshot,
      CHECK constraint rejects an invalid tier value
  [Y] Report Task 1's findings on the real money-movement
      implementation (table/columns/existing enforcement state)
  [Y] Maker-checker: an attempt to approve a money-movement
      action where approved_by = proposed_by is REJECTED
  [Y] Maker-checker: an attempt where approved_by != proposed_by
      SUCCEEDS (confirming the check isn't overly broad)
  [Y] A user with 'inquiry' tier attempting to PROPOSE a money-
      movement action for that entity is REJECTED
  [Y] A user with 'limited' or 'full' tier CAN propose (confirm
      at least one of these succeeds)
  [Y] Teardown: zero leftover rows, confirm via count(*)

Report each assertion explicitly (pass/fail). If Task 1 finds
the actual money-movement implementation differs significantly
from what this prompt assumed, STOP and report the discrepancy
clearly rather than forcing a mismatched constraint onto the
wrong table. Push when 100% pass.

