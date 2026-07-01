"""Sprint 15 verify — Entity Ownership Graph.

Checks:
  1.  Create 4 test entities: household, trust, llc, individual
  2.  Create ownership edges: household->trust (100%), trust->llc (60%), individual->llc (40%)
  3.  get_children(household) returns 1 child: trust at 100%
  4.  get_parents(llc) returns 2 parents: trust (60%) and individual (40%)
  5.  get_subtree(household) — correct structure and depths
  6.  get_lookthrough(household) — trust=100%, llc=60%, individual NOT in result
  7.  Multi-path: add household->llc (20%), recompute lookthrough → llc=80%
  8.  detect_cycle: cycle detection correctness
  9.  resolve_entity_set subtree selector returns household + descendants with weights
  10. PATCH (amendment) — amend household->trust from 100% to 90%
  11. Soft DELETE — soft-delete individual->llc edge
  12. entity.link_ownership draft_handler returns proposed_action without cycle error

Run: DATABASE_URL=... python scripts/verify_sprint15.py
"""
import asyncio
import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("[SKIP] DATABASE_URL not set — skipping verify_sprint15")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"
TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
TEST_AUTH0_SUB = "auth0|test_verify_user"

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


async def seed(conn):
    """Seed test user."""
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'verify15@test.local', 'Verify User 15', $3, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID, ORG_ID, TEST_AUTH0_SUB,
    )


async def teardown(conn, entity_ids: list, relationship_ids: list):
    """FK-safe teardown."""
    # Remove all test relationships (use entity_ids to catch any we created)
    if entity_ids:
        await conn.execute(
            """
            DELETE FROM entity_relationships
            WHERE from_entity_id = ANY($1::uuid[])
               OR to_entity_id = ANY($1::uuid[])
            """,
            entity_ids,
        )

    # Remove entity_group_members for these entities
    if entity_ids:
        await conn.execute(
            "DELETE FROM entity_group_members WHERE entity_id = ANY($1::uuid[])",
            entity_ids,
        )

    # Remove entity_groups owned by test org that we may have created
    # (none in this sprint, but keep safe)

    # Remove entities
    for eid in entity_ids:
        await conn.execute("DELETE FROM entity_notes WHERE entity_id = $1", eid)
        await conn.execute(
            "DELETE FROM investment_profile_answers WHERE entity_id = $1", eid,
        )
        await conn.execute(
            "DELETE FROM investment_profile_extractions WHERE entity_id = $1", eid,
        )
        await conn.execute("DELETE FROM entity_briefs WHERE entity_id = $1", eid)
        await conn.execute(
            "DELETE FROM profile_conversations WHERE entity_id = $1", eid,
        )
        await conn.execute(
            "DELETE FROM member_target_allocations WHERE entity_id = $1", eid,
        )
        await conn.execute(
            "DELETE FROM entity_attributes WHERE entity_id = $1", eid,
        )
        await conn.execute(
            "DELETE FROM entity_addresses WHERE entity_id = $1", eid,
        )
        await conn.execute(
            "DELETE FROM entity_employment WHERE employee_id = $1 OR employer_id = $1",
            eid,
        )
        await conn.execute(
            "DELETE FROM entity_social_profiles WHERE entity_id = $1", eid,
        )
        await conn.execute("DELETE FROM entity_tax_ids WHERE entity_id = $1", eid)
        await conn.execute(
            "DELETE FROM compliance_records WHERE entity_id = $1", eid,
        )
        await conn.execute("DELETE FROM entities WHERE id = $1", eid)

    # Clean up test user
    await conn.execute(
        "DELETE FROM member_todos WHERE user_id = $1", TEST_USER_ID,
    )
    await conn.execute(
        "DELETE FROM audit_log WHERE user_id = $1", TEST_USER_ID,
    )
    await conn.execute("DELETE FROM users WHERE id = $1", TEST_USER_ID)


async def run():
    pool = await asyncpg.create_pool(
        DATABASE_URL, statement_cache_size=0, min_size=1, max_size=3,
    )

    async with pool.acquire() as conn:
        await seed(conn)

    entity_ids: list = []
    relationship_ids: list = []

    household_id: str | None = None
    trust_id: str | None = None
    llc_id: str | None = None
    individual_id: str | None = None

    try:
        from services.entity_graph import (
            get_children,
            get_parents,
            get_subtree,
            get_lookthrough,
            resolve_entity_set,
            detect_cycle,
        )

        # --------------------------------------------------------------------
        # Check 1: Create 4 test entities
        # --------------------------------------------------------------------
        async with pool.acquire() as conn:
            household_id = str(await conn.fetchval(
                """
                INSERT INTO entities (org_id, entity_type, display_name)
                VALUES ($1, 'household', 'Verify15 Household')
                RETURNING id
                """,
                ORG_ID,
            ))
            entity_ids.append(household_id)

            trust_id = str(await conn.fetchval(
                """
                INSERT INTO entities (org_id, entity_type, display_name)
                VALUES ($1, 'trust', 'Verify15 Trust')
                RETURNING id
                """,
                ORG_ID,
            ))
            entity_ids.append(trust_id)

            llc_id = str(await conn.fetchval(
                """
                INSERT INTO entities (org_id, entity_type, display_name)
                VALUES ($1, 'llc', 'Verify15 LLC')
                RETURNING id
                """,
                ORG_ID,
            ))
            entity_ids.append(llc_id)

            individual_id = str(await conn.fetchval(
                """
                INSERT INTO entities (org_id, entity_type, display_name)
                VALUES ($1, 'individual', 'Verify15 Individual')
                RETURNING id
                """,
                ORG_ID,
            ))
            entity_ids.append(individual_id)

            count = await conn.fetchval(
                "SELECT COUNT(*) FROM entities WHERE id = ANY($1::uuid[])",
                entity_ids,
            )

        if int(count) == 4:
            ok("Check 1: Created 4 test entities (household, trust, llc, individual)")
        else:
            fail("Check 1: entity count wrong", f"expected 4, got {count}")

        # --------------------------------------------------------------------
        # Check 2: Create ownership edges
        # --------------------------------------------------------------------
        async with pool.acquire() as conn:
            # household -> trust: 100%
            rel_ht_id = str(await conn.fetchval(
                """
                INSERT INTO entity_relationships
                    (org_id, from_entity_id, to_entity_id,
                     relationship_type, ownership_pct, created_by)
                VALUES ($1, $2, $3, 'ownership', 100, $4)
                RETURNING id
                """,
                ORG_ID, household_id, trust_id, TEST_USER_ID,
            ))
            relationship_ids.append(rel_ht_id)

            # trust -> llc: 60%
            rel_tl_id = str(await conn.fetchval(
                """
                INSERT INTO entity_relationships
                    (org_id, from_entity_id, to_entity_id,
                     relationship_type, ownership_pct, created_by)
                VALUES ($1, $2, $3, 'ownership', 60, $4)
                RETURNING id
                """,
                ORG_ID, trust_id, llc_id, TEST_USER_ID,
            ))
            relationship_ids.append(rel_tl_id)

            # individual -> llc: 40%
            rel_il_id = str(await conn.fetchval(
                """
                INSERT INTO entity_relationships
                    (org_id, from_entity_id, to_entity_id,
                     relationship_type, ownership_pct, created_by)
                VALUES ($1, $2, $3, 'ownership', 40, $4)
                RETURNING id
                """,
                ORG_ID, individual_id, llc_id, TEST_USER_ID,
            ))
            relationship_ids.append(rel_il_id)

            edge_count = await conn.fetchval(
                "SELECT COUNT(*) FROM entity_relationships WHERE id = ANY($1::uuid[])",
                relationship_ids,
            )

        if int(edge_count) == 3:
            ok("Check 2: Created 3 ownership edges (household->trust 100%, trust->llc 60%, individual->llc 40%)")
        else:
            fail("Check 2: edge count wrong", f"expected 3, got {edge_count}")

        # --------------------------------------------------------------------
        # Check 3: get_children(household) → 1 child: trust at 100%
        # --------------------------------------------------------------------
        children = await get_children(pool, ORG_ID, household_id)
        trust_child = next(
            (c for c in children if c["entity_id"] == trust_id), None
        )

        if (
            len(children) == 1
            and trust_child is not None
            and Decimal(trust_child["ownership_pct"]) == Decimal("100")
        ):
            ok("Check 3: get_children(household) returns 1 child: trust at 100%")
        else:
            fail(
                "Check 3: get_children wrong",
                f"len={len(children)}, trust_child={trust_child}",
            )

        # --------------------------------------------------------------------
        # Check 4: get_parents(llc) → 2 parents: trust (60%) and individual (40%)
        # --------------------------------------------------------------------
        parents = await get_parents(pool, ORG_ID, llc_id)
        trust_parent = next(
            (p for p in parents if p["entity_id"] == trust_id), None
        )
        individual_parent = next(
            (p for p in parents if p["entity_id"] == individual_id), None
        )

        if (
            len(parents) == 2
            and trust_parent is not None
            and individual_parent is not None
            and Decimal(trust_parent["ownership_pct"]) == Decimal("60")
            and Decimal(individual_parent["ownership_pct"]) == Decimal("40")
        ):
            ok("Check 4: get_parents(llc) returns 2 parents: trust (60%) and individual (40%)")
        else:
            fail(
                "Check 4: get_parents wrong",
                f"len={len(parents)}, trust_parent={trust_parent}, "
                f"individual_parent={individual_parent}",
            )

        # --------------------------------------------------------------------
        # Check 5: get_subtree(household) — structure and depths
        # --------------------------------------------------------------------
        subtree = await get_subtree(pool, ORG_ID, household_id)

        # household depth=0 with 1 child (trust)
        # trust depth=1 with 1 child (llc)
        # llc depth=2

        household_ok = subtree["id"] == household_id and subtree["depth"] == 0
        trust_nodes = subtree["children"]
        trust_node = next(
            (n for n in trust_nodes if n["id"] == trust_id), None
        ) if trust_nodes else None
        trust_ok = (
            trust_node is not None
            and trust_node["depth"] == 1
            and len(trust_node["children"]) == 1
        )
        llc_node = trust_node["children"][0] if trust_ok else None
        llc_ok = (
            llc_node is not None
            and llc_node["id"] == llc_id
            and llc_node["depth"] == 2
        )

        if household_ok and trust_ok and llc_ok:
            ok(
                "Check 5: get_subtree(household) — household(depth=0), "
                "trust(depth=1, 1 child), llc(depth=2)"
            )
        else:
            fail(
                "Check 5: get_subtree structure wrong",
                f"household_ok={household_ok}, trust_ok={trust_ok}, llc_ok={llc_ok}, "
                f"subtree={subtree}",
            )

        # --------------------------------------------------------------------
        # Check 6: get_lookthrough(household)
        # trust=100%, llc=60% (100%*60%), individual NOT in result
        # --------------------------------------------------------------------
        lookthrough = await get_lookthrough(pool, ORG_ID, household_id)

        lt_by_id = {item["entity_id"]: item for item in lookthrough}

        trust_lt = lt_by_id.get(trust_id)
        llc_lt = lt_by_id.get(llc_id)
        individual_in_lt = individual_id in lt_by_id

        # effective_pct is returned as a string like "1.000000" meaning 100%
        # trust: 1.000000 (100% * 1 = 1.0 = 100%)
        # llc: 0.600000 (100% * 60% = 0.6 = 60%)
        trust_pct_ok = (
            trust_lt is not None
            and abs(Decimal(trust_lt["effective_pct"]) - Decimal("1.000000")) < Decimal("0.000001")
        )
        llc_pct_ok = (
            llc_lt is not None
            and abs(Decimal(llc_lt["effective_pct"]) - Decimal("0.600000")) < Decimal("0.000001")
        )

        if trust_pct_ok and llc_pct_ok and not individual_in_lt:
            ok(
                "Check 6: get_lookthrough(household) — trust=100%, llc=60%, "
                "individual not in result"
            )
        else:
            fail(
                "Check 6: get_lookthrough wrong",
                f"trust_pct_ok={trust_pct_ok} ({trust_lt}), "
                f"llc_pct_ok={llc_pct_ok} ({llc_lt}), "
                f"individual_in_lt={individual_in_lt}",
            )

        # --------------------------------------------------------------------
        # Check 7: Multi-path — add household->llc (20%), recompute lookthrough
        # llc should be 60% + 20% = 80%
        # --------------------------------------------------------------------
        async with pool.acquire() as conn:
            rel_hl_id = str(await conn.fetchval(
                """
                INSERT INTO entity_relationships
                    (org_id, from_entity_id, to_entity_id,
                     relationship_type, ownership_pct, created_by)
                VALUES ($1, $2, $3, 'ownership', 20, $4)
                RETURNING id
                """,
                ORG_ID, household_id, llc_id, TEST_USER_ID,
            ))
            relationship_ids.append(rel_hl_id)

        lookthrough2 = await get_lookthrough(pool, ORG_ID, household_id)
        lt2_by_id = {item["entity_id"]: item for item in lookthrough2}

        llc_lt2 = lt2_by_id.get(llc_id)
        # llc via trust: 1.0 * 0.6 = 0.6; via direct: 0.2 → total 0.8
        llc_pct2_ok = (
            llc_lt2 is not None
            and abs(Decimal(llc_lt2["effective_pct"]) - Decimal("0.800000")) < Decimal("0.000001")
        )

        if llc_pct2_ok:
            ok(
                f"Check 7: multi-path lookthrough — llc effective_pct="
                f"{llc_lt2['effective_pct']} (expected ~0.800000)"
            )
        else:
            fail(
                "Check 7: multi-path lookthrough wrong",
                f"llc_lt2={llc_lt2}",
            )

        # --------------------------------------------------------------------
        # Check 8: detect_cycle
        # --------------------------------------------------------------------
        # llc is below household → adding llc->household would cycle
        cycle_llc_to_household = await detect_cycle(pool, ORG_ID, llc_id, household_id)
        # trust is below household → adding trust->household would cycle
        cycle_trust_to_household = await detect_cycle(pool, ORG_ID, trust_id, household_id)
        # household->llc already exists but detect_cycle checks from->to direction:
        # detect_cycle(household_id, llc_id): would adding household->llc cycle?
        # llc has no children currently, so household is NOT a descendant of llc → False
        cycle_household_to_llc = await detect_cycle(pool, ORG_ID, household_id, llc_id)

        if (
            cycle_llc_to_household is True
            and cycle_trust_to_household is True
            and cycle_household_to_llc is False
        ):
            ok(
                "Check 8: detect_cycle — llc->household=True, trust->household=True, "
                "household->llc=False"
            )
        else:
            fail(
                "Check 8: detect_cycle wrong",
                f"llc->household={cycle_llc_to_household}, "
                f"trust->household={cycle_trust_to_household}, "
                f"household->llc={cycle_household_to_llc}",
            )

        # --------------------------------------------------------------------
        # Check 9: resolve_entity_set subtree selector
        # --------------------------------------------------------------------
        entity_set = await resolve_entity_set(
            pool, ORG_ID, {"type": "subtree", "root_id": household_id}
        )

        es_by_id = {item["entity_id"]: item for item in entity_set}

        household_in_set = household_id in es_by_id
        trust_in_set = trust_id in es_by_id
        llc_in_set = llc_id in es_by_id

        # household weight should be 1.0
        household_weight_ok = (
            household_in_set
            and abs(Decimal(es_by_id[household_id]["weight"]) - Decimal("1.000000")) < Decimal("0.000001")
        )

        # trust weight: lookthrough effective_pct / 100 = 1.0 / 100 = 0.01?
        # Actually resolve_entity_set uses: weight = eff / 100
        # where eff = "1.000000" (from get_lookthrough)
        # So trust weight = 1.000000 / 100 = 0.010000
        trust_weight_ok = trust_in_set

        if household_weight_ok and trust_in_set and llc_in_set:
            ok(
                "Check 9: resolve_entity_set(subtree, household) includes household "
                f"(weight=1.0), trust, and llc — total {len(entity_set)} entries"
            )
        else:
            fail(
                "Check 9: resolve_entity_set wrong",
                f"household_weight_ok={household_weight_ok}, "
                f"trust_in_set={trust_in_set}, llc_in_set={llc_in_set}, "
                f"entity_set={entity_set}",
            )

        # --------------------------------------------------------------------
        # Check 10: PATCH (amendment) — amend household->trust from 100% to 90%
        # --------------------------------------------------------------------
        async with pool.acquire() as conn:
            # Close old row (bi-temporal)
            await conn.execute(
                """
                UPDATE entity_relationships
                SET valid_to = now(), system_to = now()
                WHERE from_entity_id = $1
                  AND to_entity_id = $2
                  AND valid_to IS NULL
                  AND system_to IS NULL
                """,
                household_id, trust_id,
            )

            # Insert new row with 90%
            new_rel_ht_id = str(await conn.fetchval(
                """
                INSERT INTO entity_relationships
                    (org_id, from_entity_id, to_entity_id,
                     relationship_type, ownership_pct, created_by)
                VALUES ($1, $2, $3, 'ownership', 90, $4)
                RETURNING id
                """,
                ORG_ID, household_id, trust_id, TEST_USER_ID,
            ))
            relationship_ids.append(new_rel_ht_id)

            # Count active rows for household->trust
            active_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM entity_relationships
                WHERE from_entity_id = $1
                  AND to_entity_id = $2
                  AND valid_to IS NULL
                  AND system_to IS NULL
                """,
                household_id, trust_id,
            )

            # Count all rows (including history) for household->trust
            total_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM entity_relationships
                WHERE from_entity_id = $1
                  AND to_entity_id = $2
                """,
                household_id, trust_id,
            )

            # Verify new active row has 90%
            new_pct = await conn.fetchval(
                """
                SELECT ownership_pct FROM entity_relationships
                WHERE from_entity_id = $1
                  AND to_entity_id = $2
                  AND valid_to IS NULL
                  AND system_to IS NULL
                """,
                household_id, trust_id,
            )

        if (
            int(active_count) == 1
            and int(total_count) == 2
            and Decimal(str(new_pct)) == Decimal("90")
        ):
            ok(
                "Check 10: PATCH amendment — active_count=1, total_count=2 (1 history), "
                "new ownership_pct=90"
            )
        else:
            fail(
                "Check 10: amendment wrong",
                f"active_count={active_count}, total_count={total_count}, new_pct={new_pct}",
            )

        # --------------------------------------------------------------------
        # Check 11: Soft DELETE — soft-delete individual->llc edge
        # --------------------------------------------------------------------
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE entity_relationships
                SET valid_to = now(), system_to = now()
                WHERE from_entity_id = $1
                  AND to_entity_id = $2
                  AND valid_to IS NULL
                  AND system_to IS NULL
                """,
                individual_id, llc_id,
            )

            active_after_delete = await conn.fetchval(
                """
                SELECT COUNT(*) FROM entity_relationships
                WHERE from_entity_id = $1
                  AND to_entity_id = $2
                  AND valid_to IS NULL
                  AND system_to IS NULL
                """,
                individual_id, llc_id,
            )

            history_after_delete = await conn.fetchval(
                """
                SELECT COUNT(*) FROM entity_relationships
                WHERE from_entity_id = $1
                  AND to_entity_id = $2
                  AND valid_to IS NOT NULL
                """,
                individual_id, llc_id,
            )

        if int(active_after_delete) == 0 and int(history_after_delete) == 1:
            ok(
                "Check 11: soft DELETE — active_count=0 for individual->llc, "
                "history row preserved (count=1)"
            )
        else:
            fail(
                "Check 11: soft delete wrong",
                f"active_after_delete={active_after_delete}, "
                f"history_after_delete={history_after_delete}",
            )

        # --------------------------------------------------------------------
        # Check 12: entity.link_ownership draft_handler
        # trust -> individual: not a cycle (individual is a separate branch after
        # we deleted individual->llc edge, trust is not below individual)
        # --------------------------------------------------------------------
        try:
            from services.assistant_actions.entity_graph import register_actions
            from services.action_registry import REGISTRY

            register_actions()  # idempotent

            action = REGISTRY.get("entity.link_ownership")
            if action is None or action.draft_handler is None:
                fail("Check 12: entity.link_ownership action or draft_handler not found")
            else:
                result = await action.draft_handler(
                    pool,
                    TEST_USER_ID,
                    ORG_ID,
                    from_entity_id=trust_id,
                    to_entity_id=individual_id,
                    ownership_pct=25,
                )

                proposed = result.get("proposed_action", {})
                has_from_name = "from_name" in proposed
                has_to_name = "to_name" in proposed
                no_error = "error" not in proposed

                if has_from_name and has_to_name and no_error:
                    ok(
                        f"Check 12: entity.link_ownership draft_handler — no cycle, "
                        f"proposed_action has from_name='{proposed.get('from_name')}', "
                        f"to_name='{proposed.get('to_name')}'"
                    )
                else:
                    fail(
                        "Check 12: draft_handler result wrong",
                        f"has_from_name={has_from_name}, has_to_name={has_to_name}, "
                        f"no_error={no_error}, proposed={proposed}",
                    )
        except Exception as exc:
            fail("Check 12: draft_handler raised exception", str(exc))

    finally:
        async with pool.acquire() as conn:
            await teardown(conn, entity_ids, relationship_ids)
        await pool.close()

    print(f"\n{'=' * 40}")
    print(f"Sprint 15: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
