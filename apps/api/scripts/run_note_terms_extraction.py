"""Bounded term-extraction run over the EDGAR reference corpus.

CAPPED AT 50 FILINGS BY DEFAULT, DELIBERATELY. This proves the pipeline end to
end and produces the accuracy numbers a scaling decision needs. Running the
whole corpus is that decision's OUTPUT, not its default — extraction quality on
50 documents is the evidence, and nobody has reviewed it yet.

Progress is derived, never written back to reference_filings.extraction_status.
See the note_terms_extraction module docstring for why that column is off limits.

    python3 scripts/run_note_terms_extraction.py [--limit N] [--force]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.append("/mnt/c/Users/Joe/2ndActCapital/apps/api/venv/lib/python3.14/site-packages")

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"), override=False)

from services.note_terms_extraction import (  # noqa: E402
    extract_terms,
    filings_with_terms_extracted,
)

DEFAULT_LIMIT = 50

# The corpus carries a leaked test fixture (cik 9999999999, 'VERIFY FIXTURE')
# from an earlier sprint's teardown. It is another sprint's row to delete, so it
# is excluded here rather than removed.
POPULATION_SQL = """
    SELECT id, cik, filer_name, form_type, length(extracted_text) AS textlen
    FROM portfolio.reference_filings
    WHERE extraction_status = 'extracted'
      AND filer_name <> 'VERIFY FIXTURE'
      AND length(extracted_text) >= 2000
    ORDER BY filing_date DESC, id
"""


async def run(limit: int, force: bool) -> dict:
    pool = await asyncpg.create_pool(
        os.environ["DATABASE_URL"], statement_cache_size=0, min_size=1, max_size=4
    )
    try:
        async with pool.acquire() as conn:
            population = await conn.fetch(POPULATION_SQL)
            already = await filings_with_terms_extracted(conn)

        print(f"population (extracted, non-fixture, >=2000 chars) = {len(population)}")
        print(f"already have current terms rows              = {len(already)}")

        todo = [r for r in population if str(r["id"]) not in already] if not force else list(population)
        selected = todo[:limit]
        print(f"selecting {len(selected)} (cap {limit}, force={force})\n")

        results = []
        for i, row in enumerate(selected, 1):
            print(
                f"[{i}/{len(selected)}] {row['form_type']:6s} {str(row['filer_name'])[:38]:38s} "
                f"{row['textlen']:>7d} chars ",
                end="", flush=True,
            )
            try:
                res = await extract_terms(row["id"], pool, force=force)
            except Exception as exc:  # noqa: BLE001 — one bad filing must not end the run
                print(f"ERROR {type(exc).__name__}: {exc}")
                continue
            results.append(res)
            if not res.ok:
                print(f"FAILED — {res.error}")
                continue
            flag = "!" if res.hazard_disagreements else " "
            print(
                f"{res.extraction_confidence:12s}{flag} "
                f"disagree={sorted(res.hazard_disagreements) or '-'} "
                f"vfail={len(res.validator_failures)} "
                f"span=({res.source_char_start},{res.source_char_end})"
            )
        return report(results)
    finally:
        await pool.close()


def report(results: list) -> dict:
    ok = [r for r in results if r.ok and not r.reused_existing]
    failed = [r for r in results if not r.ok]

    field_states = Counter()
    for r in ok:
        field_states.update(r.field_status.values())

    confidence = Counter(r.extraction_confidence for r in ok)
    disagree_fields = Counter()
    rows_with_disagreement = 0
    for r in ok:
        if r.hazard_disagreements:
            rows_with_disagreement += 1
            disagree_fields.update(r.hazard_disagreements.keys())

    validator_fail_rows = sum(1 for r in ok if r.validator_failures)
    validator_fail_kinds = Counter()
    for r in ok:
        for f in r.validator_failures:
            validator_fail_kinds[f.split(":", 1)[0]] += 1
    warning_rows = sum(1 for r in ok if r.validator_warnings)

    ensemble_measured = sum(1 for r in ok if r.ensemble_measured)
    with_spans = sum(1 for r in ok if r.source_char_start is not None)
    underlyings = sum(len(r.underlying_texts) for r in ok)

    total_field_slots = sum(len(r.field_status) for r in ok)

    print("\n" + "=" * 72)
    print("TASK 5 — BOUNDED RUN RESULTS")
    print("=" * 72)
    print(f"filings processed          : {len(results)}")
    print(f"rows created               : {len(ok)}")
    print(f"filings failed             : {len(failed)}")
    for r in failed:
        print(f"    {r.filing_id}: {r.error}")

    print(f"\nfield_status distribution  : {total_field_slots} field slots across {len(ok)} rows")
    for state, n in field_states.most_common():
        pct = (n / total_field_slots * 100) if total_field_slots else 0
        print(f"    {state:20s} {n:5d}  {pct:5.1f}%")

    print(f"\nhazard ensemble")
    print(f"    ensemble genuinely measured (2 distinct models) : {ensemble_measured}/{len(ok)}")
    denom = ensemble_measured or 1
    print(f"    rows with >=1 hazard disagreement               : {rows_with_disagreement}"
          f"  ({rows_with_disagreement / denom * 100:.1f}% of measured)")
    if disagree_fields:
        print("    disagreements by field:")
        for f, n in disagree_fields.most_common():
            print(f"        {f:22s} {n}")
    else:
        print("    disagreements by field: none")

    print(f"\nvalidators")
    print(f"    rows with >=1 hard failure : {validator_fail_rows}/{len(ok)}"
          f"  ({validator_fail_rows / (len(ok) or 1) * 100:.1f}%)")
    for kind, n in validator_fail_kinds.most_common():
        print(f"        {kind:28s} {n}")
    print(f"    rows with warnings only    : {warning_rows}")

    print(f"\nextraction_confidence distribution")
    for c, n in confidence.most_common():
        print(f"    {c:14s} {n:4d}  {n / (len(ok) or 1) * 100:5.1f}%")

    print(f"\nsource spans populated     : {with_spans}/{len(ok)}")
    print(f"unresolved underlying edges: {underlyings}")
    print("=" * 72)

    return {
        "processed": len(results),
        "rows_created": len(ok),
        "failed": len(failed),
        "field_states": dict(field_states),
        "confidence": dict(confidence),
        "rows_with_disagreement": rows_with_disagreement,
        "ensemble_measured": ensemble_measured,
        "validator_fail_rows": validator_fail_rows,
        "with_spans": with_spans,
        "underlyings": underlyings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--force", action="store_true",
                        help="re-extract filings that already have terms (bitemporal supersede)")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set — extraction cannot run. "
              "This is NOT MEASURED, not a pass.")
        return 2

    summary = asyncio.run(run(args.limit, args.force))
    return 0 if summary["rows_created"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
