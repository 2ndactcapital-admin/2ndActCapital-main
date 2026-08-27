FEE MODULE — SPRINT fee31 (account layer). 3 tasks + verification.
Foundational sprint for the fee/billing/profitability module — no
billing logic yet, just the billable-account substrate everything
downstream depends on. Builds on nothing new; reuses households,
entities, entity_holdings (read-only reference during backfill),
organizations. Part 1 SQL (accounts, account_owners,
account_balances_daily, account_flows, account_import_batches,
all RLS'd) is already applied by Joe directly via Supabase MCP —
confirm it live before writing any code, do not re-create it.

OUT OF SCOPE: fee_schedules, billing_groups, positions
(account_positions_daily is Sprint 32), anything Altruist-API
shaped. The CSV import path is the only ingestion path this
sprint builds — no live custodian connection of any kind.

STANDING RULES: org_id never accepted from request bodies —
always derived server-side from the authenticated session /
app.current_org_id, same as every other table in this platform.
Decimal (never float) for every monetary field. No interactive
prompts anywhere in scripts. Light theme only if any UI is
touched (2nd Act Signature palette from org_settings, never
hardcoded hex) — this sprint is backend/import-UI, expect minimal
UI surface. Full account numbers must never appear in the
database, in logs, or in any model-facing text — store only
account_number_masked and account_number_hash.

=== TASK 1: Discover, don't assume ===
Query information_schema for the real, current shape of
accounts, account_owners, account_balances_daily, account_flows,
account_import_batches — column names, types, constraints, RLS
policies — exactly as applied, not as designed. Also check
entity_holdings' actual row count and date range for the seed
org, and confirm whether households.id values already exist that
new accounts should link to. Report all of this before writing
any code.

=== TASK 2: Provider-adapter interface + CSV adapter ===
Build services/custody/base.py defining the adapter interface:
  fetch_accounts() -> list[AccountRecord]
  fetch_balances(as_of: date) -> list[BalanceRecord]
  fetch_flows(from_date: date, to_date: date) -> list[FlowRecord]
Build services/custody/csv_adapter.py implementing it against an
uploaded file (column-mapping driven, not a fixed schema assumed
per custodian — different custodians export different column
names for the same concept). Register adapters by custodian_code
in a small registry keyed off org_settings, not a hardcoded
if/else chain — a second custodian profile must be addable later
without touching this sprint's code.
Hashing: account_number_hash = sha256(account_number + org-level
salt from org_settings), never the raw number, anywhere.

=== TASK 3: Import UI — upload, map, dry-run, commit ===
Upload screen -> column-mapping step (user maps source columns to
AccountRecord/BalanceRecord/FlowRecord fields) -> dry-run diff
(new accounts, changed balances, unmatched rows shown separately,
nothing written yet) -> commit, which writes an
account_import_batches row and the underlying account/balance/
flow rows in one transaction. Unmatched rows never silently drop
— they land in a visible exception list on the batch. Re-running
the identical file against a batch must be idempotent (no
duplicate balance/flow rows for the same account+date+source).

=== VERIFICATION ===
Write scripts/verify_sprint31.py — pass/fail only, no interactive
prompts, no note-entry step. Use the real app_service connection
(never the Supabase MCP bypass connection) so RLS is actually
exercised, not skipped.
Assert, against a disposable test org:
  1. accounts / account_owners / account_balances_daily /
     account_flows / account_import_batches all exist with RLS
     enabled and exactly the expected policy.
  2. A CSV import of >=1 account with 30 days of balances and a
     handful of flows commits cleanly; row counts on the batch
     match the file.
  3. Re-importing the identical file produces zero new balance/
     flow rows (idempotent) and does not error.
  4. A row referencing an unresolvable entity/household lands in
     the batch's unmatched/exception set, not silently dropped
     and not a hard failure of the whole batch.
  5. Cross-org isolation: a session scoped to org A cannot read
     org B's accounts, balances, or flows, using app_service
     (not the MCP bypass connection).
  6. No full/unmasked account number appears anywhere in
     accounts, account_import_batches, or application logs
     produced during the test run.
  7. Adapter registry resolves the CSV adapter by custodian_code
     and raises a clear, typed error for an unregistered code
     (proves a second adapter is addable without touching this
     one).
Verify effects, not exit codes — query the actual rows written,
don't just check the script returned 0.

Report the actual results, then stop. Do not proceed to Sprint 32
in this same run.
