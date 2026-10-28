"""The options TAX GATE (rep8) — every options proposal runs it before
approval (a pre-check on rep5's approval task: a BLOCK refuses the decision
with the rule named and leaves the task open; a FLAG shows on the proposal).
Every result is stored in ``public.options_tax_gate_results`` with its inputs;
a newer run closes the earlier rows (valid axis).

THE RULES (per option leg unless stated)
──────────────────────────────────────────────────────────────────────────────
section_1256      a broad-based index option traded on an exchange (FLEX
                  included) is a Section 1256 contract: 60% long-term / 40%
                  short-term whatever the holding period, and an open position
                  is marked to market at year-end → FLAG. A single-stock,
                  fund or narrow-based option is not → PASS (ordinary rules).
wash_sale         a leg that ACQUIRES the underlying's exposure (a call bought
                  to open; 26 U.S.C. 1091 counts "a contract or option to
                  acquire") inside a household loss window of the same issuer
                  / index exposure (services.wash_sale, extended so an option
                  is its underlying's exposure — never weakened) → BLOCK. A
                  leg closed at a loss (or a loss that cannot be shown not to
                  be one) while the household bought the exposure within 30
                  days → BLOCK. Both window edges are the wash-sale engine's:
                  day L ± 30 inside, L ± 31 clear.
cash_secured_put  a put WRITTEN on a stock (or index exposure) the household
                  sold at a loss within 30 days is a wash-sale purchase → BLOCK.
straddle          offsetting positions (26 U.S.C. 1092): options on the
                  underlying while the account holds the core. Index (or fund)
                  options against a full stock basket / the ETF are IDENTIFIED
                  as a straddle and treated conservatively, labelled PENDING
                  COUNSEL REVIEW (the open counsel question) → FLAG: loss
                  deferral while the offset is open; holding-period suspension
                  of the core. A proposal that would realize a loss on one leg
                  (or cannot show it does not) while the offset stays open →
                  BLOCK with the rule.
constructive_sale a COLLAR (a long put and a short call on the same
                  underlying and expiry, with no written put below — full
                  downside protection) on an APPRECIATED position whose band
                  (call strike − put strike, % of start) is at or inside the
                  setting ``replication.options.constructive_sale_band``
                  (default 15%) → BLOCK (26 U.S.C. 1259). Appreciation that
                  cannot be shown absent → treated as appreciated. A buffer
                  package (written put below the buffer) is not a collar.
qualified_covered_call  a call written on a single STOCK: exchange-traded,
                  MORE than 30 days to expiry (31 passes, 30 fails), not deep
                  in the money — conservatively, the strike at or above the
                  underlying's quoted price (the strike grid that defines the
                  "lowest qualified benchmark" is not held). No price quote →
                  BLOCK "cannot qualify without a price".

Nothing here guesses a price or a rate. Every block names its rule.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

from services import account_models as AM
from services import human_tasks as HT
from services import options_sleeve as OS
from services import rebalance_proposals as RP
from services import wash_sale as WS

T_GATE = "public.options_tax_gate_results"
BAND_SETTING = "replication.options.constructive_sale_band"
DEFAULT_BAND = Decimal("0.15")
QCC_MIN_DAYS = 30            # "more than 30 days" — statutory, not a setting
RULES = ("section_1256", "wash_sale", "cash_secured_put", "straddle", "constructive_sale", "qualified_covered_call")
RULE_LABELS = {
    "section_1256": "Section 1256 classification", "wash_sale": "Wash sale (household, 61-day window)",
    "cash_secured_put": "Cash-secured put after a loss sale", "straddle": "Straddle (offsetting positions)",
    "constructive_sale": "Constructive sale (collar on an appreciated position)",
    "qualified_covered_call": "Qualified covered call",
}
OUTCOME_LABELS = {"pass": "Pass", "flag": "Flag (shown on the proposal)", "block": "Block (approval refused)"}
PENDING_COUNSEL = "pending counsel review"
ZERO, ONE = Decimal(0), Decimal(1)


def _r(rule: str, outcome: str, reason: str, *, trade_id=None, leg_role=None, pending: bool = False,
       inputs: dict | None = None) -> dict:
    return {"rule": rule, "outcome": outcome, "reason": reason + (f" — {PENDING_COUNSEL}" if pending else ""),
            "pending_counsel": pending, "trade_id": str(trade_id) if trade_id else None, "leg_role": leg_role,
            "inputs": inputs or {}}


def _acquires(leg: dict) -> bool:
    """A leg that acquires the underlying's exposure: a call bought to open or
    a put written to open."""
    if leg["position_effect"] != "open":
        return False
    return (leg["option_type"] == "call" and leg["direction"] == "long") or (
        leg["option_type"] == "put" and leg["direction"] == "short")


# ── the rules (pure) ───────────────────────────────────────────────────────
def rule_1256(leg: dict) -> dict:
    c = leg["contract"]
    ins = {"underlying_kind": c["underlying_kind"], "broad_based_index": c["broad_based_index"],
           "exchange_listed": c["exchange_listed"], "is_flex": c["is_flex"]}
    if c["underlying_kind"] == "index" and c["broad_based_index"] and c["exchange_listed"]:
        return _r("section_1256", "flag",
                  f"Section 1256 contract (broad-based index option, exchange-traded{' FLEX' if c['is_flex'] else ''}): "
                  "gains and losses are 60% long-term / 40% short-term whatever the holding period, and an open "
                  "position is marked to market at year-end (treated as sold at fair value on the last business day "
                  "of the year)", trade_id=leg["trade_id"], leg_role=leg["role"], inputs=ins)
    why = ("a single-stock option" if c["underlying_kind"] == "equity" else "a fund option" if
           c["underlying_kind"] == "fund" else "a narrow-based index option" if not c["broad_based_index"]
           else "not exchange-traded")
    return _r("section_1256", "pass", f"not a Section 1256 contract ({why}): ordinary holding-period rules apply",
              trade_id=leg["trade_id"], leg_role=leg["role"], inputs=ins)


def rule_wash(leg: dict, ctx: WS.Context) -> dict:
    c = leg["contract"]
    key = c["global_security_id"]
    und = ctx.label(c["underlying_global_security_id"])
    put_written = leg["option_type"] == "put" and leg["direction"] == "short" and leg["position_effect"] == "open"
    rule = "cash_secured_put" if put_written else "wash_sale"
    if _acquires(leg):
        wins = WS.loss_windows(key, ctx)
        ins = {"window_from": WS.window(ctx)[0].isoformat(), "window_to": WS.window(ctx)[1].isoformat(),
               "matches": WS._matches(wins, key, ctx)}
        if wins:
            last = max(wins, key=lambda e: e.end)
            what = "Writing a put" if put_written else "Buying a call"
            return _r(rule, "block",
                      f"{what} on {und} is a contract to acquire it, and the household sold {ctx.label(last.key)} at a "
                      f"{'loss' if last.loss else 'possible loss'} on {last.end.isoformat()} ({last.account}) — inside "
                      "30 days either side the loss would be disallowed (26 U.S.C. 1091)",
                      trade_id=leg["trade_id"], leg_role=leg["role"], inputs=ins)
        return _r(rule, "pass", f"no household loss sale of {und} (or an option on it) within 30 days either side",
                  trade_id=leg["trade_id"], leg_role=leg["role"], inputs=ins)
    if leg["position_effect"] == "close":
        buys = WS.matching_buys(key, ctx)
        est = leg.get("est_result")
        ins = {"est_result": None if est is None else str(est), "matches": WS._matches(buys, key, ctx)}
        if buys and (est is None or est < 0):
            return _r("wash_sale", "block",
                      f"closing this leg {'at a loss' if est is not None else '(its loss cannot be shown absent)'} "
                      f"while the household bought {und}'s exposure within 30 days would wash the loss",
                      trade_id=leg["trade_id"], leg_role=leg["role"], inputs=ins)
        return _r("wash_sale", "pass", "no household purchase of the same exposure within 30 days of this close"
                  if not buys else "the close is not at a loss", trade_id=leg["trade_id"], leg_role=leg["role"],
                  inputs=ins)
    return _r("wash_sale", "pass", f"this leg does not acquire {und} (a put bought or a call written)",
              trade_id=leg["trade_id"], leg_role=leg["role"])


def rule_straddle(legs: list[dict], core: dict) -> list[dict]:
    out = []
    held = core["holds_offset"]
    for leg in legs:
        c = leg["contract"]
        pending = c["underlying_kind"] in ("index", "fund")
        ins = {"offset": core["offset_label"], "holds_offset": held, "underlying_kind": c["underlying_kind"]}
        if not held:
            out.append(_r("straddle", "pass", "no offsetting position is held in the account (no core sleeve "
                          "holding)", trade_id=leg["trade_id"], leg_role=leg["role"], inputs=ins))
            continue
        if leg["position_effect"] == "close":
            est = leg.get("est_result")
            ins["est_result"] = None if est is None else str(est)
            if est is None or est < 0:
                out.append(_r("straddle", "block",
                              f"realizing {'a loss' if est is not None else 'a result that cannot be shown not to be a loss'} "
                              f"on this leg while the offsetting {core['offset_label']} stays open — the loss would be "
                              "deferred (26 U.S.C. 1092(a)); close the whole straddle together or wait",
                              trade_id=leg["trade_id"], leg_role=leg["role"], pending=pending, inputs=ins))
                continue
        what = ("index options against the stock basket / ETF" if pending else "options on a stock the account holds")
        out.append(_r("straddle", "flag",
                      f"straddle identified ({what}): a loss on one leg is deferred while the other is open, and the "
                      "core's holding period is suspended while the position is held (26 U.S.C. 1092)",
                      trade_id=leg["trade_id"], leg_role=leg["role"], pending=pending, inputs=ins))
    return out


def rule_constructive_sale(legs: list[dict], core: dict, band_setting: Decimal) -> list[dict]:
    """Proposal-level, per underlying and expiry among the OPENING legs."""
    out = []
    groups: dict[tuple, list[dict]] = {}
    for leg in legs:
        if leg["position_effect"] == "open":
            groups.setdefault((leg["contract"]["underlying_global_security_id"], leg["contract"]["expiry"]), []).append(
                leg)
    for (und, exp), g in groups.items():
        lp = [x for x in g if x["option_type"] == "put" and x["direction"] == "long"]
        sc = [x for x in g if x["option_type"] == "call" and x["direction"] == "short"]
        sp = [x for x in g if x["option_type"] == "put" and x["direction"] == "short"]
        pending = g[0]["contract"]["underlying_kind"] in ("index", "fund")
        ins = {"underlying": core["labels"].get(und, und), "expiry": str(exp), "band_setting": str(band_setting),
               "appreciation": None if core["appreciation"] is None else str(core["appreciation"])}
        if not (lp and sc):
            out.append(_r("constructive_sale", "pass", "no collar: no long put and short call pair on the same "
                          "underlying and expiry", inputs=ins))
            continue
        if sp:
            out.append(_r("constructive_sale", "pass", "not a collar: the written put below the buffer leaves the "
                          "downside under it with the holder, so the risk of loss is not eliminated", inputs=ins))
            continue
        put_k = max(x["strike_pct"] for x in lp)
        call_k = min(x["strike_pct"] for x in sc)
        band = call_k - put_k
        ins.update(put_strike_pct=str(put_k), call_strike_pct=str(call_k), band=str(band))
        if band > band_setting:
            out.append(_r("constructive_sale", "pass", f"collar band {band * 100}% is wider than the "
                          f"{band_setting * 100}% setting", pending=pending, inputs=ins))
            continue
        if not core["holds_offset"]:
            out.append(_r("constructive_sale", "pass", "collar, but no appreciated position is held against it",
                          pending=pending, inputs=ins))
            continue
        appr = core["appreciation"]
        if appr is not None and appr <= 0:
            out.append(_r("constructive_sale", "pass", "collar on a position that is not appreciated (no built-in "
                          "gain to lock in)", pending=pending, inputs=ins))
            continue
        out.append(_r("constructive_sale", "block",
                      f"collar band {band * 100}% is at or inside the {band_setting * 100}% setting on "
                      f"{'an appreciated position' if appr is not None else 'a position that cannot be shown not to be appreciated'}"
                      " — a constructive sale (26 U.S.C. 1259) would realize the gain now", pending=pending,
                      inputs=ins))
    return out


def rule_qcc(leg: dict, *, today: date, price_quote: dict | None) -> dict | None:
    """Only for a call WRITTEN to open."""
    if not (leg["option_type"] == "call" and leg["direction"] == "short" and leg["position_effect"] == "open"):
        return None
    c = leg["contract"]
    if c["underlying_kind"] != "equity":
        return _r("qualified_covered_call", "pass", "not applicable: an index or fund call is not a covered call on "
                  "a stock (Section 1256 / the straddle rule decide its treatment)", trade_id=leg["trade_id"],
                  leg_role=leg["role"])
    days = (c["expiry"] - today).days
    ins = {"days_to_expiry": days, "exchange_listed": c["exchange_listed"], "strike": str(c["strike"]),
           "price": None if price_quote is None else price_quote["value"],
           "price_as_of": None if price_quote is None else price_quote["as_of"]}
    if not c["exchange_listed"]:
        return _r("qualified_covered_call", "block", "not a qualified covered call: it is not traded on an exchange",
                  trade_id=leg["trade_id"], leg_role=leg["role"], inputs=ins)
    if days <= QCC_MIN_DAYS:
        return _r("qualified_covered_call", "block", f"not a qualified covered call: {days} days to expiry — it needs "
                  f"more than {QCC_MIN_DAYS}", trade_id=leg["trade_id"], leg_role=leg["role"], inputs=ins)
    if price_quote is None:
        return _r("qualified_covered_call", "block", "cannot qualify without a price — enter the underlying's price "
                  "quote (deep-in-the-money is judged against it)", trade_id=leg["trade_id"], leg_role=leg["role"],
                  inputs=ins)
    price = Decimal(price_quote["value"])
    if Decimal(str(c["strike"])) < price:
        return _r("qualified_covered_call", "block",
                  f"not a qualified covered call: strike {c['strike']} is below the quoted price {price} "
                  f"(as of {price_quote['as_of']}) — treated as deep in the money (conservative: the strike grid "
                  "that sets the lowest qualified benchmark is not held)", trade_id=leg["trade_id"],
                  leg_role=leg["role"], inputs=ins)
    return _r("qualified_covered_call", "pass", f"qualified covered call: exchange-traded, {days} days to expiry, "
              f"strike at or above the quoted price {price} (as of {price_quote['as_of']})",
              trade_id=leg["trade_id"], leg_role=leg["role"], inputs=ins)


def evaluate(legs: list[dict], *, ctx: WS.Context, core: dict, band_setting: Decimal, today: date,
             price_quotes: dict[str, dict | None]) -> list[dict]:
    out = []
    for leg in legs:
        out.append(rule_1256(leg))
        out.append(rule_wash(leg, ctx))
        q = rule_qcc(leg, today=today, price_quote=price_quotes.get(leg["contract"]["underlying_global_security_id"]))
        if q:
            out.append(q)
    out += rule_straddle(legs, core)
    out += rule_constructive_sale(legs, core, band_setting)
    return out


# ── inputs from the database ────────────────────────────────────────────────
async def _core(conn, org_id, account: dict, p: dict, contracts: dict) -> dict:
    """What the account holds against the options: the non-option holdings
    (the core sleeve) with their market value and lot cost."""
    hold = await AM.load_holdings(conn, org_id, account["id"])
    opt_keys = set((await OS.contracts_by_security(conn, [h.key for h in hold["holdings"]])).keys())
    core = [h for h in hold["holdings"] if h.key not in opt_keys and h.quantity > 0]
    labels = {h.key: h.label for h in hold["holdings"]}
    for c in contracts.values():
        labels.setdefault(str(c["underlying_global_security_id"]), str(c["underlying_global_security_id"]))
    appreciation = ZERO
    for h in core:
        cost = [lot.unit_cost * lot.quantity for lot in h.lots if lot.unit_cost is not None]
        if h.market_value is None or len(cost) != len(h.lots) or not h.lots:
            appreciation = None
            break
        appreciation += h.market_value - sum(cost, ZERO)
    return {"holds_offset": bool(core), "offset_label": ("core sleeve (" + ", ".join(h.label for h in core[:3]) +
                                                         ("…" if len(core) > 3 else "") + ")") if core else "none",
            "appreciation": appreciation if core else None, "labels": labels,
            "core_value": sum((h.market_value for h in core if h.market_value is not None), ZERO)}


async def _est_close_result(conn, org_id, account_id, leg: dict) -> Decimal | None:
    """The realized result of closing a leg: from the account's open option lots
    (cost) and the leg's premium quote (price). Unknown when either is missing."""
    if leg["position_effect"] != "close" or leg.get("price") is None:
        return None
    lots = await conn.fetch(
        """SELECT tl.quantity, tl.original_cost FROM portfolio.tax_lots tl
           JOIN portfolio.positions p ON p.id = tl.position_id AND p.system_to IS NULL
           JOIN portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL
           WHERE tl.org_id = $1::uuid AND p.account_id = $2::uuid AND a.global_security_id = $3::uuid
             AND tl.status = 'open' AND tl.valid_to IS NULL AND tl.system_to IS NULL""",
        str(org_id), str(account_id), str(leg["contract"]["global_security_id"]))
    if not lots or any(r["original_cost"] is None or r["quantity"] is None for r in lots):
        return None
    qty = sum((abs(Decimal(str(r["quantity"]))) for r in lots), ZERO)
    cost = sum((Decimal(str(r["original_cost"])) for r in lots), ZERO)
    n = Decimal(str(leg["contracts"]))
    if qty <= 0 or n > qty:
        return None
    part = cost * n / qty
    value = n * Decimal(str(leg["price"])) * Decimal(str(leg["contract"]["multiplier"]))
    # closing a long sells (proceeds +); closing a short buys back (pays)
    return (value - part) if leg["side"] == "sell" else (-value - part)


async def gather(conn, org_id, p: dict, today: date) -> tuple[list[dict], WS.Context, dict, dict]:
    trades = await RP.trades_of(conn, p["id"])
    account = await AM.load_account(conn, org_id, p["account_id"])
    gsids = [str(t["global_security_id"]) for t in trades]
    contracts = await OS.contracts_by_security(conn, gsids)
    legs = []
    for t in trades:
        ol = RP._loads(t.get("option_leg")) or {}
        c = contracts.get(str(t["global_security_id"]))
        if not ol or c is None:
            continue
        legs.append({"trade_id": t["id"], "role": ol.get("role"), "option_type": c["option_type"],
                     "direction": ol.get("direction"), "position_effect": ol.get("position_effect"),
                     "side": t["side"], "contracts": t["quantity"], "price": t["price"],
                     "strike_pct": Decimal(str(ol["strike_pct"])) if ol.get("strike_pct") is not None else None,
                     "contract": {**c, "global_security_id": str(c["global_security_id"]),
                                  "underlying_global_security_id": str(c["underlying_global_security_id"])}})
    for leg in legs:
        leg["est_result"] = await _est_close_result(conn, org_id, account["id"], leg)
    ctx = await WS.load_context(conn, org_id, account, today, exclude_proposal_ids=[p["id"]])
    await WS.add_exposures(conn, ctx, gsids)
    core = await _core(conn, org_id, account, p, contracts)
    quotes = {}
    for leg in legs:
        u = leg["contract"]["underlying_global_security_id"]
        if leg["contract"]["underlying_kind"] == "equity" and u not in quotes:
            q = await OS.latest_quote(conn, org_id, u, "underlying_price")
            quotes[u] = None if q is None else {"value": str(q["value"]), "as_of": q["as_of"].isoformat(),
                                                "id": str(q["id"])}
    return legs, ctx, core, quotes


async def run_gate(pool, *, org_id, proposal_id, actor_id=None, today: date | None = None) -> dict:
    """Evaluate every rule on the proposal's legs and STORE the run (earlier
    runs closed). Returns the results and the blocking ones."""
    today = today or date.today()
    async with pool.acquire() as conn:
        p = await RP.load(conn, org_id, proposal_id)
        if p.get("proposal_kind") != "options":
            raise RP.ProposalError("the options tax gate runs on an options proposal")
        band = await RP._setting(conn, org_id, BAND_SETTING, DEFAULT_BAND, lo=Decimal("0.0001"), hi=ONE)
        legs, ctx, core, quotes = await gather(conn, org_id, p, today)
    results = evaluate(legs, ctx=ctx, core=core, band_setting=band, today=today, price_quotes=quotes)
    run_id = uuid.uuid4()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"UPDATE {T_GATE} SET valid_to = now() WHERE proposal_id = $1::uuid AND valid_to IS NULL "
                               "AND system_to IS NULL", str(p["id"]))
            for r in results:
                await conn.execute(
                    f"""INSERT INTO {T_GATE} (org_id, proposal_id, run_id, trade_id, leg_role, rule, outcome, reason,
                            pending_counsel, inputs, evaluated_by)
                        VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8, $9, $10::jsonb, $11::uuid)""",
                    str(org_id), str(p["id"]), str(run_id), r["trade_id"], r["leg_role"], r["rule"], r["outcome"],
                    r["reason"], r["pending_counsel"],
                    json.dumps({**r["inputs"], "evaluated_on": today.isoformat()}, default=str),
                    str(actor_id) if actor_id else None)
            await RP._audit(conn, org_id, actor_id, "options_tax_gate", p["id"],
                            {"run_id": str(run_id), "blocks": [r["rule"] for r in results if r["outcome"] == "block"],
                             "flags": [r["rule"] for r in results if r["outcome"] == "flag"]})
    return summary(results, run_id=str(run_id))


def summary(results: list[dict], *, run_id: str | None = None) -> dict:
    blocks = [r for r in results if r["outcome"] == "block"]
    return {"run_id": run_id, "results": results, "blocking": blocks,
            "flags": [r for r in results if r["outcome"] == "flag"],
            "pending_counsel": [r for r in results if r["pending_counsel"]], "passes": not blocks}


async def stored(conn, org_id, proposal_id) -> dict:
    rows = await conn.fetch(
        f"SELECT * FROM {T_GATE} WHERE org_id = $1::uuid AND proposal_id = $2::uuid AND valid_to IS NULL "
        "AND system_to IS NULL ORDER BY trade_id NULLS LAST, rule", str(org_id), str(proposal_id))
    results = [{"id": str(r["id"]), "rule": r["rule"], "rule_label": RULE_LABELS[r["rule"]], "outcome": r["outcome"],
                "outcome_label": OUTCOME_LABELS[r["outcome"]], "reason": r["reason"],
                "pending_counsel": r["pending_counsel"], "trade_id": str(r["trade_id"]) if r["trade_id"] else None,
                "leg_role": r["leg_role"], "inputs": RP._loads(r["inputs"]) or {},
                "evaluated_at": r["evaluated_at"].isoformat()} for r in rows]
    return {**summary(results, run_id=str(rows[0]["run_id"]) if rows else None),
            "evaluated_at": results[0]["evaluated_at"] if results else None}


def block_message(s: dict) -> str:
    return "options tax gate blocks approval — " + "; ".join(
        f"rule {b['rule']}: {b['reason']}" for b in s["blocking"])


# ── the approval pre-check (rep5's approval task; registered on import) ────
async def _approval_precheck(pool, task, outcome, actor_id):
    if outcome != "approve":
        return
    async with pool.acquire() as conn:
        p = await conn.fetchrow(f"SELECT id, org_id, proposal_kind FROM {RP.T_P} WHERE approval_task_id = $1::uuid "
                                "AND system_to IS NULL", task["id"])
    if p is None or p["proposal_kind"] != "options":
        return
    s = await run_gate(pool, org_id=p["org_id"], proposal_id=p["id"], actor_id=actor_id)
    if s["blocking"]:
        raise HT.HumanTaskConflict(block_message(s))
    async with pool.acquire() as conn:
        unpriced = [t["line_no"] for t in await RP.trades_of(conn, p["id"]) if t["price"] is None]
    if unpriced:
        raise HT.HumanTaskConflict(f"not priced — line(s) {unpriced} have no premium quote; every options instruction "
                                   "carries a limit price from an adviser quote with its as-of — enter the quotes and "
                                   "regenerate the proposal")


HT.register_precheck(RP.TASK_APPROVAL, _approval_precheck)


def vocabularies() -> dict:
    return {"gate_rules": [{"key": k, "label": v} for k, v in RULE_LABELS.items()],
            "gate_outcomes": [{"key": k, "label": v} for k, v in OUTCOME_LABELS.items()],
            "pending_counsel_label": PENDING_COUNSEL}

