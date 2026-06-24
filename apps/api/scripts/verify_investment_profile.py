"""Local verification for the Investment Profile endpoints (Sprint 3, Part B).

Runs the real FastAPI app in-process against the live database, stubbing only
the JWT signature check. Use where the database is reachable:

    cd apps/api
    DATABASE_URL='postgresql://...:6543/postgres' python scripts/verify_investment_profile.py

Checks: questions list returns the 10 seeded questions, an upsert saves an
answer and writes audit_log, the saved answer is returned by the answers
endpoint, and re-upserting the same question does not create a duplicate.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

JAMES_HARGROVE = "10000000-0000-0000-0000-000000000003"  # individual entity
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
        # DoD 4: questions
        r = await c.get("/api/v1/investment-profile/questions", headers=H)
        questions = r.json()
        print(f"[4] GET questions -> {r.status_code}, {len(questions)} questions")
        for q in questions[:3]:
            print(f"      [{q['category']}] {q['question_key']} ({q['question_type']})")
        ok &= r.status_code == 200 and len(questions) == 10

        first = questions[0]
        pool = await get_pool()

        # DoD 5: upsert saves an answer + writes audit_log
        before = await audit_count(pool)
        r = await c.post(
            f"/api/v1/investment-profile/{JAMES_HARGROVE}/answers",
            json={"question_id": first["id"], "answer_value": "true"},
            headers=H,
        )
        print(f"[5] POST upsert -> {r.status_code}, id={r.json().get('id')}")
        after = await audit_count(pool)
        print(f"      audit_log rows: {before} -> {after}")
        ok &= r.status_code == 201 and before is not None and after == before + 1

        # DoD 7: answer is persisted / returned
        r = await c.get(
            f"/api/v1/investment-profile/{JAMES_HARGROVE}/answers", headers=H
        )
        answers = r.json()
        saved = next((a for a in answers if a["question_id"] == first["id"]), None)
        print(
            f"[7] GET answers -> {r.status_code}, {len(answers)} answers; "
            f"saved value={saved and saved['answer_value']}"
        )
        ok &= r.status_code == 200 and saved is not None and saved["answer_value"] == "true"

        # Upsert idempotency: same question should update, not duplicate
        r = await c.post(
            f"/api/v1/investment-profile/{JAMES_HARGROVE}/answers",
            json={"question_id": first["id"], "answer_value": "false"},
            headers=H,
        )
        r2 = await c.get(
            f"/api/v1/investment-profile/{JAMES_HARGROVE}/answers", headers=H
        )
        dupes = [a for a in r2.json() if a["question_id"] == first["id"]]
        print(f"[extra] re-upsert -> {r.status_code}, rows for question={len(dupes)} (expect 1)")
        ok &= len(dupes) == 1 and dupes[0]["answer_value"] == "false"

    await close_pool()
    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main_async()) else 1)
