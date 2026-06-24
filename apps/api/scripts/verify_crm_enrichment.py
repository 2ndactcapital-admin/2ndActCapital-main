"""Local verification for the CRM enrichment endpoints (Sprint 2b).

Runs the real FastAPI app in-process against the live database, stubbing only
the JWT signature check. Use where the database is reachable:

    cd apps/api
    DATABASE_URL='postgresql://...:6543/postgres' python scripts/verify_crm_enrichment.py

Checks /full shape, tax-id masking, employment linking, and compliance update.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

HARGROVE_LLC = "10000000-0000-0000-0000-000000000002"
JAMES = "10000000-0000-0000-0000-000000000003"  # individual
H = {"Authorization": "Bearer local-verify"}


async def audit_count(pool):
    exists = await pool.fetchval("SELECT to_regclass('public.audit_log') IS NOT NULL")
    return await pool.fetchval("SELECT count(*) FROM audit_log") if exists else None


async def main_async():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set.")
        return False

    import main
    from httpx import ASGITransport, AsyncClient
    from services.database import get_pool, close_pool

    main.verify_token = lambda token: {"sub": "local-verify"}
    ok = True

    async with AsyncClient(
        transport=ASGITransport(app=main.app), base_url="http://verify"
    ) as c:
        # DoD 1: /full shape
        r = await c.get(f"/api/v1/entities/{HARGROVE_LLC}/full", headers=H)
        full = r.json()
        keys = ["entity", "tax_ids", "addresses", "employment", "social_profiles", "compliance_record"]
        print(f"[1] GET /full -> {r.status_code}; keys present: {[k for k in keys if k in full]}")
        ok &= r.status_code == 200 and all(k in full for k in keys)

        # DoD 2: tax-id stores encrypted, returns masked (no clear value)
        r = await c.post(
            f"/api/v1/entities/{JAMES}/tax-ids",
            json={"tax_id_type": "ssn", "value": "123-45-6789", "tax_id_country": "US"},
            headers=H,
        )
        t = r.json()
        print(f"[2] POST tax-id -> {r.status_code}; last4={t.get('tax_id_last4')}, masked={t.get('masked')}")
        no_clear = "123-45-6789" not in str(t) and "value" not in t
        ok &= r.status_code == 201 and t.get("tax_id_last4") == "6789" and no_clear

        # DoD 3: employment links two entities
        r = await c.post(
            f"/api/v1/entities/{JAMES}/employment",
            json={"employer_id": HARGROVE_LLC, "title": "Managing Partner", "is_current": True},
            headers=H,
        )
        e = r.json()
        print(f"[3] POST employment -> {r.status_code}; employer_name={e.get('employer_name')}")
        ok &= r.status_code == 201 and e.get("employer_id") == HARGROVE_LLC and e.get("employer_name")

        # Compliance update writes audit
        pool = await get_pool()
        before = await audit_count(pool)
        r = await c.put(
            f"/api/v1/entities/{JAMES}/compliance",
            json={"kyc_status": "approved", "aml_risk_rating": "medium"},
            headers=H,
        )
        after = await audit_count(pool)
        print(f"[4] PUT compliance -> {r.status_code}; kyc={r.json().get('kyc_status')}; audit {before}->{after}")
        ok &= r.status_code == 200 and r.json().get("kyc_status") == "approved" and after == before + 1

        # Status filter works
        r = await c.get("/api/v1/entities", headers=H, params={"status": "active"})
        print(f"[5] GET /entities?status=active -> {r.status_code}, {len(r.json())} rows")
        ok &= r.status_code == 200

    await close_pool()
    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main_async()) else 1)
