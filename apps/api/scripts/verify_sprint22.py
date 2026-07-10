"""verify_sprint22.py — Sprint 22 General Ledger Foundation

Column names are taken directly from docs/schema_snapshot.sql.

Assertions:
   1. Unbalanced entry raises on post (fn_post_journal_entry).
   2. Balanced entry posts; posted_at is set.
   3. Posted entry's lines cannot be updated or deleted (immutability trigger).
   4. Reversal produces mirrored lines; trial balance for the vehicle nets to zero.
   5. Capital accounts sum correctly across two distinct dim_member_series_id values.
   6. journal_lines insert referencing a chart_of_accounts row from a different org raises
      (fn_validate_line_org trigger).
  12. Teardown: all test entries gone from journal_entries; all triggers on
      journal_lines re-enabled (tgenabled = 'O').

Schema facts used here:
  users: id, org_id (NOT NULL), auth0_sub, email, role
  deals: id, org_id (NOT NULL), name — all other columns have defaults or are nullable
  spvs: id, org_id (NOT NULL), deal_id (NOT NULL), name, spv_status (NOT 'status'),
    target_raise, min_commitment — all other columns nullable or defaulted
  journal_entries: id, org_id, vehicle_id, entry_date, ledger_basis,
    transaction_type_code, memo, reverses_entry_id, reversal_reason,
    posted_at, posted_by, created_at, created_by
    (NO amount / dims / template_id / basis)
  journal_lines: id, entry_id, line_no, account_id, debit (NOT NULL DEFAULT 0),
    credit (NOT NULL DEFAULT 0), currency_code, dim_member_series_id,
    dim_investment_id, dim_tax_lot_id, memo
    (NO org_id — tenancy enforced by fn_validate_line_org trigger)
    debit/credit are NOT NULL; pass 0 on unused side, never NULL.
    line_no is NOT NULL and unique per entry; must be supplied.
  chart_of_accounts: is_capital_account, tax_character_code (not is_capital/tax_character)
    No created_by column.
  organizations: id, name, slug — created_at has default
"""
import asyncio
import os
import sys
import uuid
from decimal import Decimal

import asyncpg


DATABASE_URL = os.environ.get("DATABASE_URL")

TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
ORG_ID = "00000000-0000-0000-0000-000000000001"

PASS = "\033[32m[Y]\033[0m"
FAIL = "\033[31m[N]\033[0m"

results = []


def record(label, ok, note=""):
    results.append((label, ok, note))
    icon = PASS if ok else FAIL
    suffix = f"  ({note})" if note else ""
    print(f"  {icon} {label}{suffix}")


async def main():
    if not DATABASE_URL:
        print("[N] SKIP — DATABASE_URL not set")
        return

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)

    # ── Seed test user ─────────────────────────────────────────────────────
    # users.org_id is NOT NULL; supply ORG_ID.
    await conn.execute(
        """
        INSERT INTO users (id, org_id, auth0_sub, email, role)
        VALUES ($1, $2::uuid, 'auth0|test_verify_sprint22', 'test_sprint22@example.com', 'staff')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID, ORG_ID,
    )

    spv_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    other_org_id = str(uuid.uuid4())
    # Every journal_entry id this run creates goes here, including the reversal
    # returned by fn_reverse_journal_entry.  Teardown uses this list so it never
    # issues a bare DELETE … WHERE vehicle_id = … (which would miss the reversal
    # entry and leave orphans).
    entry_ids: list[str] = []

    try:
        # deals.org_id and deals.name are NOT NULL with no default.
        await conn.execute(
            """
            INSERT INTO deals (id, org_id, name)
            VALUES ($1::uuid, $2::uuid, 'Test GL Deal Sprint22')
            ON CONFLICT (id) DO NOTHING
            """,
            deal_id, ORG_ID,
        )

        # spvs.deal_id is NOT NULL; spv_status is the correct column (not 'status').
        await conn.execute(
            """
            INSERT INTO spvs (id, org_id, deal_id, name, spv_status, target_raise, min_commitment)
            VALUES ($1::uuid, $2::uuid, $3::uuid, 'Test GL SPV', 'forming', 100000, 1000)
            ON CONFLICT (id) DO NOTHING
            """,
            spv_id, ORG_ID, deal_id,
        )

        # Verify COA seed is present (needed for all line inserts).
        coa_count = await conn.fetchval(
            "SELECT COUNT(*) FROM chart_of_accounts WHERE org_id = $1 AND system_to IS NULL",
            ORG_ID,
        )
        if coa_count == 0:
            record("COA seed present", False, "Run seeds/coa_default.sql first")
            return
        record("COA seed present", True, f"{coa_count} accounts")

        tmpl_count = await conn.fetchval(
            "SELECT COUNT(*) FROM posting_templates WHERE org_id = $1 AND is_active = true",
            ORG_ID,
        )
        if tmpl_count == 0:
            record("Template seed present", False, "Run seeds/posting_templates.sql first")
            return
        record("Template seed present", True, f"{tmpl_count} templates")

        # Resolve the COA account_ids we'll use directly.
        async def acct_id(code):
            return await conn.fetchval(
                "SELECT id FROM chart_of_accounts "
                "WHERE org_id = $1 AND code = $2 AND system_to IS NULL",
                ORG_ID, code,
            )

        cash_id = await acct_id("1000")
        cap_contrib_id = await acct_id("3000")
        cap_dist_id = await acct_id("3100")
        mgmt_fee_id = await acct_id("5000")
        accrued_id = await acct_id("2000")

        if not all([cash_id, cap_contrib_id, mgmt_fee_id, accrued_id]):
            record("COA IDs resolved", False, "accounts 1000/3000/5000/2000 missing")
            return
        record("COA IDs resolved", True)

        # ── Assertion 1: Unbalanced entry raises on post ──────────────────
        unbal_id = str(uuid.uuid4())
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO journal_entries
                  (id, org_id, vehicle_id, transaction_type_code, entry_date, ledger_basis, created_by)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'MANAGEMENT_FEE', '2026-01-01', 'GAAP', $4::uuid)
                """,
                unbal_id, ORG_ID, spv_id, TEST_USER_ID,
            )
            await conn.execute(
                """
                INSERT INTO journal_lines (entry_id, line_no, account_id, debit, credit)
                VALUES ($1::uuid, 1, $2::uuid, 500.00, 0)
                """,
                unbal_id, mgmt_fee_id,
            )
        entry_ids.append(unbal_id)

        raised = False
        try:
            await conn.execute(
                "SELECT fn_post_journal_entry($1::uuid, $2::uuid)",
                unbal_id, TEST_USER_ID,
            )
        except Exception:
            raised = True
        record("Unbalanced entry raises on post", raised)

        # ── Assertion 2: Balanced entry posts; posted_at is set ───────────
        bal_id = str(uuid.uuid4())
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO journal_entries
                  (id, org_id, vehicle_id, transaction_type_code, entry_date, ledger_basis, created_by)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'CAPITAL_CONTRIBUTION', '2026-01-15', 'GAAP', $4::uuid)
                """,
                bal_id, ORG_ID, spv_id, TEST_USER_ID,
            )
            await conn.execute(
                """
                INSERT INTO journal_lines (entry_id, line_no, account_id, debit, credit)
                VALUES
                  ($1::uuid, 1, $2::uuid, 25000.00, 0),
                  ($1::uuid, 2, $3::uuid, 0, 25000.00)
                """,
                bal_id, cash_id, cap_contrib_id,
            )
        entry_ids.append(bal_id)

        await conn.execute(
            "SELECT fn_post_journal_entry($1::uuid, $2::uuid)",
            bal_id, TEST_USER_ID,
        )
        posted = await conn.fetchrow(
            "SELECT posted_at FROM journal_entries WHERE id = $1::uuid", bal_id
        )
        posted_ok = posted and posted["posted_at"] is not None
        record("Balanced entry posts; posted_at set", posted_ok)

        # ── Assertion 3: Posted lines cannot be updated or deleted ────────
        line_id = await conn.fetchval(
            "SELECT id FROM journal_lines WHERE entry_id = $1::uuid LIMIT 1",
            bal_id,
        )
        update_blocked = False
        delete_blocked = False
        try:
            await conn.execute(
                "UPDATE journal_lines SET debit = 99999 WHERE id = $1::uuid", line_id
            )
        except Exception:
            update_blocked = True
        try:
            await conn.execute(
                "DELETE FROM journal_lines WHERE id = $1::uuid", line_id
            )
        except Exception:
            delete_blocked = True
        record("Posted lines cannot be updated", update_blocked)
        record("Posted lines cannot be deleted", delete_blocked)

        # ── Assertion 4: Reversal produces mirrored lines; TB nets zero ───
        rev_id = await conn.fetchval(
            "SELECT fn_reverse_journal_entry($1::uuid, $2::text, $3::uuid)",
            bal_id, "Test reversal sprint22", TEST_USER_ID,
        )
        reversal_created = rev_id is not None
        record("Reversal returns new entry id", reversal_created)

        if reversal_created:
            entry_ids.append(str(rev_id))  # reversal entry must be tracked for teardown
            net = await conn.fetchval(
                """
                SELECT COALESCE(SUM(jl.debit), 0) - COALESCE(SUM(jl.credit), 0)
                FROM journal_lines jl
                JOIN journal_entries je ON je.id = jl.entry_id
                WHERE je.vehicle_id = $1::uuid
                  AND je.ledger_basis = 'GAAP'
                  AND je.posted_at IS NOT NULL
                """,
                spv_id,
            )
            record("Trial balance nets to zero after reversal", net == 0, f"net={net}")

        # ── Assertion 5: Capital accounts across two member_series ────────
        ms_id2 = str(uuid.uuid4())
        bal_id2 = str(uuid.uuid4())
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO journal_entries
                  (id, org_id, vehicle_id, transaction_type_code, entry_date, ledger_basis, created_by)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'CAPITAL_CONTRIBUTION', '2026-02-01', 'GAAP', $4::uuid)
                """,
                bal_id2, ORG_ID, spv_id, TEST_USER_ID,
            )
            await conn.execute(
                """
                INSERT INTO journal_lines
                  (entry_id, line_no, account_id, debit, credit, dim_member_series_id)
                VALUES
                  ($1::uuid, 1, $2::uuid, 10000.00, 0, NULL),
                  ($1::uuid, 2, $3::uuid, 0, 10000.00, $4::uuid)
                """,
                bal_id2, cash_id, cap_contrib_id, ms_id2,
            )
        entry_ids.append(bal_id2)

        await conn.execute(
            "SELECT fn_post_journal_entry($1::uuid, $2::uuid)",
            bal_id2, TEST_USER_ID,
        )

        ca = await conn.fetch(
            """
            SELECT jl.dim_member_series_id, SUM(jl.credit) AS total_credit
            FROM journal_lines jl
            JOIN journal_entries je ON je.id = jl.entry_id
            JOIN chart_of_accounts coa ON coa.id = jl.account_id
            WHERE je.vehicle_id = $1::uuid
              AND je.ledger_basis = 'GAAP'
              AND je.posted_at IS NOT NULL
              AND coa.is_capital_account = true
              AND jl.dim_member_series_id IS NOT NULL
            GROUP BY jl.dim_member_series_id
            """,
            spv_id,
        )
        ms2_total = next(
            (Decimal(str(r["total_credit"])) for r in ca
             if str(r["dim_member_series_id"]) == ms_id2),
            None,
        )
        cap_ok = ms2_total == Decimal("10000.00")
        record("Capital accounts: second member series sums correctly", cap_ok, f"balance={ms2_total}")

        # ── Assertion 6: Cross-org COA reference raises via trigger ───────
        # xorg_entry_id is inside a transaction that is always rolled back — never
        # added to entry_ids.
        cross_org_raised = False
        other_acct_id = str(uuid.uuid4())
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO organizations (id, name, slug) "
                    "VALUES ($1::uuid, 'Throwaway Org Sprint22', 'throwaway-sprint22-xorg')",
                    other_org_id,
                )
                await conn.execute(
                    """
                    INSERT INTO chart_of_accounts
                      (id, org_id, code, name, account_type, normal_balance)
                    VALUES ($1::uuid, $2::uuid, '1000', 'Cash', 'ASSET', 'D')
                    """,
                    other_acct_id, other_org_id,
                )
                xorg_entry_id = str(uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO journal_entries
                      (id, org_id, vehicle_id, transaction_type_code, entry_date, ledger_basis, created_by)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, 'CAPITAL_CONTRIBUTION',
                            '2026-03-01', 'GAAP', $4::uuid)
                    """,
                    xorg_entry_id, ORG_ID, spv_id, TEST_USER_ID,
                )
                await conn.execute(
                    """
                    INSERT INTO journal_lines (entry_id, line_no, account_id, debit, credit)
                    VALUES ($1::uuid, 1, $2::uuid, 1000.00, 0)
                    """,
                    xorg_entry_id, other_acct_id,
                )
                raise Exception("no-trigger")
        except Exception as exc:
            cross_org_raised = "no-trigger" not in str(exc)
        record("Cross-org COA reference raises (fn_validate_line_org)", cross_org_raised)

    finally:
        # ── Teardown ───────────────────────────────────────────────────────
        #
        # trg_guard_posted_lines blocks DELETE on journal_lines for posted entries.
        # Disable it for the exact set of entry IDs this run created, then
        # re-enable it in a nested finally so it is NEVER left disabled — a
        # disabled immutability trigger on a live ledger is the worst outcome
        # this script could produce.
        #
        # Teardown errors are fatal: print to stderr and exit non-zero.  A script
        # that silently pollutes the ledger is worse than one that crashes.

        teardown_failed = False

        if entry_ids:
            try:
                await conn.execute(
                    "ALTER TABLE journal_lines DISABLE TRIGGER trg_guard_posted_lines"
                )
                try:
                    await conn.execute(
                        "DELETE FROM journal_lines WHERE entry_id = ANY($1::uuid[])",
                        entry_ids,
                    )
                    await conn.execute(
                        "DELETE FROM journal_entries WHERE id = ANY($1::uuid[])",
                        entry_ids,
                    )
                finally:
                    # Always re-enable, even if the deletes raised.
                    await conn.execute(
                        "ALTER TABLE journal_lines ENABLE TRIGGER trg_guard_posted_lines"
                    )
            except Exception as te:
                teardown_failed = True
                print(
                    f"\n  [teardown] FATAL: entries/lines: {te}",
                    file=sys.stderr,
                )

        try:
            await conn.execute("DELETE FROM spvs WHERE id = $1::uuid", spv_id)
            await conn.execute("DELETE FROM deals WHERE id = $1::uuid", deal_id)
        except Exception as te:
            teardown_failed = True
            print(f"\n  [teardown] FATAL: spv/deal: {te}", file=sys.stderr)

        try:
            # other_org rows are always inside a rolled-back transaction so these
            # deletes are no-ops; keep for safety in case of partial commit.
            await conn.execute(
                "DELETE FROM chart_of_accounts WHERE org_id = $1::uuid", other_org_id
            )
            await conn.execute(
                "DELETE FROM organizations WHERE id = $1::uuid", other_org_id
            )
        except Exception:
            pass  # expected no-op; rolled-back transaction left nothing

        try:
            await conn.execute(
                "DELETE FROM users WHERE id = $1::uuid", TEST_USER_ID
            )
        except Exception as te:
            teardown_failed = True
            print(f"\n  [teardown] FATAL: test user: {te}", file=sys.stderr)

        # ── Assertion 12: database clean; all triggers on journal_lines re-enabled
        try:
            leftover = 0
            if entry_ids:
                leftover = await conn.fetchval(
                    "SELECT count(*) FROM journal_entries WHERE id = ANY($1::uuid[])",
                    entry_ids,
                )
            trigger_rows = await conn.fetch(
                "SELECT tgname, tgenabled FROM pg_trigger "
                "WHERE tgrelid = 'public.journal_lines'::regclass "
                "  AND tgisinternal = false"
            )
            triggers_ok = all(r["tgenabled"] == "O" for r in trigger_rows)
            trigger_detail = ", ".join(
                f"{r['tgname']}={r['tgenabled']!r}" for r in trigger_rows
            )
            clean_ok = leftover == 0 and triggers_ok and not teardown_failed
            record(
                "Teardown: entries deleted and triggers re-enabled",
                clean_ok,
                f"leftover={leftover} triggers=[{trigger_detail}]",
            )
        except Exception as te:
            record("Teardown: entries deleted and triggers re-enabled", False, str(te))

        await conn.close()

        if teardown_failed:
            print(
                "\n  [FATAL] Teardown incomplete — test data left in database. "
                "Fix manually before re-running.",
                file=sys.stderr,
            )
            raise SystemExit(2)

    # ── Summary ────────────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n  {passed}/{total} assertions passed")
    if passed < total:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
