"""noteextractb1 PILOT: ~2,000 ready notes across issuers and years through the
FULL cascade with whichever ensemble is ACTIVE for task 'note_terms_extraction'.
Writes readings and STAGING only — never the security master.

Reports disagreement, Jev, escalation and needs_review rates, cost per note,
and how often skip-second-reader would have been safe.

    python3 apps/api/scripts/run_note_extraction_pilot.py --dry-run --spend-cap 25
    python3 apps/api/scripts/run_note_extraction_pilot.py --dry-run --spend-cap 25 --cohort <uuid>
    python3 apps/api/scripts/run_note_extraction_pilot.py --spend-cap 25 [--limit 2000]
        [--include-corpus] [--escalation-model gpt-5-mini] [--effort gpt-oss-120b=low]

--spend-cap is REQUIRED: the run stops cleanly when the next call would cross it.
--dry-run prints the planned calls and an estimated cost and calls NO provider.
--cohort <uuid> replaces the sampler: exactly the cohort's ready_for_extraction members,
in cohort order (edgarcohorts). --limit and --include-corpus are then ignored.
--include-corpus also reads the stored 2025Q1 corpus filings that sprint A's own
rules classify as final pricing supplements (Task 1a: zero manifest rows are
ready_for_extraction today).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import UUID

import _note_extraction_common as common


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", type=UUID, default=None)
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--spend-cap", type=float, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-corpus", action="store_true")
    ap.add_argument("--escalation-model", default=common.escalation_default())
    ap.add_argument("--effort", action="append", help="deployment=low|medium|high (repeatable)")
    ap.add_argument("--max-tokens", type=int, default=4000)
    args = ap.parse_args(argv)

    from services.note_extraction import cascade, documents, proxy, runner, selection
    from services.note_extraction.spend import Plan

    conn = await common.connect()
    try:
        specs, catalog, parts = await common.load_context(conn)
        ens = await cascade.active_ensemble(conn)
        if ens is None:
            print("BLOCKED: no ACTIVE ensemble for task 'note_terms_extraction'. Pick Model 1, "
                  "Model 2 and System One in the ensemble picker after the evaluation.")
            return 2
        efforts = common.parse_efforts(args.effort)
        slots = cascade.EnsembleSlots(
            model_1=runner.reader_config("model_1", ens["model_1"], efforts, args.max_tokens),
            model_2=runner.reader_config("model_2", ens["model_2"], efforts, args.max_tokens),
            jev_route=ens["jev_route"],
            escalation=(runner.reader_config("escalation", args.escalation_model, efforts, args.max_tokens)
                        if args.escalation_model else None),
            ensemble_config_id=str(ens["id"]),
        )
        for cfg in (slots.model_1, slots.model_2) + ((slots.escalation,) if slots.escalation else ()):
            dep = catalog.get(cfg.deployment)
            if dep is None or dep.duplicate:
                print(f"BLOCKED: deployment '{cfg.deployment}' is "
                      f"{'missing from' if dep is None else 'load-balanced on'} the proxy")
                return 2
        if args.cohort:
            notes = await selection.cohort_notes(conn, args.cohort)
            counts = {"cohort": str(args.cohort), "cohort_ready": len(notes), "selected": len(notes)}
        else:
            notes, counts = await selection.select_notes(conn, limit=args.limit,
                                                         include_corpus=args.include_corpus)
        print(f"selection: {counts}")
        if not notes:
            print("nothing to read")
            return 0

        if args.dry_run:
            print("PLANNED_NOTES " + json.dumps([n.reference_filing_id for n in notes]))
            before = dict(proxy.CALLS)
            plan = Plan()
            for n in notes:
                try:
                    doc = await documents.load_document(conn, n.reference_filing_id)
                except Exception as exc:  # noqa: BLE001
                    plan.notes_skipped.append({"note": n.reference_filing_id, "error": str(exc)[:120]})
                    continue
                runner.plan_note(plan, doc, specs, slots, catalog)
            common.print_plan(plan, args.spend_cap)
            assert proxy.CALLS == before, "dry run made a provider call"
            return 0

        summary = await runner.run_notes(
            conn, [n.reference_filing_id for n in notes], specs, slots, catalog=catalog,
            spend_cap_usd=args.spend_cap, run_kind="pilot",
            config={"cohort": str(args.cohort) if args.cohort else None, "limit": args.limit, "include_corpus": args.include_corpus, "efforts": efforts,
                    "escalation_model": args.escalation_model, "selection": counts},
            participant_rows=parts, progress=lambda f, msg: print(f"  {f}  {msg}"))
        report = await pilot_report(conn, summary.run_id)
        from services.note_extraction import store
        await store.finish_run(conn, summary.run_id, status=summary.status, notes_done=summary.notes_done,
                               stop_reason=summary.stop_reason, report=report)
        print(json.dumps(report, indent=2, default=str))
        return 0
    finally:
        await conn.close()


async def pilot_report(conn, run_id) -> dict:
    rows = await conn.fetch(
        """SELECT status, disagreement_count, jev_called, escalated, fuller_text_retry,
                  skip_second_reader_safe, cost_usd
             FROM portfolio.note_extraction_staging WHERE run_id = $1""", run_id)
    n = len(rows)
    from services.note_extraction.metrics import skip_second_reader

    def rate(pred):
        return (sum(1 for r in rows if pred(r)) / n) if n else None
    return {
        "notes": n,
        "disagreement_rate": rate(lambda r: r["disagreement_count"] > 0),
        "jev_rate": rate(lambda r: r["jev_called"]),
        "escalation_rate": rate(lambda r: r["escalated"]),
        "needs_review_rate": rate(lambda r: r["status"] == "needs_review"),
        "failed_rate": rate(lambda r: r["status"] == "failed"),
        "fuller_text_retry_rate": rate(lambda r: r["fuller_text_retry"]),
        "cost_per_note": (sum(float(r["cost_usd"]) for r in rows) / n) if n else None,
        "skip_second_reader": skip_second_reader([r["skip_second_reader_safe"] for r in rows]),
    }


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
