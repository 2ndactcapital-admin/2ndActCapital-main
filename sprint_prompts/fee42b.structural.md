FEE MODULE — SPRINT fee42b (carry distribution engine). 3 tasks +
verification. Part 1 SQL (spv_carry_runs, spv_carry_run_lines, both
immutability triggers) is already applied by Joe directly via Supabase
MCP — confirm it live before writing any code.

CONTEXT, settled, do not re-derive:
- This is the FIRST real subscriber to event_type='spv_realization'
  (event-emission sprint, already merged). Do not poll spv_transactions
  directly for realizations — register a real workflow_trigger and
  consume the domain_events payload the emitter already proved carries
  per-investor allocation amounts (checks 10a-10d of that sprint).
- v_capital_accounts (a GL view, already fixed for security_invoker)
  is the source for each investor's cumulative paid-in capital and
  cumulative distributions-to-date — the numbers a preferred-return/
  hurdle calculation needs. Do NOT build a second cumulative-balance
  table; read this view. If it does not carry everything needed
  (e.g. per-SPV-class breakdown), report the gap in Task 1 rather than
  approximating silently.
- carry_pct/hurdle_pct/hurdle_type/catchup_pct/carry_basis/
  clawback_applies all come from fee42's spv_fee_terms (resolved
  through spv_fee_side_letters if an investor has one — reuse
  SFT.resolve_terms_for_entity from fee42, do not reimplement
  resolution).
- The shape mirrors fee_runs/fee_run_lines deliberately: DRAFT ->
  PREVIEW -> ADVISOR_APPROVED -> COMPLIANCE_APPROVED -> POSTED,
  immutable once posted, maker-checker through assistant_activities
  exactly as fee36 does it (related_type='spv_carry_run'). A workflow-
  triggered run creates a DRAFT/PREVIEW only — it never auto-posts.
  This is Tier-1 propose-never-dispose, not Tier-3 auto-execution.
- spv_carry_run_lines_balance_check (net_to_lp + carry_to_gp =
  gross_gain_allocated) is a real database invariant — the waterfall
  arithmetic must reconcile exactly, in Decimal, or the row cannot be
  written at all.
- Carry math itself: return of capital first (against this investor's
  unreturned paid-in capital), then preferred return up to hurdle_pct
  (HARD vs SOFT hurdle changes whether the GP catches up on the WHOLE
  preferred return or only the excess — confirm this distinction
  against real PE waterfall convention in Task 1, do not guess which
  is which), then GP catchup to catchup_pct, then the residual split
  at carry_pct/(1-carry_pct) between GP and LP. DEAL_BY_DEAL vs
  WHOLE_FUND carry_basis changes whether this calculation nets against
  ONLY this realization or against the vehicle's cumulative gain
  history — WHOLE_FUND is materially harder (needs prior realizations'
  cumulative state) and may be a real gap to report rather than force
  if v_capital_accounts can't support it cleanly; DEAL_BY_DEAL against
  a single realization is the achievable core of this sprint.

OUT OF SCOPE: clawback EXECUTION (recording that clawback_applies=true
and the theoretical clawback amount is in scope; actually clawing back
a prior distribution from a GP is a different, later mechanism).
Posting a carry run's approved numbers to the GL (GL posting is still
open question #3, fee43's territory). Any Altruist-API-shaped work.

STANDING RULES: org_id never from request bodies. Decimal everywhere,
including every intermediate waterfall step — this is exactly the
kind of multi-step calculation where a float would compound error
across tiers. No interactive prompts. The workflow-triggered proposal
path must not bypass maker-checker under any circumstance, including
being triggered automatically rather than by a human action.

=== TASK 1: Discover, don't assume ===
Query live: spv_carry_runs/spv_carry_run_lines exactly as deployed.
Read v_capital_accounts' real columns and confirm exactly what
cumulative figures it provides per investor per SPV (paid-in capital,
distributions-to-date, by class if applicable). Confirm the real
HARD-vs-SOFT hurdle convention this codebase should follow (or, if
undocumented anywhere, the standard PE convention: HARD = GP receives
no catchup on the preferred return itself, only on amounts above it;
SOFT = GP catches up on the full preferred return once the hurdle is
cleared — verify this is the correct pairing before coding it, hurdle
conventions are easy to get backwards). Confirm the real mechanism
from the event-emission sprint for registering a workflow_trigger and
reading a workflow_run's context. Report findings, especially any gap
between what WHOLE_FUND carry_basis would need and what
v_capital_accounts can actually supply, before writing code.

=== TASK 2: Pure waterfall calculation engine ===
Zero-database-access function (same discipline as fee35): given an
investor's gross_gain_allocated, their cumulative paid-in/distributed
figures, and the resolved spv_fee_terms (hurdle_pct, hurdle_type,
catchup_pct, carry_pct, carry_basis), compute return_of_capital,
preferred_return, gp_catchup, carry_to_gp, net_to_lp — each traced in
calc_detail with the tier boundaries and the running balance at each
step, same audit-trail standard as fee35's calc_detail. The four
tiers must tile the gain exactly (no gap, no double-count,
reconciling to the balance_check constraint). Golden cases, hand-
computed: (1) a realization fully absorbed by return of capital, no
preferred return reached, zero carry; (2) a realization crossing into
preferred return but not clearing the hurdle, zero carry; (3) a
realization clearing the hurdle with a HARD hurdle, GP catchup applies
only above the hurdle; (4) the same fixture with a SOFT hurdle,
producing a DIFFERENT carry_to_gp than case 3 — proving the
distinction is real, not decorative; (5) a realization large enough to
fully catch up the GP and split the residual at carry_pct.

=== TASK 3: Workflow subscription + proposal write path + end-to-end proof ===
Register the real workflow_trigger/workflow_definition pairing (per
Task 1's confirmed mechanism) for event_type='spv_realization'. When
it fires, resolve the triggering domain_event's per-investor payload,
resolve each investor's spv_fee_terms (with side letters), pull
cumulative figures from v_capital_accounts, run Task 2's engine per
investor, and write a DRAFT spv_carry_run + its lines — never further
than DRAFT automatically. Wire the DRAFT -> PREVIEW -> ADVISOR_APPROVED
-> COMPLIANCE_APPROVED -> POSTED lifecycle through assistant_activities,
matching fee36's pattern exactly. Build the real end-to-end proof: post
a real dist_gain spv_transaction, confirm the event fires (reusing the
event-emission sprint's own mechanism), confirm a real spv_carry_run
lands in DRAFT with correct per-investor lines, and confirm it can be
walked through to POSTED via the same approval mechanism fee36 uses.

=== VERIFICATION ===
Write scripts/verify_fee42b.py — pass/fail only, app_service for RLS,
teardown discipline.
Assert:
  1. Both tables deployed, RLS on, expected constraint/policy/trigger
     shape; the balance_check constraint genuinely refuses an
     unreconciled line.
  2. All five golden cases from Task 2 produce hand-computed-exact
     results, with calc_detail showing every tier boundary.
  3. HARD vs SOFT hurdle produce genuinely different carry_to_gp on
     otherwise-identical fixtures (case 3 vs case 4).
  4. Posting a real dist_gain transaction fires the event (reusing
     the event-emission mechanism) and produces a DRAFT spv_carry_run
     with correct lines, without any human action.
  5. The DRAFT run does NOT auto-advance past DRAFT — a workflow
     trigger creates a proposal, never a posted fact.
  6. The full approval chain (advisor, then compliance, then post)
     works exactly as fee36's does, including self-approval refusal.
  7. A POSTED spv_carry_run and its lines genuinely cannot be UPDATEd
     or DELETEd — confirmed by a direct database attempt, not just
     through the service layer.
  8. Every investor's line reconciles: return_of_capital +
     preferred_return + gp_catchup portion + carry split = the
     gross_gain_allocated for that investor, to the cent.
  9. Cross-org isolation on both tables via app_service.
  10. No table's row count differs from its pre-test count after the
      script exits.
Report actual results, including the honest WHOLE_FUND-vs-
DEAL_BY_DEAL gap from Task 1 if v_capital_accounts can't fully support
it, then stop.
