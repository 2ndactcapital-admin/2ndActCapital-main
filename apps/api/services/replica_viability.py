"""Replication Studio (rep4) — the five-question viability check and the
fund-versus-replica cost comparison.

For a registry entry + a custodian + an illustrative account size (default:
org setting ``replication.viability.default_account_size``, $250,000):

  Q1 rules_based    Is the fund rules-based and its holdings public?
                    instrument_class index_etf / index_mutual_fund → yes;
                    anything else → no (an active fund's holdings are a
                    manager's decisions, published with a lag).
  Q2 rebuildable    Can it be rebuilt with stocks or listed options?
                    From the live snapshot: the share of non-cash weight in
                    futures / other; above the threshold → no (a "core only"
                    replica of the stock sleeve is noted). The snapshot's as-of
                    date and age are shown; an N-PORT snapshot older than 45
                    days is flagged.
  Q3 account_size   Is the account big enough at the target tracking error?
                    Only when EVERY constituent has a close price no older than
                    5 days; otherwise "unknown — needs prices for N of M
                    constituents". Never estimates a price. The minimum is the
                    larger of (a) every position holds one unit: max p·u / w,
                    and (b) the worst-case rounding error — at most one unit per
                    position — stays within the tracking target T: Σ p·u / T;
                    the unit u is one share, or the fractional increment when
                    the custodian's fractional_shares is available.
  Q4 custodian      Capabilities of the chosen custodian ('unknown' is NOT
                    available — services.custodian_capabilities.available):
                    with no trading API the answer is "yes, via execution
                    instructions" (the manual path), with the reason.
  Q5 cost           Fund net expense ratio (approved terms) vs the model's
                    strategy fee (its default fee schedule through
                    services.fee_calc.apply_tiers — never a fee run or a bill)
                    + estimated trading cost (annualised turnover of the last N
                    snapshot diffs × 2 sides × an org-setting cost in bps). The
                    tax benefit line says "estimated by the tax engine (rep6)"
                    and carries NO number. Any missing input → unknown.

Every answer carries its inputs and its missing data. Reports are stored
immutable (portfolio.replica_viability_reports) and re-computable: computing
again writes a new report.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from services import custodian_capabilities as CC
from services import human_tasks as HT
from services import models as M
from services import replica_models as RM

T_REP = "portfolio.replica_viability_reports"
CENT = Decimal("0.01")
ZERO = Decimal(0)

QUESTIONS = (
    ("rules_based", "Is the fund rules-based and are its holdings public?"),
    ("rebuildable", "Can it be rebuilt with stocks or listed options?"),
    ("account_size", "Is the account big enough at the target tracking error?"),
    ("custodian", "Does the custodian allow every leg?"),
    ("cost", "Does the replica beat the fund after fees and tax?"),
)
QUESTION_LABELS = dict(QUESTIONS)
STATUS_LABELS = {"yes": "Yes", "no": "No", "unknown": "Unknown"}
COST_LINE_LABELS = {
    "fund_expense": "Fund expense ratio (net)", "strategy_fee": "Model strategy fee",
    "trading_cost": "Estimated trading cost", "replica_total": "Replica total",
    "tax_benefit": "Tax benefit",
}
TAX_LINE_TEXT = "Estimated by the tax engine (rep6) — not estimated here"
INDEX_CLASSES = ("index_etf", "index_mutual_fund")
#: rep8 — defined-outcome (buffer) and covered-call (income) ETFs: the underlying plus an options package
OPTIONS_CLASSES = ("buffer_etf", "income_etf")

#: org setting key → stated default. Each one used is reported in the answer's inputs with its origin.
SETTINGS = {
    "replication.viability.default_account_size": Decimal("250000"),
    "replication.viability.max_non_equity_weight": Decimal("0.05"),
    "replication.viability.nport_stale_days": Decimal("45"),
    "replication.viability.price_max_age_days": Decimal("5"),
    "replication.viability.tracking_target": Decimal("0.005"),
    "replication.viability.fractional_increment": Decimal("0.001"),
    "replication.viability.turnover_snapshots": Decimal("12"),
    "replication.viability.trading_cost_bps": Decimal("5"),
}


async def setting(conn, org_id, key: str) -> tuple[Decimal, str]:
    from services.org_settings import get_setting_with_origin

    default = SETTINGS[key]
    try:
        v, is_default = await get_setting_with_origin(conn, uuid.UUID(str(org_id)), key)
    except Exception:  # noqa: BLE001 — an unreadable setting falls back to the stated default
        return default, "default"
    if v is None or is_default:
        return default, "default"
    try:
        d = Decimal(str(v))
    except Exception:  # noqa: BLE001
        return default, "default"
    return (d, "org_setting") if d > 0 else (default, "default")


def _answer(key: str, status: str, reason: str, inputs: dict | None = None, missing: list | None = None) -> dict:
    return {"key": key, "label": QUESTION_LABELS[key], "status": status, "reason": reason,
            "inputs": inputs or {}, "missing": missing or []}


def _cents(d: Decimal) -> str:
    return str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def _today() -> date:
    return datetime.now(timezone.utc).date()


# ── the five questions ──────────────────────────────────────────────────────
def q1_rules_based(entry: dict) -> dict:
    cls = entry["instrument_class"]
    if cls in OPTIONS_CLASSES:
        return _answer("rules_based", "yes", "A defined-outcome or covered-call fund follows stated rules — an outcome "
                       "period with a cap and a buffer, or a call-writing rule — over a published underlying, so it "
                       "is rebuilt as the underlying plus an options package.", {"instrument_class": cls})
    if cls in INDEX_CLASSES:
        return _answer("rules_based", "yes", "An index fund follows published rules and discloses its holdings, so "
                       "they can be rebuilt position by position.", {"instrument_class": cls})
    return _answer("rules_based", "no", "Only rules-based index funds can be replicated: an actively managed fund's "
                   "holdings are a manager's decisions, disclosed with a lag, so a copy is always behind.",
                   {"instrument_class": cls})


async def q2_rebuildable(conn, org_id, src: RM.Sources) -> dict:
    if src.snapshot is None:
        return _answer("rebuildable", "unknown", "unknown — needs a live holdings snapshot",
                       missing=["a live holdings snapshot of the fund"])
    by_type: dict[str, Decimal] = {}
    for r in src.rows:
        by_type[r["asset_type"]] = by_type.get(r["asset_type"], ZERO) + (Decimal(str(r["weight"])) if r["weight"]
                                                                          is not None else ZERO)
    ex_cash = sum((v for k, v in by_type.items() if k != "cash"), ZERO)
    non_equity = by_type.get("future", ZERO) + by_type.get("other", ZERO)
    threshold, t_origin = await setting(conn, org_id, "replication.viability.max_non_equity_weight")
    stale_days, s_origin = await setting(conn, org_id, "replication.viability.nport_stale_days")
    as_of = src.snapshot["as_of"]
    age = (_today() - as_of).days
    nport = src.snapshot["source_key"] == "sec_nport"
    stale = nport and age > int(stale_days)
    share = (non_equity / ex_cash) if ex_cash > 0 else ZERO
    inputs = {"snapshot_id": str(src.snapshot["id"]), "as_of": as_of.isoformat(), "age_days": age,
              "source_key": src.snapshot["source_key"], "nport": nport, "nport_stale": stale,
              "nport_stale_days": str(stale_days), "nport_stale_days_origin": s_origin,
              "weight_by_type": {k: str(v) for k, v in sorted(by_type.items())},
              "non_equity_share": str(share), "max_non_equity_weight": str(threshold),
              "max_non_equity_weight_origin": t_origin}
    warn = (f" The holdings are from an N-PORT filing as of {as_of.isoformat()}, {age} days old — older than "
            f"{int(stale_days)} days, so the fund may have moved since.") if stale else ""
    if ex_cash <= 0:
        return _answer("rebuildable", "unknown", "unknown — the snapshot carries no non-cash weight" + warn, inputs,
                       ["holdings with weights"])
    if share > threshold:
        equity = by_type.get("equity", ZERO)
        core = (f" A core-only replica of the stock sleeve ({M.U.pct_text(equity / ex_cash)} of the fund) is "
                "possible.") if equity > 0 else ""
        return _answer("rebuildable", "no", f"{M.U.pct_text(share)} of the fund is in futures or other non-stock "
                       f"instruments — more than {M.U.pct_text(threshold)}.{core}{warn}", inputs)
    return _answer("rebuildable", "yes", f"{M.U.pct_text(ONE_MINUS(share))} of the fund's invested weight is in "
                   f"stocks; the rest ({M.U.pct_text(share)}) is within the {M.U.pct_text(threshold)} allowance."
                   + warn, inputs)


def ONE_MINUS(d: Decimal) -> Decimal:  # noqa: N802 — reads as the formula
    return Decimal(1) - d


async def latest_prices(conn, org_id, gsids: list[str], *, today: date, max_age_days: int) -> dict:
    """marketdata1: each constituent's end-of-day price from the security-data
    layer (internal use; the rank: custodian valuations of the org's accounts,
    approved file uploads, adviser / platform-entered prices). {gsid: Result};
    a Result that is not found carries the stale candidate (if any) in
    ``tried``, so the "older than N days" message below is unchanged."""
    from services import security_data as SD

    if not gsids:
        return {}
    return await SD.get_bulk(conn, org_id, "eod_price", gsids, as_of=today, purpose="internal",
                             max_age_days=max_age_days)


def _stale_as_of(res) -> date | None:
    """The newest stale candidate's date (the walk skipped it as too old)."""
    dates = [date.fromisoformat(t["as_of"]) for t in (res.tried if res else []) if t.get("as_of")]
    return max(dates) if dates else None


def minimum_account(priced: list[tuple[Decimal, Decimal]], *, unit: Decimal, tracking_target: Decimal) -> dict:
    """``priced`` [(target weight, price)]. Exact, then rounded UP to the cent."""
    hold = max((p * unit / w for w, p in priced), default=ZERO)
    track = sum((p * unit for _, p in priced), ZERO) / tracking_target
    need = max(hold, track)
    return {"hold_every_position": format(hold, "f"), "tracking_bound": format(track, "f"), "exact": format(need, "f"),
            "minimum": str(need.quantize(CENT, rounding=ROUND_CEILING)),
            "binding": "hold_every_position" if hold >= track else "tracking_target"}


async def q3_account_size(conn, org_id, src: RM.Sources, *, custodian_code: str, account_size: Decimal,
                          p: RM.BuildParams) -> dict:
    if src.snapshot is None:
        return _answer("account_size", "unknown", "unknown — needs a live holdings snapshot",
                       missing=["a live holdings snapshot of the fund"])
    try:
        c = RM.construct(src.rows, p)
    except M.ModelError as exc:
        return _answer("account_size", "unknown", f"unknown — {exc}", missing=["holdings that can be built"])
    max_age, a_origin = await setting(conn, org_id, "replication.viability.price_max_age_days")
    tracking, t_origin = await setting(conn, org_id, "replication.viability.tracking_target")
    inc, i_origin = await setting(conn, org_id, "replication.viability.fractional_increment")
    held = [x for x in c.positions if x["resolution_state"] != "cash"]
    today = _today()
    prices = await latest_prices(conn, org_id, [x["global_security_id"] for x in held if x["global_security_id"]],
                                 today=today, max_age_days=int(max_age))
    missing, priced, oldest, sources = [], [], None, {}
    for x in held:
        label = x["raw_ticker"] or x["raw_name"] or x["raw_cusip"]
        if not x["global_security_id"]:
            missing.append({"label": label, "why": "unresolved — no security to price"})
            continue
        hit = prices.get(x["global_security_id"])
        stale_on = None if hit is None or hit.found else _stale_as_of(hit)
        if hit is None or (not hit.found and stale_on is None):
            missing.append({"label": label, "why": "no close price"})
        elif not hit.found:
            missing.append({"label": label, "why": f"latest close is {stale_on.isoformat()} — older than "
                                                   f"{int(max_age)} days"})
        else:
            priced.append((Decimal(str(x["target_weight"])), Decimal(str(hit.value))))
            oldest = hit.as_of if oldest is None or hit.as_of < oldest else oldest
            s_ = sources.setdefault(hit.provider_key, {"provider": hit.provider_key, "name": hit.provider_name,
                                                       "count": 0, "oldest_as_of": hit.as_of.isoformat()})
            s_["count"] += 1
            s_["oldest_as_of"] = min(s_["oldest_as_of"], hit.as_of.isoformat())
    frac = await CC.capability(conn, custodian_code, "fractional_shares")
    fractional = CC.available(frac)
    inputs = {"constituents": len(held), "priced": len(priced), "price_max_age_days": str(max_age),
              "price_max_age_days_origin": a_origin, "tracking_target": str(tracking),
              "tracking_target_origin": t_origin, "fractional_shares": frac.value,
              "fractional_evidence": frac.evidence, "account_size": str(account_size), "method": p.method,
              "top_n": p.top_n, "price_data_type": "eod_price",
              "price_sources": sorted(sources.values(), key=lambda v: v["provider"])}
    if missing:
        return _answer("account_size", "unknown",
                       f"unknown — needs prices for {len(missing)} of {len(held)} constituents (a close no older "
                       f"than {int(max_age)} days); prices are never estimated.",
                       inputs, [f"price: {m['label']} ({m['why']})" for m in missing[:50]]
                       + ([f"… and {len(missing) - 50} more"] if len(missing) > 50 else []))
    unit = inc if fractional else Decimal(1)
    inputs.update(unit=str(unit), unit_kind="fractional increment" if fractional else "one whole share",
                  unit_origin=i_origin if fractional else "rule", oldest_price_date=oldest.isoformat() if oldest
                  else None)
    m = minimum_account(priced, unit=unit, tracking_target=tracking)
    inputs["minimum_account"] = m
    shares = "fractional shares" if fractional else "whole shares"
    if account_size >= Decimal(m["minimum"]):
        return _answer("account_size", "yes", f"With {shares}, every position can be held within the "
                       f"{M.U.pct_text(tracking)} tracking target from ${m['minimum']}; this account is "
                       f"${_cents(account_size)}.", inputs)
    return _answer("account_size", "no", f"With {shares}, holding every position within the {M.U.pct_text(tracking)} "
                   f"tracking target needs at least ${m['minimum']}; this account is ${_cents(account_size)}.", inputs)


def q2_options(src: RM.Sources) -> dict:
    """rep8: a buffer / income ETF's holdings file shows its options only as
    'other' rows (no strike or expiry) — the legs come from the approved terms,
    so the question is whether those terms are approved."""
    from services import options_sleeve as OS

    kind = OS.KIND_FOR_CLASS[src.entry["instrument_class"]]
    miss = OS.missing_terms(src.terms, kind)
    inputs = {"instrument_class": src.entry["instrument_class"],
              "terms_version_id": str(src.terms["id"]) if src.terms else None,
              "snapshot_as_of": src.snapshot["as_of"].isoformat() if src.snapshot else None}
    if miss:
        return _answer("rebuildable", "unknown", "unknown — the options legs come from the approved terms, and these "
                       "are missing: " + ", ".join(miss), inputs, miss)
    return _answer("rebuildable", "yes", "Yes — the underlying as the core sleeve plus listed (FLEX) index options "
                   "built from the approved terms' legs; the holdings file shows the fund's options only as 'other' "
                   "rows without strikes, so it is not the source of the legs.", inputs)


async def q4_custodian(conn, custodian_code: str, instrument_class: str | None = None) -> dict:
    api = await CC.capability(conn, custodian_code, "equity_trading_api")
    frac = await CC.capability(conn, custodian_code, "fractional_shares")
    label = CC.CUSTODIAN_LABELS.get(custodian_code, custodian_code)
    inputs = {"custodian_code": custodian_code,
              "equity_trading_api": {"value": api.value, "evidence": api.evidence,
                                     "as_of": api.as_of.isoformat() if api.as_of else None},
              "fractional_shares": {"value": frac.value, "evidence": frac.evidence,
                                    "as_of": frac.as_of.isoformat() if frac.as_of else None},
              "legs": "stocks only (futures and other instruments are not held — see question 2)"}
    missing = [f"evidence of {k} at {label}" for k, c in (("equity_trading_api", api), ("fractional_shares", frac))
               if c.value == "unknown"]
    opt = ""
    if instrument_class in OPTIONS_CLASSES:       # rep8 — the options legs: options level / FLEX; never blocks the core
        from services import options_sleeve as OS

        via = await OS.options_viability(conn, custodian_code, OS.KIND_FOR_CLASS[instrument_class])
        inputs["options"] = via
        inputs["legs"] = "the core (stocks / the ETF) plus an options package"
        missing = missing + via["missing"]
        opt = f" Options legs: {via['note']}."
    if CC.available(api):
        return _answer("custodian", "yes", f"{label} takes stock trades through its trading API.{opt}", inputs,
                       missing)
    return _answer("custodian", "yes", f"Yes, via execution instructions: {label}'s equity trading API is "
                   f"{'not evidenced (unknown counts as not available)' if api.value == 'unknown' else api.value}, "
                   f"so trades go out as instructions a person places — the manual path.{opt}", inputs, missing)


async def strategy_fee(conn, org_id, model_id, account_size: Decimal) -> tuple[Decimal | None, dict, list[str]]:
    from services.fee_calc import apply_tiers
    from services.fee_calc_inputs import DEFAULT_TIER_METHOD, FeeTierInput

    m = await conn.fetchrow(f"SELECT default_fee_schedule_id FROM {M.T_MODEL} WHERE id = $1::uuid", str(model_id))
    if m is None or m["default_fee_schedule_id"] is None:
        return None, {"model_id": str(model_id)}, ["a default fee schedule on the model (it sets the strategy fee)"]
    fs = await conn.fetchrow("SELECT id, code, version, name, status, tier_method, minimum_fee, maximum_fee "
                             "FROM public.fee_schedules WHERE id = $1 AND org_id = $2::uuid", m["default_fee_schedule_id"],
                             str(org_id))
    if fs is None:
        return None, {"model_id": str(model_id)}, ["the model's default fee schedule (not readable)"]
    tiers = [FeeTierInput(tier_seq=int(r["tier_seq"]), lower_bound=r["lower_bound"], upper_bound=r["upper_bound"],
                          rate_bps=r["rate_bps"], flat_amount=r["flat_amount"], id=str(r["id"]),
                          fee_schedule_id=str(fs["id"]))
             for r in await conn.fetch("SELECT * FROM public.fee_schedule_tiers WHERE fee_schedule_id = $1 "
                                       "ORDER BY tier_seq", fs["id"])]
    method = fs["tier_method"] or DEFAULT_TIER_METHOD
    inputs = {"fee_schedule_id": str(fs["id"]), "code": fs["code"], "version": fs["version"], "name": fs["name"],
              "status": fs["status"], "tier_method": method, "tiers": len(tiers),
              "minimum_fee": str(fs["minimum_fee"]) if fs["minimum_fee"] is not None else None,
              "maximum_fee": str(fs["maximum_fee"]) if fs["maximum_fee"] is not None else None,
              "note": "tiers only (services.fee_calc.apply_tiers) — an illustration, never a fee run or a bill; "
                      "a schedule minimum / maximum is period-scoped and not applied here"}
    if not tiers:
        return None, inputs, ["tiers on the model's default fee schedule"]
    try:
        total, slices, assumptions = apply_tiers(account_size, tiers, method)
    except Exception as exc:  # noqa: BLE001 — an invalid tier set is a reason, never a number
        return None, inputs, [f"a valid tier set on the fee schedule ({exc})"]
    inputs.update(slices=slices, assumptions=list(assumptions))
    return total, inputs, []


async def trading_cost(conn, org_id, src: RM.Sources, account_size: Decimal) -> tuple[Decimal | None, dict, list[str]]:
    n, n_origin = await setting(conn, org_id, "replication.viability.turnover_snapshots")
    bps, b_origin = await setting(conn, org_id, "replication.viability.trading_cost_bps")
    inputs = {"turnover_snapshots": int(n), "turnover_snapshots_origin": n_origin, "trading_cost_bps": str(bps),
              "trading_cost_bps_origin": b_origin,
              "formula": "annual turnover = Σ snapshot turnover × 365 / days spanned; trading cost = annual turnover "
                         "× 2 sides × account size × bps / 10,000",
              "caveat": "weight changes between snapshots include price moves, so this overstates trading"}
    if src.snapshot is None:
        return None, inputs, ["a live holdings snapshot of the fund"]
    snaps = await conn.fetch(
        "SELECT id, as_of, diff FROM portfolio.replica_holdings_snapshots WHERE fund_global_security_id = $1::uuid "
        "AND source_key = $2 AND status = 'live' AND valid_to IS NULL AND system_to IS NULL "
        "ORDER BY as_of DESC, fetched_at DESC LIMIT $3",
        str(src.entry["global_security_id"]), src.entry["holdings_source_key"], int(n) + 1)
    diffs = [s for s in snaps[:-1] if not (M._loads(s["diff"]) or {}).get("first_snapshot", True)]
    span = (snaps[0]["as_of"] - snaps[-1]["as_of"]).days if len(snaps) >= 2 else 0
    if not diffs or span <= 0:
        return None, inputs, ["at least two live holdings snapshots on different dates (turnover history)"]
    total = sum((Decimal(str((M._loads(s["diff"]) or {}).get("turnover") or "0")) for s in diffs), ZERO)
    annual = total * 365 / span
    cost = annual * 2 * account_size * bps / Decimal(10000)
    inputs.update(diffs=len(diffs), days_spanned=span, turnover_sum=str(total), annual_turnover=str(annual),
                  from_as_of=snaps[-1]["as_of"].isoformat(), to_as_of=snaps[0]["as_of"].isoformat())
    return cost, inputs, []


async def q5_cost(conn, org_id, src: RM.Sources, *, account_size: Decimal, p: RM.BuildParams) -> tuple[dict, list]:
    lines: list[dict] = []
    missing: list[str] = []
    er = RM.term_value(src.terms, "expense_ratio_net_pct")
    fund_cost = None
    if er is None:
        missing.append("the fund's net expense ratio (an approved terms version)")
        lines.append({"key": "fund_expense", "label": COST_LINE_LABELS["fund_expense"], "status": "unknown",
                      "amount": None, "rate_pct": None, "reason": "unknown — needs the approved net expense ratio"})
    else:
        rate = Decimal(str(er))
        fund_cost = account_size * rate / 100
        lines.append({"key": "fund_expense", "label": COST_LINE_LABELS["fund_expense"], "status": "known",
                      "amount": _cents(fund_cost), "exact": str(fund_cost), "rate_pct": str(rate),
                      "reason": f"{rate}% a year on ${_cents(account_size)} (approved terms "
                                f"v{src.terms['version_no']})"})
    existing = await RM.find_model(conn, org_id, src.entry["id"], p)
    fee = None
    if existing is None:
        missing.append("a model for this custodian and method (its default fee schedule sets the strategy fee)")
        lines.append({"key": "strategy_fee", "label": COST_LINE_LABELS["strategy_fee"], "status": "unknown",
                      "amount": None, "reason": "unknown — no model built yet for this custodian and method"})
    else:
        fee, f_inputs, f_missing = await strategy_fee(conn, org_id, existing["model"]["id"], account_size)
        missing += f_missing
        lines.append({"key": "strategy_fee", "label": COST_LINE_LABELS["strategy_fee"],
                      "status": "known" if fee is not None else "unknown",
                      "amount": _cents(fee) if fee is not None else None, "exact": str(fee) if fee is not None else None,
                      "reason": (f"{existing['model']['name']}'s default fee schedule at ${_cents(account_size)}"
                                 if fee is not None else f"unknown — needs {'; '.join(f_missing)}"),
                      "inputs": f_inputs})
    trade, t_inputs, t_missing = await trading_cost(conn, org_id, src, account_size)
    missing += t_missing
    lines.append({"key": "trading_cost", "label": COST_LINE_LABELS["trading_cost"],
                  "status": "known" if trade is not None else "unknown",
                  "amount": _cents(trade) if trade is not None else None,
                  "exact": str(trade) if trade is not None else None,
                  "reason": "annualised turnover × both sides × the org's trading cost" if trade is not None
                  else f"unknown — needs {'; '.join(t_missing)}", "inputs": t_inputs})
    replica = (fee + trade) if fee is not None and trade is not None else None
    lines.append({"key": "replica_total", "label": COST_LINE_LABELS["replica_total"],
                  "status": "known" if replica is not None else "unknown",
                  "amount": _cents(replica) if replica is not None else None,
                  "exact": str(replica) if replica is not None else None,
                  "reason": "strategy fee + trading cost" if replica is not None else "unknown — a part is unknown"})
    lines.append({"key": "tax_benefit", "label": COST_LINE_LABELS["tax_benefit"], "status": "not_estimated",
                  "amount": None, "reason": TAX_LINE_TEXT})
    inputs = {"account_size": str(account_size), "lines": [ln["key"] for ln in lines],
              "tax": "excluded from this answer — the tax engine (rep6) estimates it"}
    if fund_cost is None or replica is None:
        return _answer("cost", "unknown", "unknown — needs " + "; ".join(missing), inputs, missing), lines
    inputs.update(fund_cost=str(fund_cost), replica_cost=str(replica), saving=str(fund_cost - replica))
    if replica < fund_cost:
        return _answer("cost", "yes", f"Before tax, the replica costs ${_cents(replica)} a year against the fund's "
                       f"${_cents(fund_cost)} — ${_cents(fund_cost - replica)} less. The tax benefit is estimated by "
                       "the tax engine (rep6), not here.", inputs), lines
    return _answer("cost", "no", f"Before tax, the replica costs ${_cents(replica)} a year against the fund's "
                   f"${_cents(fund_cost)}. The tax benefit, estimated by the tax engine (rep6), could change this.",
                   inputs), lines


def overall(answers: list[dict]) -> str:
    st = [a["status"] for a in answers]
    if "no" in st:
        return "no"
    if "unknown" in st:
        return "unknown"
    return "yes"


# ── compute / store / read ──────────────────────────────────────────────────
async def compute(pool, *, org_id, actor_id, entry_id, custodian_code: str, account_size=None, method: str = "full",
                  top_n=None) -> dict:
    p = RM.params_from_input(custodian_code=custodian_code, method=method, top_n=top_n)
    async with pool.acquire() as conn:
        actor = await HT.assert_human(conn, actor_id, org_id)
        src = await RM.load_sources(conn, org_id, entry_id)
        if account_size is None:
            size, size_origin = await setting(conn, org_id, "replication.viability.default_account_size")
        else:
            try:
                size = M.U.to_decimal(account_size)
            except ValueError as exc:
                raise M.ModelError(f"account size: {exc}") from None
            size_origin = "request"
        if size is None or size <= 0:
            raise M.ModelError("account size must be above $0")
        size = size.quantize(CENT, rounding=ROUND_HALF_UP)
        a1 = q1_rules_based(src.entry)
        a2 = q2_options(src) if src.entry["instrument_class"] in OPTIONS_CLASSES else await q2_rebuildable(
            conn, org_id, src)
        a3 = await q3_account_size(conn, org_id, src, custodian_code=p.custodian_code, account_size=size, p=p)
        a4 = await q4_custodian(conn, p.custodian_code, src.entry["instrument_class"])
        a5, lines = await q5_cost(conn, org_id, src, account_size=size, p=p)
        answers = [a1, a2, a3, a4, a5]
        model = await RM.find_model(conn, org_id, src.entry["id"], p)
        row = await conn.fetchrow(
            f"""INSERT INTO {T_REP} (org_id, replica_security_id, custodian_code, account_size, method, top_n,
                    snapshot_id, terms_version_id, model_id, answers, cost_lines, overall, computed_by)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::uuid, $8::uuid, $9::uuid, $10::jsonb, $11::jsonb, $12,
                        $13::uuid) RETURNING *""",
            str(org_id), str(src.entry["id"]), p.custodian_code, size, p.method, p.top_n,
            str(src.snapshot["id"]) if src.snapshot else None, str(src.terms["id"]) if src.terms else None,
            model["model"]["id"] if model else None, M._dumps(answers), M._dumps(lines), overall(answers),
            str(actor["id"]))
    out = report_out(dict(row))
    out["account_size_origin"] = size_origin
    return out


def report_out(r: dict) -> dict:
    return {"id": str(r["id"]), "replica_security_id": str(r["replica_security_id"]),
            "custodian_code": r["custodian_code"], "account_size": str(r["account_size"]), "method": r["method"],
            "top_n": r["top_n"], "snapshot_id": str(r["snapshot_id"]) if r["snapshot_id"] else None,
            "terms_version_id": str(r["terms_version_id"]) if r["terms_version_id"] else None,
            "model_id": str(r["model_id"]) if r["model_id"] else None,
            "answers": M._loads(r["answers"]) or [], "cost_lines": M._loads(r["cost_lines"]) or [],
            "overall": r["overall"], "computed_by": str(r["computed_by"]), "computed_at": M._iso(r["computed_at"])}


async def list_reports(conn, org_id, entry_id, limit: int = 50) -> list[dict]:
    rows = await conn.fetch(f"SELECT * FROM {T_REP} WHERE org_id = $1::uuid AND replica_security_id = $2::uuid "
                            "AND valid_to IS NULL AND system_to IS NULL ORDER BY computed_at DESC, id LIMIT $3",
                            str(org_id), str(entry_id), limit)
    return [report_out(dict(r)) for r in rows]


async def get_report(conn, org_id, entry_id, report_id) -> dict:
    try:
        rid = uuid.UUID(str(report_id))
    except ValueError:
        raise M.ModelNotFound("viability report not found") from None
    row = await conn.fetchrow(f"SELECT * FROM {T_REP} WHERE id = $1 AND org_id = $2::uuid AND replica_security_id = "
                              "$3::uuid", rid, str(org_id), str(entry_id))
    if row is None:
        raise M.ModelNotFound("viability report not found")
    return report_out(dict(row))


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"questions": [{"key": k, "label": v} for k, v in QUESTIONS], "answer_statuses": pairs(STATUS_LABELS),
            "cost_lines": pairs(COST_LINE_LABELS), "tax_line": TAX_LINE_TEXT}
