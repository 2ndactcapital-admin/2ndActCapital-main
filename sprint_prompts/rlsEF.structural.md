RLS BATCH E+F (COMBINED, FINAL BATCH) — Assistant/Notifications/
Audit (14 tables) + Config/Reference (4 tables) = 18 tables,
verification only. This is the LAST batch of the entire RLS
rollout. Part 1 SQL already fully applied by Joe directly via
Supabase MCP — all 18 tables confirmed RLS enabled + exactly 1
policy each.

CONTEXT — two policy shapes, both already applied:
  (a) STANDARD DIRECT (17 tables, own org_id NOT NULL):
      assistant_activities, assistant_action_catalog,
      assistant_autonomy_prefs, assistant_conversations,
      profile_conversations, dashboard_briefs, member_todos,
      notifications, notification_recipients,
      notification_delivery_log, user_notification_preferences,
      audit_log, compliance_records, compliance_override_requests,
      config, org_settings, roles
  (b) GLOBAL-READ / ORG-WRITE (1 table): reference_data is
      CONFIRMED 100% NULL org_id today (155 rows across 11
      distinct lists — countries, currencies, months, etc.) —
      genuinely global platform reference data, same asymmetric
      pattern as Batch C's fx_rates/transaction_types. READ
      allows org_id IS NULL (global) OR own-org OR super_admin;
      WRITE (with_check) does NOT allow NULL for a non-super-
      admin.

IMPORTANT: assistant_activities carries the maker-checker
constraint (proposed_by/approved_by/entity_id, same user can
never both propose and approve). This must be proven STILL
correct under the new org-isolation RLS policy, not assumed.

STANDING RULES: no interactive prompts; do not touch
DATABASE_URL or which role the app connects as (still bypass
'postgres', unchanged); do not touch any table outside this
batch.

=== TASK 1: Prove standard isolation, live ===
Connect to app_service (env var Joe supplies at test time, never
hardcoded/committed). For AT LEAST 4 representative tables from
the standard-direct set — assistant_activities, audit_log,
org_settings, and one more of your choosing — prove: same-org
visible, different-org invisible, super_admin bypass works,
neither-context-set returns zero rows.

=== TASK 2: Prove the global-reference case for reference_data ===
Same discipline as Batch C's fx_rates/transaction_types proof:
  - ANY org context can READ the existing global (NULL org_id)
    rows — confirm the full expected count (155 rows / 11 lists)
    is visible regardless of which org is set
  - A non-super-admin attempting to INSERT/UPDATE a row with
    org_id = NULL is REJECTED
  - A non-super-admin CAN insert/update a row scoped to their
    OWN org_id
  - A super_admin CAN write a NULL-org (global) row
  - The real 155 existing rows are confirmed BYTE-FOR-BYTE
    UNCHANGED after the test run (same content-hash proof style
    as Batch C) — any test fixture rows must be clearly test-
    only and fully torn down

=== TASK 3: Prove maker-checker still works under the new
policy ===
Using app_service with normal org context, confirm: a self-
approval attempt on assistant_activities (approved_by =
proposed_by) is STILL correctly rejected; a different-approver
attempt still correctly succeeds.

=== TASK 4: Confirm production is unaffected ===
Bypass role (postgres) behavior completely unchanged across a
sample spanning both policy shapes in this batch.

=== VERIFICATION ===
Write verify_rlsEF.py (apps/api/scripts/) — pass/fail only, no
interactive prompts, teardown-at-start and teardown-at-end. Per
the Batch C lesson: use real Python date/datetime objects for
any date/timestamp test fixtures, never bare strings. Since
reference_data has 155 REAL existing rows, be careful: test
fixture rows must be clearly test-only and fully torn down —
never modify/delete the real 155 rows.

Assertions:
  [Y] All 18 tables confirmed RLS enabled + >=1 policy
  [Y] 4 standard tables x 4 isolation checks each = 16 assertions
  [Y] reference_data: global rows readable under any org (1);
      non-super-admin blocked from writing NULL-org row (1);
      non-super-admin CAN write own-org row (1); super_admin CAN
      write NULL-org row (1)
  [Y] The real 155 reference_data rows confirmed byte-for-byte
      unchanged after the run
  [Y] Maker-checker on assistant_activities: self-approval
      rejected, different-approver succeeds, both under the new
      RLS-context app_service connection
  [Y] Bypass role (postgres) unaffected across a sample spanning
      both policy shapes
  [Y] Teardown: zero leftover TEST rows (real reference_data
      rows must remain exactly 155)

Report each assertion explicitly. State DATABASE_URL was not
touched — this is the LAST batch, but the connection switch is
STILL a separate, deliberate, manual step for later, not part of
this sprint. Push when 100% pass — hold for manual review
regardless of tier.
