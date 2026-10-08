"""Run the cascade over many notes: a run row, the spending cap, a clean stop,
and the DRY RUN (planned calls + estimated cost, zero provider calls).

Used by scripts/run_note_extraction_pilot.py and scripts/run_note_extraction_eval.py.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field

from services.note_extraction import documents, store
from services.note_extraction.cascade import EnsembleSlots, NoteOutcome, run_note, run_static_sources
from services.note_extraction.readers import ReaderConfig, build_messages
from services.note_extraction.schema import FieldSpec
from services.note_extraction.spend import (
    Plan, PlannedCall, SpendCapReached, SpendTracker, estimate_call_cost, jev_cost_estimate,
    priced_catalog_for_bulk,
)
from services.note_extraction.trim import estimate_tokens_chars


async def persist_outcome(conn, outcome: NoteOutcome, *, run_id, ensemble_config_id) -> str:
    await store.insert_readings(conn, outcome.readings)
    return await store.insert_staging(
        conn, run_id=run_id, reference_filing_id=outcome.reference_filing_id,
        status=outcome.status, status_reason=outcome.status_reason,
        ensemble_config_id=ensemble_config_id, full_tokens_est=outcome.full_tokens_est,
        trimmed_tokens_est=outcome.trimmed_tokens_est, disagreement_count=outcome.disagreement_count,
        jev_called=outcome.jev_calls > 0, escalated=outcome.escalation_calls > 0,
        fuller_text_retry=outcome.fuller_text_retry,
        skip_second_reader_safe=outcome.skip_second_reader_safe, cost_usd=outcome.cost_usd,
        unmatched_participants=outcome.unmatched_participants, detail=outcome.detail,
        fields=outcome.staged,
    )


def plan_note(plan: Plan, doc, specs: list[FieldSpec], slots: EnsembleSlots, catalog: dict,
              *, assume_dispute_rate: float = 0.5) -> None:
    """Add one note's planned calls to ``plan`` — no provider is called."""
    tr, _rr, _et = run_static_sources(doc, specs)
    messages, _ = build_messages(specs, tr.text, filer=doc.filer_name, form_type=doc.form_type)
    chars = sum(len(m["content"]) for m in messages)
    for cfg in (slots.model_1, slots.model_2):
        dep = catalog.get(cfg.deployment)
        plan.calls.append(PlannedCall(doc.reference_filing_id, cfg.slot, cfg.deployment, chars,
                                      estimate_tokens_chars(chars), cfg.max_tokens,
                                      estimate_call_cost(dep, chars, cfg.max_tokens)))
    plan.notes += 1
    if slots.jev_route:
        plan.jev_calls_assumed += 1
        plan.calls.append(PlannedCall(doc.reference_filing_id, "jev", slots.jev_route, 0, 0, 0,
                                      jev_cost_estimate() * assume_dispute_rate))
    if slots.escalation:
        dep = catalog.get(slots.escalation.deployment)
        plan.escalation_calls_assumed += 1
        plan.calls.append(PlannedCall(doc.reference_filing_id, "escalation", slots.escalation.deployment,
                                      chars, estimate_tokens_chars(chars), slots.escalation.max_tokens,
                                      estimate_call_cost(dep, chars, slots.escalation.max_tokens)
                                      * assume_dispute_rate * 0.5))


@dataclass
class RunSummary:
    run_id: str | None
    status: str
    notes_done: int = 0
    stop_reason: str | None = None
    statuses: Counter = field(default_factory=Counter)
    spent_usd: float = 0.0
    outcomes: list[NoteOutcome] = field(default_factory=list)


async def run_notes(conn, filing_ids: list[str], specs: list[FieldSpec], slots: EnsembleSlots, *,
                    catalog: dict, spend_cap_usd: float, run_kind: str, config: dict,
                    participant_rows: list[dict], created_by=None, downloader=None,
                    keep_outcomes: bool = False, progress=None) -> RunSummary:
    """Sequential over notes (the two readers inside a note run concurrently).
    Stops CLEANLY when the next call would cross the cap: the note in flight is
    not staged; every finished note is.

    A BULK run: every model it will call must have a proxy price or a manual
    price (spend.priced_catalog_for_bulk) — refused with UnpricedModelError
    before the run row exists otherwise — and the cap then uses that price."""
    catalog = await priced_catalog_for_bulk(conn, catalog, bulk_deployments(slots))
    spend = SpendTracker(cap_usd=spend_cap_usd)
    run_id = await store.create_run(conn, run_kind=run_kind, spend_cap_usd=spend_cap_usd,
                                    config=config, ensemble_config_id=slots.ensemble_config_id,
                                    created_by=created_by, notes_planned=len(filing_ids))
    summary = RunSummary(run_id, "running")
    try:
        for fid in filing_ids:
            try:
                doc = await documents.load_document(conn, fid, downloader=downloader)
            except Exception as exc:  # noqa: BLE001 — one unreadable filing never stops the run
                summary.statuses["load_failed"] += 1
                if progress:
                    progress(fid, f"load failed: {exc}")
                continue
            try:
                outcome = await run_note(doc, specs, slots, catalog=catalog, spend=spend, run_id=run_id,
                                         origin="evaluation" if run_kind == "evaluation" else "cascade",
                                         participant_rows=participant_rows)
            except SpendCapReached as exc:
                # Calls already made for the interrupted note cost money: record
                # them (readings only — the note is not staged).
                await store.insert_readings(conn, getattr(exc, "partial_readings", []))
                summary.status, summary.stop_reason = "stopped_spend_cap", str(exc)
                break
            await persist_outcome(conn, outcome, run_id=run_id, ensemble_config_id=slots.ensemble_config_id)
            summary.notes_done += 1
            summary.statuses[outcome.status] += 1
            if keep_outcomes:
                summary.outcomes.append(outcome)
            if progress:
                progress(fid, f"{outcome.status} ${outcome.cost_usd:.5f} (run ${spend.spent_usd:.4f})")
            if summary.notes_done % 10 == 0:
                await store.update_run_progress(conn, run_id, notes_done=summary.notes_done)
        else:
            summary.status = "completed"
    except Exception as exc:  # noqa: BLE001
        summary.status, summary.stop_reason = "failed", f"{type(exc).__name__}: {exc}"
        raise
    finally:
        summary.spent_usd = spend.spent_usd
        await store.finish_run(conn, run_id, status=summary.status if summary.status != "running" else "failed",
                               notes_done=summary.notes_done, stop_reason=summary.stop_reason,
                               report={"statuses": dict(summary.statuses), "spent_usd": spend.spent_usd})
    return summary


def bulk_deployments(slots: EnsembleSlots) -> list[str]:
    """Every catalog model a run over ``slots`` will call (Jev is System One,
    not a catalog model — its cost is the explicit per-call estimate)."""
    return [d for d in (slots.model_1.deployment, slots.model_2.deployment,
                        slots.escalation.deployment if slots.escalation else None) if d]


def reader_config(slot: str, deployment: str, efforts: dict[str, str] | None = None,
                  max_tokens: int = 4000) -> ReaderConfig:
    return ReaderConfig(slot=slot, deployment=deployment,
                        reasoning_effort=(efforts or {}).get(deployment), max_tokens=max_tokens)


async def gather_docs(conn, filing_ids, *, downloader=None, limit_concurrency: int = 4):
    sem = asyncio.Semaphore(limit_concurrency)
    out = {}

    async def one(fid):
        async with sem:
            try:
                out[fid] = await documents.load_document(conn, fid, downloader=downloader)
            except Exception as exc:  # noqa: BLE001
                out[fid] = exc
    # asyncpg connections are not concurrent; load sequentially through one conn.
    for fid in filing_ids:
        await one(fid)
    return out
