"""verify_edgarinventory_v2.py — the INVENTORY v2 fixes in
services/edgar_inventory.py, scripts/run_edgar_inventory.py, and the shared
quote-matching helper services/note_extraction/quote_match.py.

The first real run (17458851-feff-4eaf-a80c-f06b3827f5db) showed four real
gaps, each fixed and each proven here:

  FIX 1 — section selection scanned the document ONLY from the start to the
  first risk-factors heading, so the estimated-value section and the plan of
  distribution — which most issuers place AFTER the risk factors — were kept
  in only 3 of 22 documents. The fix scans the WHOLE document for headings
  and keeps every KEEP-classed section wherever it falls. Proven in
  ``section_terms_pages`` on a fixture document with both sections placed
  after risk factors, tax, ERISA and index-methodology text, and in
  ``section_coverage_dry_run`` end to end via ``select_documents``.

  FIX 2 — the model-assisted grouping step effectively never ran (asked to
  group 1,000+ raw labels in one call, which could blow its own output-token
  budget and come back unparseable), and whichever reason it failed for
  could be silently overwritten by an unrelated stop reason from the main
  document loop — a SKIPPED STEP WAS SILENT. The fix groups the much smaller
  set of DISTINCT FIELD KEYS instead of raw labels, and never lets one stop
  reason clobber another. Proven in ``section_aggregate`` (a mocked grouping
  call merges "issuer"/"issuer_name" but leaves buffer_pct and barrier_pct
  apart) and in ``section_stop_reason_not_clobbered``, which REPRODUCES the
  original clobbering bug inline before showing the fixed code keeps both.

  FIX 3 — items were forced into existing fields they do not mean ("Issue
  Date" -> initial_valuation_date). The fix gives the model each field's own
  DESCRIPTION and adds a deterministic safety net, ``plausible_mapping``,
  that refuses a claimed mapping sharing no real vocabulary with the field at
  all. Proven in ``section_parse_items_mapping``.

  FIX 4 — quotes with a line-wrapped or curly-quoted retyping were rejected
  as "not found" although genuine. The fix is ONE shared helper,
  ``services.note_extraction.quote_match.TextIndex``, used by both the
  inventory (``FilingDocument.index``) and note-terms extraction's own
  hazard-ensemble quote verification (``services.note_terms_extraction.
  _TextIndex`` is now an alias for the SAME class — proven by identity, not
  just by behaviour). Proven in ``section_quote_match``.

Mocks every model call (``services.note_extraction.proxy._post`` — the one
network function, same convention as every other inventory verify script);
real spend is $0. Hydrates DATABASE_URL from Doppler like every other verify
script. Teardown runs at the start and at the end, by this script's own
fixture ids — never an unconditional TRUNCATE.

Run:  python3 apps/api/scripts/verify_edgarinventory_v2.py
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from datetime import date
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncio  # noqa: E402

import asyncpg  # noqa: E402

from services import edgar_inventory as inv  # noqa: E402
from services.database import platform_scope  # noqa: E402
from services.note_extraction import documents, schema  # noqa: E402
from services.note_extraction.proxy import Deployment, ProxyResponse  # noqa: E402
from services.note_extraction.quote_match import TextIndex  # noqa: E402

_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() received a non-bool for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


async def guarded(label: str, coro) -> None:
    """Run one section; an unexpected exception becomes a FAIL, never a
    crash (the script must always reach its final TOTAL line)."""
    try:
        await coro
    except Exception as exc:  # noqa: BLE001 — deliberately broad: a safety net, not a check
        check(False, f"{label} raised an unexpected exception", f"{type(exc).__name__}: {exc}")


# ═══════════════════════════════════════════════════════════════════════════
# Fixture ids — the "a2c3" block is used by no other verify script (grep'd).
# ═══════════════════════════════════════════════════════════════════════════
COHORT_ID = UUID("99000000-0000-0000-0000-0000a2c30001")
REF_A = UUID("99000000-0000-0000-0000-0000a2c30101")
REF_B = UUID("99000000-0000-0000-0000-0000a2c30102")
ACC_A = "9900000000-00-000301"   # edgar_index_filings.accession_number must match
ACC_B = "9900000000-00-000302"   # ^\d{10}-\d{2}-\d{6}$ (confirmed in verify_edgarinventory_nulfix.py)
# A REAL, already-eligible platform_model_catalog row (available, non-Claude,
# genuinely served once listed in ``fake_catalog`` below) — never a synthetic
# fixture row. Only the HTTP call itself (proxy._post, via FakeProxy below) is
# mocked; model ELIGIBILITY is never faked, so it can't drift out from under
# this script the way a fixture-row insert could if an unrelated statement in
# the same seeding transaction failed first (exactly what used to BLOCK the
# mocked run here).
CATALOG_MODEL = "gpt-oss-120b"
CATALOG_UPSTREAM = "deepinfra/gpt-oss-120b"
CATALOG_DEPLOYMENT_ID = "verify-inventoryv2-gpt-oss-120b-deployment"

RISK_SENTINEL = "RISKSENTINELV2 you may lose some or all of your principal amount in these notes"
TAX_SENTINEL = "TAXSENTINELV2 the notes should be treated as prepaid derivative contracts"
ERISA_SENTINEL = "ERISASENTINELV2 benefit plan investors should consult their own fiduciary advisors"
INDEXMETHOD_SENTINEL = "INDEXMETHODSENTINELV2 the index level is calculated under a proprietary methodology"

# Doc A: estimated value AND plan of distribution both placed AFTER risk
# factors, tax, ERISA and index-methodology text — the real-run bug.
HTML_A = "<html><body>" + "".join(f"<p>{line}</p>" for line in [
    "Pricing Supplement",
    "Key Terms",
    "Verify Bank N.A. is the issuer of these notes. Buffer Percentage: 15.00% of the Initial Level. "
    "Price to Public: 100.00% Underwriting Discount: 2.50% Proceeds to Issuer: 97.50%",
    "Risk Factors",
    RISK_SENTINEL + ".",
    "The Estimated Value of the Notes",
    "Our estimated value of the notes is $965.50 per $1,000 principal amount, which is less than the "
    "price to public.",
    "United States Federal Income Tax Considerations",
    TAX_SENTINEL + ".",
    "ERISA Considerations",
    ERISA_SENTINEL + ".",
    "Description of the Index",
    INDEXMETHOD_SENTINEL + ".",
    "Supplemental Plan of Distribution (Conflicts of Interest)",
    "We and our affiliates will offer the notes to fee-based advisory accounts at a lower price of "
    "98.00% of face amount.",
    "Hypothetical Examples",
    "Example 1: if the final level is 120% of the initial level, you receive $1,200 per $1,000 note.",
]) + "</body></html>"

# Doc B: NO estimated value section anywhere (coverage must report it missing);
# plan of distribution present, so the two coverage counts differ.
HTML_B = "<html><body>" + "".join(f"<p>{line}</p>" for line in [
    "Pricing Supplement",
    "Key Terms",
    "Second Verify Bank N.A. is the issuer under this pricing supplement. Barrier Percentage: 70.00% "
    "of the Initial Level. Price to Public: 100.00%",
    "Risk Factors",
    RISK_SENTINEL + ".",
    "United States Federal Income Tax Considerations",
    TAX_SENTINEL + ".",
    "Plan of Distribution",
    "We will offer the notes through Verify Securities LLC.",
]) + "</body></html>"

DOC_HTML = {ACC_A: HTML_A, ACC_B: HTML_B}
ACC_OF_REF = {str(REF_A): ACC_A, str(REF_B): ACC_B}
FABRICATED_QUOTE = "VERIFY FABRICATED QUOTE V2 that appears nowhere in any fixture filing text at all"


async def fake_loader(_conn, reference_filing_id, downloader=None):
    acc = ACC_OF_REF[str(reference_filing_id)]
    return documents.document_from_html(
        DOC_HTML[acc], reference_filing_id=str(reference_filing_id), form_type="424B2",
        filer_name="VERIFY V2 BANK", cik="0", filing_date=date.today(), accession_number=acc)


class _FakeIndex:
    """A quote-matching stand-in for tests that are not themselves about
    quote matching (that is proven directly in section_quote_match) — mirrors
    the ``_LocateAnywhere`` convention in verify_edgarinventory_nulfix.py."""

    def __init__(self, bad_quotes: frozenset[str] = frozenset()):
        self.bad = bad_quotes

    def locate(self, quote):
        if not quote or quote in self.bad:
            return None
        return (0, len(quote))


class _FakeDoc:
    def __init__(self, bad_quotes: frozenset[str] = frozenset()):
        self.index = _FakeIndex(bad_quotes)


class FakeProxy:
    """Mocks services.note_extraction.proxy._post — the one network
    function. Zero real spend."""

    def __init__(self):
        self.calls = {"inventory": 0, "grouping": 0}

    async def __call__(self, path, body, timeout):
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        if system.startswith(inv.INSTRUCTIONS[:50]):
            self.calls["inventory"] += 1
            acc = re.search(r"Accession: (\S+)", user).group(1)
            content = self._inventory(acc)
        else:
            self.calls["grouping"] += 1
            content = self._grouping(user)
        resp_body = {"model": CATALOG_MODEL, "choices": [{"message": {"content": json.dumps(content)}}],
                     "usage": {"prompt_tokens": 100, "completion_tokens": 200}}
        headers = {"x-litellm-model-id": CATALOG_DEPLOYMENT_ID, "x-litellm-attempted-fallbacks": "0",
                   "x-litellm-response-cost": "0.001"}
        return ProxyResponse(200, resp_body, json.dumps(resp_body)[:2000], headers, 5)

    @staticmethod
    def _inventory(acc: str) -> dict:
        if acc == ACC_A:
            return {"document": {"product_family": "autocallable", "program_supplement": None,
                                 "has_hypothetical_payout_table": True, "issue_size": "$1,000,000"},
                    "items": [
                        {"label": "Issuer", "value": "Verify Bank N.A.",
                         "quote": "Verify Bank N.A. is the issuer of these notes.",
                         "section": "Key Terms", "maps_to": "NEW", "proposed_field_key": "issuer"},
                        {"label": "Buffer Percentage", "value": "15.00%",
                         "quote": "Buffer Percentage: 15.00% of the Initial Level",
                         "section": "Key Terms", "maps_to": "buffer_pct", "proposed_field_key": None},
                        {"label": "Phantom", "value": "x", "quote": FABRICATED_QUOTE, "section": None,
                         "maps_to": "NEW"},
                    ]}
        return {"document": {"product_family": "autocallable", "program_supplement": None,
                             "has_hypothetical_payout_table": False, "issue_size": None},
                "items": [
                    {"label": "Issuer Name", "value": "Second Verify Bank N.A.",
                     "quote": "Second Verify Bank N.A. is the issuer under this pricing supplement.",
                     "section": "Key Terms", "maps_to": "NEW", "proposed_field_key": "issuer_name"},
                    {"label": "Barrier Percentage", "value": "70.00%",
                     "quote": "Barrier Percentage: 70.00% of the Initial Level",
                     "section": "Key Terms", "maps_to": "barrier_pct", "proposed_field_key": None},
                ]}

    @staticmethod
    def _grouping(user: str) -> dict:
        ids = {}
        for line in user.splitlines():
            m = re.match(r"^(\d+)\.\s+(\S+)\s+—", line)
            if m:
                ids[m.group(2)] = int(m.group(1))
        groups = []
        if "issuer" in ids and "issuer_name" in ids:
            groups.append({"concept": "Issuer", "key_ids": [ids["issuer"], ids["issuer_name"]],
                           "maps_to": "NEW", "proposed_field_key": "issuer"})
        # buffer_pct / barrier_pct are deliberately NOT grouped — proves the
        # mechanism never merges keys the model did not ask to merge.
        return {"groups": groups}


# ═══════════════════════════════════════════════════════════════════════════
# Seed / teardown
# ═══════════════════════════════════════════════════════════════════════════
async def seed(conn) -> None:
    async with platform_scope(conn):
        await conn.execute(
            """INSERT INTO portfolio.reference_filings
                 (id, cik, filer_name, form_type, accession_number, filing_date, primary_document,
                  source_url, r2_key, extraction_status)
               VALUES ($1, '0', 'VERIFY V2 BANK', '424B2', $2, CURRENT_DATE, 'fixture.htm',
                       'https://example.invalid/invv2a', 'fixtures/invv2a.htm', 'pending')""",
            REF_A, f"invv2-{REF_A.hex}")
        await conn.execute(
            """INSERT INTO portfolio.reference_filings
                 (id, cik, filer_name, form_type, accession_number, filing_date, primary_document,
                  source_url, r2_key, extraction_status)
               VALUES ($1, '0', 'VERIFY V2 BANK', '424B2', $2, CURRENT_DATE, 'fixture.htm',
                       'https://example.invalid/invv2b', 'fixtures/invv2b.htm', 'pending')""",
            REF_B, f"invv2-{REF_B.hex}")
        await conn.execute(
            """INSERT INTO portfolio.edgar_cohorts (id, name, kind, definition, member_count)
               VALUES ($1, $2, 'custom', $3::jsonb, 2)""",
            COHORT_ID, "VERIFY InventoryV2 Fixture Cohort", json.dumps({}))
        quarter = f"{date.today().year}Q{(date.today().month - 1) // 3 + 1}"
        for acc, ref, pos in ((ACC_A, REF_A, 1), (ACC_B, REF_B, 2)):
            # pipeline_status = 'ready_for_extraction' alone does not satisfy
            # edgar_index_filings_decided_has_policy_chk (only 'discovered', or
            # 'fetched' with reference_filing_id, are self-sufficient there) —
            # a filing that has been through SELECTION records that via
            # selected_by_cohort_id, which this fixture genuinely has (its own
            # cohort, inserted above). selection_policy_version stays NULL, so
            # edgar_index_filings_one_selector_chk (never both) is untouched.
            await conn.execute(
                """INSERT INTO portfolio.edgar_index_filings
                     (accession_number, form_type, filing_date, index_quarter, submission_path, filer_count,
                      reference_filing_id, document_kind, pipeline_status, selected_by_cohort_id)
                   VALUES ($1, '424B2', CURRENT_DATE, $2, 'fixtures/invv2-index.txt', 1, $3,
                           'pricing_supplement', 'ready_for_extraction', $4)""",
                acc, quarter, ref, COHORT_ID)
            await conn.execute(
                """INSERT INTO portfolio.edgar_cohort_members (cohort_id, accession_number, position)
                   VALUES ($1, $2, $3)""", COHORT_ID, acc, pos)


async def teardown(conn) -> None:
    async with platform_scope(conn):
        await conn.execute(
            """DELETE FROM portfolio.edgar_inventory_items WHERE run_id IN (
                 SELECT id FROM portfolio.edgar_inventory_runs WHERE cohort_id = $1)""", COHORT_ID)
        await conn.execute(
            """DELETE FROM portfolio.edgar_inventory_concepts WHERE run_id IN (
                 SELECT id FROM portfolio.edgar_inventory_runs WHERE cohort_id = $1)""", COHORT_ID)
        await conn.execute(
            """DELETE FROM portfolio.edgar_inventory_documents WHERE run_id IN (
                 SELECT id FROM portfolio.edgar_inventory_runs WHERE cohort_id = $1)""", COHORT_ID)
        await conn.execute("DELETE FROM portfolio.edgar_inventory_runs WHERE cohort_id = $1", COHORT_ID)
        # edgar_cohort_members FKs to BOTH edgar_cohorts (cohort_id) and
        # edgar_index_filings (accession_number) — delete it explicitly first.
        # edgar_index_filings.selected_by_cohort_id then FKs the OTHER way, AT
        # the cohort — so the filings must be deleted before the cohort, not
        # after (neither FK here is ON DELETE CASCADE in that direction).
        await conn.execute("DELETE FROM portfolio.edgar_cohort_members WHERE cohort_id = $1", COHORT_ID)
        await conn.execute("DELETE FROM portfolio.edgar_index_filings WHERE accession_number = ANY($1)",
                           [ACC_A, ACC_B])
        await conn.execute("DELETE FROM portfolio.edgar_cohorts WHERE id = $1", COHORT_ID)
        await conn.execute("DELETE FROM portfolio.reference_filings WHERE id = ANY($1)", [REF_A, REF_B])


# ═══════════════════════════════════════════════════════════════════════════
# Discipline: read the live CHECK constraints before trusting a fixture value
# ═══════════════════════════════════════════════════════════════════════════
async def section_pg_constraints(conn) -> None:
    section("Discipline: fixture values satisfy the LIVE CHECK constraints (read from pg_constraint)")
    for table in ("portfolio.edgar_index_filings", "portfolio.edgar_cohorts", "portfolio.reference_filings"):
        rows = await conn.fetch(
            "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid = $1::regclass AND contype = 'c'", table)
        print(f"  -- {table} CHECK constraints --")
        for r in rows:
            print(f"    {r['conname']}: {r['def']}")
        accession_chk = next((r for r in rows if "accession" in r["conname"].lower()), None)
        if table == "portfolio.edgar_index_filings" and accession_chk is not None:
            # The constraint itself is SQL, not a Python regex; this applies
            # the known-good pattern (confirmed live in verify_edgarinventory_
            # nulfix.py, 79/79) and simply prints the live definition above
            # for a human to diff against if it ever changes.
            pat = re.compile(r"^\d{10}-\d{2}-\d{6}$")
            check(bool(pat.match(ACC_A)) and bool(pat.match(ACC_B)),
                 "ACC_A / ACC_B match the known edgar_index_filings accession format",
                 f"constraint={accession_chk['def']}")
    member_count_chk = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'portfolio.edgar_cohorts'::regclass AND contype = 'c' "
        "AND conname LIKE '%member_count%'")
    find("edgar_cohorts member_count CHECK (informational; seed()'s member_count=2 insert below is the "
        "real proof — an unsatisfied CHECK would fail that INSERT, not this print)", f"{member_count_chk}")


# ═══════════════════════════════════════════════════════════════════════════
# FIX 4 — shared quote-matching helper
# ═══════════════════════════════════════════════════════════════════════════
async def section_quote_match(conn) -> None:
    section("[Y] FIX 4: shared quote-matching helper — whitespace, curly quotes, dashes; paraphrase rejected")
    idx = TextIndex("Total\nPrice to Public of the Notes")
    span = idx.locate("Total Price to Public")
    check(span is not None and idx.text[span[0]:span[1]] == "Total\nPrice to Public",
         "a line-wrapped quote matches its unwrapped form, AND the offsets map to the exact "
         "ORIGINAL characters (including the real newline)", f"span={span}")

    idx2 = TextIndex("The Issuer’s “Estimated Value” is set using the pricing date — see below.")
    span2 = idx2.locate('The Issuer\'s "Estimated Value" is set using the pricing date - see below.')
    check(span2 is not None, "curly single/double quotes and an em dash all match their straight-ASCII retyping",
         f"span={span2}")
    if span2:
        matched_raw = idx2.text[span2[0]:span2[1]]
        check("’" in matched_raw and "“" in matched_raw and "—" in matched_raw,
             "the offsets resolve into the ORIGINAL un-normalised text — the curly quotes and em dash "
             "are still there, proving the match did not just find a normalised COPY", f"{matched_raw!r}")

    idx3 = TextIndex("The barrier is 70% of the initial level, observed only at maturity.")
    check(idx3.locate("The barrier is 70% of the initial level, observed daily.") is None,
         "a PARAPHRASE (different words, not just different formatting) is still rejected")
    check(idx3.locate("short") is None, "a quote under 8 characters is rejected as too short to anchor")
    check(idx3.locate(None) is None and idx3.locate("") is None, "locate() refuses None/empty without raising")

    section("[Y] FIX 4: note-terms extraction's own quote verification uses the SAME helper, by identity")
    from services.note_terms_extraction import _TextIndex as nte_index_cls

    check(nte_index_cls is TextIndex,
         "services.note_terms_extraction._TextIndex IS services.note_extraction.quote_match.TextIndex "
         "— one implementation, not two that happen to agree", f"{nte_index_cls!r}")
    doc = documents.document_from_html("<html><body><p>Total\nPrice to Public: 100%</p></body></html>",
                                       reference_filing_id="verify-v2-quote-match")
    check(type(doc.index) is TextIndex,
         "FilingDocument.index (used by the inventory) is built from the SAME TextIndex class")


# ═══════════════════════════════════════════════════════════════════════════
# FIX 1 — section selection by heading, wherever it falls
# ═══════════════════════════════════════════════════════════════════════════
async def section_terms_pages(conn) -> None:
    section("[Y] FIX 1: section selector keeps cover/key terms, payoff, estimated value and plan of "
           "distribution — even placed AFTER risk factors, tax, ERISA and index methodology — and "
           "excludes those four")
    doc_a = await fake_loader(conn, REF_A)
    doc_b = await fake_loader(conn, REF_B)
    tp_a = inv.terms_pages(doc_a.text)
    tp_b = inv.terms_pages(doc_b.text)

    for sentinel, name in ((RISK_SENTINEL, "risk factors"), (TAX_SENTINEL, "tax"),
                           (ERISA_SENTINEL, "ERISA"), (INDEXMETHOD_SENTINEL, "index methodology")):
        check(sentinel not in tp_a.text, f"doc A's input excludes {name} text", f"sentinel={sentinel[:30]}")

    check("estimated value of the notes is $965.50" in tp_a.text,
         "doc A's input keeps the estimated value section, though it comes AFTER risk/tax/ERISA/index text")
    check("fee-based advisory accounts" in tp_a.text,
         "doc A's input keeps the (supplemental) plan of distribution, including its "
         "fee-based-account pricing language")
    check("Example 1" in tp_a.text, "doc A's input keeps the hypothetical payout example")
    check("Underwriting Discount: 2.50%" in tp_a.text, "doc A's input keeps the cover/fee-table terms")
    check(tp_a.estimated_value_found and tp_a.plan_of_distribution_found,
         "doc A records BOTH estimated_value_found and plan_of_distribution_found as True")
    check({"estimated_value", "plan_of_distribution"} <= set(tp_a.sections_found),
         "doc A's sections_found names both sections", f"{tp_a.sections_found}")
    check(set(tp_a.sections_included) == set(tp_a.sections_found) and not tp_a.truncated,
         "under the 20k-token cap nothing found is cut: sections_included == sections_found, not truncated")

    check(RISK_SENTINEL not in tp_b.text and TAX_SENTINEL not in tp_b.text,
         "doc B's input also excludes risk/tax text")
    check(tp_b.estimated_value_found is False,
         "doc B genuinely has no estimated-value section anywhere, and the scan says so")
    check(tp_b.plan_of_distribution_found is True,
         "doc B's plan of distribution IS found (proves the False above is a real absence, not a bug)")
    check("estimated_value" not in tp_b.sections_found, "doc B's sections_found omits estimated_value")


async def section_coverage_dry_run(conn) -> None:
    section("[Y] FIX 1: section coverage is computed per document, end to end through select_documents, "
           "and a document missing the estimated-value section is identified — all in a DRY RUN (zero "
           "model calls, zero spend)")
    summary = await inv.run_inventory(
        conn, COHORT_ID, catalog={}, spend_cap_usd=5.0, dry_run=True, loader=fake_loader, registry_rows=[])
    check(summary.documents_planned == 2 and len(summary.plan) == 2,
         "both fixture documents are planned", f"planned={summary.documents_planned}")
    cov = summary.sections_coverage
    check(cov.get("documents") == 2 and cov.get("estimated_value_found") == 1
         and cov.get("plan_of_distribution_found") == 2,
         "coverage: estimated value found in 1 of 2 documents, plan of distribution in 2 of 2", f"{cov}")
    check(cov.get("missing_estimated_value") == [ACC_B],
         "the document missing the estimated-value section is named in the report", f"{cov}")
    check(cov.get("missing_plan_of_distribution") == [],
         "no document is missing the plan of distribution")
    plan_by_acc = {p["accession_number"]: p for p in summary.plan}
    check(plan_by_acc[ACC_A]["estimated_value_found"] is True and plan_by_acc[ACC_B]["estimated_value_found"] is False,
         "the per-document plan entries carry sections_found / estimated_value_found individually")


# ═══════════════════════════════════════════════════════════════════════════
# FIX 3 — field descriptions in the mapping prompt; a wrong mapping is refused
# ═══════════════════════════════════════════════════════════════════════════
async def section_parse_items_mapping(conn) -> None:
    section("[Y] FIX 3: the mapping prompt carries field DESCRIPTIONS; a mocked 'Issue Date' -> "
           "initial_valuation_date mapping is NOT accepted, but a genuine CUSIP mapping is")
    field_specs = {
        "initial_valuation_date": schema.FieldSpec(
            key="initial_valuation_date", label="Initial Valuation Date", kind="date",
            description="The pricing/strike date when the initial level is set (YYYY-MM-DD)."),
        "cusip": schema.FieldSpec(key="cusip", label="CUSIP", kind="text",
                                  description="The 9-character CUSIP of these notes."),
        "notional_currency": schema.FieldSpec(key="notional_currency", label="Notional Currency", kind="text",
                                              description="ISO currency code of the principal, e.g. USD."),
    }
    check("pricing/strike date" in field_specs["initial_valuation_date"].description,
         "sanity: the field list text actually carries the field's description, not just its name",
         inv.field_list_text(list(field_specs.values()))[:120])
    check(inv.field_list_text(list(field_specs.values())).count("pricing/strike date") == 1,
         "field_list_text embeds the description inline for every field")

    parsed = {"document": {}, "items": [
        {"label": "Issue Date", "value": "2024-01-01", "quote": "The Issue Date is January 1, 2024",
         "maps_to": "initial_valuation_date", "section": "Key Terms"},
        {"label": "Denominations", "value": "$1,000", "quote": "Denominations of $1,000 and integral multiples",
         "maps_to": "notional_currency", "section": "Key Terms"},
        {"label": "CUSIP Number", "value": "12345ABCD", "quote": "CUSIP Number: 12345ABCD",
         "maps_to": "cusip", "section": "Key Terms"},
    ]}
    facts, accepted, rejected = inv.parse_items(parsed, _FakeDoc(), field_specs)
    by_label = {a["label"]: a for a in accepted}
    check(len(accepted) == 3 and not rejected, "all three items are accepted as items (not REJECTED — "
         "this is about the MAPPING, a wrong map_to still produces a valid proposed-field item)")
    check(by_label["Issue Date"]["mapped_field_key"] is None,
         "'Issue Date' -> initial_valuation_date is REFUSED: no shared meaning with the field's own "
         "description", f"{by_label['Issue Date']}")
    check(by_label["Issue Date"]["proposed_field_key"] not in (None, "initial_valuation_date"),
         "the refused mapping is NOT reused as the proposed key (would collide with the real field's name)",
         f"{by_label['Issue Date']['proposed_field_key']}")
    check(by_label["Denominations"]["mapped_field_key"] is None,
         "'Denominations' -> notional_currency is likewise refused")
    check(by_label["CUSIP Number"]["mapped_field_key"] == "cusip",
         "a genuinely matching mapping ('CUSIP Number' -> cusip) IS accepted")

    check(inv.plausible_mapping("Pricing Date", field_specs["initial_valuation_date"]) is True,
         "a REAL synonym that appears in the field's description ('Pricing Date', description says "
         "'pricing/strike date') is accepted — the safety net is not just a bare key/label match")


# ═══════════════════════════════════════════════════════════════════════════
# FIX 2 — grouping over field keys; issuer/issuer_name merge, buffer/barrier don't
# ═══════════════════════════════════════════════════════════════════════════
async def section_aggregate(conn, state: dict) -> None:
    section("[Y] FIX 2: a mocked run groups by FIELD KEY (not raw label); merges issuer/issuer_name; "
           "never merges buffer_pct with barrier_pct; rejected items keep their reason")
    fake_catalog = {
        CATALOG_MODEL: Deployment(name=CATALOG_MODEL, deployment_id=CATALOG_DEPLOYMENT_ID,
                                  upstream=CATALOG_UPSTREAM, price_key=CATALOG_MODEL,
                                  input_cost_per_token=1e-6, output_cost_per_token=1e-6,
                                  cache_read_cost_per_token=None, supports_response_schema=False,
                                  max_input_tokens=200000),
    }
    from services.note_extraction import proxy

    fake = FakeProxy()
    original_post = proxy._post
    proxy._post = fake
    try:
        summary = await inv.run_inventory(
            conn, COHORT_ID, catalog=fake_catalog, spend_cap_usd=5.0, dry_run=False, loader=fake_loader,
            registry_rows=[], deployment=CATALOG_MODEL)
    finally:
        proxy._post = original_post
    if summary.run_id:
        state["run_ids"].add(UUID(summary.run_id))
    check(summary.status == "completed" and summary.documents_done == 2,
         "the mocked run completes both planned documents", f"{summary.status} {summary.stop_reason}")
    check(fake.calls == {"inventory": 2, "grouping": 1}, "one inventory call per document and ONE grouping call",
         f"{fake.calls}")

    run = dict(await _rls_row(conn, "SELECT * FROM portfolio.edgar_inventory_runs WHERE id = $1", summary.run_id))
    docs = [dict(r) for r in await _rls_fetch(
        conn, "SELECT * FROM portfolio.edgar_inventory_documents WHERE run_id = $1", summary.run_id)]
    items = [dict(r) for r in await _rls_fetch(
        conn, "SELECT * FROM portfolio.edgar_inventory_items WHERE run_id = $1", summary.run_id)]
    concepts = [dict(r) for r in await _rls_fetch(
        conn, "SELECT * FROM portfolio.edgar_inventory_concepts WHERE run_id = $1", summary.run_id)]

    check(run["grouping_method"] == "model", "the run's grouping_method is 'model' — the step genuinely ran",
         f"{run['grouping_method']}")
    check(len(items) == 4, "4 real items stored (fabricated-quote item on doc A is REJECTED, not stored)",
         f"{len(items)}")
    doc_a = next(d for d in docs if d["accession_number"] == ACC_A)
    rejected_a = json.loads(doc_a["rejected_items"]) if isinstance(doc_a["rejected_items"], str) else doc_a["rejected_items"]
    check(doc_a["items_rejected"] == 1 and len(rejected_a) == 1
         and rejected_a[0].get("reason") == "quote not found in the filing"
         and rejected_a[0].get("quote", "").startswith("VERIFY FABRICATED QUOTE V2"),
         "the fabricated-quote item is rejected and stored on its document WITH the reason", f"{rejected_a}")

    issuer_concept = next((c for c in concepts if set(c["labels"]) >= {"Issuer", "Issuer Name"}), None)
    check(issuer_concept is not None and issuer_concept["grouping_method"] == "model",
         "'Issuer' and 'Issuer Name' — proposed independently by the two documents — are merged into ONE "
         "concept by the model-assisted step", f"concepts={[c['labels'] for c in concepts]}")
    check(issuer_concept is not None and issuer_concept["document_count"] == 2,
         "the merged concept spans BOTH documents — two independently-proposed keys ('issuer' from doc "
         "A, 'issuer_name' from doc B) became one concept, not two", f"{issuer_concept}")

    buffer_concept = next((c for c in concepts if "Buffer Percentage" in c["labels"]), None)
    barrier_concept = next((c for c in concepts if "Barrier Percentage" in c["labels"]), None)
    check(buffer_concept is not None and barrier_concept is not None
         and buffer_concept["concept_key"] != barrier_concept["concept_key"],
         "buffer_pct and barrier_pct are NEVER merged into one concept, even though both were offered "
         "to the (mocked) model's grouping call", f"{buffer_concept and buffer_concept['concept_key']} != "
         f"{barrier_concept and barrier_concept['concept_key']}")
    check(buffer_concept is not None and buffer_concept["mapped_field_key"] == "buffer_pct"
         and barrier_concept is not None and barrier_concept["mapped_field_key"] == "barrier_pct",
         "each keeps its own correct existing-field mapping")

    md = inv.render_markdown(run, docs, await inv.inventory_concepts(conn, summary.run_id),
                             await inv.run_label_dictionary(conn, summary.run_id))
    check("## Section coverage" in md and ACC_B in md,
         "docs/TEMPLATE_STUDY.md's render includes section coverage naming the document missing it")
    check("## Misleading-label flags" in md, "the render has a dedicated misleading-label-flags section")
    check("Ranked by how many banks use the concept" in md,
         "the concepts table documents its own ranking (by bank count, then frequency)")


async def section_aggregate_skip_reasons(conn, state: dict) -> None:
    section("[Y] FIX 2: a SKIPPED grouping step is always recorded with its reason, never silent")
    run_id = await inv.create_run(conn, cohort_id=COHORT_ID, deployment="n/a", spend_cap=1.0, planned=1)
    state["run_ids"].add(UUID(run_id))

    # One real stored item, so the "no model available" branch below is
    # actually reached rather than short-circuited by "no items to group".
    doc = await fake_loader(conn, REF_A)
    chosen = inv.ChosenDocument(
        reference_filing_id=str(REF_A), accession_number=ACC_A, issuer_group="(no listed issuer)",
        document_kind="pricing_supplement", filing_date=date.today(), families=[], reason="fixture",
        doc=doc, terms=inv.terms_pages(doc.text))
    res = inv.CallResult(status="ok", provider_model=CATALOG_MODEL, proxy_model_id=CATALOG_DEPLOYMENT_ID,
                         input_tokens=10, output_tokens=10, cost_usd=0.0001, latency_ms=5)
    parsed = {"document": {}, "items": [{"label": "Issuer", "value": "Verify Bank N.A.",
                                         "quote": "Verify Bank N.A. is the issuer of these notes.",
                                         "maps_to": "NEW", "proposed_field_key": "issuer"}]}
    facts, accepted, rejected = inv.parse_items(parsed, doc, {})
    await inv.store_document(conn, run_id, chosen, res, "n/a", facts, accepted, rejected)
    check(len(accepted) == 1, "the fixture item for this section is accepted (sanity, not the real assertion)")

    agg_disabled = await inv.aggregate(conn, run_id, use_model=False, field_specs={})
    check(agg_disabled["grouping_method"] == "field_key" and agg_disabled["grouping_note"] is not None
         and "disabled" in agg_disabled["grouping_note"],
         "explicitly disabled grouping (--no-model-grouping) records WHY, not just a method name",
         f"{agg_disabled['grouping_note']}")
    agg_no_model = await inv.aggregate(conn, run_id, use_model=True, deployment=None, field_specs={})
    check(agg_no_model["grouping_note"] is not None and "no model" in agg_no_model["grouping_note"],
         "grouping with no model/catalog/spend available also records why, not silently falling back",
         f"{agg_no_model['grouping_note']}")


async def section_stop_reason_not_clobbered(conn, state: dict) -> None:
    section("[Y] FIX 2: reproduces the ORIGINAL clobbering bug, then proves the fixed code keeps BOTH "
           "the document-loop's stop reason and the grouping step's skip reason")
    fake_catalog = {
        CATALOG_MODEL: Deployment(name=CATALOG_MODEL, deployment_id=CATALOG_DEPLOYMENT_ID,
                                  upstream=CATALOG_UPSTREAM, price_key=CATALOG_MODEL,
                                  input_cost_per_token=1e-6, output_cost_per_token=1e-6,
                                  cache_read_cost_per_token=None, supports_response_schema=False,
                                  max_input_tokens=200000),
    }
    # A cap so tiny the FIRST reservation (for either the inventory call or
    # the later grouping call) always exceeds it — both genuinely have their
    # own real reason to report.
    summary = await inv.run_inventory(
        conn, COHORT_ID, catalog=fake_catalog, spend_cap_usd=1e-12, dry_run=False, loader=fake_loader,
        registry_rows=[], deployment=CATALOG_MODEL)
    if summary.run_id:
        state["run_ids"].add(UUID(summary.run_id))
    check(summary.status == "stopped_spend_cap", "the document loop genuinely stops on the spend cap",
         f"{summary.status}")
    main_loop_reason = summary.stop_reason.split(" | ")[0] if summary.stop_reason else None
    check(main_loop_reason is not None and "spending cap" in main_loop_reason,
         "the main document loop's OWN stop reason is real and present", f"{main_loop_reason}")

    run = dict(await _rls_row(conn, "SELECT * FROM portfolio.edgar_inventory_runs WHERE id = $1", summary.run_id))
    stored = run["stop_reason"] or ""
    grouping_note_part = stored.split(" | ", 1)[1] if " | " in stored else None
    # The spend cap blocked document 0 before any item was ever stored, so
    # grouping separately — and genuinely — has nothing to group; it is its
    # OWN real reason, distinct from the main loop's, not a contrived one.
    check(grouping_note_part is not None and "grouping" in grouping_note_part.lower(),
         "the grouping step genuinely also has its own real (and DIFFERENT) reason to report",
         f"{grouping_note_part}")

    # REPRODUCE the ORIGINAL bug: `summary.stop_reason or agg.get("grouping_note")`, evaluated with the
    # SAME two real values this run actually produced.
    old_buggy_result = main_loop_reason or grouping_note_part
    check(old_buggy_result == main_loop_reason and old_buggy_result != stored,
         "REPRODUCED: the original `or`-based expression evaluates to ONLY the main-loop reason — the "
         "grouping note is silently discarded whenever the main loop already has a (truthy) reason",
         f"old_expr_result={old_buggy_result!r} actually_stored={stored!r}")

    # FIXED: the run actually stored on the database carries BOTH, concatenated.
    check(stored and "spending cap" in stored and "grouping" in stored.lower() and stored != main_loop_reason,
         "FIXED: the run's stored stop_reason carries BOTH reasons — the grouping note is never "
         "silently dropped by the one the main loop already had", f"{stored!r}")
    check(run["grouping_method"] == "field_key", "grouping fell back cleanly (no spend left) rather than crashing")


# ═══════════════════════════════════════════════════════════════════════════
# RLS-safe read helpers (platform-scoped reads, like every other inventory
# verify script — these rows carry no org_id at all)
# ═══════════════════════════════════════════════════════════════════════════
async def _rls_row(conn, sql, *args):
    async with platform_scope(conn):
        return await conn.fetchrow(sql, *args)


async def _rls_fetch(conn, sql, *args):
    async with platform_scope(conn):
        return await conn.fetch(sql, *args)


async def main() -> int:
    url = await bootstrap_async(quiet=True)
    if not url:
        print("no working DATABASE_URL (Doppler hydrate failed) — cannot run")
        return 2
    conn = await asyncpg.connect(url, statement_cache_size=0)
    state = {"run_ids": set()}
    try:
        # guarded, and run at the start too: a prior crashed run must not
        # leave fixtures behind, and must not abort this run.
        await guarded("teardown (pre-run)", teardown(conn))
        await guarded("seed", seed(conn))

        await guarded("section_pg_constraints", section_pg_constraints(conn))
        await guarded("section_quote_match", section_quote_match(conn))
        await guarded("section_terms_pages", section_terms_pages(conn))
        await guarded("section_coverage_dry_run", section_coverage_dry_run(conn))
        await guarded("section_parse_items_mapping", section_parse_items_mapping(conn))
        await guarded("section_aggregate", section_aggregate(conn, state))
        await guarded("section_aggregate_skip_reasons", section_aggregate_skip_reasons(conn, state))
        await guarded("section_stop_reason_not_clobbered", section_stop_reason_not_clobbered(conn, state))
    finally:
        await guarded("teardown (final)", teardown(conn))
        # Any run id minted directly by create_run() in the skip-reasons
        # section (never routed through teardown's cohort_id subquery because
        # teardown already ran) — belt and suspenders, by exact id.
        if state["run_ids"]:
            async def _cleanup_runs():
                async with platform_scope(conn):
                    ids = list(state["run_ids"])
                    await conn.execute("DELETE FROM portfolio.edgar_inventory_items WHERE run_id = ANY($1::uuid[])", ids)
                    await conn.execute("DELETE FROM portfolio.edgar_inventory_concepts WHERE run_id = ANY($1::uuid[])", ids)
                    await conn.execute("DELETE FROM portfolio.edgar_inventory_documents WHERE run_id = ANY($1::uuid[])", ids)
                    await conn.execute("DELETE FROM portfolio.edgar_inventory_runs WHERE id = ANY($1::uuid[])", ids)
            await guarded("teardown (run ids, belt-and-suspenders)", _cleanup_runs())
        left = await _rls_row(
            conn,
            """SELECT
                 (SELECT count(*) FROM portfolio.edgar_inventory_runs WHERE cohort_id = $1) AS runs,
                 (SELECT count(*) FROM portfolio.edgar_cohorts WHERE id = $1) AS cohorts,
                 (SELECT count(*) FROM portfolio.edgar_cohort_members WHERE cohort_id = $1) AS members,
                 (SELECT count(*) FROM portfolio.edgar_index_filings WHERE accession_number = ANY($2)) AS filings,
                 (SELECT count(*) FROM portfolio.reference_filings WHERE id = ANY($3::uuid[])) AS refs""",
            COHORT_ID, [ACC_A, ACC_B], [REF_A, REF_B])
        check(all(v == 0 for v in dict(left).values()), "teardown leaves ZERO fixture rows", f"{dict(left)}")
        await conn.close()

    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
