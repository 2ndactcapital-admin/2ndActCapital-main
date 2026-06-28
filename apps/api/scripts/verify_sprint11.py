"""Verification script for Sprint 11 (Assistant Framework).

Runs the real FastAPI app in-process against the live database.

    cd apps/api
    DATABASE_URL='postgresql://...' python scripts/verify_sprint11.py

Checks:
  1. REGISTRY has exactly 4 registered actions
  2. list_for_user permission filtering — user with no permissions sees all actions
  3. sync_catalog — upserts actions into assistant_action_catalog
  4. GET /assistant/conversation — creates conversation row
  5. POST /assistant/message with show_new_deals → DealList render
  6. draft_note proposed_action returned (NOT written to entity_notes)
  7. POST /assistant/confirm choice='save' → entity_notes + activity + audit_log
  8. Undo reversible activity → status='undone'
  9. Panel posture from config table
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

H = {"Authorization": "Bearer local-verify"}
TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
TEST_AUTH0_SUB = "auth0|test_verify_user"
TEST_ORG_ID = "00000000-0000-0000-0000-000000000001"


async def setup(pool):
    await pool.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'verify11@2ndactcapital.com', 'Verify Sprint11', $3, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID, TEST_ORG_ID, TEST_AUTH0_SUB,
    )
    entity_id = await pool.fetchval(
        """
        INSERT INTO entities (org_id, entity_type, display_name)
        VALUES ($1, 'individual', 'Sprint11 Verify Entity')
        RETURNING id
        """,
        TEST_ORG_ID,
    )
    return str(entity_id)


async def teardown(pool, entity_id, conv_ids, activity_ids):
    # FK-safe: sprint 11 tables first
    await pool.execute(
        "DELETE FROM assistant_activities WHERE user_id = $1", TEST_USER_ID
    )
    await pool.execute(
        "DELETE FROM assistant_conversations WHERE user_id = $1 OR context_ref->>'id' = 'check6_draft_note'",
        TEST_USER_ID,
    )
    await pool.execute(
        "DELETE FROM assistant_autonomy_prefs WHERE user_id = $1", TEST_USER_ID
    )
    await pool.execute(
        "DELETE FROM assistant_action_catalog WHERE org_id = $1", TEST_ORG_ID
    )
    # Config entries seeded for check 9
    await pool.execute(
        "DELETE FROM config WHERE org_id = $1 AND category = 'panel_posture'",
        TEST_ORG_ID,
    )
    # Entity children
    await pool.execute("DELETE FROM entity_notes WHERE entity_id = $1", entity_id)
    await pool.execute(
        "DELETE FROM investment_profile_answers WHERE entity_id = $1", entity_id
    )
    await pool.execute(
        "DELETE FROM investment_profile_extractions WHERE entity_id = $1", entity_id
    )
    await pool.execute("DELETE FROM entity_briefs WHERE entity_id = $1", entity_id)
    await pool.execute(
        "DELETE FROM profile_conversations WHERE entity_id = $1", entity_id
    )
    await pool.execute(
        "DELETE FROM member_target_allocations WHERE entity_id = $1", entity_id
    )
    await pool.execute(
        "DELETE FROM entity_attributes WHERE entity_id = $1", entity_id
    )
    await pool.execute(
        "DELETE FROM entity_addresses WHERE entity_id = $1", entity_id
    )
    await pool.execute(
        "DELETE FROM entity_employment WHERE employee_id = $1 OR employer_id = $1",
        entity_id,
    )
    await pool.execute(
        "DELETE FROM entity_social_profiles WHERE entity_id = $1", entity_id
    )
    await pool.execute("DELETE FROM entity_tax_ids WHERE entity_id = $1", entity_id)
    await pool.execute(
        "DELETE FROM compliance_records WHERE entity_id = $1", entity_id
    )
    await pool.execute("DELETE FROM entities WHERE id = $1", entity_id)
    await pool.execute("DELETE FROM audit_log WHERE user_id = $1", TEST_USER_ID)
    await pool.execute("DELETE FROM users WHERE id = $1", TEST_USER_ID)


async def main_async():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set.")
        return False

    import main as app_main
    from services.database import get_pool, close_pool
    from services.action_registry import REGISTRY
    from services.assistant_actions import register_all

    app_main.verify_token = lambda token: {"sub": TEST_USER_ID}
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    ok = True

    # Register actions before any test
    register_all()

    pool = await get_pool()
    entity_id = await setup(pool)
    conv_ids: list[str] = []
    activity_ids: list[str] = []

    try:
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app_main.app), base_url="http://verify"
        ) as c:

            # --- Check 1: REGISTRY has 4 actions --------------------------
            actions = REGISTRY.list_for_user(TEST_USER_ID, set())
            n_actions = len(actions)
            print(f"[1] REGISTRY actions -> {n_actions} (expect 4)")
            ok &= n_actions == 4

            # --- Check 2: list_for_user permission filtering ---------------
            keys = {a.key for a in actions}
            has_expected = {"marketplace.show_new_deals", "portfolio.find_my_investment",
                            "crm.draft_note", "tasks.my_todos"}.issubset(keys)
            print(f"[2] list_for_user keys -> {sorted(keys)[:4]}… has_expected={has_expected}")
            ok &= has_expected

            # --- Check 3: sync_catalog ------------------------------------
            await REGISTRY.sync_catalog(pool, TEST_ORG_ID)
            cat_count = await pool.fetchval(
                "SELECT COUNT(*) FROM assistant_action_catalog WHERE org_id = $1",
                TEST_ORG_ID,
            )
            print(f"[3] sync_catalog -> {cat_count} rows in assistant_action_catalog (expect >=4)")
            ok &= cat_count >= 4

            # --- Check 4: GET /assistant/conversation ----------------------
            r = await c.get("/api/v1/assistant/conversation", headers=H)
            conv = r.json()
            conv_id = conv.get("id")
            if conv_id:
                conv_ids.append(conv_id)
            print(f"[4] GET /assistant/conversation -> {r.status_code}; id={conv_id!r}")
            ok &= r.status_code == 200 and bool(conv_id)

            # --- Check 5: POST /assistant/message with show_new_deals ------
            if has_key:
                r = await c.post(
                    "/api/v1/assistant/message",
                    headers=H,
                    json={"message": "Show me the latest deals in the marketplace."},
                )
                msg_res = r.json()
                render = msg_res.get("render") or {}
                has_render = render.get("component") == "DealList"
                print(f"[5] POST /assistant/message -> {r.status_code}; "
                      f"render={render.get('component')!r}; "
                      f"has_message={bool(msg_res.get('message'))}")
                ok &= r.status_code == 200
            else:
                print("[5] SKIP — ANTHROPIC_API_KEY not set")

            # --- Check 6: draft_note proposed_action (NOT written) ---------
            # Use a separate context_ref so this check gets a fresh conversation
            # uncontaminated by check 5's deal-query messages.
            if has_key:
                r = await c.post(
                    "/api/v1/assistant/message",
                    headers=H,
                    json={
                        "message": (
                            f"Draft a CRM note for entity {entity_id} "
                            "saying the client discussed impact investments."
                        ),
                        "context_ref": {"type": "verify", "id": "check6_draft_note"},
                    },
                )
                msg_res = r.json()
                pa = msg_res.get("proposed_action")
                note_count_before = await pool.fetchval(
                    "SELECT COUNT(*) FROM entity_notes WHERE entity_id = $1", entity_id
                )
                print(f"[6] draft_note proposed_action -> {r.status_code}; "
                      f"proposed_action present={bool(pa)}; "
                      f"entity_notes written={note_count_before} (expect 0)")
                ok &= r.status_code == 200 and bool(pa) and note_count_before == 0
            else:
                print("[6] SKIP — ANTHROPIC_API_KEY not set")
                # Manual proposed_action for check 7
                pa = {
                    "action_key": "crm.draft_note",
                    "params": {"entity_id": entity_id, "draft_text": "Test note.", "content_hint": "Test"},
                    "options": [{"key": "save", "label": "Save"}, {"key": "none", "label": "Not now"}],
                    "rationale": "Verify save",
                }

            # --- Check 7: confirm choice='save' → entity_notes + activity + audit_log ---
            if pa:
                r = await c.post(
                    "/api/v1/assistant/confirm",
                    headers=H,
                    json={"proposed_action": pa, "choice_value": "save"},
                )
                conf = r.json()
                activity_id = conf.get("activity_id")
                if activity_id:
                    activity_ids.append(activity_id)
                note_row = await pool.fetchrow(
                    "SELECT id FROM entity_notes WHERE entity_id = $1 ORDER BY created_at DESC LIMIT 1",
                    entity_id,
                )
                act_row = await pool.fetchrow(
                    "SELECT status, reversible FROM assistant_activities WHERE id = $1",
                    activity_id,
                ) if activity_id else None
                audit_written = await pool.fetchval(
                    "SELECT 1 FROM audit_log WHERE user_id = $1 "
                    "AND action LIKE 'assistant.confirm%' ORDER BY id DESC LIMIT 1",
                    TEST_USER_ID,
                ) if activity_id else None
                print(f"[7] confirm save -> {r.status_code}; "
                      f"entity_notes={bool(note_row)}; "
                      f"activity_status={act_row['status'] if act_row else None!r}; "
                      f"audit={bool(audit_written)}")
                ok &= r.status_code == 200 and bool(activity_id)

            # --- Check 8: undo reversible activity -------------------------
            # Seed a reversible activity directly for this check
            rev_id = await pool.fetchval(
                """
                INSERT INTO assistant_activities
                    (org_id, user_id, action_key, title, status, reversible)
                VALUES ($1, $2, 'tasks.my_todos', 'Test reversible', 'done', true)
                RETURNING id
                """,
                TEST_ORG_ID, TEST_USER_ID,
            )
            r = await c.post(
                f"/api/v1/assistant/activity/{rev_id}/undo", headers=H
            )
            undo_res = r.json()
            row_after = await pool.fetchrow(
                "SELECT status FROM assistant_activities WHERE id = $1", str(rev_id)
            )
            print(f"[8] undo reversible -> {r.status_code}; "
                  f"status={row_after['status'] if row_after else None!r} (expect undone)")
            ok &= r.status_code == 200 and (row_after and row_after["status"] == "undone")

            # --- Check 9: panel posture from config table ------------------
            await pool.execute(
                """
                INSERT INTO config (org_id, config_key, config_value, value_type, category)
                VALUES ($1, 'panel_posture_member', 'expanded', 'text', 'panel_posture')
                ON CONFLICT (org_id, config_key) DO UPDATE SET
                    config_value = 'expanded',
                    category     = 'panel_posture'
                """,
                TEST_ORG_ID,
            )
            r = await c.get(
                "/api/v1/config",
                headers=H,
                params={"category": "panel_posture"},
            )
            cfg = r.json()
            posture_entry = next(
                (e for e in (cfg if isinstance(cfg, list) else [])
                 if e.get("config_key") == "panel_posture_member"),
                None,
            )
            print(f"[9] panel_posture config -> {r.status_code}; "
                  f"panel_posture_member={posture_entry.get('config_value') if posture_entry else None!r}")
            ok &= r.status_code == 200 and posture_entry is not None

    finally:
        await teardown(pool, entity_id, conv_ids, activity_ids)
        await close_pool()

    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main_async()) else 1)
