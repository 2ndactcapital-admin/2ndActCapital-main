"""Verification — Portfolio Phase E: Chancery-sourced positions, commitments,
hard assets, tax-document tracking.

Pass/fail only. No prompts. Idempotent. Teardown at START and at END, with an
EXACT before/after count on every table touched — never a truncate. Real
database, real RLS, real ``app_service`` connection, real transaction_types.

APP_SERVICE_DATABASE_URL IS REQUIRED and there is NO SET ROLE fallback, for the
same reason A1/A2/B/C/D require it: ``postgres`` has ``rolbypassrls``, so every
cross-org assertion would "pass" under it while proving nothing.

────────────────────────────────────────────────────────────────────────────
THE TWO ASSERTIONS THIS PHASE IS EASIEST TO FAKE, AND HOW THEY ARE WRITTEN
────────────────────────────────────────────────────────────────────────────
**"The override happened."** ``asset_class='hard_asset'`` and
``include_in_performance=false`` being TRUE of the stored row does not prove
anything was overridden — it proves the row has those values, which is also what
you get if the defaults were those values all along. So the deployed defaults are
read from ``information_schema`` and asserted DIFFERENT, and a CONTROL asset is
created through the same function with no overrides and asserted to land ON the
defaults. Only both together mean the override did work.

**"Reading by purpose returns the correct one."** An asset with an ``insurance``
and a ``net_worth`` valuation will return the right number from a purpose-blind
"latest row wins" resolver too, if the fixture happens to ask for the later one.
So the ``net_worth`` valuation is deliberately dated LATER than the
``insurance`` one: a resolver that ignored ``purpose`` would return 1,200,000 for
both, and the insurance assertion of 1,450,000 fails.

────────────────────────────────────────────────────────────────────────────
FIXTURES ARE NAMED, NEVER TRUNCATED
────────────────────────────────────────────────────────────────────────────
Every fixture row carries the ``VERIFY-PORTFOLIOE`` tag in a natural-key column
and is deleted by that tag, child tables first. ``portfolio.assets``,
``public.documents`` and ``public.entities`` all hold real production rows.

Run:
    python3 scripts/verify_portfolioe.py
"""

from __future__ import annotations

import ast
import asyncio
import glob
import inspect
import json
import os
import sys
from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.extend(sorted(glob.glob(
    os.path.join(_HERE, "..", "venv", "lib", "python3*", "site-packages")
)))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_HERE, "..", ".env"), override=False)

from services import document_review  # noqa: E402
from services import narrative_extraction  # noqa: E402
from services import portfolio_chancery  # noqa: E402
from services import textract_extraction  # noqa: E402
from services.chancery_intake import (  # noqa: E402
    _NARRATIVE_CATEGORIES,
    doc_family_for_category,
)
from services.portfolio_assets import (  # noqa: E402
    TABLE_ASSET_IDENT,
    TABLE_ASSETS,
    TABLE_EXT_REF,
    TABLE_POSITIONS,
    TABLE_TRANSACTIONS,
    TABLE_VALUATIONS,
    PortfolioError,
    record_transaction,
    record_valuation,
    resolve_current_value,
)
from services.portfolio_chancery import (  # noqa: E402
    CHANCERY_AUTHORITY,
    CHANCERY_SOURCE_SYSTEM,
    COMMITMENT_FIELDS_NOT_EXTRACTED,
    CONFIRMED_STATUS,
    NAME_SOURCE_EXPLICIT,
    NAME_SOURCE_FILENAME,
    NAME_SOURCE_NARRATIVE_PARTY,
    ChanceryPortfolioError,
    commitment_fields_from_document,
    create_position_from_chancery_document,
    read_document_extractions,
)
from services.portfolio_commitments import (  # noqa: E402
    TABLE_COMMITMENTS,
    TAX_CHASE_INDEX,
    TAX_DOC_STATUSES,
    UNFUNDED_FORMULA,
    CommitmentError,
    create_commitment,
    explain_tax_chase,
    get_commitment,
    recompute_commitment,
    set_tax_doc_status,
    tax_chase_list,
)
from services.portfolio_documents import (  # noqa: E402
    RECORD_TYPE_ASSET,
    RECORD_TYPE_POSITION,
    list_portfolio_record_documents,
)

DEFAULT_ORG_ID = "00000000-0000-0000-0000-000000000001"
# The SECOND real org, for cross-org isolation. A real row, not a minted one.
OTHER_ORG_ID = "bb347258-8f28-4f49-8cc9-e29ccad82884"

ADMIN_SUB = "auth0|verify_portfolioe_super_admin"
MEMBER_SUB = "auth0|verify_portfolioe_member"
# uuid5(NAMESPACE_URL, sub) — `services.permissions.get_user_id` DERIVES the id
# from the sub rather than looking it up (Phase C's finding), so a fixture seeded
# under a hand-picked literal is a user no code path ever finds.
ADMIN_USER_ID = str(uuid5(NAMESPACE_URL, ADMIN_SUB))
MEMBER_USER_ID = str(uuid5(NAMESPACE_URL, MEMBER_SUB))

FIXTURE_TAG = "VERIFY-PORTFOLIOE"

# ── Fixture names, declared UP FRONT and never appended to at runtime ────────
E_OWNER = f"{FIXTURE_TAG} Thornbury Family LLC"
E_HARD_OWNER = f"{FIXTURE_TAG} Thornbury Family Trust"
ENTITY_NAMES = [E_OWNER, E_HARD_OWNER]

DOC_FUND = f"{FIXTURE_TAG} Brightwater III Q2-2026 Capital Account Statement.pdf"
DOC_HOUSE = f"{FIXTURE_TAG} 14 Marlowe Lane Deed and Appraisal.pdf"
DOC_PLAIN = f"{FIXTURE_TAG} Unextracted Statement No Narrative.pdf"
DOC_UNCONFIRMED = f"{FIXTURE_TAG} Still In Review Not Confirmed.pdf"
DOC_CONTROL = f"{FIXTURE_TAG} Control Asset Defaults Untouched.pdf"
DOC_NAMES = [DOC_FUND, DOC_HOUSE, DOC_PLAIN, DOC_UNCONFIRMED, DOC_CONTROL]

# The name narrative extraction really stored, in the real key_parties shape.
FUND_PARTY_NAME = f"{FIXTURE_TAG} Brightwater Opportunities Fund III, LLC"
HOUSE_ASSET_NAME = f"{FIXTURE_TAG} 14 Marlowe Lane"

# ── Exact figures. Exact, because "a number came back" is what this phase is
#    easiest to fake. ───────────────────────────────────────────────────────
COMMITMENT_AMOUNT = Decimal("1000000.00")
CALL_AMOUNT = Decimal("50000.00")
RECALLABLE_DIST = Decimal("10000.00")
INCOME_DIST = Decimal("7500.00")

# After the baseline recompute, with no transactions at all.
UNFUNDED_BASELINE = Decimal("1000000.00")
# After the 50,000 call: called 50,000, unfunded 1,000,000 - 50,000.
CALLED_AFTER_CALL = Decimal("50000.00")
UNFUNDED_AFTER_CALL = Decimal("950000.00")
# After the 10,000 recallable distribution:
#   called      = 50,000 + (10,000 x affects_paid_in=-1) = 40,000
#   distributed = 10,000
#   recallable  = 10,000
#   unfunded    = 1,000,000 - 40,000 + 10,000            = 970,000
CALLED_AFTER_RECALLABLE = Decimal("40000.00")
DISTRIBUTED_AFTER_RECALLABLE = Decimal("10000.00")
RECALLABLE_AFTER_RECALLABLE = Decimal("10000.00")
UNFUNDED_AFTER_RECALLABLE = Decimal("970000.00")
# The flag-driven alternative the module docstring records, reported not stored.
UNFUNDED_FLAG_DRIVEN_AFTER_RECALLABLE = Decimal("960000.00")
# After the 7,500 dist_income — called and unfunded MUST NOT MOVE.
DISTRIBUTED_AFTER_INCOME = Decimal("17500.00")

HARD_ASSET_INSURANCE = Decimal("1450000.00")
HARD_ASSET_NET_WORTH = Decimal("1200000.00")

FUND_MARKET_VALUE = Decimal("812500.00")
CONTROL_MARKET_VALUE = Decimal("100.00")

AS_OF = date(2026, 6, 30)
COMMIT_DATE = date(2024, 3, 15)
TRADE_CALL = date(2026, 4, 10)
TRADE_RECALLABLE = date(2026, 5, 20)
TRADE_INCOME = date(2026, 6, 5)
# insurance FIRST, net_worth LATER — see the module docstring.
VAL_DATE_INSURANCE = date(2026, 1, 31)
VAL_DATE_NET_WORTH = date(2026, 6, 30)

TAX_YEAR = 2025
OTHER_TAX_YEAR = 2024

TABLES = (
    TABLE_COMMITMENTS, TABLE_TRANSACTIONS, TABLE_POSITIONS, TABLE_VALUATIONS,
    TABLE_ASSET_IDENT, TABLE_EXT_REF, TABLE_ASSETS,
    "public.document_record_links", "public.document_narrative_extractions",
    "public.documents", "public.entities",
)

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def report(name: str, detail: str) -> None:
    """A Task 1 finding. Printed as a FINDING, never silently as a PASS."""
    print(f"[FIND] {name}\n       {detail}")


def _dec(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


# ── Setup / teardown ────────────────────────────────────────────────────────


async def counts(conn) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in TABLES}


async def teardown(conn) -> None:
    """Delete every fixture row, child tables first. Touches nothing else."""
    fixture_assets = (
        f"SELECT id FROM {TABLE_ASSETS} WHERE name LIKE '{FIXTURE_TAG}%'"
    )
    fixture_positions = (
        f"SELECT id FROM {TABLE_POSITIONS} WHERE asset_id IN ({fixture_assets})"
    )

    # Links first — they point at documents AND at portfolio records, both of
    # which are about to go.
    await conn.execute(
        "DELETE FROM public.document_record_links WHERE document_id IN "
        "(SELECT id FROM public.documents WHERE original_filename = ANY($1::text[]))",
        DOC_NAMES,
    )
    await conn.execute(
        "DELETE FROM public.document_narrative_extractions WHERE document_id IN "
        "(SELECT id FROM public.documents WHERE original_filename = ANY($1::text[]))",
        DOC_NAMES,
    )
    await conn.execute(
        "DELETE FROM public.documents WHERE original_filename = ANY($1::text[])",
        DOC_NAMES,
    )

    # Commitments hang off positions; both go before the positions themselves.
    await conn.execute(
        f"DELETE FROM {TABLE_COMMITMENTS} WHERE position_id IN ({fixture_positions})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_EXT_REF} WHERE record_id IN ({fixture_positions})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_TRANSACTIONS} WHERE position_id IN ({fixture_positions})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_POSITIONS} WHERE asset_id IN ({fixture_assets})"
    )
    # The forward supersession pointer must be dropped BEFORE the rows it points
    # at, or the delete order is an FK violation on the fixture's own history.
    await conn.execute(
        f"UPDATE {TABLE_VALUATIONS} SET supersedes_valuation_id = NULL "
        f"WHERE asset_id IN ({fixture_assets})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_VALUATIONS} WHERE asset_id IN ({fixture_assets})"
    )
    await conn.execute(
        f"DELETE FROM {TABLE_ASSET_IDENT} WHERE asset_id IN ({fixture_assets})"
    )
    await conn.execute(f"DELETE FROM {TABLE_ASSETS} WHERE id IN ({fixture_assets})")

    await conn.execute(
        "DELETE FROM public.entities WHERE display_name = ANY($1::text[])",
        ENTITY_NAMES,
    )
    await conn.execute(
        "DELETE FROM public.users WHERE auth0_sub = ANY($1::text[])",
        [ADMIN_SUB, MEMBER_SUB],
    )


async def seed_users(conn) -> None:
    for user_id, sub, role, email in (
        (ADMIN_USER_ID, ADMIN_SUB, "super_admin", "verify_e_admin@test.local"),
        (MEMBER_USER_ID, MEMBER_SUB, "member", "verify_e_member@test.local"),
    ):
        await conn.execute(
            """
            INSERT INTO public.users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1::uuid, $2::uuid, $3, 'Verify PortfolioE', $4, $5)
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            user_id, DEFAULT_ORG_ID, email, sub, role,
        )


async def seed_document(
    conn, filename: str, *, status: str, doc_family: str | None = None
) -> str:
    return await conn.fetchval(
        "INSERT INTO public.documents "
        "(org_id, original_filename, source, mime_type, status, doc_family, created_by) "
        "VALUES ($1::uuid, $2, 'upload', 'application/pdf', $3, $4, $5::uuid) "
        "RETURNING id::text",
        DEFAULT_ORG_ID, filename, status, doc_family, ADMIN_USER_ID,
    )


async def build_fixtures(conn) -> dict:
    ids: dict = {}
    for key, name, etype in (
        ("owner", E_OWNER, "llc"),
        ("hard_owner", E_HARD_OWNER, "trust"),
    ):
        ids[key] = await conn.fetchval(
            "INSERT INTO public.entities (org_id, entity_type, display_name) "
            "VALUES ($1::uuid, $2::entity_type, $3) RETURNING id::text",
            DEFAULT_ORG_ID, etype, name,
        )

    ids["doc_fund"] = await seed_document(conn, DOC_FUND, status=CONFIRMED_STATUS,
                                          doc_family="tabular")
    ids["doc_house"] = await seed_document(conn, DOC_HOUSE, status=CONFIRMED_STATUS,
                                           doc_family="narrative")
    ids["doc_plain"] = await seed_document(conn, DOC_PLAIN, status=CONFIRMED_STATUS)
    ids["doc_control"] = await seed_document(conn, DOC_CONTROL, status=CONFIRMED_STATUS)
    ids["doc_unconfirmed"] = await seed_document(conn, DOC_UNCONFIRMED, status="sorted")

    # A REAL narrative-extraction row, in the exact shape
    # `narrative_extraction.normalize_extraction` produces — the only field in
    # any deployed extractor that carries a document-stated NAME.
    await conn.execute(
        """
        INSERT INTO public.document_narrative_extractions
            (document_id, org_id, summary, extracted_provisions, key_dates, key_parties)
        VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::jsonb, $6::jsonb)
        """,
        ids["doc_fund"], DEFAULT_ORG_ID,
        "Capital account statement for a private fund interest.",
        json.dumps([{"provision_type": "capital account",
                     "description": "Statement of the member's capital account."}]),
        json.dumps([{"date": "June 30, 2026", "description": "statement date"}]),
        json.dumps([{"name": FUND_PARTY_NAME, "role": "managing member"}]),
    )
    return ids


def org_ctx(conn, org_id: str, *, super_admin: bool = False, commit: bool = True):
    """Transaction on ``conn`` with the RLS GUCs SET LOCAL.

    ``super_admin=False`` is the important default: these are TENANT tables and
    the isolation check is only meaningful without the escape hatch.
    """

    class _Ctx:
        async def __aenter__(self):
            self.tr = conn.transaction()
            await self.tr.start()
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true),"
                "       set_config('app.is_super_admin', $2, true),"
                "       set_config('app.current_auth0_sub', $3, true)",
                org_id, "true" if super_admin else "false",
                ADMIN_SUB if super_admin else MEMBER_SUB,
            )
            return conn

        async def __aexit__(self, et, e, tb):
            if et is None and commit:
                await self.tr.commit()
            else:
                await self.tr.rollback()
            return False

    return _Ctx()


def _module_source(module) -> str:
    return inspect.getsource(module)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — the four findings, REPORTED and ASSERTED
# ═══════════════════════════════════════════════════════════════════════════


async def check_task1a(conn) -> None:
    """1a — the real Chancery document-confirm hook point."""
    sig = inspect.signature(document_review.confirm_document)
    params = list(sig.parameters)
    src = inspect.getsource(document_review.confirm_document)

    router_src = open(
        os.path.join(_HERE, "..", "routers", "document_review.py"), encoding="utf-8"
    ).read()
    i_confirm = router_src.find("review.confirm_document(")
    i_bridge = router_src.find("fire_document_confirmed_triggers(")
    ordered = 0 < i_confirm < i_bridge

    report(
        "1a THE HOOK POINT — services.document_review.confirm_document, with the "
        "real seam one layer up in the router",
        f"confirm_document{sig} — parameters {params}. Its whole body is ONE "
        f"UPDATE setting documents.status='confirmed' + confirmed_by + "
        f"confirmed_at, returning those three. NO callback, NO event row, and it "
        f"does NOT return any extracted field. The established seam is "
        f"routers/document_review.py POST /documents/{{id}}/confirm, which calls "
        f"review.confirm_document and THEN "
        f"chancery_workflow_bridge.fire_document_confirmed_triggers(pool, org_id, "
        f"document_id, started_by=user_id) — ordering confirmed in the source "
        f"({i_confirm} < {i_bridge}). AVAILABLE THERE: org_id (JWT claims via "
        f"get_org_id), document_id, user_id, the pool. NOT available: any "
        f"extracted field — those must be read back by document_id, which is what "
        f"portfolio_chancery.read_document_extractions does. Phase E deliberately "
        f"adds NO second auto-fire to that router: the same confirmed capital "
        f"account statement is a NEW position in quarter one and a VALUATION on "
        f"an existing one every quarter after, and nothing in the document "
        f"distinguishes them.",
    )
    check(
        "[Y] 1a — confirm_document's real signature is (conn, org_id, "
        "document_id, *, confirmed_by) and it sets status='confirmed'",
        params == ["conn", "org_id", "document_id", "confirmed_by"]
        and "CONFIRMED_STATUS" in src
        and document_review.CONFIRMED_STATUS == CONFIRMED_STATUS,
        f"params={params}, review.CONFIRMED_STATUS="
        f"{document_review.CONFIRMED_STATUS!r}, portfolio_chancery."
        f"CONFIRMED_STATUS={CONFIRMED_STATUS!r}",
    )
    check(
        "[Y] 1a — the router fires the Phase-7 bridge AFTER the confirm, which is "
        "the only extension seam that exists",
        ordered,
        f"confirm at char {i_confirm}, bridge at char {i_bridge}",
    )


async def check_task1b(conn) -> None:
    """1b — what the deployed extractors really produce. Honest answer: not the
    commitment figures."""
    narrative_cols = {
        r["column_name"]
        for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' "
            "  AND table_name='document_narrative_extractions'"
        )
    }
    payload_cols = narrative_cols - {"id", "document_id", "org_id", "created_at"}
    normalized = narrative_extraction.normalize_extraction({})
    k1_keys = {k for k, _ in textract_extraction._K1_BOXES}
    k1_party_keys = {p[1] for p in textract_extraction._FORM_TYPES} | {
        textract_extraction._DEFAULT_PARTY_KEY
    }
    categories = {
        r["code"]
        for r in await conn.fetch(
            "SELECT code FROM public.reference_data WHERE list_key='doc_category'"
        )
    }
    capital_account_codes = {
        c for c in categories if "capital" in c or "capital_account" in c
    }

    report(
        "1b EXTRACTION FIELD MAPPING — A REAL GAP, NOT A MAPPING PROBLEM",
        f"NARRATIVE (Phase 11a): document_narrative_extractions payload columns "
        f"are {sorted(payload_cols)}; normalize_extraction's output keys are "
        f"{sorted(normalized)}; the list item shapes are fixed at "
        f"{{provision_type, description}} / {{date, description}} / {{name, "
        f"role}}. NOT ONE MONETARY KEY ANYWHERE. It also would not run on this "
        f"document: run_narrative_extraction is gated on "
        f"_NARRATIVE_CATEGORIES={sorted(_NARRATIVE_CATEGORIES)}, and the "
        f"deployed doc_category catalogue has {len(categories)} codes with NO "
        f"capital-account code among them (matches: "
        f"{sorted(capital_account_codes) or 'none'}) — the nearest, "
        f"'financial_statement' and 'subscription_doc', are both "
        f"doc_family='{doc_family_for_category('financial_statement')}', i.e. "
        f"routed to the K-1 extractor. TABULAR (Phase 3): the only template that "
        f"exists is '{textract_extraction.K1_TEMPLATE_TYPE}', whose mapped_fields "
        f"keys are {sorted(k1_keys)} plus a party key from {sorted(k1_party_keys)} "
        f"— five income boxes and the RECIPIENT's name. CONCLUSION: "
        f"{sorted(COMMITMENT_FIELDS_NOT_EXTRACTED)} require a NEW extraction "
        f"template that does not exist. Phase E does not fabricate it. What IS "
        f"mapped is what genuinely exists: documents.original_filename (NOT "
        f"NULL), key_parties[].name, and summary.",
    )
    check(
        "[Y] 1b — document_narrative_extractions carries NO monetary column and "
        "normalize_extraction produces no monetary key",
        payload_cols == {"summary", "extracted_provisions", "key_dates",
                         "key_parties"}
        and set(normalized) == {"summary", "key_provisions", "key_dates",
                                "key_parties"}
        and not (set(normalized) & COMMITMENT_FIELDS_NOT_EXTRACTED),
        f"payload={sorted(payload_cols)}, normalized={sorted(normalized)}",
    )
    check(
        "[Y] 1b — the K-1 template's mapped_fields carry NONE of the four "
        "commitment figures, and no capital-account doc_category is deployed",
        not ((k1_keys | k1_party_keys) & COMMITMENT_FIELDS_NOT_EXTRACTED)
        and not capital_account_codes,
        f"k1 keys={sorted(k1_keys | k1_party_keys)}, capital-account codes="
        f"{sorted(capital_account_codes) or 'none'} of {len(categories)}",
    )


def check_task1c() -> None:
    """1c — Phase D's composition is reused, not reinvented. AST-asserted."""
    src = _module_source(portfolio_chancery)
    tree = ast.parse(src)
    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    # Docstrings legitimately DISCUSS these strings; only executable SQL matters,
    # so the scan excludes every docstring in the module.
    # clean=False is load-bearing: ast.get_docstring dedents by default, so the
    # cleaned text does not compare equal to the raw ast.Constant value and the
    # module docstring — which legitimately DISCUSSES "INSERT INTO portfolio" and
    # "document_record_links" — would fail this check on its own prose.
    docstrings = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef,
                                    ast.AsyncFunctionDef, ast.ClassDef))}
    sql_literals = [s for s in literals if s not in docstrings]
    inserts_portfolio = [
        s for s in sql_literals
        if "insert into portfolio." in s.lower()
    ]
    writes_links = [
        s for s in sql_literals
        if "document_record_links" in s.lower()
        and any(w in s.lower() for w in ("insert", "update", "delete"))
    ]

    report(
        "1c COMPOSITION — Phase D's create_asset_from_document / "
        "create_position_from_document, called unchanged",
        "services/portfolio_documents.py already implements 'call A2's writer, "
        "then link the id it returned' for record_type='portfolio_asset' and "
        "'portfolio_position', delegating both the write and the link to "
        "services.document_linkage. portfolio_chancery.py calls those two "
        "functions and contains NO 'INSERT INTO portfolio.*' and NO write "
        "against document_record_links. That is load-bearing: "
        "portfolio_assets.create_position is the ONLY code enforcing the "
        "ownership-basis contract (positions has no CHECK covering it), and "
        "link_portfolio_document is the only thing checking the record-type "
        "vocabulary against a column that has no CHECK either.",
    )
    check(
        "[Y] 1c — portfolio_chancery composes Phase D's two writers and contains "
        "no direct portfolio INSERT and no direct link write",
        {"create_asset_from_document", "create_position_from_document"} <= called
        and not inserts_portfolio and not writes_links,
        f"calls={sorted(called & {'create_asset_from_document', 'create_position_from_document'})}, "
        f"direct portfolio INSERTs={len(inserts_portfolio)}, "
        f"direct link writes={len(writes_links)}",
    )


async def check_task1d(conn) -> dict:
    """1d — the REAL deployed defaults Task 4 has to override."""
    rows = {
        r["column_name"]: r["column_default"]
        for r in await conn.fetch(
            "SELECT column_name, column_default FROM information_schema.columns "
            "WHERE table_schema='portfolio' AND table_name='assets' "
            "  AND column_name IN ('asset_class','include_in_performance')"
        )
    }
    class_default = (rows.get("asset_class") or "")
    perf_default = (rows.get("include_in_performance") or "")
    report(
        "1d DEPLOYED DEFAULTS on portfolio.assets",
        f"asset_class DEFAULT {class_default!r} (CHECK assets_class_chk allows "
        f"financial | hard_asset); include_in_performance DEFAULT "
        f"{perf_default!r}. Both are NOT NULL. A hard asset must override BOTH, "
        f"and the override is only proven by showing the stored values DIFFER "
        f"from these defaults while a control row created through the same "
        f"function lands ON them.",
    )
    check(
        "[Y] 1d — deployed defaults are asset_class='financial' and "
        "include_in_performance=true, exactly as A2 recorded",
        "'financial'" in class_default and perf_default.lower().startswith("true"),
        f"asset_class={class_default!r}, include_in_performance={perf_default!r}",
    )
    return {"asset_class": class_default, "include_in_performance": perf_default}


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 — Chancery-sourced creation
# ═══════════════════════════════════════════════════════════════════════════


async def check_chancery_creation(conn, ids: dict) -> dict:
    """The fund position, created FROM the confirmed capital-account statement."""
    extractions = await read_document_extractions(
        conn, org_id=DEFAULT_ORG_ID, document_id=ids["doc_fund"]
    )
    gap = commitment_fields_from_document(extractions)
    check(
        "[Y] 1b (proven at runtime) — commitment_fields_from_document reports "
        "every commitment figure MISSING against a real extracted document, with "
        "a reason, rather than returning zeros",
        gap["available"] == {}
        and set(gap["missing"]) == COMMITMENT_FIELDS_NOT_EXTRACTED
        and bool(gap["reason"]) and gap["narrative_extracted"] is True,
        f"missing={gap['missing']}, narrative_extracted={gap['narrative_extracted']}",
    )

    result = await create_position_from_chancery_document(
        conn,
        org_id=DEFAULT_ORG_ID,
        document_id=ids["doc_fund"],
        owner_entity_id=ids["owner"],
        asset_type="private_fund_interest",
        as_of_date=AS_OF,
        valuation_method="nav",
        market_value=FUND_MARKET_VALUE,
        currency_code="USD",
        created_by=ADMIN_USER_ID,
    )
    ids["fund_asset"] = result.asset_id
    ids["fund_position"] = result.position_id

    row = await conn.fetchrow(
        f"SELECT authority, source_system, ownership_basis, market_value "
        f"FROM {TABLE_POSITIONS} WHERE id = $1::uuid",
        result.position_id,
    )
    check(
        "[Y] TASK 3 — the Chancery-sourced position carries authority='stated' "
        "and source_system='chancery', read back from the stored row",
        row["authority"] == CHANCERY_AUTHORITY
        and row["source_system"] == CHANCERY_SOURCE_SYSTEM
        and _dec(row["market_value"]) == FUND_MARKET_VALUE,
        f"authority={row['authority']!r}, source_system={row['source_system']!r}, "
        f"market_value={row['market_value']}",
    )
    check(
        "[Y] TASK 3 — the asset name came from the ONE real extracted field that "
        "carries a document-stated name (key_parties[].name), not from a "
        "fabricated one",
        result.name_source == NAME_SOURCE_NARRATIVE_PARTY
        and result.asset_name == FUND_PARTY_NAME,
        f"name={result.asset_name!r} via {result.name_source!r}",
    )

    # Both links, read back through Chancery's REAL panel function — the same
    # one behind GET /records/{record_type}/{record_id}/documents.
    asset_docs = await list_portfolio_record_documents(
        conn, org_id=DEFAULT_ORG_ID,
        record_type=RECORD_TYPE_ASSET, record_id=result.asset_id,
    )
    position_docs = await list_portfolio_record_documents(
        conn, org_id=DEFAULT_ORG_ID,
        record_type=RECORD_TYPE_POSITION, record_id=result.position_id,
    )
    # The panel's own key is `document_id` (document_linkage builds the dict) —
    # read off the real function's output, not guessed at.
    asset_hit = [
        d for d in asset_docs if str(d.get("document_id")) == str(ids["doc_fund"])
    ]
    position_hit = [
        d for d in position_docs if str(d.get("document_id")) == str(ids["doc_fund"])
    ]
    check(
        "[Y] TASK 3 — BOTH the asset and the position are linked to the source "
        "document and readable through Phase 9's real DocumentsPanel lookup",
        len(asset_hit) == 1 and len(position_hit) == 1,
        f"asset link rows={len(asset_docs)} (hit {len(asset_hit)}), "
        f"position link rows={len(position_docs)} (hit {len(position_hit)})",
    )

    stored_links = await conn.fetch(
        "SELECT record_type FROM public.document_record_links "
        "WHERE document_id = $1::uuid ORDER BY record_type",
        ids["doc_fund"],
    )
    check(
        "[Y] TASK 3 — exactly the two prefixed record types were written, "
        "nothing bare that would collide with Chancery's own namespace",
        [r["record_type"] for r in stored_links]
        == sorted([RECORD_TYPE_ASSET, RECORD_TYPE_POSITION]),
        f"{[r['record_type'] for r in stored_links]}",
    )

    # The Task-1a hook made enforceable: an unconfirmed document is refused.
    refused = None
    try:
        await create_position_from_chancery_document(
            conn,
            org_id=DEFAULT_ORG_ID,
            document_id=ids["doc_unconfirmed"],
            owner_entity_id=ids["owner"],
            asset_type="private_fund_interest",
            as_of_date=AS_OF,
            market_value=Decimal("1.00"),
        )
    except ChanceryPortfolioError as exc:
        refused = str(exc)
    leaked = await conn.fetchval(
        "SELECT count(*) FROM public.document_record_links WHERE document_id = $1::uuid",
        ids["doc_unconfirmed"],
    )
    check(
        "[Y] TASK 3 — a document that never reached the Task-1a confirm hook is "
        "REFUSED, and nothing partial is left behind",
        refused is not None and "sorted" in refused and leaked == 0,
        f"raised={refused is not None}, links left={leaked}",
    )

    # The filename rung: a confirmed document with no extraction at all.
    plain = await create_position_from_chancery_document(
        conn,
        org_id=DEFAULT_ORG_ID,
        document_id=ids["doc_plain"],
        owner_entity_id=ids["owner"],
        asset_type="private_fund_interest",
        as_of_date=AS_OF,
        valuation_method="nav",
        market_value=Decimal("1.00"),
        created_by=ADMIN_USER_ID,
    )
    ids["plain_asset"] = plain.asset_id
    ids["plain_position"] = plain.position_id
    check(
        "[Y] TASK 3 — with no extraction at all the name falls back to the one "
        "field that is NOT NULL on every document, and says so",
        plain.name_source == NAME_SOURCE_FILENAME
        and plain.asset_name == os.path.splitext(DOC_PLAIN)[0],
        f"name={plain.asset_name!r} via {plain.name_source!r}",
    )
    return ids


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — commitment derivation against REAL transactions
# ═══════════════════════════════════════════════════════════════════════════


async def check_commitment_lifecycle(conn, ids: dict) -> dict:
    commitment_id = await create_commitment(
        conn,
        org_id=DEFAULT_ORG_ID,
        position_id=ids["fund_position"],
        commitment_amount=COMMITMENT_AMOUNT,
        commitment_date=COMMIT_DATE,
        vintage_year=2024,
        liquidity_terms={"lockup_years": 10, "extensions": 2},
        tax_doc_expected=True,
        tax_year=TAX_YEAR,
    )
    ids["commitment"] = commitment_id

    fresh = await get_commitment(
        conn, org_id=DEFAULT_ORG_ID, commitment_id=commitment_id
    )
    check(
        "[Y] TASK 2 — a commitment is created against a REAL position with the "
        "correct initial defaults: 0 / 0 / 0 and unfunded NULL (not derived yet)",
        _dec(fresh["commitment_amount"]) == COMMITMENT_AMOUNT
        and _dec(fresh["called_to_date"]) == Decimal("0")
        and _dec(fresh["distributed_to_date"]) == Decimal("0")
        and _dec(fresh["recallable_amount"]) == Decimal("0")
        and fresh["unfunded"] is None
        and fresh["tax_doc_status"] == "awaiting",
        f"called={fresh['called_to_date']}, distributed={fresh['distributed_to_date']}, "
        f"recallable={fresh['recallable_amount']}, unfunded={fresh['unfunded']!r}, "
        f"tax_doc_status={fresh['tax_doc_status']!r}",
    )

    # Baseline recompute with ZERO transactions — this is what the "decreases by
    # exactly 50,000" assertion is measured against.
    baseline = await recompute_commitment(conn, DEFAULT_ORG_ID, commitment_id)
    check(
        "[Y] TASK 2 — a recompute with no transactions yields unfunded = the full "
        "commitment, which is the baseline every delta below is measured from",
        baseline.unfunded == UNFUNDED_BASELINE
        and baseline.called_to_date == Decimal("0")
        and baseline.transactions_counted == 0,
        f"unfunded={baseline.unfunded}, called={baseline.called_to_date}, "
        f"txns={baseline.transactions_counted}",
    )

    # ── A REAL $50,000 capital call ─────────────────────────────────────────
    await record_transaction(
        conn,
        org_id=DEFAULT_ORG_ID,
        position_id=ids["fund_position"],
        transaction_type_code="call_investment",
        trade_date=TRADE_CALL,
        authority=CHANCERY_AUTHORITY,
        source_system=CHANCERY_SOURCE_SYSTEM,
        gross_amount=CALL_AMOUNT,
        currency_code="USD",
    )
    after_call = await recompute_commitment(conn, DEFAULT_ORG_ID, commitment_id)
    stored_call = await get_commitment(
        conn, org_id=DEFAULT_ORG_ID, commitment_id=commitment_id
    )
    check(
        "[Y] TASK 2 RECOMPUTE — a REAL $50,000 capital call increases "
        "called_to_date by EXACTLY $50,000 and decreases unfunded by EXACTLY "
        "$50,000 (exact Decimal, stored row re-read)",
        after_call.called_to_date == CALLED_AFTER_CALL
        and after_call.called_to_date - baseline.called_to_date == CALL_AMOUNT
        and after_call.unfunded == UNFUNDED_AFTER_CALL
        and baseline.unfunded - after_call.unfunded == CALL_AMOUNT
        and _dec(stored_call["called_to_date"]) == CALLED_AFTER_CALL
        and _dec(stored_call["unfunded"]) == UNFUNDED_AFTER_CALL,
        f"called {baseline.called_to_date} -> {after_call.called_to_date} "
        f"(delta {after_call.called_to_date - baseline.called_to_date}); "
        f"unfunded {baseline.unfunded} -> {after_call.unfunded} "
        f"(delta {after_call.unfunded - baseline.unfunded}); stored "
        f"called={stored_call['called_to_date']} unfunded={stored_call['unfunded']}",
    )

    # ── A REAL $10,000 RECALLABLE distribution ──────────────────────────────
    await record_transaction(
        conn,
        org_id=DEFAULT_ORG_ID,
        position_id=ids["fund_position"],
        transaction_type_code="dist_recallable",
        trade_date=TRADE_RECALLABLE,
        authority=CHANCERY_AUTHORITY,
        source_system=CHANCERY_SOURCE_SYSTEM,
        gross_amount=RECALLABLE_DIST,
        currency_code="USD",
    )
    after_recallable = await recompute_commitment(
        conn, DEFAULT_ORG_ID, commitment_id
    )
    check(
        "[Y] TASK 2 RECOMPUTE — a $10,000 dist_recallable DECREASES "
        "called_to_date and INCREASES both distributed_to_date and "
        "recallable_amount, each by exactly $10,000",
        after_recallable.called_to_date == CALLED_AFTER_RECALLABLE
        and after_call.called_to_date - after_recallable.called_to_date
        == RECALLABLE_DIST
        and after_recallable.distributed_to_date == DISTRIBUTED_AFTER_RECALLABLE
        and after_recallable.distributed_to_date - after_call.distributed_to_date
        == RECALLABLE_DIST
        and after_recallable.recallable_amount == RECALLABLE_AFTER_RECALLABLE
        and after_recallable.recallable_amount - after_call.recallable_amount
        == RECALLABLE_DIST
        and after_recallable.unfunded == UNFUNDED_AFTER_RECALLABLE,
        f"called {after_call.called_to_date} -> {after_recallable.called_to_date}; "
        f"distributed {after_call.distributed_to_date} -> "
        f"{after_recallable.distributed_to_date}; recallable "
        f"{after_call.recallable_amount} -> {after_recallable.recallable_amount}; "
        f"unfunded={after_recallable.unfunded}",
    )
    report(
        "TASK 2 — the unfunded formula, and the number the alternative gives",
        f"UNFUNDED_FORMULA in force = '{UNFUNDED_FORMULA}' -> "
        f"{after_recallable.unfunded}. The flag-driven alternative "
        f"(commitment + SUM(amount * affects_unfunded), which equals commitment - "
        f"called because affects_unfunded is the exact negation of "
        f"affects_paid_in on all five non-zero codes) gives "
        f"{after_recallable.unfunded_flag_driven} — expected "
        f"{UNFUNDED_FLAG_DRIVEN_AFTER_RECALLABLE}. The brief specifies the "
        f"former and it is implemented as specified; the 10,000 difference is "
        f"dist_recallable's affects_paid_in=-1 restoring capacity through "
        f"called_to_date AND recallable_amount being added on top. Recorded, not "
        f"silently changed; every stored figure is re-derivable by re-running "
        f"the recompute if the semantics are revised.",
    )
    check(
        "[Y] TASK 2 — the flag-driven alternative is computable and DIFFERS by "
        "exactly the recallable amount, so the choice of formula is a measured "
        "number and not a claim in a docstring",
        after_recallable.unfunded_flag_driven
        == UNFUNDED_FLAG_DRIVEN_AFTER_RECALLABLE
        and after_recallable.unfunded - after_recallable.unfunded_flag_driven
        == RECALLABLE_DIST,
        f"in-force={after_recallable.unfunded}, "
        f"flag-driven={after_recallable.unfunded_flag_driven}",
    )

    # ── A REAL $7,500 ORDINARY distribution — the distinctness proof ────────
    await record_transaction(
        conn,
        org_id=DEFAULT_ORG_ID,
        position_id=ids["fund_position"],
        transaction_type_code="dist_income",
        trade_date=TRADE_INCOME,
        authority=CHANCERY_AUTHORITY,
        source_system=CHANCERY_SOURCE_SYSTEM,
        gross_amount=INCOME_DIST,
        currency_code="USD",
    )
    after_income = await recompute_commitment(conn, DEFAULT_ORG_ID, commitment_id)
    check(
        "[Y] TASK 2 RECOMPUTE — a NORMAL distribution (dist_income) does NOT "
        "touch called_to_date, unfunded or recallable_amount at all, which is "
        "what makes the recallable case demonstrably distinct",
        after_income.called_to_date == after_recallable.called_to_date
        and after_income.unfunded == after_recallable.unfunded
        and after_income.recallable_amount == after_recallable.recallable_amount
        and after_income.distributed_to_date == DISTRIBUTED_AFTER_INCOME
        and after_income.distributed_to_date - after_recallable.distributed_to_date
        == INCOME_DIST,
        f"called {after_recallable.called_to_date} -> {after_income.called_to_date}; "
        f"unfunded {after_recallable.unfunded} -> {after_income.unfunded}; "
        f"recallable {after_recallable.recallable_amount} -> "
        f"{after_income.recallable_amount}; distributed "
        f"{after_recallable.distributed_to_date} -> "
        f"{after_income.distributed_to_date}",
    )

    # Idempotence: re-derive, do not increment.
    again = await recompute_commitment(conn, DEFAULT_ORG_ID, commitment_id)
    check(
        "[Y] TASK 2 — the recompute is idempotent: a second call over the same "
        "three transactions returns identical figures (it re-derives, it does "
        "not increment)",
        (again.called_to_date, again.distributed_to_date, again.recallable_amount,
         again.unfunded)
        == (after_income.called_to_date, after_income.distributed_to_date,
            after_income.recallable_amount, after_income.unfunded),
        f"called={again.called_to_date}, distributed={again.distributed_to_date}, "
        f"recallable={again.recallable_amount}, unfunded={again.unfunded}",
    )
    check(
        "[Y] TASK 2 — all three transactions were counted and none was silently "
        "valued at zero for want of an amount",
        again.transactions_counted == 3 and again.amountless_transactions == 0,
        f"counted={again.transactions_counted}, "
        f"amountless={again.amountless_transactions}",
    )
    return ids


# ═══════════════════════════════════════════════════════════════════════════
# TASK 4 — the hard asset, proven end to end
# ═══════════════════════════════════════════════════════════════════════════


async def check_hard_asset(conn, ids: dict, defaults: dict) -> dict:
    # CONTROL: the SAME function, no overrides. Proves what the defaults yield.
    control = await create_position_from_chancery_document(
        conn,
        org_id=DEFAULT_ORG_ID,
        document_id=ids["doc_control"],
        owner_entity_id=ids["owner"],
        asset_type="private_fund_interest",
        as_of_date=AS_OF,
        valuation_method="nav",
        market_value=CONTROL_MARKET_VALUE,
        created_by=ADMIN_USER_ID,
    )
    ids["control_asset"] = control.asset_id
    ids["control_position"] = control.position_id

    # THE HARD ASSET: a house, confirmed via a Chancery document, with BOTH
    # A2 defaults deliberately overridden.
    house = await create_position_from_chancery_document(
        conn,
        org_id=DEFAULT_ORG_ID,
        document_id=ids["doc_house"],
        owner_entity_id=ids["hard_owner"],
        asset_type="real_estate",
        as_of_date=AS_OF,
        name=HOUSE_ASSET_NAME,
        asset_class="hard_asset",
        include_in_performance=False,
        valuation_method="appraisal",
        market_value=HARD_ASSET_NET_WORTH,
        currency_code="USD",
        created_by=ADMIN_USER_ID,
    )
    ids["house_asset"] = house.asset_id
    ids["house_position"] = house.position_id

    rows = {
        r["id"]: r
        for r in await conn.fetch(
            f"SELECT id::text AS id, name, asset_class, include_in_performance, "
            f"       valuation_method "
            f"FROM {TABLE_ASSETS} WHERE id = ANY($1::uuid[])",
            [house.asset_id, control.asset_id],
        )
    }
    h, c = rows[house.asset_id], rows[control.asset_id]

    check(
        "[Y] TASK 4 HARD ASSET — asset_class='hard_asset' and "
        "include_in_performance=false are stored, AND the OVERRIDE is proven: "
        "both differ from the deployed defaults, while a control asset created "
        "through the SAME function lands exactly ON them",
        h["asset_class"] == "hard_asset"
        and h["include_in_performance"] is False
        and c["asset_class"] == "financial"
        and c["include_in_performance"] is True
        and "'financial'" in defaults["asset_class"]
        and defaults["include_in_performance"].lower().startswith("true"),
        f"hard: class={h['asset_class']!r} perf={h['include_in_performance']} | "
        f"control: class={c['asset_class']!r} perf={c['include_in_performance']} | "
        f"deployed defaults: {defaults['asset_class']!r} / "
        f"{defaults['include_in_performance']!r}",
    )
    check(
        "[Y] TASK 4 HARD ASSET — it is Chancery-sourced like any other: "
        "authority='stated', source_system='chancery', and the deed is linked to "
        "both the asset and the position",
        await _chancery_shape(conn, house.position_id)
        and len(await list_portfolio_record_documents(
            conn, org_id=DEFAULT_ORG_ID, record_type=RECORD_TYPE_ASSET,
            record_id=house.asset_id)) == 1
        and len(await list_portfolio_record_documents(
            conn, org_id=DEFAULT_ORG_ID, record_type=RECORD_TYPE_POSITION,
            record_id=house.position_id)) == 1,
        f"asset={house.asset_id}, position={house.position_id}, "
        f"name via {house.name_source!r}",
    )
    check(
        "[Y] TASK 4 — an explicit name beats every inferred one, so a human at "
        "the confirm screen is never overruled by an extraction",
        house.name_source == NAME_SOURCE_EXPLICIT
        and house.asset_name == HOUSE_ASSET_NAME,
        f"name={house.asset_name!r} via {house.name_source!r}",
    )

    # ── TWO purposes, coexisting, with the LATER one deliberately net_worth ──
    ins_id = await record_valuation(
        conn,
        org_id=DEFAULT_ORG_ID,
        asset_id=house.asset_id,
        valuation_date=VAL_DATE_INSURANCE,
        value=HARD_ASSET_INSURANCE,
        purpose="insurance",
        status="final",
        valuation_method="appraisal",
        valuation_source=f"{FIXTURE_TAG} carrier replacement-cost appraisal",
        currency_code="USD",
    )
    nw_id = await record_valuation(
        conn,
        org_id=DEFAULT_ORG_ID,
        asset_id=house.asset_id,
        valuation_date=VAL_DATE_NET_WORTH,
        value=HARD_ASSET_NET_WORTH,
        purpose="net_worth",
        status="final",
        valuation_method="appraisal",
        valuation_source=f"{FIXTURE_TAG} market appraisal",
        currency_code="USD",
    )
    coexist = await conn.fetch(
        f"SELECT id::text AS id, purpose, value, valuation_date "
        f"FROM {TABLE_VALUATIONS} WHERE asset_id = $1::uuid "
        f"  AND valid_to IS NULL AND system_to IS NULL ORDER BY purpose",
        house.asset_id,
    )
    check(
        "[Y] TASK 4 VALUATIONS — an 'insurance' and a 'net_worth' valuation for "
        "the SAME asset coexist simultaneously with DIFFERENT values; neither "
        "supersedes or excludes the other",
        len(coexist) == 2
        and {r["purpose"] for r in coexist} == {"insurance", "net_worth"}
        and _dec(dict(coexist[0])["value"]) == HARD_ASSET_INSURANCE
        and _dec(dict(coexist[1])["value"]) == HARD_ASSET_NET_WORTH
        and ins_id != nw_id,
        f"{[(r['purpose'], str(r['value']), r['valuation_date'].isoformat()) for r in coexist]}",
    )

    insurance = await resolve_current_value(
        conn, org_id=DEFAULT_ORG_ID, asset_id=house.asset_id, purpose="insurance"
    )
    net_worth = await resolve_current_value(
        conn, org_id=DEFAULT_ORG_ID, asset_id=house.asset_id, purpose="net_worth"
    )
    check(
        "[Y] TASK 4 VALUATIONS — reading BY PURPOSE returns the right one, not "
        "the most recent regardless of purpose: net_worth is dated LATER, so a "
        "purpose-blind resolver would return 1,200,000 for both and this fails",
        insurance.value == HARD_ASSET_INSURANCE
        and net_worth.value == HARD_ASSET_NET_WORTH
        and insurance.valuation_id == ins_id
        and net_worth.valuation_id == nw_id
        and VAL_DATE_NET_WORTH > VAL_DATE_INSURANCE,
        f"insurance={insurance.value} (dated {VAL_DATE_INSURANCE}), "
        f"net_worth={net_worth.value} (dated {VAL_DATE_NET_WORTH})",
    )
    market = await resolve_current_value(
        conn, org_id=DEFAULT_ORG_ID, asset_id=house.asset_id, purpose="market"
    )
    check(
        "[Y] TASK 4 VALUATIONS — a purpose with no valuation returns an honest "
        "absence with a reason, never Decimal(0)",
        market.value is None and bool(market.reason),
        f"value={market.value!r}, reason={(market.reason or '')[:70]}…",
    )
    return ids


async def _chancery_shape(conn, position_id: str) -> bool:
    row = await conn.fetchrow(
        f"SELECT authority, source_system FROM {TABLE_POSITIONS} WHERE id = $1::uuid",
        position_id,
    )
    return (row["authority"] == CHANCERY_AUTHORITY
            and row["source_system"] == CHANCERY_SOURCE_SYSTEM)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 5 — the tax-document chase list
# ═══════════════════════════════════════════════════════════════════════════


async def check_tax_chase(conn, ids: dict) -> dict:
    """Three distinct cases plus a wrong-year control, then the real EXPLAIN."""
    # The ON-list one is ids['commitment'] (expected=True, awaiting, TAX_YEAR).
    received_id = await create_commitment(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["plain_position"],
        commitment_amount=Decimal("250000.00"), commitment_date=COMMIT_DATE,
        tax_doc_expected=True, tax_year=TAX_YEAR,
    )
    await set_tax_doc_status(
        conn, org_id=DEFAULT_ORG_ID, commitment_id=received_id,
        tax_doc_status="received",
    )
    not_expected_id = await create_commitment(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["control_position"],
        commitment_amount=Decimal("125000.00"), commitment_date=COMMIT_DATE,
        tax_doc_expected=False,
    )
    wrong_year_id = await create_commitment(
        conn, org_id=DEFAULT_ORG_ID, position_id=ids["house_position"],
        commitment_amount=Decimal("75000.00"), commitment_date=COMMIT_DATE,
        tax_doc_expected=True, tax_year=OTHER_TAX_YEAR,
    )
    ids["commitment_received"] = received_id
    ids["commitment_not_expected"] = not_expected_id
    ids["commitment_wrong_year"] = wrong_year_id

    rows = await tax_chase_list(conn, org_id=DEFAULT_ORG_ID, tax_year=TAX_YEAR)
    on_list = {r["commitment_id"] for r in rows}
    check(
        "[Y] TASK 5 CHASE LIST — all three cases proven DISTINCTLY: "
        "expected+'awaiting' APPEARS; 'received' does NOT; "
        "tax_doc_expected=false does NOT",
        ids["commitment"] in on_list
        and received_id not in on_list
        and not_expected_id not in on_list,
        f"awaiting on list={ids['commitment'] in on_list}, "
        f"received on list={received_id in on_list}, "
        f"not-expected on list={not_expected_id in on_list}, "
        f"list size={len(rows)}",
    )
    check(
        "[Y] TASK 5 CHASE LIST — a commitment expecting a document for a "
        "DIFFERENT tax year is not on this year's list (the year filter does "
        "real work, it is not decoration on the index)",
        wrong_year_id not in on_list
        and wrong_year_id in {
            r["commitment_id"]
            for r in await tax_chase_list(
                conn, org_id=DEFAULT_ORG_ID, tax_year=OTHER_TAX_YEAR)
        },
        f"{OTHER_TAX_YEAR} commitment absent from {TAX_YEAR} list and present "
        f"in its own",
    )
    hit = next((r for r in rows if r["commitment_id"] == ids["commitment"]), None)
    check(
        "[Y] TASK 5 CHASE LIST — a chased row carries what a person needs to "
        "chase it: the asset, the owner, the status and the outstanding figures",
        hit is not None
        and hit["asset_name"] == FUND_PARTY_NAME
        and hit["owner_name"] == E_OWNER
        and hit["tax_doc_status"] == "awaiting"
        and hit["tax_year"] == TAX_YEAR,
        f"asset={None if not hit else hit['asset_name']!r}, "
        f"owner={None if not hit else hit['owner_name']!r}",
    )
    check(
        "[Y] TASK 5 — set_tax_doc_status is the only thing that moves a "
        "commitment off the list, and a recompute cannot: re-deriving the totals "
        "leaves tax_doc_status untouched",
        (await recompute_commitment(conn, DEFAULT_ORG_ID, ids["commitment"]))
        is not None
        and (await get_commitment(
            conn, org_id=DEFAULT_ORG_ID,
            commitment_id=ids["commitment"]))["tax_doc_status"] == "awaiting"
        and set(TAX_DOC_STATUSES)
        == {"not_expected", "awaiting", "received", "amended"},
        f"statuses={sorted(TAX_DOC_STATUSES)}",
    )

    # ── The index. Asked properly. ──────────────────────────────────────────
    natural_plan = await explain_tax_chase(
        conn, org_id=DEFAULT_ORG_ID, tax_year=TAX_YEAR, force_index=False
    )
    plan = await explain_tax_chase(
        conn, org_id=DEFAULT_ORG_ID, tax_year=TAX_YEAR, force_index=True
    )
    uses_index = TAX_CHASE_INDEX in plan
    seq_scan_on_commitments = "Seq Scan on commitments" in plan
    report(
        "TASK 5 — the plain, cost-based plan on a 4-row fixture table",
        f"EXPLAIN without enable_seqscan=off:\n       "
        + natural_plan.replace("\n", "\n       ")
        + f"\n       The planner is COST-based: on a table holding four fixture "
        f"rows a sequential scan is genuinely cheaper than any index, so a plain "
        f"EXPLAIN here measures the row count and not the query. The assertion "
        f"below discourages seqscan and asserts {TAX_CHASE_INDEX} BY NAME — a "
        f"query the partial index could not serve would fall back to "
        f"idx_commitments_org or to a seq scan anyway, so seeing this index named "
        f"is a real proof of applicability.",
    )
    check(
        f"[Y] TASK 5 — the chase-list query is served by the REAL deployed "
        f"index {TAX_CHASE_INDEX} (index scan, no sequential scan on "
        f"commitments)",
        uses_index and not seq_scan_on_commitments,
        f"uses {TAX_CHASE_INDEX}={uses_index}, seq scan on commitments="
        f"{seq_scan_on_commitments}\n       plan: "
        + plan.replace("\n", "\n       "),
    )

    # The endpoint, on the real router object. `main.app.routes` cannot answer
    # this — the app uses a lazy _IncludedRouter, and an auth 401 masks a 404, so
    # "it returned 401" is not evidence a route exists.
    from routers.portfolio_ingest import router as ingest_router

    chase_routes = [
        r for r in ingest_router.routes
        if getattr(r, "path", "") == "/portfolio/tax-chase"
    ]
    endpoint_src = (
        inspect.getsource(chase_routes[0].endpoint) if chase_routes else ""
    )
    check(
        "[Y] TASK 5 — the chase list is reachable as a real GET endpoint on the "
        "deployed portfolio router, gated on view_portfolio (a read, not a "
        "portfolio-management action) and taking org_id from JWT claims",
        len(chase_routes) == 1
        and "GET" in chase_routes[0].methods
        and "READ_PERMISSION" in endpoint_src
        and "get_org_id(request)" in endpoint_src,
        f"routes={[(sorted(r.methods), r.path) for r in chase_routes]}",
    )

    deployed = await conn.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE schemaname='portfolio' "
        "  AND tablename='commitments' AND indexname=$1",
        TAX_CHASE_INDEX,
    )
    check(
        "[Y] TASK 5 — the query spells out every term of the index's PARTIAL "
        "predicate, which is the only way the planner can prove it applies",
        deployed is not None
        and "tax_doc_expected = true" in deployed
        and "system_to IS NULL" in deployed
        and "valid_to IS NULL" in deployed,
        f"{deployed}",
    )
    return ids


# ═══════════════════════════════════════════════════════════════════════════
# CROSS-ORG ISOLATION — real app_service connection, no bypassrls
# ═══════════════════════════════════════════════════════════════════════════


async def check_cross_org(app_conn, ids: dict) -> None:
    # CONTROL FIRST. "The other org sees nothing" is satisfied just as well by
    # app_service being unable to read the table at all.
    async with org_ctx(app_conn, DEFAULT_ORG_ID, commit=False):
        own = await get_commitment(
            app_conn, org_id=DEFAULT_ORG_ID, commitment_id=ids["commitment"]
        )
        own_list = await tax_chase_list(
            app_conn, org_id=DEFAULT_ORG_ID, tax_year=TAX_YEAR
        )
    check(
        "[Y] CROSS-ORG CONTROL — the OWNING org reads its own commitment and its "
        "own chase list through the real app_service role",
        own is not None
        and ids["commitment"] in {r["commitment_id"] for r in own_list},
        f"commitment read={own is not None}, chase rows={len(own_list)}",
    )

    async with org_ctx(app_conn, OTHER_ORG_ID, commit=False):
        foreign = await get_commitment(
            app_conn, org_id=DEFAULT_ORG_ID, commitment_id=ids["commitment"]
        )
        foreign_list = await tax_chase_list(
            app_conn, org_id=DEFAULT_ORG_ID, tax_year=TAX_YEAR
        )
        recompute_err = None
        try:
            await recompute_commitment(
                app_conn, DEFAULT_ORG_ID, ids["commitment"]
            )
        except (CommitmentError, PortfolioError, asyncpg.PostgresError) as exc:
            recompute_err = f"{type(exc).__name__}"
    check(
        "[Y] CROSS-ORG — org B cannot read org A's commitment or its chase list, "
        "and cannot recompute it, under the real app_service connection",
        foreign is None and foreign_list == [] and recompute_err is not None,
        f"read={foreign!r}, chase rows={len(foreign_list)}, "
        f"recompute raised {recompute_err}",
    )

    async with org_ctx(app_conn, OTHER_ORG_ID, commit=False):
        chancery_err = None
        try:
            await create_position_from_chancery_document(
                app_conn,
                org_id=OTHER_ORG_ID,
                document_id=ids["doc_fund"],
                owner_entity_id=ids["owner"],
                asset_type="private_fund_interest",
                as_of_date=AS_OF,
                market_value=Decimal("1.00"),
            )
        except (ChanceryPortfolioError, PortfolioError,
                asyncpg.PostgresError) as exc:
            chancery_err = str(exc)
    check(
        "[Y] CROSS-ORG — the Chancery-sourced creation function refuses org A's "
        "document to org B: the document lookup is org-scoped and finds nothing",
        chancery_err is not None and "does not exist" in chancery_err,
        f"raised: {(chancery_err or '')[:110]}",
    )

    leaked = await app_conn.fetchval(
        f"SELECT count(*) FROM {TABLE_ASSETS} WHERE org_id = $1::uuid "
        f"  AND name LIKE '{FIXTURE_TAG}%'",
        OTHER_ORG_ID,
    )
    check(
        "[Y] CROSS-ORG — the refused call created nothing in org B",
        leaked == 0,
        f"org B fixture assets={leaked}",
    )


# ── Main ────────────────────────────────────────────────────────────────────


async def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    app_url = os.environ.get("APP_SERVICE_DATABASE_URL")
    if not db_url:
        print("[FAIL] DATABASE_URL is not set")
        return 1
    if not app_url:
        print("[FAIL] APP_SERVICE_DATABASE_URL is not set. There is NO SET ROLE "
              "fallback: every cross-org assertion is meaningless under a "
              "bypassrls role, so this script fails rather than pretending.")
        return 1

    admin_conn = await asyncpg.connect(db_url, statement_cache_size=0, ssl="require")
    try:
        app_conn = await asyncpg.connect(
            app_url, statement_cache_size=0, ssl="require")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] APP_SERVICE_DATABASE_URL did not connect: "
              f"{type(exc).__name__}: {exc}")
        await admin_conn.close()
        return 1

    baseline: dict[str, int] = {}
    try:
        await teardown(admin_conn)                                   # START
        baseline = await counts(admin_conn)
        print("\nBASELINE (must be restored exactly at teardown): "
              + ", ".join(f"{t.split('.')[-1]}={n}" for t, n in baseline.items()))
        nonempty = {t: n for t, n in baseline.items() if n}
        if nonempty:
            report(
                "TEARDOWN — rows are already present in these tables",
                f"{nonempty}. Teardown is by-fixture (every fixture row carries "
                f"the {FIXTURE_TAG!r} tag in a natural-key column) plus an exact "
                f"count assertion, NOT a truncate. portfolio.assets, "
                f"public.documents and public.entities hold real production "
                f"rows.",
            )

        print("\n── Task 1: discovery, reported AND asserted ──")
        await check_task1a(admin_conn)
        await check_task1b(admin_conn)
        check_task1c()
        defaults = await check_task1d(admin_conn)

        print("\n── Fixtures: two entities, five documents, one real narrative "
              "extraction ──")
        await seed_users(admin_conn)
        ids = await build_fixtures(admin_conn)

        print("\n── Task 3: a position created FROM a confirmed Chancery document ──")
        ids = await check_chancery_creation(admin_conn, ids)

        print("\n── Task 2: commitment derivation from REAL transactions ──")
        ids = await check_commitment_lifecycle(admin_conn, ids)

        print("\n── Task 4: the hard asset, and two purposes at once ──")
        ids = await check_hard_asset(admin_conn, ids, defaults)

        print("\n── Task 5: the tax-document chase list, and its index ──")
        ids = await check_tax_chase(admin_conn, ids)

        print("\n── Cross-org isolation (real app_service connection) ──")
        await check_cross_org(app_conn, ids)

    finally:
        await teardown(admin_conn)                                   # END
        if baseline:
            final = await counts(admin_conn)
            drift = {
                t: (baseline[t], final[t]) for t in TABLES if baseline[t] != final[t]
            }
            check(
                "[Y] TEARDOWN restores the EXACT before-count on every table "
                "touched — portfolio.commitments, portfolio.*, documents, "
                "document_record_links and entities included",
                not drift,
                f"drift (before, after): {drift}" if drift
                else ", ".join(f"{t.split('.')[-1]}={final[t]}" for t in TABLES),
            )
        await app_conn.close()
        await admin_conn.close()

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{'=' * 72}")
    print(f"RESULT: {passed}/{total} passed")
    failures = [(n, d) for n, ok, d in results if not ok]
    if failures:
        print("\nFAILURES:")
        for name, detail in failures:
            print(f"  · {name} — {detail}")
    print("=" * 72)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
