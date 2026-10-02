"""Propose 50-100 notes for the GOLD SET — stratified across issuers, years and
product types, deliberately including TRAP cases found by keyword (downside
threshold / trigger / buffer wording, memory coupons, worst-of baskets, daily
observation, issuer calls vs automatic calls, conditional principal,
distribution platforms, separate fee-based-account prices).

Proposals only: nothing is gold until a person confirms it on the gold screen.
No model is called.

    python3 apps/api/scripts/sample_gold_set.py                 # dry run: print the sample
    python3 apps/api/scripts/sample_gold_set.py --write          # write note_gold_candidates
        [--target 75] [--no-corpus] [--batch gold-2026-10]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import date

import _note_extraction_common as common


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=75)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--no-corpus", action="store_true")
    ap.add_argument("--batch", default=f"gold-{date.today().isoformat()}")
    args = ap.parse_args()

    from services.note_extraction import documents, gold, selection

    conn = await common.connect()
    try:
        ready = await selection.ready_notes(conn)
        corpus = [] if args.no_corpus else await selection.corpus_notes(conn)
        pool = []
        for n in ready + corpus:
            text = n.text
            if text is None:
                try:
                    text = (await documents.load_document(conn, n.reference_filing_id)).text
                except Exception as exc:  # noqa: BLE001
                    print(f"  skip {n.reference_filing_id}: {exc}")
                    continue
            pool.append(gold.SampleCandidate(n.reference_filing_id, n.issuer, n.filing_year,
                                             gold.product_type(text), gold.trap_tags(text)))
        print(f"pool: {len(pool)} eligible notes ({len(ready)} ready_for_extraction, {len(corpus)} corpus)")
        if not pool:
            return 0
        sample = gold.stratified_sample(pool, target=args.target)
        tags = Counter(t for c in sample for t in c.trap_tags)
        print(f"sample: {len(sample)} notes")
        print(f"  issuers:  {dict(Counter(c.issuer_group for c in sample))}")
        print(f"  years:    {dict(Counter(c.filing_year for c in sample))}")
        print(f"  products: {dict(Counter(c.product_type for c in sample))}")
        print(f"  traps:    {dict(tags)}")
        missing = [t for t in gold.TRAP_PATTERNS if t not in tags]
        if missing:
            print(f"  trap kinds with NO example in the pool: {missing}")
        if len(sample) < 50:
            print(f"  NOTE: only {len(sample)} eligible notes exist — below the 50-note floor")
        if args.write:
            n = await gold.write_candidates(conn, sample, args.batch)
            print(f"wrote {n} proposals to portfolio.note_gold_candidates (batch {args.batch})")
        else:
            print("dry run — re-run with --write to store the proposals")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
