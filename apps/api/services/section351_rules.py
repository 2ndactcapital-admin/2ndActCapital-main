"""REP14 — the Section 351 exit: the PURE rules (no database, no network, Decimal everywhere, deterministic).

Every result is an ESTIMATE and carries the label "estimate — confirm with counsel". The readings below are ours,
stated so counsel can check each one (docs/REPLICATION_STUDIO.md, rep14 section, lists them as open items).

THE INVESTMENT-COMPANY EXCEPTION — :func:`diversification_test`
──────────────────────────────────────────────────────────────────────────────
IRC §351(e)(1) denies §351 to a transfer to an investment company. Treas. Reg. §1.351-1(c)(1) makes a transfer one
"to an investment company" only if it results in diversification of the transferors' interests; under
§1.351-1(c)(6)(i) a transferor's portfolio that is ALREADY diversified is not diversified by the transfer. A
portfolio is diversified when it meets the 25 % and 50 % tests of §368(a)(2)(F)(ii):
  * no more than 25 % of the value of the (contributed) total assets in the stock and securities of any ONE issuer;
  * no more than 50 % in the stock and securities of FIVE OR FEWER issuers.
Our readings (each flagged for counsel):
  1. The test runs on EACH transferor's contribution separately (per account here — one account = one transferor's
     contribution; counsel: two accounts of one person in one plan are tested separately, not combined).
  2. Government securities are INCLUDED in total assets (the denominator) but are NOT the securities of an issuer
     (never in the numerator) — §1.351-1(c)(6)(i), second sentence.
  3. Cash and cash items are EXCLUDED from total assets — §368(a)(2)(F)(iv). With the sponsor's default cash rule
     ("not contributed — stays in the account") the cash is not part of the contribution at all.
  4. Stock of a regulated investment company (an ETF or mutual fund) is LOOKED THROUGH to its proportionate share
     of the fund's assets — §368(a)(2)(F) (the RIC / REIT look-through; counsel to confirm the clause). A name held
     directly AND inside a fund is one issuer: the two amounts are summed. A fund with no holdings data FAILS
     CLOSED ("holdings needed"); a fund holding whose constituent is not resolved to the master FAILS CLOSED too.
  5. Two share classes of one issuer are ONE issuer (the master's issuer key: the SEC CIK reference attribute; a
     security with no CIK is its own issuer). Members of a controlled group (§1563(a)) are "one issuer" in the
     statute; the master does not link parents and subsidiaries, so that grouping is an OPEN ITEM for counsel.
  6. A fund's constituent rows that are neither an equity nor cash (futures, "other") stay in the denominator and
     are not an issuer (stated; counsel to confirm).
  7. Limits are compared EXACTLY (no rounding before the comparison): 25.00 % passes, 25.01 % fails.

CONTROL — :func:`control_test`
──────────────────────────────────────────────────────────────────────────────
§351(a) with §368(c): the transferors together own at least 80 % of the ETF immediately after the exchange.
Reading: contributors' share = Σ (contributed value − cash paid to the contributor) ÷ (that + every non-contributor
seed). 80.00 % passes, 79.99 % fails.

CARRYOVER BASIS AND TACKED HOLDING PERIOD — :func:`carryover`
──────────────────────────────────────────────────────────────────────────────
§358(a)(1): the contributor's ETF shares take the basis of the property transferred, DECREASED by boot received and
INCREASED by gain recognized. §1223(1): their holding period includes the contributed property's. §362(a) / §1223(2)
give the fund the same lot basis and dates. Per-lot reading (Rev. Rul. 85-164 split holding periods; Rev. Rul. 68-55
asset-by-asset boot): the ETF shares are split across the contributed lots by each lot's share of the fair market
value; each slice keeps its lot's basis and acquisition date. BOOT (cash or other property received) is TAXABLE:
it is allocated to the lots by fair market value and recognizes gain up to each lot's own gain; a loss is never
recognized (§351(b)(2)). The plan receives ETF shares only by default (boot 0); any cash leg is shown as taxable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext

ZERO = Decimal(0)
ONE = Decimal(1)
CENT = Decimal("0.01")
Q = Decimal("0.0000000001")
QTY = Decimal("0.00000001")
MAX_ONE = Decimal("0.25")
MAX_FIVE = Decimal("0.50")
CONTROL_MIN = Decimal("0.80")
LABEL = "estimate — confirm with counsel"

#: what a line of the contribution is (the test treats each differently)
KIND_ISSUER = "issuer"            # stock / securities of an issuer (numerator and denominator)
KIND_GOVERNMENT = "government"    # denominator only (reading 2)
KIND_OTHER = "other"              # a fund's non-equity, non-cash row: denominator only (reading 6)
KIND_CASH = "cash"                # excluded from total assets (reading 3)

VERDICT_LABELS = {"pass": "Passes — estimate, confirm with counsel", "fail": "Fails",
                  "holdings_needed": "Cannot be tested — data needed (fails closed)", "not_run": "Not tested yet"}


class Section351RuleError(ValueError):
    """A request the pure rules refuse."""


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_EVEN)


def qw(v: Decimal) -> Decimal:
    return v.quantize(Q, rounding=ROUND_HALF_EVEN)


def pct_text(w: Decimal) -> str:
    """A fraction as a percent with two decimals (display only — comparisons use the exact fraction)."""
    return f"{(w * 100).quantize(CENT, rounding=ROUND_HALF_EVEN)}%"


@dataclass
class Line:
    """One piece of the contribution as the test sees it."""
    security_id: str
    label: str
    value: Decimal
    kind: str = KIND_ISSUER
    issuer_key: str | None = None
    issuer_label: str | None = None
    via: str = "direct"                 # 'direct' | 'look_through'
    fund_id: str | None = None
    fund_label: str | None = None
    holdings_as_of: date | None = None
    valuation_source: str | None = None
    valuation_as_of: date | None = None
    lot_ids: list[str] = field(default_factory=list)


# ═══ Look-through ═══════════════════════════════════════════════════════════════════════════════════════════════════
def look_through(*, fund_id: str, fund_label: str, fund_value: Decimal, snapshot: dict | None, rows: list[dict],
                 issuers: dict[str, dict], valuation_source: str | None = None,
                 valuation_as_of: date | None = None) -> tuple[list[Line], str | None]:
    """A fund's value spread over its constituents by the snapshot's weights (normalised to the snapshot's total so
    the whole fund value is allocated — stated). ``rows`` = replica_holdings_rows (asset_type, resolution_state,
    constituent_global_security_id, weight, raw_name / raw_ticker). ``issuers`` = {security_id: {issuer_key, label,
    government}}. Returns (lines, None) or ([], the fail-closed reason)."""
    if snapshot is None:
        return [], f"{fund_label}: holdings needed — no holdings data on record for this fund (fails closed)"
    as_of = snapshot.get("as_of")
    usable = [r for r in rows if r.get("weight") is not None and Decimal(str(r["weight"])) != 0]
    if not usable:
        return [], f"{fund_label}: holdings needed — the holdings snapshot as of {as_of} has no weights (fails closed)"
    unresolved = [r for r in usable if r["asset_type"] == "equity" and r["resolution_state"] != "resolved"]
    if unresolved:
        names = ", ".join(sorted({(r.get("raw_ticker") or r.get("raw_name") or "?") for r in unresolved})[:5])
        return [], (f"{fund_label}: holdings needed — {len(unresolved)} constituent row(s) are not resolved to an "
                    f"issuer ({names}…) in the snapshot as of {as_of} (fails closed)")
    with localcontext() as ctx:
        ctx.prec = 60
        total_w = sum((Decimal(str(r["weight"])) for r in usable), ZERO)
        if total_w <= 0:
            return [], f"{fund_label}: holdings needed — the snapshot's weights do not sum above zero (fails closed)"
        out = []
        for r in sorted(usable, key=lambda x: (x.get("row_num") or 0)):
            w = Decimal(str(r["weight"]))
            v = fund_value * w / total_w
            if r["asset_type"] == "cash":
                kind, key, ilabel, sid = KIND_CASH, None, None, f"cash@{fund_id}"
            elif r["asset_type"] == "equity":
                sid = str(r["constituent_global_security_id"])
                facts = issuers.get(sid) or {}
                if facts.get("government"):
                    kind, key, ilabel = KIND_GOVERNMENT, None, None
                else:
                    kind, key, ilabel = KIND_ISSUER, facts.get("issuer_key") or f"security:{sid}", \
                        facts.get("issuer_label") or facts.get("label") or (r.get("raw_name") or sid)
            else:
                kind, key, ilabel = KIND_OTHER, None, None
                sid = str(r.get("constituent_global_security_id") or f"{r['asset_type']}@{fund_id}#{r.get('row_num')}")
            out.append(Line(security_id=sid, label=(issuers.get(sid) or {}).get("label") or r.get("raw_name") or sid,
                            value=v, kind=kind, issuer_key=key, issuer_label=ilabel, via="look_through",
                            fund_id=fund_id, fund_label=fund_label, holdings_as_of=as_of,
                            valuation_source=valuation_source, valuation_as_of=valuation_as_of))
    return out, None


# ═══ The 25 % / 50 % test ═══════════════════════════════════════════════════════════════════════════════════════════
def diversification_test(lines: list[Line], *, blocked: list[str] | None = None, max_one: Decimal = MAX_ONE,
                         max_five: Decimal = MAX_FIVE) -> dict:
    """Per issuer, its share of the contributed total assets; PASS when no issuer > 25 % AND the five largest
    together ≤ 50 %. ``blocked`` = fail-closed reasons (a fund without holdings, an unpriced holding): any → the
    verdict is 'holdings_needed' whatever the numbers say."""
    blocked = list(blocked or [])
    with localcontext() as ctx:
        ctx.prec = 60
        by_issuer: dict[str, dict] = {}
        gov = other = cash = ZERO
        for ln in lines:
            if ln.value < 0:
                raise Section351RuleError(f"{ln.label}: a contributed value is never negative")
            if ln.kind == KIND_CASH:
                cash += ln.value
                continue
            if ln.kind == KIND_GOVERNMENT:
                gov += ln.value
                continue
            if ln.kind == KIND_OTHER:
                other += ln.value
                continue
            if not ln.issuer_key:
                raise Section351RuleError(f"{ln.label}: an issuer line needs its issuer key")
            g = by_issuer.setdefault(ln.issuer_key, {"issuer_key": ln.issuer_key, "label": ln.issuer_label or ln.label,
                                                     "value": ZERO, "direct": ZERO, "look_through": ZERO,
                                                     "securities": set(), "funds": set()})
            g["value"] += ln.value
            g["direct" if ln.via == "direct" else "look_through"] += ln.value
            g["securities"].add(ln.label)
            if ln.fund_label:
                g["funds"].add(ln.fund_label)
        issuer_total = sum((g["value"] for g in by_issuer.values()), ZERO)
        total = issuer_total + gov + other                 # cash excluded (reading 3)
        ranked = sorted(by_issuer.values(), key=lambda g: (-g["value"], g["issuer_key"]))
        issuers_out = []
        for g in ranked:
            w = g["value"] / total if total > 0 else ZERO
            issuers_out.append({"issuer_key": g["issuer_key"], "label": g["label"], "value": str(money(g["value"])),
                                "value_exact": str(g["value"]), "weight": str(qw(w)), "weight_pct": pct_text(w),
                                "direct": str(money(g["direct"])), "look_through": str(money(g["look_through"])),
                                "securities": sorted(g["securities"]), "via_funds": sorted(g["funds"]),
                                "_w": w})
        top1 = issuers_out[0]["_w"] if issuers_out else ZERO
        top5 = sum((x["_w"] for x in issuers_out[:5]), ZERO)
        reasons = []
        if total <= 0 and not blocked:
            blocked.append("nothing to contribute — no valued, eligible assets")
        if total > 0:
            if top1 > max_one:
                reasons.append(f"{issuers_out[0]['label']} is {pct_text(top1)} of the contribution — more than "
                               f"{pct_text(max_one)} in one issuer")
            if top5 > max_five:
                reasons.append(f"the five largest issuers are {pct_text(top5)} — more than {pct_text(max_five)} in "
                               "five or fewer issuers")
        verdict = "holdings_needed" if blocked else ("fail" if reasons else "pass")
        for x in issuers_out:
            x.pop("_w")
        return {
            "verdict": verdict, "verdict_label": VERDICT_LABELS[verdict], "label": LABEL,
            "total_assets": str(money(total)), "total_assets_exact": str(total),
            "issuer_assets": str(money(issuer_total)), "government": str(money(gov)), "other_non_issuer": str(money(other)),
            "cash_excluded": str(money(cash)),
            "largest_issuer_weight": str(qw(top1)), "largest_issuer_pct": pct_text(top1),
            "top_five_weight": str(qw(top5)), "top_five_pct": pct_text(top5),
            "margin_one": str(qw(max_one - top1)), "margin_one_pct": pct_text(max_one - top1),
            "margin_five": str(qw(max_five - top5)), "margin_five_pct": pct_text(max_five - top5),
            "limits": {"one_issuer": str(max_one), "five_issuers": str(max_five)},
            "issuers": issuers_out, "top_five": issuers_out[:5], "reasons": reasons, "blocked": blocked,
            "lines": [line_out(x) for x in lines],
            "rules": RULE_NOTES,
        }


def line_out(x: Line) -> dict:
    return {"security_id": x.security_id, "label": x.label, "value": str(money(x.value)), "value_exact": str(x.value),
            "kind": x.kind, "issuer_key": x.issuer_key, "issuer_label": x.issuer_label, "via": x.via,
            "fund_id": x.fund_id, "fund_label": x.fund_label,
            "holdings_as_of": x.holdings_as_of.isoformat() if isinstance(x.holdings_as_of, date) else x.holdings_as_of,
            "valuation_source": x.valuation_source,
            "valuation_as_of": x.valuation_as_of.isoformat() if isinstance(x.valuation_as_of, date)
            else x.valuation_as_of, "lot_ids": list(x.lot_ids)}


RULE_NOTES = [
    "IRC §351(e)(1); Treas. Reg. §1.351-1(c)(1), (c)(6)(i) applying §368(a)(2)(F)(ii): no more than 25 % of the "
    "contributed value in one issuer and no more than 50 % in five or fewer issuers.",
    "Government securities count in the total but are not an issuer (Treas. Reg. §1.351-1(c)(6)(i)).",
    "Cash and cash items are excluded from the total (§368(a)(2)(F)(iv)); the sponsor's cash rule decides whether "
    "cash is contributed at all.",
    "Fund holdings are looked through to their constituents; a name held directly and inside a fund is summed.",
    "Share classes of one issuer (one SEC CIK) are one issuer; controlled-group grouping is an open item for counsel.",
    "Every figure is an estimate — confirm with counsel; the sponsor runs its own final compliance review.",
]


# ═══ Control ════════════════════════════════════════════════════════════════════════════════════════════════════════
def control_test(contributions: list[dict], *, seed_capital: Decimal) -> dict:
    """``contributions`` = [{account_id, contributed_value, cash_to_member}]. Contributors' share of the ETF right
    after the exchange ≥ 80 % (exact comparison)."""
    if seed_capital is None or seed_capital < 0:
        raise Section351RuleError("outside seed capital is zero or more")
    with localcontext() as ctx:
        ctx.prec = 60
        contributed = sum((Decimal(str(c["contributed_value"])) for c in contributions), ZERO)
        boot = sum((Decimal(str(c.get("cash_to_member") or 0)) for c in contributions), ZERO)
        if boot > contributed:
            raise Section351RuleError("the cash paid to contributors cannot exceed what they contribute")
        theirs = contributed - boot
        total = theirs + seed_capital
        share = theirs / total if total > 0 else ZERO
    ok = total > 0 and share >= CONTROL_MIN
    return {"verdict": "pass" if ok else "fail", "label": LABEL, "contributed": str(money(contributed)),
            "cash_to_contributors": str(money(boot)), "contributors_etf_value": str(money(theirs)),
            "seed_capital": str(money(seed_capital)), "etf_total": str(money(total)),
            "contributors_share": str(qw(share)), "contributors_share_pct": pct_text(share),
            "minimum": str(CONTROL_MIN), "margin": str(qw(share - CONTROL_MIN)),
            "boot_taxable": boot > 0,
            "boot_note": ("Cash or other property received is boot — TAXABLE to the member up to the gain on the "
                          "property (an estimate; the member's tax adviser confirms)") if boot > 0 else
                         "No cash to contributors — the plan receives ETF shares only.",
            "reason": None if ok else (f"contributors would own {pct_text(share)} of the ETF — below 80 % "
                                       "(§351(a) / §368(c))" if total > 0 else "nothing contributed"),
            "rule": "IRC §351(a) with §368(c): the transferors together own at least 80 % of the ETF immediately "
                    "after the exchange."}


# ═══ Carryover basis ════════════════════════════════════════════════════════════════════════════════════════════════
def cents_split(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """``total`` (to the cent) split in proportion to ``weights`` so the parts sum to it EXACTLY (largest remainder;
    ties by position)."""
    total = money(total)
    if not weights:
        return []
    with localcontext() as ctx:
        ctx.prec = 60
        s = sum(weights, ZERO)
        if s <= 0:
            raw = [ZERO for _ in weights]
            raw[-1] = total
            return raw
        exact = [total * w / s for w in weights]
        floors = [x.quantize(CENT, rounding=ROUND_FLOOR) for x in exact]
        left = int(((total - sum(floors, ZERO)) / CENT).to_integral_value())
        order = sorted(range(len(exact)), key=lambda i: (-(exact[i] - floors[i]), i))
        for i in order[:left]:
            floors[i] += CENT
        return floors


def shares_split(shares: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """ETF shares split by FMV to 8 decimals; the last slice takes the remainder so the parts sum EXACTLY."""
    if not weights:
        return []
    with localcontext() as ctx:
        ctx.prec = 60
        s = sum(weights, ZERO)
        parts = [(shares * w / s).quantize(QTY, rounding=ROUND_DOWN) if s > 0 else ZERO for w in weights]
        parts[-1] = shares - sum(parts[:-1], ZERO)
        return parts


def carryover(lots: list[dict], *, etf_shares: Decimal | None, cash_to_member: Decimal = ZERO,
              on: date | None = None) -> dict:
    """``lots`` = [{tax_lot_id, security_id, label, quantity, basis, acquired_date, fmv}] — one contributor.
    Each lot's ETF slice: shares by FMV, basis = lot basis − boot share + gain recognized, acquisition date tacked.
    Totals reconcile to the cent (basis split by largest remainder from the exact total)."""
    from services.rebalance_engine import long_term_from

    if not lots:
        raise Section351RuleError("a basis record needs at least one contributed lot")
    if cash_to_member < 0:
        raise Section351RuleError("cash to the member is zero or more")
    with localcontext() as ctx:
        ctx.prec = 60
        fmvs = [Decimal(str(x["fmv"])) for x in lots]
        bases = [Decimal(str(x["basis"])) for x in lots]
        total_fmv = sum(fmvs, ZERO)
        if cash_to_member > total_fmv:
            raise Section351RuleError("the cash to the member cannot exceed the contributed value")
        boot_parts = cents_split(cash_to_member, fmvs) if cash_to_member > 0 else [ZERO] * len(lots)
        recognized = [max(ZERO, min(b, f - c)) for b, f, c in zip(boot_parts, fmvs, bases)]
        etf_basis_exact = [c - b + r for c, b, r in zip(bases, boot_parts, recognized)]
        total_basis_exact = sum(etf_basis_exact, ZERO)
        basis_cents = cents_split(total_basis_exact, [max(x, ZERO) for x in etf_basis_exact]) \
            if all(x >= 0 for x in etf_basis_exact) else [money(x) for x in etf_basis_exact]
        shares = shares_split(etf_shares, fmvs) if etf_shares is not None else [None] * len(lots)
        lot_basis_cents = cents_split(sum(bases, ZERO), bases) if all(x >= 0 for x in bases) else \
            [money(x) for x in bases]
    out = []
    for x, f, c, lbc, b, r, eb, bc, sh in zip(lots, fmvs, bases, lot_basis_cents, boot_parts, recognized,
                                              etf_basis_exact, basis_cents, shares):
        acq = x["acquired_date"]
        acq_d = date.fromisoformat(acq) if isinstance(acq, str) else acq
        term = None
        if on is not None and acq_d is not None:
            term = "long" if on >= long_term_from(acq_d) else "short"
        out.append({"tax_lot_id": str(x["tax_lot_id"]), "security_id": str(x["security_id"]), "label": x.get("label"),
                    "quantity": str(x["quantity"]), "basis": str(lbc), "basis_exact": str(c),
                    "acquired_date": acq_d.isoformat() if acq_d else None, "fmv": str(money(f)),
                    "gain": str(money(f - c)), "holding_period_on_transfer": term,
                    "boot_allocated": str(money(b)), "gain_recognized": str(money(r)),
                    "etf_basis": str(bc), "etf_basis_exact": str(eb), "etf_acquired_date": acq_d.isoformat()
                    if acq_d else None, "etf_shares": None if sh is None else str(sh),
                    "etf_holding_period": "tacked — includes the contributed lot's (§1223(1))"})
    return {"lines": out, "label": LABEL,
            "totals": {"lots": len(out), "fmv": str(money(total_fmv)), "basis": str(money(sum(bases, ZERO))),
                       "cash_to_member": str(money(cash_to_member)),
                       "gain_recognized": str(money(sum(recognized, ZERO))),
                       "etf_basis": str(money(total_basis_exact)),
                       "etf_shares": None if etf_shares is None else str(etf_shares),
                       "boot_taxable": cash_to_member > 0},
            "rules": ["§358(a)(1): ETF-share basis = basis of the property − boot + gain recognized.",
                      "§1223(1): the ETF shares' holding period includes the contributed lot's (tacked, per lot).",
                      "§362(a) / §1223(2): the fund takes the same basis and holding period in each lot.",
                      "Boot is taxable up to each lot's gain (asset by asset); no loss is recognized (§351(b)(2))."]}


# ═══ Fix-up: the least-tax sales that make a failing contribution pass ═══════════════════════════════════════════════
@dataclass
class FixLot:
    lot_id: str
    quantity: Decimal
    unit_cost: Decimal | None
    acquired: date | None
    term_hint: str | None = None


@dataclass
class FixHolding:
    security_id: str
    label: str
    issuer_key: str
    issuer_label: str
    price: Decimal
    quantity: Decimal
    lots: list[FixLot]


def lot_cost_key(lot: FixLot, price: Decimal, on: date, rates: dict | None):
    """The order lots are sold in: the least estimated tax per dollar raised (rep6's
    rebalance_engine.tax_per_dollar at the profile's combined rates); with no profile, the least GAIN per dollar
    (losses first). A lot with unknown cost or term sorts last. Ties: acquisition date, then lot id."""
    from services import rebalance_engine as E

    el = E.Lot(id=lot.lot_id, quantity=lot.quantity, unit_cost=lot.unit_cost, acquired=lot.acquired,
               term_hint=lot.term_hint)
    if rates is not None:
        t = E.tax_per_dollar(el, price, on, rates)
    else:
        t = None if lot.unit_cost is None or price <= 0 else (price - lot.unit_cost) / price
    return (t is None, t if t is not None else ZERO, lot.acquired or date.max, lot.lot_id)


def _test_values(values: dict[str, Decimal], issuer_of: dict[str, str], fixed: dict) -> tuple:
    """(total, issuer values sorted desc) for security values + fixed issuer values (look-through, government…)."""
    by: dict[str, Decimal] = dict(fixed.get("issuers", {}))
    for k, v in values.items():
        by[issuer_of[k]] = by.get(issuer_of[k], ZERO) + v
    total = sum(by.values(), ZERO) + fixed.get("non_issuer", ZERO)
    return total, sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))


def _top5(clipped: list[Decimal]) -> Decimal:
    return sum(sorted(clipped, reverse=True)[:5], ZERO)


def water_level(values: dict[str, Decimal], floors: dict[str, Decimal], total: Decimal) -> Decimal | None:
    """The HIGHEST common level L the issuers are trimmed to (each issuer → max(min(v, L), its unsellable floor))
    such that no issuer exceeds 25 % of ``total`` and the five largest are at most 50 % — the fewest dollars sold
    (water-filling). None when even the unsellable floors break a limit. Bisection, 200 steps, exact Decimal."""
    with localcontext() as ctx:
        ctx.prec = 60
        keys = sorted(values)

        def clip(L):
            return [max(min(values[k], L), floors.get(k, ZERO)) for k in keys]

        if total <= 0:
            return None
        lo_ok = clip(ZERO)
        if max(lo_ok, default=ZERO) > MAX_ONE * total or _top5(lo_ok) > MAX_FIVE * total:
            return None
        lo, hi = ZERO, MAX_ONE * total
        if _top5(clip(hi)) <= MAX_FIVE * total:
            return hi
        for _ in range(200):
            mid = (lo + hi) / 2
            if _top5(clip(mid)) <= MAX_FIVE * total:
                lo = mid
            else:
                hi = mid
        return lo


def fixup_plan(holdings: list[FixHolding], buys: list[dict], *, on: date, rates: dict | None,
               fixed: dict | None = None, max_rounds: int = 60) -> dict:
    """The least-tax sales that make a failing contribution pass, and the reinvestment.

    1. WATER-FILL (:func:`water_level`): the highest level L the largest issuers are trimmed to so that both limits
       hold — the fewest dollars sold. An issuer's look-through part (inside a fund) cannot be sold here: it is the
       issuer's floor.
    2. Within each trimmed issuer, its lots in order of LEAST estimated tax per dollar raised (:func:`lot_cost_key`
       — rep6's tax_per_dollar at the profile's rates), whole shares, the last lot only as far as needed (rounded up).
    3. The proceeds buy the account's model names (``buys``) in proportion to their model weights, whole shares,
       each capped so its issuer stays at or below the fifth-largest issuer after the sales (a bought name never
       becomes binding); what cannot be placed stays cash — outside the test, so the total shrinks.
    4. If the rounding or the cash left makes the test fail, the level is recomputed for the smaller total and the
       sales redone from the start (deterministic; at most ``max_rounds``)."""
    fixed = fixed or {}
    fixed_iss = {k: Decimal(str(v)) for k, v in (fixed.get("issuers") or {}).items()}
    non_issuer = Decimal(str(fixed.get("non_issuer") or 0))
    price = {h.security_id: h.price for h in holdings}
    issuer_of = {h.security_id: h.issuer_key for h in holdings}
    labels = {h.security_id: h.label for h in holdings}
    for b in buys:
        price.setdefault(b["security_id"], Decimal(str(b["price"])))
        issuer_of.setdefault(b["security_id"], b["issuer_key"])
        labels.setdefault(b["security_id"], b["label"])
    with localcontext() as ctx:
        ctx.prec = 60
        base_qty = {h.security_id: h.quantity for h in holdings}
        direct_by_issuer: dict[str, Decimal] = {}
        for h in holdings:
            direct_by_issuer[h.issuer_key] = direct_by_issuer.get(h.issuer_key, ZERO) + h.quantity * h.price
        all_iss = set(direct_by_issuer) | set(fixed_iss)
        values = {i: direct_by_issuer.get(i, ZERO) + fixed_iss.get(i, ZERO) for i in all_iss}
        total = sum(values.values(), ZERO) + non_issuer
        assume = total
        last = None
        for _ in range(max_rounds):
            L = water_level(values, fixed_iss, assume)
            if L is None:
                return {"possible": False, "reason": "the issuers held inside funds alone break a limit — no sale of "
                                                     "this account's own lots can fix it", "sales": [], "buys": [],
                        "cash_left": "0.00", "label": LABEL}
            qty = dict(base_qty)
            sales = []
            for iss in sorted(all_iss):
                need = values[iss] - max(min(values[iss], L), fixed_iss.get(iss, ZERO))
                if need <= 0:
                    continue
                cands = sorted(((lot_cost_key(lot, h.price, on, rates), h.security_id, lot)
                                for h in holdings if h.issuer_key == iss for lot in h.lots if lot.quantity > 0),
                               key=lambda c: c[0])
                for _key, sid, lot in cands:
                    if need <= 0:
                        break
                    q = min(lot.quantity, (need / price[sid]).to_integral_value(rounding=ROUND_CEILING))
                    if q <= 0:
                        continue
                    qty[sid] -= q
                    need -= q * price[sid]
                    sales.append({"security_id": sid, "label": labels[sid], "lot_id": lot.lot_id, "quantity": q,
                                  "price": price[sid], "proceeds": q * price[sid], "unit_cost": lot.unit_cost,
                                  "acquired": lot.acquired, "term_hint": lot.term_hint,
                                  "gain": None if lot.unit_cost is None else (price[sid] - lot.unit_cost) * q})
                if need > 0:
                    return {"possible": False, "reason": f"issuer {iss}: not enough lots to sell", "sales": [],
                            "buys": [], "cash_left": "0.00", "label": LABEL}
            proceeds = sum((x["proceeds"] for x in sales), ZERO)
            vals = {k: q * price[k] for k, q in qty.items() if q > 0}
            _, ranked = _test_values(vals, issuer_of, {"issuers": fixed_iss})
            cap = ranked[4][1] if len(ranked) >= 5 else ZERO
            by_iss = dict(ranked)
            over = {iss for iss in all_iss if values[iss] - max(min(values[iss], L), fixed_iss.get(iss, ZERO)) > 0}
            buyable = [b for b in buys if b["issuer_key"] not in over]
            wsum = sum((Decimal(str(b["weight"])) for b in buyable), ZERO)
            bought: dict[str, Decimal] = {}
            spent = ZERO
            for b in sorted(buyable, key=lambda x: (-Decimal(str(x["weight"])), x["security_id"])):
                k = b["security_id"]
                share = proceeds * Decimal(str(b["weight"])) / wsum if wsum > 0 else ZERO
                room = max(ZERO, cap - by_iss.get(b["issuer_key"], ZERO))
                n = (min(share, room, proceeds - spent) / price[k]).to_integral_value(rounding=ROUND_FLOOR)
                if n > 0:
                    bought[k] = bought.get(k, ZERO) + n
                    qty[k] = qty.get(k, ZERO) + n
                    spent += n * price[k]
                    by_iss[b["issuer_key"]] = by_iss.get(b["issuer_key"], ZERO) + n * price[k]
            vals = {k: q * price[k] for k, q in qty.items() if q > 0}
            post_total, post_ranked = _test_values(vals, issuer_of, {"issuers": fixed_iss, "non_issuer": non_issuer})
            top1 = post_ranked[0][1] / post_total if post_ranked and post_total > 0 else ZERO
            top5 = sum((v for _, v in post_ranked[:5]), ZERO) / post_total if post_total > 0 else ZERO
            last = (sales, bought, proceeds - spent, post_total, top1, top5, qty, L)
            if post_total > 0 and top1 <= MAX_ONE and top5 <= MAX_FIVE:
                break
            if post_total <= 0 or post_total >= assume:
                assume = assume - (assume - post_total if post_total < assume else total / 1000)
            else:
                assume = post_total
        sales, bought, cash, post_total, top1, top5, qty, L = last
    ok = post_total > 0 and top1 <= MAX_ONE and top5 <= MAX_FIVE
    merged: dict[tuple, dict] = {}
    for s_ in sales:
        key = (s_["security_id"], s_["lot_id"])
        if key in merged:
            m = merged[key]
            m["quantity"] += s_["quantity"]
            m["proceeds"] += s_["proceeds"]
            m["gain"] = None if m["gain"] is None or s_["gain"] is None else m["gain"] + s_["gain"]
        else:
            merged[key] = dict(s_)
    return {"possible": ok, "reason": None if ok else "no sale set within the round limit makes it pass",
            "level": str(money(L)), "sales": [_sale_out(x) for x in merged.values()],
            "buys": _buys_out(bought, price, labels), "cash_left": str(money(cash)),
            "after": {"largest_issuer_pct": pct_text(top1), "top_five_pct": pct_text(top5),
                      "total": str(money(post_total))},
            "after_quantities": {k: str(v) for k, v in sorted(qty.items())}, "label": LABEL,
            "method": "water-fill to the highest common level, then each trimmed issuer's least-tax lots"}


def _sale_out(s: dict) -> dict:
    return {"security_id": s["security_id"], "label": s["label"], "lot_id": s["lot_id"], "quantity": str(s["quantity"]),
            "price": str(s["price"]), "proceeds": str(money(s["proceeds"])),
            "unit_cost": None if s["unit_cost"] is None else str(s["unit_cost"]),
            "acquired": s["acquired"].isoformat() if s["acquired"] else None, "term_hint": s.get("term_hint"),
            "gain": None if s["gain"] is None else str(money(s["gain"]))}


def _buys_out(bought: dict, price: dict, labels: dict) -> list[dict]:
    return [{"security_id": k, "label": labels.get(k, k), "quantity": str(q), "price": str(price[k]),
             "cost": str(money(q * price[k]))} for k, q in sorted(bought.items()) if q > 0]


def estimated_tax(st_gain: Decimal, lt_gain: Decimal, rates: dict | None) -> dict:
    """Tax on the fix-up's realised gains at the profile's combined rates (rep6's rates; never a guessed bracket).
    A net loss on a side is not given a negative tax here (shown as 0 tax on that side, the loss itself stated)."""
    if rates is None:
        return {"estimated": False, "tax": None, "note": "not estimated — no tax profile", "label": LABEL,
                "st_gain": str(money(st_gain)), "lt_gain": str(money(lt_gain))}
    with localcontext() as ctx:
        ctx.prec = 60
        st_tax = max(st_gain, ZERO) * Decimal(str(rates["st"]))
        lt_tax = max(lt_gain, ZERO) * Decimal(str(rates["lt"]))
    return {"estimated": True, "tax": str(money(st_tax + lt_tax)), "st_tax": str(money(st_tax)),
            "lt_tax": str(money(lt_tax)), "st_gain": str(money(st_gain)), "lt_gain": str(money(lt_gain)),
            "rates": {"st": str(rates["st"]), "lt": str(rates["lt"])}, "label": LABEL,
            "note": "estimate at the tax profile's combined rates (entered by the adviser, with its date) — the "
                    "member's tax adviser confirms"}


# ═══ "Harvesting has run dry" ═══════════════════════════════════════════════════════════════════════════════════════
DEFAULT_THRESHOLDS = {"max_harvestable_loss_pct": Decimal("0.02"), "min_unrealized_gain_pct": Decimal("0.25"),
                      "min_years_since_inception": Decimal("3")}


def run_dry(*, account_value: Decimal, harvestable_loss: Decimal, unrealized_gain: Decimal,
            years_since_inception: Decimal | None, thresholds: dict) -> dict:
    """The stated rule: harvesting has run dry when ALL three hold —
      harvestable loss remaining ≤ max_harvestable_loss_pct of the account's value,
      unrealised gain ≥ min_unrealized_gain_pct of the account's value,
      years since inception (the model link's start) ≥ min_years_since_inception (unknown → not met).
    Exact comparisons; every input shown next to its threshold."""
    with localcontext() as ctx:
        ctx.prec = 60
        loss_pct = harvestable_loss / account_value if account_value > 0 else None
        gain_pct = unrealized_gain / account_value if account_value > 0 else None
    t_loss = Decimal(str(thresholds["max_harvestable_loss_pct"]))
    t_gain = Decimal(str(thresholds["min_unrealized_gain_pct"]))
    t_years = Decimal(str(thresholds["min_years_since_inception"]))
    checks = [
        {"key": "harvestable_loss", "value": None if loss_pct is None else str(qw(loss_pct)),
         "value_pct": None if loss_pct is None else pct_text(loss_pct), "threshold": str(t_loss),
         "threshold_pct": pct_text(t_loss), "rule": "harvestable loss remaining ≤ threshold × account value",
         "met": loss_pct is not None and loss_pct <= t_loss},
        {"key": "unrealized_gain", "value": None if gain_pct is None else str(qw(gain_pct)),
         "value_pct": None if gain_pct is None else pct_text(gain_pct), "threshold": str(t_gain),
         "threshold_pct": pct_text(t_gain), "rule": "unrealised gain ≥ threshold × account value",
         "met": gain_pct is not None and gain_pct >= t_gain},
        {"key": "years_since_inception", "value": None if years_since_inception is None else str(years_since_inception),
         "threshold": str(t_years), "rule": "years since the model link began ≥ threshold (unknown → not met)",
         "met": years_since_inception is not None and years_since_inception >= t_years},
    ]
    return {"run_dry": all(c["met"] for c in checks), "checks": checks}


def years_between(start: date | None, end: date) -> Decimal | None:
    if start is None:
        return None
    return (Decimal((end - start).days) / Decimal("365.25")).quantize(CENT, rounding=ROUND_DOWN)
