"""Verification script for Sprint 9 (notification bus + RBAC wiring).

Runs the real FastAPI app in-process against the live database, stubbing only
the JWT signature check.

    cd apps/api
    DATABASE_URL='postgresql://...:6543/postgres' python scripts/verify_sprint9.py

Checks:
  1. notification_bus.publish -> notification + recipient + delivery_log rows
  2. in-app delivery -> delivery_log.status = 'delivered'
  3. mark_read -> recipient.status = 'read'
  4. get_unread_count -> 0 after read
  5. update_delivery_status('failed') -> log updated, recipient -> 'failed'
  6. GET /notifications -> returns the notification
  7. GET /notifications/count -> 0 after read
  8. GET /admin/users -> returns a user list
  9. PUT /admin/users/{id}/role -> role assigned + audit_log row written
 10. has_permission -> True for a granted permission, False for a bogus one
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

H = {"Authorization": "Bearer local-verify"}
TEST_USER_ID = "99000000-0000-0000-0000-000000000009"
TEST_AUTH0_SUB = "auth0|test_verify_9_user"
TEST_ORG_ID = "00000000-0000-0000-0000-000000000001"


async def setup_test_user(pool):
    await pool.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'test9@2ndactcapital.com', 'Test 9 User', $3, 'admin')
        ON CONFLICT (auth0_sub) DO UPDATE SET role = 'admin'
        """,
        TEST_USER_ID, TEST_ORG_ID, TEST_AUTH0_SUB,
    )


async def teardown(pool, notification_ids):
    # Explicitly clear notification children before parents (do not rely solely
    # on ON DELETE CASCADE), then FK references to the test user, then the user.
    await pool.execute(
        "DELETE FROM notification_delivery_log WHERE notification_id = ANY($1::uuid[])",
        notification_ids,
    )
    await pool.execute(
        "DELETE FROM notification_recipients WHERE notification_id = ANY($1::uuid[])",
        notification_ids,
    )
    await pool.execute(
        "DELETE FROM notifications WHERE id = ANY($1::uuid[])",
        notification_ids,
    )
    await pool.execute(
        "DELETE FROM user_notification_preferences WHERE user_id = $1",
        TEST_USER_ID,
    )
    await pool.execute("DELETE FROM user_roles WHERE user_id = $1", TEST_USER_ID)
    # audit_log.user_id has an FK to users — clear it before deleting the user.
    await pool.execute("DELETE FROM audit_log WHERE user_id = $1", TEST_USER_ID)
    await pool.execute("DELETE FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)


async def main_async():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set.")
        return False

    import main
    from services.database import get_pool, close_pool
    from services.notifications import notification_bus
    from services.rbac import has_permission, get_user_permissions

    main.verify_token = lambda token: {"sub": TEST_USER_ID}
    ok = True
    notification_ids = []

    pool = await get_pool()
    await setup_test_user(pool)

    try:
        # --- Check 1: publish creates the three rows ----------------------
        nid = await notification_bus.publish(
            pool, TEST_ORG_ID, "ioi_confirmed",
            "Test Notification", "A test notification body",
            [TEST_USER_ID],
            resource_type="deal", resource_id=None, created_by=TEST_USER_ID,
        )
        if nid:
            notification_ids.append(nid)
        n_count = await pool.fetchval(
            "SELECT COUNT(*) FROM notifications WHERE id = $1", nid
        )
        r_count = await pool.fetchval(
            "SELECT COUNT(*) FROM notification_recipients WHERE notification_id = $1",
            nid,
        )
        d_count = await pool.fetchval(
            "SELECT COUNT(*) FROM notification_delivery_log WHERE notification_id = $1",
            nid,
        )
        print(f"[1] publish -> notif={n_count} recipient={r_count} delivery={d_count}")
        ok &= bool(nid) and n_count == 1 and r_count == 1 and d_count == 1

        # --- Check 2: in-app delivery marked delivered -------------------
        delivered = await pool.fetchval(
            "SELECT status FROM notification_delivery_log "
            "WHERE notification_id = $1 AND channel = 'in_app'",
            nid,
        )
        print(f"[2] in-app delivery status -> {delivered!r}")
        ok &= delivered == "delivered"

        # --- Check 6: GET /notifications returns it (before read) --------
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=main.app), base_url="http://verify"
        ) as c:
            r = await c.get("/api/v1/notifications", headers=H)
            data = r.json()
            found = any(str(n.get("id")) == str(nid) for n in data.get("notifications", []))
            print(f"[6] GET /notifications -> {r.status_code}, contains test notif: {found}")
            ok &= r.status_code == 200 and found

            # --- Check 3: mark_read ------------------------------------
            await notification_bus.mark_read(pool, nid, TEST_USER_ID)
            status = await pool.fetchval(
                "SELECT status FROM notification_recipients "
                "WHERE notification_id = $1 AND user_id = $2",
                nid, TEST_USER_ID,
            )
            print(f"[3] mark_read -> recipient status {status!r}")
            ok &= status == "read"

            # --- Check 4: unread count 0 -------------------------------
            unread = await notification_bus.get_unread_count(pool, TEST_USER_ID, TEST_ORG_ID)
            print(f"[4] get_unread_count -> {unread}")
            ok &= unread == 0

            # --- Check 7: GET /notifications/count = 0 -----------------
            r = await c.get("/api/v1/notifications/count", headers=H)
            cnt = r.json().get("unread_count")
            print(f"[7] GET /notifications/count -> {r.status_code}, {cnt}")
            ok &= r.status_code == 200 and cnt == 0

            # --- Check 8: GET /admin/users -----------------------------
            r = await c.get("/api/v1/admin/users", headers=H)
            users = r.json()
            print(f"[8] GET /admin/users -> {r.status_code}, "
                  f"{len(users) if isinstance(users, list) else '?'} users")
            ok &= r.status_code == 200 and isinstance(users, list)

            # --- Check 9: PUT /admin/users/{id}/role + audit ----------
            role_row = await pool.fetchrow(
                """
                SELECT r.id, r.name
                FROM roles r
                JOIN role_permissions rp ON rp.role_id = r.id
                GROUP BY r.id, r.name
                HAVING COUNT(rp.permission_id) > 0
                LIMIT 1
                """
            )
            if role_row is None:
                print("[9] SKIP — no role with permissions found in DB")
                print("[10] SKIP — depends on check 9")
            else:
                audit_before = await pool.fetchval("SELECT COUNT(*) FROM audit_log")
                r = await c.put(
                    f"/api/v1/admin/users/{TEST_USER_ID}/role",
                    headers=H,
                    json={"role_id": str(role_row["id"])},
                )
                assigned = await pool.fetchval(
                    "SELECT COUNT(*) FROM user_roles WHERE user_id = $1 AND role_id = $2",
                    TEST_USER_ID, role_row["id"],
                )
                audit_after = await pool.fetchval("SELECT COUNT(*) FROM audit_log")
                print(f"[9] PUT /admin/users/{TEST_USER_ID[:8]}…/role -> "
                      f"{r.status_code}; assigned={assigned}; "
                      f"audit +{audit_after - audit_before}")
                ok &= r.status_code == 200 and assigned == 1 and audit_after > audit_before

                # --- Check 10: has_permission True/False ---------------
                perms = await get_user_permissions(pool, TEST_USER_ID, TEST_ORG_ID)
                a_real_perm = next(iter(perms)) if perms else None
                if a_real_perm:
                    has_real = await has_permission(
                        pool, TEST_USER_ID, TEST_ORG_ID, a_real_perm
                    )
                    has_bogus = await has_permission(
                        pool, TEST_USER_ID, TEST_ORG_ID,
                        "definitely_not_a_real_permission_xyz",
                    )
                    print(f"[10] has_permission({a_real_perm})={has_real}, "
                          f"has_permission(bogus)={has_bogus}")
                    ok &= has_real is True and has_bogus is False
                else:
                    print("[10] SKIP — assigned role has no resolvable permissions")

        # --- Check 5: update_delivery_status('failed') -------------------
        nid2 = await notification_bus.publish(
            pool, TEST_ORG_ID, "ioi_confirmed",
            "Email Test", "Email channel test",
            [TEST_USER_ID], channels=["email"], created_by=TEST_USER_ID,
        )
        if nid2:
            notification_ids.append(nid2)
        log_id = await pool.fetchval(
            "SELECT id FROM notification_delivery_log "
            "WHERE notification_id = $1 AND channel = 'email'",
            nid2,
        )
        await notification_bus.update_delivery_status(
            pool, log_id, "failed", failure_reason="SMTP unavailable",
        )
        reason = await pool.fetchval(
            "SELECT failure_reason FROM notification_delivery_log WHERE id = $1",
            log_id,
        )
        recip_status = await pool.fetchval(
            "SELECT status FROM notification_recipients "
            "WHERE notification_id = $1 AND user_id = $2",
            nid2, TEST_USER_ID,
        )
        print(f"[5] update_delivery_status failed -> reason={reason!r}, "
              f"recipient={recip_status!r}")
        ok &= reason == "SMTP unavailable" and recip_status == "failed"

    finally:
        await teardown(pool, notification_ids)
        await close_pool()

    print("RESULT:", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main_async()) else 1)
