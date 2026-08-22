"""Verification — EDGAR reference corpus.

Pass/fail only, no prompts, idempotent, teardown at start AND end. Every check
asserts a real value, not the existence of something.

Run:
    python3 scripts/verify_edgarcorpus.py
"""

from __future__ import annotations

import asyncio
import os
import sys

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

from services import edgar_fetch, storage  # noqa: E402
from services.edgar_fetch import FilingMeta  # noqa: E402
from datetime import date  # noqa: E402

TEST_ACCESSION = "9999999999-99-999999"
TEST_DOCUMENT = "verify_edgarcorpus.htm"
TEST_CIK = "9999999999"
TEST_HTML = (
    b"<html><body><p>Verification note. The <b>Barrier Level</b> is 70% of the "
    b"Initial Level of the Underlying.</p><p>Contingent coupon applies.</p>"
    b"</body></html>"
)

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


async def teardown(conn) -> None:
    """Remove ONLY this script's fixtures. The sample corpus is left alone."""
    rows = await conn.fetch(
        "SELECT r2_key FROM portfolio.reference_filings WHERE accession_number = $1",
        TEST_ACCESSION,
    )
    await conn.execute(
        "DELETE FROM portfolio.reference_filings WHERE accession_number = $1",
        TEST_ACCESSION,
    )
    for row in rows:
        if not row["r2_key"]:
            continue
        for key in (row["r2_key"], edgar_fetch.offset_map_key(row["r2_key"])):
            try:
                storage.delete_object(key)
            except Exception:  # noqa: BLE001 — absent object is the desired state
                pass


async def non_bypass_connection():
    """A connection that does NOT bypass RLS, plus a label for how we got it.

    Preferred: APP_SERVICE_DATABASE_URL. If those credentials do not
    authenticate in this environment, fall back to assuming the app_service
    role from the admin connection — still a genuinely non-bypass session, and
    the fallback is reported rather than hidden.
    """
    url = os.environ.get("APP_SERVICE_DATABASE_URL")
    if url:
        try:
            conn = await asyncpg.connect(url, statement_cache_size=0)
            return conn, "APP_SERVICE_DATABASE_URL", None
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}"
    else:
        reason = "unset"

    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    await conn.execute("SET ROLE app_service")
    who = await conn.fetchval("SELECT current_user")
    if who != "app_service":
        await conn.close()
        raise RuntimeError("could not obtain a non-bypass app_service session")
    return conn, f"SET ROLE app_service (APP_SERVICE_DATABASE_URL {reason})", reason


async def main() -> int:
    admin = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    await teardown(admin)

    try:
        # ── 1. Table shape ─────────────────────────────────────────────────
        rls_enabled = await admin.fetchval(
            """
            SELECT c.relrowsecurity FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'portfolio' AND c.relname = 'reference_filings'
            """
        )
        check("table exists with RLS enabled", rls_enabled is True, f"relrowsecurity={rls_enabled}")

        org_id_cols = await admin.fetchval(
            """
            SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'portfolio' AND table_name = 'reference_filings'
              AND column_name = 'org_id'
            """
        )
        check("NO org_id column (global table)", org_id_cols == 0, f"org_id columns={org_id_cols}")

        policies = await admin.fetch(
            """
            SELECT policyname, cmd FROM pg_policies
            WHERE schemaname = 'portfolio' AND tablename = 'reference_filings'
            ORDER BY cmd
            """
        )
        check("exactly 4 policies", len(policies) == 4, f"count={len(policies)}")

        cmds = sorted(row["cmd"] for row in policies)
        check(
            "policies are SELECT/INSERT/UPDATE/DELETE, not a single FOR ALL",
            cmds == ["DELETE", "INSERT", "SELECT", "UPDATE"],
            f"cmds={cmds}",
        )

        # ── 2. RLS behaviour from a non-bypass session ─────────────────────
        app_conn, source, _ = await non_bypass_connection()
        try:
            org_ctx = await app_conn.fetchval(
                "SELECT coalesce(nullif(current_setting('app.org_id', true), ''), '<unset>')"
            )
            visible = await app_conn.fetchval(
                "SELECT count(*) FROM portfolio.reference_filings"
            )
            check(
                "global read with NO org context",
                org_ctx == "<unset>" and visible is not None and visible > 0,
                f"via {source}; app.org_id={org_ctx}; rows visible={visible}",
            )

            rejected = False
            detail = "insert was ALLOWED"
            try:
                async with app_conn.transaction():
                    await app_conn.execute(
                        """
                        INSERT INTO portfolio.reference_filings
                            (cik, filer_name, form_type, accession_number,
                             filing_date, primary_document, source_url)
                        VALUES ('0', 'rls probe', 'FWP', $1, '2025-01-01',
                                'rls_probe.htm', 'https://example.invalid')
                        """,
                        TEST_ACCESSION,
                    )
            except asyncpg.InsufficientPrivilegeError as exc:
                rejected = True
                detail = type(exc).__name__
            check("NEGATIVE: insert without is_super_admin is rejected", rejected, detail)

            leaked = await admin.fetchval(
                "SELECT count(*) FROM portfolio.reference_filings"
                " WHERE accession_number = $1 AND primary_document = 'rls_probe.htm'",
                TEST_ACCESSION,
            )
            check("rejected insert left no row behind", leaked == 0, f"rows={leaked}")
        finally:
            await app_conn.close()

        # ── 3. Idempotency of store_filing ─────────────────────────────────
        pool = await asyncpg.create_pool(
            os.environ["DATABASE_URL"], statement_cache_size=0, min_size=1, max_size=2
        )
        try:
            meta = FilingMeta(
                cik=TEST_CIK,
                filer_name="VERIFY FIXTURE",
                form_type="424B2",
                accession_number=TEST_ACCESSION,
                filing_date=date(2025, 1, 1),
                submission_path=f"edgar/data/{TEST_CIK}/{TEST_ACCESSION}.txt",
                primary_document=TEST_DOCUMENT,
                file_number="333-000000",
            )
            first_id = await edgar_fetch.store_filing(pool, meta, TEST_HTML)
            first = await admin.fetchrow(
                "SELECT id, content_hash, updated_at, r2_key, byte_size,"
                "       extraction_status, extracted_text"
                " FROM portfolio.reference_filings"
                " WHERE accession_number = $1 AND primary_document = $2",
                TEST_ACCESSION,
                TEST_DOCUMENT,
            )
            second_id = await edgar_fetch.store_filing(pool, meta, TEST_HTML)
            second = await admin.fetchrow(
                "SELECT id, content_hash FROM portfolio.reference_filings"
                " WHERE accession_number = $1 AND primary_document = $2",
                TEST_ACCESSION,
                TEST_DOCUMENT,
            )
            row_count = await admin.fetchval(
                "SELECT count(*) FROM portfolio.reference_filings"
                " WHERE accession_number = $1",
                TEST_ACCESSION,
            )
            check(
                "IDEMPOTENCY: two identical store_filing calls → exactly one row",
                row_count == 1 and first_id == second_id,
                f"rows={row_count} id1={first_id} id2={second_id}",
            )
            check(
                "IDEMPOTENCY: content_hash unchanged on re-store",
                first["content_hash"] == second["content_hash"] and bool(first["content_hash"]),
                f"hash={first['content_hash'][:16] if first['content_hash'] else None}…",
            )
            check(
                "fixture passed the prefilter and extracted text",
                first["extraction_status"] == "extracted"
                and "Barrier Level" in (first["extracted_text"] or ""),
                f"status={first['extraction_status']} len={len(first['extracted_text'] or '')}",
            )
        finally:
            await pool.close()

        # ── 4. R2 round-trip on a real sample-run object ───────────────────
        sample_row = await admin.fetchrow(
            """
            SELECT accession_number, r2_key, byte_size, content_hash
            FROM portfolio.reference_filings
            WHERE accession_number <> $1 AND r2_key IS NOT NULL
              AND extraction_status = 'extracted'
            ORDER BY byte_size
            LIMIT 1
            """,
            TEST_ACCESSION,
        )
        if sample_row is None:
            check("R2 round-trip on a sample-run object", False, "no sample-run row to read")
        else:
            try:
                fetched = storage.download_bytes(sample_row["r2_key"])
                check(
                    "R2 round-trip: stored object fetchable and byte length matches",
                    len(fetched) == sample_row["byte_size"],
                    f"{sample_row['r2_key']} fetched={len(fetched)} byte_size={sample_row['byte_size']}",
                )
            except Exception as exc:  # noqa: BLE001
                check("R2 round-trip on a sample-run object", False, f"{type(exc).__name__}: {exc}")

        # ── 5. Corpus content assertions ───────────────────────────────────
        totals = await admin.fetchrow(
            """
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE extraction_status = 'extracted') AS passed,
                   count(*) FILTER (WHERE extraction_status = 'skipped') AS skipped,
                   count(*) FILTER (WHERE extraction_status = 'failed') AS failed,
                   count(*) FILTER (WHERE form_type = '424B2') AS f424b2,
                   count(*) FILTER (WHERE form_type = 'FWP') AS fwp,
                   sum(byte_size) AS bytes,
                   count(*) FILTER (
                       WHERE retention_classification IS DISTINCT FROM 'public_reference'
                   ) AS misclassified
            FROM portfolio.reference_filings
            WHERE accession_number <> $1
            """,
            TEST_ACCESSION,
        )
        check(
            "sample run stored rows (zero would be a failed corpus sprint)",
            totals["n"] > 0,
            f"rows={totals['n']} passed={totals['passed']} skipped={totals['skipped']} "
            f"failed={totals['failed']} 424B2={totals['f424b2']} FWP={totals['fwp']} "
            f"bytes={totals['bytes']}",
        )

        extracted_text_len = await admin.fetchval(
            """
            SELECT max(length(extracted_text)) FROM portfolio.reference_filings
            WHERE extraction_status = 'extracted' AND accession_number <> $1
            """,
            TEST_ACCESSION,
        )
        check(
            "at least one 'extracted' row has non-empty extracted_text",
            bool(extracted_text_len and extracted_text_len > 0),
            f"longest extracted_text={extracted_text_len}",
        )

        check(
            "every row has retention_classification='public_reference'",
            totals["misclassified"] == 0,
            f"misclassified={totals['misclassified']}",
        )

        skipped_retained = await admin.fetchval(
            """
            SELECT count(*) FROM portfolio.reference_filings
            WHERE extraction_status = 'skipped' AND length(extracted_text) > 0
            """
        )
        check(
            "prefilter negatives retained with their text",
            skipped_retained == totals["skipped"],
            f"skipped={totals['skipped']} retained_with_text={skipped_retained}",
        )

        # ── 6. Offset provenance survived the round-trip ───────────────────
        if sample_row is not None:
            try:
                import json

                offsets = json.loads(
                    storage.download_bytes(
                        edgar_fetch.offset_map_key(sample_row["r2_key"])
                    )
                )
                raw = storage.download_bytes(sample_row["r2_key"])
                html, _encoding = edgar_fetch.html_text.decode_html(raw)
                sample_index = len(offsets["raw_start"]) // 2
                raw_start = offsets["raw_start"][sample_index]
                raw_end = offsets["raw_end"][sample_index]
                span = html[raw_start:raw_end]
                check(
                    "character offsets point at real raw-HTML spans",
                    len(offsets["raw_start"]) > 0
                    and offsets["encoding"] == _encoding
                    and "<" not in span,
                    f"segments={len(offsets['raw_start'])} span[{raw_start}:{raw_end}]={span[:60]!r}",
                )
            except Exception as exc:  # noqa: BLE001
                check("character offsets point at real raw-HTML spans", False, f"{type(exc).__name__}: {exc}")

        return 0 if all(passed for _, passed, _ in results) else 1
    finally:
        await teardown(admin)
        await admin.close()


if __name__ == "__main__":
    code = asyncio.run(main())
    failed = [name for name, passed, _ in results if not passed]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
    print("RESULT: " + ("PASS" if code == 0 else "FAIL"))
    raise SystemExit(code)
