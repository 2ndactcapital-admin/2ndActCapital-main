FEE MODULE — SPRINT fee37 (cost model). 3 tasks + verification. Part 1
SQL (cost_providers, cost_schedules, cost_pass_through_policies,
cost_events) is already applied by Joe directly via Supabase MCP —
confirm it live before writing any code, do not re-create it.

CONTEXT, settled: this is the first sprint to build the cost side of
the ledger at all. Nothing before this touches vendor/provider cost.
revenue_events (fee39) does not exist yet — cost_events.linked_revenue_event_id
has no FK constraint on purpose, added when fee39 lands.

RESEARCH CAVEAT: the Altruist rate card below is from the original
design-doc research (no live web search available in that session) and
may have drifted. Seed it as data with source_url/source_verified_on
populated, and flag in your own report that these numbers need a human
to re-verify against altruist.com before being trusted for a real bill
— do not present seeded rates as currently accurate.

Rate card to seed (re-verify before trusting):
  ALTRUIST_ONE_SUBSCRIPTION: 0.01%/month per household (12 bps/yr),
    minimum $1/month per account — the exact interaction between the
    household bps and the per-account minimum was flagged as ambiguous
    in the original design doc (does the $1/account minimum apply
    IN ADDITION to the bps, or is it a floor via max()?). Seed both
    readings as separate rows if the schema allows expressing the
    ambiguity, or seed the more conservative (higher-cost) reading and
    flag the ambiguity explicitly rather than silently picking one.
  DIRECT_INDEXING: 12 bps, $2,000 minimum (both tiers)
  MODEL_MARKETPLACE: 0 bps on the 350+ included models, 10-15 bps
    (or up to 15 bps DISCOUNT under Altruist One) on paid third-party
    models — this needs two cost_schedules rows, not one, since the
    included and paid tiers are genuinely different cost structures.
  MARGIN_SPREAD: 6.25% non-subscriber, 4.00-5.25% Altruist One
    (tiered — seed as a range or the top/bottom if the schema can't
    express a tier ladder cleanly; do not silently collapse to one
    number).
  CASH_SPREAD (the yield UPLIFT, i.e. a benefit not a cost — decide
    whether this belongs in cost_schedules at all, since it is a
    revenue/benefit line for the evaluator's use (fee38), not a cost
    the firm pays. Flag this as a modeling question rather than
    guessing: possibly this whole category belongs in fee38's own
    evaluator inputs, not in cost_schedules.)

OUT OF SCOPE: the Altruist One evaluator itself (fee38 consumes this
sprint's output). revenue_events / profitability rollups (fee39).
Anything Altruist-API-shaped — this is rate-card DATA, never a live
connection.

STANDING RULES: org_id never from request bodies. Decimal everywhere.
No interactive prompts. Additive-first.

=== TASK 1: Discover, don't assume ===
Query live: all four new tables exactly as deployed. Confirm
cost_events has no linked_revenue_event_id FK yet (deliberate). Check
whether anything in the existing schema (org_settings, reference_data)
already has a place for vendor rate cards that this sprint should
build on rather than duplicate.

=== TASK 2: Seed the Altruist provider profile ===
Create the ALTRUIST cost_providers row and its cost_schedules rows per
the rate card above, with the ambiguities flagged as stated rather
than resolved by guessing. Every row gets source_url and
source_verified_on populated honestly (today's date for
source_verified_on, since that's when this sprint entered it — not a
claim that Altruist's own page was re-checked today unless you
actually did check it).

=== TASK 3: Pass-through policy engine ===
Given a cost_schedule and a scope (account/household/billing_group),
resolve the applicable cost_pass_through_policy (falling back to
ORG_DEFAULT when no more-specific policy exists — same precedence
pattern as fee_assignments). Compute, for a given cost amount: the
resulting cost_event (always recorded, always the real cost) and,
when policy != ABSORB, the corresponding revenue figure implied by
pass_through_rate — do NOT write a revenue_event yet (fee39's table),
but return the number so fee39 has something concrete to consume.
MARKUP policies must have disclosure_acknowledged_by/_at populated
before the policy can be marked active — enforce this as a real gate,
not just the DB's NOT NULL-adjacent check.

=== VERIFICATION ===
Write scripts/verify_fee37.py — pass/fail only, app_service for RLS,
teardown discipline.
Assert:
  1. All four tables deployed, RLS on, expected policy/constraint shape.
  2. The Altruist provider and its schedules are seeded, each with a
     non-null source_url and source_verified_on.
  3. Each of the four pass-through policies (ABSORB/PASS_FULL/
     PASS_PARTIAL/MARKUP) produces the correct cost_event and implied
     revenue figure on a fixture.
  4. A MARKUP policy without disclosure acknowledgement is refused
     from becoming active.
  5. Precedence resolves correctly: an ACCOUNT-level pass-through
     policy overrides an ORG_DEFAULT one for the same cost_schedule.
  6. Cross-org isolation on all four tables via app_service.
  7. No table's row count differs from its pre-test count after the
     script exits.
Report actual results, including your Task 1 findings on the rate-card
ambiguities, then stop. Do not proceed to fee38 in this same run.
