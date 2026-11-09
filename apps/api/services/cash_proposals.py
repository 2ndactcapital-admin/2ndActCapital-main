"""CASH04 — from PLAN to ORDERS: one account's CASH PROPOSAL (buys / sells of the sleeve and ladder securities that move
today's cash / sleeve / ladder holdings toward the ACTIVE cash policy's targets).

A cash proposal is a KIND of rebalance proposal (``rebalance_proposals.proposal_kind = 'cash'``,
docs/cash/CASH04_NOTES.md §3) — the SAME table, lifecycle, screens and maker-checker as every Replication Studio
proposal. This module only BUILDS the trade list and writes it through that construct; everything after the draft is
rep5's own path, unchanged:

    draft ──submit (the maker, services.rebalance_proposals.submit)──▶ pending_approval
          ──approve (a DIFFERENT holder of approve_proposals; services.sod item rebalance_proposal)──▶ approved
          ──rebalance_proposals.execute → custody.trading.route_trade_list → execution.create_instructions──▶ routed

THE BUILDER (pure, Decimal — :func:`build_account`)
──────────────────────────────────────────────────────────────────────────────
Holdings are classified from the security master: a money-market fund (``mmf_category``) or a Treasury (``treasury_type``
Bill / Note, with ``maturity_date``); anything else is NON-CASH — listed as untouched, NEVER sold. A Treasury counts in
the sleeve its maturity falls in (``cash_policy.windows`` — half-open months), at PAR (what it pays at maturity); a fund
counts at its value: it fills Near-term up to what the bills there leave of the target, and the rest is Reserve. A Treasury past its maturity counts as cash at par (pending
redemption).

  1. SELL what is over target, in WITHDRAWAL ORDER (v1 §2.5): Near-term, then Reserve, then Long-term; inside a sleeve
     the fund first, then the bill / rung CLOSEST TO MATURITY first. Every sale of a bill / note before maturity
     carries ``early_sale`` — "may lose principal".
  2. BUY what is under target, Near-term → Reserve → Long-term, with the account's AVAILABLE cash (cash + the estimated
     sale proceeds + matured par): the instrument cash03 CHOSE for that sleeve × account; the Long-term sleeve by the
     LADDER's rungs (the household rung × the account's Long-term share). What the cash does not cover is the SHORTFALL
     — shown, never borrowed, never taken from a non-cash holding.
  3. Orders: a fund by NOTIONAL at market (a sale by shares at the account's own price); a bill / note by PAR
     ($100 increments) with a LIMIT per 100 of par — a bill's buy limit from the yield the plan used
     (100 ÷ (1 + y·d/365), rounded down), a note's at par (its coupon is not recorded), a sale's at the account's own
     valuation (rounded up). An indicative rung / bill (no CUSIP in the security master) is never bought.

Lines under the org's minimum trade (``replication.proposals.min_trade_size``, default $100) are left out and noted.

WRITES (:func:`write_proposal`) — under ``rebalance_proposals.account_lock``: the account must be MANAGED (rep7b
``held_away.refuse`` on path ``proposal:<trigger>``); an open proposal of another kind blocks it (never superseded); an
older open CASH draft / pending one is superseded; one transaction inserts the proposal (``trigger_ref`` = the policy
version + the ladder snapshot cash03 D3 promised) and its lines (``cash_leg``), audited ``proposal.generated``.

THE CUSTODY CLIFF. Nothing here routes, sends or moves money. The waterfall job (services.cash_waterfall) can only
reach :func:`write_draft` — a SYSTEM draft with no task; a person adopts it (:func:`adopt`), a different person
approves it, and people carry out the instructions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP, ROUND_UP, Decimal

import asyncpg

from services import account_models as AM
from services import held_away as HA
from services import human_tasks as HT
from services import rebalance_proposals as RP

log = logging.getLogger(__name__)

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
CENT = Decimal("0.01")
PX = Decimal("0.0001")              # a limit per 100 of par
SHARES = Decimal("0.001")           # fund shares
PAR_INCREMENT = Decimal(100)        # Treasury's minimum increment
DAYS_PER_YEAR = Decimal(365)
SLEEVES = ("near_term", "reserve", "long_term")
WITHDRAWAL_ORDER = SLEEVES           # v1 §2.5: Near-term first, then Reserve, then Long-term
SLEEVE_LABELS = {"near_term": "Near-term", "reserve": "Reserve", "long_term": "Long-term"}
MMF_KINDS = ("treasury_mmf", "government_mmf", "muni_mmf")
TREASURY_KIND = {"Bill": "bill", "Note": "note", "Bond": "note"}
EARLY_SALE = "early sale before maturity — may lose principal"
FI_UNITS = "quantity is PAR (USD face value); the limit is per 100 of par"
NOTE_LIMIT_TEXT = "limit at par (100.0000) — a note's coupon is not recorded, so the limit never pays above par"
NEEDS_ISSUE = "not bought — indicative (no CUSIP in the security master); needs an issue"
NOT_ALLOCATED = "no proposal — this account's share of the sleeves is not allocated (cash03: needs each account's cash " \
                "balance)"
TASK_KIND_REVIEW = "cash_waterfall_review"
PERMISSION = RP.MANAGE                      # generate / adopt: manage_proposals (rep5's author permission)
STEPS = {"withdrawal": "Sell over target (withdrawal order)", "top_up": "Top up below target",
         "rung": "Ladder rung", "reinvest": "Reinvest maturity", "earnings": "Reinvest earnings"}


class CashProposalError(ValueError):
    """A bad request (422)."""


class CashProposalConflict(RuntimeError):
    """The state refuses it (409)."""


# ═══ Pure helpers ══════════════════════════════════════════════════════════════════════════════════════════════
def money(v: Decimal | None) -> str | None:
    from services.cash_obligations import money_text

    return money_text(v)


def _d(v) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, float):
        raise CashProposalError("a binary float is not money — pass Decimal or text")
    return v if isinstance(v, Decimal) else Decimal(str(v))


def floor_par(x: Decimal) -> Decimal:
    """Whole $100 of par, rounded down (never buy more than the cash or the need)."""
    return ((x / PAR_INCREMENT).to_integral_value(rounding=ROUND_FLOOR) * PAR_INCREMENT).quantize(ONE) if x > 0 \
        else ZERO


def ceil_par(x: Decimal) -> Decimal:
    return ((x / PAR_INCREMENT).to_integral_value(rounding=ROUND_CEILING) * PAR_INCREMENT).quantize(ONE) if x > 0 \
        else ZERO


def bill_price(yield_pct: Decimal, maturity: date, today: date) -> Decimal:
    """Price per 100 of par of a bill at an investment-rate (bond-equivalent) yield: 100 ÷ (1 + y·d/365), ROUND DOWN
    to 0.0001 (a buy limit never pays more than the yield implies)."""
    days = Decimal(max((maturity - today).days, 0))
    return (HUNDRED / (ONE + (yield_pct / HUNDRED) * days / DAYS_PER_YEAR)).quantize(PX, rounding=ROUND_DOWN)


def buy_limit(instrument_type: str, y: dict | None, maturity: date | None, today: date) -> tuple[Decimal | None, str]:
    """(limit per 100 of par, how it was set) — None when a bill has no yield (never guessed)."""
    if instrument_type == "note":
        return Decimal("100.0000"), NOTE_LIMIT_TEXT
    y = y or {}
    if not y.get("estimated") or maturity is None:
        return None, f"no limit — no yield ({y.get('text') or 'not estimated'})"
    return bill_price(_d(y["yield_pct"]), maturity, today), (
        f"limit {bill_price(_d(y['yield_pct']), maturity, today)} per 100 from the yield {y['yield_pct']}% "
        f"({y.get('basis') or 'yield'}, as of {y.get('as_of')}): 100 ÷ (1 + y × days ÷ 365)")


def classify(h: dict, windows: dict, today: date) -> str:
    """'mmf' | 'matured' | a sleeve key (a Treasury, by its maturity) | 'other' (never sold)."""
    k = h.get("cash_kind")
    if k == "mmf":
        return "mmf"
    if k in ("bill", "note") and h.get("maturity") is not None:
        m = h["maturity"]
        if m <= today:
            return "matured"
        for s in SLEEVES:
            lo, hi = windows[s]
            if m < hi:
                return s
        return "long_term"
    return "other"


def _yield_pct(y: dict | None) -> Decimal | None:
    y = y or {}
    return _d(y["yield_pct"]) if y.get("estimated") else None


def _line(**kw) -> dict:
    base = {"side": None, "security_id": None, "label": None, "cash_kind": None, "sleeve": None, "quantity": None,
            "notional": None, "price": None, "price_source": "none", "price_as_of": None, "price_kind": "market",
            "limit_price": None, "reason": "", "rung_index": None, "obligations": [], "maturity": None,
            "warning": None, "est_net_yield": None, "est_after_tax_yield": None, "amount": ZERO, "step": None,
            "limit_text": None, "units": None}
    base.update(kw)
    return base


# ═══ The builder (pure) ════════════════════════════════════════════════════════════════════════════════════════
def build_account(*, account: dict, holdings: list[dict], cash: Decimal, targets: dict, choices: dict,
                  rungs: list[dict], windows: dict, today: date, min_trade: Decimal = Decimal(100)) -> dict:
    """One account's cash proposal. ``holdings``: [{security_id, label, cash_kind ('mmf'|'bill'|'note'|None),
    quantity, price (per unit / per $1 par), price_as_of, price_source, market_value, maturity}]; ``targets``:
    {sleeve: Decimal}; ``choices``: {sleeve: cash03's chosen candidate (kind, security_id, name, net, after_tax,
    maturity, cusip, indicative) or None}; ``rungs``: the account's share of the ladder [{index, maturity, par,
    security_id, cusip, name, instrument_type, yield, kind, covers}]; ``windows``: {sleeve: (from, until)}.
    Pure, Decimal, deterministic."""
    cash = _d(cash) or ZERO
    targets = {s: (_d(targets.get(s)) or ZERO) for s in SLEEVES}
    lines, notes, warnings = [], [], []
    untouched, by_sleeve, mmfs, matured = [], {s: [] for s in SLEEVES}, [], []
    for h in holdings:
        c = classify(h, windows, today)
        if c == "mmf":
            mmfs.append(h)
        elif c == "matured":
            matured.append(h)
        elif c in SLEEVES:
            by_sleeve[c].append(h)
        else:
            untouched.append({"security_id": h.get("security_id"), "label": h.get("label"),
                              "market_value": h.get("market_value"), "why": "not a cash instrument — never sold"})
    avail = cash
    for h in matured:
        par = _d(h["quantity"]) or ZERO
        avail += par
        notes.append(f"{h['label']} matured on {h['maturity'].isoformat()} — {money(par)} counted as cash at par "
                     "(pending redemption)")
    mmf_total = sum(((_d(h.get("market_value")) or ZERO) for h in mmfs), ZERO)
    par_in = {s: sum(((_d(h["quantity"]) or ZERO) for h in by_sleeve[s]), ZERO) for s in SLEEVES}
    mmf_near = min(mmf_total, max(targets["near_term"] - par_in["near_term"], ZERO))
    current = {"near_term": par_in["near_term"] + mmf_near, "reserve": par_in["reserve"] + (mmf_total - mmf_near),
               "long_term": par_in["long_term"]}
    before = dict(current)
    mmf_left = {id(h): (_d(h.get("market_value")) or ZERO) for h in mmfs}
    held_left = {id(h): (_d(h["quantity"]) or ZERO) for s in SLEEVES for h in by_sleeve[s]}
    order = 0
    sells_total = ZERO

    # ── 1. sells, in withdrawal order ─────────────────────────────────────────────────────────────────────────
    for s in WITHDRAWAL_ORDER:
        excess = current[s] - targets[s]
        if excess <= 0:
            continue
        if excess < min_trade:
            notes.append(f"{SLEEVE_LABELS[s]} is over target by {money(excess)} — under the minimum trade, kept")
            continue
        sources = []
        if s == "reserve":                       # the fund's part beyond Near-term is Reserve's, sold first (same day)
            room = mmf_total - mmf_near
            for h in sorted(mmfs, key=lambda x: (-(_d(x.get("market_value")) or ZERO), str(x["security_id"]))):
                if room <= 0:
                    break
                part = min(room, mmf_left[id(h)])
                if part > 0:
                    sources.append(("mmf", h, part))
                    room -= part
        for h in sorted(by_sleeve[s], key=lambda x: (x["maturity"], str(x["security_id"]))):
            sources.append(("fi", h, held_left[id(h)]))
        for kind, h, room in sources:
            if excess < min_trade:
                break
            order += 1
            if kind == "mmf":
                price = _d(h.get("price"))
                if not price or price <= 0:
                    notes.append(f"{h['label']}: no price — cannot sell it by shares")
                    continue
                amt = min(excess, room)
                qty = min((amt / price).quantize(SHARES, rounding=ROUND_UP), _d(h["quantity"]))
                proceeds = (qty * price).quantize(CENT, rounding=ROUND_HALF_UP)
                lines.append(_line(side="sell", security_id=h["security_id"], label=h["label"], cash_kind="mmf",
                                   sleeve=s, quantity=qty, price=price, price_source=h.get("price_source") or
                                   "account_position", price_as_of=h.get("price_as_of"), amount=proceeds,
                                   step="withdrawal", units="shares",
                                   reason=f"{SLEEVE_LABELS[s]} over target by {money(excess)} — sell the money-market "
                                          f"fund first (withdrawal order: Near-term, Reserve, Long-term)"))
                mmf_left[id(h)] -= proceeds
                excess -= proceeds
                current[s] -= proceeds
                avail += proceeds
                sells_total += proceeds
            else:
                price = _d(h.get("price"))
                if not price or price <= 0:
                    notes.append(f"{h['label']}: no valuation — cannot set a sale limit, not sold")
                    continue
                par = min(ceil_par(excess), room)
                if par <= 0:
                    continue
                limit = (price * HUNDRED).quantize(PX, rounding=ROUND_UP)
                proceeds = (par * price).quantize(CENT, rounding=ROUND_HALF_UP)
                lines.append(_line(side="sell", security_id=h["security_id"], label=h["label"],
                                   cash_kind=h["cash_kind"], sleeve=s, quantity=par, price=price,
                                   price_source=h.get("price_source") or "account_position",
                                   price_as_of=h.get("price_as_of"), price_kind="limit", limit_price=limit,
                                   amount=proceeds, step="withdrawal", maturity=h["maturity"], warning=EARLY_SALE,
                                   units="par", limit_text=f"limit {limit} per 100 — the account's own valuation",
                                   reason=f"{SLEEVE_LABELS[s]} over target by {money(excess)} — sell the rung "
                                          f"closest to maturity first ({h['maturity'].isoformat()})"))
                warnings.append({"code": "early_sale", "message": f"{h['label']}: {EARLY_SALE}"})
                held_left[id(h)] -= par
                excess -= par
                current[s] -= par
                avail += proceeds
                sells_total += proceeds

    # ── 2. buys, Near-term → Reserve → Long-term, within the available cash ──────────────────────────────────
    shortfall = {s: ZERO for s in SLEEVES}
    buys_total = ZERO
    at_items: list[tuple[Decimal, Decimal]] = []
    for s in SLEEVES:
        deficit = targets[s] - current[s]
        if deficit <= 0:
            continue
        if deficit < min_trade:
            notes.append(f"{SLEEVE_LABELS[s]} is under target by {money(deficit)} — under the minimum trade")
            continue
        ch = choices.get(s)
        if ch is None:
            notes.append(f"{SLEEVE_LABELS[s]}: {money(deficit)} not bought — no instrument chosen (cash03 has no "
                         "candidate with a yield)")
            continue
        if s == "long_term" and ch.get("kind") == "treasury_ladder":
            remaining = deficit
            held_par = {}
            for h in by_sleeve["long_term"]:
                held_par[str(h["security_id"])] = held_par.get(str(h["security_id"]), ZERO) + held_left[id(h)]
            for r in sorted(rungs, key=lambda x: (x["maturity"], x["index"])):
                if remaining < min_trade:
                    break
                need = min((_d(r["par"]) or ZERO) - held_par.get(str(r.get("security_id")), ZERO), remaining)
                if need < min_trade:
                    continue
                if not r.get("security_id"):
                    notes.append(f"rung {r['index']} ({r['maturity'].isoformat()}, par {money(need)}): {NEEDS_ISSUE}")
                    warnings.append({"code": "cash_needs_issue", "message": f"rung {r['index']}: {NEEDS_ISSUE}"})
                    remaining -= need
                    continue
                got = _buy_fi(lines, s, r, need, avail, today, step="rung", choice=ch,
                              reason=f"ladder rung {r['index']} ({r.get('kind') or 'rung'}) maturing "
                                     f"{r['maturity'].isoformat()}" + (f" — funds {', '.join(r['covers'])}"
                                                                         if r.get("covers") else ""))
                if got is None:
                    notes.append(f"rung {r['index']}: no limit could be set — no yield; not bought")
                    remaining -= need
                    continue
                cost, face, short = got
                avail -= cost
                buys_total += cost
                remaining -= need
                shortfall[s] += short
                at_items.append((cost, _yield_pct(ch.get("after_tax")) or _yield_pct(r.get("yield")) or ZERO))
            continue
        kind = ch.get("kind")
        if kind in MMF_KINDS:
            if not ch.get("security_id"):
                notes.append(f"{SLEEVE_LABELS[s]}: the chosen fund has no security in the master — not bought")
                continue
            can = min(deficit, avail).quantize(CENT, rounding=ROUND_DOWN)
            shortfall[s] += deficit - can if deficit > avail else ZERO
            if can < min_trade:
                continue
            order += 1
            lines.append(_line(side="buy", security_id=ch["security_id"], label=ch.get("name"), cash_kind="mmf",
                               sleeve=s, notional=can, amount=can, step="top_up", units="notional",
                               est_net_yield=ch.get("net"), est_after_tax_yield=ch.get("after_tax"),
                               reason=f"{SLEEVE_LABELS[s]} under target by {money(deficit)} — buy the chosen "
                                      f"{ch.get('kind_label') or kind.replace('_', ' ')} by notional"))
            avail -= can
            buys_total += can
            at_items.append((can, _yield_pct(ch.get("after_tax")) or _yield_pct(ch.get("net")) or ZERO))
        elif kind == "bill":
            if not ch.get("security_id") or ch.get("indicative"):
                notes.append(f"{SLEEVE_LABELS[s]} T-bill ({money(deficit)}): {NEEDS_ISSUE}")
                warnings.append({"code": "cash_needs_issue", "message": f"{SLEEVE_LABELS[s]} T-bill: {NEEDS_ISSUE}"})
                continue
            r = {"index": None, "maturity": ch["maturity"], "security_id": ch["security_id"], "name": ch.get("name"),
                 "instrument_type": "bill", "yield": ch.get("net"), "cusip": ch.get("cusip")}
            got = _buy_fi(lines, s, r, deficit, avail, today, step="top_up", choice=ch,
                          reason=f"{SLEEVE_LABELS[s]} under target by {money(deficit)} — buy the chosen T-bill "
                                 f"maturing {ch['maturity'].isoformat()} by par")
            if got is None:
                notes.append(f"{SLEEVE_LABELS[s]} T-bill: no limit — no yield; not bought")
                continue
            cost, face, short = got
            avail -= cost
            buys_total += cost
            shortfall[s] += short
            at_items.append((cost, _yield_pct(ch.get("after_tax")) or _yield_pct(ch.get("net")) or ZERO))
        else:
            notes.append(f"{SLEEVE_LABELS[s]}: the chosen {kind} cannot be ordered by this builder — not bought")
    for i, ln in enumerate(lines, start=1):
        ln["line_no"] = i
    total_short = sum(shortfall.values(), ZERO)
    if total_short > 0:
        warnings.append({"code": "cash_shortfall", "message": f"short by {money(total_short)} — the account's cash "
                                                             "does not cover the targets; shown, never borrowed"})
    if any(ln["units"] == "par" for ln in lines):
        warnings.append({"code": "cash_fixed_income", "message": FI_UNITS})
    bought = sum((v for v, _y in at_items), ZERO)
    effect = ({"estimated": True, "after_tax_yield_pct": (sum((v * y for v, y in at_items), ZERO) / bought)
               .quantize(PX, rounding=ROUND_HALF_UP), "annual_after_tax_income": (sum((v * y for v, y in at_items),
                                                                                       ZERO) / HUNDRED).quantize(
        CENT, rounding=ROUND_HALF_UP), "basis": "value-weighted after-tax yield of the buys (net where no profile)"}
              if bought > 0 else {"estimated": False, "text": "no buys"})
    return {"account_id": str(account["id"]), "account_label": account.get("label"),
            "lines": lines, "targets": targets, "current_before": before, "current_after": current,
            "cash": cash, "available_cash": cash + sum(((_d(h["quantity"]) or ZERO) for h in matured), ZERO),
            "cash_after": avail, "sells_total": sells_total, "buys_total": buys_total, "shortfall": total_short,
            "shortfall_by_sleeve": shortfall, "untouched": untouched, "notes": notes, "warnings": warnings,
            "after_tax_effect": effect}


def _buy_fi(lines: list, sleeve: str, r: dict, need_face: Decimal, avail: Decimal, today: date, *, step: str,
            choice: dict, reason: str) -> tuple[Decimal, Decimal, Decimal] | None:
    """Append a bill / note buy by PAR with a LIMIT. Returns (cost, face, shortfall in cash) or None (no limit)."""
    itype = r.get("instrument_type") or "bill"
    limit, how = buy_limit(itype, r.get("yield"), r.get("maturity"), today)
    if limit is None:
        return None
    unit = limit / HUNDRED
    want_face = ceil_par(need_face)
    want_cost = (want_face * unit).quantize(CENT, rounding=ROUND_HALF_UP)
    face = min(want_face, floor_par(avail / unit)) if unit > 0 else ZERO
    cost = (face * unit).quantize(CENT, rounding=ROUND_HALF_UP)
    short = (want_cost - cost) if face < want_face and avail < want_cost else ZERO
    if face > 0:
        lines.append(_line(side="buy", security_id=r["security_id"], label=r.get("name"), cash_kind=itype,
                           sleeve=sleeve, quantity=face, price=unit.quantize(Decimal("0.00000001")),
                           price_source="security_price", price_as_of=(r.get("yield") or {}).get("as_of") or today,
                           price_kind="limit", limit_price=limit, amount=cost, step=step, maturity=r.get("maturity"),
                           rung_index=r.get("index"), obligations=list(r.get("covers") or []), units="par",
                           est_net_yield=r.get("yield"), est_after_tax_yield=choice.get("after_tax"),
                           limit_text=how, reason=reason + f"; {FI_UNITS}"))
    return cost, face, short


# ═══ Plan → per-account inputs (pure) ══════════════════════════════════════════════════════════════════════════
def sleeve_windows(fields: dict, today: date) -> dict:
    from services import cash_policy as CP

    return {w["key"]: (w["from"], w["until"]) for w in CP.windows(int(fields["near_months"]),
                                                                  int(fields["reserve_months"]),
                                                                  int(fields["horizon_months"]), today)}


def account_inputs(scope_plan: dict, account_id: str, today: date) -> dict:
    """{targets, choices, rungs, windows, policy, why} for one account from cash03's built plan (raw). ``why`` set →
    the account gets no proposal."""
    built = scope_plan["built"]
    inst = built["instruments"]
    targets, choices = {}, {}
    lt_amount, lt_value = ZERO, None
    for s in inst["sleeves"]:
        row = next((r for r in s["accounts"] if str(r["account_id"]) == str(account_id)), None)
        if row is None:
            return {"why": "the account is not in this policy's scope"}
        if row["value"] is None:
            return {"why": NOT_ALLOCATED}
        targets[s["key"]] = _d(row["value"]).quantize(CENT, rounding=ROUND_HALF_UP)
        ch = row.get("chosen")
        choices[s["key"]] = None if ch is None else {**ch, "after_tax": ch.get("after_tax")}
        if s["key"] == "long_term":
            lt_amount, lt_value = _d(s["amount"]) or ZERO, _d(row["value"])
    share = (lt_value / lt_amount) if (lt_value is not None and lt_amount > 0) else ZERO
    names = {ln["key"]: ln.get("name") for ln in scope_plan.get("lines") or []}
    rungs = []
    for r in built["ladder"]["rungs"]:
        rungs.append({"index": r["index"], "maturity": r["maturity"],
                      "par": (_d(r["par"]) * share).quantize(CENT, rounding=ROUND_DOWN),
                      "security_id": r.get("security_id"), "cusip": r.get("cusip"),
                      "name": r.get("issue_name") or f"Treasury {r.get('instrument_type')} maturing "
                                                      f"{r['maturity'].isoformat()}",
                      "instrument_type": r.get("instrument_type"), "yield": r.get("yield"), "kind": r.get("kind"),
                      "indicative": bool(r.get("indicative")),
                      "covers": [names.get(k) or k for k in (r.get("targets") or [])]})
    return {"targets": targets, "choices": choices, "rungs": rungs, "lt_share": share,
            "windows": sleeve_windows(scope_plan["fields"], today), "why": None}


def assign_scopes(plans: list[dict]) -> dict[str, dict]:
    """{account id: the scope plan it is proposed from} — an entity override's accounts from THAT policy, every other
    account of the household from the household policy (CASH04 D2: never both)."""
    out: dict[str, dict] = {}
    for sp in plans:
        if sp["scope_type"] == "entity":
            for a in sp["accounts"]:
                out[str(a["id"])] = sp
    for sp in plans:
        if sp["scope_type"] == "household":
            for a in sp["accounts"]:
                out.setdefault(str(a["id"]), sp)
    return out


def ladder_snapshot(rungs: list[dict]) -> list[dict]:
    return [{"index": r["index"], "maturity": r["maturity"].isoformat(), "par": money(r["par"]),
             "cusip": r.get("cusip"), "security_id": str(r["security_id"]) if r.get("security_id") else None,
             "instrument_type": r.get("instrument_type"), "indicative": r.get("indicative"), "covers": r.get("covers")}
            for r in rungs]


def fingerprint(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ═══ Reads ═════════════════════════════════════════════════════════════════════════════════════════════════════
async def household_plans(conn, org_id, household_id, today: date, *, scope=None) -> list[dict]:
    """The household's scope plans (ACTIVE policies only), exactly as the Treasury envelope derives them."""
    from services import cash_coverage as CC
    from services import cash_instruments as CI
    from services import cash_obligations as OB
    from services import cash_policy as CP

    report = await CC.household_report(conn, org_id, household_id, today=today, member_scope=scope)
    rows = await CP.live_rows(conn, org_id, household_id=household_id, entity_ids=list(report.shares))
    want = CP.max_horizon(rows, OB.DEFAULT_HORIZON_MONTHS)
    rep = report if want <= OB.DEFAULT_HORIZON_MONTHS else await CC.household_report(
        conn, org_id, household_id, today=today, member_scope=scope, horizon_months=want)
    return await CI.scope_plans(conn, org_id, household_id, rep, today, purpose="internal")


HOLDING_ATTRS_SQL = """
SELECT a.global_security_id::text AS sid, a.field, a.value
FROM portfolio.security_reference_attributes a
WHERE a.global_security_id = ANY($1::uuid[]) AND a.field = ANY(ARRAY['mmf_category', 'treasury_type', 'maturity_date'])
  AND a.valid_to IS NULL AND a.system_to IS NULL
ORDER BY a.global_security_id, a.field, a.as_of DESC
"""


async def account_holdings(conn, org_id, account_id) -> dict:
    """{holdings (classified), cash, valuation_as_of, account} — the account's own valuation (account_models.
    load_holdings) with each security's cash class from the security master."""
    hold = await AM.load_holdings(conn, org_id, account_id)
    ids = [h.key for h in hold["holdings"]]
    attrs: dict[str, dict] = {}
    for r in await conn.fetch(HOLDING_ATTRS_SQL, ids) if ids else []:
        attrs.setdefault(r["sid"], {}).setdefault(r["field"], r["value"])
    out = []
    for h in hold["holdings"]:
        a = attrs.get(h.key, {})
        kind = "mmf" if a.get("mmf_category") else TREASURY_KIND.get(a.get("treasury_type") or "")
        mat = None
        if kind in ("bill", "note"):
            try:
                mat = date.fromisoformat(a.get("maturity_date") or "")
            except ValueError:
                kind = None          # a Treasury without a readable maturity is not classed — never sold
        out.append({"security_id": h.key, "label": h.label, "cash_kind": kind, "quantity": h.quantity,
                    "price": h.price, "price_as_of": h.price_as_of, "price_source": h.price_source,
                    "market_value": h.market_value, "maturity": mat})
    return {"holdings": out, "cash": hold["cash"], "cash_as_of": hold.get("cash_as_of"),
            "valuation_as_of": hold.get("valuation_as_of")}


async def min_trade(conn, org_id) -> Decimal:
    return await RP._setting(conn, org_id, RP.MIN_TRADE_SETTING, RP.DEFAULT_MIN_TRADE, lo=Decimal(0),
                             hi=Decimal(1000000))


# ═══ Writes (through the rebalance-proposal construct) ════════════════════════════════════════════════════════
def _dumps(v) -> str:
    return RP._dumps(v)


def _trade_reason(ln: dict) -> str:
    return ln["reason"] or STEPS.get(ln["step"] or "", "cash plan")


def _cash_leg(ln: dict) -> dict:
    return {"sleeve": ln["sleeve"], "step": ln["step"], "price_kind": ln["price_kind"],
            "limit_price": str(ln["limit_price"]) if ln["limit_price"] is not None else None,
            "limit_text": ln["limit_text"], "units": ln["units"], "cash_kind": ln["cash_kind"],
            "rung_index": ln["rung_index"], "obligations": ln["obligations"],
            "maturity": ln["maturity"].isoformat() if ln["maturity"] else None, "warning": ln["warning"],
            "amount": str(ln["amount"]), "est_net_yield": _yjson(ln["est_net_yield"]),
            "est_after_tax_yield": _yjson(ln["est_after_tax_yield"])}


def _yjson(y):
    if not y:
        return None
    return {k: (str(v) if isinstance(v, Decimal) else v.isoformat() if isinstance(v, date) else v)
            for k, v in y.items() if k in ("estimated", "yield_pct", "basis", "as_of", "text", "label", "source")}


def trigger_ref_for(scope_plan: dict, inputs: dict, built: dict, *, extra: dict | None = None) -> dict:
    active = scope_plan["active"]
    ref = {"cash_policy_id": str(active["id"]), "policy_version_no": active["version_no"],
           "scope_type": scope_plan["scope_type"], "scope_id": scope_plan["scope_id"],
           "household_id": str(scope_plan.get("household_id") or active.get("household_id") or "") or None,
           "targets": {k: money(v) for k, v in built["targets"].items()},
           "current_before": {k: money(v) for k, v in built["current_before"].items()},
           "current_after": {k: money(v) for k, v in built["current_after"].items()},
           "cash": money(built["cash"]), "cash_after": money(built["cash_after"]),
           "shortfall": money(built["shortfall"]),
           "shortfall_by_sleeve": {k: money(v) for k, v in built["shortfall_by_sleeve"].items()},
           "untouched": [{"label": u["label"], "why": u["why"]} for u in built["untouched"]],
           "notes": built["notes"], "after_tax_effect": _yjson_effect(built["after_tax_effect"]),
           "ladder": ladder_snapshot(inputs.get("rungs") or []), "lt_share": str(inputs.get("lt_share") or ZERO),
           "plan_fingerprint": fingerprint({"rungs": ladder_snapshot(inputs.get("rungs") or []),
                                            "targets": {k: money(v) for k, v in inputs["targets"].items()}})}
    ref.update(extra or {})
    return ref


def _yjson_effect(e: dict) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in e.items()}


async def write_proposal(pool, *, org_id, account_id, built: dict, trigger_ref: dict, maker_kind: str, maker_id,
                         trigger: str, today: date) -> dict:
    """Insert ONE cash proposal (draft) and its lines through the rebalance-proposal construct. Refusals (no write):
    a closed / not-managed account (the existing authority check), an open proposal of another kind, an approved
    proposal still executing. An older open CASH draft / pending one is superseded (its task withdrawn)."""
    if not built["lines"]:
        raise CashProposalConflict("nothing to trade — the account is at its targets (or nothing could be bought)")
    if (maker_kind == "user") != (maker_id is not None) or maker_kind not in ("user", "system"):
        raise CashProposalError("a cash proposal is made by a person (maker_id) or by the waterfall job (system)")
    if trigger not in ("manual", "cash_waterfall") or (trigger == "cash_waterfall") != (maker_kind == "system"):
        raise CashProposalError("a person's cash proposal is trigger manual; the waterfall's is cash_waterfall")
    pid = uuid.uuid4()
    now = datetime.now(timezone.utc)
    superseded = None
    async with RP.account_lock(pool, str(account_id)):
        async with pool.acquire() as conn:
            if maker_kind == "user":
                await HT.assert_human(conn, maker_id, org_id)
            account = await AM.load_account(conn, org_id, account_id)
            if AM.is_closed(account, today):
                raise CashProposalConflict(f"account {account['account_number_masked']} is closed")
            HA.refuse(account, "a cash proposal", CashProposalConflict, path=f"proposal:{trigger}")
            valuation = await AM.valuation_as_of(conn, org_id, account["id"])
            expiry_days = await RP._setting(conn, org_id, RP.EXPIRY_SETTING, RP.DEFAULT_EXPIRY_DAYS, lo=1, hi=90,
                                            integer=True)
        async with pool.acquire() as conn:
            async with conn.transaction():
                cur = await RP.open_proposal(conn, account["id"])
                if cur:
                    cur = await RP.load(conn, org_id, cur["id"], lock=True)
                if cur and cur["status"] == "approved":
                    raise CashProposalConflict("an approved proposal for this account is executing — a new cash "
                                               "proposal waits until its instructions are closed or cancelled")
                if cur and (cur.get("proposal_kind") or "model") != "cash":
                    raise RP.OpenProposalExists(cur)
                if cur and maker_kind == "system" and cur["maker_kind"] != "system":
                    raise CashProposalConflict("a person's open cash proposal exists for this account — the waterfall "
                                               "never supersedes it")
                if cur and cur["status"] in ("draft", "pending_approval"):
                    superseded = await RP._update(conn, cur["id"], {
                        "status": "superseded", "superseded_by_id": pid,
                        "status_reason": f"superseded by a newer cash proposal ({trigger}) {pid}"})
                    await RP._audit(conn, org_id, maker_id, "superseded", cur["id"],
                                    {"superseded_by_id": str(pid), "trigger": trigger, "kind": "cash",
                                     "previous_status": cur["status"]})
                warnings = list(built["warnings"]) + [{"code": "cash_note", "message": n} for n in built["notes"]]
                total = sum(((_d(h.get("market_value")) or ZERO) for h in built.get("_holdings") or []), ZERO)
                await conn.execute(
                    f"""INSERT INTO {RP.T_P} (id, org_id, account_id, link_id, model_id, model_version_id, trigger,
                            trigger_ref, status, advice_only, valuation_as_of, account_value, cash_value, warnings,
                            expires_at, maker_kind, maker_id, proposal_kind)
                        VALUES ($1, $2::uuid, $3::uuid, NULL, NULL, NULL, $4, $5::jsonb, 'draft', false, $6, $7, $8,
                                $9::jsonb, $10, $11, $12::uuid, 'cash')""",
                    pid, str(org_id), str(account["id"]), trigger, _dumps(trigger_ref), valuation,
                    (total + built["cash"]).quantize(CENT), built["cash"].quantize(CENT), _dumps(warnings),
                    now + timedelta(days=expiry_days), maker_kind, str(maker_id) if maker_id else None)
                for ln in built["lines"]:
                    await conn.execute(
                        f"""INSERT INTO {RP.T_T} (org_id, proposal_id, line_no, global_security_id, side, quantity,
                                notional, price, price_source, price_as_of, reason, cash_leg)
                            VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6, $7, $8, $9, $10, $11, $12::jsonb)""",
                        str(org_id), pid, ln["line_no"], str(ln["security_id"]), ln["side"], ln["quantity"],
                        ln["notional"], ln["price"], ln["price_source"], ln["price_as_of"], _trade_reason(ln),
                        _dumps(_cash_leg(ln)))
                await RP._audit(conn, org_id, maker_id, "generated", pid,
                                {"trigger": trigger, "kind": "cash", "trades": len(built["lines"]),
                                 "maker_kind": maker_kind, "cash_policy_id": trigger_ref.get("cash_policy_id"),
                                 "period": trigger_ref.get("period"), "shortfall": money(built["shortfall"]),
                                 "supersedes": str(superseded["id"]) if superseded else None})
    if superseded and superseded.get("approval_task_id"):
        await RP._withdraw_task(pool, superseded["approval_task_id"], "superseded by a newer cash proposal")
    if superseded:
        await RP._publish(pool, org_id, "superseded", superseded["id"], {"superseded_by_id": str(pid)}, maker_id)
    await RP._publish(pool, org_id, "generated", pid, {"trigger": trigger, "proposal_kind": "cash",
                                                       "account_id": str(account["id"]),
                                                       "trades": len(built["lines"])}, maker_id)
    async with pool.acquire() as conn:
        return await RP.load(conn, org_id, pid)


async def write_draft(pool, *, org_id, account_id, built: dict, trigger_ref: dict, period: str, today: date) -> dict:
    """THE waterfall job's ONLY write (S29b draft-only): a SYSTEM draft, trigger cash_waterfall, idempotent per
    (account, period) — an existing live row for the period is returned, never duplicated. Asserts the row it wrote is
    a draft with no task; it never submits, approves or routes."""
    async with pool.acquire() as conn:
        done = await conn.fetchrow(
            f"SELECT * FROM {RP.T_P} WHERE account_id = $1::uuid AND org_id = $2::uuid AND trigger = 'cash_waterfall' "
            "AND trigger_ref->>'period' = $3 AND system_to IS NULL", str(account_id), str(org_id), period)
    if done is not None:
        return {**dict(done), "_existing": True}
    try:
        row = await write_proposal(pool, org_id=org_id, account_id=account_id, built=built,
                                   trigger_ref={**trigger_ref, "period": period}, maker_kind="system", maker_id=None,
                                   trigger="cash_waterfall", today=today)
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name != "rebalance_proposals_cash_waterfall_uq":
            raise
        async with pool.acquire() as conn:
            done = await conn.fetchrow(
                f"SELECT * FROM {RP.T_P} WHERE account_id = $1::uuid AND trigger = 'cash_waterfall' AND "
                "trigger_ref->>'period' = $2 AND system_to IS NULL", str(account_id), period)
        return {**dict(done), "_existing": True}
    if row["status"] != "draft" or row["approval_task_id"] is not None or row["maker_kind"] != "system":
        raise CashProposalConflict("the waterfall may only write a SYSTEM DRAFT with no task (S29b draft-only)")
    return row


async def adopt(pool, *, org_id, proposal_id, actor_id, today: date | None = None) -> dict:
    """A person makes the waterfall's SYSTEM draft their own: a new cash proposal with the SAME lines (that person
    its maker, trigger manual, ``adopted_from``) supersedes the draft and is SUBMITTED — so a DIFFERENT person must
    approve it, and its instructions start approved through rep5's upstream path (CASH04 D8)."""
    today = today or date.today()
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        p = await RP.load(conn, org_id, proposal_id)
        if (p.get("proposal_kind") or "model") != "cash" or p["trigger"] != "cash_waterfall":
            raise CashProposalError("only a waterfall cash draft is adopted")
        if p["status"] != "draft":
            raise CashProposalConflict(f"the waterfall draft is {RP.STATUS_LABELS[p['status']].lower()}, not a draft")
        trades = await RP.trades_of(conn, p["id"])
    ref = {k: v for k, v in (RP._loads(p["trigger_ref"]) or {}).items() if k != "period"}
    ref["adopted_from"] = str(p["id"])
    ref["adopted_period"] = (RP._loads(p["trigger_ref"]) or {}).get("period")
    lines = []
    for t in trades:
        leg = RP._loads(t.get("cash_leg")) or {}
        lines.append(_line(side=t["side"], security_id=str(t["global_security_id"]), label=t.get("security_name"),
                           cash_kind=leg.get("cash_kind"), sleeve=leg.get("sleeve"), quantity=t["quantity"],
                           notional=t["notional"], price=t["price"], price_source=t["price_source"],
                           price_as_of=t["price_as_of"], price_kind=leg.get("price_kind") or "market",
                           limit_price=_d(leg.get("limit_price")), reason=t["reason"],
                           rung_index=leg.get("rung_index"), obligations=leg.get("obligations") or [],
                           maturity=date.fromisoformat(leg["maturity"]) if leg.get("maturity") else None,
                           warning=leg.get("warning"), amount=_d(leg.get("amount")) or ZERO, step=leg.get("step"),
                           limit_text=leg.get("limit_text"), units=leg.get("units"),
                           est_net_yield=leg.get("est_net_yield"), est_after_tax_yield=leg.get("est_after_tax_yield"),
                           line_no=t["line_no"]))
    built = {"lines": lines, "warnings": RP._loads(p["warnings"]) or [], "notes": [],
             "cash": _d(p["cash_value"]) or ZERO, "shortfall": _d(ref.get("shortfall")) or ZERO}
    row = await write_proposal(pool, org_id=org_id, account_id=p["account_id"], built=built, trigger_ref=ref,
                               maker_kind="user", maker_id=actor_id, trigger="manual", today=today)
    return await RP.submit(pool, org_id=org_id, proposal_id=row["id"], actor_id=actor_id)


# ═══ Household: build + write every account ═══════════════════════════════════════════════════════════════════
async def build_household(conn, org_id, household_id, today: date, *, scope=None) -> list[dict]:
    """Per account in the household's ACTIVE policies: {account, scope_plan, inputs, built | why} (no write)."""
    plans = await household_plans(conn, org_id, household_id, today, scope=scope)
    for sp in plans:
        sp.setdefault("household_id", str(household_id))
    by_account = assign_scopes(plans)
    mt = await min_trade(conn, org_id)
    out = []
    for aid, sp in sorted(by_account.items()):
        acct = next(a for a in sp["accounts"] if str(a["id"]) == aid)
        inputs = account_inputs(sp, aid, today)
        rec = {"account": acct, "scope_plan": sp, "inputs": inputs, "built": None, "why": inputs.get("why")}
        if not rec["why"]:
            h = await account_holdings(conn, org_id, aid)
            built = build_account(account=acct, holdings=h["holdings"], cash=h["cash"], targets=inputs["targets"],
                                  choices=inputs["choices"], rungs=inputs["rungs"], windows=inputs["windows"],
                                  today=today, min_trade=mt)
            built["_holdings"] = h["holdings"]
            rec["built"] = built
            if not built["lines"]:
                rec["why"] = "nothing to trade — at target, or nothing could be bought (see the notes)"
        out.append(rec)
    return out


async def propose_household(pool, *, org_id, household_id, actor_id, today: date | None = None, scope=None) -> dict:
    """``POST /cash/households/{id}/proposals`` — a person's DRAFT cash proposal per account (trigger manual), each
    through :func:`write_proposal`; audited once per household (``cash.proposals.generated``)."""
    today = today or date.today()
    async with pool.acquire() as conn:
        await HT.assert_human(conn, actor_id, org_id)
        recs = await build_household(conn, org_id, household_id, today, scope=scope)
    made, skipped = [], []
    for rec in recs:
        a = rec["account"]
        if rec["why"]:
            skipped.append({"account_id": str(a["id"]), "account_label": a.get("label"), "why": rec["why"],
                            "notes": (rec["built"] or {}).get("notes") or []})
            continue
        ref = trigger_ref_for(rec["scope_plan"], rec["inputs"], rec["built"], extra={"made_by": "person"})
        try:
            row = await write_proposal(pool, org_id=org_id, account_id=a["id"], built=rec["built"], trigger_ref=ref,
                                       maker_kind="user", maker_id=actor_id, trigger="manual", today=today)
        except (CashProposalConflict, RP.ProposalConflict) as exc:
            skipped.append({"account_id": str(a["id"]), "account_label": a.get("label"), "why": str(exc)})
            continue
        made.append({"proposal_id": str(row["id"]), "account_id": str(a["id"]), "account_label": a.get("label"),
                     "trades": len(rec["built"]["lines"]), "shortfall": money(rec["built"]["shortfall"])})
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO audit_log (org_id, user_id, action, resource_type, resource_id, payload) VALUES "
            "($1::uuid, $2::uuid, 'cash.proposals.generated', 'household', $3::uuid, $4::jsonb)",
            str(org_id), str(actor_id), str(household_id),
            _dumps({"today": today.isoformat(), "made": made, "skipped": skipped}))
    return {"made": made, "skipped": skipped, "today": today.isoformat()}


# ═══ Out (the Treasury tab's "Proposed trades" panel) ════════════════════════════════════════════════════════
def line_out(t: dict) -> dict:
    """A stored line (rebalance_proposals.trade_out) → the panel's row: grouped by sleeve / rung, reason, warning."""
    leg = t.get("cash_leg") or {}
    return {"line_no": t["line_no"], "side": t["side"], "side_label": t.get("side_label"),
            "security": (t.get("security") or {}).get("name"), "quantity": t.get("quantity"),
            "notional": t.get("notional"), "units": leg.get("units"), "price_kind": leg.get("price_kind"),
            "limit_price": leg.get("limit_price"), "limit_text": leg.get("limit_text"),
            "sleeve": leg.get("sleeve"), "sleeve_label": SLEEVE_LABELS.get(leg.get("sleeve") or "", ""),
            "rung_index": leg.get("rung_index"), "maturity": leg.get("maturity"), "step": leg.get("step"),
            "step_label": STEPS.get(leg.get("step") or "", ""), "obligations": leg.get("obligations") or [],
            "reason": t.get("reason"), "warning": leg.get("warning"), "amount": leg.get("amount"),
            "est_after_tax_yield": leg.get("est_after_tax_yield"), "est_net_yield": leg.get("est_net_yield"),
            "instruction_id": t.get("instruction_id")}


def routes_offered() -> list[dict]:
    """The routes the screen offers with the existing rule's refusal (execution_routers.route_refusal): manual always;
    fix refused with its reason (no router is connected)."""
    from services import execution_routers as XR

    return [{"key": k, "label": XR.ROUTE_LABELS[k], "refusal": XR.route_refusal(k)} for k in XR.ROUTES]


async def household_panel(conn, org_id, household_id, *, user_id, can_write: bool, can_approve: bool,
                          account_ids: list[str]) -> dict:
    """The Proposed-trades panel (STAFF only): every live cash proposal of the household's accounts, newest first,
    with its lines, and the waterfall's run history (its proposals by period)."""
    rows = await conn.fetch(
        f"SELECT * FROM {RP.T_P} WHERE org_id = $1::uuid AND account_id = ANY($2::uuid[]) AND proposal_kind = 'cash' "
        "AND system_to IS NULL ORDER BY created_at DESC LIMIT 100", str(org_id),
        [uuid.UUID(a) for a in account_ids])
    accts = {str(r["id"]): dict(r) for r in await conn.fetch(
        "SELECT id, account_number_masked, custodian_code, is_held_away, is_discretionary FROM public.accounts "
        "WHERE id = ANY($1::uuid[]) AND system_to IS NULL AND valid_to IS NULL", [uuid.UUID(a) for a in account_ids])}
    proposals, history = [], []
    for r in rows:
        p = RP.proposal_out(dict(r), accts.get(str(r["account_id"])))
        if r["status"] in RP.OPEN_STATUSES or len(proposals) < 12:
            trades = [RP.trade_out(t) for t in await RP.trades_of(conn, r["id"])]
            acts = RP.actions_for(p, can_write=can_write, can_approve=False, user_id=user_id, task_open=False)
            if can_write and r["trigger"] == "cash_waterfall" and r["status"] == "draft":
                acts.append("adopt")
            ref = p["trigger_ref"]
            proposals.append({**{k: p[k] for k in ("id", "account_id", "status", "trigger", "maker_kind",
                                                    "created_at", "expires_at", "warnings", "execution",
                                                    "status_reason")},
                              "account": p["account"], "status_label": RP.STATUS_LABELS[p["status"]],
                              "trigger_label": RP.TRIGGER_LABELS.get(p["trigger"], p["trigger"]),
                              "policy_version_no": ref.get("policy_version_no"), "period": ref.get("period"),
                              "targets": ref.get("targets"), "current_before": ref.get("current_before"),
                              "current_after": ref.get("current_after"), "shortfall": ref.get("shortfall"),
                              "after_tax_effect": ref.get("after_tax_effect"), "notes": ref.get("notes") or [],
                              "untouched": ref.get("untouched") or [], "lines": [line_out(t) for t in trades],
                              "actions": acts, "link": f"/portfolio/proposals/{p['id']}",
                              "instruction_links": [f"/portfolio/instructions/{i}" for i in
                                                    (p["execution"] or {}).get("instruction_ids") or []]})
        if r["trigger"] == "cash_waterfall":
            ref = RP._loads(r["trigger_ref"]) or {}
            history.append({"period": ref.get("period"), "proposal_id": str(r["id"]), "status": r["status"],
                            "status_label": RP.STATUS_LABELS[r["status"]], "created_at": RP._iso(r["created_at"]),
                            "account": (accts.get(str(r["account_id"])) or {}).get("account_number_masked"),
                            "workflow_run_id": ref.get("workflow_run_id"), "maturities": ref.get("maturities") or [],
                            "earnings": ref.get("earnings")})
    return {"proposals": proposals, "waterfall_history": history, "routes": routes_offered(),
            "actions": ["generate"] if can_write else [],
            "vocabularies": {"sleeves": [{"key": k, "label": v} for k, v in SLEEVE_LABELS.items()],
                             "steps": [{"key": k, "label": v} for k, v in STEPS.items()],
                             "statuses": [{"key": k, "label": v} for k, v in RP.STATUS_LABELS.items()],
                             "editable": [], "inline_editable": []}}


def instruction_sheet(line: dict, *, account: dict, executor_type: str, execute_by: date, notes: str,
                      security: dict) -> list[tuple[str, str]]:
    """The manual sheet rows (services.execution_letter.letter_rows — the ONE renderer) for the instruction a line
    WOULD become. Pure: the Olivo dry run and the verify print it; nothing is created."""
    from services import execution_letter as EL

    d = {"id": "(not created — dry run)", "side": line["side"],
         "quantity": str(line["quantity"]) if line["quantity"] is not None else None,
         "notional": str(line["notional"]) if line["notional"] is not None else None,
         "price_kind": line["price_kind"], "limit_price": str(line["limit_price"]) if line["limit_price"] else None,
         "execute_by": execute_by.isoformat(), "if_not_filled": "ask", "executor_type": executor_type,
         "executor_name": None, "notes": notes, "security": security, "account": account}
    return EL.letter_rows(d, account_display=account.get("account_number_masked") or "")


# ═══ The waterfall's review task (one per household, de-duplicated while open) ═══════════════════════════════
async def _on_review_complete(pool, task, outcome, actor_id, note):
    return None


HT.register_kind(TASK_KIND_REVIEW, label="Cash waterfall — review the draft proposals", outcomes=("done",),
                 maker_checker=False, on_complete=_on_review_complete, system_open=True)
