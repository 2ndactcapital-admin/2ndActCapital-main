RLS BATCH C — Financial/ledger tables (13 tables), verification
only. 3 tasks + verification. Part 1 SQL already fully applied
by Joe directly via Supabase MCP. This is the MOST sensitive
batch so far (real financial data) — be thorough, do not rush
verification.

CONTEXT — three different policy shapes in this batch, all
already applied:
  (a) STANDARD DIRECT (10 tables, own org_id, NOT NULL):
      chart_of_accounts, journal_entries, ownership_change_log,
      posting_templates, spv_documents, spv_status_history,
      spv_subscriptions, spv_transaction_allocations,
      spv_transactions, spvs
  (b) INDIRECT (1 table): journal_lines has NO org_id of its
      own — gated via an EXISTS subquery against its PARENT
      journal_entries row's org_id (joined on entry_id)
  (c) GLOBAL-READ / ORG-WRITE (2 tables): fx_rates and
      transaction_types are CONFIRMED 100% NULL org_id today —
      genuinely global platform reference data (5 fx_rates rows,
      16 ILPA-aligned transaction_types rows). Their policy is
      ASYMMETRIC: READ allows org_id IS NULL (global) OR own-org
      OR super_admin; WRITE (the WITH CHECK clause) does NOT
      allow org_id IS NULL for a non-super-admin — a regular org
      can only write rows scoped to their OWN org_id, never a
      global/NULL row. This protects platform reference data
      from being edited by a tenant.

STANDING RULES: no interactive prompts; do not touch
DATABASE_URL or which role the app connects as (still bypass
'postgres', unchanged); do not touch any table outside this
batch.

=== TASK 1: Prove the standard + indirect cases, live ===
Connect to app_service (env var Joe supplies at test time).
For spvs (direct) and journal_lines (indirect, via parent),
prove: same-org visible, different-org invisible, super_admin
bypass works, neither-context-set returns zero rows. For
journal_lines specifically, confirm gating is by the PARENT
journal_entries.org_id, not any column on journal_lines itself
(same style of proof as Batch A's household_memberships test).

=== TASK 2: Prove the global-reference case, live — the
important one ===
For fx_rates AND transaction_types, prove:
  - ANY org context (org A, or org B, or no context at all
    beyond just being authenticated) can READ the existing
    global (NULL org_id) rows — confirm the full expected count
    is visible regardless of which org is set
  - A NON-super-admin user attempting to INSERT or UPDATE a row
    with org_id = NULL is REJECTED by the WITH CHECK clause
  - A non-super-admin user CAN insert/update a row scoped to
    THEIR OWN org_id (org-specific override, if that's ever used)
  - A super_admin CAN write a NULL-org (global) row
This is the trickiest logic in the whole RLS rollout so far —
do not skip or shortcut any of these four sub-checks.

=== TASK 3: Confirm production is unaffected ===
Bypass role (postgres) behavior completely unchanged across a
sample of this batch's tables, including fx_rates/
transaction_types (still sees everything with no context set,
proving nothing about production changed).

=== VERIFICATION ===
Write verify_rlsC.py (apps/api/scripts/) — pass/fail only, no
interactive prompts, teardown-at-start and teardown-at-end.
Since fx_rates/transaction_types have REAL existing data (5 and
16 rows respectively), be careful: any test fixture rows you add
for isolation testing must be clearly test-only and fully torn
down — do not leave test fx rates or transaction types behind,
and do not modify/delete any of the real existing 5 or 16 rows.

Assertions:
  [Y] All 13 tables confirmed RLS enabled + >=1 policy
  [Y] spvs: same-org/different-org/super_admin/neither (4)
  [Y] journal_lines: same as above, but confirm gating is via
      the PARENT journal_entries row specifically (4)
  [Y] fx_rates: global rows readable under any org context (1);
      non-super-admin blocked from writing a NULL-org row (1);
      non-super-admin CAN write their own-org row (1);
      super_admin CAN write a NULL-org row (1)
  [Y] transaction_types: same 4 checks as fx_rates
  [Y] The real existing 5 fx_rates and 16 transaction_types rows
      are confirmed UNTOUCHED/unchanged after the test run
  [Y] Bypass role (postgres) unaffected across a sample
  [Y] Teardown: zero leftover TEST rows (not the real reference
      data, which must remain exactly as it was)

Report each assertion explicitly. State DATABASE_URL was not
touched. Push when 100% pass — hold for manual review regardless
of tier, given this is the most sensitive batch so far.
