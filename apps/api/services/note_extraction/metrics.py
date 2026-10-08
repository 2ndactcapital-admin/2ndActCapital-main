"""The evaluation harness's metrics — PURE functions, so a fixture can be checked
against a hand calculation. The harness REPORTS; it never chooses models.

Definitions (per candidate model, over the gold set):

  accuracy        correct / gold pairs. A pair is (note, field) with a gold
                  value; "correct" = the reading's normalised value equals the
                  gold's (both absent counts as correct). A failed call counts
                  as an absent reading.
  null_rate       of the gold pairs whose gold value is NOT absent, the share
                  the reader left null (missed values).
  invented_rate   of the reader's NON-null answers, the share whose quote is
                  not found verbatim in the filing (fabricated or missing quote).
  cost_per_note   sum of the candidate's call costs / notes read.
  latency         mean and median call latency (ms).
  cache_share     cached prompt tokens / prompt tokens — the repeated-prompt
                  discount actually observed.
"""
from __future__ import annotations

import statistics
from collections import defaultdict


def field_metrics(gold: dict[tuple, str | None], readings: dict[tuple, dict]) -> dict:
    """gold: {(note, field): normalised gold value or None}.
    readings: {(note, field): {"normalized": str|None, "quote_verified": bool|None}}.
    Returns {"overall": {...}, "by_field": {field: {...}}}."""
    acc: dict[str, dict] = defaultdict(lambda: {"n": 0, "correct": 0, "gold_present": 0, "missed": 0,
                                                "answered": 0, "invented": 0})
    for (note, fk), g in gold.items():
        r = readings.get((note, fk)) or {"normalized": None, "quote_verified": None}
        a = acc[fk]
        a["n"] += 1
        a["correct"] += int(r["normalized"] == g)
        if g is not None:
            a["gold_present"] += 1
            a["missed"] += int(r["normalized"] is None)
        if r["normalized"] is not None:
            a["answered"] += 1
            a["invented"] += int(r.get("quote_verified") is not True)

    def finish(a):
        return {
            **a,
            "accuracy": a["correct"] / a["n"] if a["n"] else None,
            "null_rate": a["missed"] / a["gold_present"] if a["gold_present"] else None,
            "invented_rate": a["invented"] / a["answered"] if a["answered"] else None,
        }

    total = {"n": 0, "correct": 0, "gold_present": 0, "missed": 0, "answered": 0, "invented": 0}
    for a in acc.values():
        for k in total:
            total[k] += a[k]
    return {"overall": finish(total), "by_field": {k: finish(v) for k, v in sorted(acc.items())}}


def call_metrics(calls: list[dict], notes: int) -> dict:
    """calls: [{"cost_usd", "latency_ms", "input_tokens", "cached_tokens"}]."""
    cost = sum(float(c.get("cost_usd") or 0) for c in calls)
    lat = [c["latency_ms"] for c in calls if c.get("latency_ms") is not None]
    tin = sum(int(c.get("input_tokens") or 0) for c in calls)
    cached = sum(int(c.get("cached_tokens") or 0) for c in calls)
    return {
        "calls": len(calls), "notes": notes, "cost_usd": cost,
        "cost_per_note": cost / notes if notes else None,
        "latency_ms_mean": statistics.mean(lat) if lat else None,
        "latency_ms_median": statistics.median(lat) if lat else None,
        "input_tokens": tin, "cached_tokens": cached,
        "cache_share": cached / tin if tin else None,
    }


def source_coverage(gold: dict[tuple, str | None], readings: dict[tuple, dict],
                    issuer_of: dict[str, str]) -> dict:
    """EdgarTools (or rules) coverage and accuracy, per field and per issuer.
    coverage = gold-present pairs the source answered / gold-present pairs;
    accuracy = correct / answered."""
    by_field: dict[str, dict] = defaultdict(lambda: {"gold_present": 0, "answered": 0, "correct": 0})
    by_issuer: dict[str, dict] = defaultdict(lambda: {"gold_present": 0, "answered": 0, "correct": 0})
    for (note, fk), g in gold.items():
        if g is None:
            continue
        r = readings.get((note, fk))
        for bucket in (by_field[fk], by_issuer[issuer_of.get(note, "unknown")]):
            bucket["gold_present"] += 1
            if r is not None and r.get("normalized") is not None:
                bucket["answered"] += 1
                bucket["correct"] += int(r["normalized"] == g)

    def fin(d):
        return {k: {**v, "coverage": v["answered"] / v["gold_present"] if v["gold_present"] else None,
                    "accuracy": v["correct"] / v["answered"] if v["answered"] else None}
                for k, v in sorted(d.items())}

    return {"by_field": fin(by_field), "by_issuer": fin(by_issuer)}


def trim_recall(per_note: list[dict]) -> dict:
    """per_note: trim.recall() outputs. Pooled over all gold quotes."""
    total = sum(r["total"] for r in per_note)
    found = sum(r["found"] for r in per_note)
    return {"quotes": total, "found": found, "recall": found / total if total else None}


def skip_second_reader(flags: list[bool | None]) -> dict:
    applicable = [f for f in flags if f is not None]
    safe = sum(1 for f in applicable if f)
    return {"notes": len(flags), "skip_would_apply": len(applicable), "safe": safe,
            "unsafe": len(applicable) - safe,
            "safe_rate": safe / len(applicable) if applicable else None}


def jev_accuracy(jev: dict[tuple, dict], gold: dict[tuple, str | None]) -> dict:
    """jev: {(note, field): {"normalized", "accepted"}} on disagreements."""
    n = correct = accepted = accepted_correct = 0
    for key, r in jev.items():
        if key not in gold:
            continue
        n += 1
        ok = r["normalized"] == gold[key]
        correct += int(ok)
        if r.get("accepted"):
            accepted += 1
            accepted_correct += int(ok)
    return {"questions_with_gold": n, "correct": correct, "accuracy": correct / n if n else None,
            "accepted": accepted,
            "accepted_accuracy": accepted_correct / accepted if accepted else None}


SIGNATURE_FIELDS = ("underlyings", "maturity_date", "protection_type", "buffer_pct",
                    "barrier_pct", "coupon_rate_pa", "coupon_barrier_pct", "autocall_level_pct",
                    "autocall_frequency", "basket_type", "pricing_date")


def identical_terms_across_cusips(notes: list[dict]) -> list[dict]:
    """notes: [{"note", "cusip", "fields": {key: normalised}, "issuer",
    "total_fees_pct", "estimated_value_pct", "fee_based_account_price",
    "participants"}]. Groups notes whose payoff signature is identical but whose
    CUSIPs differ — the same terms sold through different channels."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for n in notes:
        sig = tuple((k, n["fields"].get(k)) for k in SIGNATURE_FIELDS)
        if sum(1 for _, v in sig if v is not None) < 4:
            continue  # too sparse to call two notes identical
        groups[(n.get("issuer"), sig)].append(n)
    out = []
    for (issuer, sig), members in groups.items():
        cusips = {m.get("cusip") for m in members if m.get("cusip")}
        if len(cusips) < 2:
            continue
        out.append({"issuer": issuer, "signature": dict(sig), "cusips": sorted(cusips),
                    "notes": [{"note": m["note"], "cusip": m.get("cusip"),
                               "total_fees_pct": m.get("total_fees_pct"),
                               "estimated_value_pct": m.get("estimated_value_pct"),
                               "fee_based_account_price": m.get("fee_based_account_price"),
                               "participants": m.get("participants")} for m in members]})
    return out
