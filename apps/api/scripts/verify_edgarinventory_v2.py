"""verify_edgarinventory_v2.py — the INVENTORY v2 fixes in
services/edgar_inventory.py, scripts/run_edgar_inventory.py, and the shared
quote-matching helper services/note_extraction/quote_match.py.

The first real run (17458851-feff-4eaf-a80c-f06b3827f5db) showed four real
gaps, each fixed and each proven here (a fifth, FIX 5, was added later: the
real-run coverage dry run still found the estimated-value section in only
7 of 22 documents — the heading-only scan from FIX 1 misses issuers, mostly
major banks, who state it in a cover-page or key-terms SENTENCE with no
heading, sometimes inside risk factors):

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

  FIX 5 — the estimated value / plan-of-distribution economics stated in a
  SENTENCE, never under a heading of their own (a cover-page sentence, or
  one paragraph inside risk factors), were invisible to a heading-only scan.
  The fix always includes any PARAGRAPH, anywhere, matching a fixed set of
  phrases (``ALWAYS_INCLUDE``) — just that paragraph, not the rest of
  whatever KEEP or DROP section it sits in — and coverage is now measured by
  CONTENT (a figure near the phrase) as well as by heading. Proven in
  ``section_paragraph_level_inclusion`` on a cover-page-only fixture (doc C)
  and a risk-factors-paragraph-only fixture (doc D), where the one matching
  paragraph survives and the other risk-factor paragraphs around it do not.

Run aa1c8c7c-239a-4057-9fb3-2c911c9c2bc5 then showed four more, each fixed
and proven here:

  FIX 6 — 328 quotes rejected: 183 ABRIDGED with an ellipsis, 145 short table
  values whose spacing differs from the extracted text ('$985.78' vs
  '$ 985.78'). The shared matcher now accepts an abridged quote whose
  fragments (>= 12 chars) all occur IN ORDER within ~1,500 characters, and
  falls back to a whitespace-insensitive comparison mapped back to exact
  original offsets; a paraphrase is still rejected. The OLD (v2) matcher is
  reproduced inline and shown to reject the same quotes first. Proven in
  ``section_quote_match_v3``. The prompt now asks for exact, contiguous,
  short quotes with no ellipsis.

  FIX 7 — the grouping call came back 'unusable (ok: None)' after 5,947
  completion tokens: it used its own output limit, not --max-tokens, over
  every key in one call. Grouping now honours max_tokens, is sent in chunks
  (~100 keys), merged across chunks, with every chunk's outcome recorded; a
  failed chunk falls back to field-key grouping for that chunk ONLY, with a
  note. Proven in ``section_aggregate`` (max_tokens reaches every call),
  ``section_grouping_chunks`` (a cross-chunk merge) and
  ``section_grouping_chunk_fallback``.

  FIX 8 — ``--rematch`` promotes a stored run's rejected items that now
  match; ``--regroup`` reruns only grouping. Neither makes a document call.
  Proven through ``run_edgar_inventory.main`` itself in
  ``section_rematch_regroup`` (DOC_PATH redirected to a temp file, so
  docs/TEMPLATE_STUDY.md is never touched).

  FIX 9 — inventory calls were invisible to public.ai_decision_log. Every
  call now writes a row (task_type 'edgar_inventory' /
  'edgar_inventory_grouping'), re-read from an INDEPENDENT connection in
  ``section_decision_log``.

Every fixture value is checked against the LIVE CHECK and FOREIGN KEY
constraints (read from pg_constraint) in ``section_pg_constraints`` before
any fixture row relies on it.

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
import tempfile
from collections import Counter
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
from services.note_extraction import quote_match  # noqa: E402
from services.litellm_credentials import HOLLISWORKS_ORG_ID  # noqa: E402

import run_edgar_inventory as cli  # noqa: E402  (scripts/ is on sys.path via HERE.parent)

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
REF_C = UUID("99000000-0000-0000-0000-0000a2c30103")
REF_D = UUID("99000000-0000-0000-0000-0000a2c30104")
ACC_A = "9900000000-00-000301"   # edgar_index_filings.accession_number must match
ACC_B = "9900000000-00-000302"   # ^\d{10}-\d{2}-\d{6}$ (confirmed in verify_edgarinventory_nulfix.py)
ACC_C = "9900000000-00-000303"   # doc C and D are never inserted into the DB (fake_loader is a plain dict
ACC_D = "9900000000-00-000304"   # lookup, no query) — their accession format still follows the live CHECK
                                 # constraint on the chance a future edit DOES seed them.
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
RUN_MAX_TOKENS = 4321       # distinctive, so "the call carried the run's own value" is unambiguous

RISK_SENTINEL = "RISKSENTINELV2 you may lose some or all of your principal amount in these notes"
TAX_SENTINEL = "TAXSENTINELV2 the notes should be treated as prepaid derivative contracts"
ERISA_SENTINEL = "ERISASENTINELV2 benefit plan investors should consult their own fiduciary advisors"
INDEXMETHOD_SENTINEL = "INDEXMETHODSENTINELV2 the index level is calculated under a proprietary methodology"
# A SECOND risk-factors paragraph, distinct from RISK_SENTINEL, so doc D can
# prove the WHOLE section is dropped except the one always-included
# paragraph — not just that the single paragraph adjacent to it is dropped.
RISK_OTHER_SENTINEL = "RISKOTHERSENTINELV2 you could lose some or all of your investment in these notes"

# Doc A: estimated value AND plan of distribution both placed AFTER risk
# factors, tax, ERISA and index-methodology text — the real-run bug.
HTML_A = "<html><body>" + "".join(f"<p>{line}</p>" for line in [
    "Pricing Supplement",
    "Key Terms",
    "Verify Bank N.A. is the issuer of these notes. Buffer Percentage: 15.00% of the Initial Level. "
    "Price to Public: 100.00% Underwriting Discount: 2.50% Proceeds to Issuer: 97.50%",
    # FIX 6 / FIX 8: a table whose currency symbol sits in its OWN cell — the
    # extracted text reads "$" <newline> "985.78", the model quotes "$985.78".
    "<table><tr><td>Initial Share Price</td><td>$</td><td>985.78</td></tr></table>",
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

# Doc C: NO estimated-value heading anywhere — the real bank pattern, where
# the figure appears only in a cover-page SENTENCE. Coverage must count this
# as found by CONTENT, not by heading.
HTML_C = "<html><body>" + "".join(f"<p>{line}</p>" for line in [
    "Pricing Supplement",
    "Third Verify Bank N.A. is the issuer of these notes. The initial estimated value of the notes is "
    "approximately $9.42 per $10.00 stated principal amount, which is less than the price to public "
    "of $10.00.",
    "Key Terms",
    "Barrier Percentage: 65.00% of the Initial Level.",
    "Risk Factors",
    RISK_SENTINEL + ".",
    "United States Federal Income Tax Considerations",
    TAX_SENTINEL + ".",
    "Plan of Distribution",
    "We will offer the notes through Verify Securities LLC.",
]) + "</body></html>"

# Doc D: the estimated value is stated only INSIDE the risk-factors section
# (no heading of its own, anywhere) — one paragraph among three in that DROP
# section. Only that paragraph should survive; the other two (RISK_SENTINEL,
# RISK_OTHER_SENTINEL) must not.
HTML_D = "<html><body>" + "".join(f"<p>{line}</p>" for line in [
    "Pricing Supplement",
    "Fourth Verify Bank N.A. is the issuer of these notes. Price to Public: 100.00%",
    "Key Terms",
    "Barrier Percentage: 55.00% of the Initial Level.",
    "Risk Factors",
    RISK_SENTINEL + ".",
    "The estimated value of the notes on the pricing date is expected to be between $920 and $970 "
    "per $1,000 face amount, which will be less than the price to public.",
    RISK_OTHER_SENTINEL + ".",
    "United States Federal Income Tax Considerations",
    TAX_SENTINEL + ".",
    "Plan of Distribution",
    "We will offer the notes through Verify Securities LLC.",
]) + "</body></html>"

DOC_HTML = {ACC_A: HTML_A, ACC_B: HTML_B, ACC_C: HTML_C, ACC_D: HTML_D}
ACC_OF_REF = {str(REF_A): ACC_A, str(REF_B): ACC_B, str(REF_C): ACC_C, str(REF_D): ACC_D}
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

    def __init__(self, *, per_key_grouping: bool = False, fail_chunk_with_key: str | None = None):
        self.calls = {"inventory": 0, "grouping": 0}
        self.max_tokens = {"inventory": [], "grouping": []}
        self.grouping_key_lists: list[list[str]] = []
        self.per_key_grouping = per_key_grouping
        self.fail_chunk_with_key = fail_chunk_with_key

    async def __call__(self, path, body, timeout):
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        finish = "stop"
        if system.startswith(inv.INSTRUCTIONS[:50]):
            self.calls["inventory"] += 1
            self.max_tokens["inventory"].append(body.get("max_tokens"))
            acc = re.search(r"Accession: (\S+)", user).group(1)
            content = self._inventory(acc)
        else:
            self.calls["grouping"] += 1
            self.max_tokens["grouping"].append(body.get("max_tokens"))
            ids = self._ids(user)
            self.grouping_key_lists.append(list(ids))
            if self.fail_chunk_with_key and self.fail_chunk_with_key in ids:
                # The real run's shape: HTTP 200, valid JSON, no 'groups' list,
                # cut off at the output limit — reported then as "unusable (ok: None)".
                content, finish = {}, "length"
            elif self.per_key_grouping:
                content = self._grouping_per_key(ids)
            else:
                content = self._grouping(user)
        resp_body = {"model": CATALOG_MODEL,
                     "choices": [{"message": {"content": json.dumps(content)}, "finish_reason": finish}],
                     "usage": {"prompt_tokens": 100, "completion_tokens": 200}}
        # Cost 0: these mocked calls also land in public.ai_decision_log, and
        # a fake non-zero cost there would pollute real spend reporting if a
        # row ever outlived teardown.
        headers = {"x-litellm-model-id": CATALOG_DEPLOYMENT_ID, "x-litellm-attempted-fallbacks": "0",
                   "x-litellm-response-cost": "0"}
        return ProxyResponse(200, resp_body, json.dumps(resp_body)[:2000], headers, 5)

    @staticmethod
    def _ids(user: str) -> dict[str, int]:
        # A key can contain spaces (a label-derived key such as "initial share price").
        ids = {}
        for line in user.splitlines():
            m = re.match(r"^(\d+)\.\s+(.+?)\s+—\s+labels:", line)
            if m:
                ids[m.group(2)] = int(m.group(1))
        return ids

    @staticmethod
    def _grouping_per_key(ids: dict[str, int]) -> dict:
        """One group per key, named so that 'issuer' and 'issuer_name' —
        deliberately placed in DIFFERENT chunks by the caller — carry the same
        concept name and proposed key, which only a cross-chunk merge can join."""
        groups = []
        for key, i in ids.items():
            if key in ("issuer", "issuer_name"):
                groups.append({"concept": "Issuer", "key_ids": [i], "maps_to": "NEW", "proposed_field_key": "issuer"})
            else:
                groups.append({"concept": key.replace("_", " ").title(), "key_ids": [i],
                               "maps_to": key if key.endswith("_pct") else "NEW", "proposed_field_key": None})
        return {"groups": groups}

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
    check("70%" in idx3.text and idx3.locate("70%") is None,
         f"a quote under {quote_match.MIN_QUOTE_CHARS} non-whitespace characters is rejected as too short "
         "to anchor — even though it IS in the text")
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


# ═══════════════════════════════════════════════════════════════════════════
# FIX 5 — paragraph-level inclusion by CONTENT, independent of headings
# ═══════════════════════════════════════════════════════════════════════════
async def section_paragraph_level_inclusion(conn) -> None:
    section("[Y] FIX 5: a cover-page sentence AND a risk-factors paragraph each state the estimated "
           "value with a figure but under NO heading — both are found by CONTENT; the risk-factor "
           "paragraph is included on its own, the rest of that DROP section is not")
    doc_c = await fake_loader(conn, REF_C)
    doc_d = await fake_loader(conn, REF_D)
    tp_c = inv.terms_pages(doc_c.text)
    tp_d = inv.terms_pages(doc_d.text)

    check("estimated_value" not in tp_c.sections_found,
         "doc C has NO estimated-value heading anywhere", f"{tp_c.sections_found}")
    check(tp_c.estimated_value_found_heading is False,
         "doc C's heading-based estimated-value flag is False")
    check(tp_c.estimated_value_found_content is True,
         "doc C's CONTENT-based estimated-value flag is True — the cover-page sentence has a figure "
         "near 'estimated value'")
    check(tp_c.estimated_value_found is True,
         "doc C's overall estimated_value_found is True even though no heading was ever found")
    check("$9.42" in tp_c.text,
         "the cover-page sentence (with its figure) is part of the opening, which is kept whole")

    check("estimated_value" not in tp_d.sections_found,
         "doc D also has NO estimated-value heading anywhere", f"{tp_d.sections_found}")
    check(tp_d.estimated_value_found_heading is False and tp_d.estimated_value_found_content is True,
         "doc D's estimated value is found by CONTENT alone, from inside the (DROP) risk-factors section")
    check("between $920 and $970" in tp_d.text,
         "the ONE risk-factor paragraph stating the estimated value made it into the input")
    check(RISK_SENTINEL not in tp_d.text and RISK_OTHER_SENTINEL not in tp_d.text,
         "the OTHER two paragraphs in that same risk-factors section — one before, one after — did not",
         f"text={tp_d.text[:400]!r}")
    check("estimated_value" in tp_d.always_included_tags,
         "the always-included paragraph is tagged estimated_value on the TermsPages result",
         f"{tp_d.always_included_tags}")


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
    log_mark = len(inv.DECISION_LOG_IDS)
    fail_mark = len(inv.DECISION_LOG_FAILURES)
    try:
        summary = await inv.run_inventory(
            conn, COHORT_ID, catalog=fake_catalog, spend_cap_usd=5.0, dry_run=False, loader=fake_loader,
            registry_rows=[], deployment=CATALOG_MODEL, max_tokens=RUN_MAX_TOKENS)
    finally:
        proxy._post = original_post
    if summary.run_id:
        state["run_ids"].add(UUID(summary.run_id))
    state["decision_logs"]["run"] = list(inv.DECISION_LOG_IDS[log_mark:])
    state["decision_log_failures"] += inv.DECISION_LOG_FAILURES[fail_mark:]
    state["run_id"] = summary.run_id
    check(summary.status == "completed" and summary.documents_done == 2,
         "the mocked run completes both planned documents", f"{summary.status} {summary.stop_reason}")
    check(fake.calls == {"inventory": 2, "grouping": 1},
         "one inventory call per document and ONE grouping call (4 keys, well under one 100-key chunk)",
         f"{fake.calls}")

    section("[Y] FIX 7: the grouping call honours the run's --max-tokens — no separate limit of its own")
    check(fake.max_tokens["grouping"] == [RUN_MAX_TOKENS],
         f"the grouping call was sent max_tokens={RUN_MAX_TOKENS} (the run's own value)",
         f"{fake.max_tokens}")
    check(fake.max_tokens["inventory"] == [RUN_MAX_TOKENS, RUN_MAX_TOKENS],
         "and so was every inventory call — one limit for the whole run", f"{fake.max_tokens}")
    check(not hasattr(inv, "GROUPING_MAX_TOKENS"),
         "the grouping step's own separate output limit no longer exists in the module")
    check(inv.GROUPING_CHUNK_KEYS == 100, "the default grouping chunk is 100 distinct field keys",
         f"{inv.GROUPING_CHUNK_KEYS}")

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
    check(rejected_a and rejected_a[0].get("label") == "Phantom" and rejected_a[0].get("value") == "x"
         and rejected_a[0].get("maps_to") == "NEW",
         "the rejected entry keeps the WHOLE item (value, maps_to …), not just label + quote — so a later "
         "--rematch can promote it intact", f"{rejected_a}")

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


def _fake_catalog() -> dict:
    return {CATALOG_MODEL: Deployment(name=CATALOG_MODEL, deployment_id=CATALOG_DEPLOYMENT_ID,
                                      upstream=CATALOG_UPSTREAM, price_key=CATALOG_MODEL,
                                      input_cost_per_token=1e-6, output_cost_per_token=1e-6,
                                      cache_read_cost_per_token=None, supports_response_schema=False,
                                      max_input_tokens=200000)}


# ═══════════════════════════════════════════════════════════════════════════
# Discipline (v3 fixtures): the LIVE CHECK and FOREIGN KEY constraints on every
# table this script's new fixtures write to, read from pg_constraint
# ═══════════════════════════════════════════════════════════════════════════
V3_TABLES = ("public.ai_decision_log", "portfolio.edgar_inventory_runs", "portfolio.edgar_inventory_documents",
             "portfolio.edgar_inventory_concepts", "portfolio.edgar_inventory_items")
# The value each ai_decision_log column this module writes will carry in this script.
DECISION_LOG_VALUES = {"org_id": [HOLLISWORKS_ORG_ID], "task_type": list(inv.TASK_TYPE.values()),
                       "model_requested": [CATALOG_MODEL], "model_used": [CATALOG_MODEL]}


async def _constraints(conn, table: str) -> list[dict]:
    rows = await conn.fetch(
        """SELECT c.conname, c.contype::text AS contype, pg_get_constraintdef(c.oid) AS def,
                  CASE WHEN c.contype = 'f' THEN c.confrelid::regclass::text END AS reftable,
                  ARRAY(SELECT a.attname FROM unnest(c.conkey) k
                        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k) AS cols,
                  ARRAY(SELECT a.attname FROM unnest(COALESCE(c.confkey, '{}'::smallint[])) k
                        JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k) AS refcols
           FROM pg_constraint c WHERE c.conrelid = $1::regclass AND c.contype IN ('c', 'f')
           ORDER BY c.contype, c.conname""", table)
    return [dict(r) for r in rows]


async def section_pg_constraints_v3(conn) -> None:
    section("Discipline (v3): every new fixture value satisfies the LIVE CHECK and FOREIGN KEY constraints "
            "(read from pg_constraint)")
    cons = {}
    for table in V3_TABLES:
        cons[table] = await _constraints(conn, table)
        print(f"  -- {table} --")
        for c in cons[table]:
            print(f"    [{c['contype']}] {c['conname']}: {c['def']}")

    def checks_on(table, column):
        return [c for c in cons[table] if c["contype"] == "c" and column in c["def"]]

    doc_status = checks_on("portfolio.edgar_inventory_documents", "status")
    check(all("'ok'" in c["def"] for c in doc_status),
         "edgar_inventory_documents: the fixture document status 'ok' is allowed by every CHECK on status",
         f"{[c['def'] for c in doc_status]}")
    concept_method = checks_on("portfolio.edgar_inventory_concepts", "grouping_method")
    check(all("'model'" in c["def"] and "'field_key'" in c["def"] for c in concept_method),
         "edgar_inventory_concepts: both concept grouping methods written ('model', 'field_key') are allowed",
         f"{[c['def'] for c in concept_method]}")
    run_status = checks_on("portfolio.edgar_inventory_runs", "status")
    check(all("'completed'" in c["def"] and "'running'" in c["def"] for c in run_status),
         "edgar_inventory_runs: fixture run statuses 'running' / 'completed' are allowed",
         f"{[c['def'] for c in run_status]}")
    run_method = checks_on("portfolio.edgar_inventory_runs", "grouping_method")
    check(all("'model'" in c["def"] and "'field_key'" in c["def"] for c in run_method),
         "edgar_inventory_runs: the run-level grouping methods written ('model', 'field_key') are allowed "
         "(no CHECK at all is also fine)", f"{[c['def'] for c in run_method] or 'no CHECK on grouping_method'}")

    task_checks = checks_on("public.ai_decision_log", "task_type")
    check(all(all(f"'{t}'" in c["def"] for t in inv.TASK_TYPE.values()) for c in task_checks),
         "ai_decision_log: both task types ('edgar_inventory', 'edgar_inventory_grouping') are allowed by "
         "every CHECK on task_type", f"{[c['def'] for c in task_checks] or 'no CHECK on task_type'}")

    for c in cons["public.ai_decision_log"]:
        if c["contype"] != "f" or len(c["cols"]) != 1:
            continue
        col, refcol = c["cols"][0], c["refcols"][0]
        values = DECISION_LOG_VALUES.get(col)
        if values is None:
            find(f"ai_decision_log FK {c['conname']} on a column this module leaves NULL/default", c["def"])
            continue
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                               "set_config('app.is_super_admin', 'true', true)", HOLLISWORKS_ORG_ID)
            present = [await conn.fetchval(
                f"SELECT EXISTS (SELECT 1 FROM {c['reftable']} WHERE {refcol}::text = $1)", v) for v in values]
        check(all(present), f"ai_decision_log FK {c['conname']} ({col} -> {c['reftable']}.{refcol}): every "
              f"value this module writes exists in the referenced table", f"{dict(zip(values, present))}")

    item_fks = {c["cols"][0] for c in cons["portfolio.edgar_inventory_items"] if c["contype"] == "f"}
    check({"run_id", "document_id"} <= item_fks,
         "edgar_inventory_items FKs run_id/document_id are live — every fixture item below is inserted under a "
         "REAL run and document row created by the code itself (never a hand-picked id)", f"{sorted(item_fks)}")


# ═══════════════════════════════════════════════════════════════════════════
# FIX 6 — abridged quotes and whitespace-insensitive table values
# ═══════════════════════════════════════════════════════════════════════════
def _v2_locate(text: str, quote: str | None):
    """The v2 matcher's behaviour, reproduced inline (min 8 characters; exact;
    whitespace-collapsed + punctuation-folded) — so the ORIGINAL rejection can
    be shown before the fix is."""
    if not quote:
        return None
    s = quote.strip()
    if len(s) < 8:
        return None
    direct = text.find(s)
    if direct != -1:
        return direct, direct + len(s)
    chars, offs, prev = [], [], False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev:
                continue
            chars.append(" "); offs.append(i); prev = True
        else:
            chars.append(quote_match._fold(ch)); offs.append(i); prev = False
    needle = quote_match.normalise(s)
    pos = "".join(chars).find(needle) if needle else -1
    return None if pos == -1 else (offs[pos], offs[pos + len(needle) - 1] + 1)


EV_SENTENCE = ("Our estimated value of the notes is $965.50 per $1,000 principal amount, which is less than "
               "the price to public.")
EV_ABRIDGED = "Our estimated value of the notes ... which is less than the price to public."
EV_PARAPHRASE = "Our estimated value of the notes is lower than the offering price of the notes."


async def section_quote_match_v3(conn) -> None:
    section("[Y] FIX 6: an ABRIDGED quote matches when its fragments occur in order within the window")
    filler = " ".join(["The notes are senior unsecured obligations of the issuer."] * 40)  # ~2,300 chars
    text = f"Preamble. {EV_SENTENCE} {filler} Closing remark about the calculation agent."
    idx = TextIndex(text)

    check(_v2_locate(text, EV_ABRIDGED) is None,
         "REPRODUCED: the v2 matcher rejects the abridged quote (183 of run aa1c8c7c's 328 rejections)")
    span = idx.locate(EV_ABRIDGED)
    check(span is not None and text[span[0]:span[1]] == EV_SENTENCE,
         "FIXED: fragments found IN ORDER — the offsets span exactly from the first fragment's start to the "
         "last fragment's end", f"span={span} text={text[span[0]:span[1]]!r}" if span else "no span")
    uni = "Our estimated value of the notes … which is less than the price to public."
    check(idx.locate(uni) == span, "the single-character ellipsis '…' behaves the same as '...'")
    check(idx.locate("which is less than the price to public ... Our estimated value of the notes") is None,
         "the SAME fragments OUT OF ORDER are rejected — stitching, not abridging")
    far = "Our estimated value of the notes ... Closing remark about the calculation agent."
    check(text.find("Closing remark") - text.find("Our estimated") > quote_match.ELLIPSIS_WINDOW_CHARS
         and idx.locate(far) is None,
         f"two REAL fragments further apart than the {quote_match.ELLIPSIS_WINDOW_CHARS}-character window "
         "are rejected")
    check(idx.locate("Our estimated value of the notes ... which is lower than the offering price.") is None,
         "an abridged quote whose second fragment is PARAPHRASED is rejected")
    check(idx.locate("the notes ... is $965 ... public") is None,
         f"an abridged quote with no fragment of {quote_match.ELLIPSIS_MIN_FRAGMENT_CHARS}+ characters is "
         "rejected (nothing long enough to anchor)")
    check(idx.locate(EV_PARAPHRASE) is None, "a whole-quote PARAPHRASE is still rejected")

    section("[Y] FIX 6: a short table value matches across spacing differences, with exact original offsets")
    t2 = "Initial Share Price: $ 985.78 per share. Coupon Rate: 14.50 % per annum. Ticker: ASML\nUW."
    i2 = TextIndex(t2)
    for quote, raw in (("$985.78", "$ 985.78"), ("14.50%", "14.50 %"), ("ASML UW", "ASML\nUW")):
        old = _v2_locate(t2, quote)
        sp = i2.locate(quote)
        check(old is None and sp is not None and t2[sp[0]:sp[1]] == raw,
             f"{quote!r}: REPRODUCED v2 rejection, FIXED match maps back to exactly {raw!r} in the original "
             "text", f"v2={old} new={sp} -> {t2[sp[0]:sp[1]]!r}" if sp else f"v2={old} new=None")
    check(i2.locate("$985.79") is None, "a DIFFERENT number ('$985.79') is not matched — whitespace removal "
         "never changes a digit")
    check(i2.locate("Initial Share Value: $ 985.78") is None,
         "a paraphrased label around the right value is still rejected")


# ═══════════════════════════════════════════════════════════════════════════
# FIX 7 — chunked grouping, merged across chunks; a failed chunk falls back alone
# ═══════════════════════════════════════════════════════════════════════════
async def _aggregate_with(conn, run_id, fake: FakeProxy, *, chunk_keys: int, max_tokens: int) -> dict:
    from services.note_extraction import proxy
    from services.note_extraction.spend import SpendTracker

    original = proxy._post
    proxy._post = fake
    try:
        return await inv.aggregate(conn, run_id, catalog=_fake_catalog(), deployment=CATALOG_MODEL,
                                   spend=SpendTracker(cap_usd=1.0), use_model=True, field_specs={},
                                   max_tokens=max_tokens, chunk_keys=chunk_keys)
    finally:
        proxy._post = original


async def _concepts(conn, run_id) -> list[dict]:
    return [dict(r) for r in await _rls_fetch(
        conn, "SELECT * FROM portfolio.edgar_inventory_concepts WHERE run_id = $1", run_id)]


async def section_grouping_chunks(conn, state: dict) -> None:
    section("[Y] FIX 7: grouping is sent in chunks with the run's max_tokens and merged ACROSS chunks")
    run_id = state.get("run_id")
    if not run_id:
        check(False, "section_aggregate's run exists (prerequisite)")
        return
    fake = FakeProxy(per_key_grouping=True)
    mark = len(inv.DECISION_LOG_IDS)
    agg = await _aggregate_with(conn, run_id, fake, chunk_keys=3, max_tokens=2222)
    state["decision_logs"]["chunks"] = list(inv.DECISION_LOG_IDS[mark:])
    keys = fake.grouping_key_lists
    check(fake.calls["grouping"] == 2 and [len(k) for k in keys] == [3, 1],
         "4 distinct keys at 3 per chunk -> exactly 2 grouping calls of 3 and 1 keys", f"{keys}")
    check(any("issuer" in k for k in keys) and not any("issuer" in k and "issuer_name" in k for k in keys),
         "precondition: 'issuer' and 'issuer_name' landed in DIFFERENT chunks — no single call saw both",
         f"{keys}")
    check(fake.max_tokens["grouping"] == [2222, 2222], "every chunk's call carried the given max_tokens",
         f"{fake.max_tokens['grouping']}")
    chunks = agg["grouping_chunks"]
    check(len(chunks) == 2 and all(o["status"] == "model" for o in chunks)
         and all(o.get("call_id") and o.get("output_tokens") == 200 for o in chunks),
         "each chunk's outcome is recorded (status, call id, tokens)", f"{chunks}")
    check(agg["grouping_method"] == "model" and agg["grouping_note"] is None,
         "all chunks usable -> method 'model', no fallback note", f"{agg['grouping_note']}")

    concepts = await _concepts(conn, run_id)
    issuer = [c for c in concepts if {"Issuer", "Issuer Name"} & set(c["labels"])]
    check(len(issuer) == 1 and set(issuer[0]["labels"]) >= {"Issuer", "Issuer Name"}
         and issuer[0]["document_count"] == 2,
         "MERGED across chunks: 'Issuer' (key 'issuer', chunk 1) and 'Issuer Name' (key 'issuer_name', "
         "chunk 2) are ONE concept spanning both documents", f"{[c['labels'] for c in concepts]}")
    unmerged = inv.group_keys([], [g for chunk in ([{"concept": "Issuer", "key_ids": [0]}],
                                                   [{"concept": "Issuer", "key_ids": [1]}]) for g in chunk],
                              ["issuer", "issuer_name"])
    check(len(unmerged) == 2,
         "control: WITHOUT the cross-chunk merge the same two per-chunk answers stay two concepts — the merge "
         "is what joins them", f"{[g['keys'] for g in unmerged]}")
    buf = next((c for c in concepts if "Buffer Percentage" in c["labels"]), None)
    bar = next((c for c in concepts if "Barrier Percentage" in c["labels"]), None)
    check(buf is not None and bar is not None and buf["id"] != bar["id"],
         "buffer_pct and barrier_pct (same chunk, different concepts and fields) are still never merged")
    same_chunk = inv.merge_chunk_groups([[{"concept": "X", "key_ids": [0]}, {"concept": "X", "key_ids": [1]}]])
    check(len(same_chunk) == 2,
         "two groups from the SAME chunk are never joined by the merge step — that was the model's own call")


async def section_grouping_chunk_fallback(conn, state: dict) -> None:
    section("[Y] FIX 7: a failed chunk falls back to field-key grouping for THAT chunk only, with a note")
    run_id = state.get("run_id")
    if not run_id:
        check(False, "section_aggregate's run exists (prerequisite)")
        return
    fake = FakeProxy(per_key_grouping=True, fail_chunk_with_key="issuer_name")
    mark = len(inv.DECISION_LOG_IDS)
    agg = await _aggregate_with(conn, run_id, fake, chunk_keys=3, max_tokens=2222)
    state["decision_logs"]["fallback"] = list(inv.DECISION_LOG_IDS[mark:])
    chunks = agg["grouping_chunks"]
    check([o["status"] for o in chunks] == ["model", "fallback"],
         "chunk 1 grouped by the model, chunk 2 (empty answer, cut off) fell back", f"{chunks}")
    note = agg["grouping_note"] or ""
    old_note = f"model grouping unusable (ok: None); grouped by field key only"
    check("ok: None" in old_note and "ok: None" not in note,
         "REPRODUCED the old opaque note ('unusable (ok: None)'); the new note no longer says only that")
    check("chunk 2 of 2" in note and "no 'groups' list" in note and "finish_reason=length" in note
         and "for those chunks only" in note,
         "the note names the failed chunk, WHAT the answer lacked, that it was cut off, and that only that "
         "chunk fell back", note)
    check(agg["grouping_method"] == "model", "the run-level method stays 'model' — the other chunk was grouped")
    concepts = await _concepts(conn, run_id)
    by_label = {lab: c for c in concepts for lab in c["labels"]}
    check(by_label.get("Issuer Name", {}).get("grouping_method") == "field_key"
         and by_label.get("Issuer", {}).get("grouping_method") == "model"
         and by_label.get("Buffer Percentage", {}).get("grouping_method") == "model",
         "the failed chunk's key is a field_key concept; the good chunk's keys keep their model grouping",
         f"{ {k: v['grouping_method'] for k, v in by_label.items()} }")

    fake_all = FakeProxy(per_key_grouping=True, fail_chunk_with_key="issuer")
    agg_all = await _aggregate_with(conn, run_id, fake_all, chunk_keys=100, max_tokens=2222)
    state["decision_logs"]["fallback"] = list(inv.DECISION_LOG_IDS[mark:])
    check(agg_all["grouping_method"] == "field_key" and "grouped by field key only" in (agg_all["grouping_note"] or ""),
         "when EVERY chunk fails, the whole run falls back to field_key, and the note says so",
         f"{agg_all['grouping_note']}")


# ═══════════════════════════════════════════════════════════════════════════
# FIX 8 — --rematch and --regroup through the CLI itself, no document calls
# ═══════════════════════════════════════════════════════════════════════════
async def section_rematch_regroup(conn, state: dict, tmp_doc: pathlib.Path) -> None:
    from services.note_extraction import proxy

    section("[Y] FIX 8: --rematch promotes stored rejected items that now match — no model, no inventory call")
    rid = await inv.create_run(conn, cohort_id=COHORT_ID, deployment=CATALOG_MODEL, spend_cap=1.0, planned=1)
    state["run_ids"].add(UUID(rid))
    doc = await fake_loader(conn, REF_A)
    at = doc.text.find("985.78")
    table_raw = doc.text[doc.text.rfind("$", 0, at):at + len("985.78")] if at > 0 else ""
    check("$985.78" not in doc.text and table_raw.startswith("$") and any(ch.isspace() for ch in table_raw),
         "precondition: the extracted table text separates '$' and '985.78' with whitespace", f"{table_raw!r}")
    check(_v2_locate(doc.text, "$985.78") is None and _v2_locate(doc.text, EV_ABRIDGED) is None,
         "REPRODUCED: the v2 matcher rejects both the table value and the abridged quote on this document")

    chosen = inv.ChosenDocument(
        reference_filing_id=str(REF_A), accession_number=ACC_A, issuer_group="(no listed issuer)",
        document_kind="pricing_supplement", filing_date=date.today(), families=[], reason="fixture",
        doc=doc, terms=inv.terms_pages(doc.text))
    res = inv.CallResult(status="ok", provider_model=CATALOG_MODEL, proxy_model_id=CATALOG_DEPLOYMENT_ID,
                         input_tokens=10, output_tokens=10, cost_usd=0.0, latency_ms=5)
    _f, accepted, _r = inv.parse_items({"items": [{"label": "Issuer", "value": "Verify Bank N.A.",
                                                    "quote": "Verify Bank N.A. is the issuer of these notes.",
                                                    "maps_to": "NEW", "proposed_field_key": "issuer"}]}, doc, {})
    rejected = [
        # legacy shape (what run aa1c8c7c stored): label + quote + reason only
        {"label": "Initial Share Price", "quote": "$985.78", "reason": inv.QUOTE_NOT_FOUND},
        # current shape: the whole item
        {"label": "Estimated Value", "quote": EV_ABRIDGED, "reason": inv.QUOTE_NOT_FOUND, "value": "$965.50",
         "section": "The Estimated Value of the Notes", "maps_to": "NEW", "proposed_field_key": "estimated_value",
         "misleading_label": False, "misleading_note": None},
        {"label": "Phantom", "quote": FABRICATED_QUOTE, "reason": inv.QUOTE_NOT_FOUND},
        {"label": "Estimated Value (paraphrase)", "quote": EV_PARAPHRASE, "reason": inv.QUOTE_NOT_FOUND},
        {"label": "No Quote Item", "quote": None, "reason": "no quote"},
    ]
    await inv.store_document(conn, rid, chosen, res, CATALOG_MODEL, {}, accepted, rejected)
    await inv.finish_run(conn, rid, status="completed", documents_done=1, items_accepted=len(accepted),
                         items_rejected=len(rejected))

    loads = {"n": 0}

    async def counting_loader(c, ref, downloader=None):
        loads["n"] += 1
        return await fake_loader(c, ref)

    fake = FakeProxy()
    original_post, original_doc_path = proxy._post, cli.DOC_PATH
    proxy_calls_before = dict(proxy.CALLS)
    log_mark = len(inv.DECISION_LOG_IDS)
    proxy._post = fake
    cli.DOC_PATH = tmp_doc
    try:
        rc = await cli.main(["--rematch", rid], catalog=_fake_catalog(), loader=counting_loader)
        state["decision_logs"]["rematch"] = list(inv.DECISION_LOG_IDS[log_mark:])
        conn2 = state["conn2"]
        items = await _rls_fetch(conn2, "SELECT * FROM portfolio.edgar_inventory_items WHERE run_id = $1", rid)
        by_label = {r["label"]: dict(r) for r in items}
        check(rc == 0 and fake.calls == {"inventory": 0, "grouping": 0} and dict(proxy.CALLS) == proxy_calls_before,
             "--rematch exits 0 and makes ZERO model calls of any kind", f"rc={rc} {fake.calls}")
        check(loads["n"] == 1, "it loaded the stored filing text once (the one document with candidates)",
             f"{loads['n']}")
        tbl = by_label.get("Initial Share Price")
        check(tbl is not None and doc.text[tbl["quote_char_start"]:tbl["quote_char_end"]] == table_raw,
             "the legacy-shaped '$985.78' item is PROMOTED, its offsets covering exactly the original "
             "'$<whitespace>985.78' (re-read on an independent connection)",
             f"{tbl and doc.text[tbl['quote_char_start']:tbl['quote_char_end']]!r}")
        ev = by_label.get("Estimated Value")
        check(ev is not None and doc.text[ev["quote_char_start"]:ev["quote_char_end"]] == EV_SENTENCE
             and ev["value_text"] == "$965.50" and ev["proposed_field_key"] == "estimated_value"
             and ev["section"] == "The Estimated Value of the Notes",
             "the abridged full-shape item is PROMOTED intact (value, section, proposed key kept) with offsets "
             "spanning first to last fragment", f"{ev and {k: ev[k] for k in ('value_text', 'proposed_field_key')}}")
        check(tbl is not None and tbl["value_text"] is None and tbl["mapped_field_key"] is None,
             "a legacy entry with no stored value/mapping is promoted WITHOUT a guessed one")
        check(len(items) == 3 and "Phantom" not in by_label and "Estimated Value (paraphrase)" not in by_label,
             "the fabricated and the paraphrased quotes are NOT promoted", f"{sorted(by_label)}")
        drow = await _rls_row(conn2, "SELECT * FROM portfolio.edgar_inventory_documents WHERE run_id = $1", rid)
        left = json.loads(drow["rejected_items"]) if isinstance(drow["rejected_items"], str) else drow["rejected_items"]
        check(drow["items_accepted"] == 3 and drow["items_rejected"] == 3
             and {r.get("label") for r in left} == {"Phantom", "Estimated Value (paraphrase)", "No Quote Item"},
             "the document row now lists only the 3 still-rejected items (the 'no quote' one untouched) and its "
             "counters moved by 2", f"accepted={drow['items_accepted']} rejected={drow['items_rejected']}")
        rrow = await _rls_row(conn2, "SELECT * FROM portfolio.edgar_inventory_runs WHERE id = $1", rid)
        report = json.loads(rrow["report"]) if isinstance(rrow["report"], str) else rrow["report"]
        check(rrow["items_accepted"] == 3 and rrow["items_rejected"] == 3
             and len(report.get("rematches") or []) == 1 and report["rematches"][0]["promoted"] == 2,
             "the run's counters moved by 2 and the rematch is recorded on its report", f"{report.get('rematches')}")
        check(tmp_doc.exists() and "Template Study" in tmp_doc.read_text(),
             "the report was written to the redirected path (docs/TEMPLATE_STUDY.md untouched)")
        check(state["decision_logs"]["rematch"] == [], "--rematch wrote no ai_decision_log row (it made no call)")

        rc2 = await cli.main(["--rematch", rid], catalog=_fake_catalog(), loader=counting_loader)
        n_items = await _rls_row(conn2, "SELECT count(*) AS n FROM portfolio.edgar_inventory_items WHERE run_id = $1", rid)
        check(rc2 == 0 and n_items["n"] == 3, "a second --rematch promotes nothing more (idempotent)",
             f"items={n_items['n']}")

        section("[Y] FIX 8: --regroup reruns ONLY grouping and rewrites the run's concepts and report")
        rc3 = await cli.main(["--regroup", rid, "--no-model-grouping"], catalog=_fake_catalog(), loader=counting_loader)
        before = await _concepts(conn2, rid)
        rrow = await _rls_row(conn2, "SELECT * FROM portfolio.edgar_inventory_runs WHERE id = $1", rid)
        check(rc3 == 0 and before and all(c["grouping_method"] == "field_key" for c in before)
             and "disabled" in (rrow["stop_reason"] or ""),
             "setup: a --no-model-grouping regroup leaves field_key concepts and its own 'disabled' note",
             f"{rrow['stop_reason']}")

        loads["n"] = 0
        fake2 = FakeProxy(per_key_grouping=True)
        proxy._post = fake2
        mark = len(inv.DECISION_LOG_IDS)
        rc4 = await cli.main(["--regroup", rid, "--spend-cap", "1", "--model", CATALOG_MODEL, "--max-tokens", "2345",
                              "--grouping-chunk-keys", "100"], catalog=_fake_catalog(), loader=counting_loader)
        state["decision_logs"]["regroup"] = list(inv.DECISION_LOG_IDS[mark:])
        after = await _concepts(conn2, rid)
        rrow = await _rls_row(conn2, "SELECT * FROM portfolio.edgar_inventory_runs WHERE id = $1", rid)
        report = json.loads(rrow["report"]) if isinstance(rrow["report"], str) else rrow["report"]
        unassigned = await _rls_row(conn2, "SELECT count(*) AS n FROM portfolio.edgar_inventory_items "
                                           "WHERE run_id = $1 AND concept_id IS NULL", rid)
        check(rc4 == 0 and loads["n"] == 0 and fake2.calls["inventory"] == 0 and fake2.calls["grouping"] == 1,
             "--regroup made NO document load and NO inventory call — one grouping call only",
             f"rc={rc4} loads={loads['n']} calls={fake2.calls}")
        check(fake2.max_tokens["grouping"] == [2345], "the regroup call carried --max-tokens 2345",
             f"{fake2.max_tokens}")
        check(after and not ({c["id"] for c in after} & {c["id"] for c in before})
             and all(c["grouping_method"] == "model" for c in after),
             "the run's concepts were REWRITTEN (no old concept id survives) and are now model-grouped",
             f"before={len(before)} after={len(after)}")
        check(unassigned["n"] == 0, "every item — including the two promoted by --rematch — now has a concept")
        check(rrow["grouping_method"] == "model" and "disabled" not in (rrow["stop_reason"] or ""),
             "the run's grouping_method is 'model' and the OLD grouping note was replaced, not appended",
             f"{rrow['stop_reason']!r}")
        check(len(report.get("regroups") or []) == 2 and len(report.get("concepts") or []) == len(after)
             and [o["status"] for o in report.get("grouping_chunks") or []] == ["model"]
             and len(report.get("rematches") or []) == 2,
             "the report was rewritten (concepts, chunk outcomes) and keeps its history (2 rematches, 2 regroups)",
             f"regroups={len(report.get('regroups') or [])} concepts={len(report.get('concepts') or [])}")
    finally:
        proxy._post = original_post
        cli.DOC_PATH = original_doc_path


# ═══════════════════════════════════════════════════════════════════════════
# FIX 9 — every inventory call is in public.ai_decision_log
# ═══════════════════════════════════════════════════════════════════════════
async def _decision_rows(conn, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                           "set_config('app.is_super_admin', 'true', true)", HOLLISWORKS_ORG_ID)
        rows = await conn.fetch("SELECT id, org_id, task_type, model_requested, model_used, success, error_detail, "
                                "cost_usd FROM ai_decision_log WHERE id = ANY($1::uuid[])", ids)
    return [dict(r) for r in rows]


async def section_decision_log(conn, state: dict) -> None:
    section("[Y] FIX 9: inventory and grouping calls appear in public.ai_decision_log (independent connection)")
    conn2 = state["conn2"]
    check(state["decision_log_failures"] == [] and inv.DECISION_LOG_FAILURES == [],
         "no decision-log write failed (the writer is non-blocking, so a failure would otherwise be silent)",
         f"{inv.DECISION_LOG_FAILURES}")
    run_rows = await _decision_rows(conn2, state["decision_logs"].get("run") or [])
    by_type = Counter(r["task_type"] for r in run_rows)
    check(by_type == Counter({"edgar_inventory": 2, "edgar_inventory_grouping": 1}),
         "the mocked run wrote exactly 2 'edgar_inventory' rows (one per document) and 1 "
         "'edgar_inventory_grouping' row", f"{dict(by_type)}")
    check(run_rows and all(str(r["org_id"]) == HOLLISWORKS_ORG_ID and r["model_requested"] == CATALOG_MODEL
                           and r["model_used"] == CATALOG_MODEL and r["success"] is True for r in run_rows),
         "each row carries the platform org, the requested and provider-reported model, and success=true")
    fb = await _decision_rows(conn2, state["decision_logs"].get("fallback") or [])
    failed = [r for r in fb if r["success"] is False]
    check(len(fb) == 3 and len(failed) == 2 and all(r["task_type"] == "edgar_inventory_grouping" for r in fb),
         "the fallback scenarios' grouping calls are all logged: 1 usable chunk success=true, the 2 unusable "
         "ones success=false (an HTTP-200 answer with no groups is logged as the failure it was)",
         f"{[(r['task_type'], r['success']) for r in fb]}")
    check(failed and all("groups" in (r["error_detail"] or "") for r in failed)
         and all(r["error_detail"] is None for r in fb if r["success"]),
         "each failed row's error_detail says why (no 'groups' list); successful rows carry none",
         f"{[r['error_detail'] for r in failed]}")
    rg = await _decision_rows(conn2, state["decision_logs"].get("regroup") or [])
    check(len(rg) == 1 and rg[0]["task_type"] == "edgar_inventory_grouping",
         "--regroup's one grouping call is logged as 'edgar_inventory_grouping'", f"{len(rg)}")


async def teardown_decision_logs(conn, state: dict) -> None:
    ids = [i for v in state["decision_logs"].values() for i in v]
    if not ids:
        return
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                               "set_config('app.is_super_admin', 'true', true)", HOLLISWORKS_ORG_ID)
            await conn.execute("DELETE FROM ai_decision_log WHERE id = ANY($1::uuid[])", ids)
    except Exception as exc:  # noqa: BLE001 — reported below
        find("ai_decision_log fixture DELETE raised", f"{type(exc).__name__}: {exc}")
    left = await _decision_rows(conn, ids)
    if left:
        policies = await conn.fetch("SELECT policyname, cmd FROM pg_policies WHERE tablename = 'ai_decision_log'")
        find(f"{len(left)} of {len(ids)} mocked ai_decision_log rows could not be deleted by this role "
             f"(cost_usd 0, model {CATALOG_MODEL}, org {HOLLISWORKS_ORG_ID}) — the table's policies:",
             f"{[(p['policyname'], p['cmd']) for p in policies]}; ids={[str(r['id']) for r in left]}")
    else:
        check(True, f"all {len(ids)} mocked ai_decision_log rows deleted by exact id")


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
    # A SECOND, independent connection: writes made through `conn` (and the
    # CLI's own connection) are re-read here, never from the writer itself.
    conn2 = await asyncpg.connect(url, statement_cache_size=0)
    tmp_dir = tempfile.TemporaryDirectory(prefix="verify_invv3_")
    tmp_doc = pathlib.Path(tmp_dir.name) / "TEMPLATE_STUDY.md"
    state = {"run_ids": set(), "decision_logs": {}, "decision_log_failures": [], "conn2": conn2}
    try:
        # guarded, and run at the start too: a prior crashed run must not
        # leave fixtures behind, and must not abort this run.
        await guarded("teardown (pre-run)", teardown(conn))
        await guarded("seed", seed(conn))

        await guarded("section_pg_constraints", section_pg_constraints(conn))
        await guarded("section_quote_match", section_quote_match(conn))
        await guarded("section_terms_pages", section_terms_pages(conn))
        await guarded("section_paragraph_level_inclusion", section_paragraph_level_inclusion(conn))
        await guarded("section_coverage_dry_run", section_coverage_dry_run(conn))
        await guarded("section_parse_items_mapping", section_parse_items_mapping(conn))
        await guarded("section_aggregate", section_aggregate(conn, state))
        await guarded("section_aggregate_skip_reasons", section_aggregate_skip_reasons(conn, state))
        await guarded("section_stop_reason_not_clobbered", section_stop_reason_not_clobbered(conn, state))
        await guarded("section_pg_constraints_v3", section_pg_constraints_v3(conn))
        await guarded("section_quote_match_v3", section_quote_match_v3(conn))
        await guarded("section_grouping_chunks", section_grouping_chunks(conn, state))
        await guarded("section_grouping_chunk_fallback", section_grouping_chunk_fallback(conn, state))
        await guarded("section_rematch_regroup", section_rematch_regroup(conn, state, tmp_doc))
        await guarded("section_decision_log", section_decision_log(conn, state))
    finally:
        await guarded("teardown (decision log rows, by exact id)", teardown_decision_logs(conn, state))
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
        await conn2.close()
        tmp_dir.cleanup()

    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
