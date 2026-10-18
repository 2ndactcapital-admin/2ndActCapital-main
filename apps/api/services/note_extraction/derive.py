"""DERIVED values — computed from resolved fields, never extracted, always marked.

Every result is a ``Derived`` with ``derived=True``, the inputs it used and a
plain-language basis. The cascade writes it as a reading with source 'derived'
and stages it with resolution 'derived'. A derivation whose inputs are missing
or inconsistent returns None — it never guesses.

    tenor_months            pricing_date -> maturity_date (registry derived_from)
    max_principal_loss_pct  protection_type + buffer / barrier level + downside
                            leverage + principal_at_risk
    estimated value unit    decision B: whichever of estimated_value_per_1000 /
                            estimated_value_pct the filing STATED is extracted;
                            the other is derived from it (x10 or /10)
    no_call_months          notefix2 Task 4: issue_date -> the first observation
                            date in the schedule that carries a call level/amount
    currency / call_type /
    total_fees_usd /
    proceeds_to_issuer_usd  bulkfetch Task 9: CALCULATED dollar totals —
                            issue_size x the pct range / 100, each end of the
                            range separately. Never extracted, never edited.
    coupon_memory           notefix2 Task 4: DEFAULTS, applied only when the
                            field is unresolved — a stated value (including a
                            non-USD currency) is never overwritten, same
                            "decision B" guard every derivation already gets
                            from the cascade (``spec.extraction_method ==
                            METHOD_MODEL and resolved value is not None``).
                            call_type / coupon_memory need a FULL-TEXT signal
                            (is there any call/redemption or memory/accrual
                            wording at all) because "no call language found"
                            is not something any single field's resolved
                            value can answer on its own; the signal itself is
                            a rule over the whole filing
                            (services.note_extraction.rules.has_call_language
                            / has_memory_language), never a model's reading.

NOTEFIX2 Task 1c / Task 4: every function here takes already-RESOLVED inputs
(the cascade's or prefill's agreed value, never a single unverified reading)
— ``derive_all`` is the boundary; its caller is responsible for building
``values`` from resolution, not from one reader. The gold-1 pre-fill's
tenor_months bug (note #1: 96.18 computed from model_1's own typo-sentence
reading of pricing_date, while the rules independently read the correct
date from a label) was exactly a caller handing this module an unverified
single reading — fixed in prefill.py's ``_resolved_for_derivation``, not
here: this module has no way to know whether its caller did that correctly.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from services.note_extraction.schema import as_range


@dataclass
class Derived:
    field_key: str
    value: object
    inputs: dict
    basis: str
    derived: bool = True
    derived_from: list[str] = field(default_factory=list)


def _d(v) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _date(v) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _num(d: Decimal):
    q = d.quantize(Decimal("0.01")).normalize()
    f = float(q)
    return int(f) if f.is_integer() else f


def tenor_months(pricing_date, maturity_date) -> Derived | None:
    """Whole months plus the day fraction of the final month, to 2dp
    (2026-03-27 -> 2028-03-27 = 24; -> 2028-04-03 = 24.23)."""
    p, m = _date(pricing_date), _date(maturity_date)
    if p is None or m is None or m <= p:
        return None
    months = (m.year - p.year) * 12 + (m.month - p.month)
    days = m.day - p.day
    if days < 0:
        months -= 1
        prev_month = m.month - 1 or 12
        prev_year = m.year if m.month > 1 else m.year - 1
        days += calendar.monthrange(prev_year, prev_month)[1]
    frac = Decimal(days) / Decimal(calendar.monthrange(m.year, m.month)[1])
    value = Decimal(months) + frac
    return Derived("tenor_months", _num(value), {"pricing_date": p.isoformat(), "maturity_date": m.isoformat()},
                   "months from pricing_date to maturity_date", derived_from=["pricing_date", "maturity_date"])


def max_principal_loss_pct(protection_type, buffer_pct=None, barrier_pct=None, downside_leverage=None,
                           principal_at_risk=None) -> Derived | None:
    """The largest percent of principal lost at maturity.
        principal_at_risk False or 'full'  -> 0
        'none' or 'barrier'                -> 100 (below a barrier the whole decline applies)
        'buffer' X                         -> (100 - X) x downside leverage (default 1), capped at 100
        'floor'                            -> unknown (v3 has no floor level field) -> None
    """
    inputs = {"protection_type": protection_type, "buffer_pct": buffer_pct, "barrier_pct": barrier_pct,
              "downside_leverage": downside_leverage, "principal_at_risk": principal_at_risk}
    src = ["protection_type", "buffer_pct", "barrier_pct", "downside_leverage", "principal_at_risk"]
    if principal_at_risk is False or protection_type == "full":
        return Derived("max_principal_loss_pct", 0, inputs, "principal is not at risk at maturity", derived_from=src)
    if protection_type in ("none", "barrier"):
        return Derived("max_principal_loss_pct", 100, inputs,
                       "no buffer: the whole decline can apply", derived_from=src)
    if protection_type == "buffer":
        b = _d(buffer_pct)
        if b is None or not (Decimal(0) < b < Decimal(100)):
            return None
        lev = _d(downside_leverage) or Decimal(1)
        loss = min(Decimal(100), (Decimal(100) - b) * lev)
        return Derived("max_principal_loss_pct", _num(loss), inputs,
                       "(100 - buffer) x downside leverage, capped at 100", derived_from=src)
    return None


def estimated_value_counterpart(per_1000=None, pct=None) -> Derived | None:
    """Decision B. Exactly one of the two stated -> the other, marked derived.
    Both stated -> nothing to derive (each is its own extracted reading)."""
    r1000, rpct = as_range(per_1000), as_range(pct)
    if (r1000 is None) == (rpct is None):
        return None

    def scale(r, k):
        return {"min": _num(r["min"] * k) if r["min"] is not None else None,
                "max": _num(r["max"] * k) if r["max"] is not None else None,
                "bound": r.get("bound")}
    if r1000 is not None:
        return Derived("estimated_value_pct", scale(r1000, Decimal("0.1")), {"estimated_value_per_1000": per_1000},
                       "per $1,000 / 10", derived_from=["estimated_value_per_1000"])
    return Derived("estimated_value_per_1000", scale(rpct, Decimal(10)), {"estimated_value_pct": pct},
                   "percent of principal x 10", derived_from=["estimated_value_pct"])


def no_call_months(issue_date, observation_schedule) -> Derived | None:
    """Whole months from ``issue_date`` to the first observation date in the
    schedule that carries a call level or amount — None when there is no
    schedule, no issue date, or no row that actually checks a call."""
    idate = _date(issue_date)
    if idate is None or not isinstance(observation_schedule, list) or not observation_schedule:
        return None
    call_dates = sorted(
        d for row in observation_schedule if isinstance(row, dict)
        and (row.get("call_level_pct") is not None or row.get("call_amount") is not None)
        for d in [_date(row.get("observation_date"))] if d is not None
    )
    if not call_dates:
        return None
    first = call_dates[0]
    if first <= idate:
        return None
    months = (first.year - idate.year) * 12 + (first.month - idate.month)
    if first.day < idate.day:
        months -= 1
    if months < 0:
        return None
    return Derived("no_call_months", months,
                   {"issue_date": idate.isoformat(), "first_call_observation_date": first.isoformat()},
                   "months from issue_date to the first observation date that checks a call",
                   derived_from=["issue_date", "observation_schedule"])


def default_total_fees(total_fees_pct, agent_commission_pct, structuring_fee_pct=None,
                       other_fees_pct=None) -> Derived | None:
    """notefix2 F6: when the filing states ONLY an underwriting discount /
    agent's commission and nothing for ``total_fees_pct``, the commission IS
    the total — derived, never left blank. Declines to guess when more than
    one fee component is independently stated (structuring or other fees),
    since then the total is not simply the commission."""
    if total_fees_pct is not None:
        return None
    r = as_range(agent_commission_pct)
    if r is None:
        return None
    if as_range(structuring_fee_pct) is not None or as_range(other_fees_pct) is not None:
        return None
    value = {"min": _num(r["min"]) if r["min"] is not None else None,
             "max": _num(r["max"]) if r["max"] is not None else None, "bound": r.get("bound")}
    return Derived("total_fees_pct", value, {"agent_commission_pct": agent_commission_pct},
                   "default: total fees = the stated underwriting discount / agent's commission "
                   "(no other fee component stated)", derived_from=["agent_commission_pct"])


def default_currency(currency) -> Derived | None:
    """USD when the filing states no currency at all."""
    if currency is not None:
        return None
    return Derived("currency", "USD", {"currency": None}, "default: USD when the filing states no currency",
                   derived_from=["currency"])


def default_call_type(call_type, has_call_language: bool) -> Derived | None:
    """'none' only when the field is unresolved AND no call/redemption
    wording exists anywhere in the filing — a genuine absence, not a guess
    made because the model happened not to answer."""
    if call_type is not None or has_call_language:
        return None
    return Derived("call_type", "none", {"call_type": None, "has_call_language": False},
                   "default: 'none' — no call or redemption wording found in the filing",
                   derived_from=["call_type"])


def default_coupon_memory(coupon_type, coupon_memory, has_memory_language: bool) -> Derived | None:
    """False only for an unresolved field on a note with a contingent coupon
    AND no memory / accrual-of-missed-coupons wording anywhere in the
    filing."""
    if coupon_memory is not None or coupon_type != "contingent" or has_memory_language:
        return None
    return Derived("coupon_memory", False, {"coupon_type": coupon_type, "has_memory_language": False},
                   "default: false — a contingent coupon with no memory/accrual-of-missed-coupons wording",
                   derived_from=["coupon_type"])


_CENT = Decimal("0.01")
# Shown on the review screen as CALCULATED, never edited: the review API
# refuses a gold write to either key, and they never count toward a note's
# fields-to-review total.
CALCULATED_FIELDS = ("total_fees_usd", "proceeds_to_issuer_usd")


def _usd_range(issue_size, pct_value) -> dict | None:
    size = _dec_or_none(issue_size)
    r = as_range(pct_value)
    if size is None or size <= 0 or r is None:
        return None

    def end(v):
        return None if v is None else _num((size * Decimal(str(v)) / 100).quantize(_CENT))

    return {"min": end(r["min"]), "max": end(r["max"]), "bound": r.get("bound")}


def _dec_or_none(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def dollar_totals(issue_size, total_fees_pct, proceeds_to_issuer_pct) -> list[Derived]:
    """bulkfetch Task 9: total_fees_usd and proceeds_to_issuer_usd =
    issue_size x pct / 100, range ends computed separately (an "up to 3.25%"
    fee is "up to $27,950" on an $860,000 issue). Nothing when an input is
    missing — never a guess."""
    out = []
    for key, pct_key, pct in (("total_fees_usd", "total_fees_pct", total_fees_pct),
                              ("proceeds_to_issuer_usd", "proceeds_to_issuer_pct", proceeds_to_issuer_pct)):
        v = _usd_range(issue_size, pct)
        if v is not None:
            out.append(Derived(key, v, {"issue_size": issue_size, pct_key: pct},
                               f"calculated: issue_size x {pct_key} / 100, each end of the range separately",
                               derived_from=["issue_size", pct_key]))
    return out


def derive_all(values: dict, *, has_call_language: bool = False, has_memory_language: bool = False
              ) -> list[Derived]:
    """Every derivation the RESOLVED ``values`` allow. ``has_call_language``
    / ``has_memory_language`` are full-filing-text signals (notefix1-style
    rule facts, never a model's reading) the two language-dependent defaults
    need."""
    out: list[Derived] = []
    t = tenor_months(values.get("pricing_date"), values.get("maturity_date"))
    if t:
        out.append(t)
    m = max_principal_loss_pct(values.get("protection_type"), values.get("buffer_pct"), values.get("barrier_pct"),
                               values.get("downside_leverage"), values.get("principal_at_risk"))
    if m:
        out.append(m)
    e = estimated_value_counterpart(values.get("estimated_value_per_1000"), values.get("estimated_value_pct"))
    if e:
        out.append(e)
    n = no_call_months(values.get("issue_date"), values.get("observation_schedule"))
    if n:
        out.append(n)
    tf = default_total_fees(values.get("total_fees_pct"), values.get("agent_commission_pct"),
                            values.get("structuring_fee_pct"), values.get("other_fees_pct"))
    if tf:
        out.append(tf)
    c = default_currency(values.get("currency"))
    if c:
        out.append(c)
    ct = default_call_type(values.get("call_type"), has_call_language)
    if ct:
        out.append(ct)
    cm = default_coupon_memory(values.get("coupon_type"), values.get("coupon_memory"), has_memory_language)
    if cm:
        out.append(cm)
    # dollar totals read total_fees_pct AFTER its own default above
    fees = values.get("total_fees_pct") if values.get("total_fees_pct") is not None else (tf.value if tf else None)
    out += dollar_totals(values.get("issue_size"), fees, values.get("proceeds_to_issuer_pct"))
    return out
