RLS BATCH B — Entity/CRM tables (15 tables), verification only.
2 tasks + verification. Part 1 SQL already fully applied by Joe
directly via Supabase MCP — all 15 tables confirmed RLS enabled
+ exactly 1 policy each. All 15 turned out to have their OWN
org_id column directly (denormalized) — no indirect/subquery
policies needed in this batch, simpler than Batch A.

CONTEXT: entities, entity_addresses, entity_attributes,
entity_briefs, entity_document_tags, entity_documents,
entity_employment, entity_group_members, entity_groups,
entity_holdings, entity_notes, entity_ownership,
entity_relationships, entity_social_profiles, entity_tax_ids —
all use the standard direct org_id policy (same pattern as
Batch A's 13 direct tables).

IMPORTANT: entity_relationships is uniquely load-bearing — it is
the SAME table resolve_entity_set (from SOC Phase 1) walks for
MEMBER-side visibility (ownership/beneficiary look-through).
This batch's new org-scoped RLS policy must be proven compatible
with that existing function, not just tested in isolation.

STANDING RULES: no interactive prompts; do not touch
DATABASE_URL or which role the app connects as (still bypass
'postgres', unchanged); do not touch any table outside this
batch.

=== TASK 1: Prove it, live, against app_service ===
Connect to app_service (env var Joe supplies at test time, never
hardcoded/committed). For AT LEAST 4 representative tables —
entities (the root), entity_relationships (the sensitive one,
see above), entity_holdings, and one more of your choosing —
prove: same-org context sees the row, different-org context
does not, super_admin sees regardless of org, neither-context-
set returns zero rows.

=== TASK 2: Confirm resolve_entity_set still works correctly
under the new entity_relationships policy ===
Using app_service with a normal org context set (simulating a
real authenticated request, not a bootstrap/no-context case),
call resolve_entity_set (or whatever it's actually named — find
it first, don't assume) for a member with both an OWNERSHIP edge
and a BENEFICIARY edge (from SOC Phase 1) to different entities,
all within the SAME org. Confirm the function still correctly
returns the full look-through set — the new RLS policy on
entity_relationships must not silently break or truncate this
existing, already-proven logic. Also confirm the bypass
'postgres' role's behavior is completely unchanged.

=== VERIFICATION ===
Write verify_rlsB.py (apps/api/scripts/) — pass/fail only, no
interactive prompts, teardown-at-start and teardown-at-end.

Assertions:
  [Y] All 15 tables confirmed to have RLS enabled + >=1 policy
      (query pg_class/pg_policy directly)
  [Y] 4 representative tables x 4 isolation checks each = 16
      assertions (same-org visible / different-org invisible /
      super_admin bypass / neither-set-zero-rows)
  [Y] resolve_entity_set called under app_service with normal org
      context correctly returns both an ownership-reached AND a
      beneficiary-reached entity, matching what it would return
      under the bypass role for the same test data (no silent
      truncation from the new policy)
  [Y] Bypass role (postgres) unaffected across a sample of this
      batch's tables
  [Y] Teardown: zero leftover rows

Report each assertion explicitly. State DATABASE_URL was not
touched. Push when 100% pass — hold for manual review regardless
of tier.
