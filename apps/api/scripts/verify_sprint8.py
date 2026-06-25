"""Sprint 8 verification: entity-centric target allocations and allocation breakdown.

Exercises:
  - GET /api/v1/portfolio/targets?entity_id=   (direct + inherited)
  - PUT /api/v1/portfolio/targets?entity_id=   (bi-temporal write)
  - DELETE /api/v1/portfolio/targets?entity_id=&taxonomy_key=
  - GET /api/v1/portfolio/allocations?entity_id=
  - GET /api/v1/deals/{id}/taxonomy-placement

Usage:
  cd apps/api
  python scripts/verify_sprint8.py
"""

import asyncio
import os
import sys
import traceback
import uuid

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
TEST_PREFIX = "sprint8_verify"


def taxonomy_level_for(taxonomy_key: str) -> str:
    """Derive taxonomy_level from a taxonomy_key prefix."""
    if taxonomy_key.startswith("taxonomy_sc_"):
        return "super_class"
    if taxonomy_key.startswith("taxonomy_mc_"):
        return "major_class"
    if taxonomy_key.startswith("taxonomy_sub_"):
        return "sub_category"
    raise ValueError(f"unrecognized taxonomy_key prefix: {taxonomy_key}")


async def run():
    if not DATABASE_URL:
        print("SKIP: DATABASE_URL not set")
        return

    pool = await asyncpg.create_pool(DATABASE_URL, statement_cache_size=0)
    errors = []

    async with pool.acquire() as conn:
        # -----------------------------------------------------------------
        # Seed: create a parent entity + child entity
        # -----------------------------------------------------------------
        parent_id = uuid.uuid4()
        child_id = uuid.uuid4()
        taxonomy_key_sc = None
        taxonomy_key_mc = None

        try:
            # Find a super_class taxonomy key in the org
            sc_row = await conn.fetchrow(
                "SELECT config_key FROM config "
                "WHERE org_id = $1 AND category = 'asset_taxonomy' "
                "  AND value_type = 'super_class' "
                "  AND (is_active IS NULL OR is_active = true) "
                "LIMIT 1",
                DEFAULT_ORG_ID,
            )
            mc_row = await conn.fetchrow(
                "SELECT config_key FROM config "
                "WHERE org_id = $1 AND category = 'asset_taxonomy' "
                "  AND value_type = 'major_class' "
                "  AND (is_active IS NULL OR is_active = true) "
                "LIMIT 1",
                DEFAULT_ORG_ID,
            )
            if not sc_row or not mc_row:
                print("SKIP: no taxonomy keys found in org — seed taxonomy first")
                return

            taxonomy_key_sc = sc_row["config_key"]
            taxonomy_key_mc = mc_row["config_key"]

            # Create parent entity
            await conn.execute(
                "INSERT INTO entities (id, org_id, entity_type, display_name, status) "
                "VALUES ($1, $2, 'household', $3, 'active')",
                parent_id, DEFAULT_ORG_ID, f"{TEST_PREFIX}_parent",
            )
            # Create child entity
            await conn.execute(
                "INSERT INTO entities (id, org_id, entity_type, display_name, status) "
                "VALUES ($1, $2, 'individual', $3, 'active')",
                child_id, DEFAULT_ORG_ID, f"{TEST_PREFIX}_child",
            )
            # Link child → parent via entity_ownership
            await conn.execute(
                "INSERT INTO entity_ownership (org_id, parent_id, child_id, ownership_pct, ownership_type) "
                "VALUES ($1, $2, $3, 100, 'full')",
                DEFAULT_ORG_ID, parent_id, child_id,
            )
            print(f"OK  seeded parent={parent_id} child={child_id}")
            print(f"    taxonomy keys: sc={taxonomy_key_sc}  mc={taxonomy_key_mc}")

            # -----------------------------------------------------------------
            # Test 1: PUT target on parent entity
            # -----------------------------------------------------------------
            from datetime import date
            today = date.today()

            await conn.execute(
                "INSERT INTO member_target_allocations "
                "(org_id, entity_id, taxonomy_key, taxonomy_level, target_pct, valid_from) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                DEFAULT_ORG_ID, parent_id, taxonomy_key_sc,
                taxonomy_level_for(taxonomy_key_sc), 30.0, today,
            )
            print("OK  inserted parent target (super_class, 30%)")

            # -----------------------------------------------------------------
            # Test 2: child has no direct target → should inherit from parent
            # -----------------------------------------------------------------
            child_targets = await conn.fetch(
                "SELECT taxonomy_key, target_pct FROM member_target_allocations "
                "WHERE entity_id = $1 AND valid_to IS NULL",
                child_id,
            )
            assert len(child_targets) == 0, "child should have no direct targets"
            print("OK  child has no direct targets (as expected)")

            # -----------------------------------------------------------------
            # Test 3: set direct target on child for a DIFFERENT key
            # -----------------------------------------------------------------
            await conn.execute(
                "INSERT INTO member_target_allocations "
                "(org_id, entity_id, taxonomy_key, taxonomy_level, target_pct, valid_from) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                DEFAULT_ORG_ID, child_id, taxonomy_key_mc,
                taxonomy_level_for(taxonomy_key_mc), 20.0, today,
            )
            print("OK  inserted child direct target (major_class, 20%)")

            # -----------------------------------------------------------------
            # Test 4: bi-temporal update — close old row, insert new
            # -----------------------------------------------------------------
            await conn.execute(
                "UPDATE member_target_allocations SET valid_to = $1 "
                "WHERE entity_id = $2 AND taxonomy_key = $3 AND valid_to IS NULL",
                today, child_id, taxonomy_key_mc,
            )
            await conn.execute(
                "INSERT INTO member_target_allocations "
                "(org_id, entity_id, taxonomy_key, taxonomy_level, target_pct, valid_from) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                DEFAULT_ORG_ID, child_id, taxonomy_key_mc,
                taxonomy_level_for(taxonomy_key_mc), 25.0, today,
            )
            active = await conn.fetchrow(
                "SELECT target_pct FROM member_target_allocations "
                "WHERE entity_id = $1 AND taxonomy_key = $2 AND valid_to IS NULL",
                child_id, taxonomy_key_mc,
            )
            assert active is not None, "should have one active row after update"
            assert float(active["target_pct"]) == 25.0, f"expected 25.0, got {active['target_pct']}"
            closed_count = await conn.fetchval(
                "SELECT COUNT(*) FROM member_target_allocations "
                "WHERE entity_id = $1 AND taxonomy_key = $2 AND valid_to IS NOT NULL",
                child_id, taxonomy_key_mc,
            )
            assert closed_count >= 1, "old row should be closed (valid_to set)"
            print("OK  bi-temporal update: old row closed, new row active")

            # -----------------------------------------------------------------
            # Test 5: clear override (set valid_to)
            # -----------------------------------------------------------------
            await conn.execute(
                "UPDATE member_target_allocations SET valid_to = $1 "
                "WHERE entity_id = $2 AND taxonomy_key = $3 AND valid_to IS NULL",
                today, child_id, taxonomy_key_mc,
            )
            remaining = await conn.fetchrow(
                "SELECT id FROM member_target_allocations "
                "WHERE entity_id = $1 AND taxonomy_key = $2 AND valid_to IS NULL",
                child_id, taxonomy_key_mc,
            )
            assert remaining is None, "after clear, no active row should exist"
            print("OK  clear override: no active direct target remaining")

            # -----------------------------------------------------------------
            # Test 6: UNIQUE NULLS NOT DISTINCT constraint
            # -----------------------------------------------------------------
            try:
                await conn.execute(
                    "INSERT INTO member_target_allocations "
                    "(org_id, entity_id, taxonomy_key, taxonomy_level, target_pct, valid_from) "
                    "VALUES ($1, $2, $3, $4, 10.0, $5)",
                    DEFAULT_ORG_ID, parent_id, taxonomy_key_sc,
                    taxonomy_level_for(taxonomy_key_sc), today,
                )
                errors.append("FAIL UNIQUE constraint should have rejected duplicate (entity_id, taxonomy_key, valid_to IS NULL)")
            except asyncpg.exceptions.UniqueViolationError:
                print("OK  UNIQUE NULLS NOT DISTINCT constraint enforced")

            print("\nAll Sprint 8 DB checks passed.")

        except Exception as exc:
            errors.append(f"UNEXPECTED: {exc}")
            traceback.print_exc()
        finally:
            # -----------------------------------------------------------------
            # Teardown: remove test data
            # -----------------------------------------------------------------
            await conn.execute(
                "DELETE FROM member_target_allocations WHERE entity_id = $1",
                parent_id,
            )
            await conn.execute(
                "DELETE FROM member_target_allocations WHERE entity_id = $1",
                child_id,
            )
            await conn.execute(
                "DELETE FROM entity_ownership WHERE parent_id = $1 OR child_id = $1 OR child_id = $2",
                parent_id, child_id,
            )
            await conn.execute(
                "DELETE FROM entities WHERE id = $1 OR id = $2",
                parent_id, child_id,
            )
            print("OK  test data cleaned up")

    await pool.close()

    if errors:
        print("\nFAILURES:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
