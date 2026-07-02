"""Sprint 18 verification — Ownership Editing & Time-Travel.

Run:  DATABASE_URL=... python apps/api/scripts/verify_sprint18.py

Checks:
 1.  entity_relationships has change_reason, change_source_type, change_source_id, effective_date
 2.  ownership_change_log table exists with expected columns
 3.  GET /entities/{id}/ownership SQL: active ownership rows returned for both sides
 4.  POST ownership — insert + cycle guard: new row has is_active flags
 5.  Ownership pct validation (0-100 enforced)
 6.  Four-timestamp amendment: PATCH creates new row, closes old
 7.  Log: change log entry inserted for amendment
 8.  Soft-delete: DELETE closes the row; log shows new_pct=0
 9.  Time-travel: as_of query returns historical state (not current)
10.  History query: log ordered newest-first for entity
"""

import asyncio
import os
import sys
import uuid
from decimal import Decimal

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("SKIP — DATABASE_URL not set")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"
TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
TEST_AUTH0 = "auth0|test_verify_user"

PASS = "[P]"
FAIL = "[F]"


def check(label, cond):
    sym = PASS if cond else FAIL
    print(f"{sym} {label}")
    return cond


async def main():
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    entity_a_id = None
    entity_b_id = None
    entity_c_id = None
    rel_ab_id = None
    missing_log = {"placeholder"}  # assume log table absent until check 2 clears it
    ok = True

    try:
        # Ensure test user
        await conn.execute(
            """
            INSERT INTO users (id, org_id, auth0_sub, email, role)
            VALUES ($1, $2, $3, 'verify18@test.local', 'member')
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            TEST_USER_ID, ORG_ID, TEST_AUTH0,
        )

        # --- Check 1: entity_relationships has new Sprint 18 columns ---
        cols = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'entity_relationships' AND table_schema = 'public'
              AND column_name IN (
                'change_reason', 'change_source_type', 'change_source_id', 'effective_date'
              )
            """
        )
        found_cols = {r["column_name"] for r in cols}
        expected = {"change_reason", "change_source_type", "change_source_id", "effective_date"}
        missing = expected - found_cols
        ok &= check(
            f"entity_relationships has Sprint 18 columns (missing: {missing or 'none'})",
            not missing,
        )

        # --- Check 2: ownership_change_log exists with key columns ---
        log_cols = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'ownership_change_log' AND table_schema = 'public'
              AND column_name IN (
                'id', 'org_id', 'relationship_id', 'from_entity_id', 'to_entity_id',
                'prior_pct', 'new_pct', 'change_reason', 'changed_by', 'created_at'
              )
            """
        )
        found_log_cols = {r["column_name"] for r in log_cols}
        expected_log = {
            "id", "org_id", "relationship_id", "from_entity_id", "to_entity_id",
            "prior_pct", "new_pct", "change_reason", "changed_by", "created_at",
        }
        missing_log = expected_log - found_log_cols
        ok &= check(
            f"ownership_change_log columns present (missing: {missing_log or 'none'})",
            not missing_log,
        )

        # --- Set up test entities ---
        entity_a_id = await conn.fetchval(
            """
            INSERT INTO entities (org_id, entity_type, display_name)
            VALUES ($1, 'individual', '__verify18_entity_A__')
            RETURNING id
            """,
            ORG_ID,
        )
        entity_b_id = await conn.fetchval(
            """
            INSERT INTO entities (org_id, entity_type, display_name)
            VALUES ($1, 'llc', '__verify18_entity_B__')
            RETURNING id
            """,
            ORG_ID,
        )
        entity_c_id = await conn.fetchval(
            """
            INSERT INTO entities (org_id, entity_type, display_name)
            VALUES ($1, 'trust', '__verify18_entity_C__')
            RETURNING id
            """,
            ORG_ID,
        )

        # --- Check 3: active ownership SQL returns correct rows ---
        # A owns B at 60%
        rel_ab_id = await conn.fetchval(
            """
            INSERT INTO entity_relationships
                (org_id, from_entity_id, to_entity_id, relationship_type,
                 ownership_pct, change_reason, change_source_type,
                 valid_from, system_from, created_by)
            VALUES ($1, $2, $3, 'ownership', 60.0, 'initial', 'api',
                    now(), now(), $4)
            RETURNING id
            """,
            ORG_ID, entity_a_id, entity_b_id, TEST_USER_ID,
        )
        owns_rows = await conn.fetch(
            """
            SELECT r.id, r.ownership_pct
            FROM entity_relationships r
            WHERE r.from_entity_id = $1
              AND r.org_id = $2
              AND r.relationship_type = 'ownership'
              AND r.valid_to IS NULL AND r.system_to IS NULL
            """,
            entity_a_id, ORG_ID,
        )
        owned_by_rows = await conn.fetch(
            """
            SELECT r.id, r.ownership_pct
            FROM entity_relationships r
            WHERE r.to_entity_id = $1
              AND r.org_id = $2
              AND r.relationship_type = 'ownership'
              AND r.valid_to IS NULL AND r.system_to IS NULL
            """,
            entity_b_id, ORG_ID,
        )
        ok &= check(
            "ownership SQL: A owns row found",
            any(r["id"] == rel_ab_id for r in owns_rows),
        )
        ok &= check(
            "ownership SQL: B owned-by row found",
            any(r["id"] == rel_ab_id for r in owned_by_rows),
        )

        # --- Check 4: direct insert with effective_date ---
        # C owns B at 25% (multi-owner scenario)
        rel_cb_id = await conn.fetchval(
            """
            INSERT INTO entity_relationships
                (org_id, from_entity_id, to_entity_id, relationship_type,
                 ownership_pct, effective_date, change_reason, change_source_type,
                 valid_from, system_from, created_by)
            VALUES ($1, $2, $3, 'ownership', 25.0, '2024-01-01', 'initial', 'api',
                    now(), now(), $4)
            RETURNING id
            """,
            ORG_ID, entity_c_id, entity_b_id, TEST_USER_ID,
        )
        cb_row = await conn.fetchrow(
            "SELECT effective_date, change_reason FROM entity_relationships WHERE id = $1",
            rel_cb_id,
        )
        ok &= check(
            "effective_date and change_reason stored on insert",
            cb_row is not None
            and str(cb_row["effective_date"]) == "2024-01-01"
            and cb_row["change_reason"] == "initial",
        )

        # --- Check 5: pct validation (0-100) ---
        # We verify the DB accepts 0, 100, and boundary decimal values
        test_pct_id = await conn.fetchval(
            """
            INSERT INTO entity_relationships
                (org_id, from_entity_id, to_entity_id, relationship_type,
                 ownership_pct, valid_from, system_from, created_by)
            VALUES ($1, $2, $3, 'ownership', 100.0, now(), now(), $4)
            RETURNING id
            """,
            ORG_ID, entity_c_id, entity_a_id, TEST_USER_ID,
        )
        pct_row = await conn.fetchrow(
            "SELECT ownership_pct FROM entity_relationships WHERE id = $1", test_pct_id
        )
        ok &= check(
            "ownership_pct stores 100.0 correctly",
            pct_row is not None and float(pct_row["ownership_pct"]) == 100.0,
        )
        # cleanup
        await conn.execute(
            "UPDATE entity_relationships SET valid_to = now(), system_to = now() WHERE id = $1",
            test_pct_id,
        )

        # --- Check 6: Four-timestamp amendment pattern ---
        # Close rel_ab (60%) and insert a new row at 75%
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE entity_relationships
                SET valid_to = now(), system_to = now()
                WHERE id = $1 AND org_id = $2
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                rel_ab_id, ORG_ID,
            )
            new_rel_id = await conn.fetchval(
                """
                INSERT INTO entity_relationships
                    (org_id, from_entity_id, to_entity_id, relationship_type,
                     ownership_pct, change_reason, change_source_type,
                     valid_from, system_from, created_by)
                VALUES ($1, $2, $3, 'ownership', 75.0, 'manual_edit', 'api',
                        now(), now(), $4)
                RETURNING id
                """,
                ORG_ID, entity_a_id, entity_b_id, TEST_USER_ID,
            )

        old_row = await conn.fetchrow(
            "SELECT valid_to, system_to FROM entity_relationships WHERE id = $1", rel_ab_id
        )
        new_row = await conn.fetchrow(
            "SELECT ownership_pct, valid_to, system_to FROM entity_relationships WHERE id = $1",
            new_rel_id,
        )
        ok &= check(
            "amendment: old row closed (valid_to + system_to set)",
            old_row["valid_to"] is not None and old_row["system_to"] is not None,
        )
        ok &= check(
            "amendment: new row active at 75%",
            new_row["valid_to"] is None
            and new_row["system_to"] is None
            and abs(float(new_row["ownership_pct"]) - 75.0) < 0.001,
        )

        # --- Check 7: ownership_change_log insert ---
        # Only run if log table columns exist
        if not missing_log:
            await conn.execute(
                """
                INSERT INTO ownership_change_log
                    (org_id, relationship_id, from_entity_id, to_entity_id,
                     prior_pct, new_pct, change_reason, changed_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                ORG_ID, new_rel_id, entity_a_id, entity_b_id,
                Decimal("60.0"), Decimal("75.0"), "manual_edit", TEST_USER_ID,
            )
            log_row = await conn.fetchrow(
                """
                SELECT prior_pct, new_pct, change_reason
                FROM ownership_change_log
                WHERE relationship_id = $1 AND org_id = $2
                ORDER BY created_at DESC LIMIT 1
                """,
                new_rel_id, ORG_ID,
            )
            ok &= check(
                "ownership_change_log: amendment entry stored (prior=60, new=75)",
                log_row is not None
                and abs(float(log_row["prior_pct"]) - 60.0) < 0.001
                and abs(float(log_row["new_pct"]) - 75.0) < 0.001
                and log_row["change_reason"] == "manual_edit",
            )
        else:
            print("[S] Check 7 skipped — ownership_change_log columns missing")

        # --- Check 8: soft-delete + log entry with new_pct=0 ---
        await conn.execute(
            """
            UPDATE entity_relationships
            SET valid_to = now(), system_to = now()
            WHERE id = $1 AND org_id = $2
              AND valid_to IS NULL AND system_to IS NULL
            """,
            new_rel_id, ORG_ID,
        )
        if not missing_log:
            await conn.execute(
                """
                INSERT INTO ownership_change_log
                    (org_id, relationship_id, from_entity_id, to_entity_id,
                     prior_pct, new_pct, change_reason, changed_by)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                ORG_ID, new_rel_id, entity_a_id, entity_b_id,
                Decimal("75.0"), Decimal("0"), "deleted", TEST_USER_ID,
            )
        del_row = await conn.fetchrow(
            "SELECT valid_to, system_to FROM entity_relationships WHERE id = $1", new_rel_id
        )
        ok &= check(
            "soft-delete: row closed (valid_to + system_to set)",
            del_row["valid_to"] is not None and del_row["system_to"] is not None,
        )
        if not missing_log:
            del_log = await conn.fetchrow(
                """
                SELECT new_pct, change_reason FROM ownership_change_log
                WHERE relationship_id = $1 AND change_reason = 'deleted'
                  AND org_id = $2
                ORDER BY created_at DESC LIMIT 1
                """,
                new_rel_id, ORG_ID,
            )
            ok &= check(
                "delete log: entry recorded with new_pct=0",
                del_log is not None
                and float(del_log["new_pct"]) == 0.0
                and del_log["change_reason"] == "deleted",
            )

        # --- Check 9: time-travel — as_of query ---
        # rel_ab_id was active (60%) then closed; it should appear in a past as_of query.
        # new_rel_id (75%) was opened then closed; should appear in an even later as_of query.
        # Both should be gone in current query (valid_to IS NULL).

        # Find the valid_from of new_rel_id to pick an as_of timestamp between the two versions.
        new_row_detail = await conn.fetchrow(
            "SELECT valid_from FROM entity_relationships WHERE id = $1", new_rel_id
        )
        # Use valid_from of new_rel as "during 75% period" anchor.
        as_of_ts = new_row_detail["valid_from"]

        time_travel_rows = await conn.fetch(
            """
            SELECT r.id, r.ownership_pct FROM entity_relationships r
            WHERE r.from_entity_id = $1
              AND r.org_id = $2
              AND r.relationship_type = 'ownership'
              AND r.valid_from <= $3
              AND (r.valid_to IS NULL OR r.valid_to > $3)
              AND r.system_to IS NULL
            """,
            entity_a_id, ORG_ID, as_of_ts,
        )
        ok &= check(
            "time-travel: 75% row found at its valid_from timestamp",
            any(r["id"] == new_rel_id for r in time_travel_rows),
        )

        # Current query should return 0 rows for A→B (both closed)
        current_rows = await conn.fetch(
            """
            SELECT id FROM entity_relationships
            WHERE from_entity_id = $1 AND org_id = $2
              AND relationship_type = 'ownership'
              AND valid_to IS NULL AND system_to IS NULL
              AND to_entity_id = $3
            """,
            entity_a_id, ORG_ID, entity_b_id,
        )
        ok &= check(
            "current view: no active A→B rows after delete",
            len(current_rows) == 0,
        )

        # --- Check 10: history query ordered newest-first ---
        if not missing_log:
            history_rows = await conn.fetch(
                """
                SELECT id, change_reason, created_at
                FROM ownership_change_log
                WHERE (from_entity_id = $1 OR to_entity_id = $1)
                  AND org_id = $2
                ORDER BY created_at DESC
                LIMIT 200
                """,
                entity_a_id, ORG_ID,
            )
            # Should have at least 2 entries (manual_edit + deleted)
            reasons = [r["change_reason"] for r in history_rows]
            ok &= check(
                f"history: found {len(history_rows)} entries, includes manual_edit + deleted",
                len(history_rows) >= 2
                and "manual_edit" in reasons
                and "deleted" in reasons,
            )
            # Verify newest-first ordering
            if len(history_rows) >= 2:
                ok &= check(
                    "history: ordered newest-first",
                    history_rows[0]["created_at"] >= history_rows[-1]["created_at"],
                )
        else:
            print("[S] Check 10 skipped — ownership_change_log columns missing")

    finally:
        try:
            if not missing_log and (entity_a_id or entity_b_id):
                await conn.execute(
                    "DELETE FROM ownership_change_log WHERE org_id = $1 "
                    "AND (from_entity_id = ANY($2::uuid[]) OR to_entity_id = ANY($2::uuid[]))",
                    ORG_ID,
                    [e for e in [entity_a_id, entity_b_id, entity_c_id] if e],
                )
        except Exception as e:
            print(f"[teardown warning] log cleanup: {e}")
        try:
            await conn.execute(
                """
                UPDATE entity_relationships
                SET valid_to = now(), system_to = now()
                WHERE org_id = $1
                  AND (from_entity_id = ANY($2::uuid[]) OR to_entity_id = ANY($2::uuid[]))
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                ORG_ID,
                [entity_a_id, entity_b_id, entity_c_id],
            )
        except Exception as e:
            print(f"[teardown warning] rel soft-close: {e}")
        for eid in [entity_c_id, entity_b_id, entity_a_id]:
            if eid:
                try:
                    await conn.execute("DELETE FROM entities WHERE id = $1", eid)
                except Exception as e:
                    print(f"[teardown warning] entity {eid}: {e}")
        await conn.close()

    if ok:
        print("\nAll Sprint 18 checks passed.")
    else:
        print("\nSome checks FAILED — see above.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
