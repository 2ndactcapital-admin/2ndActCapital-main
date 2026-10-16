"""GOLD PRE-FILL (goldset.structural): one model reads each gold-batch note so the
reviewer starts from a proposed value instead of a blank field.

One extraction run per batch, run_kind 'gold_prefill'. Per note, the SAME
pieces the cascade uses, minus the second reader, Jev and escalation:

    rules -> EdgarTools -> trim -> ONE reader (slot 'model_1')
          -> rules-only fields -> derivations -> self-checks

Every value is written as a reading carrying the run's id (origin 'cascade',
metadata.gold_prefill = true). Nothing is staged — a single reader's answer is
not a resolved value under the cascade's rules — and nothing touches
securities_global_note_terms. The self-check findings go to the run's report.

NOTEFIX1: the reader call validates PER FIELD now (readers.read /
schema.parse_reader_output). A field that fails its own shape is dropped
(every other field is kept) and recorded, with a coerced field flagged
separately, in ``PrefillNote.field_issues`` and the run's per-note report.
The whole note only goes ``reader_failed`` when the response is not
parseable JSON at all — the bug that failed note 6380efa7-5a87-4202-8b6c-
3bcaf34030aa in the gold-1 pre-fill (one range-shaped field under the old
scalar registry shape) can no longer happen.

ANCHORING: the reviewer sees this model's values first, so this model's own
score against the gold set is biased upward. ``ANCHORING_NOTE`` is shown on the
review screen and written to the run's config.

GUARDS, all before the run row exists:
  * Claude models and OpenRouter routes are refused (ruled out for this work).
  * ``model_catalog.assert_model_allowed_for_task`` with task
    'note_terms_extraction' and NO org: a public_data_only model (gpt-5-mini)
    is ACCEPTED — the gold set is public SEC filings.
  * ``spend.priced_catalog_for_bulk``: a model with neither a proxy price nor a
    manual catalog price is refused (UnpricedModelError).
  * ``max_tokens`` below MIN_MAX_TOKENS is refused: gpt-5-mini is a reasoning
    model and returns an EMPTY answer when its reasoning eats a small budget.
DRY RUN writes nothing (no run row, no reading) and calls no model.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field

from services.note_extraction import checks, derive, documents, participants, readers, rules, store
from services.note_extraction import compare as cmp
from services.note_extraction.cascade import TASK_KEY, _call_row, _reading_from_evidence, run_static_sources
from services.note_extraction.schema import METHOD_MODEL, METHOD_RULES, FieldSpec, normalize, reader_specs
from services.note_extraction.spend import (
    SpendCapReached, SpendTracker, estimate_call_cost, priced_catalog_for_bulk,
)
from services.note_extraction.store import ReadingRow
from services.note_extraction.trim import estimate_tokens_chars

RUN_KIND = "gold_prefill"
SLOT = "model_1"
MIN_MAX_TOKENS = 32000
ANCHORING_NOTE = (
    "Pre-filled by {model}. The reviewer sees these values first, so {model}'s own score against this "
    "gold set is biased upward and must be reported with that caveat.")


class PrefillRefused(RuntimeError):
    pass


async def existing_prefill_runs(conn, batch: str) -> list[str]:
    from services.database import platform_scope

    async with platform_scope(conn):
        rows = await conn.fetch(
            "SELECT id FROM portfolio.note_extraction_runs WHERE run_kind = $1 AND config->>'batch' = $2",
            RUN_KIND, batch)
    return [str(r["id"]) for r in rows]


async def batch_has_gold_values(conn, filing_ids: list[str]) -> bool:
    from services.database import platform_scope

    if not filing_ids:
        return False
    async with platform_scope(conn):
        return bool(await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM portfolio.note_gold_values "
            "WHERE reference_filing_id = ANY($1::uuid[]) AND valid_to IS NULL)", filing_ids))


async def check_supersede(conn, batch: str, filing_ids: list[str], *, supersede: bool) -> None:
    """Refuse a SECOND gold_prefill run for ``batch`` unless ``--supersede`` is
    passed, and refuse it even then once a human has recorded a gold value for
    any note in the batch (goldset.structural Task 5). A batch with no prior
    gold_prefill run never needs ``--supersede``. Nothing is written either
    way — this only decides whether ``run_prefill`` may be called. The review
    screen (``gold.prefill_runs_for``) already serves the newest run per note
    by ``started_at``, so a superseding run is picked up with no further
    change once it is allowed to exist."""
    prior = await existing_prefill_runs(conn, batch)
    if not prior:
        return
    if not supersede:
        raise PrefillRefused(
            f"batch {batch!r} already has {len(prior)} gold_prefill run(s) — pass --supersede to add another")
    if await batch_has_gold_values(conn, filing_ids):
        raise PrefillRefused(
            f"batch {batch!r} already has a human gold value — --supersede is refused once review has started")


def anchoring_note(model: str) -> str:
    return ANCHORING_NOTE.format(model=model)


def _is_claude(*values) -> bool:
    return any(v and ("claude" in v.lower() or "anthropic" in v.lower()) for v in values)


def _is_openrouter(*values) -> bool:
    return any(v and "openrouter" in v.lower() for v in values)


async def check_model(conn, model: str, catalog: dict, *, max_tokens: int) -> dict:
    """Run every guard; return the priced catalog. Raises PrefillRefused,
    model_catalog.PublicDataOnlyError or spend.UnpricedModelError."""
    from services.database import platform_scope
    from services.model_catalog import assert_model_allowed_for_task

    if max_tokens < MIN_MAX_TOKENS:
        raise PrefillRefused(f"--max-tokens {max_tokens} is below {MIN_MAX_TOKENS}: a reasoning model "
                             f"returns an empty answer when reasoning uses up a small budget")
    dep = catalog.get(model)
    if _is_claude(model, getattr(dep, "upstream", None)):
        raise PrefillRefused(f"'{model}' is a Claude model — ruled out for this work")
    if _is_openrouter(model, getattr(dep, "upstream", None)):
        raise PrefillRefused(f"'{model}' is an OpenRouter route — not used")
    if dep is None:
        raise PrefillRefused(f"'{model}' is not served by the LiteLLM proxy under that name")
    async with platform_scope(conn):
        await assert_model_allowed_for_task(conn, model, task_key=TASK_KEY, org_id=None)
        return await priced_catalog_for_bulk(conn, catalog, [model])


@dataclass
class PrefillNote:
    reference_filing_id: str
    status: str                      # ok | reader_failed | load_failed
    error: str | None = None
    readings: list[ReadingRow] = field(default_factory=list)
    values: dict = field(default_factory=dict)
    self_checks: list[dict] = field(default_factory=list)
    field_issues: dict = field(default_factory=dict)   # notefix1: key -> {error, raw_value, coerced}
    input_disagreements: list[str] = field(default_factory=list)   # notefix2 Task 4/1c
    calculation_agent_affiliation: dict | None = None   # notefix2 F1
    cost_usd: float = 0.0


async def prefill_note(doc, specs: list[FieldSpec], cfg: readers.ReaderConfig, *, catalog: dict,
                       spend: SpendTracker | None, run_id,
                       participant_rows: list[dict] | None = None,
                       issuer_groups: dict[str, str] | None = None) -> PrefillNote:
    """notefix2 Task 1c / Task 4: ``values`` (what every derivation in
    ``derive.derive_all`` reads) is the RESOLVED value of each field, never
    model_1's raw, unverified reading alone. With only one reader, "resolved"
    here means: the model's answer stands when rules found nothing or agrees
    with it; when an INDEPENDENT rules reading disagrees, the field is left
    unresolved (``input_disagreements``) rather than trusting either one — the
    exact mechanism the gold-1 tenor_months bug (note #1) needed: rules read
    the correct 2019-01-28 pricing_date via its own label match while model_1
    read the filing's own 2018 typo sentence; with this guard neither
    resolves pricing_date alone, so tenor_months (and no_call_months) are
    correctly left null rather than computed from the wrong one."""
    by_key = {s.key: s for s in specs}
    rspecs = reader_specs(specs)
    out = PrefillNote(doc.reference_filing_id, "ok")
    origin = "cascade"
    meta = {"gold_prefill": True}
    tr, rr, et = await asyncio.to_thread(run_static_sources, doc, specs)
    pricing_hint = rr.hits["pricing_date"].value if "pricing_date" in rr.hits else None
    hypo_span = rr.extras["hypothetical_span"].value if "hypothetical_span" in rr.extras else None

    def _ev(spec_key, source, value, quote):
        return cmp.evidence(doc, by_key[spec_key], source, value, quote,
                            reject_if_equals=pricing_hint, hypothetical_span=hypo_span)

    rules_ev: dict[str, cmp.Evidence] = {}
    for key, hit in rr.hits.items():
        if key not in by_key:
            continue
        e = _ev(key, "rules", hit.value, hit.quote)
        rules_ev[key] = e
        out.readings.append(_reading_from_evidence(doc, e, key, run_id=run_id, origin=origin, extra=dict(meta)))
    for key, v in et.fields.items():
        if key not in by_key:
            continue
        e = _ev(key, "edgartools", v["value"], v["quote"])
        out.readings.append(_reading_from_evidence(
            doc, e, key, run_id=run_id, origin=origin,
            extra={**meta, "edgartools_version": et.version}))

    tags = [f"note:{doc.reference_filing_id}", f"run:{run_id}", "usage:gold_prefill"]
    call = await readers.read(cfg, rspecs, tr.text, filer=doc.filer_name, form_type=doc.form_type,
                              catalog=catalog, spend=spend, tags=tags)
    out.readings.append(_call_row(doc, call, run_id=run_id, origin=origin, ensemble_config_id=None,
                                  extra={**meta, "trimmed_tokens_est": tr.trimmed_tokens_est}))
    out.cost_usd = call.cost_usd or 0.0
    values: dict = {}
    if call.usable:
        for s in rspecs:
            f = call.fields.get(s.key) or {}
            e = _ev(s.key, SLOT, f.get("value"), f.get("quote"))
            out.readings.append(_reading_from_evidence(doc, e, s.key, run_id=run_id, origin=origin, call=call,
                                                       prompt_version=readers.PROMPT_VERSION, extra=dict(meta)))
            rule_e = rules_ev.get(s.key)
            if e.normalized is not None:
                if rule_e is not None and rule_e.normalized is not None and rule_e.normalized != e.normalized:
                    out.input_disagreements.append(s.key)   # never resolve either side alone
                    continue
                values[s.key] = e.value
            elif rule_e is not None and rule_e.normalized is not None:
                values[s.key] = rule_e.value   # the reader found nothing; the rule's labeled value stands
        out.field_issues = call.field_issues
    else:
        out.status, out.error = "reader_failed", call.error

    for k, spec in by_key.items():
        if spec.extraction_method == METHOD_RULES and k in rules_ev and rules_ev[k].normalized is not None:
            values[k] = rules_ev[k].value

    for d in derive.derive_all(values, has_call_language=rules.has_call_language(doc.text),
                               has_memory_language=rules.has_memory_language(doc.text)):
        spec = by_key.get(d.field_key)
        if spec is None:
            continue
        if spec.extraction_method == METHOD_MODEL and values.get(d.field_key) is not None:
            continue  # a STATED value is never overwritten by a derived one
        out.readings.append(ReadingRow(
            reference_filing_id=doc.reference_filing_id, field_key=d.field_key, source="derived",
            value=d.value, value_normalized=normalize(spec, d.value), run_id=run_id, origin=origin,
            metadata={**meta, "derived": True, "derived_from": d.derived_from, "basis": d.basis,
                      "inputs": d.inputs}))
        values[d.field_key] = d.value

    # distribution participants (notefix2 F1): the same role-resolution rule
    # cascade.py applies, done here too since gold_prefill is a real read
    # path the reviewer sees values from.
    issuer_group = (issuer_groups or {}).get(doc.cik)
    if isinstance(values.get("distribution"), list):
        members, _dist_unmatched = participants.match_distribution(values["distribution"], participant_rows or [])
        values["distribution"] = participants.resolve_distribution_roles(members, issuer_group)
    out.calculation_agent_affiliation = participants.calculation_agent_affiliation(
        values.get("calculation_agent"), issuer_group, participant_rows or [])

    extras = rr.extras
    findings = checks.run_self_checks(
        values,
        stated_initial_valuation_date=(extras["initial_valuation_date"].value
                                       if "initial_valuation_date" in extras else None),
        hypothetical_rows=(extras["hypothetical_rows"].value if "hypothetical_rows" in extras else None),
        stated_totals=rules.find_stated_dollar_totals(doc.text))
    out.self_checks = [asdict(f) for f in findings]
    out.values = values
    return out


@dataclass
class PrefillPlan:
    notes: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    @property
    def est_input_tokens(self) -> int:
        return sum(n["est_input_tokens"] for n in self.notes)

    @property
    def est_cost_usd(self) -> float:
        return sum(n["est_cost_usd"] for n in self.notes)


async def plan(conn, filing_ids: list[str], specs: list[FieldSpec], cfg: readers.ReaderConfig, *,
               catalog: dict, downloader=None) -> PrefillPlan:
    """DRY RUN: per note, prompt size, estimated tokens and cost. No write, no call."""
    rspecs = reader_specs(specs)
    p = PrefillPlan()
    dep = catalog.get(cfg.deployment)
    for fid in filing_ids:
        try:
            doc = await documents.load_document(conn, fid, downloader=downloader)
        except Exception as exc:  # noqa: BLE001
            p.skipped.append({"reference_filing_id": fid, "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        tr, _rr, _et = await asyncio.to_thread(run_static_sources, doc, specs)
        messages, _ = readers.build_messages(rspecs, tr.text, filer=doc.filer_name, form_type=doc.form_type)
        chars = sum(len(m["content"]) for m in messages)
        p.notes.append({"reference_filing_id": fid, "filer": doc.filer_name, "prompt_chars": chars,
                        "est_input_tokens": estimate_tokens_chars(chars), "est_output_tokens": cfg.max_tokens,
                        "est_cost_usd": estimate_call_cost(dep, chars, cfg.max_tokens)})
    return p


async def batch_filing_ids(conn, batch: str) -> list[str]:
    from services.database import platform_scope

    async with platform_scope(conn):
        rows = await conn.fetch(
            """SELECT reference_filing_id FROM portfolio.note_gold_candidates
                WHERE sample_batch = $1 AND status <> 'skipped'
                ORDER BY issuer_group, filing_year, reference_filing_id""", batch)
    return [str(r["reference_filing_id"]) for r in rows]


async def run_prefill(conn, *, batch: str, filing_ids: list[str], specs: list[FieldSpec], model: str,
                      max_tokens: int, spend_cap_usd: float, catalog: dict, created_by=None,
                      downloader=None, extra_config: dict | None = None, progress=None,
                      participant_rows: list[dict] | None = None,
                      issuer_groups: dict[str, str] | None = None) -> dict:
    """The real run. ``catalog`` must already be the guarded one (check_model)."""
    cfg = readers.ReaderConfig(slot=SLOT, deployment=model, max_tokens=max_tokens)
    config = {"batch": batch, "model": model, "max_tokens": max_tokens, "slot": SLOT,
              "anchoring_note": anchoring_note(model), "schema_version": "notefields.v3",
              **(extra_config or {})}
    spend = SpendTracker(cap_usd=spend_cap_usd)
    run_id = await store.create_run(conn, run_kind=RUN_KIND, spend_cap_usd=spend_cap_usd, config=config,
                                    created_by=created_by, notes_planned=len(filing_ids))
    status, stop_reason, done = "running", None, 0
    per_note: dict[str, dict] = {}
    try:
        for fid in filing_ids:
            try:
                doc = await documents.load_document(conn, fid, downloader=downloader)
            except Exception as exc:  # noqa: BLE001 — one unreadable filing never stops the run
                per_note[fid] = {"status": "load_failed", "error": f"{type(exc).__name__}: {exc}"[:300]}
                if progress:
                    progress(fid, per_note[fid]["status"])
                continue
            try:
                note = await prefill_note(doc, specs, cfg, catalog=catalog, spend=spend, run_id=run_id,
                                          participant_rows=participant_rows, issuer_groups=issuer_groups)
            except SpendCapReached as exc:
                status, stop_reason = "stopped_spend_cap", str(exc)
                break
            await store.insert_readings(conn, note.readings)
            done += 1
            per_note[fid] = {"status": note.status, "error": note.error, "cost_usd": note.cost_usd,
                             "fields_with_value": len(note.values), "self_checks": note.self_checks,
                             "field_issues": note.field_issues, "input_disagreements": note.input_disagreements,
                             "calculation_agent_affiliation": note.calculation_agent_affiliation}
            if progress:
                progress(fid, f"{note.status} ${note.cost_usd:.5f} (run ${spend.spent_usd:.4f})")
        else:
            status = "completed"
    except Exception as exc:  # noqa: BLE001
        status, stop_reason = "failed", f"{type(exc).__name__}: {exc}"
        raise
    finally:
        await store.finish_run(conn, run_id, status=status if status != "running" else "failed",
                               notes_done=done, stop_reason=stop_reason,
                               report={"gold_prefill": {"batch": batch, "notes": per_note},
                                       "spent_usd": spend.spent_usd})
    return {"run_id": run_id, "status": status, "notes_done": done, "stop_reason": stop_reason,
            "spent_usd": spend.spent_usd, "notes": per_note}
