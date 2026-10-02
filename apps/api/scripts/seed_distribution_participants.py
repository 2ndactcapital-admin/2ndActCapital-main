"""SEED portfolio.distribution_participants FROM THE DATA (Decision 11).

Tallies the participant names the RULES find in the plan-of-distribution
sections of the pilot's filings (or every stored eligible filing with
--all-eligible), and proposes each unseen name as a 'proposed' entry with its
alias spellings and observed count. No list is hard-coded; no model is called.
Unmatched names are printed, never discarded.

    python3 apps/api/scripts/seed_distribution_participants.py --run-id <pilot run>   # dry run
    python3 apps/api/scripts/seed_distribution_participants.py --all-eligible --write
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import _note_extraction_common as common


async def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run-id", help="tally the filings of this pilot run")
    g.add_argument("--all-eligible", action="store_true", help="tally every eligible stored filing")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    from services.note_extraction import documents, participants, rules, selection, trim

    conn = await common.connect()
    try:
        if args.run_id:
            rows = await conn.fetch(
                "SELECT reference_filing_id FROM portfolio.note_extraction_staging WHERE run_id = $1", args.run_id)
            ids = [str(r["reference_filing_id"]) for r in rows]
        else:
            notes, _ = await selection.select_notes(conn, limit=100000, include_corpus=True)
            ids = [n.reference_filing_id for n in notes]
        obs = []
        for fid in ids:
            try:
                doc = await documents.load_document(conn, fid)
            except Exception as exc:  # noqa: BLE001
                print(f"  skip {fid}: {exc}")
                continue
            tr = trim.trim(doc.text)
            for p in rules.find_participant_names(doc.text, tr.distribution_spans):
                obs.append({"name": p["name"], "issuer_name": doc.filer_name,
                            "filing_year": doc.filing_date.year if doc.filing_date else None})
        rows = participants.tally(obs)
        existing = await participants.load_participants(conn)
        m = participants.match_names([r["canonical_name"] for r in rows], existing)
        print(f"{len(ids)} filings, {len(obs)} name mentions, {len(rows)} distinct participants")
        for r in rows:
            print(f"  {r['count']:5}  {r['proposed_type']:17} {r['canonical_name']}"
                  + (f"   aliases={r['aliases']}" if r["aliases"] else ""))
        print(f"already on the table: {len(m.matched)}; new (unmatched): {len(m.unmatched)}")
        print(json.dumps([u["name"] for u in m.unmatched], indent=1))
        if args.write:
            print(await participants.propose_from_tally(conn, rows))
        else:
            print("dry run — re-run with --write to propose these entries")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
