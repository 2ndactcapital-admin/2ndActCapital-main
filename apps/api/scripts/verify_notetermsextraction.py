"""Verification — term extraction from reference_filings into note-terms rows.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END.

APP_SERVICE_DATABASE_URL IS REQUIRED and there is NO SET ROLE fallback. If that
credential does not connect, this script FAILS loudly rather than quietly
verifying RLS under a differently-privileged session.

THE ENSEMBLE CHECKS ARE MOCKED ON PURPOSE
──────────────────────────────────────────────────────────────────────────────
The two core assertions — disagreement forces needs_review, agreement does not —
are run against scripted model responses, not live calls. A live two-model
ensemble is nondeterministic: an assertion that depends on Sonnet happening to
disagree with Haiku today is a flaky test that will eventually pass or fail for
reasons unrelated to the code. The mock pins the behaviour under test (the
comparison logic) and the real run's disagreement RATE is reported separately
from live data further down.

Run:
    python3 scripts/verify_notetermsextraction.py
"""

from __future__ import annotations

import asyncio
import json
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

import services.note_terms_extraction as nte  # noqa: E402
from models.note_terms import HAZARD_FIELD_KEYS  # noqa: E402
from services.note_terms_corrections import (  # noqa: E402
    SOURCE_HAZARD_ENSEMBLE,
    get_note_terms_corrections,
    log_note_terms_correction,
)
from services.note_terms_validators import (  # noqa: E402
    autocall_le_coupon_barrier,
    barrier_price_consistent,
    cik_matches_filer,
    cusip_checksum,
    tenor_consistent,
)

TERMS_TABLE = "portfolio.securities_global_note_terms"
FILINGS_TABLE = "portfolio.reference_filings"
SECURITIES_TABLE = "portfolio.securities_global"
IDENTIFIERS_TABLE = "portfolio.securities_global_identifiers"
RELATIONSHIPS_TABLE = "portfolio.securities_global_relationships"
REGISTRY_TABLE = "portfolio.note_terms_field_registry"

# Fixed fixture identity so teardown is exact and reruns are idempotent.
AGREE_ACCESSION = "9999999999-77-777771"
DISAGREE_ACCESSION = "9999999999-77-777772"
FIXTURE_ACCESSIONS = (AGREE_ACCESSION, DISAGREE_ACCESSION)
FIXTURE_CIK = "895421"  # MORGAN STANLEY — real, so cik_matches_filer can pass
FIXTURE_FILER = "VERIFY notetermsextraction fixture"
FIXTURE_SECURITY_NAME = "VERIFY notetermsextraction fixture note"
FIXTURE_CUSIP = "99999VER3"  # checksum-valid, deliberately not a real CUSIP

# A real CUSIP from the corpus (the Citigroup NDX FWP) and the same identifier
# with its first two characters transposed — the realistic mistyping.
REAL_CUSIP = "17333HJG0"
TRANSPOSED_CUSIP = "71333HJG0"

FIXTURE_TEXT = """VERIFY FIXTURE — notetermsextraction. Contingent Income Auto-Callable \
Securities due January 15, 2027.
Issuer: Morgan Stanley. CUSIP: 99999VER3.
The Underlying is the Common Stock of NVIDIA Corporation.
Initial Valuation Date: January 15, 2025. Final Valuation Date: January 15, 2027.
Initial Level: 100.00. Barrier Level: 70.00, which is 70% of the Initial Level.
The securities have a buffer of 30% against decline in the underlying.
Contingent coupon of 9.50% per annum, paid if the underlying closes at or above \
the Coupon Barrier of 60% of the Initial Level.
The securities are automatically callable quarterly if the underlying closes at \
or above the Call Threshold of 100% of the Initial Level.
No call period: the first six months. Notional currency: USD. Return basis: \
price return, excluding dividends.
"""

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ── Scripted model responses ──────────────────────────────────────────────────

def _f(value, quote, absent=False):
    return {"value": value, "absent": absent, "quote": quote}


PRIMARY_PAYLOAD = {
    "issuer": "Morgan Stanley",
    "cusip": FIXTURE_CUSIP,
    "security_name": FIXTURE_SECURITY_NAME,
    "initial_level": 100,
    "barrier_price": 70,
    "barrier_pct": 70,
    "underlyings": ["the Common Stock of NVIDIA Corporation"],
    "fields": {
        "product_archetype": _f("autocallable", "automatically callable quarterly"),
        "protection_type": _f("buffer", "buffer of 30% against decline"),
        "basket_type": _f("single", "the Common Stock of NVIDIA Corporation"),
        "return_basis": _f("price", "price return, excluding dividends"),
        "is_decrement_index": _f(False, "Return basis: price return"),
        "autocall_frequency": _f("quarterly", "automatically callable quarterly"),
        "terms_status": _f("final", "Final Valuation Date: January 15, 2027"),
        "protection_pct": _f(30, "buffer of 30%"),
        # cap_pct / participation_rate do not apply to an autocallable — the
        # registry says so, and the pipeline must mark them not_applicable
        # rather than extraction_failed.
        "cap_pct": _f(None, "", absent=True),
        "participation_rate": _f(None, "", absent=True),
        "coupon_rate": _f(9.5, "Contingent coupon of 9.50% per annum"),
        "coupon_barrier_pct": _f(60, "Coupon Barrier of 60% of the Initial Level"),
        "autocall_barrier_pct": _f(100, "Call Threshold of 100% of the Initial Level"),
        "has_no_call_period": _f(True, "No call period: the first six months"),
        "no_call_months": _f(6, "No call period: the first six months"),
        "initial_valuation_date": _f("2025-01-15", "Initial Valuation Date: January 15, 2025"),
        "final_valuation_date": _f("2027-01-15", "Final Valuation Date: January 15, 2027"),
        "tenor_years": _f(2, "due January 15, 2027"),
        "notional_currency": _f("USD", "Notional currency: USD"),
    },
}

HAZARD_AGREE = {
    "protection_type": {"value": "buffer", "quote": "buffer of 30%"},
    "basket_type": {"value": "single", "quote": "the Common Stock of NVIDIA"},
    "return_basis": {"value": "price", "quote": "price return, excluding dividends"},
    "is_decrement_index": {"value": False, "quote": "price return"},
    "autocall_frequency": {"value": "quarterly", "quote": "callable quarterly"},
    "terms_status": {"value": "final", "quote": "Final Valuation Date"},
}

# Two hazard fields flipped to their catastrophic opposites: buffer->floor
# (opposite payoff) and single->worst_of (completely different risk). Both are
# arithmetically invisible — every numeric validator still passes.
HAZARD_DISAGREE = dict(HAZARD_AGREE)
HAZARD_DISAGREE["protection_type"] = {"value": "floor", "quote": "buffer of 30%"}
HAZARD_DISAGREE["basket_type"] = {"value": "worst_of", "quote": "the Common Stock"}

MOCK_PRIMARY_MODEL = "claude-haiku-4-5-20251001"
MOCK_SECONDARY_MODEL = "mock-independent-second-model"


def install_mock(hazard_payload: dict):
    """Patch the two model entry points. Returns a restore callable."""
    real_call = nte.call_claude_json
    real_last = nte._last_ensemble_model_used

    async def fake_call(system, user, max_tokens=400, **kwargs):
        task = kwargs.get("task_type")
        if task == "note_terms_hazard_ensemble":
            return dict(hazard_payload)
        if task == "note_terms_extraction":
            return json.loads(json.dumps(PRIMARY_PAYLOAD))
        return None

    async def fake_last(pool):
        # A genuinely different model id, so the independence guard is satisfied
        # and the ensemble counts as measured.
        return MOCK_SECONDARY_MODEL

    nte.call_claude_json = fake_call
    nte._last_ensemble_model_used = fake_last

    def restore():
        nte.call_claude_json = real_call
        nte._last_ensemble_model_used = real_last

    return restore


# ── Fixtures ──────────────────────────────────────────────────────────────────


async def teardown(conn) -> None:
    """Remove ONLY this script's fixtures, children before parents."""
    filing_ids = [
        r["id"] for r in await conn.fetch(
            f"SELECT id FROM {FILINGS_TABLE} WHERE accession_number = ANY($1::text[])",
            list(FIXTURE_ACCESSIONS),
        )
    ]
    terms_ids = [
        r["id"] for r in await conn.fetch(
            f"SELECT id FROM {TERMS_TABLE} WHERE reference_filing_id = ANY($1::uuid[])",
            filing_ids,
        )
    ] if filing_ids else []

    security_ids = [
        r["id"] for r in await conn.fetch(
            f"SELECT id FROM {SECURITIES_TABLE} WHERE name = $1", FIXTURE_SECURITY_NAME
        )
    ]

    if terms_ids:
        await conn.execute(
            "DELETE FROM document_field_corrections WHERE target_type = 'note_terms' "
            "AND target_id = ANY($1::uuid[])",
            terms_ids,
        )
    await conn.execute(
        "DELETE FROM document_field_corrections WHERE target_type = 'note_terms' "
        "AND field_name LIKE 'verify\\_%'"
    )
    if filing_ids:
        await conn.execute(
            f"DELETE FROM {TERMS_TABLE} WHERE reference_filing_id = ANY($1::uuid[])",
            filing_ids,
        )
    if security_ids:
        await conn.execute(
            f"DELETE FROM {RELATIONSHIPS_TABLE} WHERE from_global_security_id = ANY($1::uuid[])",
            security_ids,
        )
        await conn.execute(
            f"DELETE FROM {IDENTIFIERS_TABLE} WHERE global_security_id = ANY($1::uuid[])",
            security_ids,
        )
        await conn.execute(
            f"DELETE FROM {TERMS_TABLE} WHERE global_security_id = ANY($1::uuid[])",
            security_ids,
        )
        await conn.execute(f"DELETE FROM {SECURITIES_TABLE} WHERE id = ANY($1::uuid[])", security_ids)
    await conn.execute(
        f"DELETE FROM {IDENTIFIERS_TABLE} WHERE id_value = $1", FIXTURE_CUSIP
    )
    await conn.execute(
        f"DELETE FROM {FILINGS_TABLE} WHERE accession_number = ANY($1::text[])",
        list(FIXTURE_ACCESSIONS),
    )


async def seed_filing(conn, accession: str) -> str:
    return str(await conn.fetchval(
        f"""
        INSERT INTO {FILINGS_TABLE}
            (cik, filer_name, form_type, accession_number, filing_date,
             primary_document, source_url, extracted_text, extraction_status)
        VALUES ($1, $2, '424B2', $3, DATE '2025-01-15', $4,
                'https://example.invalid/verify', $5, 'extracted')
        RETURNING id
        """,
        FIXTURE_CIK, FIXTURE_FILER, accession, f"{accession}.htm", FIXTURE_TEXT,
    ))


async def app_service_connection():
    url = os.environ.get("APP_SERVICE_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "APP_SERVICE_DATABASE_URL is unset — RLS cannot be verified honestly. "
            "There is no SET ROLE fallback here by design."
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


# ── Checks ────────────────────────────────────────────────────────────────────


def check_validators() -> None:
    """All five validators, on known-GOOD and known-BAD inputs.

    Positive-only tests are worthless here: a validator hardcoded to return True
    would sail through them. Every one of the five gets both.
    """
    # 1. cusip_checksum
    good = cusip_checksum(REAL_CUSIP)[0] and cusip_checksum("037833100")[0]
    bad = (not cusip_checksum("17333HJG1")[0]) and (not cusip_checksum("NOTACUSIP")[0]) \
        and (not cusip_checksum("")[0])
    check("cusip_checksum accepts valid and rejects invalid", good and bad,
          f"valid={good} rejects_bad={bad}")

    # 2. cusip_checksum — the realistic error mode
    transposed_rejected = not cusip_checksum(TRANSPOSED_CUSIP)[0]
    check(
        "cusip_checksum rejects a single transposed digit",
        cusip_checksum(REAL_CUSIP)[0] and transposed_rejected,
        f"{REAL_CUSIP} valid, {TRANSPOSED_CUSIP} rejected={transposed_rejected}",
    )

    # 3. cik_matches_filer
    ok_match = cik_matches_filer("Morgan Stanley Finance LLC", "1666268")[0]
    ok_mismatch = not cik_matches_filer("Barclays Bank PLC", "895421")[0]
    ok_empty = not cik_matches_filer("", "895421")[0]
    ok_unknown = cik_matches_filer("Some New Issuer SA", "1234567")[0]
    check("cik_matches_filer matches, rejects the wrong party, tolerates unknown CIK",
          ok_match and ok_mismatch and ok_empty and ok_unknown,
          f"match={ok_match} mismatch_rejected={ok_mismatch} "
          f"empty_rejected={ok_empty} unknown_cik_tolerated={ok_unknown}")

    # 4. barrier_price_consistent
    ok_good = barrier_price_consistent(Decimal("0.70"), Decimal("4500"), Decimal("3150"))[0]
    ok_pct = barrier_price_consistent(70, Decimal("4500"), Decimal("3150"))[0]
    ok_round = barrier_price_consistent(
        Decimal("0.70"), Decimal("4500"), Decimal("3150.009"))[0]
    ok_bad = not barrier_price_consistent(
        Decimal("0.70"), Decimal("4500"), Decimal("3600"))[0]
    check("barrier_price_consistent holds on consistent input and fails on inconsistent",
          ok_good and ok_pct and ok_round and ok_bad,
          f"frac={ok_good} pct_form={ok_pct} within_tolerance={ok_round} "
          f"inconsistent_rejected={ok_bad}")

    # 5. autocall_le_coupon_barrier
    ok_normal = autocall_le_coupon_barrier(Decimal("0.60"), Decimal("1.00"))[0]
    ok_equal = autocall_le_coupon_barrier(Decimal("0.70"), Decimal("0.70"))[0]
    ok_inverted = not autocall_le_coupon_barrier(Decimal("1.00"), Decimal("0.60"))[0]
    check("autocall_le_coupon_barrier accepts the usual ordering and flags inversion",
          ok_normal and ok_equal and ok_inverted,
          f"normal={ok_normal} equal={ok_equal} inverted_flagged={ok_inverted}")

    # 6. tenor_consistent
    ok_t = tenor_consistent(date(2025, 1, 15), date(2027, 1, 15), Decimal("2"))[0]
    ok_t_bad = not tenor_consistent(date(2025, 1, 15), date(2027, 1, 15), Decimal("5"))[0]
    ok_t_rev = not tenor_consistent(date(2027, 1, 15), date(2025, 1, 15), Decimal("2"))[0]
    check("tenor_consistent matches real dates and rejects a wrong tenor",
          ok_t and ok_t_bad and ok_t_rev,
          f"consistent={ok_t} wrong_tenor_rejected={ok_t_bad} reversed_rejected={ok_t_rev}")


async def check_ensemble(pool, conn, registry_keys: set[str]) -> None:
    """The core of the sprint: disagreement flags, agreement does not."""
    # ── Disagreement ──────────────────────────────────────────────────────
    disagree_filing = await seed_filing(conn, DISAGREE_ACCESSION)
    restore = install_mock(HAZARD_DISAGREE)
    try:
        res = await nte.extract_terms(disagree_filing, pool)
    finally:
        restore()

    flagged = res.ok and res.extraction_confidence == "needs_review"
    fields_flagged = set(res.hazard_disagreements)
    check(
        "HAZARD ENSEMBLE — disagreement forces extraction_confidence='needs_review'",
        flagged and fields_flagged == {"protection_type", "basket_type"},
        f"confidence={res.extraction_confidence} disagreed_on={sorted(fields_flagged)} "
        f"validator_failures={res.validator_failures}",
    )

    recorded = await get_note_terms_corrections(conn, res.note_terms_id) if res.note_terms_id else []
    by_field = {r["field_name"]: r for r in recorded}
    both_recorded = True
    detail_bits = []
    for fld, primary, secondary in (
        ("protection_type", "buffer", "floor"),
        ("basket_type", "single", "worst_of"),
    ):
        row = by_field.get(fld)
        if row is None:
            both_recorded = False
            detail_bits.append(f"{fld}=MISSING")
            continue
        notes = json.loads(row["notes"] or "{}")
        payload = json.loads(notes.get("notes") or "{}")
        got_primary = payload.get("primary", {}).get("value")
        got_secondary = payload.get("secondary", {}).get("value")
        okrow = (
            got_primary == primary and got_secondary == secondary
            and row["org_id"] is None
            and notes.get("source") == SOURCE_HAZARD_ENSEMBLE
        )
        both_recorded = both_recorded and okrow
        detail_bits.append(f"{fld}: primary={got_primary!r} secondary={got_secondary!r}")
    check(
        "HAZARD ENSEMBLE — BOTH answers recorded, neither silently chosen",
        both_recorded and len(by_field) >= 2,
        "; ".join(detail_bits),
    )

    # ── Agreement, isolated from validator-triggered needs_review ─────────
    agree_filing = await seed_filing(conn, AGREE_ACCESSION)
    restore = install_mock(HAZARD_AGREE)
    try:
        res_ok = await nte.extract_terms(agree_filing, pool)
    finally:
        restore()

    clean = (
        res_ok.ok
        and not res_ok.hazard_disagreements
        and not res_ok.validator_failures
        and res_ok.extraction_confidence == "high"
    )
    check(
        "HAZARD ENSEMBLE — agreement does NOT force needs_review (ensemble is not fail-closed)",
        clean,
        f"confidence={res_ok.extraction_confidence} disagreements={res_ok.hazard_disagreements} "
        f"validator_failures={res_ok.validator_failures} "
        f"validator_warnings={res_ok.validator_warnings} "
        f"hazard_fields_compared={len(res_ok.hazard_compared)}",
    )

    check(
        "HAZARD ENSEMBLE — all six registry hazard fields were compared, not a subset",
        set(res_ok.hazard_compared) == set(HAZARD_FIELD_KEYS),
        f"compared={sorted(res_ok.hazard_compared)}",
    )

    # field_status completeness on the fixture row
    missing = registry_keys - set(res_ok.field_status)
    extra = set(res_ok.field_status) - registry_keys
    check(
        "field_status covers every registry field on the fixture row (no key omitted)",
        not missing and not extra,
        f"{len(res_ok.field_status)} keys; missing={sorted(missing)} extra={sorted(extra)}",
    )

    na = {k for k, v in res_ok.field_status.items() if v == "not_applicable"}
    check(
        "field_status uses not_applicable for archetype-irrelevant fields",
        {"cap_pct", "participation_rate"} <= na,
        f"not_applicable={sorted(na)}",
    )
    return res_ok


async def check_real_rows(conn, registry_keys: set[str]) -> dict:
    """Everything below is measured from the REAL Task 5 rows, not fixtures."""
    rows = await conn.fetch(
        f"""
        SELECT t.id, t.reference_filing_id, t.terms_status, t.extraction_confidence,
               t.field_status, t.source_char_start, t.source_char_end,
               f.extraction_status, f.form_type, f.filer_name, f.extracted_text
        FROM {TERMS_TABLE} t
        JOIN {FILINGS_TABLE} f ON f.id = t.reference_filing_id
        WHERE t.valid_to IS NULL AND t.system_to IS NULL
          AND f.accession_number <> ALL($1::text[])
        ORDER BY t.valid_from
        """,
        list(FIXTURE_ACCESSIONS),
    )

    check("Task 5 produced rows (an extraction sprint that extracted nothing is not a pass)",
          len(rows) > 0, f"{len(rows)} current note-terms rows from real filings")
    if not rows:
        return {}

    # field_status completeness across EVERY real row
    bad = []
    for r in rows:
        fs = r["field_status"]
        fs = json.loads(fs) if isinstance(fs, str) else dict(fs or {})
        missing = registry_keys - set(fs)
        if missing:
            bad.append((str(r["id"]), sorted(missing)))
    check(
        "field_status is populated for EVERY registry field on EVERY created row",
        not bad,
        f"{len(rows)} rows x {len(registry_keys)} registry fields; offenders={bad[:3]}",
    )

    # source spans, with the ACTUAL substrings reported
    spanned = [r for r in rows if r["source_char_start"] is not None
               and r["source_char_end"] is not None]
    check("source_char_start/end are populated", len(spanned) >= 3,
          f"{len(spanned)}/{len(rows)} rows carry a source span")

    print("\n  --- source-span substrings (first 3 rows, 220 chars each) ---")
    plausible = 0
    for r in spanned[:3]:
        text = r["extracted_text"] or ""
        s, e = r["source_char_start"], r["source_char_end"]
        snippet = text[s:e][:220].replace("\n", " ")
        in_bounds = 0 <= s < e <= len(text)
        # "plausibly related" made concrete: the span must actually contain
        # structured-note vocabulary, not merely be non-null.
        low = text[s:e].lower()
        related = any(k in low for k in (
            "underlying", "barrier", "buffer", "coupon", "valuation date",
            "principal", "participation", "maturity", "notes", "securities",
        ))
        plausible += 1 if (in_bounds and related) else 0
        print(f"    {str(r['id'])[:8]} [{s}:{e}] in_bounds={in_bounds} related={related}")
        print(f"      {snippet!r}")
    check(
        "source spans slice real, term-related text out of extracted_text (3 rows inspected)",
        plausible >= 3, f"{plausible}/3 spans in bounds and containing note vocabulary",
    )

    # terms_status derivation
    wrong_status = [
        str(r["id"]) for r in rows
        if r["terms_status"] != nte.FORM_TYPE_TO_TERMS_STATUS.get(r["form_type"])
    ]
    check("terms_status is derived from form_type (FWP->preliminary, 424B2->final)",
          not wrong_status, f"{len(rows)} rows, mismatches={wrong_status[:3]}")

    return {"rows": rows}


async def check_status_collision(conn) -> None:
    """Task 3 step 7's resolution must be internally consistent.

    The resolution was: never write reference_filings.extraction_status from the
    terms pipeline, and derive terms progress from the note-terms rows instead.
    The assertions below are what that resolution implies. If someone later
    "helpfully" writes a terms state into that column, these break.
    """
    vocab = {"pending", "fetched", "extracted", "failed", "skipped"}
    statuses = {
        r["extraction_status"]: r["n"] for r in await conn.fetch(
            f"SELECT extraction_status, count(*) AS n FROM {FILINGS_TABLE} GROUP BY 1"
        )
    }
    check(
        "extraction_status still holds ONLY corpus-pipeline values (no terms state leaked in)",
        set(statuses) <= vocab,
        f"observed={statuses}",
    )

    # Every filing we extracted terms from must still read 'extracted'.
    drifted = await conn.fetch(
        f"""
        SELECT DISTINCT f.id, f.extraction_status
        FROM {TERMS_TABLE} t JOIN {FILINGS_TABLE} f ON f.id = t.reference_filing_id
        WHERE t.valid_to IS NULL AND f.extraction_status <> 'extracted'
        """
    )
    check(
        "no filing with terms was left in a status the corpus pipeline would misread",
        not drifted,
        f"{len(drifted)} filings drifted off 'extracted'"
        + (f" e.g. {drifted[0]['extraction_status']!r}" if drifted else ""),
    )

    # The corpus meaning of 'extracted' still holds: it has usable text.
    empty = await conn.fetchval(
        f"""
        SELECT count(*) FROM {FILINGS_TABLE}
        WHERE extraction_status = 'extracted'
          AND (extracted_text IS NULL OR length(btrim(extracted_text)) = 0)
        """
    )
    check("'extracted' still means what the corpus sprint made it mean (has text)",
          empty == 0, f"{empty} filings claim 'extracted' with no text")

    derived = await nte.filings_with_terms_extracted(conn)
    direct = {
        str(r["reference_filing_id"]) for r in await conn.fetch(
            f"SELECT DISTINCT reference_filing_id FROM {TERMS_TABLE} "
            "WHERE reference_filing_id IS NOT NULL AND valid_to IS NULL AND system_to IS NULL"
        )
    }
    check("terms progress is derivable without that column, and the two agree",
          derived == direct, f"{len(derived)} filings have terms")


async def check_correction_logging(app_conn) -> None:
    """Task 4 — the wrapper, exercised under the real app_service role."""
    target = await app_conn.fetchval(
        f"SELECT id FROM {TERMS_TABLE} WHERE valid_to IS NULL ORDER BY valid_from DESC LIMIT 1"
    )
    if target is None:
        check("log_note_terms_correction writes a global, org-NULL correction", False,
              "no note-terms row exists to correct")
        return

    correction_id = await log_note_terms_correction(
        app_conn,
        note_terms_id=str(target),
        field_name="verify_protection_type",
        original_value="buffer",
        corrected_value="floor",
        notes="verify_notetermsextraction",
    )

    # Read it back with NO org context at all — that is the property under test.
    await app_conn.execute("SELECT set_config('app.current_org_id', '', false)")
    await app_conn.execute("SELECT set_config('app.is_super_admin', '', false)")
    row = await app_conn.fetchrow(
        "SELECT id, target_type, target_id, org_id, document_id, field_name, "
        "original_value, corrected_value, corrected_by "
        "FROM document_field_corrections WHERE id = $1::uuid",
        correction_id,
    )
    ok = (
        row is not None
        and row["target_type"] == "note_terms"
        and str(row["target_id"]) == str(target)
        and row["org_id"] is None
        and row["document_id"] is None
        and row["corrected_value"] == "floor"
    )
    check(
        "log_note_terms_correction: target_type='note_terms', org_id NULL, "
        "readable under app_service with no org context",
        ok,
        f"target_type={row['target_type'] if row else None} "
        f"org_id={row['org_id'] if row else None} "
        f"readable={row is not None}",
    )

    await app_conn.execute("SELECT set_config('app.is_super_admin', 'true', false)")
    await app_conn.execute(
        "DELETE FROM document_field_corrections WHERE id = $1::uuid", correction_id
    )
    await app_conn.execute("SELECT set_config('app.is_super_admin', '', false)")


async def report_task5(conn) -> None:
    """Task 5's actual numbers, measured from what is in the database."""
    rows = await conn.fetch(
        f"""
        SELECT t.id, t.extraction_confidence, t.field_status, t.terms_status,
               t.source_char_start, f.form_type
        FROM {TERMS_TABLE} t JOIN {FILINGS_TABLE} f ON f.id = t.reference_filing_id
        WHERE t.valid_to IS NULL AND t.system_to IS NULL
          AND f.accession_number <> ALL($1::text[])
        """,
        list(FIXTURE_ACCESSIONS),
    )
    states: dict[str, int] = {}
    for r in rows:
        fs = r["field_status"]
        fs = json.loads(fs) if isinstance(fs, str) else dict(fs or {})
        for v in fs.values():
            states[v] = states.get(v, 0) + 1
    conf: dict[str, int] = {}
    for r in rows:
        conf[r["extraction_confidence"]] = conf.get(r["extraction_confidence"], 0) + 1
    forms: dict[str, int] = {}
    for r in rows:
        forms[r["form_type"]] = forms.get(r["form_type"], 0) + 1

    # Matched as text, not cast to jsonb: Postgres does not guarantee the
    # target_type filter is evaluated before the cast, and one legacy row with
    # non-JSON notes would turn this report into an error.
    source_marker = f'"source": "{SOURCE_HAZARD_ENSEMBLE}"'
    # Scoped to REAL rows — this script's own fixtures would otherwise inflate
    # the disagreement rate it is reporting.
    real_targets = f"""
        target_id IN (
            SELECT t.id FROM {TERMS_TABLE} t
            JOIN {FILINGS_TABLE} f ON f.id = t.reference_filing_id
            WHERE f.accession_number <> ALL($2::text[])
        )
    """
    disagreement_rows = await conn.fetchval(
        "SELECT count(DISTINCT target_id) FROM document_field_corrections "
        f"WHERE target_type = 'note_terms' AND notes LIKE '%' || $1 || '%' AND {real_targets}",
        source_marker, list(FIXTURE_ACCESSIONS),
    )
    disagreement_fields = await conn.fetch(
        "SELECT field_name, count(*) AS n FROM document_field_corrections "
        f"WHERE target_type = 'note_terms' AND notes LIKE '%' || $1 || '%' AND {real_targets} "
        "GROUP BY 1 ORDER BY 2 DESC",
        source_marker, list(FIXTURE_ACCESSIONS),
    )
    edges = await conn.fetchval(
        f"SELECT count(*) FROM {RELATIONSHIPS_TABLE} WHERE link_state = 'unresolved'"
    )
    resolved_edges = await conn.fetchval(
        f"SELECT count(*) FROM {RELATIONSHIPS_TABLE} WHERE link_state <> 'unresolved'"
    )
    securities = await conn.fetchval(f"SELECT count(*) FROM {SECURITIES_TABLE}")
    cusips = await conn.fetchval(
        f"SELECT count(*) FROM {IDENTIFIERS_TABLE} WHERE id_type = 'cusip'"
    )
    population = await conn.fetchval(
        f"""SELECT count(*) FROM {FILINGS_TABLE}
            WHERE extraction_status='extracted' AND filer_name <> 'VERIFY FIXTURE'
              AND length(extracted_text) >= 2000"""
    )

    total_slots = sum(states.values()) or 1
    print("\n" + "=" * 72)
    print("TASK 5 NUMBERS (measured from the database, not re-run)")
    print("=" * 72)
    print(f"  input population (extracted, non-fixture)  : {population}")
    print(f"  note-terms rows created                    : {len(rows)}  by form {forms}")
    print(f"  securities_global rows created             : {securities}")
    print(f"  CUSIP identifiers attached                 : {cusips}")
    print(f"  field_status distribution ({total_slots} slots):")
    for k, v in sorted(states.items(), key=lambda kv: -kv[1]):
        print(f"      {k:20s} {v:5d}  {v / total_slots * 100:5.1f}%")
    print(f"  extraction_confidence distribution:")
    for k, v in sorted(conf.items(), key=lambda kv: -kv[1]):
        print(f"      {str(k):20s} {v:5d}  {v / (len(rows) or 1) * 100:5.1f}%")
    print(f"  rows with >=1 hazard disagreement          : {disagreement_rows}"
          f"  ({disagreement_rows / (len(rows) or 1) * 100:.1f}%)")
    for r in disagreement_fields:
        print(f"      {r['field_name']:22s} {r['n']}")
    print(f"  unresolved underlying edges                : {edges}")
    print(f"  RESOLVED underlying edges                  : {resolved_edges}"
          f"   <- must be 0; resolution is the NEXT sprint")
    print("=" * 72)

    check("underlying edges exist and are ALL unresolved (no resolver was built here)",
          edges > 0 and resolved_edges == 0,
          f"unresolved={edges} resolved={resolved_edges}")


# ── Main ──────────────────────────────────────────────────────────────────────


async def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("[FAIL] DATABASE_URL is unset")
        return 1

    try:
        app_conn, who = await app_service_connection()
    except Exception as exc:
        check("APP_SERVICE_DATABASE_URL connects as a non-bypass role", False, str(exc))
        print("\nRESULT: FAIL (1 check, 1 failed)")
        return 1
    check("APP_SERVICE_DATABASE_URL connects as a non-bypass role", True, f"current_user={who}")

    pool = await asyncpg.create_pool(
        os.environ["DATABASE_URL"], statement_cache_size=0, min_size=1, max_size=4
    )
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        await teardown(conn)  # START

        registry_keys = {
            r["field_key"] for r in await conn.fetch(f"SELECT field_key FROM {REGISTRY_TABLE}")
        }
        live_hazard = {
            r["field_key"] for r in await conn.fetch(
                f"SELECT field_key FROM {REGISTRY_TABLE} WHERE hazard_field"
            )
        }
        check("registry is loaded and declares exactly 6 hazard fields",
              len(registry_keys) > 0 and len(live_hazard) == 6,
              f"{len(registry_keys)} fields, hazard={sorted(live_hazard)}")
        check("live registry hazard set matches models.note_terms.HAZARD_FIELD_KEYS",
              live_hazard == set(HAZARD_FIELD_KEYS),
              f"registry={sorted(live_hazard)}")

        check_validators()
        await check_ensemble(pool, conn, registry_keys)
        await check_real_rows(conn, registry_keys)
        await check_status_collision(conn)
        await check_correction_logging(app_conn)
        await report_task5(conn)
    finally:
        try:
            await teardown(conn)  # END
        finally:
            await conn.close()
            await pool.close()
            await app_conn.close()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\nRESULT: {'PASS' if failed == 0 else 'FAIL'} "
          f"({len(results)} checks, {passed} passed, {failed} failed)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
