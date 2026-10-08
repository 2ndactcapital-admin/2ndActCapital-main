"""SELF-CHECKS — free accuracy checks over a note's resolved values.

Each check either stays quiet (consistent, or not applicable because an input
is missing) or returns a ``Finding`` naming the check, the fields involved and
a plain reason. A finding sends the note to needs_review. NOTHING HERE FIXES A
VALUE: filings contain errors too, so a disagreement is for a person to read.

    fees_reconcile          price to public - total fees ~ proceeds to issuer
    estimated_value_below_price   estimated value < price to public
    observation_dates_in_term     every observation date in [pricing, maturity]
    initial_valuation_date        decision A: a stated initial valuation date
                                  that differs from the pricing date
    payoff_matches_examples       the payoff recomputed from the extracted terms
                                  agrees with the filing's hypothetical table
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from services.note_extraction.schema import as_range

FEES_TOLERANCE_PCT = Decimal("0.05")
PAYOFF_TOLERANCE_PER_1000 = Decimal("1.00")

CHECKS = ("fees_reconcile", "estimated_value_below_price", "observation_dates_in_term",
          "initial_valuation_date", "payoff_matches_examples")


@dataclass
class Finding:
    check: str
    fields: list[str]
    reason: str
    detail: dict | None = None
    needs_review: bool = True


def _d(v) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _date(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v is not None else None
    except ValueError:
        return None


def fees_reconcile(values: dict) -> Finding | None:
    price, fees, proceeds = (_d(values.get(k)) for k in ("price_to_public_pct", "total_fees_pct",
                                                           "proceeds_to_issuer_pct"))
    if price is None or fees is None or proceeds is None:
        return None
    gap = price - fees - proceeds
    if abs(gap) <= FEES_TOLERANCE_PCT:
        return None
    return Finding("fees_reconcile", ["price_to_public_pct", "total_fees_pct", "proceeds_to_issuer_pct"],
                   f"price to public {price} - total fees {fees} = {price - fees}, but proceeds to issuer "
                   f"is {proceeds} (off by {gap})", {"gap_pct": str(gap)})


def estimated_value_below_price(values: dict) -> Finding | None:
    price = _d(values.get("price_to_public_pct"))
    ev = as_range(values.get("estimated_value_pct"))
    if ev is None:
        r = as_range(values.get("estimated_value_per_1000"))
        ev = None if r is None else {k: (v / 10 if isinstance(v, Decimal) else v) for k, v in r.items()}
    if price is None or ev is None:
        return None
    high = ev["max"] if ev["max"] is not None else ev["min"]
    if high is None or high < price:
        return None
    return Finding("estimated_value_below_price", ["estimated_value_pct", "estimated_value_per_1000",
                                                   "price_to_public_pct"],
                   f"estimated value {high}% is not below the price to public {price}%")


def observation_dates_in_term(values: dict) -> Finding | None:
    p, m = _date(values.get("pricing_date")), _date(values.get("maturity_date"))
    sched = values.get("observation_schedule")
    if p is None or m is None or not isinstance(sched, list) or not sched:
        return None
    bad = []
    for row in sched:
        for k in ("observation_date", "payment_date"):
            d = _date((row or {}).get(k)) if isinstance(row, dict) else None
            if d is not None and not (p <= d <= m):
                bad.append(f"{k} {d.isoformat()}")
    if not bad:
        return None
    return Finding("observation_dates_in_term", ["observation_schedule", "pricing_date", "maturity_date"],
                   f"{len(bad)} schedule date(s) fall outside {p.isoformat()}..{m.isoformat()}: "
                   + ", ".join(bad[:5]))


def initial_valuation_date(values: dict, stated_initial: str | None) -> Finding | None:
    """Decision A: the initial valuation date IS the pricing date; a filing
    that states a different one is flagged, never merged."""
    p, i = _date(values.get("pricing_date")), _date(stated_initial)
    if p is None or i is None or p == i:
        return None
    return Finding("initial_valuation_date", ["pricing_date"],
                   f"the filing states an initial valuation date {i.isoformat()} that differs from the "
                   f"pricing date {p.isoformat()} (decision A: review, never merge)")


def payoff_per_1000(values: dict, underlying_return_pct) -> Decimal | None:
    """Payment at maturity per $1,000 for one hypothetical underlying return,
    from the extracted terms only. None when the terms do not determine it
    (coupons, calls, a floor without a level, or a missing level)."""
    r = _d(underlying_return_pct)
    if r is None:
        return None
    if values.get("coupon_type") in ("fixed", "contingent") or values.get("call_type") in ("automatic", "issuer"):
        return None
    ptype = values.get("protection_type")
    lev = _d(values.get("downside_leverage")) or Decimal(1)
    fixed_amt, fixed_thr = _d(values.get("fixed_payout_amount")), _d(values.get("fixed_payout_threshold_pct"))
    one = Decimal(1000)
    if fixed_amt is not None and fixed_thr is not None and Decimal(100) + r >= fixed_thr:
        return fixed_amt
    if r > 0:
        part = _d(values.get("participation_rate")) or Decimal(100)
        gain = r * part / 100
        cap = _d(values.get("cap_pct"))
        if cap is not None:
            gain = min(gain, cap)
        return one * (1 + gain / 100)
    if ptype == "full":
        return one
    if ptype == "none":
        return one * (1 + r * lev / 100)
    if ptype == "buffer":
        b = _d(values.get("buffer_pct"))
        if b is None:
            return None
        return one if -r <= b else one * (1 + (r + b) * lev / 100)
    if ptype == "barrier":
        bar = _d(values.get("barrier_pct"))
        if bar is None:
            return None
        return one if Decimal(100) + r >= bar else one * (1 + r * lev / 100)
    return None


def payoff_matches_examples(values: dict, rows: list[dict] | None) -> Finding | None:
    if not rows:
        return None
    compared, mismatches = 0, []
    for row in rows:
        want = _d(row.get("payment_per_1000"))
        got = payoff_per_1000(values, row.get("underlying_return_pct"))
        if want is None or got is None:
            continue
        compared += 1
        if abs(got - want) > PAYOFF_TOLERANCE_PER_1000:
            mismatches.append({"underlying_return_pct": row.get("underlying_return_pct"),
                               "filing": str(want), "recomputed": str(got.quantize(Decimal("0.01")))})
    if not compared or not mismatches:
        return None
    return Finding("payoff_matches_examples",
                   ["protection_type", "buffer_pct", "barrier_pct", "participation_rate", "cap_pct"],
                   f"the recomputed payoff disagrees with the filing's hypothetical table on "
                   f"{len(mismatches)} of {compared} row(s)", {"mismatches": mismatches[:10]})


def run_self_checks(values: dict, *, stated_initial_valuation_date: str | None = None,
                    hypothetical_rows: list[dict] | None = None) -> list[Finding]:
    found = [
        fees_reconcile(values),
        estimated_value_below_price(values),
        observation_dates_in_term(values),
        initial_valuation_date(values, stated_initial_valuation_date),
        payoff_matches_examples(values, hypothetical_rows),
    ]
    return [f for f in found if f is not None]
