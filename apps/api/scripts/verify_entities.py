"""Local verification for the Entity/CRM endpoints (Sprint 2).

Runs the real FastAPI app in-process against the live database and stubs ONLY
the JWT signature check, so no Auth0 token is needed. Use in an environment
where the database is reachable (local machine, Render shell, etc.).

    cd apps/api
    DATABASE_URL='postgresql://...:6543/postgres' python scripts/verify_entities.py

Checks: list returns the seeded entities, create writes an entity + audit_log
row, the Hargrove Capital LLC ownership graph shows both owners, and the >100%
ownership guard rejects.
"""

import asyncio
import os
import secrets
import sys

# Make the apps/api package importable when run from anywhere.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

HARGROVE_LLC = "10000000-0000-0000-0000-000000000002"
MERIDIAN = "10000000-0000-0000-0000-000000000005"
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
        r = await c.get("/api/v1/entities", headers=H)
        rows = r.json()
        print(f"[1] GET /entities -> {r.status_code}, {len(rows)} entities")
        for e in rows:
            print(f"      {e['display_name']:<26} {e['entity_type']}")
        ok &= r.status_code == 200 and len(rows) >= 5

        pool = await get_pool()
        before = await audit_count(pool)
        sfx = secrets.token_hex(3)
        r = await c.post(
            "/api/v1/entities",
            json={"entity_type": "llc", "display_name": f"Verify {sfx}"},
            headers=H,
        )
        print(f"[2] POST /entities -> {r.status_code}, id={r.json().get('id')}")
        after = await audit_count(pool)
        print(f"      audit_log rows: {before} -> {after}")
        ok &= r.status_code == 201 and before is not None and after == before + 1

        r = await c.get(f"/api/v1/entities/{HARGROVE_LLC}/ownership-graph", headers=H)
        g = r.json()
        owners = [e for e in g.get("edges", []) if e["child_id"] == HARGROVE_LLC]
        print(
            f"[3] ownership-graph -> {r.status_code}, "
            f"{len(g.get('nodes', []))} nodes, {len(owners)} owners of Hargrove LLC"
        )
        ok &= r.status_code == 200 and len(owners) == 2

        r = await c.post(
            "/api/v1/entity-ownership",
            json={"parent_id": MERIDIAN, "child_id": HARGROVE_LLC, "ownership_pct": 50},
            headers=H,
        )
        print(f"[4] >100% ownership rejected -> {r.status_code} (expect 400)")
        ok &= r.status_code == 400

    await close_pool()
    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main_async()) else 1)
