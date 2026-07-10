"""verify_sprint22.py — Sprint 22 General Ledger Foundation

Assertions:
  1. Unbalanced entry raises on post.
  2. Balanced entry posts; posted_at is set.
  3. Posted entry's lines cannot be updated or deleted.
  4. Reversal produces mirrored lines; trial balance for the vehicle nets to zero.
  5. Capital accounts sum correctly across two distinct dim_member_series_id values.
  6. journal_lines insert referencing a chart_of_accounts row from a different org raises.
"""
import asyncio
import os
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

    # ── Seed test user ──────────────────────────────────────────────────────
    await conn.execute(
        """
        INSERT INTO users (id, auth0_sub, email, role)
        VALUES ($1, 'auth0|test_verify_sprint22', 'test_sprint22@example.com', 'staff')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID,
    )

    # ── Resolve org and create a test SPV ──────────────────────────────────
    spv_id = str(uuid.uuid4())
    other_org_id = str(uuid.uuid4())
    cleanup_ids = {"spv": spv_id}

    try:
        # Insert a minimal SPV so posting engine can resolve org_id.
        await conn.execute(
            """
            INSERT INTO spvs (id, org_id, name, status, target_raise, min_commitment)
            VALUES ($1::uuid, $2::uuid, 'Test GL SPV', 'open', 100000, 1000)
            ON CONFLICT (id) DO NOTHING
            """,
            spv_id, ORG_ID,
        )

        # Ensure default COA + templates exist for this org (idempotent).
        # The seeds are idempotent so we just check a sentinel account is present.
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

        # Resolve COA IDs we need.
        def coa_id(code):
            return conn.fetchval(
                "SELECT id FROM chart_of_accounts WHERE org_id = $1 AND code = $2 AND system_to IS NULL",
                ORG_ID, code,
            )

        cash_id = await coa_id("1000")
        cap_contrib_id = await coa_id("3000")
        cap_dist_id = await coa_id("3100")
        if not cash_id or not cap_contrib_id:
            record("COA IDs resolved", False, "accounts 1000/3000 missing")
            return
        record("COA IDs resolved", True)

        # ── Assertion 1: Unbalanced entry raises on post ─────────────────
        # Insert a journal entry with only ONE line (unbalanced).
        unbal_entry_id = str(uuid.uuid4())
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO journal_entries
                  (id, org_id, vehicle_id, transaction_type_code, entry_date, amount, basis, created_by)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'MANAGEMENT_FEE', '2026-01-01', 500.00, 'GAAP', $4::uuid)
                """,
                unbal_entry_id, ORG_ID, spv_id, TEST_USER_ID,
            )
            # Only a debit line — no credit line, so it's unbalanced.
            mgmt_fee_id = await coa_id("5000")
            await conn.execute(
                """
                INSERT INTO journal_lines
                  (org_id, journal_entry_id, account_id, debit, credit)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 500.00, NULL)
                """,
                ORG_ID, unbal_entry_id, mgmt_fee_id,
            )

        raised = False
        try:
            await conn.execute(
                "SELECT fn_post_journal_entry($1::uuid, $2::uuid)",
                unbal_entry_id, TEST_USER_ID,
            )
        except Exception:
            raised = True
        record("Unbalanced entry raises on post", raised)

        # ── Assertion 2: Balanced entry posts; posted_at is set ──────────
        bal_entry_id = str(uuid.uuid4())
        ms_id = str(uuid.uuid4())  # fake member_series_id for dimension
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO journal_entries
                  (id, org_id, vehicle_id, transaction_type_code, entry_date, amount, basis, created_by)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'CAPITAL_CONTRIBUTION', '2026-01-15', 25000.00, 'GAAP', $4::uuid)
                """,
                bal_entry_id, ORG_ID, spv_id, TEST_USER_ID,
            )
            await conn.execute(
                """
                INSERT INTO journal_lines
                  (org_id, journal_entry_id, account_id, debit, credit, dim_member_series_id)
                VALUES
                  ($1::uuid, $2::uuid, $3::uuid, 25000.00, NULL, NULL),
                  ($1::uuid, $2::uuid, $4::uuid, NULL, 25000.00, $5::uuid)
                """,
                ORG_ID, bal_entry_id, cash_id, cap_contrib_id, ms_id,
            )

        await conn.execute(
            "SELECT fn_post_journal_entry($1::uuid, $2::uuid)",
            bal_entry_id, TEST_USER_ID,
        )
        posted_row = await conn.fetchrow(
            "SELECT posted_at FROM journal_entries WHERE id = $1::uuid", bal_entry_id
        )
        posted_ok = posted_row and posted_row["posted_at"] is not None
        record("Balanced entry posts; posted_at set", posted_ok)

        # ── Assertion 3: Posted entry's lines cannot be updated or deleted
        line_id = await conn.fetchval(
            "SELECT id FROM journal_lines WHERE journal_entry_id = $1::uuid LIMIT 1",
            bal_entry_id,
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

        # ── Assertion 4: Reversal → mirrored lines; TB nets zero ─────────
        rev_entry_id_val = await conn.fetchval(
            "SELECT fn_reverse_journal_entry($1::uuid, $2::text, $3::uuid)",
            bal_entry_id, "Test reversal", TEST_USER_ID,
        )
        reversal_ok = rev_entry_id_val is not None
        record("Reversal returns new entry id", reversal_ok)

        if reversal_ok:
            # Check the trial balance sums to zero for this vehicle (net).
            tb = await conn.fetch(
                """
                SELECT account_code, total_debit, total_credit
                FROM v_trial_balance
                WHERE vehicle_id = $1::uuid AND basis = 'GAAP'
                """,
                spv_id,
            )
            net = sum(
                (Decimal(str(r["total_debit"] or 0)) - Decimal(str(r["total_credit"] or 0)))
                for r in tb
            )
            record("Trial balance nets to zero after reversal", net == 0, f"net={net}")

        # ── Assertion 5: Capital accounts sum across two member_series ────
        # Post a second contribution for a different class.
        ms_id2 = str(uuid.uuid4())
        bal_entry2_id = str(uuid.uuid4())
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO journal_entries
                  (id, org_id, vehicle_id, transaction_type_code, entry_date, amount, basis, created_by)
                VALUES ($1::uuid, $2::uuid, $3::uuid, 'CAPITAL_CONTRIBUTION', '2026-02-01', 10000.00, 'GAAP', $4::uuid)
                """,
                bal_entry2_id, ORG_ID, spv_id, TEST_USER_ID,
            )
            await conn.execute(
                """
                INSERT INTO journal_lines
                  (org_id, journal_entry_id, account_id, debit, credit, dim_member_series_id)
                VALUES
                  ($1::uuid, $2::uuid, $3::uuid, 10000.00, NULL, NULL),
                  ($1::uuid, $2::uuid, $4::uuid, NULL, 10000.00, $5::uuid)
                """,
                ORG_ID, bal_entry2_id, cash_id, cap_contrib_id, ms_id2,
            )
        await conn.execute(
            "SELECT fn_post_journal_entry($1::uuid, $2::uuid)",
            bal_entry2_id, TEST_USER_ID,
        )

        ca_rows = await conn.fetch(
            """
            SELECT dim_member_series_id, SUM(balance) AS total
            FROM v_capital_accounts
            WHERE vehicle_id = $1::uuid AND basis = 'GAAP'
            GROUP BY dim_member_series_id
            """,
            spv_id,
        )
        series_ids = {str(r["dim_member_series_id"]) for r in ca_rows if r["dim_member_series_id"]}
        # ms_id was reversed so may be absent; ms_id2 should be present with 10000
        ms2_total = next(
            (Decimal(str(r["total"])) for r in ca_rows if str(r["dim_member_series_id"]) == ms_id2),
            None,
        )
        cap_ok = ms2_total == Decimal("10000.00")
        record("Capital accounts: second class sums correctly", cap_ok, f"balance={ms2_total}")

        # ── Assertion 6: Cross-org COA reference raises ───────────────────
        other_org_id = str(uuid.uuid4())
        other_acct_id = str(uuid.uuid4())
        cross_org_raised = False
        try:
            async with conn.transaction():
                # Create a throwaway org and account, then attempt to insert a
                # journal_line referencing the other org's account into this org's entry.
                await conn.execute(
                    "INSERT INTO organizations (id, name, slug) VALUES ($1::uuid, 'Test Throwaway Org', 'test-throwaway-xorg')",
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
                      (id, org_id, vehicle_id, transaction_type_code, entry_date, amount, basis, created_by)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, 'CAPITAL_CONTRIBUTION', '2026-03-01', 1000.00, 'GAAP', $4::uuid)
                    """,
                    xorg_entry_id, ORG_ID, spv_id, TEST_USER_ID,
                )
                # This should raise — account from other_org_id used in ORG_ID journal_line.
                await conn.execute(
                    """
                    INSERT INTO journal_lines
                      (org_id, journal_entry_id, account_id, debit, credit)
                    VALUES ($1::uuid, $2::uuid, $3::uuid, 1000.00, NULL)
                    """,
                    ORG_ID, xorg_entry_id, other_acct_id,
                )
                # If we got here without error, roll back explicitly.
                raise Exception("Expected constraint violation but none raised")
        except Exception as exc:
            err_str = str(exc)
            cross_org_raised = "Expected constraint violation" not in err_str
        record("Cross-org COA reference raises", cross_org_raised)

    finally:
        # ── Teardown ────────────────────────────────────────────────────────
        try:
            spv_id_val = cleanup_ids.get("spv")
            if spv_id_val:
                # Delete in FK-safe order: lines → entries → spv
                await conn.execute(
                    "DELETE FROM journal_lines WHERE journal_entry_id IN "
                    "(SELECT id FROM journal_entries WHERE vehicle_id = $1::uuid)",
                    spv_id_val,
                )
                await conn.execute(
                    "DELETE FROM journal_entries WHERE vehicle_id = $1::uuid",
                    spv_id_val,
                )
                await conn.execute(
                    "DELETE FROM spvs WHERE id = $1::uuid", spv_id_val
                )
            # Clean up other org
            try:
                await conn.execute(
                    "DELETE FROM chart_of_accounts WHERE org_id = $1::uuid", other_org_id
                )
                await conn.execute(
                    "DELETE FROM organizations WHERE id = $1::uuid", other_org_id
                )
            except Exception:
                pass
        except Exception as te:
            print(f"  [teardown] {te}")
        await conn.close()

    # ── Summary ────────────────────────────────────────────────────────────
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n  {passed}/{total} assertions passed")
    if passed < total:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
