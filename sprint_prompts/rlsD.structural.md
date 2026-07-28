RLS BATCH D — Deals/Marketplace tables (11 tables), verification
only. 2 tasks + verification. Part 1 SQL already fully applied
by Joe directly via Supabase MCP — all 11 tables confirmed RLS
enabled + exactly 1 policy each. All 11 have their own org_id
(NOT NULL) — standard direct policy, same pattern as Batch B, no
indirect subqueries or global-reference asymmetry in this batch.

CONTEXT: deals, deal_ai_summaries, deal_documents, deal_interest,
deal_scores, deal_votes, investment_stage_history,
member_investments, investment_profile_answers,
investment_profile_extractions, investment_profile_questions —
all use the standard org_id NULLIF-guarded policy.

STANDING RULES: no interactive prompts; do not touch
DATABASE_URL or which role the app connects as (still bypass
'postgres', unchanged); do not touch any table outside this
batch.

=== TASK 1: Prove it, live, against app_service ===
Connect to app_service (env var Joe supplies at test time, never
hardcoded/committed). For AT LEAST 4 representative tables —
deals (the root/parent), member_investments (the member-facing
side), deal_scores, and one more of your choosing — prove:
same-org context sees the row, different-org context does not,
super_admin sees regardless of org, neither-context-set returns
zero rows.

=== TASK 2: Confirm production is unaffected ===
Bypass role (postgres) behavior completely unchanged across a
sample of this batch's tables — still sees everything regardless
of context.

=== VERIFICATION ===
Write verify_rlsD.py (apps/api/scripts/) — pass/fail only, no
interactive prompts, teardown-at-start and teardown-at-end.

IMPORTANT LESSON FROM BATCH C: verify_rlsC.py had a real bug
where a test fixture date constant was a plain Python string
passed into an asyncpg date-typed query parameter (needs a real
datetime.date object, not a string). Before writing any date/
timestamp test fixtures in this script, double-check you are
using proper Python date/datetime objects, not string literals,
for any column that is a date/timestamp type.

Assertions:
  [Y] All 11 tables confirmed RLS enabled + >=1 policy
  [Y] 4 representative tables x 4 isolation checks each = 16
      assertions (same-org visible / different-org invisible /
      super_admin bypass / neither-set-zero-rows)
  [Y] Bypass role (postgres) unaffected across a sample
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. State DATABASE_URL was not
touched. Push when 100% pass — hold for manual review regardless
of tier.
