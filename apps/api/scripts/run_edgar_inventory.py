"""edgarcohorts TEMPLATE STUDY — the inventory pass over a cohort's fetched filings.

Inventory, NOT extraction: the model lists every data element the selected
sections contain (cover/key terms, payoff/coupon/call/schedule, hypothetical
examples, estimated value, plan of distribution — wherever each falls in the
document, never just a straight read to the first risk-factors heading), with
exact quotes (each checked against the filing — a quote that is not found
rejects the item), mapped to an existing field only on identical meaning or
else proposed NEW; the items are then grouped, by field key rather than raw
label, into concepts. See services/edgar_inventory.py. Section coverage
(estimated value / plan of distribution found or not, per document) prints
below and is written into docs/TEMPLATE_STUDY.md.

    python3 apps/api/scripts/run_edgar_inventory.py --cohort <uuid> --dry-run --spend-cap 5
    python3 apps/api/scripts/run_edgar_inventory.py --cohort <uuid> --spend-cap 5 [--model <id>]
        [--per-issuer-min 4] [--per-issuer-max 6] [--product-supplements 2] [--no-model-grouping]
        [--concurrency 4]
    python3 apps/api/scripts/run_edgar_inventory.py --write-doc <inventory-run-uuid>
        (re)write docs/TEMPLATE_STUDY.md from a stored run — no model call
    python3 apps/api/scripts/run_edgar_inventory.py --rematch <inventory-run-uuid>
        re-check that run's stored REJECTED items against the stored filing text with the
        current quote matcher and promote those that now match — no model call
    python3 apps/api/scripts/run_edgar_inventory.py --regroup <inventory-run-uuid> --spend-cap 1
        [--model <id>] [--max-tokens 8000] [--grouping-chunk-keys 100] [--no-model-grouping]
        rerun ONLY the grouping step on that run's stored items and rewrite its concepts and
        report — no document is loaded and no inventory call is made
    --rematch X --regroup X together: promote first, then regroup (promoted items have no
    concept until the run is regrouped).

--max-tokens is the per-call output limit for BOTH the inventory calls and the grouping
calls (grouping is sent in chunks of --grouping-chunk-keys distinct field keys).

--spend-cap is REQUIRED for a run: the run stops cleanly when the next call would cross it.
--dry-run picks the documents, cuts their terms pages and prints token counts and an
estimated cost; it calls NO model.
The model is chosen at run time from platform_model_catalog rows that are 'available', not
Claude, and served by the LiteLLM proxy; if none is, the script reports BLOCKED (exit 2).
Every model call is also written to public.ai_decision_log (task_type 'edgar_inventory' /
'edgar_inventory_grouping').
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


async def rematch_regroup(conn, args, *, catalog=None, loader=None) -> int:
    """--rematch and/or --regroup on a stored run. Neither makes an inventory
    (document) call: --rematch calls no model at all; --regroup makes only
    grouping calls (none with --no-model-grouping)."""
    from services import edgar_inventory as inv
    from services.note_extraction import documents, proxy

    calls_before = dict(inv.CALLS)
    if args.rematch:
        try:
            r = await inv.rematch_run(conn, args.rematch, loader=loader or documents.load_document,
                                      progress=lambda k, msg: print(f"  {k}  {msg}"))
        except LookupError as exc:
            print(str(exc))
            return 2
        print(f"rematch {r['run_id']}: {r['candidates']} rejected item(s) re-checked across "
              f"{r['documents_checked']} document(s) — {r['promoted']} promoted, {r['still_rejected']} still "
              f"rejected" + (f", {len(r['unloadable'])} document(s) not loadable" if r["unloadable"] else ""))
        if r["promoted"] and not args.regroup:
            print("  promoted items have no concept yet — run --regroup on this run to fold them in")
    if args.regroup:
        if catalog is None and not args.no_model_grouping:
            catalog = await asyncio.to_thread(proxy.deployment_catalog)
        try:
            g = await inv.regroup_run(conn, args.regroup, catalog=catalog, spend_cap_usd=args.spend_cap,
                                      deployment=args.model, use_model=not args.no_model_grouping,
                                      max_tokens=args.max_tokens, chunk_keys=args.grouping_chunk_keys)
        except LookupError as exc:
            print(str(exc))
            return 2
        except inv.InventoryBlocked as exc:
            print(str(exc))
            return 2
        print(f"regroup {g['run_id']}: {g['grouping_method']} — {g['concepts']} concepts, "
              f"${g['spent_usd']:.4f}")
        for o in g["grouping_chunks"] or []:
            print(f"  chunk {o['chunk']}/{o['of']} ({o['keys']} keys): {o['status']}"
                  + (f" — {o['error']}" if o.get("error") else ""))
        if g["grouping_note"]:
            print(f"  {g['grouping_note']}")
    assert inv.CALLS["inventory"] == calls_before["inventory"], "an inventory (document) call was made"
    await write_doc(conn, args.regroup or args.rematch)
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
    ap.add_argument("--grouping-chunk-keys", type=int, default=100)
    ap.add_argument("--no-model-grouping", action="store_true")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--write-doc", type=UUID, default=None)
    ap.add_argument("--rematch", type=UUID, default=None)
    ap.add_argument("--regroup", type=UUID, default=None)
    args = ap.parse_args(argv)
    if args.concurrency < 1:
        ap.error("--concurrency must be at least 1")
    if args.grouping_chunk_keys < 1:
        ap.error("--grouping-chunk-keys must be at least 1")
    if args.rematch and args.regroup and args.rematch != args.regroup:
        ap.error("--rematch and --regroup together must name the same run")
    if args.regroup and not args.no_model_grouping and args.spend_cap is None:
        ap.error("--spend-cap is required for --regroup (it makes grouping calls); "
                 "or pass --no-model-grouping")

    from services import edgar_inventory as inv
    from services.note_extraction import documents, proxy, schema

    conn = await common.connect()
    try:
        if args.write_doc:
            return await write_doc(conn, args.write_doc)
        if args.rematch or args.regroup:
            return await rematch_regroup(conn, args, catalog=catalog, loader=loader)
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
        cov = summary.sections_coverage
        if cov.get("documents"):
            print(f"section coverage: estimated value found in {cov['estimated_value_found']}/{cov['documents']} "
                  f"(heading {cov['estimated_value_found_heading']}, content {cov['estimated_value_found_content']}), "
                  f"plan of distribution found in {cov['plan_of_distribution_found']}/{cov['documents']} "
                  f"(heading {cov['plan_of_distribution_found_heading']}, content {cov['plan_of_distribution_found_content']})")
            if cov["missing_estimated_value"]:
                print(f"  missing estimated value: {', '.join(cov['missing_estimated_value'])}")
            if cov["missing_plan_of_distribution"]:
                print(f"  missing plan of distribution: {', '.join(cov['missing_plan_of_distribution'])}")
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
