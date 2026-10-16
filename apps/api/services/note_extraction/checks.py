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
    dollar_totals_match           bulkfetch: the CALCULATED total_fees_usd /
                                  proceeds_to_issuer_usd agree with the dollar
                                  totals the cover table states (+-$1 or
                                  +-0.01%), naming both numbers when not

NOTEFIX1: total_fees_pct and proceeds_to_issuer_pct are RANGES now (price_to_
public_pct stays scalar). fees_reconcile compares intervals, not numbers: it
computes the IMPLIED proceeds interval price - [fees.min, fees.max] and fires
only when that interval and the stated proceeds range do NOT overlap (within
tolerance) — i.e. only when no feasible combination of a fee value and a
proceeds value is consistent with price. A single stated amount is a range
with min = max, so a wholly scalar note reduces to the old equality check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from services.note_extraction.schema import as_range

FEES_TOLERANCE_PCT = Decimal("0.05")
PAYOFF_TOLERANCE_PER_1000 = Decimal("1.00")
COUPON_TOLERANCE_PCT = Decimal("0.01")
PROTECTION_LEVEL_EPSILON_PCT = Decimal("0.01")

CHECKS = ("fees_reconcile", "estimated_value_below_price", "observation_dates_in_term",
          "initial_valuation_date", "payoff_matches_examples", "coupon_cross_check",
          "maturity_after_pricing", "payoff_matches_at_protection_levels", "dollar_totals_match")
DOLLAR_TOLERANCE_USD = Decimal("1")
DOLLAR_TOLERANCE_REL = Decimal("0.0001")      # 0.01%


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
    """price - fees ~= proceeds, where fees and proceeds are both ranges
    (notefix1). A single stated amount is a range with min = max, so this
    reduces to the old equality check when every field is scalar-as-range.

    Not an equality test on numbers: an INTERVAL overlap test. The implied
    proceeds interval is price minus the fee range (varying the fee across
    its own min/max); the finding fires only when that interval and the
    stated proceeds range share no feasible point, even after widening both
    sides by the tolerance. A wide-but-consistent pair of ranges never fires,
    because some fee/proceeds combination inside both ranges satisfies price
    - fee = proceeds."""
    price = _d(values.get("price_to_public_pct"))
    fees, proceeds = as_range(values.get("total_fees_pct")), as_range(values.get("proceeds_to_issuer_pct"))
    if price is None or fees is None or proceeds is None:
        return None
    # An open-ended bound ("up to X" / "at least X") is unconstrained on its
    # open side, not collapsed to the stated side: fees range [0, price],
    # proceeds range [0, price] of principal.
    fee_lo = fees["min"] if fees["min"] is not None else Decimal(0)
    fee_hi = fees["max"] if fees["max"] is not None else price
    proceeds_lo = proceeds["min"] if proceeds["min"] is not None else Decimal(0)
    proceeds_hi = proceeds["max"] if proceeds["max"] is not None else price
    implied_lo, implied_hi = price - fee_hi, price - fee_lo
    if implied_hi < proceeds_lo - FEES_TOLERANCE_PCT or proceeds_hi < implied_lo - FEES_TOLERANCE_PCT:
        # notefix2 F6: propose the total fees a feasible proceeds value would
        # actually imply (price - proceeds), so the finding is something a
        # reviewer can act on, not just a disagreement.
        proposed_lo, proposed_hi = price - proceeds_hi, price - proceeds_lo
        if proposed_lo > proposed_hi:
            proposed_lo, proposed_hi = proposed_hi, proposed_lo
        return Finding("fees_reconcile", ["price_to_public_pct", "total_fees_pct", "proceeds_to_issuer_pct"],
                       f"price to public {price} minus total fees {fees['min']}..{fees['max']} implies "
                       f"proceeds {implied_lo}..{implied_hi}, but proceeds to issuer is stated as "
                       f"{proceeds['min']}..{proceeds['max']} — no feasible combination reconciles",
                       {"implied_proceeds": [str(implied_lo), str(implied_hi)],
                        "stated_proceeds": [str(proceeds["min"]), str(proceeds["max"])],
                        "proposed_total_fees_pct": {"min": str(proposed_lo), "max": str(proposed_hi)}})
    return None


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


_FREQ_PER_YEAR = {
    "monthly": 12, "quarterly": 4, "semi_annual": 2, "semiannual": 2, "annual": 1,
}


def _periods_per_year(freq) -> int | None:
    if not freq:
        return None
    key = re.sub(r"[\s-]+", "_", str(freq).strip().lower())
    return _FREQ_PER_YEAR.get(key)


def coupon_cross_check(values: dict) -> Finding | None:
    """notefix2 Task 5: when the observation schedule states a FIXED per-
    period coupon amount (per $1,000) and ``coupon_frequency`` is one of the
    known frequencies, ``coupon_rate_pa`` must equal amount x periods/yr /
    denomination x 100, within ``COUPON_TOLERANCE_PCT``. Catches the filing
    quoting "$18.75 quarterly on $1,000" (7.50% per annum) while the model
    reads a different headline rate (6%, #46)."""
    rate = _d(values.get("coupon_rate_pa"))
    periods = _periods_per_year(values.get("coupon_frequency"))
    sched = values.get("observation_schedule")
    if rate is None or periods is None or not isinstance(sched, list) or not sched:
        return None
    amounts = [_d(row.get("coupon_amount")) for row in sched if isinstance(row, dict)]
    amounts = [a for a in amounts if a is not None and a > 0]
    if not amounts or any(a != amounts[0] for a in amounts):
        return None  # no fixed per-period amount to cross-check against
    denom = _d(values.get("denomination")) or Decimal(1000)
    implied = amounts[0] * periods / denom * 100
    if abs(implied - rate) <= COUPON_TOLERANCE_PCT:
        return None
    return Finding("coupon_cross_check", ["coupon_rate_pa", "observation_schedule", "coupon_frequency"],
                   f"coupon_rate_pa is stated as {rate}% but the schedule's fixed per-period amount "
                   f"({amounts[0]} x {periods}/yr / {denom}) implies {implied}%",
                   {"stated_coupon_rate_pa": str(rate), "implied_from_schedule": str(implied)})


def maturity_after_pricing(values: dict) -> Finding | None:
    """notefix2 Task 5: maturity_date (and final_valuation_date, when stated)
    must be strictly after pricing_date. A date equal to pricing_date is
    already rejected earlier, at the evidence level
    (compare.evidence's ``reject_if_equals``); this check catches the
    remaining case — a resolved maturity BEFORE pricing, or an unresolved
    final_valuation_date <= pricing_date that still made it through
    agreement (e.g. a rule or a single unopposed reader)."""
    p = _date(values.get("pricing_date"))
    if p is None:
        return None
    bad = []
    m = _date(values.get("maturity_date"))
    if m is not None and m <= p:
        bad.append(f"maturity_date {m.isoformat()}")
    fv = _date(values.get("final_valuation_date"))
    if fv is not None and fv <= p:
        bad.append(f"final_valuation_date {fv.isoformat()}")
    if not bad:
        return None
    return Finding("maturity_after_pricing", ["pricing_date", "maturity_date", "final_valuation_date"],
                   f"pricing_date is {p.isoformat()}, but " + " and ".join(bad) + " is not strictly after it")


def payoff_matches_at_protection_levels(values: dict, rows: list[dict] | None) -> Finding | None:
    """notefix2 Task 7: extends ``payoff_matches_examples`` with SYNTHETIC
    rows at each stated protection level ± ``PROTECTION_LEVEL_EPSILON_PCT``
    (a final level just below and just above the barrier/buffer) — catching a
    barrier read as a buffer (or vice versa) even on a filing whose own
    hypothetical table happens not to tabulate a row exactly at that level.
    Compared against the filing's own table only where a row already sits
    within ``PROTECTION_LEVEL_EPSILON_PCT`` of the synthetic one — this never
    invents a filing value, it only widens WHICH of the filing's real rows
    gets used as ground truth for the comparison."""
    if not rows:
        return None
    level = _d(values.get("barrier_pct")) if values.get("protection_type") == "barrier" else (
        _d(values.get("buffer_pct")) if values.get("protection_type") == "buffer" else None)
    if level is None:
        return None
    mismatches = []
    for sign in (Decimal(-1), Decimal(1)):
        synth_return = -(Decimal(100) - level) + sign * PROTECTION_LEVEL_EPSILON_PCT
        nearby = [r for r in rows if _d(r.get("underlying_return_pct")) is not None
                 and abs(_d(r.get("underlying_return_pct")) - synth_return) <= PROTECTION_LEVEL_EPSILON_PCT]
        if not nearby:
            continue
        row = nearby[0]
        want = _d(row.get("payment_per_1000"))
        got = payoff_per_1000(values, row.get("underlying_return_pct"))
        if want is None or got is None:
            continue
        if abs(got - want) > PAYOFF_TOLERANCE_PER_1000:
            mismatches.append({"underlying_return_pct": row.get("underlying_return_pct"),
                               "filing": str(want), "recomputed": str(got.quantize(Decimal("0.01"))),
                               "level_checked": str(level)})
    if not mismatches:
        return None
    return Finding("payoff_matches_at_protection_levels",
                   ["protection_type", "buffer_pct", "barrier_pct"],
                   f"the recomputed payoff at the protection level ({level}%) disagrees with the filing's "
                   f"hypothetical table on {len(mismatches)} row(s) — check buffer vs barrier",
                   {"mismatches": mismatches})


def _usd(v) -> str:
    return f"${v:,.2f}".replace(".00", "")


def dollar_totals_match(values: dict, stated: dict | None) -> Finding | None:
    """Each calculated dollar total vs the filing's stated total. A calculated
    RANGE agrees when the stated number lies inside it (widened by the
    tolerance); a point agrees within +-$1 or +-0.01% of the stated number."""
    if not stated:
        return None
    bad = []
    for key, pct_key in (("total_fees_usd", "total_fees_pct"), ("proceeds_to_issuer_usd", "proceeds_to_issuer_pct")):
        want = _d(stated.get(key))
        calc = as_range(values.get(key))
        if want is None or calc is None:
            continue
        tol = max(DOLLAR_TOLERANCE_USD, abs(want) * DOLLAR_TOLERANCE_REL)
        lo = calc["min"] if calc["min"] is not None else calc["max"]
        hi = calc["max"] if calc["max"] is not None else calc["min"]
        if lo is None:
            continue
        if not (lo - tol <= want <= hi + tol):
            shown = _usd(lo) if lo == hi else f"{_usd(lo)}-{_usd(hi)}"
            bad.append({"field": key, "calculated": shown, "stated": _usd(want), "pct_field": pct_key})
    if not bad:
        return None
    return Finding("dollar_totals_match", ["issue_size"] + [b["field"] for b in bad] + [b["pct_field"] for b in bad],
                   "; ".join(f"{b['field']}: calculated {b['calculated']} but the filing states {b['stated']}"
                             for b in bad),
                   {"mismatches": bad, "stated_quote": stated.get("quote")})


def run_self_checks(values: dict, *, stated_initial_valuation_date: str | None = None,
                    hypothetical_rows: list[dict] | None = None,
                    stated_totals: dict | None = None) -> list[Finding]:
    found = [
        fees_reconcile(values),
        estimated_value_below_price(values),
        observation_dates_in_term(values),
        initial_valuation_date(values, stated_initial_valuation_date),
        payoff_matches_examples(values, hypothetical_rows),
        coupon_cross_check(values),
        maturity_after_pricing(values),
        payoff_matches_at_protection_levels(values, hypothetical_rows),
        dollar_totals_match(values, stated_totals),
    ]
    return [f for f in found if f is not None]
