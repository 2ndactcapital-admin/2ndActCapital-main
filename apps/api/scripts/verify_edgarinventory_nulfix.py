"""verify_edgarinventory_nulfix.py — the NUL-stripping fix in
services/edgar_inventory.py and services/note_extraction (readings, staging,
gold).

A real run crashed with ``asyncpg.exceptions.UntranslatableCharacterError:
\\u0000 cannot be converted to text`` — that is Postgres's jsonb input
function refusing the SIX-CHARACTER JSON ESCAPE SEQUENCE ``\\u0000`` (not a
raw NUL byte) because it cannot be converted to the server's text
representation. A genuinely raw NUL byte (``\\x00``) handed to a plain
``text`` column is a DIFFERENT failure — Postgres's encoding checker rejects
it as an invalid byte sequence, raising
``asyncpg.exceptions.CharacterNotInRepertoireError`` instead. Both are real,
both come from model output (a filing's raw bytes echoed back, or a model
re-serializing a NUL into its JSON answer), and both must be stripped before
anything is written — this script proves each separately, by its own real
exception type, and then proves every real write path carries BOTH forms
through without crashing and without a NUL surviving the round trip. This
script:

  1. reproduces the ORIGINAL crash — an unsanitized ``\\u0000`` JSON escape
     written to a jsonb column raises ``UntranslatableCharacterError`` — and,
     separately, proves a raw NUL byte in a plain text column raises the
     DIFFERENT ``CharacterNotInRepertoireError``, so neither case is confused
     with the other
  2. proves ``services.note_extraction.sanitize`` strips a NUL from a string
     and from every string nested inside a dict/list, and that
     ``edgar_inventory._str_or_none`` / ``parse_items`` do the same on a raw
     (never-stripped) model response
  3. proves the real write paths — ``edgar_inventory.store_document`` (the
     inventory documents + items tables), ``store.insert_readings``
     (readings), ``store.insert_staging`` (staging + staged fields), and
     ``gold.record_gold_value`` (the human gold path) — now carry a NUL
     through safely in BOTH forms: a raw NUL already sitting in a Python
     string, and a NUL that arrives as a ``\\u0000`` escape inside a JSON
     response text and is only realised as an actual NUL character once the
     code's own real parser (``json.loads`` for the inventory call,
     ``schema.parse_reader_output`` — the real pydantic parser the readers
     use — for a reading) decodes it. Every case round-trips from the
     database afterwards and is checked for a surviving NUL.

Hydrates secrets from Doppler over HTTPS at startup, like every other verify
script. Teardown is by the fixture's own ids (or, for the run-scoped tables
where the real write functions mint their own ids, by the fixture's
``created_by`` / ``cohort_id`` markers) — never a broad delete.

Run:  python3 apps/api/scripts/verify_edgarinventory_nulfix.py
"""
from __future__ import annotations

import json
import pathlib
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
from services.note_extraction import gold, store  # noqa: E402
from services.note_extraction.sanitize import strip_nul, strip_nul_deep  # noqa: E402
from services.note_extraction.schema import FieldSpec, KIND_TEXT, parse_reader_output  # noqa: E402

NUL = "\x00"
ESCAPE = "\\u0000"
# the "c0de" fixture block is used by no other verify script (grep'd).
ORG = UUID("99000000-0000-0000-0000-0000c0de0001")
U_REVIEWER = UUID("99000000-0000-0000-0000-0000c0de0011")
F_FILING = UUID("99000000-0000-0000-0000-0000c0de0101")
F_FILING2 = UUID("99000000-0000-0000-0000-0000c0de0102")
COHORT_ID = UUID("99000000-0000-0000-0000-0000c0de0201")
# edgar_cohort_members.accession_number FKs to edgar_index_filings, not
# reference_filings — must match edgar_index_filings_accession_format_chk
# (^\d{10}-\d{2}-\d{6}$), so it can't reuse F_FILING's own "nulfix-<hex>"
# accession_number (that format is only legal on reference_filings).
COHORT_MEMBER_ACCESSION = "9900000000-00-000201"

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


async def guarded(label: str, coro) -> None:
    """Run one section; an unexpected exception becomes a FAIL, never a crash
    (the script must always reach its final TOTAL line)."""
    try:
        await coro
    except Exception as exc:  # noqa: BLE001 — deliberately broad: a safety net, not a check
        check(False, f"{label} raised an unexpected exception", f"{type(exc).__name__}: {exc}")


class _LocateAnywhere:
    """A stand-in for ``FilingDocument.index`` — ``parse_items`` only calls
    ``.locate(quote)`` to turn an accepted quote into a char span; what span
    it gets back is irrelevant to NUL-stripping, so always accept."""

    def locate(self, quote):
        return (0, len(quote)) if quote else None


class _FakeFilingDoc:
    index = _LocateAnywhere()


async def seed(conn) -> None:
    async with platform_scope(conn):
        await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                           ORG, "NULFIX Fixture Org", f"{ORG}-nulfix")
        await conn.execute(
            """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
               VALUES ($1, $2, $3, $4, $5, 'member', true)""",
            U_REVIEWER, ORG, "nulfix-reviewer@test.local", "nulfix_reviewer", "nulfix_reviewer")
        await conn.execute(
            """INSERT INTO portfolio.reference_filings
                 (id, cik, filer_name, form_type, accession_number, filing_date, primary_document,
                  source_url, r2_key, extraction_status)
               VALUES ($1, '0', 'NULFIX VERIFY FIXTURE ISSUER', '424B2', $2, CURRENT_DATE, 'fixture.htm',
                       'https://example.invalid/nulfix', 'fixtures/nulfix.htm', 'pending')""",
            F_FILING, f"nulfix-{F_FILING.hex}")
        await conn.execute(
            """INSERT INTO portfolio.reference_filings
                 (id, cik, filer_name, form_type, accession_number, filing_date, primary_document,
                  source_url, r2_key, extraction_status)
               VALUES ($1, '0', 'NULFIX VERIFY FIXTURE ISSUER 2', '424B2', $2, CURRENT_DATE, 'fixture2.htm',
                       'https://example.invalid/nulfix2', 'fixtures/nulfix2.htm', 'pending')""",
            F_FILING2, f"nulfix-{F_FILING2.hex}")
        await conn.execute(
            """INSERT INTO portfolio.edgar_cohorts (id, name, kind, definition, member_count)
               VALUES ($1, $2, 'custom', $3::jsonb, $4)""",
            COHORT_ID, "NULFIX Fixture Cohort", json.dumps({}), 1)
        # edgar_cohorts_member_count_check requires 1..50000 — give the cohort
        # one real member row, linked to the fixture filing via reference_filing_id.
        await conn.execute(
            """INSERT INTO portfolio.edgar_index_filings
                 (accession_number, form_type, filing_date, index_quarter, submission_path,
                  filer_count, reference_filing_id)
               VALUES ($1, '424B2', CURRENT_DATE, $2, 'fixtures/nulfix-index.txt', 1, $3)""",
            COHORT_MEMBER_ACCESSION, f"{date.today().year}Q{(date.today().month - 1) // 3 + 1}", F_FILING)
        await conn.execute(
            """INSERT INTO portfolio.edgar_cohort_members (cohort_id, accession_number, position)
               VALUES ($1, $2, 1)""",
            COHORT_ID, COHORT_MEMBER_ACCESSION)


async def teardown(conn) -> None:
    async with platform_scope(conn):
        await conn.execute(
            """DELETE FROM portfolio.note_extraction_staged_fields WHERE staging_id IN (
                 SELECT id FROM portfolio.note_extraction_staging WHERE run_id IN (
                   SELECT id FROM portfolio.note_extraction_runs WHERE created_by = $1))""", U_REVIEWER)
        await conn.execute(
            """DELETE FROM portfolio.note_extraction_staging WHERE run_id IN (
                 SELECT id FROM portfolio.note_extraction_runs WHERE created_by = $1)""", U_REVIEWER)
        await conn.execute("DELETE FROM portfolio.note_extraction_runs WHERE created_by = $1", U_REVIEWER)
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
        # cascades edgar_cohort_members (ON DELETE CASCADE on cohort_id) before
        # the edgar_index_filings delete below, which that member row FKs to.
        await conn.execute("DELETE FROM portfolio.edgar_cohorts WHERE id = $1", COHORT_ID)
        await conn.execute("DELETE FROM portfolio.edgar_index_filings WHERE accession_number = $1",
                           COHORT_MEMBER_ACCESSION)
        await conn.execute("DELETE FROM portfolio.note_gold_values WHERE reference_filing_id = $1", F_FILING)
        await conn.execute("DELETE FROM portfolio.note_term_readings WHERE reference_filing_id = ANY($1)",
                           [F_FILING, F_FILING2])
        await conn.execute("DELETE FROM portfolio.reference_filings WHERE id = ANY($1)", [F_FILING, F_FILING2])
        await conn.execute("DELETE FROM users WHERE id = $1", U_REVIEWER)
        await conn.execute("DELETE FROM organizations WHERE id = $1", ORG)


# ── 1. Sanitize module + _str_or_none + parse_items, pure ───────────────────
async def section_pure_sanitize(conn) -> None:
    check(strip_nul(f"a{NUL}b") == "ab", "strip_nul removes an embedded NUL byte")
    check(strip_nul(None) is None, "strip_nul passes non-strings through unchanged")
    check(strip_nul_deep({"q": f"x{NUL}y", "n": [f"a{NUL}", {"z": f"b{NUL}c"}]})
          == {"q": "xy", "n": ["a", {"z": "bc"}]},
          "strip_nul_deep recurses through nested dicts and lists")

    check(inv._str_or_none(f"Redemption Barrier{NUL}") == "Redemption Barrier",
         "_str_or_none strips NUL from a plain label/quote/value")
    nested = inv._str_or_none({"a": f"b{NUL}c"})
    check(nested is not None and NUL not in nested,
         "_str_or_none strips NUL from a dict/list value (json-encoded) too")

    # a raw (never run through strip_nul_deep) model response, fed straight
    # into parse_items — proves parse_items' own defense, not just the
    # upstream strip_nul_deep in edgar_inventory._call.
    raw_parsed = {
        "document": {"product_family": f"Autocallable{NUL} Note", "program_supplement": f"Supp{NUL} A",
                     "has_hypothetical_payout_table": True, "issue_size": f"$1,000,000{NUL}"},
        "items": [
            {"label": f"Trigger{NUL} Value", "quote": f"The Trigger{NUL} Value is 70%",
             "value": f"70%{NUL}", "section": f"Terms{NUL}",
             "misleading_label": True, "misleading_note": f"flag{NUL} note"},
            f"not-a-dict-item{NUL}",
        ],
    }
    facts, accepted, rejected = inv.parse_items(raw_parsed, _FakeFilingDoc(), set())
    check(NUL not in (facts.get("product_family") or ""), "parse_items strips NUL from document facts")
    check(len(accepted) == 1, "parse_items accepts the one well-formed item")
    if accepted:
        a = accepted[0]
        for field in ("label", "value_text", "quote", "section", "misleading_note"):
            check(NUL not in (a.get(field) or ""), f"parse_items strips NUL from item field '{field}'")
    check(any(r.get("reason") == "not an object" and NUL not in r.get("item", "") for r in rejected),
         "parse_items strips NUL even inside a rejected (malformed) item's echoed repr")


# ── 2. Reproduce the ORIGINAL crash — the TWO distinct real exceptions ──────
async def section_reproduce_original_crash(conn) -> None:
    raw_json_text = json.dumps({"q": f"a NUL right here {NUL} in the quote"})
    check(NUL not in raw_json_text and ESCAPE in raw_json_text,
         "the jsonb reproduction payload carries the JSON \\u0000 ESCAPE, not a raw NUL byte "
         "(json.dumps already converted it)")

    reproduced_untranslatable = False
    try:
        async with platform_scope(conn):
            await conn.execute(
                """INSERT INTO portfolio.note_term_readings
                     (reference_filing_id, field_key, source, value)
                   VALUES ($1, 'verify_nulfix_raw_json_escape', 'model_1', $2::jsonb)""",
                F_FILING, raw_json_text)
    except asyncpg.exceptions.UntranslatableCharacterError:
        reproduced_untranslatable = True
    except Exception as exc:  # noqa: BLE001 — wrong-exception-type is itself a finding, not a crash
        check(False, "unexpected exception reproducing the \\u0000-escape jsonb crash",
             f"{type(exc).__name__}: {exc}")
    check(reproduced_untranslatable,
         "an unsanitized \\u0000 JSON escape written to a jsonb column crashes with "
         "UntranslatableCharacterError — reproduces the ORIGINAL production crash")

    reproduced_repertoire = False
    try:
        async with platform_scope(conn):
            await conn.execute(
                """INSERT INTO portfolio.note_term_readings
                     (reference_filing_id, field_key, source, source_quote)
                   VALUES ($1, 'verify_nulfix_raw_text', 'model_1', $2)""",
                F_FILING, f"a NUL right here {NUL} in the quote")
    except asyncpg.exceptions.CharacterNotInRepertoireError:
        reproduced_repertoire = True
    except Exception as exc:  # noqa: BLE001
        check(False, "unexpected exception reproducing the raw-NUL text-column crash",
             f"{type(exc).__name__}: {exc}")
    check(reproduced_repertoire,
         "a raw NUL byte in a text column parameter crashes with CharacterNotInRepertoireError — "
         "a DIFFERENT exception from the jsonb \\u0000-escape case above")


# ── 3. Readings: both NUL forms through store.insert_readings ──────────────
async def section_readings(conn) -> None:
    # Form A: a raw NUL already sitting in a Python string (e.g. a label or
    # error message the code itself composed from filing text).
    row = store.ReadingRow(
        reference_filing_id=str(F_FILING), field_key="verify_nulfix_reading_raw", source="model_1",
        value={"label": f"Trigger Value{NUL}", "list": [f"x{NUL}y"]},
        value_normalized=f"trigger value{NUL}",
        source_quote=f"The Trigger Value is 70%{NUL} of the Initial Value",
        provider_model=f"claude-haiku-4-5-20251001{NUL}",
    )
    await store.insert_readings(conn, [row])
    check(True, "store.insert_readings does not crash on a raw-NUL-in-string reading")
    back = await conn.fetchrow(
        """SELECT value, value_normalized, source_quote, provider_model
             FROM portfolio.note_term_readings WHERE id = $1""", row.id)
    check(back is not None, "the raw-NUL reading round-trips back out of the table")
    if back is not None:
        check(NUL not in back["source_quote"], "source_quote carries no NUL after round-trip")
        check(NUL not in back["value_normalized"], "value_normalized carries no NUL after round-trip")
        check(NUL not in back["provider_model"], "provider_model carries no NUL after round-trip")
        check(NUL not in json.dumps(json.loads(back["value"])),
             "the jsonb value's nested strings carry no NUL after round-trip")

    # Form B: a NUL that arrives as a \u0000 escape inside a JSON response
    # TEXT, decoded by the REAL parser the readers use (schema.parse_reader_output,
    # i.e. pydantic's model_validate_json) — not a hand-built Python string.
    spec = FieldSpec(key="verify_nulfix_reading_jsonescape", label="Verify JSON-escape field",
                     kind=KIND_TEXT, description="verify fixture field")
    raw_model_text = json.dumps({spec.key: {"value": f"Model Value{NUL}", "quote": f"Model quote {NUL} text"}})
    check(NUL not in raw_model_text and ESCAPE in raw_model_text,
         "the simulated model response text carries the JSON \\u0000 escape, not a raw NUL byte")
    parsed_fields, perr = parse_reader_output([spec], raw_model_text)
    check(perr is None and parsed_fields is not None, "parse_reader_output accepts the \\u0000-escaped response")
    if parsed_fields is not None:
        check(NUL in parsed_fields[spec.key]["value"],
             "pydantic's JSON parser turns the \\u0000 escape back into a real NUL character, just like "
             "json.loads — proving the write path must handle it post-parse, not pre-parse")
        row2 = store.ReadingRow(
            reference_filing_id=str(F_FILING), field_key=spec.key, source="model_1",
            value=parsed_fields[spec.key], value_normalized=parsed_fields[spec.key]["value"],
            source_quote=parsed_fields[spec.key]["quote"],
        )
        await store.insert_readings(conn, [row2])
        check(True, "store.insert_readings does not crash on a reading decoded from a \\u0000-escaped response")
        back2 = await conn.fetchrow(
            """SELECT value, value_normalized, source_quote
                 FROM portfolio.note_term_readings WHERE id = $1""", row2.id)
        check(back2 is not None, "the decoded-escape reading round-trips back out of the table")
        if back2 is not None:
            check(NUL not in back2["source_quote"], "decoded-escape source_quote carries no NUL after round-trip")
            check(NUL not in back2["value_normalized"],
                 "decoded-escape value_normalized carries no NUL after round-trip")
            check(NUL not in json.dumps(json.loads(back2["value"])),
                 "the decoded-escape jsonb value carries no NUL after round-trip")


# ── 4. Inventory items + documents: both NUL forms through store_document ──
async def section_inventory_documents(conn) -> None:
    run_id = await inv.create_run(conn, cohort_id=COHORT_ID, deployment="fixture-deployment",
                                  spend_cap=1.0, planned=2, created_by=U_REVIEWER)

    def make_chosen(filing_id: UUID) -> inv.ChosenDocument:
        return inv.ChosenDocument(
            reference_filing_id=str(filing_id), accession_number=f"nulfix-{filing_id.hex}",
            issuer_group="NULFIX ISSUER", document_kind="424B2", filing_date=date.today(),
            families=[], reason="fixture", doc=_FakeFilingDoc(),
            terms=inv.TermsPages(text="x", chars=1, tokens_est=1, full_chars=1, sections=[], stopped_at=None))

    def make_result() -> inv.CallResult:
        return inv.CallResult(status="ok", provider_model="fixture-model", proxy_model_id="fixture-proxy",
                              input_tokens=10, output_tokens=10, cost_usd=0.0001, latency_ms=5)

    # Form A: a raw NUL already sitting in a Python string, run through the
    # real parse_items (same as section_pure_sanitize, but now also written
    # to the DB via the real store_document to prove the jsonb/text columns
    # round-trip clean, not just that the in-memory dicts are clean).
    raw_parsed = {
        "document": {"product_family": f"Buffered{NUL} Note A", "program_supplement": f"Supp{NUL} A",
                     "has_hypothetical_payout_table": True, "issue_size": f"$1,000,000{NUL}"},
        "items": [{"label": f"Barrier{NUL} Level A", "quote": f"The Barrier{NUL} Level A is 60%",
                   "value": f"60%{NUL}", "section": f"Terms{NUL} A",
                   "misleading_label": True, "misleading_note": f"note{NUL} A"},
                  f"not-a-dict-item{NUL} A"],
    }
    facts_a, accepted_a, rejected_a = inv.parse_items(raw_parsed, _FakeFilingDoc(), set())
    doc_id_a = await inv.store_document(conn, run_id, make_chosen(F_FILING), make_result(),
                                        "fixture-deployment", facts_a, accepted_a, rejected_a)
    check(True, "store_document does not crash on a raw-NUL-in-string document/items write")

    # Form B: a NUL that arrives as a \u0000 escape inside the model's raw
    # JSON response TEXT, decoded by the REAL parse used in edgar_inventory._call
    # (json.loads, then strip_nul_deep) before parse_items ever sees it.
    model_fields_with_nul = {
        "document": {"product_family": f"Buffered{NUL} Note B", "program_supplement": f"Supp{NUL} B",
                     "has_hypothetical_payout_table": False, "issue_size": f"$2,000,000{NUL}"},
        "items": [{"label": f"Barrier{NUL} Level B", "quote": f"The Barrier{NUL} Level B is 50%",
                   "value": f"50%{NUL}", "section": f"Terms{NUL} B",
                   "misleading_label": False, "misleading_note": None},
                  f"not-a-dict-item{NUL} B"],
    }
    content_text = json.dumps(model_fields_with_nul)
    check(NUL not in content_text and ESCAPE in content_text,
         "the simulated model response text carries the JSON \\u0000 escape, not a raw NUL byte")
    reparsed = json.loads(content_text)
    check(NUL in reparsed["items"][0]["label"],
         "json.loads turns the \\u0000 escape back into a real NUL character — mirrors edgar_inventory._call")
    stripped = strip_nul_deep(reparsed)
    facts_b, accepted_b, rejected_b = inv.parse_items(stripped, _FakeFilingDoc(), set())
    doc_id_b = await inv.store_document(conn, run_id, make_chosen(F_FILING2), make_result(),
                                        "fixture-deployment", facts_b, accepted_b, rejected_b)
    check(True, "store_document does not crash on a document/items write decoded from a \\u0000-escaped response")

    for label, doc_id, accepted in (("raw-NUL", doc_id_a, accepted_a), ("decoded-escape", doc_id_b, accepted_b)):
        drow = await conn.fetchrow(
            """SELECT product_family, program_supplement, issue_size, rejected_items
                 FROM portfolio.edgar_inventory_documents WHERE id = $1""", doc_id)
        check(drow is not None, f"the {label} document round-trips back out of the table")
        if drow is not None:
            check(NUL not in (drow["product_family"] or ""), f"{label} product_family carries no NUL")
            check(NUL not in (drow["program_supplement"] or ""), f"{label} program_supplement carries no NUL")
            check(NUL not in (drow["issue_size"] or ""), f"{label} issue_size carries no NUL")
            rejected_back = json.loads(drow["rejected_items"])
            check(len(rejected_back) == 1, f"{label} rejected_items carries the one malformed item")
            check(NUL not in json.dumps(rejected_back), f"{label} rejected_items carries no NUL")
        irows = await conn.fetch(
            """SELECT label, value_text, quote, section, misleading_note
                 FROM portfolio.edgar_inventory_items WHERE document_id = $1""", doc_id)
        check(len(irows) == len(accepted) == 1, f"the {label} item round-trips back out of the table")
        for r in irows:
            for col in ("label", "value_text", "quote", "section", "misleading_note"):
                check(NUL not in (r[col] or ""), f"{label} item column '{col}' carries no NUL after round-trip")


# ── 5. Staging: both NUL forms through store.insert_staging ────────────────
async def section_staging(conn) -> None:
    run_id = await store.create_run(conn, run_kind="verify", spend_cap_usd=1.0, config={},
                                    created_by=U_REVIEWER)

    spec = FieldSpec(key="verify_nulfix_staged_field", label="Verify staged field",
                     kind=KIND_TEXT, description="verify fixture field")
    raw_model_text = json.dumps({spec.key: {"value": f"Staged Value{NUL}", "quote": f"Staged quote {NUL} text"}})
    check(NUL not in raw_model_text and ESCAPE in raw_model_text,
         "the staging fixture's simulated model text carries the JSON \\u0000 escape, not a raw NUL byte")
    parsed_fields, perr = parse_reader_output([spec], raw_model_text)
    check(perr is None and parsed_fields is not None, "parse_reader_output accepts the staging fixture's response")
    resolved_value = parsed_fields[spec.key]["value"] if parsed_fields else None
    source_quote = parsed_fields[spec.key]["quote"] if parsed_fields else None
    check(NUL in (resolved_value or ""), "the staged field's resolved_value carries a real NUL, decoded from \\u0000")

    staged = store.StagedField(field_key=spec.key, resolved_value=resolved_value, resolution="verified_agreement",
                               is_critical=False, needs_review=False, source_quote=source_quote)
    staging_id = await store.insert_staging(
        conn, run_id=run_id, reference_filing_id=str(F_FILING), status="verified",
        status_reason=f"auto-resolved{NUL}", ensemble_config_id=None, full_tokens_est=100, trimmed_tokens_est=80,
        disagreement_count=0, jev_called=False, escalated=False, fuller_text_retry=False,
        skip_second_reader_safe=True, cost_usd=0.001, unmatched_participants=[f"Unknown Agent{NUL}"],
        detail={"note": f"fixture detail{NUL}"}, fields=[staged])
    check(True, "store.insert_staging does not crash on raw-NUL (status_reason/detail/participants) "
               "AND decoded-escape (resolved_value/source_quote) in the same write")

    srow = await conn.fetchrow(
        """SELECT status_reason, unmatched_participants, detail
             FROM portfolio.note_extraction_staging WHERE id = $1""", staging_id)
    check(srow is not None, "the staging row round-trips back out of the table")
    if srow is not None:
        check(NUL not in srow["status_reason"], "staging status_reason carries no NUL after round-trip")
        check(NUL not in json.dumps(json.loads(srow["unmatched_participants"])),
             "staging unmatched_participants carries no NUL after round-trip")
        check(NUL not in json.dumps(json.loads(srow["detail"])), "staging detail carries no NUL after round-trip")

    frow = await conn.fetchrow(
        """SELECT resolved_value, source_quote FROM portfolio.note_extraction_staged_fields
            WHERE staging_id = $1 AND field_key = $2""", staging_id, spec.key)
    check(frow is not None, "the staged field round-trips back out of the table")
    if frow is not None:
        check(NUL not in json.dumps(json.loads(frow["resolved_value"])),
             "staged field resolved_value carries no NUL after round-trip")
        check(NUL not in frow["source_quote"], "staged field source_quote carries no NUL after round-trip")


# ── 6. Gold: both NUL forms through gold.record_gold_value ─────────────────
async def section_gold(conn) -> None:
    # Form A: a raw NUL already sitting in a Python string (a reviewer's own
    # typed correction/notes).
    spec = FieldSpec(key="verify_nulfix_field_raw", label="Verify Field", kind=KIND_TEXT,
                     description="verify fixture field")
    await gold.record_gold_value(
        conn, reviewer_id=str(U_REVIEWER), reference_filing_id=str(F_FILING), spec=spec,
        action="corrected", value=f"Seventy Percent{NUL}", source_quote=f"70%{NUL} Trigger Value",
        notes=f"reviewer note{NUL} with a stray NUL")
    check(True, "gold.record_gold_value does not crash on raw-NUL value/quote/notes")
    gback = await conn.fetchrow(
        """SELECT source_quote, notes FROM portfolio.note_gold_values
            WHERE reference_filing_id = $1 AND field_key = $2""", F_FILING, spec.key)
    check(gback is not None, "the raw-NUL gold row round-trips back out of the table")
    if gback is not None:
        check(NUL not in (gback["source_quote"] or ""), "gold source_quote carries no NUL after round-trip")
        check(NUL not in (gback["notes"] or ""), "gold notes carries no NUL after round-trip")

    # Form B: a value the reviewer confirmed/corrected to match a model
    # suggestion whose JSON contained a \u0000 escape (decoded by json.loads,
    # same real conversion a reader's output would have gone through).
    spec2 = FieldSpec(key="verify_nulfix_field_jsonescape", label="Verify Field 2", kind=KIND_TEXT,
                      description="verify fixture field 2")
    suggestion_text = json.dumps({"text": f"Seventy Percent{NUL}"})
    check(NUL not in suggestion_text and ESCAPE in suggestion_text,
         "the gold fixture's model-suggestion text carries the JSON \\u0000 escape, not a raw NUL byte")
    decoded = json.loads(suggestion_text)
    check(NUL in decoded["text"], "json.loads turns the \\u0000 escape back into a real NUL character")
    await gold.record_gold_value(
        conn, reviewer_id=str(U_REVIEWER), reference_filing_id=str(F_FILING), spec=spec2,
        action="confirmed", value=decoded["text"], source_quote=f"70%{NUL} Trigger Value (confirmed)")
    check(True, "gold.record_gold_value does not crash on a decoded-escape value")
    gback2 = await conn.fetchrow(
        """SELECT value, source_quote FROM portfolio.note_gold_values
            WHERE reference_filing_id = $1 AND field_key = $2""", F_FILING, spec2.key)
    check(gback2 is not None, "the decoded-escape gold row round-trips back out of the table")
    if gback2 is not None:
        check(NUL not in json.dumps(json.loads(gback2["value"])),
             "decoded-escape gold value carries no NUL after round-trip")
        check(NUL not in (gback2["source_quote"] or ""),
             "decoded-escape gold source_quote carries no NUL after round-trip")


async def main() -> int:
    url = await bootstrap_async(quiet=True)
    if not url:
        print("no working DATABASE_URL (Doppler hydrate failed) — cannot run")
        return 2
    conn = await asyncpg.connect(url, statement_cache_size=0)
    try:
        # guarded, and run at the start too: a prior run crashed before its own
        # teardown must not leave fixtures behind, and must not abort this run.
        await guarded("teardown (pre-run)", teardown(conn))
        await guarded("seed", seed(conn))

        await guarded("section_pure_sanitize", section_pure_sanitize(conn))
        await guarded("section_reproduce_original_crash", section_reproduce_original_crash(conn))
        await guarded("section_readings", section_readings(conn))
        await guarded("section_inventory_documents", section_inventory_documents(conn))
        await guarded("section_staging", section_staging(conn))
        await guarded("section_gold", section_gold(conn))
    finally:
        await guarded("teardown (final)", teardown(conn))
        await conn.close()

    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
