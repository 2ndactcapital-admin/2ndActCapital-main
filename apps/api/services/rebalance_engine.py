"""Rebalance engine (rep5) — PURE: no database, no clock, exact Decimal.

Everything a rebalance PROPOSAL computes from what an account holds and what
its model targets. services.rebalance_proposals loads the inputs (positions,
lots, cash, the model's look-through, the account's settings) and stores the
output; nothing here reads or writes anything, so every rule below is proven
directly on hand-built inputs.

WHAT IS TARGETED — :func:`apply_exclusions`, :func:`apply_cash_floor`
──────────────────────────────────────────────────────────────────────────────
Excluded securities leave the target; their weight W goes by the account's
exclusion policy:
  pro_rata        spread over the remaining securities in proportion to their
                  weight — rep4's ``allocate`` (largest remainder at
                  numeric(14,10)), so the total is preserved EXACTLY;
  to_cash         added to the cash target;
  use_alternates  moved to the excluded position's first alternate that is not
                  itself excluded (the model's own alternates); a position with
                  no usable alternate falls back to pro rata, and the note says so.
Then the cash target is at least the account's cash floor (plus any staff-
entered withdrawal), the securities scaled pro rata to make room.

WHAT IS SOLD — :func:`plan_sales`
──────────────────────────────────────────────────────────────────────────────
Lots by lowest tax per dollar raised: losses first, then highest cost; at
equal cost long-term before short-term (then oldest, then id). The short-term
guard (``avoid_short_term``) DEFERS a short-term lot at a gain that turns
long-term within ``short_term_wait_days`` — to the date it turns. The gain
budget caps NET realized gain for the calendar year: gains already realized
this year count against it, losses taken in the same proposal add room, and a
gain lot that does not fit is sold only as far as it fits; what is left is
"sell later" in the next year(s), one budget per year. A lot whose cost is
unknown is never sold against a budget (its gain cannot be known).

WHAT IS BOUGHT — :func:`build`
──────────────────────────────────────────────────────────────────────────────
To the target weight, from the cash the sales raise above the cash target —
scaled down pro rata when the budget held the sales back. A priced security is
bought in whole shares (fractional only when the custodian's fractional_shares
is 'yes'); an UNPRICED one by notional (dollars) — never a guessed quantity. A
holding the account cannot value is never traded. Trades under the minimum
trade size are dropped as dust (counted in a warning).

WASH-SALE FLAG — :func:`wash_sale_flags` (rep6 replaces it with full control)
──────────────────────────────────────────────────────────────────────────────
A buy of a security the household SOLD AT A LOSS in the prior 30 days, and a
loss sale of a security the household BOUGHT in the prior 30 days (every
account of the household, IRAs included). A flag is information on the trade;
it never blocks it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal, localcontext

ZERO = Decimal(0)
ONE = Decimal(1)
Q = Decimal("0.0000000001")           # weights — numeric(14, 10)
QTY = Decimal("0.00000001")           # quantities and prices — numeric(28, 8)
CENT = Decimal("0.01")
CASH = "cash"
MAX_PLAN_YEARS = 50
WASH_WINDOW_DAYS = 30


def d(v) -> Decimal | None:
    if v is None:
        return None
    return v if isinstance(v, Decimal) else Decimal(str(v))


def qw(v: Decimal) -> Decimal:
    return v.quantize(Q, rounding=ROUND_HALF_EVEN)


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_EVEN)


def pct(v: Decimal) -> str:
    return f"{(v * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN)}%"


def allocate(weights: list[tuple[str, Decimal, int]], total: Decimal) -> dict[str, Decimal]:
    """rep4's largest-remainder allocation (services.replica_models.allocate),
    re-stated here so the engine stays import-free: ``weights`` scaled to sum to
    exactly ``total`` at numeric(14,10)."""
    from decimal import ROUND_FLOOR

    total = total.quantize(Q)
    s = sum((w for _, w, _ in weights), ZERO)
    if s <= 0:
        raise ValueError("nothing to allocate to")
    with localcontext() as ctx:
        ctx.prec = 60
        units = total / Q
        raw = [(k, w * units / s, t) for k, w, t in weights]
        floors = {k: u.to_integral_value(rounding=ROUND_FLOOR) for k, u, _ in raw}
        left = units - sum(floors.values(), ZERO)
        order = sorted(raw, key=lambda x: (-(x[1] - floors[x[0]]), x[2]))
        for i in range(int(left)):
            floors[order[i][0]] += 1
        return {k: (floors[k] * Q).quantize(Q) for k, _, _ in raw}


# ── Targets ─────────────────────────────────────────────────────────────────
def apply_exclusions(target: dict[str, Decimal], excluded: dict[str, str], policy: str,
                     alternates: dict[str, list[str]] | None = None, labels: dict[str, str] | None = None
                     ) -> tuple[dict[str, Decimal], list[str]]:
    """``target`` {security id | 'cash': weight} → (new target, plain notes).
    The sum of the weights is preserved exactly."""
    labels = labels or {}
    alternates = alternates or {}
    out = {k: d(v) for k, v in target.items()}
    notes: list[str] = []
    hit = [k for k in out if k != CASH and k in excluded]
    if not hit:
        return out, notes
    if policy not in ("pro_rata", "to_cash", "use_alternates"):
        raise ValueError(f"unknown exclusion policy {policy!r}")
    spread = ZERO
    for k in hit:
        w = out.pop(k)
        name = labels.get(k, k)
        if policy == "to_cash":
            out[CASH] = out.get(CASH, ZERO) + w
            notes.append(f"{name} is excluded ({excluded[k]}): its {pct(w)} goes to cash")
        elif policy == "use_alternates":
            alt = next((a for a in alternates.get(k, []) if a not in excluded and a != k), None)
            if alt is not None:
                out[alt] = out.get(alt, ZERO) + w
                notes.append(f"{name} is excluded ({excluded[k]}): its {pct(w)} goes to its alternate "
                             f"{labels.get(alt, alt)}")
            else:
                spread += w
                notes.append(f"{name} is excluded ({excluded[k]}) and the model gives no usable alternate: its "
                             f"{pct(w)} is spread pro rata instead")
        else:
            spread += w
            notes.append(f"{name} is excluded ({excluded[k]}): its {pct(w)} is spread pro rata")
    if spread > 0:
        rest = [(k, v, i) for i, (k, v) in enumerate(sorted(out.items())) if k != CASH and v > 0]
        if not rest:
            out[CASH] = out.get(CASH, ZERO) + spread
            notes.append("no other security is left to spread over: the weight goes to cash")
        else:
            total = sum((v for _, v, _ in rest), ZERO) + spread
            for k, v in allocate(rest, total).items():
                out[k] = v
    return out, notes


def apply_cash_floor(target: dict[str, Decimal], cash_floor: Decimal, extra_cash: Decimal = ZERO
                     ) -> tuple[dict[str, Decimal], list[str]]:
    """The cash target is at least the floor (+ ``extra_cash``, a withdrawal as
    a fraction of the account); securities scale pro rata to make room."""
    out = dict(target)
    notes = []
    total = sum(out.values(), ZERO)
    cash = out.get(CASH, ZERO)
    need = min(max(cash, cash_floor) + extra_cash, total)
    if need == cash:
        return out, notes
    rest = [(k, v, i) for i, (k, v) in enumerate(sorted(out.items())) if k != CASH and v > 0]
    room = total - need
    if rest and room > 0:
        out.update(allocate(rest, room))
    else:
        for k, _, _ in rest:
            out[k] = ZERO
    out[CASH] = qw(need)
    if extra_cash > 0:
        notes.append(f"cash target raised to {pct(need)} to fund the withdrawal")
    elif cash_floor > cash:
        notes.append(f"cash target raised from {pct(cash)} to the account's cash floor {pct(cash_floor)}")
    return out, notes


# ── Lots ────────────────────────────────────────────────────────────────────
@dataclass
class Lot:
    id: str | None
    quantity: Decimal
    unit_cost: Decimal | None
    acquired: date | None
    term_hint: str | None = None


@dataclass
class Holding:
    key: str                      # global security id
    label: str
    quantity: Decimal
    market_value: Decimal | None
    price: Decimal | None
    price_as_of: date | None
    price_source: str             # 'account_position' | 'none'
    lots: list[Lot] = field(default_factory=list)
    asset_ids: list[str] = field(default_factory=list)


def add_years(day: date, n: int) -> date:
    try:
        return day.replace(year=day.year + n)
    except ValueError:            # 29 February
        return day.replace(year=day.year + n, day=28)


def long_term_from(acquired: date) -> date:
    """The first day a lot is long-term: held MORE than one year."""
    return add_years(acquired, 1) + timedelta(days=1)


def term_of(lot: Lot, on: date) -> str:
    if lot.acquired is not None:
        return "long" if on >= long_term_from(lot.acquired) else "short"
    return lot.term_hint if lot.term_hint in ("short", "long") else "unknown"


def lot_order_key(lot: Lot, price: Decimal, on: date):
    """Lowest tax per dollar raised: losses first, then highest cost; at equal
    cost long-term before short-term; then oldest; then id."""
    cost = lot.unit_cost
    gain = None if cost is None else price - cost
    return (cost is None, gain is not None and gain > 0, -(cost if cost is not None else ZERO),
            0 if term_of(lot, on) == "long" else 1, lot.acquired or date.max, lot.id or "")


@dataclass
class SaleSettings:
    today: date
    gain_budget: Decimal | None          # None = unlimited
    realized_ytd: Decimal = ZERO
    avoid_short_term: bool = True
    short_term_wait_days: int = 30
    fractional: bool = False


@dataclass
class LotSale:
    lot_id: str | None
    quantity: Decimal
    unit_cost: Decimal | None
    term: str
    gain: Decimal | None
    acquired: date | None


@dataclass
class LaterSale:
    key: str
    lot_id: str | None
    quantity: Decimal
    year: int | None
    on_date: date | None
    gain: Decimal | None
    reason: str


@dataclass
class SalePlan:
    now: dict[str, list[LotSale]]
    later: list[LaterSale]
    blocked: list[dict]
    net_gain_now: Decimal
    room_now: Decimal | None


def _units(fractional: bool) -> Decimal:
    return QTY if fractional else ONE


def _floor(q: Decimal, unit: Decimal) -> Decimal:
    return (q / unit).to_integral_value(rounding=ROUND_DOWN) * unit if q > 0 else ZERO


def plan_sales(requests: list[tuple[Holding, Decimal]], s: SaleSettings) -> SalePlan:
    """``requests`` [(holding, quantity wanted)] — which lots go now, which
    later (by year, or by the date a short-term lot turns long-term)."""
    unit = _units(s.fractional)
    budget = s.gain_budget
    room = None if budget is None else budget - s.realized_ytd
    now: dict[str, list[LotSale]] = {}
    later: list[LaterSale] = []
    blocked: list[dict] = []
    net = ZERO
    wanted = {h.key: q for h, q in requests}
    portions = []          # (sort key, holding, lot, gain per share)
    deferred = []          # (holding, lot, date turns long)
    for h, _ in requests:
        price = h.price
        for lot in sorted(h.lots, key=lambda x: lot_order_key(x, price, s.today)):
            g = None if lot.unit_cost is None else price - lot.unit_cost
            term = term_of(lot, s.today)
            if g is None:
                if budget is not None:
                    blocked.append({"key": h.key, "lot_id": lot.id, "quantity": lot.quantity,
                                    "reason": "cost basis unknown — never sold against a gain budget"})
                    continue
            elif s.avoid_short_term and term == "short" and g > 0 and lot.acquired is not None:
                turns = long_term_from(lot.acquired)
                if (turns - s.today).days <= s.short_term_wait_days:
                    deferred.append((h, lot, turns, g))
                    continue
            portions.append((h, lot, g, term))

    def take(h: Holding, lot: Lot, q: Decimal, g, term):
        nonlocal net
        gain = None if g is None else q * g
        now.setdefault(h.key, []).append(LotSale(lot.id, q, lot.unit_cost, term, gain, lot.acquired))
        wanted[h.key] -= q
        if gain is not None:
            net += gain

    # 1. every loss (and zero-gain, and unknown-cost when unlimited) portion first — they add room
    rest = []
    for h, lot, g, term in portions:
        if wanted[h.key] <= 0:
            continue
        if g is None or g <= 0:
            q = min(lot.quantity, wanted[h.key])
            if q > 0:
                take(h, lot, q, g, term)
        else:
            rest.append((h, lot, g, term))
    # 2. gains, lowest tax per dollar raised first, while they fit the year's budget
    rest.sort(key=lambda x: (x[2] / x[0].price, 0 if x[3] == "long" else 1, x[1].acquired or date.max, x[1].id or ""))
    leftover = []
    for h, lot, g, term in rest:
        w = wanted[h.key]
        if w <= 0:
            continue
        q = min(lot.quantity, w)
        if room is None or net + q * g <= room:
            take(h, lot, q, g, term)
            continue
        fit = _floor((room - net) / g, unit) if room - net > 0 else ZERO
        fit = min(fit, q)
        if fit > 0:
            take(h, lot, fit, g, term)
        if q - fit > 0:
            leftover.append((h, lot, q - fit, g))
            wanted[h.key] -= q - fit
    # 3. schedule what waits: deferred short-term lots from the day they turn, budget overflow from next year
    years: dict[int, Decimal] = {}
    if room is not None:
        years[s.today.year] = room - net

    def schedule(h, lot, q, g, first_year, on_date, reason):
        if g is None or g <= 0 or budget is None:
            later.append(LaterSale(h.key, lot.id, q, first_year, on_date, None if g is None else q * g, reason))
            return
        y = first_year
        while q > 0 and y < s.today.year + MAX_PLAN_YEARS:
            r = years.setdefault(y, budget)
            fit = q if q * g <= r else (_floor(r / g, unit) if r > 0 else ZERO)
            if fit > 0:
                later.append(LaterSale(h.key, lot.id, fit, y, on_date if y == first_year else None, fit * g,
                                       reason))
                years[y] = r - fit * g
                q -= fit
            y += 1
        if q > 0:
            later.append(LaterSale(h.key, lot.id, q, None, None, q * g,
                                   f"{reason} — beyond {MAX_PLAN_YEARS} years at this gain budget"))

    for h, lot, turns, g in deferred:
        q = min(lot.quantity, wanted[h.key]) if wanted[h.key] > 0 else ZERO
        if q <= 0:
            continue
        wanted[h.key] -= q
        schedule(h, lot, q, g, turns.year, turns,
                 f"short-term gain: deferred until it turns long-term on {turns.isoformat()}")
    for h, lot, q, g in leftover:
        schedule(h, lot, q, g, s.today.year + 1, None, "over this year's gain budget")
    return SalePlan(now=now, later=later, blocked=blocked, net_gain_now=net,
                    room_now=None if room is None else room)


# ── The proposal ────────────────────────────────────────────────────────────
@dataclass
class BuildSettings:
    sale: SaleSettings
    cash_floor: Decimal = ZERO
    min_trade: Decimal = ZERO
    withdrawal: Decimal | None = None


@dataclass
class TradeOut:
    key: str
    label: str
    side: str
    quantity: Decimal | None
    notional: Decimal | None
    price: Decimal | None
    price_source: str
    price_as_of: date | None
    lots: list[dict]
    est_st_gain: Decimal
    est_lt_gain: Decimal
    reason: str


@dataclass
class BuildResult:
    account_value: Decimal
    cash: Decimal
    target: dict[str, Decimal]
    before: dict[str, Decimal]
    after: dict[str, Decimal]
    drift_before: Decimal
    drift_after: Decimal
    trades: list[TradeOut]
    later: list[LaterSale]
    warnings: list[dict]
    notes: list[str]
    plan: list[dict]
    est_st_gain: Decimal
    est_lt_gain: Decimal
    total_embedded_gain: Decimal
    years_to_complete: int
    plan_by_year: list[dict]


def _weights(values: dict[str, Decimal], total: Decimal) -> dict[str, Decimal]:
    if total <= 0:
        return {k: ZERO for k in values}
    with localcontext() as ctx:
        ctx.prec = 60
        return {k: qw(v / total) for k, v in values.items()}


def max_drift(actual: dict[str, Decimal], target: dict[str, Decimal]) -> Decimal:
    keys = set(actual) | set(target)
    return max((abs(actual.get(k, ZERO) - target.get(k, ZERO)) for k in keys), default=ZERO)


def current_weights(holdings: list[Holding], cash: Decimal) -> tuple[Decimal, dict[str, Decimal]]:
    priced = {h.key: h.market_value for h in holdings if h.price is not None and h.market_value is not None}
    total = sum(priced.values(), ZERO) + cash
    return total, _weights({**priced, CASH: cash}, total)


def build(holdings: list[Holding], cash: Decimal, target: dict[str, Decimal], labels: dict[str, str],
          unheld_prices: dict[str, tuple[Decimal, date, str]], s: BuildSettings,
          excluded: dict[str, str] | None = None) -> BuildResult:
    """Everything the proposal shows, from current holdings to the target."""
    excluded = excluded or {}
    warnings: list[dict] = []
    notes: list[str] = []
    unit = _units(s.sale.fractional)
    priced = [h for h in holdings if h.price is not None and h.market_value is not None]
    unpriced = [h for h in holdings if h.price is None or h.market_value is None]
    for h in unpriced:
        warnings.append({"code": "cannot_value", "message": f"{h.label}: cannot value — needs a price; it is not "
                         "traded and is left out of the account value", "key": h.key})
    A, before = current_weights(holdings, cash)
    if A <= 0:
        raise ValueError("the account has no valued holdings or cash to rebalance")
    tgt = dict(target)
    extra = ZERO
    if s.withdrawal:
        with localcontext() as ctx:
            ctx.prec = 60
            extra = qw(min(s.withdrawal / A, ONE))
    tgt, n2 = apply_cash_floor(tgt, s.cash_floor, extra)
    notes += n2
    held = {h.key: h for h in priced}
    T = {k: w * A for k, w in tgt.items()}

    # sells
    requests: list[tuple[Holding, Decimal]] = []
    sell_reason: dict[str, str] = {}
    dust = 0
    for h in priced:
        t = T.get(h.key, ZERO)
        if h.key not in tgt or tgt.get(h.key, ZERO) == 0:
            if h.market_value < s.min_trade:
                dust += 1
                continue
            requests.append((h, h.quantity))
            sell_reason[h.key] = (f"Excluded from this account ({excluded[h.key]}) — sold" if h.key in excluded
                                  else "Not in the model — sold")
        elif h.market_value - t > 0:
            if h.market_value - t < s.min_trade:
                dust += 1
                continue
            q = _floor((h.market_value - t) / h.price, unit)
            if q <= 0:
                dust += 1
                continue
            requests.append((h, min(q, h.quantity)))
            sell_reason[h.key] = (f"Trim to the model weight {pct(tgt[h.key])} (holds {pct(before.get(h.key, ZERO))})")
    asked = {h.key: q for h, q in requests}
    sp = plan_sales(requests, s.sale)
    for b in sp.blocked:
        warnings.append({"code": "unknown_cost_basis", "message": f"{labels.get(b['key'], b['key'])}: a lot with no "
                         "cost basis is not sold while a gain budget applies", "key": b["key"]})
    trades: list[TradeOut] = []
    proceeds = ZERO
    sold: dict[str, Decimal] = {}
    st_total = lt_total = ZERO
    for h, _ in requests:
        portions = sp.now.get(h.key) or []
        q = sum((p.quantity for p in portions), ZERO)
        if q <= 0:
            continue
        if q * h.price < s.min_trade:
            dust += 1
            continue
        st = sum((p.gain for p in portions if p.gain is not None and p.term != "long"), ZERO)
        lt = sum((p.gain for p in portions if p.gain is not None and p.term == "long"), ZERO)
        st_total += st
        lt_total += lt
        sold[h.key] = q
        proceeds += q * h.price
        reason = sell_reason[h.key]
        if q < asked[h.key]:
            reason += "; part of it waits (gain budget / short-term guard — see sell later)"
        trades.append(TradeOut(h.key, h.label, "sell", q, None, h.price, h.price_source, h.price_as_of,
                               [{"lot_id": p.lot_id, "quantity": p.quantity, "unit_cost": p.unit_cost,
                                 "term": p.term, "est_gain": p.gain,
                                 "acquired_date": p.acquired.isoformat() if p.acquired else None}
                                for p in portions if p.lot_id],
                               st, lt, reason))
    # buys from the cash raised above the cash target
    available = cash + proceeds - T.get(CASH, ZERO)
    wants = []
    for k, w in sorted(tgt.items()):
        if k == CASH or w <= 0:
            continue
        cur = held[k].market_value if k in held else ZERO
        need = T[k] - cur
        if k in excluded:
            continue
        if any(h.key == k for h in unpriced):
            continue          # cannot value what it holds → never traded
        if need <= 0:
            continue
        if need < s.min_trade:
            dust += 1
            continue
        wants.append((k, need))
    total_need = sum((n for _, n in wants), ZERO)
    scale = ONE
    if total_need > 0 and available < total_need:
        with localcontext() as ctx:
            ctx.prec = 60
            scale = max(ZERO, available) / total_need
        if wants:
            warnings.append({"code": "buys_scaled", "message": f"buys scaled to {pct(scale)} of what the target "
                             "needs — the sales the gain budget allows do not raise enough cash"})
    bought: dict[str, Decimal] = {}
    for k, need in wants:
        dollars = need * scale
        if dollars < s.min_trade or dollars <= 0:
            dust += 1
            continue
        if k in held:
            price, as_of, src = held[k].price, held[k].price_as_of, held[k].price_source
        elif k in unheld_prices:
            price, as_of, src = unheld_prices[k]
        else:
            price = as_of = None
            src = "none"
        label = labels.get(k, k)
        if price is not None:
            q = _floor(dollars / price, unit)
            if q <= 0 or q * price < s.min_trade:
                dust += 1
                continue
            bought[k] = q * price
            trades.append(TradeOut(k, label, "buy", q, None, price, src, as_of, [], ZERO, ZERO,
                                   f"Buy to the model weight {pct(tgt[k])} (holds {pct(before.get(k, ZERO))})"))
        else:
            n = dollars.quantize(CENT, rounding=ROUND_DOWN)
            if n < s.min_trade or n <= 0:
                dust += 1
                continue
            bought[k] = n
            trades.append(TradeOut(k, label, "buy", None, n, None, "none", None, [], ZERO, ZERO,
                                   f"Buy to the model weight {pct(tgt[k])} — no price for it, so it is bought by "
                                   f"amount (${n}), never a guessed number of shares"))
            warnings.append({"code": "unpriced_buy", "message": f"{label}: no price — bought by notional", "key": k})
    if dust:
        warnings.append({"code": "dust_suppressed", "message": f"{dust} trade(s) under the minimum trade size "
                         f"(${money(s.min_trade)}) were left out", "count": dust})
    # after
    vals = {h.key: h.market_value for h in priced}
    for k, q in sold.items():
        vals[k] = vals[k] - q * held[k].price
    for k, v in bought.items():
        vals[k] = vals.get(k, ZERO) + v
    cash_after = cash + proceeds - sum(bought.values(), ZERO)
    vals = {k: v for k, v in vals.items() if v != 0 or k in tgt}
    after = _weights({**vals, CASH: cash_after}, A)
    tgt_q = {k: qw(v) for k, v in tgt.items()}
    # plan + embedded gain
    plan = []
    embedded = ZERO
    later_by_key: dict[str, list[LaterSale]] = {}
    for x in sp.later:
        later_by_key.setdefault(x.key, []).append(x)
    for h in holdings:
        if h.price is None or h.market_value is None:
            plan.append({"key": h.key, "label": h.label, "action": "cannot_value", "quantity": h.quantity,
                         "reason": "cannot value — needs a price"})
            continue
        for lot in h.lots:
            if lot.unit_cost is not None:
                embedded += (h.price - lot.unit_cost) * lot.quantity
        in_t = tgt.get(h.key, ZERO) > 0
        later = later_by_key.get(h.key, [])
        q_now = sold.get(h.key, ZERO)
        if not in_t:
            action = "sell_now" if not later else ("sell_later" if q_now == 0 else "sell_now_and_later")
        elif q_now > 0:
            action = "trim"
        elif h.key in bought:
            action = "add"
        else:
            action = "keep"
        plan.append({"key": h.key, "label": h.label, "action": action, "quantity": h.quantity,
                     "sell_now": q_now, "sell_later": [{"year": x.year, "on_date": x.on_date.isoformat()
                                                        if x.on_date else None, "quantity": x.quantity,
                                                        "est_gain": x.gain, "reason": x.reason} for x in later]})
    for k in bought:
        if k not in held:
            plan.append({"key": k, "label": labels.get(k, k), "action": "add", "quantity": ZERO})
    years = sorted({x.year for x in sp.later if x.year is not None})
    if any(x.year is None for x in sp.later):
        ytc = MAX_PLAN_YEARS
    elif years:
        ytc = years[-1] - s.sale.today.year + 1
    elif trades:
        ytc = 1
    else:
        ytc = 0
    by_year: dict[int, dict] = {}
    if sold:
        by_year[s.sale.today.year] = {"year": s.sale.today.year, "est_gain": st_total + lt_total,
                                      "sales": len(sold)}
    for x in sp.later:
        if x.year is None:
            continue
        e = by_year.setdefault(x.year, {"year": x.year, "est_gain": ZERO, "sales": 0})
        e["est_gain"] += x.gain or ZERO
        e["sales"] += 1
    if sp.later:
        warnings.append({"code": "sell_later", "message": f"{len(sp.later)} lot portion(s) wait for a later year "
                         "or for the day a short-term lot turns long-term", "count": len(sp.later)})
    return BuildResult(
        account_value=A, cash=cash, target=tgt_q, before=before, after=after,
        drift_before=qw(max_drift(before, tgt_q)), drift_after=qw(max_drift(after, tgt_q)), trades=trades,
        later=sp.later, warnings=warnings, notes=notes, plan=plan, est_st_gain=st_total, est_lt_gain=lt_total,
        total_embedded_gain=embedded, years_to_complete=ytc,
        plan_by_year=[by_year[y] for y in sorted(by_year)])


# ── Wash-sale flag (basic; rep6 replaces it) ────────────────────────────────
def wash_sale_flags(trades: list[TradeOut], household_txns: list[dict], today: date) -> dict[int, dict]:
    """{trade index: flag}. ``household_txns``: {key, side 'buy'|'sell',
    trade_date, quantity, loss (True / False / None = unknown), account}."""
    start = today - timedelta(days=WASH_WINDOW_DAYS)
    flags: dict[int, dict] = {}
    for i, t in enumerate(trades):
        recent = [x for x in household_txns if x["key"] == t.key and start <= x["trade_date"] <= today]
        if t.side == "buy":
            hits = [x for x in recent if x["side"] == "sell" and x.get("loss") is not False]
            kind = "buy_after_loss_sale"
            note = ("the household sold this security at a loss in the prior 30 days — buying it now may be a "
                    "wash sale")
        else:
            loss = any(lot.get("est_gain") is not None and lot["est_gain"] < 0 for lot in t.lots) or (
                t.est_st_gain + t.est_lt_gain < 0)
            if not loss:
                continue
            hits = [x for x in recent if x["side"] == "buy"]
            kind = "loss_sale_after_buy"
            note = ("this sale realizes a loss and the household bought this security in the prior 30 days — the "
                    "loss may be disallowed as a wash sale")
        if hits:
            flags[i] = {"rule": "basic_61_day", "kind": kind, "note": note + ". A flag only: it does not block "
                        "the trade (full household wash-sale control is rep6).",
                        "window_days": WASH_WINDOW_DAYS,
                        "matches": [{"account": x.get("account"), "registration_type": x.get("registration_type"),
                                     "tax_status": x.get("tax_status"), "side": x["side"],
                                     "trade_date": x["trade_date"].isoformat(),
                                     "quantity": None if x.get("quantity") is None else str(x["quantity"]),
                                     "loss_known": x.get("loss") is not None} for x in hits]}
    return flags
