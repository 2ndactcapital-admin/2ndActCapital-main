RLS BATCH A — SOC/RBAC tables (14 tables), verification only.
2 tasks + verification. Discovery already done and Part 1 SQL
already fully applied by Joe directly via Supabase MCP — all 14
tables confirmed to have RLS enabled + exactly 1 policy each.

CONTEXT: 13 tables use the standard direct org_id policy
(profiles, permission_sets, teams, staff_assignments,
restricted_access_grants, restricted_access_audit,
trading_authority_grants, delegate_grants,
external_access_grants, households, doc_category_proposals,
permission_set_permissions, profile_permissions — the latter
two turned out to have their OWN org_id column, denormalized,
so no subquery was needed). household_memberships has NO org_id
of its own — its policy reaches org via an EXISTS subquery
against its parent households row's org_id. This is the
trickiest one in the batch and deserves the most scrutiny in
verification.

STANDING RULES: no interactive prompts; do not touch
DATABASE_URL or which role the app connects as (still bypass
'postgres', unchanged); do not touch any table outside this
batch.

=== TASK 1: Prove it, live, against app_service ===
Connect to app_service (env var Joe supplies at test time, never
hardcoded/committed). For AT LEAST these 4 representative tables
— profiles (direct, simple), permission_set_permissions (direct,
denormalized org_id), household_memberships (INDIRECT via
parent — the important one), and one more of your choosing —
prove: same-org context sees the row, different-org context does
not, super_admin sees regardless of org, neither-context-set
returns zero rows.

=== TASK 2: Confirm production is unaffected ===
Confirm the bypass 'postgres' role's behavior is completely
unchanged across a sample of this batch's tables — still sees
everything regardless of context.

=== VERIFICATION ===
Write verify_rlsA.py (apps/api/scripts/) — pass/fail only, no
interactive prompts, teardown-at-start and teardown-at-end.

Assertions:
  [Y] All 14 tables confirmed to have RLS enabled + >=1 policy
      (query pg_class/pg_policy directly, don't just trust this
      prompt's claim)
  [Y] 4 representative tables x 4 isolation checks each = 16
      assertions (same-org visible / different-org invisible /
      super_admin bypass / neither-set-zero-rows)
  [Y] household_memberships specifically: confirm a membership
      row is visible when the CONTEXT org matches the PARENT
      household's org (not the membership row itself, which has
      no org_id) — this is the one place a subtle bug could hide
  [Y] Bypass role unaffected
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. State DATABASE_URL was not
touched. Push when 100% pass — hold for manual review regardless
of tier.
