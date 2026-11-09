"""CASH05 — Treasury → OVERVIEW: the liquid balance over time with transfer markers, the blended net and after-tax
yield with its method note, and cash01's coverage card + per-entity table (passed through, never recomputed).
Read-only. Deterministic for a fixed "today".

BALANCE SERIES. The liquid balance on day D = THE net-worth core evaluated AS OF D (``networth.load(as_of=D)`` →
``figures``), narrowed to the liquid rows (``cash_holdings.is_liquid``): cash01's Cash & Equivalents + the sleeve
instruments, each position once, the household at its look-through shares (an entity filter: that entity's own at 1).
A position is a statement as of its date, so the core's answer on D is the latest statement on or before D.
The chart's POINTS are core evaluations on: every day of the range when it spans ≤ ``MAX_POINTS`` days, else every
k-th day (k = ⌈days ÷ (MAX_POINTS − 1)⌉), ALWAYS plus the range's first day, today, every transfer-marker date and
every date a liquid position was restated in the range — so no step in the balance is skipped. Between points the
balance is drawn as a step (the core's own semantics).

RANGES: 30d / 90d (today − 30 / 90 days), 6m / 12m (cash01's ``add_months``, clamped), YTD (1 January), all (the first
liquid statement of the scope). Both ends are inclusive.

TOOLTIP per point: the value, PENDING IN / PENDING OUT = Σ positive / negative changes of the activity entries traded
on or before that day and settling after it (``cash_activity``).

BLENDED YIELD (v1 §5, each holding on its own basis — cash03): Σ value × yield ÷ Σ value over the holdings that have a
yield (the rest listed, never guessed) − the advisory fee's annual rate on cash (``cash_earnings.fee_rate``; "not
charged" when the schedule excludes cash). AFTER-TAX where an approved household tax profile exists: each position at
its ACCOUNT's treatment (tax-deferred / exempt → no tax now) and the instrument's exemptions (``cash_instruments``).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from services import cash_activity as CA
from services import cash_holdings as CH
from services import cash_obligations as OB

ZERO = Decimal(0)
ONE = Decimal(1)
RANGES = (("30d", "30 days"), ("90d", "90 days"), ("6m", "6 months"), ("ytd", "Year to date"), ("12m", "12 months"),
          ("all", "All"))
DEFAULT_RANGE = "90d"
MAX_POINTS = 120
METHOD_NOTE = ("Blended net yield = Σ (holding value × its yield) ÷ the value of the holdings with a yield − the "
               "advisory fee's annual rate on cash. Each holding on its own basis: a money-market fund's 7-day net "
               "yield (SEC Form N-MFP), a bill's yield to maturity from the account's own valuation, a note's auction "
               "yield. Holdings without a yield are listed, not guessed.")
PRIME_KIND = "government_mmf"     # a prime fund is fully taxed: the same exemptions as a government fund (none applied)

LIQUID_STATEMENTS_SQL = """
SELECT p.as_of_date, p.asset_id::text AS asset_id, COALESCE(p.taxonomy_key, a.default_taxonomy_key) AS taxonomy_key
  FROM portfolio.positions p
  JOIN portfolio.assets a ON a.id = p.asset_id AND a.org_id = p.org_id AND a.valid_to IS NULL AND a.system_to IS NULL
 WHERE p.org_id = $1::uuid AND p.owner_entity_id = ANY($2::uuid[]) AND p.valid_to IS NULL AND p.system_to IS NULL
   AND p.superseded_by_source IS NULL AND p.as_of_date <= $3::date
"""


class RangeError(ValueError):
    pass


def range_start(key: str, today: date, earliest: date | None) -> date:
    k = (key or DEFAULT_RANGE).lower()
    if k == "30d":
        return today - timedelta(days=30)
    if k == "90d":
        return today - timedelta(days=90)
    if k == "6m":
        return OB.add_months(today, -6)
    if k == "12m":
        return OB.add_months(today, -12)
    if k == "ytd":
        return date(today.year, 1, 1)
    if k == "all":
        return min(earliest, today) if earliest else today
    raise RangeError(f"unknown range {key!r}; one of {[r for r, _ in RANGES]}")


def sample_days(start: date, today: date, extra) -> list[date]:
    """The core-evaluation days (pure; see the module doc)."""
    span = (today - start).days
    step = 1 if span < MAX_POINTS else -(-span // (MAX_POINTS - 1))
    days = {start + timedelta(days=i) for i in range(0, span + 1, step)} | {start, today}
    days |= {d for d in extra if start <= d <= today}
    return sorted(days)


def pending_on(entries: list[dict], d: date) -> tuple[Decimal, Decimal]:
    """(pending in, pending out) on day ``d``: entries traded on / before d that settle after d. Pure."""
    pin = pout = ZERO
    for e in entries:
        if e["change"] is None or e["settle_date"] is None:
            continue
        if e["date"] <= d < e["settle_date"]:
            if e["change"] > ZERO:
                pin += e["change"]
            else:
                pout += -e["change"]
    return pin, pout


def markers(entries: list[dict], start: date, today: date) -> list[dict]:
    return [{"id": e["id"], "date": e["date"].isoformat(), "change": CH.money(e["change"]),
             "direction": "in" if (e["change"] or ZERO) >= ZERO else "out", "description": e["description"],
             "pending": e["pending"]}
            for e in sorted(entries, key=lambda x: (x["date"], x["id"]))
            if e["type"] == "transfer" and start <= e["date"] <= today and e["change"] is not None]


def tax_kind(h: dict) -> str | None:
    from services import cash_instruments as CI

    if h["kind"] == "mmf":
        return CI.MMF_CATEGORY_KIND.get(h.get("mmf_category") or "", PRIME_KIND)
    if h["kind"] == "bill":
        return "bill"
    if h["kind"] == "note":
        return "treasury_ladder"
    return None


def blend(holdings: list[dict], fee: dict, *, rates: dict | None, treatments: dict) -> dict:
    """The blended net and after-tax yield. Pure. ``treatments``: {account_id: cash_instruments.treatment(...)}."""
    from services import cash_instruments as CI

    with_y = [h for h in holdings if h["yield"].get("estimated") and h["value"] > ZERO]
    without = [{"name": h["name"], "value": CH.money(h["value"]), "why": h["yield"].get("text")}
               for h in holdings if not (h["yield"].get("estimated") and h["value"] > ZERO) and h["value"] > ZERO]
    covered = sum((h["value"] for h in with_y), ZERO)
    total = sum((h["value"] for h in holdings if h["value"] > ZERO), ZERO)
    if covered <= ZERO:
        return {"estimated": False, "text": "not estimated — no holding in this scope has a yield",
                "without_yield": without, "covered": CH.money(covered), "total": CH.money(total)}
    gross = sum((h["value"] * h["yield"]["yield_pct"] for h in with_y), ZERO) / covered
    fee_ok = bool(fee.get("estimated"))
    fee_pct = fee["rate_pct"] if fee_ok else ZERO
    net = gross - fee_pct
    out = {"estimated": True, "gross_yield_pct": CH.pct(gross), "fee_pct": CH.pct(fee_pct) if fee_ok else None,
           "fee_text": fee.get("text"), "net_yield_pct": CH.pct(net),
           "fee_deducted": fee_ok, "covered": CH.money(covered), "total": CH.money(total), "without_yield": without,
           "method": METHOD_NOTE}
    if rates is None:
        out["after_tax"] = {"estimated": False, "text": CI.NEEDS_PROFILE}
        return out
    acc = ZERO
    for h in with_y:
        k = tax_kind(h)
        for p in h["positions"]:
            t = treatments.get(p["account_id"]) or {"tax_applies": True, "label": "no account — treated as taxable"}
            r = CI.applicable_rate(k, rates) if (k and t["tax_applies"]) else ZERO
            acc += p["value"] * h["yield"]["yield_pct"] * (ONE - r)
    at = acc / covered - fee_pct
    out["after_tax"] = {"estimated": True, "yield_pct": CH.pct(at),
                        "label": f"after-tax estimate — each holding at its account's treatment and its instrument's "
                                 f"exemptions (tax profile {rates['tax_year']}, as of {rates['as_of']}); the fee is "
                                 "deducted after tax"}
    return out


async def series(conn, scope: CH.Scope, assets: dict, start: date, today: date, extra) -> list[dict]:
    from services import networth as NW

    roots = sorted(scope.members) if scope.entity_id is None else [scope.entity_id]
    keep = CH.keep_for(assets)
    out = []
    for d in sample_days(start, today, extra):
        ctx = await NW.load(conn, scope.org_id, roots, as_of=d, keep=keep, edges=scope.edges)
        out.append({"date": d, "value": NW.figures(ctx, weights=scope.weights)["gross"]})
    return out


async def build(conn, scope: CH.Scope, today: date, *, range_key: str, purpose: str, report, account_ids: list[str],
                cur: dict | None = None) -> dict:
    """The overview (Decimal). ``report`` is cash01's household report (the coverage card's ONE source)."""
    from services import cash_earnings as CE
    from services import cash_instruments as CI
    from services import tax_profiles as TP

    cur = cur or await CH.current(conn, scope, today, purpose=purpose)
    assets = cur["assets"]
    rows = [dict(r) for r in await conn.fetch(LIQUID_STATEMENTS_SQL, scope.org_id, scope.owners, today)] \
        if scope.owners else []
    liquid_rows = [r for r in rows if CH.is_liquid(r, assets)]
    earliest = min((r["as_of_date"] for r in liquid_rows), default=None)
    start = range_start(range_key, today, earliest)
    got = await CA.ledger(conn, scope, today, start=start - timedelta(days=31), end=today, assets=assets)
    ents = got["entries"]
    mk = markers(ents, start, today)
    restated = {r["as_of_date"] for r in liquid_rows if start <= r["as_of_date"] <= today}
    pts = await series(conn, scope, assets, start, today, restated | {date.fromisoformat(m["date"]) for m in mk})
    points = []
    for p in pts:
        pin, pout = pending_on(ents, p["date"])
        points.append({"date": p["date"].isoformat(), "value": CH.money(p["value"]), "pending_in": CH.money(pin),
                       "pending_out": CH.money(pout)})
    fee = await CE.fee_rate(conn, scope.org_id, account_ids, today)
    prof = await TP.approved_profile(conn, scope.org_id, "household", scope.household_id, today.year)
    rates = CI.rates(prof)
    acct_ids = sorted({p["account_id"] for h in cur["holdings"] for p in h["positions"] if p["account_id"]})
    treat = {}
    if acct_ids:
        for r in await conn.fetch("SELECT id::text AS id, tax_status, registration_type FROM public.accounts WHERE "
                                  "id = ANY($1::uuid[]) AND org_id = $2::uuid", acct_ids, scope.org_id):
            treat[r["id"]] = CI.treatment(dict(r))
    yields = blend(cur["holdings"], fee, rates=rates, treatments=treat)
    if scope.entity_id is None:
        cov = report.household
    else:
        cov = next((e for e in report.entities if e["entity_id"] == scope.entity_id), None)
    first, last = (pts[0]["value"], pts[-1]["value"]) if pts else (ZERO, ZERO)
    return {"range": {"key": (range_key or DEFAULT_RANGE).lower(), "start": start.isoformat(), "end": today.isoformat()},
            "points": points, "markers": mk, "balance": CH.money(cur["total"]),
            "change_in_range": CH.money(last - first), "yields": yields, "coverage": cov,
            "coverage_entities": report.entities if scope.entity_id is None else [],
            "series_basis": ("the net-worth core as of each day, liquid rows only (Cash & Equivalents + the sleeve "
                             "instruments), the household at its look-through shares"
                             if scope.entity_id is None else
                             "the net-worth core as of each day, this entity's own liquid positions"),
            "points_basis": (f"{len(points)} core evaluations: every day up to {MAX_POINTS} days, else every k-th day, "
                             "plus the range's first day, today, each transfer date and each restatement date"),
            "pending_basis": "activity entries traded on or before the day that settle after it"}


def vocabularies() -> dict:
    return {"ranges": [{"key": k, "label": v} for k, v in RANGES], "default_range": DEFAULT_RANGE,
            "editable": [], "inline_editable": []}


def q(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
