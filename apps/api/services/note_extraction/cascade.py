"""The per-note cascade: rules -> EdgarTools -> trim -> Model 1 + Model 2 ->
compare -> (fuller-text retry) -> Jev on disputes -> escalation -> staging.

Every value any source produced is written as a reading; the resolved values
go to staging. Model 2 is ALWAYS called in B1 — skip-second-reader is only
MEASURED (``skip_second_reader_safe`` on the staging row). Nothing here writes
to the security master.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from services.note_extraction import compare as cmp
from services.note_extraction import edgartools_reader, jev, participants, readers, rules, trim
from services.note_extraction.documents import FilingDocument
from services.note_extraction.schema import (
    CRITICAL_FIELDS, KIND_PARTICIPANTS, FieldSpec, normalize, subset,
)
from services.note_extraction.spend import SpendCapReached, SpendTracker
from services.note_extraction.store import CALL_FIELD, ReadingRow, StagedField

TASK_KEY = "note_terms_extraction"


@dataclass
class EnsembleSlots:
    model_1: readers.ReaderConfig
    model_2: readers.ReaderConfig
    jev_route: str | None
    escalation: readers.ReaderConfig | None
    ensemble_config_id: str | None = None

    def __post_init__(self):
        if self.model_1.deployment == self.model_2.deployment:
            raise ValueError("Model 1 and Model 2 must be two different deployments")


@dataclass
class NoteOutcome:
    reference_filing_id: str
    status: str                                  # verified | needs_review | failed
    status_reason: str | None
    readings: list[ReadingRow] = field(default_factory=list)
    staged: list[StagedField] = field(default_factory=list)
    detail: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    jev_calls: int = 0
    escalation_calls: int = 0
    reader_calls: list = field(default_factory=list)
    disagreement_count: int = 0
    fuller_text_retry: bool = False
    skip_second_reader_safe: bool | None = None
    unmatched_participants: list = field(default_factory=list)
    full_tokens_est: int | None = None
    trimmed_tokens_est: int | None = None


async def active_ensemble(conn, task_key: str = TASK_KEY) -> dict | None:
    row = await conn.fetchrow(
        "SELECT e.*, s.model_route AS jev_route FROM ai_ensemble_configs e "
        "JOIN ai_system_one_models s ON s.key = e.system_one_model "
        "WHERE e.task_key = $1 AND e.is_active", task_key)
    return dict(row) if row else None


def _reading_from_evidence(doc, e: cmp.Evidence, spec_key: str, *, run_id, origin, call=None,
                           ensemble_config_id=None, prompt_version=None, extra=None) -> ReadingRow:
    r = ReadingRow(
        reference_filing_id=doc.reference_filing_id, field_key=spec_key, source=e.source,
        value=e.value, value_normalized=e.normalized, run_id=run_id, origin=origin,
        source_quote=e.quote, quote_verified=e.quote_verified, value_in_quote=e.value_in_quote,
        raw_char_start=e.location.raw_start if e.location and e.location.raw_start is not None else None,
        raw_char_end=e.location.raw_end if e.location and e.location.raw_start is not None else None,
        ensemble_config_id=ensemble_config_id, prompt_version=prompt_version, metadata=extra or {},
    )
    if call is not None:
        r.deployment_name = call.deployment
        r.provider_model = call.provider_model
        r.proxy_model_id = call.proxy_model_id
        r.prompt_prefix_hash = call.prefix_hash
        r.reasoning_effort = call.reasoning_effort
        r.call_id = call.call_id
        r.status = call.status
    e.reading_key = r.id
    return r


def _call_row(doc, call: readers.ReaderCall, *, run_id, origin, ensemble_config_id, extra=None) -> ReadingRow:
    return ReadingRow(
        reference_filing_id=doc.reference_filing_id, field_key=CALL_FIELD, source=call.slot,
        run_id=run_id, origin=origin, status=call.status, error=call.error,
        deployment_name=call.deployment, provider_model=call.provider_model,
        proxy_model_id=call.proxy_model_id, ensemble_config_id=ensemble_config_id,
        prompt_version=readers.PROMPT_VERSION, prompt_prefix_hash=call.prefix_hash,
        reasoning_effort=call.reasoning_effort, input_tokens=call.input_tokens,
        output_tokens=call.output_tokens, cached_tokens=call.cached_tokens, cost_usd=call.cost_usd,
        latency_ms=call.latency_ms, call_id=call.call_id,
        metadata={"response_format": call.response_format, "prompt_chars": call.prompt_chars,
                  "filings_in_call": call.filings_in_call,
                  "attempted_fallbacks": call.attempted_fallbacks, **(extra or {})},
    )


def run_static_sources(doc: FilingDocument, specs: list[FieldSpec]):
    """Rules + EdgarTools + trim. No network beyond what the caller already did."""
    tr = trim.trim(doc.text)
    rr = rules.run_rules(doc.text, distribution_spans=tr.distribution_spans)
    et = edgartools_reader.read_html(doc.html)
    return tr, rr, et


async def run_note(doc: FilingDocument, specs: list[FieldSpec], slots: EnsembleSlots, *,
                   catalog: dict, spend: SpendTracker | None, run_id=None, origin: str = "cascade",
                   participant_rows: list[dict] | None = None, static=None) -> NoteOutcome:
    """Run the full cascade on one note. Raises SpendCapReached before any call
    that would exceed the cap (the caller stops the run cleanly). The raised
    exception carries ``partial_readings`` — every call already MADE for this
    note — so the run can record that spend instead of losing it."""
    out = NoteOutcome(doc.reference_filing_id, "failed", None)
    try:
        return await _run_note(out, doc, specs, slots, catalog=catalog, spend=spend, run_id=run_id,
                               origin=origin, participant_rows=participant_rows, static=static)
    except SpendCapReached as exc:
        exc.partial_readings = list(out.readings)
        raise


async def _read_pair(out: NoteOutcome, doc, coros, *, run_id, origin, ens_id, extra=None):
    """Run reader calls concurrently. A call that completed is recorded even if
    its sibling hit the spending cap; then the cap is re-raised."""
    results = await asyncio.gather(*coros, return_exceptions=True)
    calls = []
    cap = None
    for r in results:
        if isinstance(r, readers.ReaderCall):
            calls.append(r)
            out.reader_calls.append(r)
            out.readings.append(_call_row(doc, r, run_id=run_id, origin=origin, ensemble_config_id=ens_id,
                                          extra=extra))
        elif isinstance(r, SpendCapReached):
            cap = r
        elif isinstance(r, BaseException):
            raise r
    if cap is not None:
        raise cap
    return calls


async def _run_note(out: NoteOutcome, doc: FilingDocument, specs: list[FieldSpec], slots: EnsembleSlots, *,
                    catalog: dict, spend: SpendTracker | None, run_id, origin: str,
                    participant_rows: list[dict] | None, static) -> NoteOutcome:
    by_key = {s.key: s for s in specs}
    ens_id = slots.ensemble_config_id
    tr, rr, et = static or await asyncio.to_thread(run_static_sources, doc, specs)
    out.full_tokens_est, out.trimmed_tokens_est = tr.full_tokens_est, tr.trimmed_tokens_est

    # (a) rules and (b) EdgarTools readings
    independent: dict[str, list[cmp.Evidence]] = {}
    for key, hit in rr.hits.items():
        if key not in by_key:
            continue
        e = cmp.evidence(doc, by_key[key], "rules", hit.value, hit.quote)
        independent.setdefault(key, []).append(e)
        out.readings.append(_reading_from_evidence(doc, e, key, run_id=run_id, origin=origin,
                                                   ensemble_config_id=ens_id))
    for key, v in et.fields.items():
        if key not in by_key:
            continue
        e = cmp.evidence(doc, by_key[key], "edgartools", v["value"], v["quote"])
        independent.setdefault(key, []).append(e)
        out.readings.append(_reading_from_evidence(
            doc, e, key, run_id=run_id, origin=origin, ensemble_config_id=ens_id,
            extra={"edgartools_version": et.version, "network_attempts": len(et.network_attempts)}))
    out.detail["edgartools"] = {"ok": et.ok, "error": et.error, "version": et.version,
                                "license": et.license, "tables": et.tables_found,
                                "network_attempts": len(et.network_attempts)}
    out.detail["rules_rejected"] = rr.rejected

    # (d) two readers — two separate calls, two different deployments, concurrently
    tags = [f"note:{doc.reference_filing_id}"] + ([f"run:{run_id}"] if run_id else [])
    c1, c2 = await _read_pair(out, doc, [
        readers.read(slots.model_1, specs, tr.text, filer=doc.filer_name, form_type=doc.form_type,
                     catalog=catalog, spend=spend, tags=tags),
        readers.read(slots.model_2, specs, tr.text, filer=doc.filer_name, form_type=doc.form_type,
                     catalog=catalog, spend=spend, tags=tags),
    ], run_id=run_id, origin=origin, ens_id=ens_id, extra={"trimmed_tokens_est": tr.trimmed_tokens_est})
    if not c1.usable and not c2.usable:
        out.status, out.status_reason = "failed", f"both readers failed: {c1.error} | {c2.error}"
        out.cost_usd = sum(c.cost_usd or 0 for c in (c1, c2))
        return out

    def ev_for(call, key):
        if not call.usable:
            return None
        f = call.fields.get(key) or {}
        return cmp.evidence(doc, by_key[key], call.slot, f.get("value"), f.get("quote"))

    m1 = {k: ev_for(c1, k) for k in by_key}
    m2 = {k: ev_for(c2, k) for k in by_key}

    # (c) retry once with fuller text when BOTH readers return null on a critical field
    both_null = [k for k in by_key if k in CRITICAL_FIELDS and m1[k] is not None and m2[k] is not None
                 and m1[k].normalized is None and m2[k].normalized is None]
    if both_null:
        out.fuller_text_retry = True
        fuller = trim.fuller_text(doc.text)
        sub = subset(specs, both_null)
        r1, r2 = await _read_pair(out, doc, [
            readers.read(slots.model_1, sub, fuller, filer=doc.filer_name, form_type=doc.form_type,
                         catalog=catalog, spend=spend, tags=tags + ["retry:fuller_text"]),
            readers.read(slots.model_2, sub, fuller, filer=doc.filer_name, form_type=doc.form_type,
                         catalog=catalog, spend=spend, tags=tags + ["retry:fuller_text"]),
        ], run_id=run_id, origin=origin, ens_id=ens_id, extra={"retry": "fuller_text"})
        for rc, target in ((r1, m1), (r2, m2)):
            if rc.usable:
                for k in both_null:
                    target[k] = ev_for(rc, k)

    for calls_map, call in ((m1, c1), (m2, c2)):
        for k, e in calls_map.items():
            if e is not None:
                src_call = call
                for rc in out.reader_calls[2:]:
                    if rc.slot == call.slot and rc.usable and k in rc.fields:
                        src_call = rc
                out.readings.append(_reading_from_evidence(
                    doc, e, k, run_id=run_id, origin=origin, call=src_call,
                    ensemble_config_id=ens_id, prompt_version=readers.PROMPT_VERSION))

    # (e) compare in code
    comps = {k: cmp.compare_field(by_key[k], m1[k], m2[k], independent.get(k)) for k in by_key}
    disputed = [c for c in comps.values() if c.outcome == "disputed"]
    out.disagreement_count = len(disputed)
    out.skip_second_reader_safe = cmp.skip_second_reader_would_be_safe(
        sorted(CRITICAL_FIELDS & set(by_key)), m1, independent, comps)

    resolved: dict[str, dict] = {}
    for k, c in comps.items():
        if c.outcome == "verified_agreement":
            resolved[k] = {"value": c.value, "resolution": "verified_agreement", "evidence": c.winner}
        elif c.outcome == "agreed_null":
            resolved[k] = {"value": None, "resolution": "agreed_null", "evidence": None}

    # (f) Jev: ONE call for all of this note's disputed questions
    if disputed and slots.jev_route:
        jc = await jev.ask(slots.jev_route, doc, disputed, fallback_text=tr.text[:6000], spend=spend)
        out.jev_calls = 1
        out.readings.append(ReadingRow(
            reference_filing_id=doc.reference_filing_id, field_key=CALL_FIELD, source="jev",
            run_id=run_id, origin=origin, status="ok" if jc.status == "ok" else "failed", error=jc.error,
            deployment_name=jc.route, provider_model=jc.provider_model, ensemble_config_id=ens_id,
            cost_usd=jc.cost_usd, latency_ms=jc.latency_ms, call_id=jc.call_id,
            metadata={"questions": sorted(a.spec.key for a in disputed), "state_chars": jc.state_chars}))
        for key, a in jc.answers.items():
            cand = a.candidate
            value = None if cand is None else cand.value
            best = cand.best if cand else None
            row = ReadingRow(
                reference_filing_id=doc.reference_filing_id, field_key=key, source="jev",
                value=value, value_normalized=normalize(by_key[key], value), run_id=run_id,
                origin=origin, deployment_name=jc.route, provider_model=jc.provider_model,
                ensemble_config_id=ens_id, probability=a.probability, call_id=jc.call_id,
                source_quote=best.quote if best else None,
                quote_verified=best.quote_verified if best else None,
                metadata={"choice": a.choice, "accepted": a.accepted})
            out.readings.append(row)
            if a.accepted:
                resolved[key] = {"value": value, "resolution": "jev", "evidence": best,
                                 "probability": a.probability, "reading_id": row.id}

    # (g) escalation for what Jev could not settle confidently
    remaining = [c for c in disputed if c.spec.key not in resolved]
    if remaining and slots.escalation is not None:
        sub = subset(specs, [c.spec.key for c in remaining])
        (ec,) = await _read_pair(out, doc, [
            readers.read(slots.escalation, sub, tr.text, filer=doc.filer_name,
                         form_type=doc.form_type, catalog=catalog, spend=spend,
                         tags=tags + ["escalation"])], run_id=run_id, origin=origin, ens_id=ens_id)
        out.escalation_calls = 1
        if ec.usable:
            for c in remaining:
                f = ec.fields.get(c.spec.key) or {}
                e = cmp.evidence(doc, c.spec, "escalation", f.get("value"), f.get("quote"))
                row = _reading_from_evidence(doc, e, c.spec.key, run_id=run_id, origin=origin, call=ec,
                                             ensemble_config_id=ens_id, prompt_version=readers.PROMPT_VERSION)
                out.readings.append(row)
                if e.normalized is not None and e.supports:
                    resolved[c.spec.key] = {"value": e.value, "resolution": "escalation", "evidence": e,
                                            "reading_id": row.id}

    # staging
    needs_review = []
    for k, spec in by_key.items():
        r = resolved.get(k)
        if r is None:
            crit = spec.critical
            if crit:
                needs_review.append(k)
            out.staged.append(StagedField(k, None, "unresolved", crit, crit))
            continue
        e = r.get("evidence")
        loc = e.location if e else None
        out.staged.append(StagedField(
            k, r["value"], r["resolution"], spec.critical, False,
            winning_reading_id=r.get("reading_id") or (e.reading_key if e else None),
            source_quote=e.quote if e else None,
            raw_char_start=loc.raw_start if loc and loc.raw_start is not None else None,
            raw_char_end=loc.raw_end if loc and loc.raw_start is not None else None,
            probability=r.get("probability")))

    # distribution participants: rules names + resolved list, matched, nothing dropped
    names = [{"name": p["name"], "source": "rules"} for p in rr.participant_names]
    dist_key = next((s.key for s in specs if s.kind == KIND_PARTICIPANTS), None)
    if dist_key and isinstance((resolved.get(dist_key) or {}).get("value"), list):
        names += [{"name": p.get("name"), "source": "resolved", "role": p.get("role")}
                  for p in resolved[dist_key]["value"] if isinstance(p, dict)]
    match = participants.match_names(names, participant_rows or [])
    out.unmatched_participants = match.unmatched
    out.detail["participants_matched"] = match.matched

    out.cost_usd = sum(r.cost_usd or 0 for r in out.readings if r.field_key == CALL_FIELD)
    if needs_review:
        out.status, out.status_reason = "needs_review", "unresolved critical: " + ", ".join(needs_review)
    else:
        out.status, out.status_reason = "verified", None
    return out


__all__ = ["EnsembleSlots", "NoteOutcome", "run_note", "run_static_sources", "active_ensemble",
           "SpendCapReached", "TASK_KEY"]
