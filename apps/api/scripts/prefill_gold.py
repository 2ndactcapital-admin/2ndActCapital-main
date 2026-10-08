"""GOLD PRE-FILL (goldset.structural) — one model reads every note of a gold batch
so the reviewer starts from a proposed value.

    python3 apps/api/scripts/prefill_gold.py --batch gold-2026-10 --model gpt-5-mini \
        --max-tokens 32000 --spend-cap 5 --dry-run
    python3 apps/api/scripts/prefill_gold.py --batch gold-2026-10 --model gpt-5-mini \
        --max-tokens 32000 --spend-cap 5

One extraction run per batch (run_kind 'gold_prefill'); every reading carries
the run id. Rules + EdgarTools + ONE reader + rules-only fields + derivations +
self-checks on the v3 registry; nothing is staged and nothing touches
securities_global_note_terms. Refused before any write: a Claude model, an
OpenRouter route, a model with no proxy price and no manual price, and
--max-tokens below 32000. A public_data_only model (gpt-5-mini) IS accepted —
the gold set is public SEC filings. --dry-run prints the notes, estimated
tokens and cost and writes nothing.

ANCHORING: the reviewer sees this model's values first, so its own score
against the gold set is biased upward and must be reported with that caveat.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import _note_extraction_common as common


async def main(argv: list[str] | None = None) -> int:
    from services.model_catalog import PublicDataOnlyError
    from services.note_extraction import prefill, readers, schema
    from services.note_extraction.spend import UnpricedModelError

    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", required=True)
    ap.add_argument("--model", default="gpt-5-mini")
    ap.add_argument("--max-tokens", type=int, default=prefill.MIN_MAX_TOKENS)
    ap.add_argument("--spend-cap", type=float, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="only the first N notes of the batch")
    args = ap.parse_args(argv)
    if args.spend_cap <= 0:
        ap.error("--spend-cap must be positive")

    conn = await common.connect()
    try:
        specs, catalog, _parts = await common.load_context(conn)
        try:
            catalog = await prefill.check_model(conn, args.model, catalog, max_tokens=args.max_tokens)
        except (prefill.PrefillRefused, PublicDataOnlyError, UnpricedModelError) as exc:
            print(f"refused: {exc}")
            return 2
        ids = await prefill.batch_filing_ids(conn, args.batch)
        if args.limit:
            ids = ids[: args.limit]
        if not ids:
            print(f"batch {args.batch!r} has no notes to pre-fill")
            return 0
        print(f"batch {args.batch!r}: {len(ids)} notes, model {args.model}, max_tokens {args.max_tokens}, "
              f"{len(schema.reader_specs(specs))} model fields of {len(specs)} live fields")
        print(prefill.anchoring_note(args.model))
        if args.dry_run:
            cfg = readers.ReaderConfig(slot=prefill.SLOT, deployment=args.model, max_tokens=args.max_tokens)
            p = await prefill.plan(conn, ids, specs, cfg, catalog=catalog)
            print("\nDRY RUN — no model was called, nothing was written.")
            for n in p.notes:
                print(f"  {n['reference_filing_id']}  {str(n['filer'])[:40]:40} ~{n['est_input_tokens']:>7} in "
                      f"~{n['est_output_tokens']:>6} out  ~${n['est_cost_usd']:.4f}")
            for s in p.skipped:
                print(f"  {s['reference_filing_id']}  NOT LOADABLE: {s['error']}")
            print(f"  total: {len(p.notes)} notes, ~{p.est_input_tokens} input tokens, "
                  f"~${p.est_cost_usd:.4f} (cap ${args.spend_cap:.2f}; output priced at the max_tokens ceiling)")
            return 0
        res = await prefill.run_prefill(
            conn, batch=args.batch, filing_ids=ids, specs=specs, model=args.model, max_tokens=args.max_tokens,
            spend_cap_usd=args.spend_cap, catalog=catalog,
            progress=lambda fid, msg: print(f"  {fid}: {msg}", flush=True))
        print(f"\nrun {res['run_id']}: {res['status']}, {res['notes_done']} notes, ${res['spent_usd']:.4f}"
              + (f" — {res['stop_reason']}" if res["stop_reason"] else ""))
        return 0 if res["status"] in ("completed", "stopped_spend_cap") else 1
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
