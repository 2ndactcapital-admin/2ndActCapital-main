"""Verification script for Sprint 6b.

Runs the real FastAPI app in-process against the live database, stubbing
only the JWT signature check.

    cd apps/api
    DATABASE_URL='postgresql://...:6543/postgres' python scripts/verify_sprint6b.py

Checks:
  1. GET /deals/stage-summary -> list of {stage, count}
  2. GET /deals returns deal_stage field
  3. POST /deals/{id}/compliance-requests -> 201
  4. GET /deals/{id}/compliance-requests -> list (staff)
  5. PUT /deals/{id}/compliance-requests/{req_id} -> approve/deny
  6. GET /config?category=deal_stages -> configured stages
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

H = {"Authorization": "Bearer local-verify"}
TEST_USER_ID = "99000000-0000-0000-0000-000000000002"
TEST_AUTH0_SUB = "auth0|test_verify_6b_user"
TEST_ORG_ID = "00000000-0000-0000-0000-000000000001"


async def setup_test_user(pool):
    await pool.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'test6b@2ndactcapital.com', 'Test 6b User', $3, 'admin')
        ON CONFLICT (auth0_sub) DO UPDATE SET role = 'admin'
        """,
        TEST_USER_ID,
        TEST_ORG_ID,
        TEST_AUTH0_SUB,
    )


async def teardown(pool, deal_ids, compliance_req_ids):
    for rid in compliance_req_ids:
        await pool.execute(
            "DELETE FROM compliance_override_requests WHERE id = $1::uuid", rid
        )
    for did in deal_ids:
        await pool.execute("DELETE FROM deals WHERE id = $1::uuid", did)
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
    compliance_req_ids = []

    pool = await get_pool()
    await setup_test_user(pool)

    try:
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=main.app), base_url="http://verify"
        ) as c:

            # Check 1: GET /deals/stage-summary -> list of {stage, count}
            r = await c.get("/api/v1/deals/stage-summary", headers=H)
            summary = r.json()
            print(
                f"[1] GET /deals/stage-summary -> {r.status_code}, "
                f"{len(summary)} stages"
            )
            if r.status_code == 200 and isinstance(summary, list):
                for item in summary:
                    print(f"      {item.get('stage')!r}: {item.get('count')}")
            ok &= r.status_code == 200 and isinstance(summary, list)

            # Check 2: GET /deals returns deal_stage field
            r = await c.get("/api/v1/deals", headers=H)
            deals_list = r.json()
            has_stage_field = isinstance(deals_list, list) and (
                len(deals_list) == 0 or "deal_stage" in deals_list[0]
            )
            print(
                f"[2] GET /deals -> {r.status_code}, "
                f"{len(deals_list)} deals, deal_stage field present: {has_stage_field}"
            )
            ok &= r.status_code == 200 and has_stage_field

            # Create a deal to test compliance requests
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
                # Create a minimal deal
                r2 = await c.post(
                    "/api/v1/deals",
                    headers=H,
                    json={"name": "6b Test Deal"},
                )
                if r2.status_code == 201:
                    test_deal_id = r2.json().get("id")
                    deal_ids.append(test_deal_id)

            if not test_deal_id:
                print("[3-5] SKIP — could not find or create a deal")
                ok = False
            else:
                # Check 3: POST /deals/{id}/compliance-requests -> 201
                r = await c.post(
                    f"/api/v1/deals/{test_deal_id}/compliance-requests",
                    headers=H,
                    json={"request_notes": "Sprint 6b test request"},
                )
                req_data = r.json()
                print(
                    f"[3] POST /deals/{test_deal_id[:8]}…/compliance-requests "
                    f"-> {r.status_code}; id={str(req_data.get('id', ''))[:8]}…"
                )
                ok &= r.status_code == 201
                if r.status_code == 201 and req_data.get("id"):
                    compliance_req_ids.append(req_data["id"])

                # Check 4: GET /deals/{id}/compliance-requests -> list
                r = await c.get(
                    f"/api/v1/deals/{test_deal_id}/compliance-requests",
                    headers=H,
                )
                reqs = r.json()
                print(
                    f"[4] GET /deals/{test_deal_id[:8]}…/compliance-requests "
                    f"-> {r.status_code}, {len(reqs) if isinstance(reqs, list) else '?'} rows"
                )
                ok &= r.status_code == 200 and isinstance(reqs, list) and len(reqs) >= 1

                # Check 5: PUT /deals/{id}/compliance-requests/{req_id} -> approve
                if compliance_req_ids:
                    req_id = compliance_req_ids[-1]
                    r = await c.put(
                        f"/api/v1/deals/{test_deal_id}/compliance-requests/{req_id}",
                        headers=H,
                        json={"status": "approved", "review_notes": "Approved in test"},
                    )
                    upd = r.json()
                    print(
                        f"[5] PUT compliance-request/{req_id[:8]}… -> "
                        f"{r.status_code}; status={upd.get('status')!r}"
                    )
                    ok &= r.status_code == 200 and upd.get("status") == "approved"
                else:
                    print("[5] SKIP — no compliance request created in check 3")
                    ok = False

            # Check 6: GET /config?category=deal_stages -> configured stages
            r = await c.get(
                "/api/v1/config", headers=H, params={"category": "deal_stages"}
            )
            stage_cfg = r.json()
            print(
                f"[6] GET /config?category=deal_stages -> {r.status_code}, "
                f"{len(stage_cfg)} stages"
            )
            if isinstance(stage_cfg, list):
                for s in stage_cfg:
                    print(f"      {s.get('config_key')!r}: {s.get('config_value')!r}")
            ok &= r.status_code == 200 and isinstance(stage_cfg, list) and len(stage_cfg) >= 1

    finally:
        await teardown(pool, deal_ids, compliance_req_ids)
        await close_pool()

    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main_async()) else 1)
