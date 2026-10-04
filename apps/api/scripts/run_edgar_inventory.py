"""edgarcohorts TEMPLATE STUDY — the inventory pass over a cohort's fetched filings.

Inventory, NOT extraction: the model lists every data element the terms pages
contain, with exact quotes (each checked against the filing — a quote that is
not found rejects the item), mapped to the existing field registry or NEW; the
items are then grouped into concepts. See services/edgar_inventory.py.

    python3 apps/api/scripts/run_edgar_inventory.py --cohort <uuid> --dry-run --spend-cap 5
    python3 apps/api/scripts/run_edgar_inventory.py --cohort <uuid> --spend-cap 5 [--model <id>]
        [--per-issuer-min 4] [--per-issuer-max 6] [--product-supplements 2] [--no-model-grouping]
        [--concurrency 4]
    python3 apps/api/scripts/run_edgar_inventory.py --write-doc <inventory-run-uuid>
        (re)write docs/TEMPLATE_STUDY.md from a stored run — no model call

--spend-cap is REQUIRED for a run: the run stops cleanly when the next call would cross it.
--dry-run picks the documents, cuts their terms pages and prints token counts and an
estimated cost; it calls NO model.
The model is chosen at run time from platform_model_catalog rows that are 'available', not
Claude, and served by the LiteLLM proxy; if none is, the script reports BLOCKED (exit 2).
--concurrency documents are read concurrently (default 4); the spending cap stays exact
regardless. Ctrl+C, or any crash, marks the run 'failed' with its real counts and spend
instead of leaving it stuck 'running'.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import signal
import sys
from uuid import UUID

import _note_extraction_common as common

REPO = pathlib.Path(__file__).resolve().parents[3]
DOC_PATH = REPO / "docs" / "TEMPLATE_STUDY.md"


async def write_doc(conn, run_id) -> int:
    from services import edgar_inventory as inv
    from services.database import platform_scope

    async with platform_scope(conn):
        run = await conn.fetchrow("SELECT * FROM portfolio.edgar_inventory_runs WHERE id = $1", run_id)
        docs = await conn.fetch("SELECT * FROM portfolio.edgar_inventory_documents WHERE run_id = $1 "
                                "ORDER BY issuer_group, accession_number", run_id)
    if run is None:
        print(f"no inventory run {run_id}")
        return 2
    concepts = await inv.inventory_concepts(conn, run_id)
    dictionary = await inv.run_label_dictionary(conn, run_id)
    DOC_PATH.write_text(inv.render_markdown(dict(run), [dict(d) for d in docs], concepts, dictionary))
    print(f"wrote {DOC_PATH} ({len(concepts)} concepts, {len(docs)} documents)")
    return 0


async def main(argv: list[str] | None = None, *, catalog=None, loader=None) -> int:
    """``catalog`` / ``loader`` replace the proxy's /model/info and the R2
    document loader (tests only)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", type=UUID)
    ap.add_argument("--spend-cap", type=float)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("--per-issuer-min", type=int, default=4)
    ap.add_argument("--per-issuer-max", type=int, default=6)
    ap.add_argument("--product-supplements", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=8000)
    ap.add_argument("--no-model-grouping", action="store_true")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--write-doc", type=UUID, default=None)
    args = ap.parse_args(argv)
    if args.concurrency < 1:
        ap.error("--concurrency must be at least 1")

    from services import edgar_inventory as inv
    from services.note_extraction import documents, proxy, schema

    conn = await common.connect()
    try:
        if args.write_doc:
            return await write_doc(conn, args.write_doc)
        if not args.cohort:
            ap.error("--cohort is required")
        if args.spend_cap is None:
            ap.error("--spend-cap is required")
        if catalog is None:
            catalog = await asyncio.to_thread(proxy.deployment_catalog)
        registry_rows = await schema.load_registry_rows(conn)
        before = (dict(proxy.CALLS), dict(inv.CALLS))
        summary = await inv.run_inventory(
            conn, args.cohort, catalog=catalog, spend_cap_usd=args.spend_cap, dry_run=args.dry_run,
            loader=loader or documents.load_document, registry_rows=registry_rows, deployment=args.model,
            max_tokens=args.max_tokens, lo=args.per_issuer_min, hi=args.per_issuer_max,
            product_supplements=args.product_supplements, group_with_model=not args.no_model_grouping,
            concurrency=args.concurrency, progress=lambda k, msg: print(f"  {k}  {msg}"))
        print(f"\nmodels in platform_model_catalog:")
        for m in summary.model_report:
            print(f"  {m['model_id']:32} {'ELIGIBLE' if m['eligible'] else 'no — ' + (m['why_not'] or '')}")
        print(f"\ndocuments planned: {summary.documents_planned}  (not loadable: {len(summary.unloadable)})")
        tokens = sum(p["terms_tokens_est"] for p in summary.plan)
        print(f"terms-page tokens (est.): {tokens:,}   estimated cost: ${summary.est_cost_usd:.4f}"
              f"   spending cap: ${args.spend_cap:.2f}")
        if args.dry_run:
            print("PLANNED_DOCUMENTS " + json.dumps([p["accession_number"] for p in summary.plan]))
            assert (dict(proxy.CALLS), dict(inv.CALLS)) == before, "dry run made a model call"
            print("DRY RUN — no model was called.")
        if summary.status == "blocked":
            print(summary.stop_reason)
            return 2
        if not args.dry_run:
            print(f"run {summary.run_id}: {summary.status} — {summary.documents_done} documents, "
                  f"{summary.items_accepted} items, {summary.items_rejected} rejected (quote not found), "
                  f"${summary.spent_usd:.4f}" + (f" — {summary.stop_reason}" if summary.stop_reason else ""))
            await write_doc(conn, summary.run_id)
        return 0
    finally:
        await conn.close()


def _run() -> int:
    """Run ``main()`` as the loop's own task and turn Ctrl+C / SIGTERM into a
    cooperative cancellation delivered INSIDE the task, so ``run_inventory``'s
    own cleanup (marking the run 'failed' with its real counts and spend)
    gets a chance to execute before the process exits. A bare
    ``asyncio.run(main())`` does not guarantee this: a SIGINT that lands while
    the loop is blocked in its selector can propagate straight out of
    ``run_until_complete`` without ever reaching the running coroutine."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(main())
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except NotImplementedError:
            pass  # Windows: no loop signal handlers; Ctrl+C falls back to KeyboardInterrupt
    try:
        return loop.run_until_complete(task)
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("interrupted — run marked failed")
        return 130
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        asyncio.set_event_loop(None)
        loop.close()


if __name__ == "__main__":
    sys.exit(_run())
