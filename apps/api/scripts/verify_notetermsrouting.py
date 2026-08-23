"""Verification — STP policy + note-terms routing + the review queue.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END.

APP_SERVICE_DATABASE_URL IS REQUIRED and there is NO SET ROLE fallback. If that
credential does not connect, this script FAILS loudly rather than quietly
"verifying" RLS under a role that bypasses it.

THE MODEL CALLS ARE MOCKED, THE ROUTING IS NOT
──────────────────────────────────────────────────────────────────────────────
Every routing assertion runs the REAL extraction pipeline end to end — real
inserts, real ensemble comparison, real correction logging, real
``route_note_terms_row`` — with only the two Anthropic calls replaced by
scripted payloads. A live ensemble is nondeterministic; an assertion that
depends on two models happening to disagree today is a test that fails for
reasons unrelated to this sprint. The mock pins WHAT the readers said. It does
not touch what the code does with it.

Run:
    python3 scripts/verify_notetermsrouting.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.append(
    "/mnt/c/Users/Joe/2ndActCapital/apps/api/venv/lib/python3.12/site-packages"
)

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
    override=False,
)

import services.note_terms_extraction as nte  # noqa: E402
from services.note_terms_corrections import (  # noqa: E402
    SOURCE_HAZARD_ENSEMBLE,
    SOURCE_HUMAN,
)
from services.note_terms_routing import (  # noqa: E402
    NoteTermsRoutingPermissionError,
    active_policy,
    envelope_source,
    grant_stp,
    revoke_stp,
    route_note_terms_row,
)

TERMS_TABLE = "portfolio.securities_global_note_terms"
FILINGS_TABLE = "portfolio.reference_filings"
POLICY_TABLE = "portfolio.note_terms_stp_policy"
SECURITIES_TABLE = "portfolio.securities_global"
IDENTIFIERS_TABLE = "portfolio.securities_global_identifiers"
RELATIONSHIPS_TABLE = "portfolio.securities_global_relationships"
CORRECTIONS_TABLE = "document_field_corrections"

# ── Fixture identity — fixed, so teardown is exact and reruns are idempotent ──
#
# Synthetic CIKs, deliberately. Granting an STP policy against a REAL issuer's
# CIK would leave a real trust decision behind if teardown ever missed, and
# would make the fixture rows indistinguishable from corpus rows in the queue.
# 99999xxxxx matches nothing in EDGAR.
CIK_TRUSTED = "9999900001"      # gets an active policy
CIK_UNTRUSTED = "9999900002"    # never gets one
FIXTURE_CIKS = (CIK_TRUSTED, CIK_UNTRUSTED)

# One filing per routing case.
F_DISAGREE_TRUSTED = "9999999999-88-888881"   # PROOF 1 — must queue anyway
F_AGREE_TRUSTED = "9999999999-88-888882"      # PROOF 2 — must go stp
F_AGREE_UNTRUSTED = "9999999999-88-888883"    # PROOF 3 — must queue (safe default)
F_DISAGREE_UNTRUSTED = "9999999999-88-888884"  # comparator for the "same storage" check
FIXTURE_ACCESSIONS = (
    F_DISAGREE_TRUSTED, F_AGREE_TRUSTED, F_AGREE_UNTRUSTED, F_DISAGREE_UNTRUSTED,
)

FIXTURE_FILER = "VERIFY notetermsrouting fixture"
FIXTURE_SECURITY_PREFIX = "VERIFY notetermsrouting fixture note"
FIXTURE_CUSIP = "99999VER3"  # checksum-valid, deliberately not a real CUSIP

# Seeded staff identities for the HTTP checks.
ADMIN_USER_ID = "99000000-0000-0000-0000-000000000011"
ADMIN_SUB = "auth0|verify_routing_super_admin"
MEMBER_USER_ID = "99000000-0000-0000-0000-000000000012"
MEMBER_SUB = "auth0|verify_routing_member"
DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"

FIXTURE_TEXT = """VERIFY FIXTURE — notetermsrouting. Contingent Income Auto-Callable \
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


# ── Scripted model responses (lifted from the extraction sprint's fixture) ────

def _f(value, quote, absent=False):
    return {"value": value, "absent": absent, "quote": quote}


def primary_payload(security_name: str) -> dict:
    return {
        "issuer": "Morgan Stanley",
        "cusip": FIXTURE_CUSIP,
        "security_name": security_name,
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

# buffer -> floor (opposite payoff) and single -> worst_of (different risk).
# Both are arithmetically invisible; every numeric validator still passes.
HAZARD_DISAGREE = dict(HAZARD_AGREE)
HAZARD_DISAGREE["protection_type"] = {"value": "floor", "quote": "buffer of 30%"}
HAZARD_DISAGREE["basket_type"] = {"value": "worst_of", "quote": "the Common Stock"}

MOCK_SECONDARY_MODEL = "mock-independent-second-model"


def install_mock(hazard_payload: dict, security_name: str):
    """Patch the two model entry points. Returns a restore callable."""
    real_call = nte.call_claude_json
    real_last = nte._last_ensemble_model_used

    async def fake_call(system, user, max_tokens=400, **kwargs):
        task = kwargs.get("task_type")
        if task == "note_terms_hazard_ensemble":
            return dict(hazard_payload)
        if task == "note_terms_extraction":
            return json.loads(json.dumps(primary_payload(security_name)))
        return None

    async def fake_last(pool):
        # A genuinely different model id, so the independence guard is satisfied
        # and the ensemble counts as MEASURED — which is what makes 'high'
        # confidence, and therefore STP eligibility, reachable at all.
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
            f"SELECT id FROM {SECURITIES_TABLE} WHERE name LIKE $1",
            f"{FIXTURE_SECURITY_PREFIX}%",
        )
    ]

    if terms_ids:
        await conn.execute(
            f"DELETE FROM {CORRECTIONS_TABLE} WHERE target_type = 'note_terms' "
            "AND target_id = ANY($1::uuid[])",
            terms_ids,
        )
    if filing_ids:
        await conn.execute(
            f"DELETE FROM {TERMS_TABLE} WHERE reference_filing_id = ANY($1::uuid[])",
            filing_ids,
        )
    if security_ids:
        await conn.execute(
            f"DELETE FROM {CORRECTIONS_TABLE} WHERE target_type = 'note_terms' "
            f"AND target_id IN (SELECT id FROM {TERMS_TABLE} "
            "WHERE global_security_id = ANY($1::uuid[]))",
            security_ids,
        )
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
        await conn.execute(
            f"DELETE FROM {SECURITIES_TABLE} WHERE id = ANY($1::uuid[])", security_ids
        )
    await conn.execute(
        f"DELETE FROM {IDENTIFIERS_TABLE} WHERE id_value = $1", FIXTURE_CUSIP
    )
    await conn.execute(
        f"DELETE FROM {FILINGS_TABLE} WHERE accession_number = ANY($1::text[])",
        list(FIXTURE_ACCESSIONS),
    )
    # Policies last: they are parents of nothing, but leaving one behind would
    # silently change how a later run of THIS script routes.
    await conn.execute(
        f"DELETE FROM {POLICY_TABLE} WHERE cik = ANY($1::text[])", list(FIXTURE_CIKS)
    )
    await conn.execute(
        "DELETE FROM users WHERE auth0_sub = ANY($1::text[])", [ADMIN_SUB, MEMBER_SUB]
    )


async def seed_filing(conn, accession: str, cik: str) -> str:
    return str(await conn.fetchval(
        f"""
        INSERT INTO {FILINGS_TABLE}
            (cik, filer_name, form_type, accession_number, filing_date,
             primary_document, source_url, extracted_text, extraction_status)
        VALUES ($1, $2, '424B2', $3, DATE '2025-01-15', $4,
                'https://example.invalid/verify', $5, 'extracted')
        RETURNING id
        """,
        cik, FIXTURE_FILER, accession, f"{accession}.htm", FIXTURE_TEXT,
    ))


async def seed_users(conn) -> None:
    """A super_admin and a plain member, for the endpoint gate checks."""
    for user_id, sub, role, email in (
        (ADMIN_USER_ID, ADMIN_SUB, "super_admin", "verify_routing_admin@test.local"),
        (MEMBER_USER_ID, MEMBER_SUB, "member", "verify_routing_member@test.local"),
    ):
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify Routing', $4, $5)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, DEFAULT_ORG_ID, email, sub, role,
        )


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


# ── 1. Schema + RLS shape ─────────────────────────────────────────────────────


async def check_schema(conn) -> None:
    exists = await conn.fetchval(f"SELECT to_regclass('{POLICY_TABLE}')")
    rls = await conn.fetchval(
        f"SELECT relrowsecurity FROM pg_class WHERE oid = '{POLICY_TABLE}'::regclass"
    ) if exists else False
    policies = await conn.fetch(
        f"SELECT polname, polcmd FROM pg_policy "
        f"WHERE polrelid = '{POLICY_TABLE}'::regclass ORDER BY polname"
    ) if exists else []
    cmds = sorted(
        (p["polcmd"].decode() if isinstance(p["polcmd"], bytes) else p["polcmd"])
        for p in policies
    )
    has_org = await conn.fetchval(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema = 'portfolio' AND table_name = 'note_terms_stp_policy' "
        "AND column_name = 'org_id'"
    )
    # r=SELECT a=INSERT w=UPDATE d=DELETE. Four SEPARATE policies, never one
    # FOR ALL ('*'), matching every other table in this schema.
    check(
        "note_terms_stp_policy exists, RLS enabled, 4 separate policies, no org_id",
        bool(exists) and bool(rls) and cmds == ["a", "d", "r", "w"] and has_org == 0,
        f"exists={bool(exists)} rls={bool(rls)} policy_cmds={cmds} org_id_columns={has_org}",
    )

    cols = {
        r["column_name"]: r["data_type"] for r in await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'portfolio' "
            "AND table_name = 'securities_global_note_terms' "
            "AND column_name IN ('routing_decision', 'routed_at')"
        )
    }
    check(
        "securities_global_note_terms carries routing_decision + routed_at",
        cols.get("routing_decision") == "text"
        and cols.get("routed_at") == "timestamp with time zone",
        f"{cols}",
    )


async def check_unique_constraint(conn) -> None:
    """A revoked policy and a NEW active one coexist; two ACTIVE ones do not."""
    await conn.execute(
        f"DELETE FROM {POLICY_TABLE} WHERE cik = $1", CIK_TRUSTED
    )
    # A revoked historical grant.
    await conn.execute(
        f"""
        INSERT INTO {POLICY_TABLE}
            (cik, form_type, enabled, granted_by, revoked_by, revoked_at, notes)
        VALUES ($1, '424B2', false, 'verify', 'verify', now(), 'revoked history')
        """,
        CIK_TRUSTED,
    )
    coexist_ok = True
    coexist_detail = ""
    try:
        await conn.execute(
            f"INSERT INTO {POLICY_TABLE} (cik, form_type, granted_by, notes) "
            "VALUES ($1, '424B2', 'verify', 'active alongside history')",
            CIK_TRUSTED,
        )
    except Exception as exc:  # noqa: BLE001
        coexist_ok = False
        coexist_detail = f"{type(exc).__name__}: {exc}"

    second_active_rejected = False
    second_detail = ""
    try:
        await conn.execute(
            f"INSERT INTO {POLICY_TABLE} (cik, form_type, granted_by, notes) "
            "VALUES ($1, '424B2', 'verify', 'second active — must fail')",
            CIK_TRUSTED,
        )
        second_detail = "a SECOND active policy was accepted"
    except asyncpg.UniqueViolationError as exc:
        second_active_rejected = True
        second_detail = type(exc).__name__

    counts = await conn.fetchrow(
        f"SELECT count(*) AS total, count(*) FILTER (WHERE enabled) AS active "
        f"FROM {POLICY_TABLE} WHERE cik = $1 AND form_type = '424B2'",
        CIK_TRUSTED,
    )
    check(
        "UNIQUE is partial: a revoked policy + one new active policy coexist, "
        "a second ACTIVE one is rejected",
        coexist_ok and second_active_rejected
        and counts["total"] == 2 and counts["active"] == 1,
        f"revoked+active coexist={coexist_ok}{(' — ' + coexist_detail) if coexist_detail else ''}; "
        f"second active rejected={second_active_rejected} ({second_detail}); "
        f"rows for pairing: total={counts['total']} active={counts['active']}",
    )

    # A revoked row must carry its stamps — otherwise 'enabled=false' is an
    # assertion with no evidence behind it.
    stamp_rejected = False
    try:
        await conn.execute(
            f"INSERT INTO {POLICY_TABLE} (cik, form_type, enabled, granted_by) "
            "VALUES ($1, 'FWP', false, 'verify')",
            CIK_TRUSTED,
        )
    except asyncpg.CheckViolationError:
        stamp_rejected = True
    check(
        "a disabled policy with no revoked_at is rejected (the audit trail cannot be empty)",
        stamp_rejected,
    )

    # Leave the pairing with exactly ONE active policy for the routing proofs.
    await conn.execute(
        f"DELETE FROM {POLICY_TABLE} WHERE cik = $1 AND enabled = false", CIK_TRUSTED
    )


# ── 2. The three routing proofs ───────────────────────────────────────────────


async def run_extraction(pool, conn, accession: str, cik: str, hazard: dict) -> dict:
    """Seed a filing, run the real pipeline with scripted readers, read the row."""
    filing_id = await seed_filing(conn, accession, cik)
    restore = install_mock(hazard, f"{FIXTURE_SECURITY_PREFIX} {accession}")
    try:
        result = await nte.extract_terms(filing_id, pool)
    finally:
        restore()
    if not result.ok:
        return {"result": result, "row": None, "filing_id": filing_id}
    row = await conn.fetchrow(
        f"SELECT * FROM {TERMS_TABLE} WHERE id = $1::uuid", result.note_terms_id
    )
    return {"result": result, "row": dict(row) if row else None, "filing_id": filing_id}


async def check_routing(pool, conn) -> dict:
    """The three proofs, plus the 'STP does not skip computation' comparison."""
    # The trusted pairing has exactly one active policy at this point.
    policy = await active_policy(conn, CIK_TRUSTED, "424B2")
    check(
        "an ACTIVE policy exists for the trusted fixture pairing before routing",
        policy is not None,
        f"cik={CIK_TRUSTED} form=424B2 policy={'present' if policy else 'ABSENT'}",
    )

    cases = {
        "disagree_trusted": await run_extraction(
            pool, conn, F_DISAGREE_TRUSTED, CIK_TRUSTED, HAZARD_DISAGREE),
        "agree_trusted": await run_extraction(
            pool, conn, F_AGREE_TRUSTED, CIK_TRUSTED, HAZARD_AGREE),
        "agree_untrusted": await run_extraction(
            pool, conn, F_AGREE_UNTRUSTED, CIK_UNTRUSTED, HAZARD_AGREE),
        "disagree_untrusted": await run_extraction(
            pool, conn, F_DISAGREE_UNTRUSTED, CIK_UNTRUSTED, HAZARD_DISAGREE),
    }

    for name, case in cases.items():
        if case["row"] is None:
            check(f"fixture {name} extracted", False,
                  f"pipeline failed: {case['result'].error}")
    if any(c["row"] is None for c in cases.values()):
        return cases

    dt = cases["disagree_trusted"]
    at = cases["agree_trusted"]
    au = cases["agree_untrusted"]
    du = cases["disagree_untrusted"]

    # ── PROOF 1 — the non-negotiable one.
    check(
        "ROUTING RULE PROOF 1: a hazard disagreement routes to 'queued' EVEN WITH "
        "an active STP policy for its (cik, form_type)",
        dt["row"]["routing_decision"] == "queued"
        and bool(dt["result"].hazard_disagreements),
        f"routing_decision={dt['row']['routing_decision']!r} "
        f"disagreed_on={sorted(dt['result'].hazard_disagreements)} "
        f"policy_active=True confidence={dt['row']['extraction_confidence']!r}",
    )

    # ── PROOF 2 — agreement under an active policy.
    check(
        "ROUTING RULE PROOF 2: agreement + an active STP policy routes to 'stp'",
        at["row"]["routing_decision"] == "stp"
        and not at["result"].hazard_disagreements,
        f"routing_decision={at['row']['routing_decision']!r} "
        f"disagreements={at['result'].hazard_disagreements} "
        f"confidence={at['row']['extraction_confidence']!r}",
    )

    # ── PROOF 3 — the safe default.
    check(
        "ROUTING RULE PROOF 3: agreement with NO policy routes to 'queued' (safe default)",
        au["row"]["routing_decision"] == "queued"
        and not au["result"].hazard_disagreements
        and await active_policy(conn, CIK_UNTRUSTED, "424B2") is None,
        f"routing_decision={au['row']['routing_decision']!r} "
        f"confidence={au['row']['extraction_confidence']!r} policy=None",
    )

    check("routed_at is stamped on every routed row",
          all(c["row"]["routed_at"] is not None for c in cases.values()))

    # ── STP does not skip computation ────────────────────────────────────────
    # Two comparisons, because "identically" has two halves.
    #
    # (a) AGREEING rows: the STP'd row and the queued row must show the same
    #     ensemble outcome — same six fields compared, ensemble measured, same
    #     confidence, same field_status, same source offsets. Only
    #     routing_decision differs.
    same_agreeing = (
        sorted(at["result"].hazard_compared) == sorted(au["result"].hazard_compared)
        and len(at["result"].hazard_compared) == 6
        and at["result"].ensemble_measured is True
        and au["result"].ensemble_measured is True
        and at["row"]["extraction_confidence"] == au["row"]["extraction_confidence"] == "high"
        and at["row"]["field_status"] == au["row"]["field_status"]
        and at["row"]["source_char_start"] == au["row"]["source_char_start"]
        and at["row"]["source_char_end"] == au["row"]["source_char_end"]
        and at["row"]["routing_decision"] != au["row"]["routing_decision"]
    )
    check(
        "an STP'd row stores the SAME ensemble result as a queued row — only "
        "routing_decision differs",
        same_agreeing,
        f"stp: compared={len(at['result'].hazard_compared)} "
        f"measured={at['result'].ensemble_measured} "
        f"conf={at['row']['extraction_confidence']!r} route='stp' | "
        f"queued: compared={len(au['result'].hazard_compared)} "
        f"measured={au['result'].ensemble_measured} "
        f"conf={au['row']['extraction_confidence']!r} route='queued'",
    )

    # (b) DISAGREEING rows under a policy vs without one: the disagreement
    #     records must be written identically. This is the sharpest form of the
    #     assertion — a policy must not suppress the ensemble's OUTPUT either.
    trusted_records = await _disagreement_records(conn, dt["row"]["id"])
    untrusted_records = await _disagreement_records(conn, du["row"]["id"])
    check(
        "a disagreement under an ACTIVE policy is recorded exactly as one with no "
        "policy — STP suppresses no computation and no storage",
        trusted_records == untrusted_records and len(trusted_records) == 2,
        f"under policy={sorted(trusted_records)} | no policy={sorted(untrusted_records)}",
    )

    return cases


async def _disagreement_records(conn, note_terms_id) -> dict:
    """The ensemble's recorded disagreements for one row, as {field: (a, b)}."""
    rows = await conn.fetch(
        f"SELECT field_name, original_value, corrected_value, notes, corrected_by "
        f"FROM {CORRECTIONS_TABLE} WHERE target_type = 'note_terms' AND target_id = $1::uuid",
        note_terms_id,
    )
    out = {}
    for r in rows:
        if envelope_source(r["notes"]) != SOURCE_HAZARD_ENSEMBLE:
            continue
        # corrected_by must be NULL — a machine observation is not a person's
        # correction, and conflating them would poison the review screen.
        out[r["field_name"]] = (
            r["original_value"], r["corrected_value"], r["corrected_by"] is None
        )
    return out


# ── 3. The 54 pre-existing rows ───────────────────────────────────────────────


async def check_pre_existing(conn) -> None:
    """Every row that predates this sprint still has routing_decision IS NULL.

    Asserted on the rows themselves, not on "the migration ran". A migration
    that ran and then a stray UPDATE that backfilled would pass the weaker
    check and fail this one.
    """
    row = await conn.fetchrow(
        f"""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE t.routing_decision IS NULL) AS unrouted,
               count(*) FILTER (WHERE t.routing_decision IS NOT NULL) AS routed
        FROM {TERMS_TABLE} t
        JOIN {FILINGS_TABLE} f ON f.id = t.reference_filing_id
        WHERE f.accession_number <> ALL($1::text[])
        """,
        list(FIXTURE_ACCESSIONS),
    )
    check(
        "the pre-existing note-terms rows are ALL routing_decision IS NULL, "
        "unchanged by this sprint",
        row["total"] == 54 and row["routed"] == 0,
        f"pre-existing rows={row['total']} (expected 54), "
        f"routing_decision NULL={row['unrouted']}, non-NULL={row['routed']}",
    )


# ── 4. Policy writes are Super-Admin-gated, in the app AND in the database ────


async def check_policy_gate(app_pool, app_conn) -> None:
    app_rejected = False
    app_detail = ""
    try:
        await grant_stp(
            app_pool, cik=CIK_UNTRUSTED, form_type="424B2",
            granted_by="verify", notes="must not be granted",
            is_super_admin=False,
        )
        app_detail = "grant_stp RETURNED without Super Admin"
    except NoteTermsRoutingPermissionError as exc:
        app_rejected = True
        app_detail = f"{type(exc).__name__}"

    revoke_rejected = False
    try:
        await revoke_stp(
            app_pool, cik=CIK_TRUSTED, form_type="424B2",
            revoked_by="verify", is_super_admin=False,
        )
    except NoteTermsRoutingPermissionError:
        revoke_rejected = True

    check(
        "grant_stp and revoke_stp are REJECTED without is_super_admin",
        app_rejected and revoke_rejected,
        f"grant: {app_detail}; revoke rejected={revoke_rejected}",
    )

    # And the database says no too, independently of the Python guard: a raw
    # INSERT under app_service with no super-admin GUC must hit RLS.
    rls_rejected = False
    rls_detail = ""
    try:
        await app_conn.execute("SELECT set_config('app.is_super_admin', 'false', false)")
        await app_conn.execute(
            f"INSERT INTO {POLICY_TABLE} (cik, form_type, granted_by) "
            "VALUES ($1, 'FWP', 'verify')",
            CIK_UNTRUSTED,
        )
        rls_detail = "the INSERT succeeded under app_service with no super-admin GUC"
    except asyncpg.InsufficientPrivilegeError as exc:
        rls_rejected = True
        rls_detail = type(exc).__name__
    except Exception as exc:  # noqa: BLE001
        rls_detail = f"{type(exc).__name__}: {exc}"
    check(
        "RLS independently rejects a policy INSERT under app_service with no "
        "super-admin context",
        rls_rejected,
        rls_detail,
    )

    still_absent = await app_conn.fetchval(
        f"SELECT count(*) FROM {POLICY_TABLE} WHERE cik = $1", CIK_UNTRUSTED
    )
    check("no policy was created for the untrusted pairing by either attempt",
          still_absent == 0, f"rows={still_absent}")


async def check_global_read(app_conn) -> None:
    """Global read works under app_service with NO org context set at all."""
    await app_conn.execute("SELECT set_config('app.current_org_id', '', false)")
    await app_conn.execute("SELECT set_config('app.is_super_admin', 'false', false)")
    visible = await app_conn.fetchval(
        f"SELECT count(*) FROM {POLICY_TABLE} WHERE cik = $1", CIK_TRUSTED
    )
    org_ctx = await app_conn.fetchval("SELECT current_setting('app.current_org_id', true)")
    check(
        "global read on note_terms_stp_policy works under app_service with no org context",
        visible == 1,
        f"rows visible={visible} with app.current_org_id={org_ctx!r}",
    )


# ── 5. The endpoints ──────────────────────────────────────────────────────────


async def check_endpoints(conn, cases: dict) -> None:
    """Drive the real HTTP surface: the queue, its gate, and resolve."""
    try:
        import main
        from starlette.testclient import TestClient
    except Exception as exc:  # noqa: BLE001
        check("queue endpoint returns exactly the queue-definition rows", False,
              f"could not import app/TestClient: {type(exc).__name__}: {exc}")
        return

    subs = {"admin": ADMIN_SUB, "member": MEMBER_SUB}
    current = {"who": "admin"}
    main.verify_token = lambda _t: {
        "sub": subs[current["who"]],
        "email": "verify_routing@test.local",
        "org_id": DEFAULT_ORG_ID,
    }
    hdr = {"Authorization": "Bearer stub"}

    # The exact set the queue is DEFINED to return, computed independently in
    # SQL. Comparing the endpoint against a re-derivation of the definition —
    # not against a hand-listed set of fixtures — is what makes "exactly" mean
    # something for the real 54 rows as well as the 4 fixtures.
    expected_ids = {
        str(r["id"]) for r in await conn.fetch(
            f"""
            SELECT t.id FROM {TERMS_TABLE} t
            JOIN {FILINGS_TABLE} f ON f.id = t.reference_filing_id
            WHERE t.valid_to IS NULL AND t.system_to IS NULL
              AND (t.extraction_confidence = 'needs_review'
                   OR t.routing_decision = 'queued')
            """
        )
    }

    def drive_queue():
        with TestClient(main.app, raise_server_exceptions=False) as c:
            return c.get("/api/v1/admin/pricing/note-terms/queue", headers=hdr)

    resp = await asyncio.to_thread(drive_queue)
    body = resp.json() if resp.status_code == 200 else {}
    returned_ids = {item["id"] for item in body.get("queue", [])}

    dt_id = str(cases["disagree_trusted"]["row"]["id"])
    at_id = str(cases["agree_trusted"]["row"]["id"])
    au_id = str(cases["agree_untrusted"]["row"]["id"])
    du_id = str(cases["disagree_untrusted"]["row"]["id"])

    check(
        "queue endpoint returns EXACTLY the rows matching the query definition",
        resp.status_code == 200 and returned_ids == expected_ids,
        f"HTTP {resp.status_code}; returned={len(returned_ids)} expected={len(expected_ids)}; "
        f"missing={len(expected_ids - returned_ids)} extra={len(returned_ids - expected_ids)}",
    )
    check(
        "the known fixture mix lands correctly: queued/needs_review rows IN, the "
        "STP'd row OUT",
        dt_id in returned_ids and au_id in returned_ids and du_id in returned_ids
        and at_id not in returned_ids,
        f"disagree+policy in={dt_id in returned_ids} agree-no-policy in={au_id in returned_ids} "
        f"disagree-no-policy in={du_id in returned_ids} stp'd excluded={at_id not in returned_ids}",
    )

    queued_item = next((i for i in body.get("queue", []) if i["id"] == dt_id), None)
    check(
        "a queued row carries its disagreed fields and the SOURCE SENTENCE, not a "
        "page reference",
        queued_item is not None
        and sorted(queued_item["disagreed_fields"]) == ["basket_type", "protection_type"]
        and bool(queued_item["source_excerpt"])
        and queued_item["source_excerpt"] in FIXTURE_TEXT,
        "" if queued_item is None else
        f"fields={sorted(queued_item['disagreed_fields'])} "
        f"excerpt_chars={len(queued_item['source_excerpt'] or '')} "
        f"of {queued_item['extracted_text_length']}",
    )

    # The gate.
    current["who"] = "member"
    resp_member = await asyncio.to_thread(drive_queue)
    current["who"] = "admin"
    check(
        "the queue endpoint is Super Admin only — a member gets 403",
        resp_member.status_code == 403,
        f"HTTP {resp_member.status_code}",
    )

    # ── Resolve ──────────────────────────────────────────────────────────────
    before = await conn.fetchrow(
        f"SELECT protection_type, field_status FROM {TERMS_TABLE} WHERE id = $1::uuid",
        dt_id,
    )

    def drive_resolve():
        with TestClient(main.app, raise_server_exceptions=False) as c:
            return c.post(
                f"/api/v1/admin/pricing/note-terms/{dt_id}/resolve",
                headers=hdr,
                json={"field": "protection_type", "chosen_value": "floor",
                      "source": "secondary"},
            )

    resolve_resp = await asyncio.to_thread(drive_resolve)
    resolve_body = resolve_resp.json() if resolve_resp.status_code == 200 else {}

    after = await conn.fetchrow(
        f"SELECT protection_type, field_status FROM {TERMS_TABLE} WHERE id = $1::uuid",
        dt_id,
    )
    after_status = after["field_status"]
    if isinstance(after_status, str):
        after_status = json.loads(after_status)

    human_rows = await conn.fetch(
        f"""
        SELECT field_name, original_value, corrected_value, notes, corrected_by,
               target_type, org_id, document_id
        FROM {CORRECTIONS_TABLE}
        WHERE target_type = 'note_terms' AND target_id = $1::uuid
        """,
        dt_id,
    )
    human = [r for r in human_rows if envelope_source(r["notes"]) == SOURCE_HUMAN]

    check(
        "resolve logs through log_note_terms_correction with target_type='note_terms' "
        "(org_id and document_id NULL, corrected_by = the reviewer)",
        resolve_resp.status_code == 200
        and len(human) == 1
        and human[0]["field_name"] == "protection_type"
        and human[0]["target_type"] == "note_terms"
        and human[0]["org_id"] is None
        and human[0]["document_id"] is None
        and str(human[0]["corrected_by"]) == ADMIN_USER_ID,
        f"HTTP {resolve_resp.status_code}; human corrections={len(human)}"
        + (f"; corrected_by={human[0]['corrected_by']}" if human else ""),
    )
    check(
        "resolve writes the chosen value and sets field_status[field] = 'extracted'",
        after["protection_type"] == "floor"
        and after_status.get("protection_type") == "extracted"
        and before["protection_type"] == "buffer",
        f"protection_type {before['protection_type']!r} -> {after['protection_type']!r}; "
        f"field_status.protection_type={after_status.get('protection_type')!r}",
    )
    check(
        "resolve does NOT rewrite routing_decision or extraction_confidence — the row "
        "was queued, and that stays true about the moment it was extracted",
        (await conn.fetchval(
            f"SELECT routing_decision FROM {TERMS_TABLE} WHERE id = $1::uuid", dt_id
        )) == "queued"
        and (await conn.fetchval(
            f"SELECT extraction_confidence FROM {TERMS_TABLE} WHERE id = $1::uuid", dt_id
        )) == "needs_review",
    )
    # One field of two is settled, so the pairing is NOT clear and the grant
    # offer must NOT appear. This is the check that stops the screen proposing
    # trust halfway through a review.
    check(
        "the STP grant is NOT offered while the pairing still has unresolved fields",
        resolve_body.get("pairing_cleared") is False
        and resolve_body.get("pairing_outstanding_rows", 0) >= 1,
        f"pairing_cleared={resolve_body.get('pairing_cleared')} "
        f"outstanding={resolve_body.get('pairing_outstanding_rows')}",
    )

    bad = await asyncio.to_thread(lambda: _post_bad_field(main, TestClient, hdr, dt_id))
    check(
        "resolve rejects a field name that is not a correctable term column",
        bad.status_code == 400,
        f"HTTP {bad.status_code}",
    )


def _post_bad_field(main, TestClient, hdr, note_terms_id):
    with TestClient(main.app, raise_server_exceptions=False) as c:
        return c.post(
            f"/api/v1/admin/pricing/note-terms/{note_terms_id}/resolve",
            headers=hdr,
            json={"field": "id", "chosen_value": "x", "source": "manual"},
        )


# ── Main ──────────────────────────────────────────────────────────────────────


async def main_async() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("[FAIL] DATABASE_URL is unset")
        return 1

    try:
        app_conn, who = await app_service_connection()
    except Exception as exc:  # noqa: BLE001
        check("APP_SERVICE_DATABASE_URL connects as a non-bypass role", False, str(exc))
        print("\nRESULT: FAIL (1 check, 0 passed, 1 failed)")
        return 1
    check("APP_SERVICE_DATABASE_URL connects as a non-bypass role", True,
          f"current_user={who}")

    app_pool = await asyncpg.create_pool(
        os.environ["APP_SERVICE_DATABASE_URL"], statement_cache_size=0,
        min_size=1, max_size=2,
    )
    pool = await asyncpg.create_pool(
        os.environ["DATABASE_URL"], statement_cache_size=0, min_size=1, max_size=4
    )
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], statement_cache_size=0)
    try:
        await teardown(conn)  # START
        await seed_users(conn)

        await check_schema(conn)
        await check_unique_constraint(conn)
        cases = await check_routing(pool, conn)
        await check_pre_existing(conn)
        await check_policy_gate(app_pool, app_conn)
        await check_global_read(app_conn)
        if all(c["row"] is not None for c in cases.values()):
            await check_endpoints(conn, cases)
        else:
            check("endpoint checks ran", False, "routing fixtures did not extract")
    finally:
        try:
            await teardown(conn)  # END
        finally:
            await conn.close()
            await pool.close()
            await app_pool.close()
            await app_conn.close()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\nRESULT: {'PASS' if failed == 0 else 'FAIL'} "
          f"({len(results)} checks, {passed} passed, {failed} failed)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
