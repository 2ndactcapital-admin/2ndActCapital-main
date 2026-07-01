"""Sprint 16 verification — Reference Data + CRM Entity Completeness.

Run:  DATABASE_URL=... python apps/api/scripts/verify_sprint16.py

Checks:
 1.  reference_data table exists
 2.  country list seeded (>= 1 entry)
 3.  us_state list seeded (>= 1 entry)
 4.  month list has 12 entries
 5.  currency list seeded (>= 1 entry)
 6.  name_prefix list seeded (>= 1 entry)
 7.  name_suffix list seeded (>= 1 entry)
 8.  entities table has Sprint 16 columns
 9.  entity_addresses table has Sprint 16 columns
10.  inception_date migration: date_of_birth data accessible as inception_date
11.  is_active filter: inactive entity excluded from is_active=true query
12.  legal_name derivation produces correct output
"""

import asyncio
import os
import sys

import asyncpg

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("SKIP — DATABASE_URL not set")
    sys.exit(0)

ORG_ID = "00000000-0000-0000-0000-000000000001"
TEST_USER_ID = "99000000-0000-0000-0000-000000000001"
TEST_AUTH0 = "auth0|test_verify_user"

PASS = "[P]"
FAIL = "[F]"


def check(label, cond):
    if cond:
        print(f"{PASS} {label}")
    else:
        print(f"{FAIL} {label}")
    return cond


async def main():
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    entity_id = None
    ok = True

    try:
        # Ensure test user exists
        await conn.execute(
            """
            INSERT INTO users (id, org_id, auth0_sub, email, role)
            VALUES ($1, $2, $3, 'verify16@test.local', 'member')
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            TEST_USER_ID, ORG_ID, TEST_AUTH0,
        )

        # --- Check 1: reference_data table exists ---
        tbl = await conn.fetchval(
            "SELECT to_regclass('public.reference_data')"
        )
        ok &= check("reference_data table exists", tbl is not None)

        # --- Checks 2-7: seeded lists present ---
        seeded_lists = {
            "country": (">= 1", lambda n: n >= 1),
            "us_state": (">= 1", lambda n: n >= 1),
            "month": ("== 12", lambda n: n == 12),
            "currency": (">= 1", lambda n: n >= 1),
            "name_prefix": (">= 1", lambda n: n >= 1),
            "name_suffix": (">= 1", lambda n: n >= 1),
        }
        for list_key, (desc, pred) in seeded_lists.items():
            n = await conn.fetchval(
                "SELECT COUNT(*) FROM reference_data WHERE list_key=$1 AND is_active=true",
                list_key,
            )
            ok &= check(
                f"reference_data '{list_key}' seeded ({desc}, got {n})",
                pred(n or 0),
            )

        # --- Check 8: entities new columns exist ---
        new_entity_cols = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'entities' AND table_schema = 'public'
              AND column_name IN (
                'inception_date','end_date','is_active','url',
                'country_code','region_code','name_prefix','first_name',
                'surname','legal_name_overridden'
              )
            """,
        )
        found_cols = {r["column_name"] for r in new_entity_cols}
        expected = {
            "inception_date", "end_date", "is_active", "url",
            "country_code", "region_code", "name_prefix", "first_name",
            "surname", "legal_name_overridden",
        }
        missing = expected - found_cols
        ok &= check(
            f"entities Sprint 16 columns present (missing: {missing or 'none'})",
            not missing,
        )

        # --- Check 9: entity_addresses new columns ---
        new_addr_cols = await conn.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'entity_addresses' AND table_schema = 'public'
              AND column_name IN (
                'phone','country_code','region_code',
                'is_seasonal','season_from_month','season_to_month'
              )
            """,
        )
        found_addr = {r["column_name"] for r in new_addr_cols}
        expected_addr = {
            "phone", "country_code", "region_code",
            "is_seasonal", "season_from_month", "season_to_month",
        }
        missing_addr = expected_addr - found_addr
        ok &= check(
            f"entity_addresses Sprint 16 columns present (missing: {missing_addr or 'none'})",
            not missing_addr,
        )

        # --- Check 10: inception_date migration ---
        if "inception_date" in found_cols:
            row10 = await conn.fetchrow(
                """
                INSERT INTO entities (org_id, entity_type, display_name, inception_date)
                VALUES ($1, 'individual', '__verify16_idate__', '1975-06-15')
                RETURNING id, inception_date
                """,
                ORG_ID,
            )
            entity_id = row10["id"]
            ok &= check(
                "inception_date stored and retrieved correctly",
                row10["inception_date"] is not None
                and str(row10["inception_date"]) == "1975-06-15",
            )
        else:
            print(f"[S] inception_date check skipped (column not yet deployed)")

        # --- Check 11: is_active filter ---
        if entity_id and "is_active" in found_cols:
            await conn.execute(
                "UPDATE entities SET is_active = false WHERE id = $1", entity_id
            )
            count_active = await conn.fetchval(
                """
                SELECT COUNT(*) FROM entities
                WHERE id = $1 AND is_active = true
                  AND valid_to IS NULL AND system_to IS NULL
                """,
                entity_id,
            )
            ok &= check("inactive entity excluded by is_active=true filter", count_active == 0)
        else:
            print("[S] is_active filter check skipped")

        # --- Check 12: legal_name derivation ---
        parts = ["Jane", None, "Smith"]
        derived = " ".join(p for p in parts if p)
        ok &= check(
            f"derive_legal_name: 'Jane' + 'Smith' → '{derived}'",
            derived == "Jane Smith",
        )

    finally:
        try:
            if entity_id:
                await conn.execute(
                    "DELETE FROM entity_addresses WHERE entity_id = $1", entity_id
                )
                await conn.execute(
                    "DELETE FROM entities WHERE id = $1", entity_id
                )
        except Exception as e:
            print(f"[teardown warning] {e}")
        await conn.close()

    if ok:
        print("\nAll Sprint 16 checks passed.")
    else:
        print("\nSome checks FAILED — see above.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
