"""verify_edgarinventory_nulfix.py — the NUL-stripping fix in
services/edgar_inventory.py and services/note_extraction (readings, staging,
gold).

A real run crashed with ``asyncpg.exceptions.UntranslatableCharacterError:
\\u0000 cannot be converted to text`` — a Postgres ``text`` column rejects an
embedded NUL byte outright, and a model can echo one straight out of a
filing's raw bytes (labels, values, quotes, notes, errors, the model's own
reported name). This script:

  1. reproduces the ORIGINAL crash with a raw, unsanitized insert into the
     exact table/column the real failure hit
  2. proves ``services.note_extraction.sanitize`` strips a NUL from a string
     and from every string nested inside a dict/list
  3. proves the real write paths — ``edgar_inventory._str_or_none`` (labels /
     values / quotes / notes), ``store.insert_readings`` (readings), and
     ``gold.record_gold_value`` (the human gold path) — now carry a
     NUL-laced value through without crashing, and that no NUL survives in
     what actually lands in the database

Hydrates secrets from Doppler over HTTPS at startup, like every other verify
script. Teardown is by the fixture's own ids, not a broad delete.

Run:  python3 apps/api/scripts/verify_edgarinventory_nulfix.py
"""
from __future__ import annotations

import json
import pathlib
import sys
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
from services.note_extraction.schema import FieldSpec, KIND_TEXT  # noqa: E402

NUL = "\x00"
# the "c0de" fixture block is used by no other verify script (grep'd).
ORG = UUID("99000000-0000-0000-0000-0000c0de0001")
U_REVIEWER = UUID("99000000-0000-0000-0000-0000c0de0011")
F_FILING = UUID("99000000-0000-0000-0000-0000c0de0101")

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


async def teardown(conn) -> None:
    async with platform_scope(conn):
        await conn.execute("DELETE FROM portfolio.note_gold_values WHERE reference_filing_id = $1", F_FILING)
        await conn.execute("DELETE FROM portfolio.note_term_readings WHERE reference_filing_id = $1", F_FILING)
        await conn.execute("DELETE FROM portfolio.reference_filings WHERE id = $1", F_FILING)
        await conn.execute("DELETE FROM users WHERE id = $1", U_REVIEWER)
        await conn.execute("DELETE FROM organizations WHERE id = $1", ORG)


async def main() -> int:
    url = await bootstrap_async(quiet=True)
    if not url:
        print("no working DATABASE_URL (Doppler hydrate failed) — cannot run")
        return 2
    conn = await asyncpg.connect(url, statement_cache_size=0)
    try:
        await teardown(conn)   # in case a prior run was interrupted before its own teardown
        await seed(conn)

        # ── 1. The sanitize module itself, pure ──────────────────────────────
        check(strip_nul(f"a{NUL}b") == "ab", "strip_nul removes an embedded NUL byte")
        check(strip_nul(None) is None, "strip_nul passes non-strings through unchanged")
        check(strip_nul_deep({"q": f"x{NUL}y", "n": [f"a{NUL}", {"z": f"b{NUL}c"}]})
              == {"q": "xy", "n": ["a", {"z": "bc"}]},
              "strip_nul_deep recurses through nested dicts and lists")

        # ── 2. edgar_inventory's own label/value/quote sanitization ─────────
        check(inv._str_or_none(f"Redemption Barrier{NUL}") == "Redemption Barrier",
             "_str_or_none strips NUL from a plain label/quote/value")
        nested = inv._str_or_none({"a": f"b{NUL}c"})
        check(nested is not None and NUL not in nested,
             "_str_or_none strips NUL from a dict/list value (json-encoded) too")

        # ── 3. Reproduce the ORIGINAL crash: a raw, unsanitized insert ───────
        reproduced = False
        try:
            async with platform_scope(conn):
                await conn.execute(
                    """INSERT INTO portfolio.note_term_readings
                         (reference_filing_id, field_key, source, source_quote)
                       VALUES ($1, 'verify_nulfix_raw', 'model_1', $2)""",
                    F_FILING, f"a NUL right here {NUL} in the quote")
        except asyncpg.exceptions.UntranslatableCharacterError:
            reproduced = True
        check(reproduced, "an unsanitized NUL byte in a text column crashes with "
                          "UntranslatableCharacterError — reproduces the original bug")

        # ── 4. The real write path now carries it through safely ────────────
        row = store.ReadingRow(
            reference_filing_id=str(F_FILING), field_key="verify_nulfix_reading", source="model_1",
            value={"label": f"Trigger Value{NUL}", "list": [f"x{NUL}y"]},
            value_normalized=f"trigger value{NUL}",
            source_quote=f"The Trigger Value is 70%{NUL} of the Initial Value",
            provider_model=f"claude-haiku-4-5-20251001{NUL}",
        )
        wrote = False
        try:
            await store.insert_readings(conn, [row])
            wrote = True
        except asyncpg.exceptions.UntranslatableCharacterError as exc:
            check(False, "store.insert_readings crashed on a NUL-laced reading", str(exc))
        if wrote:
            check(True, "store.insert_readings no longer crashes on a NUL-laced reading")
            back = await conn.fetchrow(
                """SELECT value, value_normalized, source_quote, provider_model
                     FROM portfolio.note_term_readings WHERE id = $1""", row.id)
            check(back is not None, "the sanitized reading round-trips back out of the table")
            if back is not None:
                check(NUL not in back["source_quote"], "source_quote carries no NUL after round-trip")
                check(NUL not in back["value_normalized"], "value_normalized carries no NUL after round-trip")
                check(NUL not in back["provider_model"], "provider_model carries no NUL after round-trip")
                check(NUL not in json.dumps(json.loads(back["value"])),
                     "the jsonb value's nested strings carry no NUL after round-trip")

        # ── 5. Gold: the human write path ────────────────────────────────────
        spec = FieldSpec(key="verify_nulfix_field", label="Verify Field", kind=KIND_TEXT,
                         description="verify fixture field")
        gold_ok = False
        try:
            await gold.record_gold_value(
                conn, reviewer_id=str(U_REVIEWER), reference_filing_id=str(F_FILING), spec=spec,
                action="corrected", value=f"Seventy Percent{NUL}", source_quote=f"70%{NUL} Trigger Value",
                notes=f"reviewer note{NUL} with a stray NUL")
            gold_ok = True
        except asyncpg.exceptions.UntranslatableCharacterError as exc:
            check(False, "gold.record_gold_value crashed on a NUL-laced quote/notes", str(exc))
        if gold_ok:
            check(True, "gold.record_gold_value no longer crashes on a NUL-laced quote/notes")
            gback = await conn.fetchrow(
                """SELECT source_quote, notes FROM portfolio.note_gold_values
                    WHERE reference_filing_id = $1 AND field_key = 'verify_nulfix_field'""", F_FILING)
            check(gback is not None, "the gold row round-trips back out of the table")
            if gback is not None:
                check(NUL not in (gback["source_quote"] or ""), "gold source_quote carries no NUL after round-trip")
                check(NUL not in (gback["notes"] or ""), "gold notes carries no NUL after round-trip")
    finally:
        await teardown(conn)
        await conn.close()

    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
