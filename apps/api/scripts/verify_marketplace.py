"""Local verification for the Marketplace endpoints (Sprint 5).

Runs the real FastAPI app in-process against the live database, stubbing only
the JWT signature check. Use where the database is reachable:

    cd apps/api
    DATABASE_URL='postgresql://...:6543/postgres' python scripts/verify_marketplace.py

Covers: config dimensions, deal list with aggregates, deal detail, create
(+ auto slug), score upsert (+ composite recalculation), vote toggle, the
interest compliance gate (403), and document upload to R2.
"""

import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

H = {"Authorization": "Bearer local-verify"}

# Fixed test identity. The token stub returns this UUID as the ``sub`` claim so
# get_user_id() resolves it directly (a valid UUID sub is returned as-is), and a
# matching row is seeded in ``users`` so FKs like deal_scores.scored_by hold.
TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
TEST_AUTH0_SUB = "auth0|test_verify_user"
TEST_ORG_ID = "00000000-0000-0000-0000-000000000001"


async def setup_test_user(pool):
    await pool.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'test@2ndactcapital.com', 'Test User', $3, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID,
        TEST_ORG_ID,
        TEST_AUTH0_SUB,
    )


async def teardown_test_user(pool):
    await pool.execute("DELETE FROM deal_scores WHERE scored_by = $1", TEST_USER_ID)
    await pool.execute("DELETE FROM deal_votes WHERE user_id = $1", TEST_USER_ID)
    await pool.execute("DELETE FROM deal_interest WHERE user_id = $1", TEST_USER_ID)
    await pool.execute("DELETE FROM deals WHERE created_by = $1", TEST_USER_ID)
    await pool.execute("DELETE FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)


async def audit_count(pool):
    exists = await pool.fetchval("SELECT to_regclass('public.audit_log') IS NOT NULL")
    return await pool.fetchval("SELECT count(*) FROM audit_log") if exists else None


async def main_async():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set.")
        return False

    import main
    from services.database import get_pool, close_pool

    # Token stub returns the seeded test user's UUID as the ``sub`` claim, so
    # every request resolves to a real users row.
    main.verify_token = lambda token: {"sub": TEST_USER_ID}
    ok = True

    pool = await get_pool()
    await setup_test_user(pool)

    try:
        ok = await run_checks()
    finally:
        await teardown_test_user(pool)
        await close_pool()

    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


async def run_checks():
    from httpx import ASGITransport, AsyncClient

    import main

    ok = True

    async with AsyncClient(
        transport=ASGITransport(app=main.app), base_url="http://verify"
    ) as c:
        # DoD 1: config scoring dimensions (from DB, not hardcoded)
        r = await c.get("/api/v1/config", headers=H, params={"category": "deal_scoring"})
        dims = r.json()
        print(f"[1] GET /config?category=deal_scoring -> {r.status_code}, {len(dims)} dimensions")
        for d in dims[:6]:
            print(f"      {d.get('config_key')} (weight={d.get('config_value')})")
        ok &= r.status_code == 200 and len(dims) == 6

        # DoD 2: deals list with vote/score aggregates
        r = await c.get("/api/v1/deals", headers=H)
        deals = r.json()
        print(f"[2] GET /deals -> {r.status_code}, {len(deals)} deals")
        ok &= r.status_code == 200 and len(deals) >= 3
        if not deals:
            print("FAIL: no deals to test against")
            return False
        first = deals[0]
        print(
            f"      first: {first['name']!r} score={first.get('composite_score')} "
            f"votes={first.get('vote_count')} docs={first.get('document_count')}"
        )

        # DoD 3: deal detail with scores/documents/votes
        r = await c.get(f"/api/v1/deals/{first['id']}", headers=H)
        detail = r.json()
        keys = ["deal", "scores", "documents"]
        print(f"[3] GET /deals/{{id}} -> {r.status_code}; keys: {[k for k in keys if k in detail]}")
        ok &= r.status_code == 200 and all(k in detail for k in keys)

        # DoD 3b: create deal -> auto slug
        r = await c.post(
            "/api/v1/deals",
            headers=H,
            json={
                "name": "Verify Test Deal #1",
                "description": "Created by verify script",
                "asset_class": "Private Credit",
                "target_raise": 5000000,
            },
        )
        created = r.json()
        print(f"[4] POST /deals -> {r.status_code}; slug={created.get('slug')!r}, status={created.get('deal_status')}")
        ok &= r.status_code == 201 and bool(created.get("slug")) and created.get("deal_status") == "draft"
        new_id = created.get("id")

        # DoD 4: score one dimension -> composite recalculated
        if dims and new_id:
            dim = dims[0]["config_key"]
            r = await c.post(
                f"/api/v1/deals/{new_id}/scores",
                headers=H,
                json={"dimension": dim, "score": 80, "weight": 1.0},
            )
            print(f"[5] POST /deals/{{id}}/scores ({dim}=80) -> {r.status_code}")
            r2 = await c.get(f"/api/v1/deals/{new_id}", headers=H)
            composite = r2.json()["deal"].get("composite_score")
            print(f"      composite after one score: {composite}")
            ok &= r.status_code == 201 and composite is not None and abs(composite - 80) < 0.01

        # DoD 5: vote toggle — vote, then vote again removes it
        if new_id:
            r = await c.post(f"/api/v1/deals/{new_id}/vote", headers=H, json={"vote": 1})
            s1 = r.json()
            r = await c.post(f"/api/v1/deals/{new_id}/vote", headers=H, json={"vote": 1})
            s2 = r.json()
            print(
                f"[6] vote up -> upvotes={s1.get('upvotes')}, user_vote={s1.get('user_vote')}; "
                f"vote up again -> upvotes={s2.get('upvotes')}, user_vote={s2.get('user_vote')}"
            )
            ok &= s1.get("upvotes") == 1 and s1.get("user_vote") == 1
            ok &= s2.get("upvotes") == 0 and s2.get("user_vote") is None

        # DoD 6: interest without compliance -> 403
        if new_id:
            r = await c.post(
                f"/api/v1/deals/{new_id}/interest",
                headers=H,
                json={"amount_interest": 100000},
            )
            body = r.json()
            err = body.get("detail", {})
            print(f"[7] POST /deals/{{id}}/interest (no compliance) -> {r.status_code}; detail={err}")
            ok &= r.status_code == 403

        # DoD 7: document upload -> R2 object + record pending
        r2_account_id = os.environ.get("R2_ACCOUNT_ID", "")
        if new_id and r2_account_id and r2_account_id != "your-account-id":
            files = {"file": ("test.txt", io.BytesIO(b"hello marketplace"), "text/plain")}
            r = await c.post(
                f"/api/v1/deals/{new_id}/documents",
                headers=H,
                files=files,
                data={"document_type": "other"},
            )
            doc = r.json()
            print(f"[8] POST /deals/{{id}}/documents -> {r.status_code}; status={doc.get('processing_status')}")
            ok &= r.status_code == 201 and doc.get("processing_status") == "pending"
        else:
            print("[8] SKIP document upload — R2 env vars not set")

    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main_async()) else 1)
