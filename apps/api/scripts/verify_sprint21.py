"""verify_sprint21.py — Sprint 21: Portfolio Allocation Lens.

Checks:
  1. entity_holdings table exists in public schema.
  2. entity_holdings has all 8 required columns.
  3. _classify_state(0, 0) == 'none'.
  4. _classify_state(30, 50) == 'under'  (actual < 0.75 * target).
  5. _classify_state(80, 100) == 'on'   (0.75 ≤ ratio ≤ 1.15).
  6. _classify_state(120, 100) == 'over' (ratio > 1.15).
  7. _classify_state(5, 0) == 'off_plan' (actual > 0, target = 0).
  8. aggregate_allocation returns 3-level tree with ALL taxonomy nodes.
  9. Roll-up math: sc actual_pct == sum of child mc actual_pct (within 0.01).
 10. portfolio.show_allocation registered in REGISTRY.
"""

import asyncio
import os
import sys
from decimal import Decimal
from uuid import UUID

import asyncpg

# Allow importing from the api package alongside the script.
API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

from services.allocation_lens import aggregate_allocation, _classify_state
from services.action_registry import REGISTRY
from services.assistant_actions import register_all

ORG_ID = "00000000-0000-0000-0000-000000000001"
ORG_UUID = UUID(ORG_ID)
TEST_ENTITY_ID = UUID("99000000-0000-0000-0000-000000000099")
TEST_USER_ID = UUID("99000000-0000-0000-0000-000000000001")

_ok = True


def check(label: str, passed: bool) -> bool:
    global _ok
    mark = "[P]" if passed else "[F]"
    print(f"{mark} {label}")
    if not passed:
        _ok = False
    return passed


async def pre_teardown(conn) -> None:
    """Remove any leftover fixtures from a prior run (FK-safe order)."""
    for stmt, *args in [
        ("DELETE FROM member_target_allocations WHERE entity_id = $1", TEST_ENTITY_ID),
        ("DELETE FROM entity_holdings WHERE entity_id = $1", TEST_ENTITY_ID),
        ("DELETE FROM entities WHERE id = $1", TEST_ENTITY_ID),
        ("DELETE FROM users WHERE id = $1", TEST_USER_ID),
    ]:
        try:
            await conn.execute(stmt, *args)
        except Exception:
            pass


async def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("SKIP — DATABASE_URL not set")
        sys.exit(0)

    conn = await asyncpg.connect(url, statement_cache_size=0)

    try:
        # ── Pre-teardown ────────────────────────────────────────────────────
        await pre_teardown(conn)

        # ── Checks 1-2: table schema ────────────────────────────────────────
        exists = await conn.fetchval(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'entity_holdings'
            """
        )
        check("Check 1: entity_holdings table exists", bool(exists))

        if exists:
            cols = {
                r["column_name"]
                for r in await conn.fetch(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'entity_holdings'
                    """
                )
            }
            required = {"id", "org_id", "entity_id", "taxonomy_key",
                        "market_value", "currency_code", "as_of_date", "source"}
            check("Check 2: entity_holdings has required columns",
                  required.issubset(cols))
        else:
            check("Check 2: entity_holdings has required columns (SKIP — table missing)", False)

        # ── Checks 3-7: _classify_state pure-function tests ─────────────────
        check("Check 3: _classify_state(0, 0) == 'none'",
              _classify_state(Decimal("0"), Decimal("0")) == "none")

        check("Check 4: _classify_state(30, 50) == 'under'  [30 < 0.75*50=37.5]",
              _classify_state(Decimal("30"), Decimal("50")) == "under")

        check("Check 5: _classify_state(80, 100) == 'on'   [75 ≤ 80 ≤ 115]",
              _classify_state(Decimal("80"), Decimal("100")) == "on")

        check("Check 6: _classify_state(120, 100) == 'over' [120 > 115]",
              _classify_state(Decimal("120"), Decimal("100")) == "over")

        check("Check 7: _classify_state(5, 0) == 'off_plan' [actual>0, target=0]",
              _classify_state(Decimal("5"), Decimal("0")) == "off_plan")

        # ── Checks 8-9: aggregate_allocation end-to-end ─────────────────────
        if not exists:
            check("Check 8: aggregate_allocation structure (SKIP — table missing)", False)
            check("Check 9: roll-up math correct (SKIP — table missing)", False)
        else:
            # Fetch first super_class key and its first major_class key.
            tax_rows = await conn.fetch(
                """
                SELECT config_key, config_value FROM config
                WHERE org_id = $1 AND category = 'asset_taxonomy'
                  AND (is_active IS NULL OR is_active = true)
                ORDER BY display_order NULLS LAST, config_key
                """,
                ORG_UUID,
            )

            import re
            sc_key = mc_key = None
            for r in tax_rows:
                k = r["config_key"]
                if sc_key is None and re.fullmatch(r"taxonomy_sc_\d+", k):
                    sc_key = k
                if sc_key and mc_key is None and re.fullmatch(
                    r"taxonomy_mc_(\d+)_(\d+)", k
                ):
                    # Must belong to the chosen super-class
                    parts = k.split("_")
                    if f"taxonomy_sc_{parts[2]}" == sc_key:
                        mc_key = k
                if sc_key and mc_key:
                    break

            if not sc_key or not mc_key:
                check("Check 8: aggregate_allocation (SKIP — taxonomy not seeded)", False)
                check("Check 9: roll-up math (SKIP — taxonomy not seeded)", False)
            else:
                # Seed test user + entity
                await conn.execute(
                    """
                    INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                    VALUES ($1, $2, 's21verify@placeholder.local',
                            'Sprint 21 Verify', 'auth0|s21_verify', 'member')
                    ON CONFLICT (id) DO NOTHING
                    """,
                    TEST_USER_ID, ORG_UUID,
                )
                await conn.execute(
                    """
                    INSERT INTO entities
                        (id, org_id, entity_type, display_name,
                         valid_from, status, profile_mode)
                    VALUES ($1, $2, 'individual', 'Sprint 21 Verify Entity',
                            now(), 'prospect', 'foundation')
                    ON CONFLICT (id) DO NOTHING
                    """,
                    TEST_ENTITY_ID, ORG_UUID,
                )

                # Seed holdings: $2,000,000 at the first major-class key.
                await conn.execute(
                    """
                    INSERT INTO entity_holdings
                        (org_id, entity_id, taxonomy_key, market_value,
                         currency_code, as_of_date, source)
                    VALUES ($1, $2, $3, 2000000, 'USD', CURRENT_DATE, 'manual')
                    """,
                    ORG_UUID, TEST_ENTITY_ID, mc_key,
                )

                # Seed targets: sc target = 100%, mc target = 80%.
                await conn.executemany(
                    """
                    INSERT INTO member_target_allocations
                        (org_id, entity_id, taxonomy_key, taxonomy_level,
                         target_pct, valid_from, system_from)
                    VALUES ($1, $2, $3, $4, $5, now(), now())
                    """,
                    [
                        (ORG_UUID, TEST_ENTITY_ID, sc_key, "super", 100.0),
                        (ORG_UUID, TEST_ENTITY_ID, mc_key, "major", 80.0),
                    ],
                )

                # Build a pool for aggregate_allocation (uses same DB URL).
                from services.database import get_pool
                pool = await get_pool()

                selector = {"type": "entity", "id": str(TEST_ENTITY_ID)}
                result = await aggregate_allocation(pool, selector, ORG_ID)

                scs = result.get("super_classes", [])

                # Count taxonomy nodes from config to verify completeness.
                sc_count_expected = sum(
                    1 for r in tax_rows
                    if re.fullmatch(r"taxonomy_sc_\d+", r["config_key"])
                )

                check(
                    "Check 8: aggregate returns 3-level tree with all super_classes",
                    len(scs) == sc_count_expected and all(
                        "major_classes" in sc and all(
                            "sub_categories" in mc
                            for mc in sc["major_classes"]
                        )
                        for sc in scs
                    ),
                )

                # Roll-up math: sc actual_pct == sum(mc actual_pct under that sc)
                sc1 = next((s for s in scs if s["key"] == sc_key), None)
                if sc1:
                    mc_sum = sum(mc["actual_pct"] for mc in sc1["major_classes"])
                    sc_pct = sc1["actual_pct"]
                    check(
                        f"Check 9: sc actual_pct ({sc_pct:.4f}) == sum mc actual_pct ({mc_sum:.4f}) ±0.01",
                        abs(sc_pct - mc_sum) <= 0.01,
                    )
                else:
                    check("Check 9: roll-up math (SKIP — sc not found in result)", False)

        # ── Check 10: show_allocation registered in REGISTRY ─────────────────
        register_all()
        action = REGISTRY.get("portfolio.show_allocation")
        check(
            "Check 10: portfolio.show_allocation registered in REGISTRY",
            action is not None and action.key == "portfolio.show_allocation",
        )

    finally:
        # ── Teardown (FK-safe order) ──────────────────────────────────────────
        await pre_teardown(conn)
        await conn.close()

    if _ok:
        print("\nAll Sprint 21 checks passed.")
    else:
        print("\nSome checks FAILED — see above.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
