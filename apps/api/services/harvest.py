"""Daily tax-loss harvesting (rep6) — as PROPOSALS a person approves.

THE SCAN (rep5's org_tick on the scheduler path, BEFORE the drift scan; Task 1e)
──────────────────────────────────────────────────────────────────────────────
For every account following a model with ``harvesting_engine = 'hollisworks'``,
once a day (public.harvest_runs: one live run per account per day):
  * CANDIDATES — every open lot at an unrealized loss on the account's own
    valuation (price = position market value / quantity, rep5):
        loss          = (adjusted unit cost − price) × quantity
        trading cost  = market value × the org's bps setting × 2 sides
        net loss      = loss − trading cost
    taken when the net loss is at or above BOTH the account's % threshold (net
    loss ÷ the lot's cost basis) and its $ threshold (link settings; else the
    org settings; else the stated defaults). An unpriced holding is skipped
    ("needs a price"); a lot with no cost basis is skipped (its loss cannot be
    known).
  * VALUE — loss × the lot's COMBINED rate from the subject's APPROVED profile
    (ST when the lot is short-term on the sale date, else LT): an ESTIMATE,
    labelled with the rate and the profile's as-of. No profile → the candidate
    is listed with "not estimated — no tax profile", never a default rate.
  * REPLACEMENT — the first APPROVED substitute that household wash-sale control
    clears (services.wash_sale: not denied with the security, no loss window of
    its own); none → skipped with the reason, or harvested to cash when the
    account allows it (stated). A household buy within 30 days holds the
    harvest back (rule b) — skipped with the clear date.
  * SWAP BACK (setting) — 31 days or more after an approved harvest, a proposal
    to sell the replacement and buy the original back, when the original's buy
    is clear; the sale goes through rep5's plan_sales, so the gain budget and
    the short-term guard apply to it.
  * The gain budget: a harvest realizes only losses — it ADDS room; the
    short-term guard (it defers short-term GAINS) does not apply to a loss sale.
OUTPUT: ONE proposal per account per day, trigger 'harvest', through rep5's
generate (a SYSTEM maker → a review task → a person approves on
approve_proposals); every candidate — taken or skipped, with its reason — is a
row of public.harvest_candidates. An account that already has an open proposal
is not harvested that day (each candidate recorded as 'open_proposal').
``harvesting_engine = 'custodian'`` → no harvest proposals at all (the run says
so); the custodian's realized losses still reach the loss bank through its
lots, and wash-sale control still applies to Hollisworks' own proposals.
NOT ESTIMATED — needs prices: the tracking-error cost of a harvest (no licensed
price history yet; securities_global_prices is empty).

THE CUSTODY CLIFF. Nothing here trades: a harvest is a proposal.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal, localcontext

from services import account_models as AM
from services import rebalance_engine as E
from services import tax_profiles as TP
from services import wash_sale as WS

log = logging.getLogger(__name__)

T_RUN = "public.harvest_runs"
T_CAND = "public.harvest_candidates"
PCT_SETTING = "replication.tax.harvest_loss_pct"
MIN_SETTING = "replication.tax.harvest_loss_min"
COST_SETTING = "replication.tax.trading_cost_bps"
TO_CASH_SETTING = "replication.tax.harvest_to_cash"
SWAP_SETTING = "replication.tax.harvest_swap_back"
DEFAULT_PCT = Decimal("0.05")
DEFAULT_MIN = Decimal("500")
DEFAULT_COST_BPS = Decimal("0")
SWAP_BACK_DAYS = WS.WINDOW_DAYS + 1
SKIP_LABELS = {"below_threshold": "Below the loss thresholds (net of trading cost)",
               "needs_price": "Needs a price — the account cannot value the holding",
               "no_cost_basis": "No cost basis — its loss cannot be known",
               "recent_household_buy": "A household buy within 30 days (wash sale) — held back",
               "no_clear_replacement": "No approved substitute is clear of wash-sale windows",
               "open_proposal": "The account already has an open proposal",
               "proposal_failed": "The proposal could not be generated",
               "swap_back_not_clear": "Swap back: the original is not yet clear to buy",
               "swap_back_gain_budget": "Swap back: the gain budget or short-term guard holds the sale",
               "nothing_to_swap": "Swap back: the replacement is no longer held"}
RUN_OUTCOME_LABELS = {"proposed": "A harvest proposal was made", "nothing_to_harvest": "Nothing to harvest",
                      "skipped": "Not harvested today", "failed": "Failed"}
KIND_LABELS = {"harvest": "Harvest", "swap_back": "Swap back"}
Q = Decimal("0.0000000001")


def money(v: Decimal) -> Decimal:
    return v.quantize(E.CENT, rounding=ROUND_HALF_EVEN)


# ── Settings ────────────────────────────────────────────────────────────────
async def load_settings(conn, org_id, link: dict) -> dict:
    from services.rebalance_proposals import _setting

    pct = link.get("harvest_loss_pct")
    pct_src = "account"
    if pct is None:
        pct = await _setting(conn, org_id, PCT_SETTING, None, lo=Decimal("0.0000000001"), hi=Decimal("0.9999999999"))
        pct_src = "org_setting"
        if pct is None:
            pct, pct_src = DEFAULT_PCT, "default"
    mn = link.get("harvest_loss_min")
    mn_src = "account"
    if mn is None:
        mn = await _setting(conn, org_id, MIN_SETTING, None, lo=Decimal(0))
        mn_src = "org_setting"
        if mn is None:
            mn, mn_src = DEFAULT_MIN, "default"
    bps = await _setting(conn, org_id, COST_SETTING, None, lo=Decimal(0), hi=Decimal(10000))
    bps_src = "org_setting" if bps is not None else "default"
    bps = DEFAULT_COST_BPS if bps is None else bps

    async def flag(own, key):
        if own is not None:
            return bool(own), "account"
        v = await WS._setting(conn, org_id, key, None)
        return (v is True, "org_setting") if isinstance(v, bool) else (False, "default")

    to_cash, tc_src = await flag(link.get("harvest_to_cash"), TO_CASH_SETTING)
    swap, sw_src = await flag(link.get("harvest_swap_back"), SWAP_SETTING)
    return {"loss_pct": Decimal(str(pct)), "loss_pct_source": pct_src, "loss_min": Decimal(str(mn)),
            "loss_min_source": mn_src, "trading_cost_bps": Decimal(str(bps)), "trading_cost_source": bps_src,
            "to_cash": to_cash, "to_cash_source": tc_src, "swap_back": swap, "swap_back_source": sw_src}


def settings_out(s: dict) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in s.items()}


# ── The arithmetic (pure) ───────────────────────────────────────────────────
def evaluate_lot(*, quantity: Decimal, unit_cost: Decimal, price: Decimal, term: str, settings: dict,
                 rates: dict | None) -> dict:
    """One lot's harvest numbers, exact. ``rates`` from tax_profiles.combined_rates."""
    cost_basis = unit_cost * quantity
    mv = price * quantity
    unrealized = (price - unit_cost) * quantity
    loss = -unrealized
    tcost = mv * settings["trading_cost_bps"] / Decimal(10000) * 2
    net_loss = loss - tcost
    with localcontext() as ctx:
        ctx.prec = 60
        loss_pct = (net_loss / cost_basis).quantize(Q) if cost_basis > 0 else Decimal(0)
    passes_pct = loss_pct >= settings["loss_pct"]
    passes_amount = net_loss >= settings["loss_min"]
    if rates is None:
        rate, value, note = None, None, TP.NO_PROFILE
    elif term not in ("short", "long"):
        rate, value, note = None, None, "not estimated — the holding period is unknown"
    else:
        rate = rates["st"] if term == "short" else rates["lt"]
        value = money(loss * rate) if loss > 0 else None
        note = (f"estimate: loss × the {'short' if term == 'short' else 'long'}-term combined rate {rate} "
                f"({rates['tax_year']} profile, rates as of {rates['as_of']})") if value is not None else \
            "not estimated — not a loss"
    return {"quantity": quantity, "unit_cost": unit_cost, "price": price, "cost_basis": cost_basis,
            "market_value": mv, "unrealized": unrealized, "trading_cost": tcost, "net_loss": net_loss,
            "loss_pct": loss_pct, "term": term, "passes_pct": passes_pct, "passes_amount": passes_amount,
            "rate_used": rate, "est_tax_value": value, "value_note": note}


def first_clear_substitute(key: str, ctx: WS.Context, own: list) -> tuple[dict | None, list[dict]]:
    tried = []
    for cand in ctx.substitutes.get(key, []):
        r = cand["key"]
        if WS.identical(r, key, ctx):
            tried.append({"security_id": r, "label": cand["label"], "why_not": "denied with the security — a denial "
                          "overrides the substitution set"})
            continue
        w = WS.loss_windows(r, ctx, own)
        if w:
            tried.append({"security_id": r, "label": cand["label"], "why_not": "its own household loss window runs "
                          f"until {WS.clear_from(w).isoformat()}"})
            continue
        return cand, tried
    return None, tried


def _qty(dollars: Decimal, price: Decimal, fractional: bool) -> Decimal:
    unit = E.QTY if fractional else E.ONE
    return (dollars / price / unit).to_integral_value(rounding=ROUND_DOWN) * unit


def _buy(key, label, dollars: Decimal, px: dict, fractional: bool, reason: str) -> E.TradeOut:
    hit = px.get(key)
    if hit is not None:
        price, as_of, src = hit
        q = _qty(dollars, price, fractional)
        if q > 0:
            return E.TradeOut(key, label, "buy", q, None, price, src, as_of, [], E.ZERO, E.ZERO, reason)
    n = dollars.quantize(E.CENT, rounding=ROUND_DOWN)
    return E.TradeOut(key, label, "buy", None, n, None, "none", None, [], E.ZERO, E.ZERO,
                      reason + f" — no price for it, so it is bought by amount (${n})")


# ── The scan ────────────────────────────────────────────────────────────────
def _cand(account_id, h: E.Holding, lot, kind="harvest", **kw) -> dict:
    base = {"kind": kind, "tax_lot_id": lot.id if lot else None, "global_security_id": h.key, "label": h.label,
            "quantity": lot.quantity if lot else h.quantity, "unit_cost": lot.unit_cost if lot else None,
            "price": h.price, "price_as_of": h.price_as_of,
            "price_source": "account_position" if h.price is not None else "none", "cost_basis": None,
            "market_value": None, "unrealized": None, "trading_cost": None, "net_loss": None, "loss_pct": None,
            "term": None, "passes_pct": None, "passes_amount": None, "rate_used": None, "est_tax_value": None,
            "value_note": "not estimated", "replacement_security_id": None, "replacement_note": None,
            "wash_sale": {}, "outcome": "skipped", "skip_reason": None, "skip_detail": None,
            "original_candidate_id": None}
    base.update(kw)
    return base


async def _write_run(conn, *, org_id, run_id, account_id, today, engine, outcome, reason, settings, profile, rates,
                     valuation_as_of, proposal_id, cands: list[dict]) -> None:
    taken = sum(1 for c in cands if c["outcome"] == "proposed")
    await conn.execute(
        f"""INSERT INTO {T_RUN} (id, org_id, account_id, run_date, engine, outcome, reason, settings, tax_profile_id,
                rates, valuation_as_of, proposal_id, candidates, taken)
            VALUES ($1, $2::uuid, $3::uuid, $4, $5, $6, $7, $8::jsonb, $9::uuid, $10::jsonb, $11, $12::uuid, $13, $14)""",
        run_id, str(org_id), str(account_id), today, engine, outcome, reason[:2000], TP._dumps(settings_out(settings)),
        str(profile["id"]) if profile else None, TP._dumps(TP.rates_out(rates)), valuation_as_of,
        str(proposal_id) if proposal_id else None, len(cands), taken)
    for c in cands:
        await conn.execute(
            f"""INSERT INTO {T_CAND} (id, org_id, run_id, account_id, kind, tax_lot_id, global_security_id, quantity,
                    unit_cost, price, price_as_of, price_source, cost_basis, market_value, unrealized, trading_cost,
                    net_loss, loss_pct, term, threshold_pct, threshold_amount, passes_pct, passes_amount, rate_used,
                    est_tax_value, value_note, replacement_security_id, replacement_note, wash_sale, outcome,
                    skip_reason, skip_detail, proposal_id, original_candidate_id)
                VALUES ($1, $2::uuid, $3, $4::uuid, $5, $6::uuid, $7::uuid, $8, $9, $10, $11, $12, $13, $14, $15, $16,
                        $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27::uuid, $28, $29::jsonb, $30, $31, $32,
                        $33::uuid, $34::uuid)""",
            c.get("id") or uuid.uuid4(), str(org_id), run_id, str(account_id), c["kind"], c["tax_lot_id"],
            c["global_security_id"], _q(c["quantity"]), _q(c["unit_cost"]), _q(c["price"]), c["price_as_of"],
            c["price_source"], _q(c["cost_basis"]), _q(c["market_value"]), _q(c["unrealized"]), _q(c["trading_cost"]),
            _q(c["net_loss"]), c["loss_pct"], c["term"], settings["loss_pct"], settings["loss_min"], c["passes_pct"],
            c["passes_amount"], c["rate_used"], c["est_tax_value"], c["value_note"], c["replacement_security_id"],
            c["replacement_note"], TP._dumps(c["wash_sale"] or {}), c["outcome"], c["skip_reason"],
            (c["skip_detail"] or None) and str(c["skip_detail"])[:2000],
            str(proposal_id) if (proposal_id and c["outcome"] == "proposed") else None, c["original_candidate_id"])


def _q(v):
    return None if v is None else Decimal(str(v)).quantize(E.QTY)


async def scan_account(pool, *, org_id, account_id, today: date | None = None) -> dict:
    """One account's daily harvest: candidates → (maybe) one harvest proposal."""
    from services import rebalance_proposals as RP
    from services.custodian_capabilities import available, capability
    from services.custody.registry import trade_custodian_key

    today = today or date.today()
    run_id = uuid.uuid4()
    async with pool.acquire() as conn:
        account = await AM.load_account(conn, org_id, account_id)
        link = await AM.active_link(conn, account["id"])
        if link is None or AM.is_closed(account, today):
            return {"action": "none", "reason": "no active link"}
        done = await conn.fetchval(f"SELECT id FROM {T_RUN} WHERE account_id = $1::uuid AND run_date = $2 "
                                   "AND valid_to IS NULL AND system_to IS NULL", str(account["id"]), today)
        if done:
            return {"action": "none", "reason": "already scanned today", "run_id": str(done)}
        settings = await load_settings(conn, org_id, link)
        profile = await TP.profile_for_account(conn, org_id, account, today.year)
        rates = TP.combined_rates(profile)
        hold = await AM.load_holdings(conn, org_id, account["id"])
        engine = link["harvesting_engine"]

        async def finish(outcome, reason, cands=(), proposal_id=None):
            async with conn.transaction():
                await _write_run(conn, org_id=org_id, run_id=run_id, account_id=account["id"], today=today,
                                 engine=engine, outcome=outcome, reason=reason, settings=settings, profile=profile,
                                 rates=rates, valuation_as_of=hold["valuation_as_of"], proposal_id=proposal_id,
                                 cands=list(cands))
            return {"action": "harvest" if proposal_id else "none", "reason": reason, "run_id": str(run_id),
                    "proposal_id": str(proposal_id) if proposal_id else None, "candidates": len(cands),
                    "taken": sum(1 for c in cands if c["outcome"] == "proposed")}

        if engine == "custodian":
            return await finish("skipped", "the custodian's own tax-loss harvesting is on for this account — "
                                "Hollisworks makes no harvest proposals; its realized losses reach the loss bank "
                                "through the custodian's lots, and wash-sale control still applies to Hollisworks' "
                                "own proposals")
        cls = WS.tax_class(account)
        if cls == "tax_advantaged":
            return await finish("nothing_to_harvest", "an IRA / Roth realizes no deductible loss — nothing to harvest")
        model = await AM.model_brief(conn, org_id, link["model_id"])
        if not model["live_version_id"]:
            return await finish("skipped", f"{model['name']} has no approved version")
        cur = await RP.open_proposal(conn, account["id"])
        ctx = await WS.load_context(conn, org_id, account, today)
        key = trade_custodian_key(account.get("custodian_code")) or "manual"
        fractional = available(await capability(conn, key, "fractional_shares"))
        ytd = await RP.realized_ytd(conn, org_id, account["id"], today)
        stale = await RP._setting(conn, org_id, RP.STALE_SETTING, RP.DEFAULT_STALE_DAYS, lo=0, hi=366, integer=True)
        swap_prior = await _swap_prior(conn, org_id, account["id"], today) if settings["swap_back"] else []
        want_px = {c["key"] for v in ctx.substitutes.values() for c in v} | {p["original"] for p in swap_prior}
        px = {h.key: (h.price, h.price_as_of, h.price_source) for h in hold["holdings"] if h.price is not None}
        px.update(await RP._unheld_prices(conn, sorted(want_px - set(px)), today - timedelta(days=stale)))

    cands: list[dict] = []
    picks: dict[str, list[dict]] = {}
    held_by = {h.key: h for h in hold["holdings"]}
    for h in hold["holdings"]:
        for lot in h.lots:
            if h.price is None:
                cands.append(_cand(account["id"], h, lot, skip_reason="needs_price",
                                   value_note="not estimated — needs a price",
                                   skip_detail=f"{h.label}: the account cannot value it — needs a price"))
                continue
            if lot.unit_cost is None:
                if lot.id is not None:
                    cands.append(_cand(account["id"], h, lot, skip_reason="no_cost_basis",
                                       value_note="not estimated — no cost basis",
                                       skip_detail="the lot has no cost basis, so its loss cannot be known"))
                continue
            term = E.term_of(lot, today)
            ev = evaluate_lot(quantity=lot.quantity, unit_cost=lot.unit_cost, price=h.price, term=term,
                              settings=settings, rates=rates)
            if ev["unrealized"] >= 0:
                continue          # not at a loss — not a candidate
            c = _cand(account["id"], h, lot, **ev)
            if not (ev["passes_pct"] and ev["passes_amount"]):
                c.update(skip_reason="below_threshold", skip_detail=(
                    f"net loss {money(ev['net_loss'])} ({E.pct(ev['loss_pct'])} of cost, after "
                    f"{money(ev['trading_cost'])} est. trading cost) vs thresholds {E.pct(settings['loss_pct'])} and "
                    f"${money(settings['loss_min'])} — needs both"))
                cands.append(c)
                continue
            r = WS.sale_restriction(h.key, account_class=cls, ctx=ctx)
            if r:
                c.update(skip_reason="recent_household_buy", skip_detail=r["reason"],
                         wash_sale={"outcome": "deferred", "rule": "b", "until": r["until"].isoformat(),
                                    "matches": r["matches"], "reason": r["reason"]})
                cands.append(c)
                continue
            picks.setdefault(h.key, []).append(c)
            cands.append(c)
    own = [WS.Event(k, "sell", today, today, True, str(account["id"]), account["account_number_masked"], cls,
                    "this_proposal") for k in picks]
    trades: list[tuple] = []
    for k, group in picks.items():
        h = held_by[k]
        cand, tried = first_clear_substitute(k, ctx, own)
        if cand is None and not settings["to_cash"]:
            for c in group:
                c.update(skip_reason="no_clear_replacement",
                         skip_detail="no approved substitute is clear" + (
                             ": " + "; ".join(f"{t['label']} — {t['why_not']}" for t in tried) if tried else
                             " (no approved substitution set for this security)"),
                         wash_sale={"outcome": "clear", "tried": tried})
            continue
        q = sum((c["quantity"] for c in group), E.ZERO)
        lots = [{"lot_id": c["tax_lot_id"], "quantity": c["quantity"], "unit_cost": c["unit_cost"], "term": c["term"],
                 "est_gain": c["unrealized"], "acquired_date": next((x.acquired.isoformat() for x in h.lots
                                                                     if x.id == c["tax_lot_id"] and x.acquired), None)}
                for c in group]
        st = sum((c["unrealized"] for c in group if c["term"] != "long"), E.ZERO)
        lt = sum((c["unrealized"] for c in group if c["term"] == "long"), E.ZERO)
        vals = [c["est_tax_value"] for c in group if c["est_tax_value"] is not None]
        value_txt = (f"est. tax value ${money(sum(vals, E.ZERO))} (estimate, profile rates)" if vals and
                     len(vals) == len(group) else TP.NO_PROFILE if rates is None else "value partly not estimated")
        reason = (f"Tax-loss harvest: realizes an estimated loss of ${money(-(st + lt))} "
                  f"(short-term ${money(-st)}, long-term ${money(-lt)}) — {value_txt}")
        sell = E.TradeOut(k, h.label, "sell", q, None, h.price, h.price_source, h.price_as_of, lots, st, lt, reason)
        trades.append((sell, {"outcome": "clear", "rule": None, "security_id": k, "label": h.label, "matches": [],
                              "reason": "no household buy of this security, or of a pair denied with it, within 30 "
                                        "days"}))
        proceeds = q * h.price
        if cand is not None:
            buy = _buy(cand["key"], cand["label"], proceeds, px, fractional,
                       f"Harvest replacement for {h.label}: {cand['label']} — an approved substitute "
                       f"({cand['reason']}), not substantially identical")
            res = WS.evaluate_buy(cand["key"], account_class=cls, ctx=ctx, own=own)
            trades.append((buy, {**res, "harvest_replacement_for": {"security_id": k, "label": h.label}}))
            for c in group:
                c.update(outcome="proposed", replacement_security_id=cand["key"],
                         replacement_note=f"{cand['label']} — {cand['reason']} (set v{cand.get('version_no')})",
                         wash_sale={"outcome": "clear", "replacement": cand["key"], "tried": tried})
        else:
            for c in group:
                c.update(outcome="proposed", replacement_note="harvested to cash (the account allows it) — no "
                         "approved substitute is clear; the proceeds stay in cash",
                         wash_sale={"outcome": "clear", "to_cash": True, "tried": tried})
    # swap backs (setting): 31+ days after an approved harvest, sell the replacement, buy the original
    harvest_loss = sum((t.est_st_gain + t.est_lt_gain for t, _ in trades if t.side == "sell"), E.ZERO)
    for sp in swap_prior:
        r_key, s_key = sp["replacement"], sp["original"]
        h = held_by.get(r_key)
        swap_lots = [x for x in (h.lots if h else []) if x.id and x.acquired and x.acquired >= sp["run_date"]]
        base = {"kind": "swap_back", "original_candidate_id": sp["candidate_id"], "replacement_security_id": s_key}
        if h is None or h.price is None or not swap_lots:
            cands.append({**_cand(account["id"], h or E.Holding(r_key, ctx.label(r_key), E.ZERO, None, None, None,
                                                                "none"), None, **base),
                          "skip_reason": "nothing_to_swap", "value_note": "not estimated",
                          "skip_detail": "the account no longer holds replacement lots bought on or after the harvest "
                                         f"of {sp['run_date'].isoformat()}"})
            continue
        res = WS.evaluate_buy(s_key, account_class=cls, ctx=ctx, own=own)
        r_hold = WS.sale_restriction(r_key, account_class=cls, ctx=ctx)
        if res["outcome"] != "clear" or r_hold:
            cands.append({**_cand(account["id"], h, None, **base), "skip_reason": "swap_back_not_clear",
                          "value_note": "not estimated",
                          "skip_detail": res["reason"] if res["outcome"] != "clear" else r_hold["reason"],
                          "wash_sale": {"outcome": res["outcome"], "reason": res["reason"]}})
            continue
        sub = E.Holding(h.key, h.label, sum((x.quantity for x in swap_lots), E.ZERO), None, h.price, h.price_as_of,
                        h.price_source, list(swap_lots))
        plan = E.plan_sales([(sub, sub.quantity)], E.SaleSettings(
            today=today, gain_budget=None if link["gain_budget"] is None else Decimal(str(link["gain_budget"])),
            realized_ytd=ytd + harvest_loss, avoid_short_term=link["avoid_short_term"],
            short_term_wait_days=link["short_term_wait_days"], fractional=fractional))
        now = plan.now.get(h.key) or []
        if not now:
            why = "; ".join(sorted({x.reason for x in plan.later})) or "nothing can be sold now"
            cands.append({**_cand(account["id"], h, None, **base), "skip_reason": "swap_back_gain_budget",
                          "value_note": "not estimated", "skip_detail": why})
            continue
        q = sum((p.quantity for p in now), E.ZERO)
        st = sum((p.gain for p in now if p.gain is not None and p.term != "long"), E.ZERO)
        lt = sum((p.gain for p in now if p.gain is not None and p.term == "long"), E.ZERO)
        gain = st + lt
        tax_txt = TP.NO_PROFILE if rates is None else (
            f"est. tax on the gain ${money(max(st, E.ZERO) * rates['st'] + max(lt, E.ZERO) * rates['lt'])} (estimate)")
        sell = E.TradeOut(h.key, h.label, "sell", q, None, h.price, h.price_source, h.price_as_of,
                          [{"lot_id": p.lot_id, "quantity": p.quantity, "unit_cost": p.unit_cost, "term": p.term,
                            "est_gain": p.gain, "acquired_date": p.acquired.isoformat() if p.acquired else None}
                           for p in now], st, lt,
                          f"Swap back: sell the harvest replacement {h.label} ({(today - sp['run_date']).days} days "
                          f"after the harvest) — realizes an estimated ${money(gain)}; {tax_txt}")
        trades.append((sell, {"outcome": "clear", "rule": None, "security_id": h.key, "label": h.label,
                              "matches": [], "reason": "no household buy of the replacement within 30 days"}))
        buy = _buy(s_key, ctx.label(s_key), q * h.price, px, fractional,
                   f"Swap back: buy the original {ctx.label(s_key)} again — clear of every household wash-sale window")
        trades.append((buy, {**res, "swap_back_of": {"security_id": h.key, "label": h.label}}))
        if plan.later:
            trades[-2][1]["reason"] += "; part waits (gain budget / short-term guard): " + "; ".join(
                sorted({x.reason for x in plan.later}))
        cands.append({**_cand(account["id"], h, None, **base), "quantity": q, "unrealized": gain,
                      "term": "short" if lt == 0 else ("long" if st == 0 else "unknown"), "outcome": "proposed",
                      "value_note": f"swap back — {tax_txt}",
                      "replacement_note": f"back to the original {ctx.label(s_key)}",
                      "wash_sale": {"outcome": "clear", "reason": res["reason"]}})
    if cur is not None:
        for c in cands:
            if c["outcome"] == "proposed":
                c.update(outcome="skipped", skip_reason="open_proposal",
                         skip_detail=f"the account's proposal {cur['id']} ({cur['status']}) is open — one open proposal "
                                     "per account; the harvest waits for tomorrow's scan")
        async with pool.acquire() as conn:
            return await _finish(conn, org_id, run_id, account, today, engine, "skipped",
                                 f"an open proposal ({cur['status']}) already covers this account", settings, profile,
                                 rates, hold, cands, None)
    if not trades:
        why = (f"{len(cands)} candidate(s), none taken" if cands else "no lot is at a loss on the account's own valuation")
        async with pool.acquire() as conn:
            return await _finish(conn, org_id, run_id, account, today, engine, "nothing_to_harvest", why, settings,
                                 profile, rates, hold, cands, None)
    plan = {"run_id": str(run_id), "lines": trades, "settings": settings_out(settings), "rates": TP.rates_out(rates),
            "notes": ["a harvest realizes only losses: it adds room to the gain budget; the short-term guard (it "
                      "defers short-term gains) does not apply to a loss sale",
                      "tracking-error cost of the harvest: " + TP.NEEDS_PRICES]}
    try:
        p = await RP.generate(pool, org_id=org_id, account_id=account["id"], trigger="harvest", maker_kind="system",
                              trigger_ref={"run_id": str(run_id), "candidates": len(cands),
                                           "taken": sum(1 for c in cands if c["outcome"] == "proposed"),
                                           "rates": TP.rates_out(rates)}, today=today, harvest_plan=plan)
    except Exception as exc:  # noqa: BLE001 — the run records the failure; tomorrow's scan tries again
        log.exception("harvest proposal failed for %s", account["id"])
        for c in cands:
            if c["outcome"] == "proposed":
                c.update(outcome="skipped", skip_reason="proposal_failed", skip_detail=str(exc)[:1000])
        async with pool.acquire() as conn:
            return await _finish(conn, org_id, run_id, account, today, engine, "failed", f"the proposal failed: {exc}",
                                 settings, profile, rates, hold, cands, None)
    async with pool.acquire() as conn:
        return await _finish(conn, org_id, run_id, account, today, engine, "proposed",
                             f"one harvest proposal: {sum(1 for c in cands if c['outcome'] == 'proposed')} of "
                             f"{len(cands)} candidate(s) taken", settings, profile, rates, hold, cands, p["id"])


async def _finish(conn, org_id, run_id, account, today, engine, outcome, reason, settings, profile, rates, hold, cands,
                  proposal_id) -> dict:
    async with conn.transaction():
        await _write_run(conn, org_id=org_id, run_id=run_id, account_id=account["id"], today=today, engine=engine,
                         outcome=outcome, reason=reason, settings=settings, profile=profile, rates=rates,
                         valuation_as_of=hold["valuation_as_of"], proposal_id=proposal_id, cands=cands)
    return {"action": "harvest" if proposal_id else "none", "reason": reason, "run_id": str(run_id),
            "proposal_id": str(proposal_id) if proposal_id else None, "candidates": len(cands),
            "taken": sum(1 for c in cands if c["outcome"] == "proposed")}


async def _swap_prior(conn, org_id, account_id, today: date) -> list[dict]:
    """Approved harvests at least 31 days old whose replacement has not been
    swapped back yet — one per (original, replacement), the earliest."""
    rows = await conn.fetch(
        f"""SELECT c.id, c.global_security_id, c.replacement_security_id, r.run_date
            FROM {T_CAND} c JOIN {T_RUN} r ON r.id = c.run_id
            JOIN public.rebalance_proposals p ON p.id = c.proposal_id AND p.system_to IS NULL
            WHERE c.org_id = $1::uuid AND c.account_id = $2::uuid AND c.kind = 'harvest' AND c.outcome = 'proposed'
              AND c.replacement_security_id IS NOT NULL AND p.status IN ('approved', 'executed')
              AND r.run_date <= $3 AND c.valid_to IS NULL AND c.system_to IS NULL
              AND NOT EXISTS (SELECT 1 FROM {T_CAND} s JOIN {T_CAND} o ON o.id = s.original_candidate_id
                              WHERE s.kind = 'swap_back' AND s.outcome = 'proposed' AND s.account_id = c.account_id
                                AND o.global_security_id = c.global_security_id
                                AND o.replacement_security_id = c.replacement_security_id)
            ORDER BY r.run_date, c.id""", str(org_id), str(account_id), today - timedelta(days=SWAP_BACK_DAYS))
    out: dict[tuple, dict] = {}
    for r in rows:
        k = (str(r["global_security_id"]), str(r["replacement_security_id"]))
        out.setdefault(k, {"candidate_id": r["id"], "original": k[0], "replacement": k[1], "run_date": r["run_date"]})
    return list(out.values())


async def compute_for_proposal(conn, org_id, account: dict, link: dict, version_id, plan: dict, *,
                               today: date) -> dict:
    """rep5's generate, for trigger 'harvest': the scan's lines become the
    proposal (same shape as rebalance_proposals.compute returns)."""
    from services import rebalance_proposals as RP

    hold = await AM.load_holdings(conn, org_id, account["id"])
    tg = await RP.target_for(conn, org_id, link, version_id)
    lines = plan["lines"]
    A, before = E.current_weights(hold["holdings"], hold["cash"])
    if A <= 0:
        raise RP.ProposalConflict("the account has no valued holdings or cash")
    target = {k: E.qw(v) for k, v in tg["target"].items()}
    going = [t for t, r in lines if WS.routable(r)]
    after = E.after_weights(hold["holdings"], hold["cash"], going, A)
    st = sum((t.est_st_gain for t in going if t.side == "sell"), E.ZERO)
    lt = sum((t.est_lt_gain for t in going if t.side == "sell"), E.ZERO)
    res = E.BuildResult(account_value=A, cash=hold["cash"], target=target, before=before, after=after,
                        drift_before=E.qw(E.max_drift(before, target)), drift_after=E.qw(E.max_drift(after, target)),
                        trades=going, later=[], warnings=[], notes=plan.get("notes") or [], plan=[], est_st_gain=st,
                        est_lt_gain=lt, total_embedded_gain=E.ZERO, years_to_complete=0, plan_by_year=[])
    labels = dict(tg["labels"])
    for t, _ in lines:
        labels.setdefault(t.key, t.label)
    warnings = [{"code": "harvest_note", "message": n} for n in plan.get("notes") or []]
    rates = plan.get("rates") or {}
    warnings.append({"code": "harvest_note", "message": "tax values are estimates at the profile's combined rates "
                     f"(ST {rates.get('st')}, LT {rates.get('lt')}, as of {rates.get('as_of')})" if rates.get("estimated")
                     else f"tax values: {TP.NO_PROFILE}"})
    thr, thr_src = await RP.drift_threshold(conn, org_id, link, version_id)
    ytd = await RP.realized_ytd(conn, org_id, account["id"], today)
    return {"result": res, "lines": lines, "warnings": warnings, "labels": labels, "holdings": hold,
            "realized_ytd": ytd, "gain_budget": None if link["gain_budget"] is None else Decimal(str(link["gain_budget"])),
            "fractional": None, "threshold": thr, "threshold_source": thr_src, "min_trade": None, "today": today}


# ── Reading ─────────────────────────────────────────────────────────────────
def run_out(r: dict) -> dict:
    return {"id": str(r["id"]), "account_id": str(r["account_id"]), "run_date": r["run_date"].isoformat(),
            "engine": r["engine"], "outcome": r["outcome"], "reason": r["reason"],
            "settings": TP._loads(r["settings"]) or {}, "rates": TP._loads(r["rates"]) or {},
            "valuation_as_of": TP._iso(r["valuation_as_of"]),
            "proposal_id": str(r["proposal_id"]) if r["proposal_id"] else None, "candidates": r["candidates"],
            "taken": r["taken"], "created_at": TP._iso(r["created_at"])}


def candidate_out(c: dict, labels: dict | None = None) -> dict:
    labels = labels or {}
    n = TP._num
    return {"id": str(c["id"]), "run_id": str(c["run_id"]), "account_id": str(c["account_id"]), "kind": c["kind"],
            "tax_lot_id": str(c["tax_lot_id"]) if c["tax_lot_id"] else None,
            "global_security_id": str(c["global_security_id"]),
            "security": labels.get(str(c["global_security_id"])), "quantity": n(c["quantity"]),
            "unit_cost": n(c["unit_cost"]), "price": n(c["price"]), "price_as_of": TP._iso(c["price_as_of"]),
            "price_source": c["price_source"], "cost_basis": n(c["cost_basis"]), "market_value": n(c["market_value"]),
            "unrealized": n(c["unrealized"]), "trading_cost": n(c["trading_cost"]), "net_loss": n(c["net_loss"]),
            "loss_pct": n(c["loss_pct"]), "term": c["term"], "threshold_pct": n(c["threshold_pct"]),
            "threshold_amount": n(c["threshold_amount"]), "passes_pct": c["passes_pct"],
            "passes_amount": c["passes_amount"], "rate_used": n(c["rate_used"]), "est_tax_value": n(c["est_tax_value"]),
            "value_label": TP.ESTIMATE if c["est_tax_value"] is not None else None, "value_note": c["value_note"],
            "replacement_security_id": str(c["replacement_security_id"]) if c["replacement_security_id"] else None,
            "replacement": labels.get(str(c["replacement_security_id"])) if c["replacement_security_id"] else None,
            "replacement_note": c["replacement_note"], "wash_sale": TP._loads(c["wash_sale"]) or {},
            "outcome": c["outcome"], "skip_reason": c["skip_reason"], "skip_detail": c["skip_detail"],
            "proposal_id": str(c["proposal_id"]) if c["proposal_id"] else None,
            "original_candidate_id": str(c["original_candidate_id"]) if c["original_candidate_id"] else None}


async def runs_for(conn, org_id, *, account_id=None, limit: int = 50) -> list[dict]:
    args: list = [str(org_id)]
    sql = f"SELECT * FROM {T_RUN} WHERE org_id = $1::uuid AND valid_to IS NULL AND system_to IS NULL"
    if account_id:
        args.append(TP._uuid(account_id, "account"))
        sql += f" AND account_id = ${len(args)}::uuid"
    args.append(max(1, min(int(limit), 500)))
    sql += f" ORDER BY run_date DESC, created_at DESC LIMIT ${len(args)}"
    return [run_out(dict(r)) for r in await conn.fetch(sql, *args)]


async def candidates_of(conn, org_id, run_id) -> list[dict]:
    rows = [dict(r) for r in await conn.fetch(
        f"SELECT * FROM {T_CAND} WHERE org_id = $1::uuid AND run_id = $2::uuid AND valid_to IS NULL AND system_to IS NULL "
        "ORDER BY outcome DESC, global_security_id, tax_lot_id", str(org_id), TP._uuid(run_id, "run"))]
    labels = await WS.security_labels(conn, {r["global_security_id"] for r in rows}
                                      | {r["replacement_security_id"] for r in rows if r["replacement_security_id"]})
    return [candidate_out(r, labels) for r in rows]


def vocabularies() -> dict:
    def pairs(d):
        return [{"key": k, "label": v} for k, v in d.items()]

    return {"skip_reasons": pairs(SKIP_LABELS), "run_outcomes": pairs(RUN_OUTCOME_LABELS),
            "candidate_kinds": pairs(KIND_LABELS)}
