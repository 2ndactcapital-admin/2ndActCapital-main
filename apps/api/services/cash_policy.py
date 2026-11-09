"""CASH02 — cash POLICIES and the THREE SLEEVES (Near-term / Reserve / Long-term). A policy is a TARGET, never a trade.

A policy belongs to a HOUSEHOLD (the default) or to an ENTITY that pays its own obligations (an override). It holds two
boundaries in MONTHS OF OBLIGATIONS, a horizon, a rung count, a buffer, an objective, a preset and the minimum
coverage. Everything in dollars is DERIVED at read time from (policy, the cash01 obligation schedule); nothing in
dollars is stored. docs/cash/CASH_POLICY.md is the reference; every rule below is a pure function.

SLEEVE WINDOWS (months from "today", half-open — an obligation exactly ON a boundary belongs to the LATER sleeve):
    Near-term  [0, N)      Reserve  [N, R)      Long-term  [R, horizon)
  each end clipped to the horizon: a horizon shorter than R leaves Long-term empty and cuts Reserve at the horizon (the
  window says so). Month m starts on ``cash_obligations.add_months(today, m)`` — the ledger's own month arithmetic.

SLEEVE TARGETS: Σ counted obligations dated in the window (the household's share-weighted lines, or the entity's own —
``cash_coverage.scope_lines``). Near-term adds the BUFFER: buffer_months × the average monthly gross outflow
(``cash_coverage.monthly_outflow``, the coverage formula). % of the liquid total = target ÷ liquid × 100 (one decimal);
no liquid → "not computed — no cash position". Unallocated = liquid − Σ targets (negative = short).

PRESETS → boundaries (the stored N / R always equal the preset's mapping; the server applies it):
    stay_liquid  N = R = horizon (everything Near-term)      balanced  N = 0, R = horizon (everything Reserve)
    recommended  N, R from the questionnaire objective         custom    the boundaries as entered

QUESTIONNAIRE (3 steps). (1) Outflows: prefilled with the scheduled next-12-month outflow; trailing 3-month ACTUAL
outflows (calls + fees in portfolio.transactions) annualised ×4 are the cross-check — the entered figure is "well
below" when < 80 % of that; no transactions at all → "no outflow history". (2) Objective, a 5-point scale:
    1 most stable N=6 R=18 · 2 N=4 R=15 · 3 balanced N=3 R=12 (v1's defaults) · 4 N=2 R=9 · 5 most yield N=1 R=6
  (bounds: N ∈ [1, 6], R ∈ [6, 18]; monotone; deterministic). (3) Recommendation: the editor view at those boundaries.
  Applying it saves a DRAFT — never an active policy.

ESTIMATED YIELD per sleeve (``YIELD_PROXIES``, FRED via services.market_data.read_service, the latest observation in
the 31 days to "today"): Near-term 3-month Treasury (fred.dgs3mo; else derived fred.dgs10 − fred.t10y3m on a common
date), Reserve 6-month / 1-year (fred.dgs6mo, fred.dgs1), Long-term 2-year (fred.dgs2). Labelled "estimate — Treasury
curve proxy, as of <date>"; no series → "not estimated — needs <series>". Net = the proxy (no cash advisory fee is
configured). AFTER-TAX only with an approved tax profile (services.tax_profiles) for the policy's subject and tax
year: yield × (1 − (federal ordinary + NIIT when it applies)) — Treasury interest is state- and local-tax exempt, so
the profile's state / city rates are NOT applied; else "after-tax: needs a tax profile".

MAKER-CHECKER (services.sod item type ``cash_policy``; services.human_tasks kind ``cash_policy_approval``):
    save → DRAFT (editing a draft keeps its id: system-axis archive, the editor joins maker_ids; editing an ACTIVE
           policy creates a new draft version)
    submit → PENDING_APPROVAL + the human task (payload = the derived view as of submit — what the checker approves)
    approve (a different eligible person; sod.gate + complete_task; recorded in sod_decisions) → ACTIVE; the prior
           active version → RETIRED (valid_to = now), bi-temporally
    reject (a reason) → REJECTED
The database refuses approved_by ∈ maker_ids (cash_policies_maker_checker_chk) and a second active policy per scope.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from services import cash_coverage as CC
from services import cash_obligations as OB
from services import human_tasks as HT
from services import sod

log = logging.getLogger(__name__)

ZERO = Decimal(0)
HUNDRED = Decimal(100)
TENTH = Decimal("0.1")
CENT = Decimal("0.01")

T = "public.cash_policies"
PERMISSION = "manage_cash_policy"
TASK = "cash_policy_approval"
SUBJECT = "cash_policy"
EVENT_SOURCE = "cash_policy"
#: the disclosure-library key every estimated yield carries (services.disclosures)
YIELD_KEY = "cash.yield_estimate"
YIELD_ESTIMATE = "Estimates use the Treasury curve; actual yields differ; have counsel review."

SCOPES = (("household", "Household (default)"), ("entity", "Entity override — pays its own obligations"))
SLEEVES = (("near_term", "Near-term"), ("reserve", "Reserve"), ("long_term", "Long-term"))
PRESETS = (("stay_liquid", "Stay liquid"), ("balanced", "Balanced"), ("recommended", "Recommended"),
           ("custom", "Custom"))
PRESET_NOTES = {"stay_liquid": "Everything Near-term: N = R = the horizon.",
                "balanced": "Everything Reserve: N = 0, R = the horizon.",
                "recommended": "From the questionnaire: the objective positions N and R.",
                "custom": "The boundaries as you set them."}
STATUS_LABELS = {"draft": "Draft — not submitted", "pending_approval": "Awaiting a second person's approval",
                 "active": "Active", "retired": "Retired — replaced by a newer version", "rejected": "Rejected"}
OBJECTIVES = (("stability", "Stability"), ("balanced", "Balanced"), ("yield", "Yield"))
#: score → (objective, N, R, label). Documented bounds: N ∈ [1, 6], R ∈ [6, 18]; 3 = v1's defaults.
OBJECTIVE_SCALE = (
    (1, "stability", 6, 18, "Most stable"),
    (2, "stability", 4, 15, "More stable"),
    (3, "balanced", 3, 12, "Balanced"),
    (4, "yield", 2, 9, "More yield"),
    (5, "yield", 1, 6, "Most yield"),
)
DEFAULTS = {"near_months": 3, "reserve_months": 12, "horizon_months": 24, "rung_count": 8, "buffer_months": "0",
            "objective_score": 3, "preset": "custom", "min_coverage_months": "6"}
BOUNDS = {"near_months": (0, 120), "reserve_months": (0, 120), "horizon_months": (1, OB.MAX_HORIZON_MONTHS),
          "rung_count": (1, 52), "buffer_months": (0, 12), "objective_score": (1, 5), "min_coverage_months": (0, 120)}
FIELD_LABELS = {"near_months": "Near-term / Reserve boundary (months)", "reserve_months":
                "Reserve / Long-term boundary (months)", "horizon_months": "Horizon (months)",
                "rung_count": "Ladder rungs", "buffer_months": "Near-term buffer (months of outflow)",
                "objective": "Objective", "objective_score": "Objective (1 most stable – 5 most yield)",
                "preset": "Preset", "min_coverage_months": "Minimum coverage (months)"}
POLICY_FIELDS = ("near_months", "reserve_months", "horizon_months", "rung_count", "buffer_months", "objective",
                 "objective_score", "preset", "min_coverage_months")

#: ordered candidates per sleeve: the first one with data wins. "difference" = a − b on their latest COMMON date.
YIELD_PROXIES = {
    "near_term": ({"kind": "series", "keys": ("fred.dgs3mo",), "basis": "3-month Treasury"},
                  {"kind": "difference", "keys": ("fred.dgs10", "fred.t10y3m"),
                   "basis": "3-month Treasury, derived: 10-year yield minus the 10-year–3-month spread"}),
    "reserve": ({"kind": "series", "keys": ("fred.dgs6mo",), "basis": "6-month Treasury"},
                {"kind": "series", "keys": ("fred.dgs1",), "basis": "1-year Treasury"}),
    "long_term": ({"kind": "series", "keys": ("fred.dgs2",), "basis": "2-year Treasury"},),
}
YIELD_LOOKBACK_DAYS = 31
TRAILING_MONTHS = 3
WELL_BELOW_RATIO = Decimal("0.8")
OUTFLOW_CATEGORIES = ("call", "fee")
NO_TAX_PROFILE = "after-tax: needs a tax profile"
NO_LIQUID = "not computed — no cash position"
NO_HISTORY = "no outflow history"


class PolicyError(ValueError):
    """Refused for a reason the caller can fix (422)."""


class PolicyNotFound(LookupError):
    """No such policy in the caller's org (404)."""


class PolicyConflict(Exception):
    """The policy is not in a state that allows this (409)."""


class MakerCheckerViolation(PermissionError):
    """A maker tried to decide their own policy (403)."""


# ═══ Pure calculation ═════════════════════════════════════════════════════════════════════════════════════════
def _m(v: Decimal | None) -> str | None:
    return OB.money_text(v) if v is not None else None


def pct_text(v: Decimal | None) -> str | None:
    return None if v is None else format(v.quantize(TENTH, rounding=ROUND_HALF_UP), "f")


def rate_text(v: Decimal | None) -> str | None:
    return None if v is None else format(v.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def objective_of(score: int) -> str:
    for s, obj, _n, _r, _l in OBJECTIVE_SCALE:
        if s == score:
            return obj
    raise PolicyError("objective: a score from 1 (most stable) to 5 (most yield)")


def recommend(score: int) -> tuple[int, int]:
    """Questionnaire step 2 → (N, R). Deterministic; the table is the documentation."""
    for s, _obj, n, r, _l in OBJECTIVE_SCALE:
        if s == score:
            return n, r
    raise PolicyError("objective: a score from 1 (most stable) to 5 (most yield)")


def preset_boundaries(preset: str, *, horizon: int, near: int | None = None, reserve: int | None = None,
                      score: int = 3) -> tuple[int, int]:
    if preset == "stay_liquid":
        return horizon, horizon
    if preset == "balanced":
        return 0, horizon
    if preset == "recommended":
        return recommend(score)
    if preset == "custom":
        if near is None or reserve is None:
            raise PolicyError("custom: both boundaries are required")
        return near, reserve
    raise PolicyError(f"preset is one of {[k for k, _ in PRESETS]}")


def windows(near: int, reserve: int, horizon: int, today: date) -> list[dict]:
    """The three half-open month windows, each end clipped to the horizon. Pure."""
    a, b, h = min(near, horizon), min(reserve, horizon), horizon
    spans = (("near_term", 0, a, near), ("reserve", a, b, reserve), ("long_term", b, h, h))
    out = []
    labels = dict(SLEEVES)
    for key, s, e, want in spans:
        note = None
        if key != "long_term" and want > horizon:
            note = f"the horizon ({horizon} months) ends before this boundary ({want} months) — clipped"
        elif key == "long_term" and reserve >= horizon:
            note = (f"empty — the Reserve / Long-term boundary ({reserve} months) is at or beyond the horizon "
                    f"({horizon} months)")
        out.append({"key": key, "label": labels[key], "start_month": s, "end_month": e, "months": e - s,
                    "from": OB.add_months(today, s), "until": OB.add_months(today, e), "note": note})
    return out


def sleeve_targets(near: int, reserve: int, horizon: int, buffer_months: Decimal, lines: list[dict],
                   liquid: Decimal | None, today: date) -> dict:
    """Sleeve $ targets from the obligation schedule. ``lines``: [{date, amount, counted, key}]. Pure, Decimal."""
    counted = [ln for ln in lines if ln.get("counted", True)]
    monthly = CC.monthly_outflow(counted, today)
    buffer_amount = (buffer_months * monthly).quantize(CENT, rounding=ROUND_HALF_UP) if buffer_months else ZERO
    sleeves, total = [], ZERO
    for w in windows(near, reserve, horizon, today):
        inside = [ln for ln in counted if w["from"] <= ln["date"] < w["until"]]
        amount = sum((ln["amount"] for ln in inside), ZERO)
        buf = buffer_amount if w["key"] == "near_term" else ZERO
        target = amount + buf
        total += target
        sleeves.append({**w, "obligations_amount": amount, "buffer_amount": buf, "amount": target,
                        "count": len(inside), "keys": [ln["key"] for ln in inside]})
    for s in sleeves:
        s["pct"] = (s["amount"] / liquid * HUNDRED) if liquid is not None and liquid > 0 else None
    beyond = sum((ln["amount"] for ln in counted if ln["date"] >= OB.add_months(today, horizon)), ZERO)
    return {"sleeves": sleeves, "total": total, "liquid": liquid, "monthly_outflow": monthly,
            "buffer_amount": buffer_amount,
            "unallocated": (liquid - total) if liquid is not None else None, "beyond_horizon": beyond}


def latest(points: list[tuple[date, Decimal]], today: date) -> tuple[date, Decimal] | None:
    eligible = [(d, v) for d, v in points if d <= today]
    return max(eligible, key=lambda x: x[0]) if eligible else None


def proxy_yield(sleeve: str, reads: dict, today: date, proxies: dict | None = None) -> dict:
    """One sleeve's estimated yield from the FIRST candidate with data. ``reads``: {series_key: [(date, Decimal)]}
    (a key absent / empty = not available). Pure."""
    cands = (proxies or YIELD_PROXIES)[sleeve]
    for c in cands:
        if c["kind"] == "series":
            hit = latest(reads.get(c["keys"][0]) or [], today)
            if hit:
                return {"estimated": True, "yield_pct": hit[1], "as_of": hit[0], "basis": c["basis"],
                        "series": list(c["keys"]), "derived": False}
        elif c["kind"] == "difference":
            a, b = (dict(reads.get(k) or []) for k in c["keys"])
            common = sorted(d for d in set(a) & set(b) if d <= today)
            if common:
                d = common[-1]
                return {"estimated": True, "yield_pct": a[d] - b[d], "as_of": d, "basis": c["basis"],
                        "series": list(c["keys"]), "derived": True}
    needs = " or ".join(" + ".join(c["keys"]) for c in cands)
    return {"estimated": False, "needs": needs, "text": f"not estimated — needs {needs}"}


def tax_rate(profile: dict | None) -> Decimal | None:
    """Federal ordinary + NIIT (when it applies) — the rate on Treasury interest; state / city NOT applied."""
    if not profile or profile.get("federal_ordinary_rate") is None:
        return None
    fo = Decimal(str(profile["federal_ordinary_rate"]))
    niit = Decimal(str(profile.get("niit_rate") or "0")) if profile.get("niit_applies") else ZERO
    return fo + niit


def after_tax(y: dict, profile: dict | None) -> dict:
    rate = tax_rate(profile)
    if not y.get("estimated"):
        return {"estimated": False, "text": y.get("text")}
    if rate is None:
        return {"estimated": False, "text": NO_TAX_PROFILE}
    fo = Decimal(str(profile["federal_ordinary_rate"]))
    return {"estimated": True, "yield_pct": y["yield_pct"] * (1 - rate), "rate": rate,
            "label": (f"after-tax estimate — federal ordinary {fo} + NIIT {rate - fo}; Treasury interest is state- and "
                      f"local-tax exempt (tax profile {profile.get('tax_year')}, as of {profile.get('as_of')})")}


def blended(sleeves: list[dict], key: str) -> dict:
    """Σ(target × yield) ÷ Σ target over the sleeves that hold money; any such sleeve unestimated → not estimated."""
    held = [s for s in sleeves if s["amount"] > 0]
    if not held:
        return {"estimated": False, "text": "no obligations in the sleeves"}
    missing = [s for s in held if not (s.get(key) or {}).get("estimated")]
    if missing:
        return {"estimated": False, "text": "not estimated — " + "; ".join(
            f"{s['label']}: {(s.get(key) or {}).get('text')}" for s in missing)}
    tot = sum((s["amount"] for s in held), ZERO)
    return {"estimated": True, "yield_pct": sum((s["amount"] * s[key]["yield_pct"] for s in held), ZERO) / tot}


def evaluate(fields: dict, lines: list[dict], liquid: Decimal | None, reads: dict, profile: dict | None,
             today: date, proxies: dict | None = None) -> dict:
    """The whole derived view of one policy (or an unsaved edit). Pure, Decimal; every figure with source + as-of."""
    t = sleeve_targets(fields["near_months"], fields["reserve_months"], fields["horizon_months"],
                       Decimal(str(fields["buffer_months"])), lines, liquid, today)
    for s in t["sleeves"]:
        s["yield"] = proxy_yield(s["key"], reads, today, proxies)
        s["after_tax"] = after_tax(s["yield"], profile)
    counted = [ln for ln in lines if ln.get("counted", True)]
    months = CC.months_of_coverage(liquid if liquid is not None else ZERO, CC.monthly_outflow(counted, today))
    mn = Decimal(str(fields["min_coverage_months"]))
    return {**t, "coverage": {"months": months, "min_months": mn,
                              "below_minimum": months is not None and months < mn},
            "blended": blended(t["sleeves"], "yield"), "blended_after_tax": blended(t["sleeves"], "after_tax"),
            "today": today, "tax_profile": bool(profile)}


def render(view: dict) -> dict:
    """The JSON form: money and rates as fixed-point text, dates ISO, labels attached."""
    def y(x: dict) -> dict:
        if not x.get("estimated"):
            return {"estimated": False, "text": x.get("text"), **({"needs": x["needs"]} if x.get("needs") else {})}
        out = {"estimated": True, "yield_pct": rate_text(x["yield_pct"])}
        if "as_of" in x:
            out |= {"as_of": x["as_of"].isoformat(), "basis": x["basis"], "series": x["series"],
                    "derived": x["derived"], "label": f"estimate — Treasury curve proxy, as of {x['as_of'].isoformat()}"}
        if "label" in x:
            out["label"] = x["label"]
        return out

    liquid = view["liquid"]
    cov = view["coverage"]
    return {
        "as_of": view["today"].isoformat(),
        "source": "the cash01 obligation schedule (stored + TA-projected + debt service + CRM payments) and the "
                  "net-worth core's Cash & Equivalents",
        "liquid": _m(liquid), "liquid_text": None if liquid is not None else NO_LIQUID,
        "total": _m(view["total"]), "unallocated": _m(view["unallocated"]),
        "monthly_outflow": _m(view["monthly_outflow"]), "buffer_amount": _m(view["buffer_amount"]),
        "beyond_horizon": _m(view["beyond_horizon"]),
        "sleeves": [{"key": s["key"], "label": s["label"], "start_month": s["start_month"], "end_month": s["end_month"],
                     "months": s["months"], "from": s["from"].isoformat(), "until": s["until"].isoformat(),
                     "note": s["note"], "amount": _m(s["amount"]), "obligations_amount": _m(s["obligations_amount"]),
                     "buffer_amount": _m(s["buffer_amount"]), "count": s["count"], "keys": s["keys"],
                     "pct": pct_text(s["pct"]), "pct_text": None if s["pct"] is not None else NO_LIQUID,
                     "yield": y(s["yield"]), "after_tax": y(s["after_tax"])} for s in view["sleeves"]],
        "blended": y(view["blended"]), "blended_after_tax": y(view["blended_after_tax"]),
        "coverage": {"months": None if cov["months"] is None else format(cov["months"], "f"),
                     "months_text": "no obligations in the next 12 months" if cov["months"] is None else None,
                     "min_months": format(cov["min_months"].normalize(), "f"),
                     "below_minimum": cov["below_minimum"]},
        "tax_profile": view["tax_profile"],
    }


def questionnaire(entered: Decimal | None, scheduled_12m: Decimal, trailing_3m: Decimal | None) -> dict:
    """Step 1's cross-check. Pure. ``trailing_3m`` None = no transaction history for the scope."""
    figure = scheduled_12m if entered is None else entered
    warnings = []
    annualised = None
    if trailing_3m is None:
        warnings.append({"key": "no_history", "text": NO_HISTORY})
    else:
        annualised = trailing_3m * 4
        if figure < annualised * WELL_BELOW_RATIO:
            warnings.append({"key": "well_below", "text": (
                f"the outflow entered ({_m(figure)}) is well below the trailing 3 months' actual outflows annualised "
                f"({_m(annualised)}) — check the obligations ledger is complete")})
    if figure > scheduled_12m:
        warnings.append({"key": "unscheduled", "text": (
            f"{_m(figure - scheduled_12m)} of the 12-month outflow entered is not on the obligations ledger — add it "
            "so the sleeves cover it")})
    return {"scheduled_12m": _m(scheduled_12m), "entered_12m": _m(figure),
            "trailing_3m": _m(trailing_3m), "trailing_annualised": _m(annualised),
            "trailing_basis": "capital calls and fees paid (portfolio.transactions, categories call / fee) — no "
                              "spending or withdrawal type exists in the ledger, so lifestyle outflows are not seen",
            "warnings": warnings}


def diff(active: dict | None, other: dict | None) -> list[dict]:
    if not active or not other:
        return []
    out = []
    for f in POLICY_FIELDS:
        a, b = _field_text(active.get(f)), _field_text(other.get(f))
        if a != b:
            out.append({"field": f, "label": FIELD_LABELS[f], "from": a, "to": b})
    return out


def _field_text(v):
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")
    return None if v is None else str(v)


# ═══ Validation ═══════════════════════════════════════════════════════════════════════════════════════════════
def _int(data: dict, k: str) -> int:
    v = data.get(k, DEFAULTS.get(k))
    if isinstance(v, bool) or not isinstance(v, int):
        raise PolicyError(f"{k}: a whole number of months, not {v!r}")
    lo, hi = BOUNDS[k]
    if not lo <= v <= hi:
        raise PolicyError(f"{k}: {lo}–{hi}")
    return v


def _dec(data: dict, k: str) -> Decimal:
    v = data.get(k, DEFAULTS.get(k))
    if isinstance(v, (bool, float)):
        raise PolicyError(f"{k}: send the number as text (e.g. \"6\")")
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        raise PolicyError(f"{k}: a number, not {v!r}") from None
    lo, hi = BOUNDS[k]
    if not d.is_finite() or d < lo or d > hi or d != d.quantize(CENT):
        raise PolicyError(f"{k}: {lo}–{hi}, at most two decimals")
    return d


def normalise(data: dict) -> dict:
    """The stored fields from a body: the preset applied (stored N / R always equal its mapping), the objective named
    from its score, the database's own rules checked first so a refusal names the field."""
    unknown = set(data) - set(POLICY_FIELDS) - {"near_months", "reserve_months"}
    if unknown:
        raise PolicyError(f"unknown field(s): {sorted(unknown)}")
    preset = data.get("preset", DEFAULTS["preset"])
    if preset not in dict(PRESETS):
        raise PolicyError(f"preset is one of {[k for k, _ in PRESETS]}")
    horizon = _int(data, "horizon_months")
    score = _int(data, "objective_score")
    near = _int(data, "near_months") if preset == "custom" else None
    reserve = _int(data, "reserve_months") if preset == "custom" else None
    n, r = preset_boundaries(preset, horizon=horizon, near=near, reserve=reserve, score=score)
    if n > r:
        raise PolicyError("the Near-term / Reserve boundary cannot be after the Reserve / Long-term boundary")
    obj = objective_of(score)
    if data.get("objective") not in (None, obj):
        raise PolicyError(f"objective follows the score: a score of {score} is '{obj}'")
    return {"near_months": n, "reserve_months": r, "horizon_months": horizon, "rung_count": _int(data, "rung_count"),
            "buffer_months": _dec(data, "buffer_months"), "objective": obj, "objective_score": score,
            "preset": preset, "min_coverage_months": _dec(data, "min_coverage_months")}


# ═══ Reads ════════════════════════════════════════════════════════════════════════════════════════════════════
def _uuid(v, what: str) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        raise PolicyNotFound(f"{what} not found") from None


def _iso(v):
    return v.isoformat() if v is not None else None


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


async def read_yields(conn, today: date, proxies: dict | None = None) -> dict:
    """{series_key: [(date, Decimal)]} for every proxy series, through the market-data read service (one key per
    request so a missing series never hides another). A key the registry lacks or holds inactive → []."""
    from services.market_data import read_service as RS

    keys = sorted({k for cands in (proxies or YIELD_PROXIES).values() for c in cands for k in c["keys"]})
    out: dict = {}
    for k in keys:
        try:
            res = await RS.read_series(conn, RS.SeriesQuery([k], today - timedelta(days=YIELD_LOOKBACK_DAYS), today,
                                                            "native"))
            pts = res["series"][0]["points"] if res.get("series") else []
            out[k] = [(date.fromisoformat(d), Decimal(v)) for d, v in pts]
        except RS.MarketDataRequestError:
            out[k] = []
    return out


async def trailing_outflows(conn, org_id, weights: dict, today: date) -> Decimal | None:
    """Σ call + fee debits of the scope's positions (at each owner's weight) in the trailing 3 months; None when the
    scope's positions carry no transaction at all (no history)."""
    if not weights:
        return None
    since = OB.add_months(today, -TRAILING_MONTHS)
    rows = await conn.fetch(
        "SELECT p.owner_entity_id::text AS e, t.trade_date, tt.category, "
        "abs(COALESCE(t.net_amount, t.gross_amount, 0)) AS amt FROM portfolio.transactions t "
        "JOIN portfolio.positions p ON p.id = t.position_id AND p.org_id = t.org_id "
        "JOIN public.transaction_types tt ON tt.code = t.transaction_type_code "
        "WHERE t.org_id = $1::uuid AND p.owner_entity_id = ANY($2::uuid[]) AND t.valid_to IS NULL "
        "AND t.system_to IS NULL AND t.trade_date < $3::date",
        str(org_id), [uuid.UUID(e) for e in weights], today)
    if not rows:
        return None
    return sum((Decimal(r["amt"]) * weights[r["e"]] for r in rows
                if r["category"] in OUTFLOW_CATEGORIES and since <= r["trade_date"] < today), ZERO)


async def subject_profile(conn, org_id, scope_type: str, scope_id, today: date) -> dict | None:
    from services import tax_profiles as TP

    return await TP.approved_profile(conn, org_id, scope_type, scope_id, today.year)


async def live_rows(conn, org_id, *, household_id=None, entity_ids=()) -> list[dict]:
    rows = await conn.fetch(
        f"SELECT * FROM {T} WHERE org_id = $1::uuid AND system_to IS NULL AND (household_id = $2::uuid OR "
        "entity_id = ANY($3::uuid[])) ORDER BY scope_type, version_no DESC",
        str(org_id), str(household_id) if household_id else None, [uuid.UUID(str(e)) for e in entity_ids])
    return [dict(r) for r in rows]


async def load(conn, org_id, policy_id, *, lock: bool = False) -> dict:
    pid = _uuid(policy_id, "cash policy")
    row = await conn.fetchrow(f"SELECT * FROM {T} WHERE id = $1::uuid AND org_id = $2::uuid AND system_to IS NULL"
                              + (" FOR UPDATE" if lock else ""), pid, str(org_id))
    if row is None:
        raise PolicyNotFound("cash policy not found")
    return dict(row)


def fields_of(row: dict) -> dict:
    return {k: row[k] for k in POLICY_FIELDS}


def policy_out(r: dict, names: dict | None = None) -> dict:
    names = names or {}
    return {"id": str(r["id"]), "scope_type": r["scope_type"],
            "household_id": str(r["household_id"]) if r["household_id"] else None,
            "entity_id": str(r["entity_id"]) if r["entity_id"] else None, "version_no": r["version_no"],
            "near_months": r["near_months"], "reserve_months": r["reserve_months"],
            "horizon_months": r["horizon_months"], "rung_count": r["rung_count"],
            "buffer_months": format(r["buffer_months"].normalize(), "f"), "objective": r["objective"],
            "objective_score": r["objective_score"], "preset": r["preset"],
            "min_coverage_months": format(r["min_coverage_months"].normalize(), "f"),
            "status": r["status"], "status_label": STATUS_LABELS[r["status"]],
            "maker_ids": [str(m) for m in r["maker_ids"]],
            "maker_names": [names.get(str(m), "A colleague") for m in r["maker_ids"]],
            "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
            "approved_by_name": names.get(str(r["approved_by"])) if r["approved_by"] else None,
            "approved_at": _iso(r["approved_at"]), "submitted_at": _iso(r["submitted_at"]),
            "approval_task_id": str(r["approval_task_id"]) if r["approval_task_id"] else None,
            "rejected_at": _iso(r["rejected_at"]), "reject_reason": r["reject_reason"],
            "decision_note": r["decision_note"], "retired_at": _iso(r["retired_at"]),
            "retired_reason": r["retired_reason"], "reason": r["reason"], "questionnaire": _loads(r["questionnaire"]),
            "valid_from": _iso(r["valid_from"]), "valid_to": _iso(r["valid_to"]), "created_at": _iso(r["created_at"])}


async def user_names(conn, ids) -> dict:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    rows = await conn.fetch("SELECT id::text AS id, full_name FROM public.users WHERE id = ANY($1::uuid[])", ids)
    return {r["id"]: r["full_name"] or "A colleague" for r in rows}


def presets_view(base: dict, lines, liquid, reads, profile, today, proxies=None) -> list[dict]:
    """Every preset at the base horizon / score / buffer / minimum, with its derived sleeves and blended yield."""
    out = []
    for key, label in PRESETS:
        f = dict(base, preset=key)
        try:
            n = normalise({**{k: f[k] for k in POLICY_FIELDS if k != "objective"}})
        except PolicyError:
            continue
        v = render(evaluate(n, lines, liquid, reads, profile, today, proxies))
        out.append({"key": key, "label": label, "note": PRESET_NOTES[key], "near_months": n["near_months"],
                    "reserve_months": n["reserve_months"], "sleeves": v["sleeves"], "blended": v["blended"],
                    "blended_after_tax": v["blended_after_tax"]})
    return out


def _as_body(row_or_fields: dict) -> dict:
    f = {k: row_or_fields[k] for k in POLICY_FIELDS}
    f["buffer_months"] = format(Decimal(str(f["buffer_months"])).normalize(), "f")
    f["min_coverage_months"] = format(Decimal(str(f["min_coverage_months"])).normalize(), "f")
    return f


def vocabularies(can_author: bool) -> dict:
    return {"scopes": [{"key": k, "label": v} for k, v in SCOPES],
            "sleeves": [{"key": k, "label": v} for k, v in SLEEVES],
            "presets": [{"key": k, "label": v, "note": PRESET_NOTES[k]} for k, v in PRESETS],
            "statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
            "objectives": [{"key": k, "label": v} for k, v in OBJECTIVES],
            "objective_scale": [{"score": s, "objective": o, "near_months": n, "reserve_months": r, "label": lb}
                                for s, o, n, r, lb in OBJECTIVE_SCALE],
            "fields": [{"key": k, "label": v} for k, v in FIELD_LABELS.items()],
            "bounds": {k: {"min": lo, "max": hi} for k, (lo, hi) in BOUNDS.items()},
            "defaults": dict(DEFAULTS),
            "editable": list(POLICY_FIELDS) if can_author else [],
            "inline_editable": []}


async def household_block(conn, org_id, household_id, *, report, today: date, member: bool, can_author: bool,
                          user_id, proxies: dict | None = None) -> dict:
    """The Treasury envelope's ``policy`` block: per scope (the household + every entity with a policy) the ACTIVE
    policy with its derived sleeves; for staff also the open version (draft / awaiting approval) with its derived view,
    its diff vs active, its task and whether THIS caller may decide it, and the version history. A member gets the
    active policies only (read-only). ``report`` is cash_coverage.household_report's (its horizon must cover every
    policy's — the caller rebuilds it when not)."""
    ents = [r["entity_id"] for r in report.entities]
    rows = await live_rows(conn, org_id, household_id=household_id, entity_ids=list(report.shares))
    reads = await read_yields(conn, today, proxies)
    names = await user_names(conn, [m for r in rows for m in (*r["maker_ids"], r["approved_by"])])
    scope_keys = [("household", str(household_id))] + sorted(
        {("entity", str(r["entity_id"])) for r in rows if r["scope_type"] == "entity"})
    scopes = []
    for st, sid in scope_keys:
        mine = [r for r in rows if r["scope_type"] == st and str(r["household_id"] if st == "household"
                                                                  else r["entity_id"]) == sid]
        active = next((r for r in mine if r["status"] == "active"), None)
        open_ = next((r for r in mine if r["status"] in ("draft", "pending_approval")), None)
        if member and active is None:
            continue
        lines = CC.scope_lines(report, st, None if st == "household" else sid)
        liquid = CC.scope_liquid(report, st, None if st == "household" else sid)
        profile = await subject_profile(conn, org_id, st, sid, today)
        label = "The household" if st == "household" else (report.names.get(sid) or "An entity")
        entry = {"scope_type": st, "scope_id": sid, "label": label,
                 "active": None if active is None else {
                     **policy_out(active, names),
                     "derived": render(evaluate(fields_of(active), lines, liquid, reads, profile, today, proxies))}}
        if not member:
            entry["open"] = None if open_ is None else {
                **policy_out(open_, names),
                "derived": render(evaluate(fields_of(open_), lines, liquid, reads, profile, today, proxies)),
                "diff": diff(active, open_),
                "can_submit": can_author and open_["status"] == "draft",
                "can_decide": bool(can_author and open_["status"] == "pending_approval"
                                   and sod.may_check_display(user_id, [str(m) for m in open_["maker_ids"]])),
                "decide_blocked": None if not (can_author and open_["status"] == "pending_approval") else (
                    None if sod.may_check_display(user_id, [str(m) for m in open_["maker_ids"]]) else
                    "You made this version — a different person must decide it")}
            entry["history"] = [policy_out(r, names) for r in mine]
            if can_author:
                base = _as_body(open_ or active) if (open_ or active) else dict(DEFAULTS)
                entry["presets"] = presets_view({**base, "objective": None}, lines, liquid, reads, profile, today,
                                                proxies)
                weights = report.shares if st == "household" else {sid: Decimal(1)}
                sched = sum((ln["amount"] for ln in lines if ln.get("counted", True)
                             and today <= ln["date"] < OB.add_months(today, 12)), ZERO)
                entry["questionnaire"] = questionnaire(None, sched, await trailing_outflows(conn, org_id, weights,
                                                                                            today))
        scopes.append(entry)
    return {"scopes": scopes, "member_view": member, "today": today.isoformat(),
            "override_candidates": [] if member or not can_author else [
                {"entity_id": e, "display_name": report.names.get(e)} for e in ents
                if ("entity", e) not in scope_keys],
            "vocabularies": vocabularies(can_author and not member),
            "actions": ["save_draft", "submit", "decide", "preview"] if can_author and not member else []}


def max_horizon(rows: list[dict], floor: int) -> int:
    return max([floor] + [r["horizon_months"] for r in rows])


# ═══ Writes ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def _audit(conn, org_id, user_id, action: str, resource_id, payload: dict) -> None:
    await conn.execute(
        "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) "
        "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6::jsonb)",
        str(org_id), str(user_id) if user_id else None, f"cash.policy.{action}", "cash_policies", str(resource_id),
        json.dumps(payload, default=str))


async def _publish(pool, org_id, action: str, resource_id, payload: dict, actor_id) -> None:
    from services.domain_events import publish_event

    try:
        await publish_event(pool, str(org_id), f"cash_policy.{action}", EVENT_SOURCE, str(resource_id),
                            json.loads(json.dumps(payload, default=str)),
                            created_by=str(actor_id) if actor_id else None)
    except Exception:  # noqa: BLE001 — the event is a notification; the write stands
        log.exception("cash_policy.%s event not published", action)


async def _archive(conn, row_id) -> None:
    """System-axis: a copy of the live row (system_to = now(), archived_from_id = its id); the caller then updates the
    live row (same id, system_from = now())."""
    got = await conn.fetchval(
        f"""INSERT INTO {T}
            SELECT (jsonb_populate_record(NULL::{T}, to_jsonb(t) || jsonb_build_object(
                'id', gen_random_uuid(), 'archived_from_id', t.id, 'system_to', now()))).*
            FROM {T} t WHERE t.id = $1::uuid AND t.system_to IS NULL RETURNING id""", str(row_id))
    if got is None:
        raise PolicyConflict("the policy is not live — refusing to change it")


async def check_scope(conn, org_id, household_id, scope_type: str, entity_id, reach: set[str]) -> tuple[str, str]:
    """(scope_type, scope_id): the household itself, or an entity this household reaches."""
    if scope_type == "household":
        if entity_id is not None:
            raise PolicyError("a household policy names no entity")
        return "household", str(household_id)
    if scope_type == "entity":
        if entity_id is None or str(entity_id) not in reach:
            raise PolicyError("an entity override names an entity of this household")
        return "entity", str(entity_id)
    raise PolicyError(f"scope is one of {[k for k, _ in SCOPES]}")


async def save_draft(pool, *, org_id, actor_id, household_id, scope_type: str, entity_id, data: dict,
                     reach: set[str], reason: str | None = None, questionnaire_answers: dict | None = None) -> dict:
    """Save a DRAFT for the scope. An open draft is edited (system-axis archive, the editor joins maker_ids — every
    editor is a maker); a version awaiting approval refuses (decide it first); otherwise a NEW version is drafted
    (from scratch, or an edit of the active policy). Never activates anything."""
    fields = normalise(data)
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        st, sid = await check_scope(conn, org_id, household_id, scope_type, entity_id, reach)
        col = "household_id" if st == "household" else "entity_id"
        q = json.dumps(questionnaire_answers, default=str) if questionnaire_answers is not None else None
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"cash_policy:{st}:{sid}")
            open_ = await conn.fetchrow(f"SELECT * FROM {T} WHERE org_id = $1::uuid AND scope_type = $2 AND {col} = "
                                        "$3::uuid AND status IN ('draft', 'pending_approval') AND system_to IS NULL "
                                        "FOR UPDATE", str(org_id), st, sid)
            if open_ is not None and open_["status"] == "pending_approval":
                raise PolicyConflict("a version of this policy is awaiting approval — decide it first")
            aid = uuid.UUID(str(actor["id"]))
            if open_ is not None:
                await _archive(conn, open_["id"])
                makers = list(open_["maker_ids"]) + ([aid] if aid not in open_["maker_ids"] else [])
                row = await conn.fetchrow(
                    f"""UPDATE {T} SET near_months = $2, reserve_months = $3, horizon_months = $4, rung_count = $5,
                            buffer_months = $6, objective = $7, objective_score = $8, preset = $9,
                            min_coverage_months = $10, maker_ids = $11::uuid[], reason = COALESCE($12, reason),
                            questionnaire = COALESCE($13::jsonb, questionnaire), system_from = now()
                        WHERE id = $1 AND system_to IS NULL RETURNING *""",
                    open_["id"], *(fields[k] for k in POLICY_FIELDS), makers, reason, q)
                action = "draft_edited"
            else:
                vno = await conn.fetchval(f"SELECT COALESCE(max(version_no), 0) + 1 FROM {T} WHERE org_id = $1::uuid "
                                          f"AND scope_type = $2 AND {col} = $3::uuid", str(org_id), st, sid)
                row = await conn.fetchrow(
                    f"""INSERT INTO {T} (org_id, scope_type, {col}, version_no, near_months, reserve_months,
                            horizon_months, rung_count, buffer_months, objective, objective_score, preset,
                            min_coverage_months, status, maker_ids, created_by, reason, questionnaire)
                        VALUES ($1::uuid, $2, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, 'draft',
                                ARRAY[$14::uuid], $14::uuid, $15, $16::jsonb) RETURNING *""",
                    str(org_id), st, sid, vno, *(fields[k] for k in POLICY_FIELDS), aid, reason, q)
                action = "draft_created"
            await _audit(conn, org_id, aid, action, row["id"], {"scope_type": st, "scope_id": sid,
                                                               "version_no": row["version_no"], **_as_body(row)})
    await _publish(pool, org_id, action, row["id"], {"scope_type": st, "version_no": row["version_no"]}, actor["id"])
    return policy_out(dict(row))


async def submit(pool, *, org_id, actor_id, policy_id, derived: dict, scope_label: str, household_id=None) -> dict:
    """DRAFT → PENDING_APPROVAL with its maker-checker human task. The submitter is a maker. ``derived`` (the view as
    of submit) is the task's payload: the record of what the checker is asked to approve."""
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        row = await load(conn, org_id, policy_id)
    if row["status"] != "draft":
        raise PolicyConflict(f"the policy is {row['status']}, not a draft")
    aid = uuid.UUID(str(actor["id"]))
    task = await HT.open_task(
        pool, org_id=org_id, kind=TASK, subject_type=SUBJECT, subject_id=row["id"], maker_id=actor["id"],
        eligible_permission=PERMISSION,
        title=f"Approve cash policy v{row['version_no']}: {scope_label} — Near-term {row['near_months']} / Reserve "
              f"{row['reserve_months']} months",
        payload={"scope_type": row["scope_type"], "scope": scope_label, "version_no": row["version_no"],
                 "policy": _as_body(row), "derived_at_submit": derived,
                 # every editor + the submitter: the inbox treats each as a maker (human_tasks.task_makers)
                 "maker_ids": sorted({str(m) for m in row["maker_ids"]} | {str(aid)})},
        action_path=(f"/portfolio/households/{row['household_id'] or household_id}?tab=treasury"
                     if (row["household_id"] or household_id) else None))
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await load(conn, org_id, row["id"], lock=True)
                if cur["status"] != "draft":
                    raise PolicyConflict(f"the policy is {cur['status']}, not a draft")
                await _archive(conn, cur["id"])
                makers = list(cur["maker_ids"]) + ([aid] if aid not in cur["maker_ids"] else [])
                new = await conn.fetchrow(
                    f"UPDATE {T} SET status = 'pending_approval', submitted_by = $2, submitted_at = now(), "
                    "approval_task_id = $3::uuid, maker_ids = $4::uuid[], system_from = now() WHERE id = $1 AND "
                    "system_to IS NULL RETURNING *", cur["id"], aid, task["id"], makers)
                await _audit(conn, org_id, aid, "submitted", cur["id"], {"task_id": task["id"],
                                                                        "version_no": cur["version_no"]})
    except Exception:
        await _withdraw_task(pool, task["id"], "the cash policy could not be submitted")
        raise
    await _publish(pool, org_id, "submitted", new["id"], {"task_id": task["id"]}, actor["id"])
    return {**policy_out(dict(new)), "task": task}


async def _withdraw_task(pool, task_id, reason: str) -> None:
    from services.workflow_engine import cancel_user_task

    try:
        await cancel_user_task(pool, uuid.UUID(str(task_id)), None, reason)
    except Exception:  # noqa: BLE001 — the task may already be decided
        log.info("task %s was not open to withdraw", task_id)


def _refuse(d) -> Exception:
    return MakerCheckerViolation("maker-checker: you drafted, edited or submitted this cash policy, so a different "
                                 "holder of manage_cash_policy must decide it")


async def _precheck(pool, task, outcome, actor_id):
    """Re-checked BEFORE the task commits (it stays open on refusal): the policy is still awaiting approval."""
    async with pool.acquire() as conn:
        row = await load(conn, task["org_id"], task["subject_id"])
    if row["status"] != "pending_approval":
        raise HT.HumanTaskConflict(f"the cash policy is {row['status']}, not awaiting approval")


async def _apply(pool, task: dict, outcome: str, actor_id, note) -> dict | None:
    """The decision lands (idempotent): approve → ACTIVE, the prior active retired; reject → REJECTED."""
    org_id = task["org_id"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await load(conn, org_id, task["subject_id"], lock=True)
            if row["status"] != "pending_approval":
                return policy_out(row)
            checker = uuid.UUID(str(actor_id))
            await sod.gate(conn, org_id=org_id, item_type=sod.CASH_POLICY, checker_id=checker,
                           maker_ids=[str(m) for m in row["maker_ids"]], item_id=str(row["id"]),
                           household_id=row["household_id"], site="cash_policy._apply", refuse=_refuse)
            col = "household_id" if row["scope_type"] == "household" else "entity_id"
            retired = None
            if outcome == "approve":
                prev = await conn.fetchrow(f"SELECT id FROM {T} WHERE org_id = $1::uuid AND scope_type = $2 AND {col} "
                                           "= $3 AND status = 'active' AND system_to IS NULL FOR UPDATE",
                                           str(org_id), row["scope_type"], row[col])
                if prev is not None:
                    await _archive(conn, prev["id"])
                    await conn.execute(
                        f"UPDATE {T} SET status = 'retired', valid_to = now(), retired_at = now(), retired_reason = $2, "
                        "superseded_by = $3, system_from = now() WHERE id = $1 AND system_to IS NULL",
                        prev["id"], f"replaced by version {row['version_no']}", row["id"])
                    retired = str(prev["id"])
                await _archive(conn, row["id"])
                new = await conn.fetchrow(
                    f"UPDATE {T} SET status = 'active', approved_by = $2, approved_at = now(), decision_note = $3, "
                    "valid_from = now(), system_from = now() WHERE id = $1 AND system_to IS NULL RETURNING *",
                    row["id"], checker, note)
            else:
                await _archive(conn, row["id"])
                new = await conn.fetchrow(
                    f"UPDATE {T} SET status = 'rejected', rejected_by = $2, rejected_at = now(), reject_reason = $3, "
                    "system_from = now() WHERE id = $1 AND system_to IS NULL RETURNING *", row["id"], checker, note)
            await _audit(conn, org_id, checker, "approved" if outcome == "approve" else "rejected", row["id"],
                         {"task_id": task["id"], "note": note, "retired": retired, "version_no": row["version_no"]})
    await _publish(pool, org_id, "approved" if outcome == "approve" else "rejected", new["id"],
                   {"task_id": task["id"], "retired": retired}, actor_id)
    return {**policy_out(dict(new)), "retired_policy_id": retired}


async def _on_task_complete(pool, task, outcome, actor_id, note):
    return await _apply(pool, task, outcome, actor_id, note)


HT.register_kind(TASK, label="Cash policy", outcomes=("approve", "reject"), maker_checker=True,
                 on_complete=_on_task_complete)
HT.register_precheck(TASK, _precheck)


async def decide(pool, *, org_id, actor_id, policy_id, outcome: str, note: str | None = None) -> dict:
    """The ONE decision path (the route and bulk approve both call it): a different eligible person (services.sod —
    a refusal is recorded in sod_decisions and audited, nothing changes), then services.human_tasks.complete_task."""
    if outcome not in ("approve", "reject"):
        raise PolicyError("outcome is approve or reject")
    if outcome == "reject" and not (note or "").strip():
        raise PolicyError("a rejection needs a note saying why")
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        row = await load(conn, org_id, policy_id)
    if row["status"] != "pending_approval":
        raise PolicyConflict(f"the cash policy is {row['status']}, not awaiting approval")
    async with pool.acquire() as conn:
        await sod.gate(conn, org_id=org_id, item_type=sod.CASH_POLICY, checker_id=actor["id"],
                       maker_ids=[str(m) for m in row["maker_ids"]], item_id=str(row["id"]),
                       household_id=row["household_id"], site="cash_policy.decide", refuse=_refuse)
    try:
        done = await HT.complete_task(pool, org_id=org_id, task_id=row["approval_task_id"], actor_id=actor["id"],
                                      outcome=outcome, note=(note or "").strip() or None,
                                      sod_household_id=row["household_id"])
    except HT.MakerCheckerError as exc:
        raise MakerCheckerViolation(str(exc)) from exc
    except HT.HumanTaskPermissionError as exc:
        raise PermissionError(str(exc)) from exc
    except HT.HumanTaskConflict as exc:
        raise PolicyConflict(str(exc)) from exc
    result = done.get("subject_result")
    if result is None:
        async with pool.acquire() as conn:
            result = policy_out(await load(conn, org_id, policy_id))
    return result


async def pending_for_bulk(conn, org_id) -> list[dict]:
    """Every policy awaiting approval in the org, with its household (for the bulk-approve list / visibility)."""
    rows = await conn.fetch(
        f"""SELECT p.*, COALESCE(h.id, hm.household_id) AS hh_id, COALESCE(h.name, hm.name) AS household_name,
                   e.display_name AS entity_name
              FROM {T} p
              LEFT JOIN public.households h ON h.id = p.household_id
              LEFT JOIN LATERAL (SELECT m.household_id, hx.name FROM public.household_memberships m
                                   JOIN public.households hx ON hx.id = m.household_id
                                  WHERE m.entity_id = p.entity_id ORDER BY hx.name LIMIT 1) hm ON p.entity_id IS NOT NULL
              LEFT JOIN LATERAL (SELECT display_name FROM public.entities x WHERE x.id = p.entity_id
                                  AND x.system_to IS NULL ORDER BY x.valid_to NULLS FIRST LIMIT 1) e ON true
             WHERE p.org_id = $1::uuid AND p.status = 'pending_approval' AND p.system_to IS NULL
             ORDER BY p.submitted_at""", str(org_id))
    return [dict(r) for r in rows]
