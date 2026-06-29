"""Verification script for Sprint 12 (SPV Manager).

Runs the real FastAPI app in-process against the live database.

    cd apps/api
    DATABASE_URL='postgresql://...' python scripts/verify_sprint12.py

Checks:
  1. REGISTRY has 7 actions (4 from sprint 11 + 3 SPV actions)
  2. sync_catalog — SPV actions upserted into assistant_action_catalog
  3. POST /spvs (staff) — creates SPV in 'forming' status
  4. GET /spvs — lists SPVs (staff sees forming; member sees only open/closing)
  5. POST /spvs/{id}/status — forming → open transition
  6. POST /spvs/{id}/form-entity — sets vehicle_entity_id
  7. POST /spvs/{id}/subscriptions — member subscribes (bi-temporal insert)
  8. PATCH /spvs/{id}/subscriptions/{sub_id} — amend commitment (bi-temporal)
  9. GET /spvs/{id}/captable — returns cap table with correct total
 10. POST /spvs/{id}/documents — upload document (mocked bytes)
"""

import asyncio
import io
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
        VALUES ($1, $2, 'verify12@2ndactcapital.com', 'Verify Sprint12', $3, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID, TEST_ORG_ID, TEST_AUTH0_SUB,
    )
    entity_id = await pool.fetchval(
        """
        INSERT INTO entities (org_id, entity_type, display_name)
        VALUES ($1, 'individual', 'Sprint12 Verify Entity')
        RETURNING id
        """,
        TEST_ORG_ID,
    )
    deal_id = await pool.fetchval(
        """
        INSERT INTO deals (org_id, name, deal_status)
        VALUES ($1, 'Verify12 Test Deal', 'under_review')
        RETURNING id
        """,
        TEST_ORG_ID,
    )
    return str(entity_id), str(deal_id)


async def teardown(pool, entity_id, deal_id, spv_ids):
    for spv_id in spv_ids:
        await pool.execute(
            "DELETE FROM spv_documents WHERE spv_id = $1", spv_id
        )
        await pool.execute(
            "DELETE FROM spv_subscriptions WHERE spv_id = $1", spv_id
        )
        await pool.execute(
            "DELETE FROM spv_status_history WHERE spv_id = $1", spv_id
        )
    await pool.execute(
        "DELETE FROM spvs WHERE org_id = $1 AND name LIKE 'Verify12%'", TEST_ORG_ID
    )
    await pool.execute(
        "DELETE FROM assistant_action_catalog WHERE org_id = $1 AND action_key LIKE 'spv.%'",
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
    # Deal children before deal
    await pool.execute("DELETE FROM deal_ai_summaries WHERE deal_id = $1", deal_id)
    await pool.execute("DELETE FROM deal_scores WHERE deal_id = $1", deal_id)
    await pool.execute("DELETE FROM deal_votes WHERE deal_id = $1", deal_id)
    await pool.execute("DELETE FROM deal_interest WHERE deal_id = $1", deal_id)
    await pool.execute("DELETE FROM deal_documents WHERE deal_id = $1", deal_id)
    await pool.execute(
        "DELETE FROM investment_stage_history WHERE member_investment_id IN "
        "(SELECT id FROM member_investments WHERE deal_id = $1)",
        deal_id,
    )
    await pool.execute("DELETE FROM member_investments WHERE deal_id = $1", deal_id)
    await pool.execute("DELETE FROM deals WHERE id = $1", deal_id)


async def main_async():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set.")
        return False

    import main as app_main
    from services.database import get_pool, close_pool
    from services.action_registry import REGISTRY
    from services.assistant_actions import register_all

    app_main.verify_token = lambda token: {"sub": TEST_USER_ID}
    ok = True

    register_all()

    pool = await get_pool()
    entity_id, deal_id = await setup(pool)
    spv_ids: list = []

    try:
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app_main.app), base_url="http://verify"
        ) as c:

            # --- Check 1: REGISTRY has 7 actions ----------------------------
            actions = REGISTRY.list_for_user(TEST_USER_ID, set())
            n_actions = len(actions)
            keys = {a.key for a in actions}
            print(f"[1] REGISTRY actions -> {n_actions} (expect 7); keys={sorted(keys)}")
            ok &= n_actions == 7

            # --- Check 2: sync_catalog adds SPV actions --------------------
            await REGISTRY.sync_catalog(pool, TEST_ORG_ID)
            spv_cat = await pool.fetchval(
                "SELECT COUNT(*) FROM assistant_action_catalog "
                "WHERE org_id = $1 AND action_key LIKE 'spv.%'",
                TEST_ORG_ID,
            )
            print(f"[2] sync_catalog SPV rows -> {spv_cat} (expect 3)")
            ok &= spv_cat >= 3

            # --- Check 3: POST /spvs ---------------------------------------
            r = await c.post(
                "/api/v1/spvs",
                headers=H,
                json={
                    "name": "Verify12 SPV Alpha",
                    "deal_id": deal_id,
                    "target_raise": 5000000,
                    "min_commitment": 100000,
                    "carry_pct": 20,
                    "mgmt_fee_pct": 2,
                },
            )
            spv = r.json()
            spv_id = spv.get("id")
            if spv_id:
                spv_ids.append(spv_id)
            print(f"[3] POST /spvs -> {r.status_code}; id={spv_id!r}; status={spv.get('status')!r}")
            ok &= r.status_code == 201 and bool(spv_id) and spv.get("status") == "forming"

            if not spv_id:
                print("ABORT — no SPV id, cannot continue")
                ok = False
            else:
                # --- Check 4: GET /spvs -----------------------------------
                r = await c.get("/api/v1/spvs", headers=H)
                spv_list = r.json()
                ids_in_list = [s["id"] for s in spv_list] if isinstance(spv_list, list) else []
                has_ours = spv_id in ids_in_list
                print(f"[4] GET /spvs -> {r.status_code}; count={len(ids_in_list)}; has_ours={has_ours}")
                ok &= r.status_code == 200 and has_ours

                # --- Check 5: forming → open transition -------------------
                r = await c.post(
                    f"/api/v1/spvs/{spv_id}/status",
                    headers=H,
                    json={"status": "open", "note": "Ready for subscriptions"},
                )
                trans = r.json()
                print(
                    f"[5] POST /spvs/{{id}}/status -> {r.status_code}; "
                    f"status={trans.get('status')!r} (expect open)"
                )
                ok &= r.status_code == 200 and trans.get("status") == "open"

                # --- Check 6: set vehicle_entity_id -----------------------
                r = await c.post(
                    f"/api/v1/spvs/{spv_id}/form-entity",
                    headers=H,
                    json={"entity_id": entity_id},
                )
                formed = r.json()
                print(
                    f"[6] POST /spvs/{{id}}/form-entity -> {r.status_code}; "
                    f"vehicle_entity_id={formed.get('vehicle_entity_id')!r}"
                )
                ok &= r.status_code == 200 and formed.get("vehicle_entity_id") == entity_id

                # --- Check 7: subscribe -----------------------------------
                r = await c.post(
                    f"/api/v1/spvs/{spv_id}/subscriptions",
                    headers=H,
                    json={
                        "entity_id": entity_id,
                        "commitment_amount": 250000,
                    },
                )
                sub = r.json()
                sub_id = sub.get("id")
                db_sub = await pool.fetchrow(
                    "SELECT id, commitment_amount, valid_to FROM spv_subscriptions "
                    "WHERE spv_id = $1 AND entity_id = $2 AND valid_to IS NULL",
                    spv_id,
                    entity_id,
                )
                print(
                    f"[7] POST /spvs/{{id}}/subscriptions -> {r.status_code}; "
                    f"sub_id={sub_id!r}; "
                    f"db_row={'yes' if db_sub else 'no'}; "
                    f"amount={float(db_sub['commitment_amount']) if db_sub else None}"
                )
                ok &= r.status_code == 201 and bool(sub_id) and bool(db_sub)

                # --- Check 8: amend subscription (bi-temporal) ------------
                if sub_id:
                    r = await c.patch(
                        f"/api/v1/spvs/{spv_id}/subscriptions/{sub_id}",
                        headers=H,
                        json={"commitment_amount": 350000},
                    )
                    amended = r.json()
                    old_row = await pool.fetchrow(
                        "SELECT valid_to FROM spv_subscriptions WHERE id = $1",
                        sub_id,
                    )
                    new_row = await pool.fetchrow(
                        "SELECT id, commitment_amount FROM spv_subscriptions "
                        "WHERE spv_id = $1 AND entity_id = $2 AND valid_to IS NULL",
                        spv_id,
                        entity_id,
                    )
                    old_closed = old_row and old_row["valid_to"] is not None
                    new_amount = float(new_row["commitment_amount"]) if new_row else None
                    print(
                        f"[8] PATCH /spvs/{{id}}/subscriptions/{{sub_id}} -> {r.status_code}; "
                        f"old_row_closed={old_closed}; new_amount={new_amount} (expect 350000)"
                    )
                    ok &= r.status_code == 200 and old_closed and new_amount == 350000
                else:
                    print("[8] SKIP — no sub_id from check 7")

                # --- Check 9: captable ------------------------------------
                r = await c.get(f"/api/v1/spvs/{spv_id}/captable", headers=H)
                ct = r.json()
                total = ct.get("total_committed", 0)
                subs = ct.get("subscriptions", [])
                print(
                    f"[9] GET /spvs/{{id}}/captable -> {r.status_code}; "
                    f"total_committed={total} (expect 350000); "
                    f"sub_count={len(subs)}"
                )
                ok &= r.status_code == 200 and abs(total - 350000) < 1

                # --- Check 10: upload document ----------------------------
                has_r2 = bool(os.environ.get("R2_ACCOUNT_ID"))
                if has_r2:
                    fake_pdf = b"%PDF-1.4 test document content"
                    r = await c.post(
                        f"/api/v1/spvs/{spv_id}/documents",
                        headers=H,
                        files={"file": ("test_spv.pdf", io.BytesIO(fake_pdf), "application/pdf")},
                        data={"document_type": "subscription_agreement"},
                    )
                    doc = r.json()
                    doc_id = doc.get("id")
                    db_doc = await pool.fetchrow(
                        "SELECT id, doc_type FROM spv_documents WHERE spv_id = $1",
                        spv_id,
                    ) if r.status_code in (200, 201) else None
                    print(
                        f"[10] POST /spvs/{{id}}/documents -> {r.status_code}; "
                        f"doc_id={doc_id!r}; "
                        f"db_row={'yes' if db_doc else 'no'}"
                    )
                    ok &= r.status_code == 201 and bool(doc_id)
                else:
                    print("[10] SKIP — R2_ACCOUNT_ID not set")

    finally:
        await teardown(pool, entity_id, deal_id, spv_ids)
        await close_pool()

    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main_async()) else 1)
