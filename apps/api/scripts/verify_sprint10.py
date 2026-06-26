"""Verification script for Sprint 10 (Client Intelligence Layer).

Runs the real FastAPI app in-process against the live database, stubbing only
the JWT signature check.

    cd apps/api
    DATABASE_URL='postgresql://...:6543/postgres' python scripts/verify_sprint10.py

Checks:
  1. POST conversation/start -> conversation created with an opening AI message
  2. POST conversation/message -> AI responds, messages array grows
  3. Foundation questions loaded from DB (category='foundation')
  4. POST /entities/{id}/notes -> note created, extraction_status='pending'
  5. extract_from_note -> extracted_fields populated, status='completed'
     (skipped without ANTHROPIC_API_KEY)
  6. POST /investment-profile/{id}/brief -> returns brief_text
     (skipped without ANTHROPIC_API_KEY)
  7. GET /investment-profile/{id}/brief -> returns current brief
     (skipped without ANTHROPIC_API_KEY)
  8. POST /extract + PUT extraction review -> advisor_reviewed=true
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

H = {"Authorization": "Bearer local-verify"}
TEST_USER_ID = "99000000-0000-0000-0000-000000000010"
TEST_AUTH0_SUB = "auth0|test_verify_10_user"
TEST_ORG_ID = "00000000-0000-0000-0000-000000000001"


async def setup(pool):
    await pool.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'test10@2ndactcapital.com', 'Test 10 User', $3, 'admin')
        ON CONFLICT (auth0_sub) DO UPDATE SET role = 'admin'
        """,
        TEST_USER_ID, TEST_ORG_ID, TEST_AUTH0_SUB,
    )
    entity_id = await pool.fetchval(
        """
        INSERT INTO entities (org_id, entity_type, display_name)
        VALUES ($1, 'individual', 'Sprint10 Verify Entity')
        RETURNING id
        """,
        TEST_ORG_ID,
    )
    return str(entity_id)


async def teardown(pool, entity_id):
    await pool.execute("DELETE FROM entity_notes WHERE entity_id = $1", entity_id)
    await pool.execute(
        "DELETE FROM investment_profile_extractions WHERE entity_id = $1", entity_id
    )
    await pool.execute(
        "DELETE FROM investment_profile_answers WHERE entity_id = $1", entity_id
    )
    await pool.execute(
        "DELETE FROM profile_conversations WHERE entity_id = $1", entity_id
    )
    await pool.execute("DELETE FROM entity_briefs WHERE entity_id = $1", entity_id)
    await pool.execute("DELETE FROM entities WHERE id = $1", entity_id)
    await pool.execute("DELETE FROM audit_log WHERE user_id = $1", TEST_USER_ID)
    await pool.execute("DELETE FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)


async def main_async():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set.")
        return False

    import main
    from services.database import get_pool, close_pool
    from services.extraction import extract_from_note

    main.verify_token = lambda token: {"sub": TEST_USER_ID}
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    ok = True

    pool = await get_pool()
    entity_id = await setup(pool)

    try:
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=main.app), base_url="http://verify"
        ) as c:
            base = f"/api/v1/investment-profile/{entity_id}"

            # --- Check 3: Foundation questions loaded from DB ---------------
            r = await c.get(
                "/api/v1/investment-profile/questions",
                headers=H, params={"category": "foundation"},
            )
            foundation = r.json()
            n_foundation = len(foundation) if isinstance(foundation, list) else 0
            print(f"[3] foundation questions in DB -> {r.status_code}, "
                  f"{n_foundation} (expect 10)")
            ok &= r.status_code == 200 and n_foundation >= 1

            # --- Check 1: start conversation -------------------------------
            r = await c.post(f"{base}/conversation/start", headers=H)
            convo = r.json()
            msgs = convo.get("messages", []) if isinstance(convo, dict) else []
            has_opening = bool(msgs) and msgs[0].get("role") == "assistant"
            print(f"[1] conversation/start -> {r.status_code}; "
                  f"opening assistant message: {has_opening}")
            ok &= r.status_code == 200 and has_opening

            # --- Check 2: send a message -----------------------------------
            r = await c.post(
                f"{base}/conversation/message",
                headers=H,
                json={"message": "I want to preserve capital for my grandchildren."},
            )
            mres = r.json()
            print(f"[2] conversation/message -> {r.status_code}; "
                  f"ai message present: {bool(mres.get('message'))}; "
                  f"index={mres.get('question_index')}")
            ok &= r.status_code == 200 and bool(mres.get("message"))

            r = await c.get(f"{base}/conversation", headers=H)
            convo2 = r.json()
            grew = len(convo2.get("messages", [])) > len(msgs)
            print(f"[2b] messages array grew -> {grew}")
            ok &= grew

            # --- Check 4: create a note ------------------------------------
            r = await c.post(
                f"/api/v1/entities/{entity_id}/notes",
                headers=H,
                json={
                    "note_text": "Client mentioned they sold their company and "
                    "now have significant liquidity. New email is jh@example.com.",
                    "note_type": "meeting",
                },
            )
            note = r.json()
            note_id = note.get("id")
            print(f"[4] POST notes -> {r.status_code}; "
                  f"status={note.get('extraction_status')!r}")
            ok &= r.status_code == 201 and note.get("extraction_status") == "pending"

            # --- Check 5: extract_from_note --------------------------------
            if has_key and note_id:
                await extract_from_note(
                    pool, TEST_ORG_ID, note_id, entity_id,
                    "Client sold their company; new email jh@example.com.",
                )
                row = await pool.fetchrow(
                    "SELECT extracted_fields, extraction_status FROM entity_notes "
                    "WHERE id = $1",
                    note_id,
                )
                done = row["extraction_status"] == "completed" and row["extracted_fields"] is not None
                print(f"[5] extract_from_note -> status={row['extraction_status']!r}, "
                      f"fields present: {row['extracted_fields'] is not None}")
                ok &= done
            else:
                print("[5] SKIP — ANTHROPIC_API_KEY not set")

            # --- Seed a foundation answer for extraction/brief tests -------
            if foundation:
                await c.post(
                    f"{base}/answers",
                    headers=H,
                    json={
                        "question_id": foundation[0]["id"],
                        "answer_value": "My goal is to protect my family's wealth "
                        "for at least 20 years and never risk a catastrophic loss.",
                    },
                )

            # --- Check 8: extraction + review ------------------------------
            r = await c.post(f"{base}/extract", headers=H)
            extractions = r.json()
            print(f"[8a] POST extract -> {r.status_code}, "
                  f"{len(extractions) if isinstance(extractions, list) else '?'} extractions")
            ok &= r.status_code == 200 and isinstance(extractions, list)

            if extractions:
                ext_id = extractions[0]["id"]
                r = await c.put(
                    f"{base}/extractions/{ext_id}/review",
                    headers=H,
                    json={"accepted": True},
                )
                reviewed = r.json()
                print(f"[8b] PUT extraction review -> {r.status_code}; "
                      f"advisor_reviewed={reviewed.get('advisor_reviewed')}")
                ok &= r.status_code == 200 and reviewed.get("advisor_reviewed") is True
            else:
                print("[8b] SKIP — no extraction created (no foundation answer?)")

            # --- Checks 6 & 7: brief --------------------------------------
            if has_key:
                r = await c.post(f"{base}/brief", headers=H)
                brief = r.json()
                print(f"[6] POST brief -> {r.status_code}; "
                      f"brief_text present: {bool(brief.get('brief_text'))}")
                ok &= r.status_code == 200 and bool(brief.get("brief_text"))

                r = await c.get(f"{base}/brief", headers=H)
                gb = r.json()
                print(f"[7] GET brief -> {r.status_code}; "
                      f"brief_text present: {bool(gb and gb.get('brief_text'))}")
                ok &= r.status_code == 200 and bool(gb and gb.get("brief_text"))
            else:
                print("[6] SKIP — ANTHROPIC_API_KEY not set")
                print("[7] SKIP — ANTHROPIC_API_KEY not set")

    finally:
        await teardown(pool, entity_id)
        await close_pool()

    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main_async()) else 1)
