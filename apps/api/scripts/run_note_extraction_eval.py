"""noteextractb1 EVALUATION HARNESS — every candidate model on the GOLD SET.

For each candidate (a proxy deployment, optionally at a reasoning effort — by
default gpt-oss-120b is run at BOTH low and medium) it reads every gold note
once and reports, per field and overall: accuracy, null rate, invented-value
rate (quote not found in the filing), cost per note and latency. It also
reports: how much of the needed text trimming keeps (does the trimmed text
contain each gold quote), EdgarTools' and the rules' coverage and accuracy per
field and per issuer, how often skip-second-reader would have been safe (for
the --pair), Jev's accuracy on that pair's disagreements, the repeated-prompt
discount actually observed, accuracy on the distribution and fee fields, a
TALLY of distribution participants by issuer and year, and identical terms
appearing under different CUSIPs.

IT REPORTS; IT DOES NOT CHOOSE MODELS. Joe picks the ensemble in the picker.

    python3 apps/api/scripts/run_note_extraction_eval.py --dry-run --spend-cap 10
    python3 apps/api/scripts/run_note_extraction_eval.py --spend-cap 10 \
        [--candidates gpt-oss-120b@low,gpt-oss-120b@medium,gemini-2.5-flash-lite,...] \
        [--pair gpt-oss-120b@low,gemini-2.5-flash-lite] [--no-jev]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict

import _note_extraction_common as common

DEFAULT_CANDIDATES = (
    "gpt-oss-120b@low", "gpt-oss-120b@medium", "gemini-2.5-flash-lite", "mistral-small-24b",
    "gpt-5-nano", "gpt-5-mini", "deepseek-v3.2", "qwen2.5-7b",
)
DISTRIBUTION_FEE_FIELDS = ("distribution", "agent_commission_pct", "total_commissions_fees_pct",
                           "price_to_public_pct", "fee_based_account_price_pct", "estimated_value_pct")


def parse_candidate(token: str) -> tuple[str, str | None]:
    dep, _, effort = token.partition("@")
    return dep.strip(), (effort.strip() or None)


async def load_gold(conn, specs_by_key) -> dict:
    rows = await conn.fetch(
        """SELECT g.reference_filing_id, g.field_key, g.value, g.source_quote, rf.filer_name, rf.filing_date
             FROM portfolio.note_gold_values g
             JOIN portfolio.reference_filings rf ON rf.id = g.reference_filing_id
            WHERE g.valid_to IS NULL""")
    from services.note_extraction.schema import normalize
    gold, quotes, raw, issuer_of, year_of = {}, defaultdict(list), {}, {}, {}
    for r in rows:
        spec = specs_by_key.get(r["field_key"])
        if spec is None:
            continue
        note = str(r["reference_filing_id"])
        value = json.loads(r["value"]) if r["value"] is not None else None
        gold[(note, r["field_key"])] = normalize(spec, value)
        raw[(note, r["field_key"])] = value
        if r["source_quote"]:
            quotes[note].append(r["source_quote"])
        issuer_of[note] = r["filer_name"]
        year_of[note] = r["filing_date"].year if r["filing_date"] else None
    return {"gold": gold, "quotes": quotes, "raw": raw, "issuer_of": issuer_of, "year_of": year_of}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spend-cap", type=float, required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--candidates", default=",".join(DEFAULT_CANDIDATES))
    ap.add_argument("--pair", default=None, help="two candidates for skip-second-reader and Jev measurement")
    ap.add_argument("--no-jev", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=4000)
    args = ap.parse_args()

    from services.note_extraction import (
        cascade, compare as cmp, documents, jev, metrics, participants, proxy, readers, store,
    )
    from services.note_extraction.schema import CRITICAL_FIELDS
    from services.note_extraction.spend import (
        Plan, PlannedCall, SpendCapReached, SpendTracker, estimate_call_cost, jev_cost_estimate,
    )
    from services.note_extraction.trim import estimate_tokens_chars, recall

    conn = await common.connect()
    try:
        specs, catalog, parts = await common.load_context(conn)
        by_key = {s.key: s for s in specs}
        G = await load_gold(conn, by_key)
        notes = sorted({n for n, _ in G["gold"]})
        print(f"gold set: {len(notes)} notes, {len(G['gold'])} hand-checked field values")
        if not notes:
            print("BLOCKED: the gold set is empty — review notes on the gold screen first.")
            return 2

        cands, blocked = [], []
        for tok in [t for t in args.candidates.split(",") if t.strip()]:
            dep, effort = parse_candidate(tok)
            d = catalog.get(dep)
            if d is None or d.duplicate:
                blocked.append({"candidate": tok, "reason": "not registered on the proxy" if d is None
                                else "load-balanced over two deployments"})
                continue
            cands.append((tok, readers.ReaderConfig("model_1", dep, effort, args.max_tokens)))
        for b in blocked:
            print(f"[BLOCKED] {b['candidate']}: {b['reason']}")
        pair = [t.strip() for t in args.pair.split(",")] if args.pair else []
        jev_route = None
        if not args.no_jev:
            jev_route = await conn.fetchval(
                "SELECT model_route FROM ai_system_one_models WHERE is_default AND availability = 'available'")

        docs, statics = {}, {}
        for n in notes:
            try:
                docs[n] = await documents.load_document(conn, n)
                statics[n] = await asyncio.to_thread(cascade.run_static_sources, docs[n], specs)
            except Exception as exc:  # noqa: BLE001
                print(f"  note {n}: not loadable ({exc})")

        if args.dry_run:
            before = dict(proxy.CALLS)
            plan = Plan(notes=len(docs))
            for tok, cfg in cands:
                for n, doc in docs.items():
                    msgs, _ = readers.build_messages(specs, statics[n][0].text, filer=doc.filer_name,
                                                     form_type=doc.form_type)
                    chars = sum(len(m["content"]) for m in msgs)
                    plan.calls.append(PlannedCall(n, tok, cfg.deployment, chars, estimate_tokens_chars(chars),
                                                  cfg.max_tokens,
                                                  estimate_call_cost(catalog.get(cfg.deployment), chars, cfg.max_tokens)))
            if len(pair) == 2 and jev_route:
                plan.jev_calls_assumed = len(docs)
                for n in docs:
                    plan.calls.append(PlannedCall(n, "jev", jev_route, 0, 0, 0, jev_cost_estimate()))
            common.print_plan(plan, args.spend_cap)
            assert proxy.CALLS == before, "dry run made a provider call"
            return 0

        spend = SpendTracker(cap_usd=args.spend_cap)
        run_id = await store.create_run(conn, run_kind="evaluation", spend_cap_usd=args.spend_cap,
                                        config={"candidates": [t for t, _ in cands], "blocked": blocked,
                                                "pair": pair, "jev_route": jev_route},
                                        notes_planned=len(docs))
        per_cand: dict[str, dict] = {tok: {"readings": {}, "calls": [], "evidence": {}} for tok, _ in cands}
        status, stop_reason = "completed", None
        try:
            for tok, cfg in cands:
                for n, doc in docs.items():
                    tr = statics[n][0]
                    call = await readers.read(cfg, specs, tr.text, filer=doc.filer_name, form_type=doc.form_type,
                                              catalog=catalog, spend=spend,
                                              tags=[f"run:{run_id}", f"candidate:{tok}"])
                    rows = [cascade._call_row(doc, call, run_id=run_id, origin="evaluation",
                                              ensemble_config_id=None, extra={"evaluation_candidate": tok})]
                    per_cand[tok]["calls"].append({"cost_usd": call.cost_usd, "latency_ms": call.latency_ms,
                                                   "input_tokens": call.input_tokens,
                                                   "cached_tokens": call.cached_tokens})
                    for k, spec in by_key.items():
                        if not call.usable:
                            continue
                        f = call.fields.get(k) or {}
                        e = cmp.evidence(doc, spec, "model_1", f.get("value"), f.get("quote"))
                        per_cand[tok]["evidence"][(n, k)] = e
                        per_cand[tok]["readings"][(n, k)] = {"normalized": e.normalized,
                                                             "quote_verified": e.quote_verified}
                        rows.append(cascade._reading_from_evidence(
                            doc, e, k, run_id=run_id, origin="evaluation", call=call,
                            prompt_version=readers.PROMPT_VERSION, extra={"evaluation_candidate": tok}))
                    await store.insert_readings(conn, rows)
                    print(f"  {tok:28} {n}  {call.status}  ${call.cost_usd or 0:.5f}  (run ${spend.spent_usd:.4f})")
        except SpendCapReached as exc:
            status, stop_reason = "stopped_spend_cap", str(exc)
            print(f"STOPPED: {exc}")

        # ── independent sources (free) ──
        rules_r, et_r, recalls = {}, {}, []
        for n, doc in docs.items():
            tr, rr, et = statics[n]
            recalls.append(recall(tr.text, G["quotes"].get(n, [])))
            for k, h in rr.hits.items():
                if k in by_key:
                    e = cmp.evidence(doc, by_key[k], "rules", h.value, h.quote)
                    rules_r[(n, k)] = {"normalized": e.normalized, "evidence": e}
            for k, v in et.fields.items():
                if k in by_key:
                    e = cmp.evidence(doc, by_key[k], "edgartools", v["value"], v["quote"])
                    et_r[(n, k)] = {"normalized": e.normalized, "evidence": e}

        # ── pair: skip-second-reader + Jev on disagreements ──
        pair_report = None
        if len(pair) == 2 and all(p in per_cand for p in pair) and status == "completed":
            a, b = per_cand[pair[0]]["evidence"], per_cand[pair[1]]["evidence"]
            flags, jev_rows = [], {}
            for n, doc in docs.items():
                comps = {k: cmp.compare_field(by_key[k], a.get((n, k)), b.get((n, k)),
                                              [x["evidence"] for x in (rules_r.get((n, k)), et_r.get((n, k))) if x])
                         for k in by_key}
                independent = defaultdict(list)
                for src in (rules_r, et_r):
                    for (nn, k), x in src.items():
                        if nn == n:
                            independent[k].append(x["evidence"])
                m1 = {k: a.get((n, k)) for k in by_key}
                flags.append(cmp.skip_second_reader_would_be_safe(sorted(CRITICAL_FIELDS & set(by_key)),
                                                                  m1, independent, comps))
                disputed = [c for c in comps.values() if c.outcome == "disputed"]
                if disputed and jev_route:
                    try:
                        jc = await jev.ask(jev_route, doc, disputed, fallback_text=statics[n][0].text[:6000],
                                           spend=spend)
                    except SpendCapReached as exc:
                        status, stop_reason = "stopped_spend_cap", str(exc)
                        break
                    for k, ans in jc.answers.items():
                        val = ans.candidate.normalized if ans.candidate else None
                        jev_rows[(n, k)] = {"normalized": val, "accepted": ans.accepted}
            pair_report = {"pair": pair, "skip_second_reader": metrics.skip_second_reader(flags),
                           "jev": metrics.jev_accuracy(jev_rows, G["gold"]) if jev_route else None}

        # ── report ──
        report = {"gold_notes": len(docs), "gold_values": len(G["gold"]), "blocked_candidates": blocked,
                  "candidates": {}, "trim_recall": metrics.trim_recall(recalls),
                  "edgartools": metrics.source_coverage(G["gold"], et_r, G["issuer_of"]),
                  "rules": metrics.source_coverage(G["gold"], rules_r, G["issuer_of"]),
                  "pair": pair_report, "spent_usd": spend.spent_usd}
        for tok, d in per_cand.items():
            fm = metrics.field_metrics(G["gold"], d["readings"])
            dist_gold = {k: v for k, v in G["gold"].items() if k[1] in DISTRIBUTION_FEE_FIELDS}
            report["candidates"][tok] = {
                "fields": fm, "calls": metrics.call_metrics(d["calls"], len(docs)),
                "distribution_and_fees": metrics.field_metrics(dist_gold, d["readings"])["overall"],
            }
        staged = [{"issuer": G["issuer_of"].get(n), "year": G["year_of"].get(n),
                   "participants": G["raw"].get((n, "distribution")) or []} for n in docs]
        report["distribution_tally"] = participants.channel_tally(staged)
        rule_names = [{"name": p["name"], "issuer_name": docs[n].filer_name,
                       "filing_year": G["year_of"].get(n)} for n in docs for p in statics[n][1].participant_names]
        report["rules_participant_tally"] = participants.tally(rule_names)
        report["participants_unmatched"] = participants.match_names(rule_names, parts).unmatched
        sig_notes = []
        for n in docs:
            sig_notes.append({"note": n, "issuer": G["issuer_of"].get(n), "cusip": G["raw"].get((n, "cusip")),
                              "fields": {k: G["gold"].get((n, k)) for k in metrics.SIGNATURE_FIELDS},
                              "total_commissions_fees_pct": G["raw"].get((n, "total_commissions_fees_pct")),
                              "estimated_value_pct": G["raw"].get((n, "estimated_value_pct")),
                              "fee_based_account_price_pct": G["raw"].get((n, "fee_based_account_price_pct")),
                              "participants": G["raw"].get((n, "distribution"))})
        report["identical_terms_across_cusips"] = metrics.identical_terms_across_cusips(sig_notes)
        await store.finish_run(conn, run_id, status=status, notes_done=len(docs), stop_reason=stop_reason,
                               report=report)
        print(json.dumps({k: report[k] for k in ("gold_notes", "trim_recall", "pair", "spent_usd")},
                         indent=2, default=str))
        for tok, c in report["candidates"].items():
            o = c["fields"]["overall"]
            print(f"  {tok:28} acc={o['accuracy']} null={o['null_rate']} invented={o['invented_rate']} "
                  f"$/note={c['calls']['cost_per_note']} cache={c['calls']['cache_share']}")
        print(f"run {run_id} — full report on the results grid (/admin/note-extraction/results)")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
