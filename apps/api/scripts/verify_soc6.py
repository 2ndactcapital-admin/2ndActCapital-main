"""SOC Phase 6 (FINAL) verify — Member-side relationships.

Exercises the three standalone Phase-6 services directly (SOC structural
pattern: enforcement lives in importable services, HELD for manual wiring — no
existing endpoint's behavior is changed):

  * services.trusted_contacts   — Task 1 (notify-only, ZERO data access)
  * services.delegate_grants    — Task 2 (scoped/time-bound/springing, audited
                                  AS a delegated action; view_only integrates
                                  with resolve_entity_set)
  * services.external_access    — Task 3 (expiring, scoped, non-persistent)

Pass/fail only, no interactive prompts, idempotent (teardown-at-start AND
teardown-at-end by stable identifiers).

Run: DATABASE_URL=... python scripts/verify_soc6.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_soc6")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"

# Stable test users (deleted by exact id at teardown).
U_DELEGATE_VIEW = "99000000-0000-0000-0000-0000000006a1"  # view_only delegate
U_DELEGATE_TXN = "99000000-0000-0000-0000-0000000006a2"   # transact delegate
U_STAFF = "99000000-0000-0000-0000-0000000006a3"          # staff (visibility)
ALL_TEST_USERS = [U_DELEGATE_VIEW, U_DELEGATE_TXN, U_STAFF]

TEST_ENTITY_PREFIX = "SOC6Verify"

NOW = datetime.now(timezone.utc)

passed = 0
failed = 0


def ok(label):
    global passed
    passed += 1
    print(f"[P] {label}")


def fail(label, reason=""):
    global failed
    failed += 1
    print(f"[F] {label}{': ' + reason if reason else ''}")


async def cleanup(conn):
    """Remove all test data by stable identifiers. Idempotent, FK-safe order."""
    ent_filter = TEST_ENTITY_PREFIX + "%"
    ent_subq = (
        "SELECT id FROM entities WHERE org_id = $1 AND display_name LIKE $3"
    )
    # audit_log rows written by record_delegated_action (actor = delegate user).
    await conn.execute(
        "DELETE FROM audit_log WHERE org_id = $1 AND user_id = ANY($2::uuid[])",
        ORG_ID, ALL_TEST_USERS,
    )
    # assistant_activities (references entities + users).
    await conn.execute(
        f"""
        DELETE FROM assistant_activities
        WHERE org_id = $1
          AND (user_id = ANY($2::uuid[])
               OR proposed_by = ANY($2::uuid[])
               OR entity_id IN ({ent_subq}))
        """,
        ORG_ID, ALL_TEST_USERS, ent_filter,
    )
    # Phase-6 relationship tables (reference entities + users).
    await conn.execute(
        f"""
        DELETE FROM delegate_grants
        WHERE org_id = $1
          AND (delegate_user_id = ANY($2::uuid[])
               OR granted_by = ANY($2::uuid[])
               OR principal_entity_id IN ({ent_subq}))
        """,
        ORG_ID, ALL_TEST_USERS, ent_filter,
    )
    await conn.execute(
        f"""
        DELETE FROM external_access_grants
        WHERE org_id = $1
          AND (granted_by = ANY($2::uuid[])
               OR entity_id IN ({ent_subq}))
        """,
        ORG_ID, ALL_TEST_USERS, ent_filter,
    )
    await conn.execute(
        f"""
        DELETE FROM trusted_contacts
        WHERE org_id = $1
          AND (added_by = ANY($2::uuid[])
               OR member_entity_id IN ({ent_subq}))
        """,
        ORG_ID, ALL_TEST_USERS, ent_filter,
    )
    await conn.execute(
        "DELETE FROM entities WHERE org_id = $1 AND display_name LIKE $2",
        ORG_ID, ent_filter,
    )
    await conn.execute(
        "DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_TEST_USERS,
    )


async def leftover_count(conn) -> int:
    ent_filter = TEST_ENTITY_PREFIX + "%"
    return int(await conn.fetchval(
        """
        SELECT
            (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
          + (SELECT count(*) FROM entities
                WHERE org_id = $2 AND display_name LIKE $3)
          + (SELECT count(*) FROM trusted_contacts
                WHERE org_id = $2
                  AND (added_by = ANY($1::uuid[])
                       OR member_entity_id IN (SELECT id FROM entities
                            WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM delegate_grants
                WHERE org_id = $2
                  AND (delegate_user_id = ANY($1::uuid[])
                       OR granted_by = ANY($1::uuid[])
                       OR principal_entity_id IN (SELECT id FROM entities
                            WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM external_access_grants
                WHERE org_id = $2
                  AND (granted_by = ANY($1::uuid[])
                       OR entity_id IN (SELECT id FROM entities
                            WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM assistant_activities
                WHERE org_id = $2
                  AND (user_id = ANY($1::uuid[])
                       OR proposed_by = ANY($1::uuid[])
                       OR entity_id IN (SELECT id FROM entities
                            WHERE org_id = $2 AND display_name LIKE $3)))
          + (SELECT count(*) FROM audit_log
                WHERE org_id = $2 AND user_id = ANY($1::uuid[]))
        """,
        ALL_TEST_USERS, ORG_ID, ent_filter,
    ))


async def seed_user(conn, user_id, tag, role="member", email=None):
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        user_id, ORG_ID,
        email or f"soc6_{tag}@test.local", f"SOC6 {tag}",
        f"auth0|test_soc6_{tag}", role,
    )


async def seed_entity(conn, tag) -> str:
    return str(await conn.fetchval(
        """
        INSERT INTO entities (org_id, entity_type, display_name)
        VALUES ($1, 'individual', $2) RETURNING id
        """,
        ORG_ID, f"{TEST_ENTITY_PREFIX} {tag}",
    ))


def _grant_row(**over):
    """A delegate_grants-shaped dict with sensible defaults, for the pure
    is_active_delegate truth table."""
    base = {
        "revoked_at": None,
        "is_springing": False,
        "activated_at": None,
        "effective_from": None,
        "effective_until": None,
    }
    base.update(over)
    return base


async def run():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3,
    )

    from services.trusted_contacts import (
        create_trusted_contact,
        list_trusted_contacts,
    )
    from services.delegate_grants import (
        grant_delegate,
        get_delegate_visible_entity_ids,
        is_active_delegate,
        record_delegated_action,
    )
    from services.external_access import (
        grant_external_access,
        is_active_external_grant,
        revoke_external_access,
    )
    from services.entity_graph import resolve_entity_set
    from services.staff_visibility import get_staff_visible_entity_ids

    try:
        # ---- Teardown-at-start -------------------------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)

        # ---- Seed --------------------------------------------------------
        async with pool.acquire() as conn:
            await seed_user(conn, U_DELEGATE_VIEW, "delegate_view")
            await seed_user(conn, U_DELEGATE_TXN, "delegate_txn")
            # Staff user whose email matches a trusted contact (leakage bait).
            await seed_user(
                conn, U_STAFF, "staff", role="member",
                email="soc6_staff@test.local",
            )
            principal_entity = await seed_entity(conn, "Principal")

        # ------------------------------------------------------------------
        # Assertion 1: the three tables exist matching the snapshot, and the
        #              delegate_grants scope CHECK rejects an invalid value.
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            regs = {
                t: await conn.fetchval(f"SELECT to_regclass('public.{t}')")
                for t in (
                    "trusted_contacts",
                    "delegate_grants",
                    "external_access_grants",
                )
            }
            scope_rejected = False
            try:
                await conn.execute(
                    """
                    INSERT INTO delegate_grants
                        (org_id, principal_entity_id, scope)
                    VALUES ($1, $2, 'bogus_scope')
                    """,
                    ORG_ID, principal_entity,
                )
            except asyncpg.exceptions.CheckViolationError:
                scope_rejected = True
        if all(regs.values()) and scope_rejected:
            ok("Assertion 1: trusted_contacts / delegate_grants / "
               "external_access_grants all exist; delegate_grants scope CHECK "
               "rejects an invalid value ('bogus_scope')")
        else:
            fail("Assertion 1: schema/CHECK wrong",
                 f"regs={regs}, scope_rejected={scope_rejected}")

        # ------------------------------------------------------------------
        # Assertion 2: a trusted contact does NOT appear in, or affect,
        #              resolve_entity_set / staff_visibility for ANYONE.
        #              Checked structurally (no visibility module references the
        #              table) AND behaviorally (a seeded contact never surfaces).
        # ------------------------------------------------------------------
        contact_id = await create_trusted_contact(
            pool, ORG_ID, principal_entity,
            contact_name="Jane Trusted",
            contact_email="soc6_staff@test.local",  # same as U_STAFF's email
            contact_phone="+1-555-0100",
            relationship_to_member="daughter",
            added_by=U_DELEGATE_VIEW,
        )
        # Structural: none of the visibility engines reference trusted_contacts.
        svc_dir = os.path.join(os.path.dirname(__file__), "..", "services")
        vis_files = [
            "staff_visibility.py",
            "entity_graph.py",
            "restricted_access.py",
        ]
        structural_clean = True
        for fname in vis_files:
            with open(os.path.join(svc_dir, fname)) as fh:
                if "trusted_contact" in fh.read():
                    structural_clean = False
        # Behavioral: the member's resolved set is just the member; the contact
        # (and the staff user whose email matches it) grants nobody visibility.
        member_set = {
            item["entity_id"]
            for item in await resolve_entity_set(
                pool, ORG_ID, {"type": "subtree", "root_id": principal_entity}
            )
        }
        staff_set = await get_staff_visible_entity_ids(pool, U_STAFF, ORG_ID)
        contacts = await list_trusted_contacts(pool, ORG_ID, principal_entity)
        behavioral_clean = (
            member_set == {principal_entity}       # only the member, nothing extra
            and principal_entity not in staff_set  # no staff visibility conferred
            and len(contacts) == 1                  # the contact does exist as data
        )
        if structural_clean and behavioral_clean:
            ok("Assertion 2: trusted contact is notify-only — no visibility module "
               "references the table, and a seeded contact never appears in or "
               "affects resolve_entity_set / staff_visibility results")
        else:
            fail("Assertion 2: trusted-contact leakage",
                 f"structural_clean={structural_clean}, "
                 f"member_set={member_set}, staff_set={staff_set}, "
                 f"contacts={len(contacts)}")

        # ------------------------------------------------------------------
        # Assertion 3: is_active_delegate across ALL FIVE states.
        # ------------------------------------------------------------------
        future = NOW + timedelta(days=1)
        past = NOW - timedelta(days=1)
        states = {
            "not_yet_effective": (
                _grant_row(effective_from=future), False,
            ),
            "expired": (
                _grant_row(effective_from=past, effective_until=past), False,
            ),
            "springing_not_activated": (
                _grant_row(is_springing=True, activated_at=None), False,
            ),
            "springing_activated": (
                _grant_row(is_springing=True, activated_at=past), True,
            ),
            "revoked": (
                _grant_row(revoked_at=past), False,
            ),
        }
        wrong = {
            name: is_active_delegate(row, now=NOW)
            for name, (row, want) in states.items()
            if is_active_delegate(row, now=NOW) != want
        }
        if not wrong:
            ok("Assertion 3: is_active_delegate correct for all five states "
               "(not-yet-effective, expired, springing-not-activated, "
               "springing-and-activated, revoked)")
        else:
            fail("Assertion 3: is_active_delegate wrong for", str(wrong))

        # ------------------------------------------------------------------
        # Assertion 4: a view_only delegate gains visibility into the
        #              principal's entity via resolve_entity_set integration.
        # ------------------------------------------------------------------
        await grant_delegate(
            pool, ORG_ID,
            principal_entity_id=principal_entity,
            scope="view_only",
            delegate_user_id=U_DELEGATE_VIEW,
            granted_by=U_DELEGATE_VIEW,
        )
        delegate_visible = await get_delegate_visible_entity_ids(
            pool, ORG_ID, U_DELEGATE_VIEW
        )
        # A delegate with no grant sees nothing.
        none_visible = await get_delegate_visible_entity_ids(
            pool, ORG_ID, U_DELEGATE_TXN
        )
        if principal_entity in delegate_visible and principal_entity not in none_visible:
            ok("Assertion 4: view_only delegate gains visibility into the "
               "principal's entity via resolve_entity_set; a delegate with no "
               "grant sees nothing")
        else:
            fail("Assertion 4: view_only visibility integration wrong",
                 f"delegate_visible={delegate_visible}, none_visible={none_visible}")

        # ------------------------------------------------------------------
        # Assertion 5: an action taken by a delegate is logged with BOTH the
        #              delegate's own user_id AND the principal entity_id —
        #              never attributed to the principal alone.
        # ------------------------------------------------------------------
        await grant_delegate(
            pool, ORG_ID,
            principal_entity_id=principal_entity,
            scope="transact",
            delegate_user_id=U_DELEGATE_TXN,
            granted_by=U_DELEGATE_TXN,
        )
        activity_id = await record_delegated_action(
            pool, ORG_ID,
            principal_entity_id=principal_entity,
            delegate_user_id=U_DELEGATE_TXN,
            action_key="spv.subscribe",
            title="Delegated subscription on principal's behalf",
        )
        async with pool.acquire() as conn:
            act = await conn.fetchrow(
                """
                SELECT user_id, proposed_by, entity_id, payload
                FROM assistant_activities WHERE id = $1
                """,
                activity_id,
            )
            audit = await conn.fetchrow(
                """
                SELECT user_id, payload FROM audit_log
                WHERE org_id = $1 AND user_id = $2
                  AND action = 'delegated_action:spv.subscribe'
                ORDER BY created_at DESC LIMIT 1
                """,
                ORG_ID, U_DELEGATE_TXN,
            )
        import json as _json
        act_payload = act["payload"]
        if isinstance(act_payload, str):
            act_payload = _json.loads(act_payload)
        both_on_activity = (
            str(act["user_id"]) == U_DELEGATE_TXN
            and str(act["proposed_by"]) == U_DELEGATE_TXN
            and str(act["entity_id"]) == principal_entity
            and act_payload.get("acting_as") == "delegate"
            and act_payload.get("delegate_user_id") == U_DELEGATE_TXN
            and act_payload.get("principal_entity_id") == principal_entity
        )
        audit_ok = audit is not None and str(audit["user_id"]) == U_DELEGATE_TXN
        if both_on_activity and audit_ok:
            ok("Assertion 5: delegated action captures BOTH the delegate's "
               "user_id AND the principal entity_id (on the activity row + "
               "payload marker + audit_log actor) — not the principal alone")
        else:
            fail("Assertion 5: delegated action not attributed to both parties",
                 f"both_on_activity={both_on_activity}, audit_ok={audit_ok}, "
                 f"activity={dict(act) if act else None}")

        # ------------------------------------------------------------------
        # Assertion 6: is_active_external_grant excludes expired + revoked,
        #              includes an active grant.
        # ------------------------------------------------------------------
        active_gid = await grant_external_access(
            pool, ORG_ID,
            entity_id=principal_entity,
            grantee_email="attorney@external.example",
            expires_at=NOW + timedelta(days=30),
            grantee_name="External Attorney",
            scope_description="Estate documents review",
            granted_by=U_DELEGATE_TXN,
        )
        expired_gid = await grant_external_access(
            pool, ORG_ID,
            entity_id=principal_entity,
            grantee_email="expired@external.example",
            expires_at=NOW - timedelta(days=1),  # already past
            granted_by=U_DELEGATE_TXN,
        )
        revoked_gid = await grant_external_access(
            pool, ORG_ID,
            entity_id=principal_entity,
            grantee_email="revoked@external.example",
            expires_at=NOW + timedelta(days=30),
            granted_by=U_DELEGATE_TXN,
        )
        await revoke_external_access(pool, ORG_ID, revoked_gid)
        async with pool.acquire() as conn:
            rows = {
                str(r["id"]): r
                for r in await conn.fetch(
                    "SELECT * FROM external_access_grants WHERE id = ANY($1::uuid[])",
                    [active_gid, expired_gid, revoked_gid],
                )
            }
        active_ok = is_active_external_grant(rows[active_gid], now=NOW) is True
        expired_ok = is_active_external_grant(rows[expired_gid], now=NOW) is False
        revoked_ok = is_active_external_grant(rows[revoked_gid], now=NOW) is False
        if active_ok and expired_ok and revoked_ok:
            ok("Assertion 6: is_active_external_grant includes an active grant, "
               "excludes an expired grant, and excludes a revoked grant")
        else:
            fail("Assertion 6: external-grant active check wrong",
                 f"active_ok={active_ok}, expired_ok={expired_ok}, "
                 f"revoked_ok={revoked_ok}")

        # ------------------------------------------------------------------
        # Assertion 7: teardown leaves zero leftover rows.
        # ------------------------------------------------------------------
        async with pool.acquire() as conn:
            await cleanup(conn)
            remaining = await leftover_count(conn)
        if remaining == 0:
            ok("Assertion 7: teardown complete — zero leftover test rows (count=0)")
        else:
            fail("Assertion 7: leftover rows after teardown", f"count={remaining}")

    finally:
        try:
            async with pool.acquire() as conn:
                await cleanup(conn)
        finally:
            await pool.close()

    print(f"\n{'=' * 48}")
    print(f"SOC Phase 6 (FINAL): {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
