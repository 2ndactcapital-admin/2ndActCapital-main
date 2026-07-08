"""verify_cleanup.py — Sprint 16/17 cleanup audit verification.

Checks:
  1. entity_documents has no updated_at column
  2. dashboard_briefs uses generated_at (not brief_date)
  3. entity_documents query runs without updated_at
  4. dashboard_briefs SELECT/WHERE on generated_at::date works
  5. entity_type::text cast works in search filters
  6. reference lists for name_prefix, name_suffix, country exist
"""

import asyncio
import os
import sys

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set")
    sys.exit(0)

import asyncpg

ORG_ID = "00000000-0000-0000-0000-000000000001"
TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
TEST_AUTH0_SUB = "auth0|test_cleanup_verify"
TEST_EMAIL = "cleanup_verify@test.local"


async def teardown(conn):
    """Remove test rows in FK-safe order."""
    await conn.execute("DELETE FROM audit_log WHERE user_id = $1", TEST_USER_ID)
    await conn.execute("DELETE FROM user_roles WHERE user_id = $1", TEST_USER_ID)
    await conn.execute(
        "DELETE FROM user_notification_preferences WHERE user_id = $1", TEST_USER_ID
    )
    await conn.execute("DELETE FROM users WHERE id = $1", TEST_USER_ID)


async def main():
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    failures = []

    try:
        # Defensive teardown first — remove any leftover rows from a prior failed run.
        await teardown(conn)

        # Seed test user (email NOT NULL).
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1, $2, $3, 'Cleanup Verify', $4, 'member')
            ON CONFLICT (id) DO NOTHING
            """,
            TEST_USER_ID, ORG_ID, TEST_EMAIL, TEST_AUTH0_SUB,
        )

        # 1. entity_documents has no updated_at column
        cols = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'entity_documents'
            """,
        )
        col_names = {r["column_name"] for r in cols}
        if "updated_at" in col_names:
            failures.append("entity_documents.updated_at exists — should have been removed")
            print("[FAIL] 1. entity_documents still has updated_at column")
        else:
            print("[PASS] 1. entity_documents has no updated_at column")

        # 2. dashboard_briefs has generated_at, not brief_date
        brief_cols = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'dashboard_briefs'
            """,
        )
        brief_col_names = {r["column_name"] for r in brief_cols}
        if "brief_date" in brief_col_names:
            failures.append("dashboard_briefs.brief_date exists — should be generated_at")
            print("[FAIL] 2. dashboard_briefs has brief_date column (wrong)")
        elif "generated_at" not in brief_col_names:
            failures.append("dashboard_briefs.generated_at missing")
            print("[FAIL] 2. dashboard_briefs missing generated_at column")
        else:
            print("[PASS] 2. dashboard_briefs has generated_at, no brief_date")

        # 3. entity_documents query runs without updated_at
        try:
            await conn.fetch(
                """
                SELECT id, org_id, entity_id, title, doc_category,
                       file_name, content_type, file_size, storage_key,
                       version, supersedes_id, status, uploaded_by, created_at,
                       COALESCE(
                         array_agg(t.tag ORDER BY t.tag) FILTER (WHERE t.tag IS NOT NULL),
                         ARRAY[]::text[]
                       ) AS tags
                FROM entity_documents d
                LEFT JOIN entity_document_tags t ON t.document_id = d.id
                WHERE d.org_id = $1
                GROUP BY d.id, d.org_id, d.entity_id, d.title, d.doc_category,
                         d.file_name, d.content_type, d.file_size, d.storage_key,
                         d.version, d.supersedes_id, d.status, d.uploaded_by, d.created_at
                LIMIT 1
                """,
                ORG_ID,
            )
            print("[PASS] 3. entity_documents query runs without updated_at")
        except Exception as e:
            failures.append(f"entity_documents query error: {e}")
            print(f"[FAIL] 3. entity_documents query: {e}")

        # 4. dashboard_briefs generated_at::date comparison works
        try:
            from datetime import date
            today = date.today()
            await conn.fetchrow(
                "SELECT narration FROM dashboard_briefs WHERE user_id = $1 AND generated_at::date = $2",
                TEST_USER_ID, today,
            )
            print("[PASS] 4. dashboard_briefs generated_at::date query works")
        except Exception as e:
            failures.append(f"dashboard_briefs generated_at query error: {e}")
            print(f"[FAIL] 4. dashboard_briefs generated_at query: {e}")

        # 5. entity_type::text cast works in search
        try:
            await conn.fetch(
                """
                SELECT id FROM entities
                WHERE org_id = $1
                  AND valid_to IS NULL AND system_to IS NULL
                  AND entity_type::text = ANY($2::text[])
                LIMIT 1
                """,
                ORG_ID, ["individual", "llc"],
            )
            print("[PASS] 5. entity_type::text cast works in search filter")
        except Exception as e:
            failures.append(f"entity_type cast error: {e}")
            print(f"[FAIL] 5. entity_type::text cast: {e}")

        # 6. Reference lists for name_prefix, name_suffix, country exist
        for list_key in ("name_prefix", "name_suffix", "country"):
            try:
                row = await conn.fetchrow(
                    "SELECT count(*) AS cnt FROM reference_items WHERE list_key = $1 AND org_id = $2",
                    list_key, ORG_ID,
                )
                cnt = row["cnt"] if row else 0
                if cnt > 0:
                    print(f"[PASS] 6. reference list '{list_key}' has {cnt} items")
                else:
                    print(f"[WARN] 6. reference list '{list_key}' is empty (may be intentional)")
            except Exception as e:
                failures.append(f"reference list '{list_key}' error: {e}")
                print(f"[FAIL] 6. reference list '{list_key}': {e}")

    finally:
        await teardown(conn)
        await conn.close()

    print()
    if failures:
        print(f"RESULT: {len(failures)} failure(s)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("RESULT: all checks passed")


asyncio.run(main())
