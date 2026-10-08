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


def derive_all(values: dict) -> list[Derived]:
    """Every derivation the resolved ``values`` allow."""
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
    return out
