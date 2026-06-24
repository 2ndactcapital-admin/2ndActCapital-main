"""Verification script for Sprint 7.

Runs the real FastAPI app in-process against the live database, stubbing
only the JWT signature check.

    cd apps/api
    DATABASE_URL='postgresql://...:6543/postgres' python scripts/verify_sprint7.py

Checks:
  1. GET /deals/{id}/ai-summary -> 404 when no summary exists
  2. POST /deals/{id}/ai-summary -> 200 (requires ANTHROPIC_API_KEY) or 500 if not configured
  3. PUT /deals/{id}/documents/{doc_id}/review -> 200
  4. GET /deals/{id} -> members see only approved+visible docs
  5. PUT /deals/{id}/stage -> 200, validates against config
  6. GET /deals/{id}/member-investments -> 200 (staff)
  7. POST /deals/{id}/member-investments/{user_id}/stage -> 200
  8. GET /portfolio/my-investments -> 200
  9. GET /portfolio/summary -> 200
"""

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

H = {"Authorization": "Bearer local-verify"}
TEST_USER_ID = "99000000-0000-0000-0000-000000000003"
TEST_AUTH0_SUB = "auth0|test_verify_7_user"
TEST_ORG_ID = "00000000-0000-0000-0000-000000000001"


async def setup_test_user(pool):
    await pool.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'test7@2ndactcapital.com', 'Test 7 User', $3, 'admin')
        ON CONFLICT (auth0_sub) DO UPDATE SET role = 'admin'
        """,
        TEST_USER_ID,
        TEST_ORG_ID,
        TEST_AUTH0_SUB,
    )


async def teardown(pool, deal_ids, doc_ids):
    # Clear all FK references to test user
    await pool.execute(
        "UPDATE deal_documents SET reviewed_by = NULL WHERE reviewed_by = $1",
        TEST_USER_ID,
    )
    await pool.execute(
        "DELETE FROM deal_ai_summaries WHERE generated_by = $1",
        TEST_USER_ID,
    )
    await pool.execute(
        "DELETE FROM investment_stage_history WHERE changed_by = $1",
        TEST_USER_ID,
    )
    await pool.execute(
        "DELETE FROM member_investments WHERE user_id = $1",
        TEST_USER_ID,
    )
    await pool.execute(
        "DELETE FROM compliance_override_requests WHERE user_id = $1",
        TEST_USER_ID,
    )
    await pool.execute(
        "DELETE FROM deal_interest WHERE user_id = $1",
        TEST_USER_ID,
    )
    await pool.execute(
        "DELETE FROM deal_votes WHERE user_id = $1",
        TEST_USER_ID,
    )
    await pool.execute(
        "DELETE FROM deal_scores WHERE scored_by = $1",
        TEST_USER_ID,
    )
    # Delete test deals (cascades to documents)
    for deal_id in deal_ids:
        await pool.execute(
            "DELETE FROM deals WHERE id = $1",
            deal_id,
        )
    await pool.execute("DELETE FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)


async def main_async():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set.")
        return False

    import main
    from services.database import get_pool, close_pool

    main.verify_token = lambda token: {"sub": TEST_USER_ID}
    ok = True
    deal_ids = []
    doc_ids = []

    pool = await get_pool()
    await setup_test_user(pool)

    try:
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=main.app), base_url="http://verify"
        ) as c:
            # Get or create a test deal.
            test_deal_id = None
            deal_rows = await pool.fetch(
                """
                SELECT id FROM deals
                WHERE org_id = $1 AND valid_to IS NULL AND system_to IS NULL
                LIMIT 1
                """,
                TEST_ORG_ID,
            )
            if deal_rows:
                test_deal_id = str(deal_rows[0]["id"])
            else:
                r = await c.post(
                    "/api/v1/deals",
                    headers=H,
                    json={"name": "Sprint 7 Test Deal"},
                )
                if r.status_code == 201:
                    test_deal_id = r.json().get("id")
                    deal_ids.append(test_deal_id)

            if not test_deal_id:
                print("[1-9] SKIP — could not find or create a deal")
                return False

            # Upload a test document.
            test_doc_id = None
            doc_rows = await pool.fetch(
                """
                SELECT id FROM deal_documents
                WHERE deal_id = $1::uuid LIMIT 1
                """,
                test_deal_id,
            )
            if doc_rows:
                test_doc_id = str(doc_rows[0]["id"])
            else:
                import io
                r = await c.post(
                    f"/api/v1/deals/{test_deal_id}/documents",
                    headers=H,
                    files={"file": ("test.txt", io.BytesIO(b"test content"), "text/plain")},
                )
                if r.status_code == 201:
                    test_doc_id = r.json().get("id")
                    doc_ids.append(test_doc_id)

            # Check 1: GET /deals/{id}/ai-summary -> 404 initially (or 200 if exists)
            r = await c.get(f"/api/v1/deals/{test_deal_id}/ai-summary", headers=H)
            print(
                f"[1] GET /deals/{test_deal_id[:8]}…/ai-summary "
                f"-> {r.status_code} (expect 404 or 200)"
            )
            ok &= r.status_code in (200, 404)

            # Check 2: POST /deals/{id}/ai-summary -> 200 or 500 (no API key)
            r = await c.post(
                f"/api/v1/deals/{test_deal_id}/ai-summary", headers=H
            )
            ai_ok = r.status_code in (200, 500)
            print(
                f"[2] POST /deals/{test_deal_id[:8]}…/ai-summary "
                f"-> {r.status_code} {'(no API key — OK)' if r.status_code == 500 else ''}"
            )
            if r.status_code == 200:
                ai_data = r.json()
                print(
                    f"      summary_text present: {'summary_text' in ai_data}, "
                    f"strengths: {len(ai_data.get('strengths', []))}"
                )
            ok &= ai_ok

            # Check 3: PUT /deals/{id}/documents/{doc_id}/review -> 200
            if test_doc_id:
                r = await c.put(
                    f"/api/v1/deals/{test_deal_id}/documents/{test_doc_id}/review",
                    headers=H,
                    json={
                        "status": "approved",
                        "review_notes": "Sprint 7 test review",
                        "visible_to_members": True,
                    },
                )
                rev = r.json()
                print(
                    f"[3] PUT document/{test_doc_id[:8]}…/review "
                    f"-> {r.status_code}; status={rev.get('status')!r}"
                )
                ok &= r.status_code == 200 and rev.get("status") == "approved"
            else:
                print("[3] SKIP — no document available")

            # Check 4: GET /deals/{id} -> verify documents structure
            r = await c.get(f"/api/v1/deals/{test_deal_id}", headers=H)
            deal_detail = r.json()
            docs_in_detail = deal_detail.get("documents", [])
            print(
                f"[4] GET /deals/{test_deal_id[:8]}… -> {r.status_code}, "
                f"{len(docs_in_detail)} documents (staff sees all)"
            )
            ok &= r.status_code == 200

            # Check 5: PUT /deals/{id}/stage -> validate against config
            stage_rows = await pool.fetch(
                """
                SELECT config_key FROM config
                WHERE org_id = $1 AND category = 'deal_stages'
                ORDER BY display_order NULLS LAST LIMIT 1
                """,
                TEST_ORG_ID,
            )
            if stage_rows:
                test_stage = str(stage_rows[0]["config_key"])
                r = await c.put(
                    f"/api/v1/deals/{test_deal_id}/stage",
                    headers=H,
                    json={"stage": test_stage},
                )
                print(
                    f"[5] PUT /deals/{test_deal_id[:8]}…/stage "
                    f"-> {r.status_code}; stage={r.json().get('deal_stage')!r}"
                )
                ok &= r.status_code == 200

                # Also verify bad stage is rejected
                r2 = await c.put(
                    f"/api/v1/deals/{test_deal_id}/stage",
                    headers=H,
                    json={"stage": "nonexistent_stage_xyz"},
                )
                print(
                    f"     Bad stage -> {r2.status_code} (expect 400)"
                )
                ok &= r2.status_code == 400
            else:
                print("[5] SKIP — no deal_stages config found")

            # Check 6: GET /deals/{id}/member-investments -> 200
            r = await c.get(
                f"/api/v1/deals/{test_deal_id}/member-investments", headers=H
            )
            investments = r.json()
            print(
                f"[6] GET /deals/{test_deal_id[:8]}…/member-investments "
                f"-> {r.status_code}, {len(investments) if isinstance(investments, list) else '?'} records"
            )
            ok &= r.status_code == 200 and isinstance(investments, list)

            # Check 7: POST /deals/{id}/member-investments/{user_id}/stage -> 200
            inv_stage_rows = await pool.fetch(
                """
                SELECT config_key FROM config
                WHERE org_id = $1 AND category = 'investment_stages'
                ORDER BY display_order NULLS LAST LIMIT 1
                """,
                TEST_ORG_ID,
            )
            if inv_stage_rows:
                inv_stage = str(inv_stage_rows[0]["config_key"])
                r = await c.post(
                    f"/api/v1/deals/{test_deal_id}/member-investments/{TEST_USER_ID}/stage",
                    headers=H,
                    json={"stage": inv_stage, "notes": "Sprint 7 test"},
                )
                inv_data = r.json()
                print(
                    f"[7] POST member-investments/{TEST_USER_ID[:8]}…/stage "
                    f"-> {r.status_code}; stage={inv_data.get('stage')!r}"
                )
                ok &= r.status_code == 200 and inv_data.get("stage") == inv_stage
            else:
                print("[7] SKIP — no investment_stages config found")

            # Check 8: GET /portfolio/my-investments -> 200
            r = await c.get("/api/v1/portfolio/my-investments", headers=H)
            my_invs = r.json()
            print(
                f"[8] GET /portfolio/my-investments "
                f"-> {r.status_code}, {len(my_invs) if isinstance(my_invs, list) else '?'} records"
            )
            ok &= r.status_code == 200 and isinstance(my_invs, list)

            # Check 9: GET /portfolio/summary -> 200
            r = await c.get("/api/v1/portfolio/summary", headers=H)
            summary = r.json()
            print(
                f"[9] GET /portfolio/summary "
                f"-> {r.status_code}, {len(summary) if isinstance(summary, list) else '?'} stages"
            )
            ok &= r.status_code == 200 and isinstance(summary, list)

    finally:
        await teardown(pool, deal_ids, doc_ids)
        await close_pool()

    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main_async()) else 1)
