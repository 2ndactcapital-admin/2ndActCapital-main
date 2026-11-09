"""CASH03 — the OBLIGATION-MATCHED LADDER for the Long-term sleeve of an active cash policy. A TARGET plan, never a
trade (proposals and orders are cash04). Derived at read time; nothing is stored (docs/cash/CASH03_NOTES.md D3).

INPUTS: the policy's fields (cash02 — rung_count, the Reserve / Long-term boundary R, the horizon), the cash01
obligation schedule for the policy's scope (``cash_coverage.scope_lines``: counted lines), "today", the AVAILABLE
ISSUES (Treasury bills / notes in the security master, from Treasury Fiscal Data), their auction yields, and the
Treasury curve (FRED, ``portfolio.index_levels``).

THE RULES (docs/cash/CASH_LADDER.md is the reference; every rule is a pure function below):
  1. WINDOW — the Long-term window is cash02's ``windows(...)['long_term']``: months [R, horizon) from today,
     half-open. Only counted obligations dated inside it are laddered.
  2. LARGE — an obligation is LARGE when its amount ≥ 10 % of the Long-term sleeve target (Σ the window's
     obligations), or it is FIRM and ≥ $250,000. Large obligations on the same date share ONE targeted rung.
  3. RUNG COUNT — at most the policy's ``rung_count`` rungs. When there are more large dates than rungs, the largest
     (then earliest) keep their targeted rung; the rest are funded as small obligations (and say so).
  4. FILLERS — the remaining rungs are MONTHLY fillers, on the first day of each Long-term month (month m starts on
     ``cash_obligations.add_months(today, m)``) that holds no targeted rung. With more such months than free rungs,
     the months are spread evenly (indices ⌊i × n ÷ slots⌋, which always includes the window's first month).
  5. MATURITY — on or before the date it funds: the real issue maturing LATEST on or before the target date (and
     within 35 days of it); else an INDICATIVE maturity — the target date moved to the prior business day
     (``us_business_days``), labelled "indicative maturity — no matching issue".
  6. TYPE — the issue's own (bill / note); an indicative rung is a bill up to 364 days, else a note. A muni rung needs
     a muni yield source and none is registered, so no rung is ever a muni.
  7. SIZING — a targeted rung's par = Σ the large obligations it targets. Each SMALL obligation is funded by the rung
     with the latest maturity on or before its date (its par grows); one before every rung is listed as
     "unfunded by the ladder — fund it from Reserve". The LEFTOVER (the sleeve amount − Σ par; the sleeve amount
     defaults to the Long-term target) is spread across the fillers equally to the cent, the odd cents to the
     earliest; no filler → the last rung. A negative leftover is reported as a shortfall, never cut from a rung.
  8. YIELD — a matched issue's own auction yield when that auction is at most 35 days old (Treasury Fiscal Data:
     high yield, or a bill's high investment rate); else the FRED Treasury curve interpolated linearly in months at
     the rung's remaining term (flat beyond the curve's ends, said so). No curve → "not estimated — needs the
     Treasury curve". Every yield carries its basis and as-of.
Deterministic; money and yields in Decimal (yields in percent); the JSON form is text.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from services import cash_obligations as OB
from services import us_business_days as BD

ZERO = Decimal(0)
CENT = Decimal("0.01")
BP = Decimal("0.01")
LARGE_SHARE = Decimal("0.10")
LARGE_FIRM_MIN = Decimal("250000")
MATCH_WINDOW_DAYS = 35
AUCTION_FRESH_DAYS = 35
AUCTION_MAX_AGE_DAYS = 1200     # the read keeps any auction of a live issue; freshness is decided by the rule above
BILL_MAX_DAYS = 364
DAYS_PER_MONTH = Decimal("30.4375")
CURVE_MAX_AGE_DAYS = 10
#: the Treasury curve in portfolio.index_levels (provider fred, marketdata1's nightly): (index key, term in months)
CURVE_POINTS = (("DGS1MO", 1), ("DGS3MO", 3), ("DGS6MO", 6), ("DGS1", 12), ("DGS2", 24), ("DGS5", 60), ("DGS7", 84),
                ("DGS10", 120))
ISSUE_PROVIDER = "treasury_fiscal"
INDICATIVE = "indicative maturity — no matching issue"
NO_CURVE = "not estimated — needs the Treasury curve (FRED DGS1MO–DGS10 in portfolio.index_levels)"
RUNG_KINDS = (("targeted", "Targeted — funds a large obligation"), ("filler", "Filler — monthly"))
INSTRUMENT_TYPES = (("bill", "Treasury bill"), ("note", "Treasury note"), ("muni", "Municipal bond"))
THRESHOLD_TEXT = (f"large = at least {int(LARGE_SHARE * 100)} % of the Long-term sleeve target, or a firm obligation of "
                  f"at least ${LARGE_FIRM_MIN:,.0f}")


# ═══ Pure: terms, the curve, issues ═══════════════════════════════════════════════════════════════════════════
def term_months(today: date, maturity: date) -> Decimal:
    return (Decimal((maturity - today).days) / DAYS_PER_MONTH).quantize(CENT, rounding=ROUND_HALF_UP)


def instrument_type_for(today: date, maturity: date) -> str:
    return "bill" if (maturity - today).days <= BILL_MAX_DAYS else "note"


def curve_yield(curve: list[dict], months: Decimal) -> dict:
    """Linear interpolation in months over ``curve`` ([{key, months, yield_pct, as_of}]); flat beyond its ends."""
    pts = sorted((c for c in curve if c.get("yield_pct") is not None), key=lambda c: c["months"])
    if not pts:
        return {"estimated": False, "text": NO_CURVE}
    as_of = min(p["as_of"] for p in pts)
    keys = [p["key"] for p in pts]
    m = Decimal(months)
    if m <= pts[0]["months"] or len(pts) == 1:
        p = pts[0] if m <= pts[0]["months"] else pts[-1]
        flat = m < p["months"] or (len(pts) == 1 and m != p["months"])
        return {"estimated": True, "yield_pct": p["yield_pct"], "as_of": as_of, "source": "curve",
                "basis": f"FRED Treasury curve at {m} months ({p['key']}{', flat beyond the curve' if flat else ''})",
                "keys": keys}
    if m >= pts[-1]["months"]:
        p = pts[-1]
        return {"estimated": True, "yield_pct": p["yield_pct"], "as_of": as_of, "source": "curve",
                "basis": f"FRED Treasury curve at {m} months ({p['key']}"
                         f"{', flat beyond the curve' if m > p['months'] else ''})", "keys": keys}
    for a, b in zip(pts, pts[1:]):
        if a["months"] <= m <= b["months"]:
            span = Decimal(b["months"] - a["months"])
            y = a["yield_pct"] + (b["yield_pct"] - a["yield_pct"]) * (m - a["months"]) / span
            return {"estimated": True, "yield_pct": y, "as_of": as_of, "source": "curve",
                    "basis": f"FRED Treasury curve interpolated at {m} months ({a['key']}–{b['key']})", "keys": keys}
    return {"estimated": False, "text": NO_CURVE}   # unreachable: the bounds above cover every m


def match_issue(issues: list[dict], target: date, today: date) -> dict | None:
    """The ladder-type issue maturing LATEST on or before ``target``, within MATCH_WINDOW_DAYS of it and after today.
    Ties (same maturity): a bill before a note, then the CUSIP."""
    lo = target - timedelta(days=MATCH_WINDOW_DAYS)
    cands = [i for i in issues if i.get("treasury_type") in ("Bill", "Note") and today < i["maturity"] <= target
             and i["maturity"] >= lo]
    if not cands:
        return None
    return max(cands, key=lambda i: (i["maturity"], i["treasury_type"] == "Bill", "".join(
        chr(255 - ord(c)) for c in i["cusip"])))


def issue_yield(issue: dict, auctions: dict, today: date, curve: list[dict]) -> dict:
    """A matched issue's yield: its own auction (≤ AUCTION_FRESH_DAYS old), else the curve at its remaining term."""
    a = auctions.get(issue["cusip"])
    if a is not None and a.get("found") and (today - a["as_of"]).days <= AUCTION_FRESH_DAYS:
        basis = "auction high investment rate (bond-equivalent)" if issue.get("treasury_type") == "Bill" \
            else "auction high yield"
        return {"estimated": True, "yield_pct": a["yield_pct"], "as_of": a["as_of"], "source": "auction",
                "basis": f"{basis}, auction of {a['as_of'].isoformat()} (Treasury Fiscal Data)",
                "provider": a.get("provider")}
    y = curve_yield(curve, term_months(today, issue["maturity"]))
    if y.get("estimated"):
        why = ("its last auction is more than {} days old".format(AUCTION_FRESH_DAYS) if a is not None and a.get("found")
               else "no auction yield")
        y["basis"] += f" — {why}"
    return y


# ═══ Pure: the threshold, fillers, the ladder ═══════════════════════════════════════════════════════════════════
def is_large(line: dict, lt_target: Decimal) -> tuple[bool, str | None]:
    amt = line["amount"]
    if lt_target > 0 and amt >= lt_target * LARGE_SHARE:
        return True, f"at least {int(LARGE_SHARE * 100)} % of the Long-term target"
    if line.get("certainty") == "firm" and amt >= LARGE_FIRM_MIN:
        return True, f"a firm obligation of at least ${LARGE_FIRM_MIN:,.0f}"
    return False, None


def long_term_window(fields: dict, today: date) -> dict:
    from services import cash_policy as CP

    return next(w for w in CP.windows(fields["near_months"], fields["reserve_months"], fields["horizon_months"], today)
                if w["key"] == "long_term")


def spread_months(cands: list[int], slots: int) -> list[int]:
    if slots <= 0 or not cands:
        return []
    if len(cands) <= slots:
        return list(cands)
    n = len(cands)
    return [cands[(i * n) // slots] for i in range(slots)]


def split_cents(amount: Decimal, n: int) -> list[Decimal]:
    """``amount`` in ``n`` parts to the cent; the odd cents go to the first parts."""
    if n <= 0:
        return []
    each = (amount / n).quantize(CENT, rounding=ROUND_DOWN)
    rest = int(((amount - each * n) / CENT).to_integral_value())
    return [each + (CENT if i < rest else ZERO) for i in range(n)]


def build_ladder(fields: dict, lines: list[dict], today: date, *, issues: list[dict] | None = None,
                 auctions: dict | None = None, curve: list[dict] | None = None,
                 sleeve_amount: Decimal | None = None) -> dict:
    """The ladder for one policy version. ``lines``: [{key, date, amount, counted, certainty, name, source_type}].
    ``issues``: [{cusip, security_id, name, treasury_type, maturity}]; ``auctions``: {cusip: {found, yield_pct,
    as_of}}; ``curve``: [{key, months, yield_pct, as_of}]. Pure, Decimal, deterministic."""
    issues, auctions, curve = list(issues or []), dict(auctions or {}), list(curve or [])
    w = long_term_window(fields, today)
    k = int(fields["rung_count"])
    out = {"window": w, "rung_count": k, "rungs": [], "obligations": [], "unfunded": [], "notes": [],
           "threshold": THRESHOLD_TEXT, "today": today}
    lt = sorted((ln for ln in lines if ln.get("counted", True) and w["from"] <= ln["date"] < w["until"]),
                key=lambda ln: (ln["date"], ln["key"]))
    target = sum((ln["amount"] for ln in lt), ZERO)
    amount = target if sleeve_amount is None else Decimal(sleeve_amount)
    out |= {"target": target, "sleeve_amount": amount}
    if w["months"] <= 0:
        out["notes"].append(w["note"] or "the Long-term window is empty — no ladder")
        return _finish(out)
    large: dict[date, list[dict]] = {}
    flags = {}
    for ln in lt:
        big, why = is_large(ln, target)
        flags[ln["key"]] = why
        if big:
            large.setdefault(ln["date"], []).append(ln)
    groups = sorted(large.items(), key=lambda kv: (-sum((x["amount"] for x in kv[1]), ZERO), kv[0]))
    kept, demoted = groups[:k], groups[k:]
    for d, grp in demoted:
        for ln in grp:
            flags[ln["key"]] = None
            out["notes"].append(f"{ln['name'] or ln['key']} ({d.isoformat()}) is large but the {k} rungs are taken by "
                                "larger obligations — funded as a small obligation")
    targeted_dates = sorted(d for d, _g in kept)
    month_of = {m: (OB.add_months(today, m), OB.add_months(today, m + 1)) for m in range(w["start_month"], w["end_month"])}
    free = [m for m, (a, b) in month_of.items() if not any(a <= d < b for d in targeted_dates)]
    filler_months = spread_months(free, k - len(kept))
    rungs = []
    for d in targeted_dates:
        grp = dict(kept)[d]
        rungs.append({"kind": "targeted", "target_date": d, "targets": [ln["key"] for ln in grp],
                      "par": sum((ln["amount"] for ln in grp), ZERO)})
    for m in filler_months:
        rungs.append({"kind": "filler", "target_date": month_of[m][0], "month": m, "targets": [], "par": ZERO})
    for r in rungs:
        issue = match_issue(issues, r["target_date"], today)
        if issue is not None:
            r |= {"maturity": issue["maturity"], "matched": True, "indicative": False, "cusip": issue["cusip"],
                  "security_id": issue.get("security_id"), "issue_name": issue.get("name"),
                  "instrument_type": "bill" if issue["treasury_type"] == "Bill" else "note",
                  "label": None, "adjustment": None, "yield": issue_yield(issue, auctions, today, curve)}
        else:
            mat = BD.prior_business_day(r["target_date"])
            r |= {"maturity": mat, "matched": False, "indicative": True, "cusip": None, "security_id": None,
                  "issue_name": None, "instrument_type": instrument_type_for(today, mat), "label": INDICATIVE,
                  "adjustment": BD.adjustment_note(r["target_date"], mat),
                  "yield": curve_yield(curve, term_months(today, mat))}
    rungs.sort(key=lambda r: (r["maturity"], r["kind"] != "targeted", r["target_date"]))
    targeted_keys = {key for r in rungs for key in r["targets"]}
    for r in rungs:
        r["funds"] = []
    for ln in lt:
        if ln["key"] in targeted_keys:
            continue
        home = [r for r in rungs if r["maturity"] <= ln["date"]]
        if not home:
            out["unfunded"].append({"key": ln["key"], "date": ln["date"], "amount": ln["amount"], "name": ln.get("name"),
                                    "text": "unfunded by the ladder — no rung matures on or before it; fund it from "
                                            "Reserve"})
            continue
        r = home[-1]
        r["funds"].append(ln["key"])
        r["par"] += ln["amount"]
    leftover = amount - sum((r["par"] for r in rungs), ZERO) - sum((u["amount"] for u in out["unfunded"]), ZERO)
    fillers = [r for r in rungs if r["kind"] == "filler"]
    for r in rungs:
        r["leftover"] = ZERO
    if leftover > 0 and rungs:
        homes = fillers or [rungs[-1]]
        for r, part in zip(homes, split_cents(leftover, len(homes))):
            r["leftover"] = part
            r["par"] += part
        if not fillers:
            out["notes"].append("no filler rung — the leftover sleeve amount is added to the last rung")
    elif leftover < 0:
        out["notes"].append(f"short by {OB.money_text(-leftover)} — the sleeve amount does not cover its obligations")
    out |= {"leftover": leftover}
    by_rung = {}
    for i, r in enumerate(rungs, start=1):
        r["index"] = i
        for key in r["targets"] + r["funds"]:
            by_rung[key] = i
    out["rungs"] = rungs
    out["obligations"] = [{"key": ln["key"], "date": ln["date"], "amount": ln["amount"], "name": ln.get("name"),
                           "certainty": ln.get("certainty"), "source_type": ln.get("source_type"),
                           "large": flags.get(ln["key"]) is not None, "large_reason": flags.get(ln["key"]),
                           "rung": by_rung.get(ln["key"])} for ln in lt]
    return _finish(out)


def _finish(out: dict) -> dict:
    rungs = out["rungs"]
    out["par_total"] = sum((r["par"] for r in rungs), ZERO)
    est = [r for r in rungs if r["yield"].get("estimated")]
    if rungs and len(est) == len(rungs) and out["par_total"] > 0:
        out["yield"] = {"estimated": True, "yield_pct": sum((r["par"] * r["yield"]["yield_pct"] for r in rungs), ZERO)
                        / out["par_total"], "as_of": min(r["yield"]["as_of"] for r in rungs),
                        "basis": "par-weighted rung yields", "source": "ladder"}
    elif not rungs:
        out["yield"] = {"estimated": False, "text": "no ladder — no Long-term obligations or rungs"}
    else:
        miss = [r for r in rungs if not r["yield"].get("estimated")]
        out["yield"] = {"estimated": False, "text": f"not estimated — {len(miss)} of {len(rungs)} rungs have no yield "
                                                    f"({miss[0]['yield'].get('text')})"}
    out["indicative_count"] = sum(1 for r in rungs if r.get("indicative"))
    return out


# ═══ JSON ═════════════════════════════════════════════════════════════════════════════════════════════════════
def pct_text(v: Decimal | None) -> str | None:
    return None if v is None else format(Decimal(v).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), "f")


def yield_json(y: dict | None, *, member: bool = False) -> dict:
    y = y or {}
    if not y.get("estimated"):
        return {"estimated": False, "text": y.get("text") or "not estimated"}
    out = {"estimated": True, "yield_pct": pct_text(y["yield_pct"]), "basis": y.get("basis"),
           "as_of": y["as_of"].isoformat() if y.get("as_of") else None, "source": y.get("source")}
    out["label"] = f"{y.get('basis')}, as of {out['as_of']}" if out["as_of"] else y.get("basis")
    return out


def render(ladder: dict, names: dict | None = None, *, member: bool = False) -> dict:
    names = names or {}
    w = ladder["window"]
    return {
        "window": {"start_month": w["start_month"], "end_month": w["end_month"], "months": w["months"],
                   "from": w["from"].isoformat(), "until": w["until"].isoformat(), "note": w.get("note")},
        "rung_count": ladder["rung_count"], "threshold": ladder["threshold"],
        "target": OB.money_text(ladder["target"]), "sleeve_amount": OB.money_text(ladder["sleeve_amount"]),
        "par_total": OB.money_text(ladder["par_total"]),
        "leftover": OB.money_text(ladder.get("leftover", ZERO)),
        "yield": yield_json(ladder["yield"]), "indicative_count": ladder["indicative_count"],
        "rungs": [{"index": r["index"], "kind": r["kind"], "target_date": r["target_date"].isoformat(),
                   "maturity": r["maturity"].isoformat(), "instrument_type": r["instrument_type"],
                   "matched": r["matched"], "indicative": r["indicative"], "label": r["label"],
                   "adjustment": r["adjustment"],
                   "cusip": None if member else r.get("cusip"), "security_id": r.get("security_id"),
                   "issue_name": r.get("issue_name"),
                   "par": OB.money_text(r["par"]), "leftover": OB.money_text(r["leftover"]),
                   "targets": r["targets"], "funds": r["funds"],
                   "target_names": [names.get(k) for k in r["targets"]],
                   "yield": yield_json(r["yield"])} for r in ladder["rungs"]],
        "obligations": [{**{k: v for k, v in o.items() if k not in ("date", "amount")},
                         "date": o["date"].isoformat(), "amount": OB.money_text(o["amount"])}
                        for o in ladder["obligations"]],
        "unfunded": [{**u, "date": u["date"].isoformat(), "amount": OB.money_text(u["amount"])}
                     for u in ladder["unfunded"]],
        "notes": ladder["notes"], "as_of": ladder["today"].isoformat(),
    }


def fingerprint(rendered: dict) -> str:
    """A stable hash of a rendered plan (the audit row of a regeneration names it)."""
    return hashlib.sha256(json.dumps(rendered, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ═══ Reads (the request connection; RLS context already set) ════════════════════════════════════════════════════
async def read_curve(conn, org_id, today: date, *, purpose: str = "internal", points=None,
                     providers: dict | None = None) -> tuple[list[dict], list[str]]:
    """The Treasury curve through THE read path (security_data.layer — rank, licence, staleness)."""
    from services.security_data import layer as L

    pts = list(points or CURVE_POINTS)
    got = await L.get_bulk(conn, org_id, "index_level", [k for k, _m in pts], as_of=today, purpose=purpose,
                           max_age_days=CURVE_MAX_AGE_DAYS, providers=providers)
    curve, missing = [], []
    for key, months in pts:
        r = got[key]
        if r.found:
            curve.append({"key": key, "months": months, "yield_pct": Decimal(r.value), "as_of": r.as_of,
                          "provider": r.provider_key})
        else:
            missing.append(f"{key}: {r.missing}")
    return curve, missing


async def available_issues(conn, org_id, today: date, until: date, *, purpose: str = "internal",
                           cusip_like: str | None = None) -> tuple[list[dict], str | None]:
    """Treasury bills / notes in the security master maturing in (today, until], from the treasury_fiscal provider —
    or ([], why) when that provider may not be used (not connected, licence) for this purpose. ``cusip_like``
    narrows the read (tests: their fixture prefix)."""
    from services.security_data import registry as R
    from services.security_data.types import ReadContext

    rows = await R.providers(conn)
    why = R.source_allowed(rows.get(ISSUE_PROVIDER), ReadContext(str(org_id), purpose))
    if why is not None:
        return [], f"Treasury issues ({ISSUE_PROVIDER}): {why}"
    recs = await conn.fetch(
        "SELECT g.id::text AS security_id, g.name, i.id_value AS cusip, t.value AS treasury_type, m.value AS maturity "
        "FROM portfolio.security_reference_attributes m "
        "JOIN portfolio.security_reference_attributes t ON t.global_security_id = m.global_security_id "
        " AND t.field = 'treasury_type' AND t.provider_key = $1 AND t.valid_to IS NULL AND t.system_to IS NULL "
        "JOIN portfolio.securities_global g ON g.id = m.global_security_id AND g.valid_to IS NULL "
        " AND g.system_to IS NULL AND g.merged_into_id IS NULL "
        "JOIN portfolio.securities_global_identifiers i ON i.global_security_id = g.id AND i.id_type = 'cusip' "
        " AND i.valid_to IS NULL AND i.system_to IS NULL "
        "WHERE m.field = 'maturity_date' AND m.provider_key = $1 AND m.valid_to IS NULL AND m.system_to IS NULL "
        "AND t.value = ANY($2::text[]) AND m.value > $3::text AND m.value <= $4::text "
        "AND ($5::text IS NULL OR i.id_value LIKE $5::text) ORDER BY m.value, i.id_value",
        ISSUE_PROVIDER, ["Bill", "Note"], today.isoformat(), until.isoformat(), cusip_like)
    return [{"security_id": r["security_id"], "name": r["name"], "cusip": r["cusip"],
             "treasury_type": r["treasury_type"], "maturity": date.fromisoformat(r["maturity"])} for r in recs], None


async def read_auctions(conn, org_id, cusips: list[str], today: date, *, purpose: str = "internal",
                        providers: dict | None = None) -> dict:
    """{cusip: {found, yield_pct, as_of, provider, missing}} — each issue's latest auction yield (rank walk)."""
    from services.market_data import treasury_fiscal as TF
    from services.security_data import layer as L

    if not cusips:
        return {}
    got = await L.get_bulk(conn, org_id, "index_level", [TF.index_key(c) for c in cusips], as_of=today,
                           purpose=purpose, max_age_days=AUCTION_MAX_AGE_DAYS, providers=providers)
    out = {}
    for c in cusips:
        r = got[TF.index_key(c)]
        out[c] = ({"found": True, "yield_pct": Decimal(r.value), "as_of": r.as_of, "provider": r.provider_key}
                  if r.found else {"found": False, "missing": r.missing})
    return out
