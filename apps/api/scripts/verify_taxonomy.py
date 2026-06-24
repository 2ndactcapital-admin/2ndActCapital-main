"""Verification script for Sprint 6: Asset Taxonomy.

Runs the real FastAPI app in-process against the live database, stubbing
only the JWT signature check.

    cd apps/api
    DATABASE_URL='postgresql://...:6543/postgres' python scripts/verify_taxonomy.py

Checks:
  1. GET /taxonomy -> 8 super_classes
  2. SC7 has a "Volatility Strategies" major class
  3. Volatility Strategies has 4 sub-categories incl. Long Vol + Short Vol
  4. POST /deals with valid taxonomy keys -> 201
  5. POST /deals with non-existent taxonomy key -> 422
  6. GET /config?category=asset_taxonomy -> 150+ rows
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

H = {"Authorization": "Bearer local-verify"}
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


async def teardown(pool, deal_ids):
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

    pool = await get_pool()
    await setup_test_user(pool)

    try:
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=main.app), base_url="http://verify"
        ) as c:

            # Check 1: GET /taxonomy -> 8 super_classes
            r = await c.get("/api/v1/taxonomy", headers=H)
            taxonomy = r.json()
            scs = taxonomy.get("super_classes", [])
            print(
                f"[1] GET /taxonomy -> {r.status_code}, {len(scs)} super_classes"
            )
            for sc in scs:
                mc_count = len(sc.get("major_classes", []))
                print(f"      {sc['key']}: {sc['label']!r} ({mc_count} MCs)")
            ok &= r.status_code == 200 and len(scs) == 8

            # Check 2: SC7 has "Volatility Strategies" major class
            sc7 = next((s for s in scs if s["key"] == "taxonomy_sc_7"), None)
            vol_mc = None
            if sc7:
                vol_mc = next(
                    (
                        m
                        for m in sc7.get("major_classes", [])
                        if "Volatility" in m.get("label", "")
                    ),
                    None,
                )
            found = vol_mc["key"] if vol_mc else "NOT FOUND"
            print(f"[2] SC7 Volatility Strategies MC: {found!r}")
            ok &= vol_mc is not None

            # Check 3: Volatility Strategies has 4 sub-categories
            if vol_mc:
                subs = vol_mc.get("sub_categories", [])
                sub_labels = [s["label"] for s in subs]
                has_long = any("Long Vol" in l for l in sub_labels)
                has_short = any("Short Vol" in l for l in sub_labels)
                print(
                    f"[3] Volatility Strategies subs ({len(subs)}): {sub_labels}"
                )
                ok &= len(subs) == 4 and has_long and has_short
            else:
                print("[3] SKIP — Volatility Strategies MC not found")
                ok = False

            # Check 4: POST /deals with valid taxonomy keys -> 201
            if scs:
                sc = scs[0]
                mcs = sc.get("major_classes", [])
                if mcs:
                    mc = mcs[0]
                    body = {
                        "name": "Taxonomy Verify Deal",
                        "asset_super_class": sc["key"],
                        "asset_class": mc["key"],
                    }
                    subs = mc.get("sub_categories", [])
                    if subs:
                        body["asset_sub_category"] = subs[0]["key"]
                    r = await c.post("/api/v1/deals", headers=H, json=body)
                    created = r.json()
                    print(
                        f"[4] POST /deals (valid taxonomy) -> {r.status_code}; "
                        f"slug={created.get('slug')!r}"
                    )
                    ok &= r.status_code == 201
                    if r.status_code == 201 and created.get("id"):
                        deal_ids.append(created["id"])
                else:
                    print("[4] SKIP — no major classes in first super-class")
            else:
                print("[4] SKIP — taxonomy empty")

            # Check 5: POST /deals with non-existent taxonomy key -> 422
            r = await c.post(
                "/api/v1/deals",
                headers=H,
                json={"name": "Bad Taxonomy Deal", "asset_class": "taxonomy_mc_99_99"},
            )
            print(
                f"[5] POST /deals (invalid taxonomy key) -> {r.status_code}"
            )
            ok &= r.status_code == 422

            # Check 6: GET /config?category=asset_taxonomy -> 150+ rows
            r = await c.get(
                "/api/v1/config", headers=H, params={"category": "asset_taxonomy"}
            )
            rows = r.json()
            print(
                f"[6] GET /config?category=asset_taxonomy -> {r.status_code}, "
                f"{len(rows)} rows"
            )
            ok &= r.status_code == 200 and len(rows) >= 150

    finally:
        await teardown(pool, deal_ids)
        await close_pool()

    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main_async()) else 1)
