"""CASH05 — Treasury → EARNINGS for a selectable period: dividends per fund + ladder interest + cash interest − the
advisory fee attributable to cash = total. Read-only. Decimal end to end.

METHOD (docs/cash/CASH_TREASURY_SCREENS.md) — CASH BASIS, so every income line IS a sum of activity-ledger entries of
the period (``cash_activity.ledger``, by trade date) and reconciles to the cent:

* DIVIDENDS per fund = Σ ledger ``dividend`` / ``interest`` entries booked on a FUND holding (a money-market fund
  pays its income as dividends, whichever code the custodian used);
* LADDER INTEREST = coupons (Σ ``interest`` entries on Treasury holdings) + discount REALISED at maturity (each
  ``maturity`` entry's proceeds − the matured par's cost, the position's ``cost_basis`` pro rata to the quantity);
  a bill still held ACCRETES its discount straight-line to maturity — shown beside the total ("accreting, not yet
  realised"), never in it;
* CASH INTEREST = Σ ``dividend`` / ``interest`` entries booked on cash and on any other Cash & Equivalents line;
* ADVISORY FEE attributable to cash — from THE fee engine's own records, never re-priced: for every POSTED (or
  exported / reconciled) ``fee_run_lines`` row of a scope account whose billing period overlaps the period,
  ``net_fee × billed cash ÷ billable value × overlap days ÷ period days`` (accrued daily, charged per run). Billed
  cash is the engine's own input — ``account_balances_daily.cash_value`` at the run's period end — after the
  schedule's ``cash_treatment``: EXCLUDE → 0; EXCLUDE_ABOVE_PCT → min(cash, total × pct); INCLUDE → cash. When every
  schedule governing the scope's accounts EXCLUDES cash the line reads "fee on cash: not charged" (0.00); no schedule
  assigned → "fee on cash: not estimated — no fee schedule is assigned".

TOTAL = dividends + ladder interest + cash interest − fee. The ledger's own fee DEBITS on cash holdings are shown
beside it (an account's whole fee is usually debited from its cash; only cash's share is a cost of the cash).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from services import cash_activity as CA
from services import cash_holdings as CH
from services import cash_obligations as OB

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
POSTED_STATUSES = ("POSTED", "EXPORTED", "RECONCILED")
PERIODS = (("mtd", "Month to date"), ("qtd", "Quarter to date"), ("ytd", "Year to date"), ("12m", "Last 12 months"),
           ("custom", "Custom"))
FUND_KINDS = ("mmf",)
LADDER_KINDS = ("bill", "note")
NOT_CHARGED = "fee on cash: not charged (the fee schedule excludes cash)"
NO_SCHEDULE = "fee on cash: not estimated — no fee schedule is assigned to these accounts"
NO_RUN = "no posted fee run overlaps the period — nothing accrued"
FREQ_PER_YEAR = {"MONTHLY": Decimal(12), "QUARTERLY": Decimal(4), "SEMIANNUAL": Decimal(2), "ANNUAL": ONE}


class PeriodError(ValueError):
    pass


def period_bounds(key: str | None, today: date, start: date | None = None, end: date | None = None) -> tuple[date, date, str]:
    k = (key or "mtd").lower()
    if k == "custom":
        if start is None or end is None or start > end:
            raise PeriodError("a custom period needs start ≤ end")
        if end > today:
            raise PeriodError("a period cannot end after today")
        return start, end, k
    if k == "mtd":
        return date(today.year, today.month, 1), today, k
    if k == "qtd":
        return date(today.year, 3 * ((today.month - 1) // 3) + 1, 1), today, k
    if k == "ytd":
        return date(today.year, 1, 1), today, k
    if k == "12m":
        return OB.add_months(today, -12) + timedelta(days=1), today, k
    raise PeriodError(f"unknown period {key!r}; one of {[p for p, _ in PERIODS]}")


def realised_discount(e: dict) -> tuple[Decimal | None, str | None]:
    """A maturity entry's proceeds − the matured par's cost. Pure."""
    cost = e.get("position_cost_basis")
    if cost is None:
        return None, "not estimated — needs the matured position's cost basis"
    cost = Decimal(str(cost))
    pq, tq = e.get("position_quantity"), e.get("quantity")
    if pq is not None and tq is not None and Decimal(str(pq)) > ZERO:
        cost = cost * abs(Decimal(str(tq))) / Decimal(str(pq))
    if e["change"] is None:
        return None, "not estimated — needs the maturity's proceeds"
    return abs(e["change"]) - cost, None


def accreting(holdings: list[dict], start: date, end: date, today: date) -> list[dict]:
    """Bills still held: straight-line discount accretion over [start, end] ∩ [position as-of, maturity]. Pure,
    informational (not in the total)."""
    out = []
    for h in holdings:
        if h["kind"] != "bill" or not h["maturity"] or h["maturity"] <= today:
            continue
        for p in h["positions"]:
            cost, qty, frm = p.get("cost_basis"), p.get("quantity"), p["as_of"]
            if cost is None or qty is None:
                out.append({"holding": h["name"], "text": "not estimated — needs the position's cost basis"})
                continue
            life = (h["maturity"] - frm).days
            lo, hi = max(start, frm), min(end, h["maturity"])
            days = (hi - lo).days + 1 if hi >= lo else 0
            disc = Decimal(str(qty)) - Decimal(str(cost))
            amt = disc * Decimal(days) / Decimal(life) if life > 0 else ZERO
            out.append({"holding": h["name"], "amount": amt * p["share"], "days": days,
                        "text": f"(par − cost) × {days} ÷ {life} days"})
    return out


def breakdown(entries: list[dict], fee: dict) -> dict:
    """The period's earnings from its ledger entries + the fee read. Pure; every income term names its entries."""
    funds: dict[str, dict] = {}
    coupons, discounts, cash_int = [], [], []
    missing = []
    for e in entries:
        if e["change"] is None:
            continue
        if e["type"] in ("dividend", "interest"):
            if e["holding_kind"] in FUND_KINDS:
                f = funds.setdefault(e["security_id"] or e["asset_id"], {"holding": e["holding"], "amount": ZERO,
                                                                         "entries": []})
                f["amount"] += e["change"]
                f["entries"].append(e["id"])
            elif e["holding_kind"] in LADDER_KINDS:
                coupons.append(e)
            else:
                cash_int.append(e)
        elif e["type"] == "maturity":
            d, why = realised_discount(e)
            if d is None:
                missing.append({"entry": e["id"], "text": why})
            else:
                discounts.append((e, d))
    div = sum((f["amount"] for f in funds.values()), ZERO)
    coupon = sum((e["change"] for e in coupons), ZERO)
    disc = sum((d for _e, d in discounts), ZERO)
    ladder = coupon + disc
    cash = sum((e["change"] for e in cash_int), ZERO)
    fee_amt = fee["amount"] if fee.get("amount") is not None else ZERO
    total = div + ladder + cash - fee_amt
    ledger_income = sum((e["change"] for e in entries if e["change"] is not None and e["type"] in
                         ("dividend", "interest")), ZERO) + disc
    ledger_fees = sum((-e["change"] for e in entries if e["change"] is not None and e["type"] == "fee"), ZERO)
    return {"dividends": div, "funds": sorted(funds.values(), key=lambda f: f["holding"] or ""),
            "ladder_interest": ladder, "coupons": coupon, "realised_discount": disc,
            "coupon_entries": [e["id"] for e in coupons], "maturity_entries": [e["id"] for e, _d in discounts],
            "cash_interest": cash, "cash_entries": [e["id"] for e in cash_int],
            "fee": fee_amt, "total": total, "missing": missing,
            "ledger_income": ledger_income, "ledger_fee_debits": ledger_fees,
            "reconciles": (div + ladder + cash) == ledger_income}


# ═══ The fee engine's read ══════════════════════════════════════════════════════════════════════════════════════
LINES_SQL = """
SELECT l.id::text AS id, l.fee_run_id::text AS run_id, l.account_id::text AS account_id, l.net_fee, l.billable_value,
       l.fee_schedule_id::text AS fee_schedule_id, r.period_start, r.period_end, r.status, r.billing_frequency,
       s.cash_treatment, s.cash_exclusion_pct, s.code AS schedule_code
  FROM public.fee_run_lines l
  JOIN public.fee_runs r ON r.id = l.fee_run_id AND r.org_id = l.org_id AND r.valid_to IS NULL AND r.system_to IS NULL
  JOIN public.fee_schedules s ON s.id = l.fee_schedule_id AND s.org_id = l.org_id
 WHERE l.org_id = $1::uuid AND l.account_id = ANY($2::uuid[]) AND r.status = ANY($3::text[])
   AND r.period_start <= $5::date AND r.period_end >= $4::date
 ORDER BY r.period_end, l.account_id, l.id
"""
BAL_SQL = """
SELECT cash_value, total_market_value, as_of_date FROM public.account_balances_daily
 WHERE org_id = $1::uuid AND account_id = $2::uuid AND as_of_date <= $3::date
 ORDER BY as_of_date DESC, is_final DESC, is_billing_source DESC, source_system LIMIT 1
"""


def billed_cash(treatment: str, cash: Decimal, total: Decimal, pct_: Decimal | None) -> Decimal:
    if treatment == "EXCLUDE":
        return ZERO
    if treatment == "EXCLUDE_ABOVE_PCT":
        return min(cash, total * (pct_ or ZERO))
    return cash


def accrual(line: dict, bal: dict | None, start: date, end: date) -> dict:
    """One posted run line's share attributable to cash, accrued over the overlap with [start, end]. Pure."""
    lo, hi = max(start, line["period_start"]), min(end, line["period_end"])
    overlap = (hi - lo).days + 1
    span = (line["period_end"] - line["period_start"]).days + 1
    billable = Decimal(str(line["billable_value"] or 0))
    if bal is None:
        return {"amount": None, "text": "not estimated — needs the account's balance at the run's period end"}
    cash = billed_cash(line["cash_treatment"], Decimal(str(bal["cash_value"])), Decimal(str(bal["total_market_value"])),
                       None if line["cash_exclusion_pct"] is None else Decimal(str(line["cash_exclusion_pct"])))
    if billable <= ZERO:
        return {"amount": ZERO, "text": "billable value 0"}
    frac = min(ONE, cash / billable)
    amt = Decimal(str(line["net_fee"])) * frac * Decimal(overlap) / Decimal(span)
    return {"amount": amt, "fraction": frac, "overlap_days": overlap, "period_days": span, "billed_cash": cash,
            "text": f"net fee {CH.money(Decimal(str(line['net_fee'])))} × billed cash {CH.money(cash)} ÷ billable "
                    f"{CH.money(billable)} × {overlap} ÷ {span} days"}


async def schedules_for(conn, org_id, account_ids: list[str], as_of: date) -> dict[str, dict | None]:
    from services import fee_schedules as FS

    out = {}
    for a in account_ids:
        try:
            res = await FS.resolve_assignment_for_account(conn, str(org_id), a, as_of=as_of)
        except Exception:  # noqa: BLE001 — a non-current account has no schedule
            res = None
        if res is None:
            out[a] = None
            continue
        row = await conn.fetchrow("SELECT cash_treatment, cash_exclusion_pct, code, billing_frequency FROM "
                                  "public.fee_schedules WHERE id = $1::uuid AND org_id = $2::uuid",
                                  res.fee_schedule_id, str(org_id))
        out[a] = dict(row) if row else None
    return out


async def fee_on_cash(conn, org_id, account_ids: list[str], start: date, end: date) -> dict:
    """{charged: True | False | None, amount (Decimal | None), text, lines, schedules}."""
    scheds = await schedules_for(conn, org_id, account_ids, end)
    assigned = {a: s for a, s in scheds.items() if s is not None}
    sched_out = [{"account_id": a, "schedule": s["code"] if s else None,
                  "cash_treatment": s["cash_treatment"] if s else None} for a, s in sorted(scheds.items())]
    if not assigned:
        return {"charged": None, "amount": None, "text": NO_SCHEDULE, "lines": [], "schedules": sched_out}
    if all(s["cash_treatment"] == "EXCLUDE" for s in assigned.values()):
        return {"charged": False, "amount": ZERO, "text": NOT_CHARGED, "lines": [], "schedules": sched_out}
    rows = [dict(r) for r in await conn.fetch(LINES_SQL, str(org_id), list(assigned), list(POSTED_STATUSES), start,
                                              end)]
    lines, total, unest = [], ZERO, []
    for r in rows:
        bal = await conn.fetchrow(BAL_SQL, str(org_id), r["account_id"], r["period_end"])
        a = accrual(r, dict(bal) if bal else None, start, end)
        if a["amount"] is None:
            unest.append(r["id"])
        else:
            total += a["amount"]
        lines.append({"line_id": r["id"], "run_id": r["run_id"], "account_id": r["account_id"],
                      "period": f"{r['period_start']} – {r['period_end']}", "status": r["status"],
                      "cash_treatment": r["cash_treatment"], "amount": CH.money(a["amount"]) if a["amount"]
                      is not None else None, "method": a["text"]})
    text = ("advisory fee accrued on cash (the fee engine's posted runs, pro rata to the days in the period)"
            if rows else NO_RUN)
    if unest:
        text += f"; {len(unest)} run line(s) not estimated — needs the account balance"
    return {"charged": True, "amount": total, "text": text, "lines": lines, "schedules": sched_out}


async def fee_rate(conn, org_id, account_ids: list[str], today: date) -> dict:
    """The advisory fee's annual rate on cash for the blended yield: {estimated, rate_pct, text}. EXCLUDE everywhere →
    0 ("not charged"); else the latest posted run line per account: net_fee ÷ billable × periods a year,
    billable-weighted."""
    scheds = await schedules_for(conn, org_id, account_ids, today)
    assigned = {a: s for a, s in scheds.items() if s is not None}
    if not assigned:
        return {"estimated": False, "text": NO_SCHEDULE}
    if all(s["cash_treatment"] == "EXCLUDE" for s in assigned.values()):
        return {"estimated": True, "rate_pct": ZERO, "text": NOT_CHARGED}
    rows = [dict(r) for r in await conn.fetch(LINES_SQL, str(org_id), list(assigned), list(POSTED_STATUSES),
                                              date(1900, 1, 1), today)]
    latest: dict[str, dict] = {}
    for r in rows:
        latest[r["account_id"]] = r      # ordered by period_end ascending → the last one wins
    num = den = ZERO
    for r in latest.values():
        b = Decimal(str(r["billable_value"] or 0))
        if b <= ZERO:
            continue
        num += Decimal(str(r["net_fee"])) * FREQ_PER_YEAR.get(r["billing_frequency"], ONE)
        den += b
    if den <= ZERO:
        return {"estimated": False, "text": "advisory fee: not estimated — needs a posted fee run"}
    return {"estimated": True, "rate_pct": num / den * HUNDRED,
            "text": "advisory fee: the latest posted run per account, net fee ÷ billable × runs a year"}


async def build(conn, scope: CH.Scope, today: date, *, period: str | None, start: date | None, end: date | None,
                account_ids: list[str], holdings: list[dict] | None = None) -> dict:
    p0, p1, key = period_bounds(period, today, start, end)
    got = await CA.ledger(conn, scope, today, start=p0, end=p1)
    fee = await fee_on_cash(conn, scope.org_id, account_ids, p0, p1)
    b = breakdown(got["entries"], fee)
    acc = accreting(holdings or [], p0, p1, today)
    return {"period": {"key": key, "start": p0.isoformat(), "end": p1.isoformat()}, "b": b, "fee": fee,
            "accreting": acc, "entries": got["entries"]}


def render(out: dict) -> dict:
    b, fee = out["b"], out["fee"]
    m = CH.money
    fee_text = fee["text"]
    return {
        "period": out["period"],
        "dividends": {"total": m(b["dividends"]), "funds": [{"holding": f["holding"], "amount": m(f["amount"]),
                                                             "entries": f["entries"]} for f in b["funds"]]},
        "ladder_interest": {"total": m(b["ladder_interest"]), "coupons": m(b["coupons"]),
                            "realised_discount": m(b["realised_discount"]), "coupon_entries": b["coupon_entries"],
                            "maturity_entries": b["maturity_entries"],
                            "method": "coupons received + discount realised at maturity (proceeds − cost); a bill "
                                      "still held accretes straight-line and is shown below, not in the total"},
        "cash_interest": {"total": m(b["cash_interest"]), "entries": b["cash_entries"]},
        "fee": {"amount": m(b["fee"]), "charged": fee["charged"], "text": fee_text, "lines": fee["lines"],
                "schedules": fee["schedules"]},
        "total": m(b["total"]),
        "formula": "dividends + ladder interest + cash interest − advisory fee on cash = total",
        "reconciliation": {"ledger_income": m(b["ledger_income"]), "reconciles": b["reconciles"],
                           "ledger_fee_debits": m(b["ledger_fee_debits"]),
                           "text": "every income line is a sum of this period's activity entries (cash basis); the "
                                   "fee is the engine's accrual on cash, beside the fee debits the ledger shows"},
        "missing": b["missing"],
        "accreting": [{**a, "amount": m(a["amount"]) if a.get("amount") is not None else None}
                      for a in out["accreting"]],
    }


def vocabularies() -> dict:
    return {"periods": [{"key": k, "label": v} for k, v in PERIODS], "editable": [], "inline_editable": []}
