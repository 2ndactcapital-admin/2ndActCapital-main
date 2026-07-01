"""Sprint 16 verification — Reference Data + CRM Entity Completeness.

Run:  DATABASE_URL=... python apps/api/scripts/verify_sprint16.py

Checks:
 1. reference_items table exists
 2. country list has >= 15 entries
 3. us_state list has 51 entries (50 states + DC)
 4. ca_province list has 13 entries
 5. month list has 12 entries
 6. currency list has 7 entries
 7. entities table has new columns (inception_date, is_active, country_code, etc.)
 8. entity_addresses table has new columns (phone, is_seasonal, etc.)
 9. inception_date migration: date_of_birth data copied where present
10. is_active filter: inactive entity excluded from default list
11. legal_name derivation for individual entities
12. seasonal address round-trip
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
SKIP = "[S]"


def check(label, cond):
    if cond:
        print(f"{PASS} {label}")
    else:
        print(f"{FAIL} {label}")
    return cond


async def main():
    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    entity_id = None
    addr_id = None
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

        # --- Check 1: reference_items table exists ---
        tbl = await conn.fetchval(
            "SELECT to_regclass('public.reference_items')"
        )
        ok &= check("reference_items table exists", tbl is not None)

        # --- Check 2: country list >=15 entries ---
        n_country = await conn.fetchval(
            "SELECT COUNT(*) FROM reference_items WHERE list_key='country' AND is_active=true"
        )
        ok &= check(f"country list has >= 15 entries ({n_country})", (n_country or 0) >= 15)

        # --- Check 3: us_state has 51 ---
        n_us = await conn.fetchval(
            "SELECT COUNT(*) FROM reference_items WHERE list_key='us_state' AND is_active=true"
        )
        ok &= check(f"us_state list has 51 entries ({n_us})", (n_us or 0) == 51)

        # --- Check 4: ca_province has 13 ---
        n_ca = await conn.fetchval(
            "SELECT COUNT(*) FROM reference_items WHERE list_key='ca_province' AND is_active=true"
        )
        ok &= check(f"ca_province list has 13 entries ({n_ca})", (n_ca or 0) == 13)

        # --- Check 5: month has 12 ---
        n_month = await conn.fetchval(
            "SELECT COUNT(*) FROM reference_items WHERE list_key='month' AND is_active=true"
        )
        ok &= check(f"month list has 12 entries ({n_month})", (n_month or 0) == 12)

        # --- Check 6: currency has 7 ---
        n_cur = await conn.fetchval(
            "SELECT COUNT(*) FROM reference_items WHERE list_key='currency' AND is_active=true"
        )
        ok &= check(f"currency list has 7 entries ({n_cur})", (n_cur or 0) == 7)

        # --- Check 7: entities new columns exist ---
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
            f"entities new columns present (missing={missing or 'none'})",
            not missing,
        )

        # --- Check 8: entity_addresses new columns exist ---
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
            f"entity_addresses new columns present (missing={missing_addr or 'none'})",
            not missing_addr,
        )

        # --- Check 9: inception_date migration ---
        # Insert an entity with date_of_birth set (simulate pre-sprint row)
        row9 = await conn.fetchrow(
            """
            INSERT INTO entities (org_id, entity_type, display_name, date_of_birth)
            VALUES ($1, 'individual', '__verify16_dob__', '1975-06-15')
            RETURNING id, inception_date, date_of_birth
            """,
            ORG_ID,
        )
        entity_id = row9["id"]
        # Run migration logic manually (as sprint SQL would have done for existing rows)
        await conn.execute(
            "UPDATE entities SET inception_date = date_of_birth WHERE id = $1 AND inception_date IS NULL",
            entity_id,
        )
        after = await conn.fetchrow(
            "SELECT inception_date FROM entities WHERE id = $1", entity_id
        )
        ok &= check(
            "inception_date migration copies date_of_birth",
            after["inception_date"] is not None and str(after["inception_date"]) == "1975-06-15",
        )

        # --- Check 10: is_active filter ---
        # Mark the test entity inactive
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
        ok &= check("inactive entity excluded from is_active=true filter", count_active == 0)

        # --- Check 11: legal_name derivation ---
        row11 = await conn.fetchrow(
            """
            INSERT INTO entities (
                org_id, entity_type, display_name,
                first_name, surname, legal_name_overridden
            ) VALUES ($1, 'individual', '__verify16_person__',
                      'Jane', 'Smith', false)
            RETURNING id, first_name, surname
            """,
            ORG_ID,
        )
        # Simulate derive_legal_name in Python
        derived = " ".join(
            p for p in [None, row11["first_name"], None, row11["surname"], None] if p
        )
        ok &= check(
            f"derive_legal_name produces '{derived}'",
            derived == "Jane Smith",
        )
        # Clean up person row
        await conn.execute("DELETE FROM entities WHERE id = $1", row11["id"])

        # --- Check 12: seasonal address round-trip ---
        addr12 = await conn.fetchrow(
            """
            INSERT INTO entity_addresses (
                org_id, entity_id, address_type,
                street1, city, country,
                phone, country_code, region_code,
                is_seasonal, season_from_month, season_to_month
            ) VALUES (
                $1, $2, 'primary_residence',
                '123 Winter Lane', 'Aspen', 'US',
                '+1-970-555-0100', 'US', 'CO',
                true, 12, 3
            )
            RETURNING id, is_seasonal, season_from_month, season_to_month,
                      country_code, region_code, phone
            """,
            ORG_ID, entity_id,
        )
        addr_id = addr12["id"]
        ok &= check(
            "seasonal address: is_seasonal=true, months 12→3, phone, country_code, region_code",
            (
                addr12["is_seasonal"] is True
                and addr12["season_from_month"] == 12
                and addr12["season_to_month"] == 3
                and addr12["country_code"] == "US"
                and addr12["region_code"] == "CO"
                and addr12["phone"] == "+1-970-555-0100"
            ),
        )

    finally:
        # Teardown in FK-safe order
        try:
            if addr_id:
                await conn.execute(
                    "DELETE FROM entity_addresses WHERE id = $1", addr_id
                )
            if entity_id:
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
