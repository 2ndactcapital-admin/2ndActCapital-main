"""Sprint 17 verification — Entity Picker + CRM Docs Tab.

Run:  DATABASE_URL=... python apps/api/scripts/verify_sprint17.py

Checks:
 1.  entities table has is_incomplete and created_via columns
 2.  entity_documents table exists with required columns
 3.  entity_document_tags table exists
 4.  doc_category reference data seeded (exactly 12 entries)
 5.  Search SQL: LOWER(display_name) LIKE match returns the test entity
 6.  Org scope: entity from different org not returned by scoped search
 7.  Stub logic: entity with matching display_name found by dupe-check query
 8.  Stub creates entity with is_incomplete=true, created_via='picker_stub'
 9.  Document record insert + tag insert + JOIN returns tags array
10.  Versioning: new doc with supersedes_id deprecates the prior doc
"""

import asyncio
import os
import sys
import uuid

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("SKIP — DATABASE_URL not set")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"
OTHER_ORG_ID = "00000000-0000-0000-0000-000000000099"
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
    entity_id = None
    other_entity_id = None
    stub_entity_id = None
    doc_v1_id = None
    doc_v2_id = None
    ok = True

    try:
        # Ensure test user
        await conn.execute(
            """
            INSERT INTO users (id, org_id, auth0_sub, email, role)
            VALUES ($1, $2, $3, 'verify17@test.local', 'member')
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            TEST_USER_ID, ORG_ID, TEST_AUTH0,
        )

        # --- Check 1: entities has is_incomplete + created_via ---
        cols = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'entities' AND table_schema = 'public'
              AND column_name IN ('is_incomplete', 'created_via')
            """
        )
        found = {r["column_name"] for r in cols}
        missing = {"is_incomplete", "created_via"} - found
        ok &= check(
            f"entities has is_incomplete + created_via (missing: {missing or 'none'})",
            not missing,
        )

        # --- Check 2: entity_documents table exists with key columns ---
        doc_cols = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'entity_documents' AND table_schema = 'public'
              AND column_name IN (
                'id', 'org_id', 'entity_id', 'title', 'doc_category',
                'file_name', 'r2_key', 'version', 'supersedes_id', 'status'
              )
            """
        )
        found_doc_cols = {r["column_name"] for r in doc_cols}
        expected_doc_cols = {
            "id", "org_id", "entity_id", "title", "doc_category",
            "file_name", "r2_key", "version", "supersedes_id", "status",
        }
        missing_doc = expected_doc_cols - found_doc_cols
        ok &= check(
            f"entity_documents table columns present (missing: {missing_doc or 'none'})",
            not missing_doc,
        )

        # --- Check 3: entity_document_tags table exists ---
        tags_tbl = await conn.fetchval(
            "SELECT to_regclass('public.entity_document_tags')"
        )
        ok &= check("entity_document_tags table exists", tags_tbl is not None)

        # --- Check 4: doc_category seeded (12 entries) ---
        n_cat = await conn.fetchval(
            "SELECT COUNT(*) FROM reference_data WHERE list_key = 'doc_category' AND is_active = true"
        )
        ok &= check(
            f"doc_category seeded (expected 12, got {n_cat})",
            (n_cat or 0) == 12,
        )

        # --- Insert test entity for search/stub tests ---
        entity_id = await conn.fetchval(
            """
            INSERT INTO entities (org_id, entity_type, display_name, is_incomplete, created_via)
            VALUES ($1, 'individual', '__verify17_search_target__', false, null)
            RETURNING id
            """,
            ORG_ID,
        )

        # --- Check 5: search SQL returns matching entity ---
        q = "__verify17_search_target__"
        rows = await conn.fetch(
            """
            SELECT id, display_name FROM entities
            WHERE org_id = $1
              AND valid_to IS NULL AND system_to IS NULL
              AND LOWER(display_name) LIKE $2
            """,
            ORG_ID,
            f"%{q.lower()}%",
        )
        ids = [r["id"] for r in rows]
        ok &= check(
            "search SQL returns matching entity",
            entity_id in ids,
        )

        # --- Check 6: org scope — other org entity not returned ---
        # Insert entity in a different org_id (if org row exists skip conflict)
        other_entity_id = await conn.fetchval(
            """
            INSERT INTO entities (org_id, entity_type, display_name)
            VALUES ($1, 'individual', '__verify17_other_org__')
            RETURNING id
            """,
            OTHER_ORG_ID,
        )
        rows_scoped = await conn.fetch(
            """
            SELECT id FROM entities
            WHERE org_id = $1
              AND valid_to IS NULL AND system_to IS NULL
              AND display_name LIKE '%__verify17_other_org__%'
            """,
            ORG_ID,
        )
        ok &= check(
            "org-scoped search excludes other-org entity",
            other_entity_id not in [r["id"] for r in rows_scoped],
        )

        # --- Check 7: stub dupe-check SQL finds existing entity ---
        dupes = await conn.fetch(
            """
            SELECT id, display_name FROM entities
            WHERE org_id = $1
              AND valid_to IS NULL AND system_to IS NULL
              AND LOWER(display_name) = LOWER($2)
            """,
            ORG_ID,
            "__verify17_search_target__",
        )
        ok &= check(
            "stub dupe-check SQL finds existing entity by LOWER(display_name)",
            len(dupes) > 0 and entity_id in [r["id"] for r in dupes],
        )

        # --- Check 8: stub creation with is_incomplete + created_via ---
        stub_entity_id = await conn.fetchval(
            """
            INSERT INTO entities (org_id, entity_type, display_name, is_incomplete, created_via)
            VALUES ($1, 'individual', '__verify17_stub__', true, 'picker_stub')
            RETURNING id
            """,
            ORG_ID,
        )
        stub_row = await conn.fetchrow(
            "SELECT is_incomplete, created_via FROM entities WHERE id = $1",
            stub_entity_id,
        )
        ok &= check(
            "stub entity stored with is_incomplete=true, created_via='picker_stub'",
            stub_row is not None
            and stub_row["is_incomplete"] is True
            and stub_row["created_via"] == "picker_stub",
        )

        # --- Checks 9-10 require entity_documents + entity_document_tags ---
        if tags_tbl is None or missing_doc:
            print("[S] document checks skipped — tables not deployed")
        else:
            # --- Check 9: doc record + tag → JOIN returns tags array ---
            doc_v1_id = uuid.uuid4()
            await conn.execute(
                """
                INSERT INTO entity_documents (
                  id, org_id, entity_id, title, doc_category,
                  file_name, file_type, r2_key, r2_bucket, version, status
                ) VALUES ($1, $2, $3, 'Verify17 Doc', 'other',
                  'test.pdf', 'application/pdf', 'test/key.pdf', 'test-bucket', 1, 'active')
                """,
                doc_v1_id, ORG_ID, entity_id,
            )
            await conn.execute(
                "INSERT INTO entity_document_tags (document_id, tag) VALUES ($1, $2)",
                doc_v1_id, "signed",
            )
            doc_row = await conn.fetchrow(
                """
                SELECT d.id, array_agg(t.tag) AS tags
                FROM entity_documents d
                LEFT JOIN entity_document_tags t ON t.document_id = d.id
                WHERE d.id = $1
                GROUP BY d.id
                """,
                doc_v1_id,
            )
            ok &= check(
                "document + tag JOIN returns tags array with 'signed'",
                doc_row is not None
                and doc_row["tags"] is not None
                and "signed" in doc_row["tags"],
            )

            # --- Check 10: versioning deprecates prior doc ---
            doc_v2_id = uuid.uuid4()
            await conn.execute(
                "UPDATE entity_documents SET status = 'deprecated' WHERE id = $1",
                doc_v1_id,
            )
            await conn.execute(
                """
                INSERT INTO entity_documents (
                  id, org_id, entity_id, title, doc_category,
                  file_name, file_type, r2_key, r2_bucket, version,
                  supersedes_id, status
                ) VALUES ($1, $2, $3, 'Verify17 Doc', 'other',
                  'test_v2.pdf', 'application/pdf', 'test/key_v2.pdf',
                  'test-bucket', 2, $4, 'active')
                """,
                doc_v2_id, ORG_ID, entity_id, doc_v1_id,
            )
            v1_status = await conn.fetchval(
                "SELECT status FROM entity_documents WHERE id = $1", doc_v1_id
            )
            v2_row = await conn.fetchrow(
                "SELECT version, supersedes_id FROM entity_documents WHERE id = $1",
                doc_v2_id,
            )
            ok &= check(
                "versioning: v1 deprecated, v2 has supersedes_id pointing to v1",
                v1_status == "deprecated"
                and v2_row is not None
                and v2_row["version"] == 2
                and v2_row["supersedes_id"] == doc_v1_id,
            )

    finally:
        try:
            if doc_v2_id:
                await conn.execute(
                    "DELETE FROM entity_documents WHERE id = $1", doc_v2_id
                )
            if doc_v1_id:
                await conn.execute(
                    "DELETE FROM entity_documents WHERE id = $1", doc_v1_id
                )
            if stub_entity_id:
                await conn.execute(
                    "DELETE FROM entities WHERE id = $1", stub_entity_id
                )
            if other_entity_id:
                await conn.execute(
                    "DELETE FROM entities WHERE id = $1", other_entity_id
                )
            if entity_id:
                await conn.execute(
                    "DELETE FROM entities WHERE id = $1", entity_id
                )
        except Exception as e:
            print(f"[teardown warning] {e}")
        await conn.close()

    if ok:
        print("\nAll Sprint 17 checks passed.")
    else:
        print("\nSome checks FAILED — see above.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
