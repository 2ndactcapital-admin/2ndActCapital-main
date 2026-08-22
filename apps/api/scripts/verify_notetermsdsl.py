"""Verification — payoff DSL + versioned note-terms extension.

Pass/fail only, no prompts, idempotent, teardown at start AND end. Every check
asserts a real value, not the existence of something.

APP_SERVICE_DATABASE_URL IS REQUIRED. There is no SET ROLE fallback here. A
prior sprint's RLS checks quietly fell back to ``SET ROLE app_service`` when
that credential was broken, which means an RLS regression could pass unnoticed
under a differently-privileged session. If the credential does not connect,
this script FAILS loudly instead.

Run:
    python3 scripts/verify_notetermsdsl.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.append(
    "/mnt/c/Users/Joe/2ndActCapital/apps/api/venv/lib/python3.14/site-packages"
)

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
    override=False,
)

from models.note_terms import (  # noqa: E402
    FIELD_STATES,
    HAZARD_FIELD_KEYS,
    FieldStatusError,
    NoteTerms,
    validate_field_status,
)

TERMS_TABLE = "portfolio.securities_global_note_terms"
REGISTRY_TABLE = "portfolio.note_terms_field_registry"

# Fixed fixture ids so teardown is exact and reruns are idempotent.
TEST_SECURITY_ID = "99000000-0000-0000-0000-0000000000d5"
TEST_SECURITY_NAME = "VERIFY notetermsdsl structured note"
TEST_FILING_ACCESSION = "9999999999-88-888888"
TEST_FILING_DOCUMENT = "verify_notetermsdsl.htm"

# Every column on the terms table that holds a monetary or percentage value.
# All must be Postgres `numeric` — never real/double precision.
NUMERIC_TERM_COLUMNS = [
    "protection_pct",
    "cap_pct",
    "participation_rate",
    "coupon_rate",
    "coupon_barrier_pct",
    "autocall_barrier_pct",
    "tenor_years",
]

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


async def teardown(conn) -> None:
    """Remove ONLY this script's fixtures, child rows before parents."""
    await conn.execute(
        f"DELETE FROM {TERMS_TABLE} WHERE global_security_id = $1::uuid",
        TEST_SECURITY_ID,
    )
    await conn.execute(
        "DELETE FROM portfolio.reference_filings WHERE accession_number = $1",
        TEST_FILING_ACCESSION,
    )
    await conn.execute(
        "DELETE FROM portfolio.securities_global WHERE id = $1::uuid",
        TEST_SECURITY_ID,
    )


async def seed(conn) -> str:
    """Create the parent security + a reference filing. Returns the filing id."""
    await conn.execute(
        """
        INSERT INTO portfolio.securities_global (id, name, security_type)
        VALUES ($1::uuid, $2, 'structured_note')
        ON CONFLICT (id) DO NOTHING
        """,
        TEST_SECURITY_ID,
        TEST_SECURITY_NAME,
    )
    return await conn.fetchval(
        """
        INSERT INTO portfolio.reference_filings
            (cik, filer_name, form_type, accession_number, filing_date,
             primary_document, source_url, retention_classification)
        VALUES ('9999999999', 'VERIFY notetermsdsl', '424B2', $1,
                DATE '2026-01-02', $2,
                'https://www.sec.gov/verify/notetermsdsl', 'public_reference')
        ON CONFLICT (accession_number, primary_document) DO UPDATE
            SET filer_name = EXCLUDED.filer_name
        RETURNING id
        """,
        TEST_FILING_ACCESSION,
        TEST_FILING_DOCUMENT,
    )


async def app_service_connection():
    """A genuinely non-bypass session. Fails loudly — no SET ROLE fallback."""
    url = os.environ.get("APP_SERVICE_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "APP_SERVICE_DATABASE_URL is unset — RLS cannot be verified honestly. "
            "Refusing to fall back to SET ROLE."
        )
    conn = await asyncpg.connect(url, statement_cache_size=0)
    who = await conn.fetchval("SELECT current_user")
    bypass = await conn.fetchval(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    if bypass:
        await conn.close()
        raise RuntimeError(f"APP_SERVICE_DATABASE_URL role {who!r} bypasses RLS")
    return conn, who


async def main() -> int:
    admin = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)

    # Fail loudly and early if the non-bypass credential is broken.
    try:
        app_conn, app_user = await app_service_connection()
    except Exception as exc:  # noqa: BLE001
        check("APP_SERVICE_DATABASE_URL connects as a non-bypass role", False, str(exc))
        await admin.close()
        print("\n0 passed, 1 failed — RLS credential unusable, aborting.")
        return 1
    check(
        "APP_SERVICE_DATABASE_URL connects as a non-bypass role",
        True,
        f"current_user={app_user}, rolbypassrls=false",
    )

    await teardown(admin)

    try:
        filing_id = await seed(admin)

        # ── 1. Table shape + RLS ──────────────────────────────────────────
        for table in ("securities_global_note_terms", "note_terms_field_registry"):
            rls = await admin.fetchval(
                """
                SELECT c.relrowsecurity FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'portfolio' AND c.relname = $1
                """,
                table,
            )
            check(f"{table} exists with RLS enabled", rls is True, f"relrowsecurity={rls}")

            org_cols = await admin.fetchval(
                """
                SELECT count(*) FROM information_schema.columns
                WHERE table_schema = 'portfolio' AND table_name = $1
                  AND column_name = 'org_id'
                """,
                table,
            )
            check(f"{table} has NO org_id column", org_cols == 0, f"org_id columns={org_cols}")

            policies = await admin.fetch(
                "SELECT policyname, cmd FROM pg_policies "
                "WHERE schemaname = 'portfolio' AND tablename = $1 ORDER BY cmd",
                table,
            )
            check(f"{table} has exactly 4 policies", len(policies) == 4, f"count={len(policies)}")

            cmds = sorted(row["cmd"] for row in policies)
            check(
                f"{table} policies are SELECT/INSERT/UPDATE/DELETE, not one FOR ALL",
                cmds == ["DELETE", "INSERT", "SELECT", "UPDATE"],
                f"cmds={cmds}",
            )

        # ── 2. Numeric columns are `numeric`, not float ───────────────────
        types = {
            row["column_name"]: row["data_type"]
            for row in await admin.fetch(
                """
                SELECT column_name, data_type FROM information_schema.columns
                WHERE table_schema = 'portfolio'
                  AND table_name = 'securities_global_note_terms'
                """
            )
        }
        wrong = {c: types.get(c) for c in NUMERIC_TERM_COLUMNS if types.get(c) != "numeric"}
        check(
            "all monetary/percentage columns are Postgres numeric, not float",
            not wrong,
            f"checked {len(NUMERIC_TERM_COLUMNS)}; offenders={wrong}" if wrong
            else f"all {len(NUMERIC_TERM_COLUMNS)} are numeric",
        )

        # ── 3. VERSIONING PROOF — the core assertion of this sprint ───────
        prelim_id = await admin.fetchval(
            f"""
            INSERT INTO {TERMS_TABLE}
                (global_security_id, reference_filing_id, terms_status,
                 product_archetype, protection_type, protection_pct, cap_pct)
            VALUES ($1::uuid, $2::uuid, 'preliminary', 'buffered_note', 'buffer',
                    $3, $4)
            RETURNING id
            """,
            TEST_SECURITY_ID,
            filing_id,
            Decimal("10.00"),
            Decimal("18.50"),
        )
        final_id = await admin.fetchval(
            f"""
            INSERT INTO {TERMS_TABLE}
                (global_security_id, reference_filing_id, terms_status,
                 product_archetype, protection_type, protection_pct, cap_pct)
            VALUES ($1::uuid, $2::uuid, 'final', 'buffered_note', 'buffer',
                    $3, $4)
            RETURNING id
            """,
            TEST_SECURITY_ID,
            filing_id,
            Decimal("8.00"),
            Decimal("16.25"),
        )
        rows = await admin.fetch(
            f"""
            SELECT id, terms_status, protection_pct FROM {TERMS_TABLE}
            WHERE global_security_id = $1::uuid
              AND system_to IS NULL AND valid_to IS NULL
            ORDER BY terms_status
            """,
            TEST_SECURITY_ID,
        )
        statuses = sorted(row["terms_status"] for row in rows)
        check(
            "VERSIONING: preliminary AND final coexist as two rows for one security",
            len(rows) == 2
            and statuses == ["final", "preliminary"]
            and prelim_id != final_id,
            f"rows={len(rows)} statuses={statuses}",
        )

        by_status = {row["terms_status"]: row["protection_pct"] for row in rows}
        check(
            "VERSIONING: the preliminary-vs-final delta survives (10.00 -> 8.00)",
            by_status.get("preliminary") == Decimal("10.00")
            and by_status.get("final") == Decimal("8.00"),
            f"preliminary={by_status.get('preliminary')} final={by_status.get('final')}",
        )

        # ── 4. Unique constraint REJECTS a true duplicate ─────────────────
        dup_rejected = False
        dup_detail = "duplicate was accepted"
        try:
            await admin.execute(
                f"""
                INSERT INTO {TERMS_TABLE}
                    (global_security_id, reference_filing_id, terms_status)
                VALUES ($1::uuid, $2::uuid, 'final')
                """,
                TEST_SECURITY_ID,
                filing_id,
            )
        except asyncpg.exceptions.UniqueViolationError as exc:
            dup_rejected = True
            dup_detail = f"rejected by {exc.constraint_name or 'unique index'}"
        check(
            "unique rejects duplicate (global_security_id, terms_status, reference_filing_id)",
            dup_rejected,
            dup_detail,
        )

        # ── 5. Unique does NOT reject a different terms_status ────────────
        restated_ok = False
        restated_detail = ""
        try:
            restated_id = await admin.fetchval(
                f"""
                INSERT INTO {TERMS_TABLE}
                    (global_security_id, reference_filing_id, terms_status)
                VALUES ($1::uuid, $2::uuid, 'restated')
                RETURNING id
                """,
                TEST_SECURITY_ID,
                filing_id,
            )
            restated_ok = restated_id is not None
            restated_detail = "3 current rows now share one global_security_id"
        except Exception as exc:  # noqa: BLE001
            restated_detail = f"wrongly rejected: {type(exc).__name__}"
        current_count = await admin.fetchval(
            f"""
            SELECT count(*) FROM {TERMS_TABLE}
            WHERE global_security_id = $1::uuid
              AND system_to IS NULL AND valid_to IS NULL
            """,
            TEST_SECURITY_ID,
        )
        check(
            "unique does NOT reject a second/third DIFFERENT terms_status",
            restated_ok and current_count == 3,
            f"{restated_detail}; current rows={current_count}",
        )

        # ── 6. field_status validator ─────────────────────────────────────
        valid_status = {
            "protection_pct": "extracted",
            "coupon_rate": "not_applicable",
            "cap_pct": "extraction_failed",
            "tenor_years": "not_in_template",
        }
        validator_ok = validate_field_status(valid_status) == valid_status
        check(
            "field_status validator accepts all four states",
            validator_ok and set(valid_status.values()) == FIELD_STATES,
            f"states covered={sorted(set(valid_status.values()))}",
        )

        raised = False
        raised_detail = "no exception raised"
        try:
            validate_field_status({"protection_pct": "extracted", "cap_pct": "probably_fine"})
        except FieldStatusError as exc:
            raised = True
            raised_detail = str(exc)[:80]
        check("field_status validator raises on a fifth invalid state", raised, raised_detail)

        # The dataclass must run the same gate, not just the free function.
        model_raised = False
        try:
            NoteTerms(
                global_security_id=TEST_SECURITY_ID,
                terms_status="final",
                field_status={"cap_pct": "guessed"},
            )
        except FieldStatusError:
            model_raised = True
        check("NoteTerms.__post_init__ enforces the same four states", model_raised)

        # ── 7. Field registry ─────────────────────────────────────────────
        hazard_rows = await admin.fetch(
            f"SELECT field_key FROM {REGISTRY_TABLE} WHERE hazard_field ORDER BY field_key"
        )
        hazard_keys = {row["field_key"] for row in hazard_rows}
        expected_hazards = {
            "protection_type",
            "basket_type",
            "return_basis",
            "is_decrement_index",
            "autocall_frequency",
            "terms_status",
        }
        check(
            "registry has exactly 6 hazard fields, matching the specified list",
            len(hazard_rows) == 6 and hazard_keys == expected_hazards,
            f"count={len(hazard_rows)} "
            f"missing={sorted(expected_hazards - hazard_keys)} "
            f"unexpected={sorted(hazard_keys - expected_hazards)}",
        )
        check(
            "models.HAZARD_FIELD_KEYS matches the seeded registry",
            set(HAZARD_FIELD_KEYS) == hazard_keys,
            f"code={len(HAZARD_FIELD_KEYS)} db={len(hazard_keys)}",
        )

        # Every registered field must be a real column on the terms table.
        registry_keys = {
            row["field_key"] for row in await admin.fetch(f"SELECT field_key FROM {REGISTRY_TABLE}")
        }
        orphans = sorted(registry_keys - set(types))
        check(
            "every registry field_key is a real column on the terms table",
            not orphans,
            f"{len(registry_keys)} registered; orphans={orphans}" if orphans
            else f"{len(registry_keys)} registered, all resolve",
        )

        # ── 8. Global read under app_service with NO org context ──────────
        org_ctx = await app_conn.fetchval("SELECT current_setting('app.current_org_id', true)")
        terms_read = await app_conn.fetchval(
            f"SELECT count(*) FROM {TERMS_TABLE} WHERE global_security_id = $1::uuid",
            TEST_SECURITY_ID,
        )
        registry_read = await app_conn.fetchval(f"SELECT count(*) FROM {REGISTRY_TABLE}")
        check(
            "global read works under app_service with no org context set",
            terms_read == 3 and registry_read == 19 and not org_ctx,
            f"org_ctx={org_ctx!r} terms={terms_read} registry={registry_read}",
        )

        # ── 9. NEGATIVE — write under app_service without is_super_admin ──
        for table, label in ((TERMS_TABLE, "note_terms"), (REGISTRY_TABLE, "field_registry")):
            blocked = False
            detail = "insert was ACCEPTED — RLS is not gating writes"
            tx = app_conn.transaction()
            await tx.start()
            try:
                await app_conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")
                if table == TERMS_TABLE:
                    await app_conn.execute(
                        f"INSERT INTO {table} (global_security_id, terms_status) "
                        f"VALUES ($1::uuid, 'preliminary')",
                        TEST_SECURITY_ID,
                    )
                else:
                    await app_conn.execute(
                        f"INSERT INTO {table} (field_key, display_label, data_type) "
                        f"VALUES ('verify_notetermsdsl_bogus', 'Bogus', 'text')"
                    )
            except asyncpg.exceptions.InsufficientPrivilegeError as exc:
                blocked = True
                detail = f"rejected: {type(exc).__name__}"
            finally:
                await tx.rollback()
            check(
                f"NEGATIVE: {label} insert under app_service without is_super_admin is rejected",
                blocked,
                detail,
            )

        # ── 10. Bitemporal convention copied exactly ──────────────────────
        bitemporal = await admin.fetch(
            """
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'portfolio'
              AND table_name = 'securities_global_note_terms'
              AND column_name IN ('valid_from', 'valid_to', 'system_from', 'system_to')
            ORDER BY column_name
            """
        )
        shape = {
            row["column_name"]: (row["data_type"], row["is_nullable"]) for row in bitemporal
        }
        expected_shape = {
            "system_from": ("timestamp with time zone", "NO"),
            "system_to": ("timestamp with time zone", "YES"),
            "valid_from": ("timestamp with time zone", "NO"),
            "valid_to": ("timestamp with time zone", "YES"),
        }
        check(
            "bitemporal columns match portfolio.securities_global exactly",
            shape == expected_shape,
            f"shape={shape}" if shape != expected_shape else "valid_from/valid_to/system_from/system_to",
        )

    finally:
        await teardown(admin)
        await app_conn.close()
        await admin.close()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
