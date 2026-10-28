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

THE LOT-RELIEF METHOD (lotmethod) — :class:`LotMethod` on ``SaleSettings.method``
──────────────────────────────────────────────────────────────────────────────
Precedence, always: WASH-SALE HOLDS and HARD BLOCKS (unknown cost against a
budget) > GUARDS (the short-term guard defers; the gain budget decides now or
later) > THE METHOD (which lots, in what order). A method only orders lots;
it never sells a wash-held loss lot, never sells an unknown-cost lot against a
budget, never sells a deferred short-term gain now, never exceeds the budget.
  default (no method set)  today's rule, EXACTLY — the code path above, unchanged.
  tax_optimized            estimated tax per dollar raised, lowest first:
                           (price − cost) ÷ price × the lot's combined rate (ST
                           rate when short-term on the sale date, else LT) from
                           the tax subject's approved rep6 profile. So a short-
                           term loss ranks ahead of a long-term loss of the same
                           size, a long-term gain ahead of a short-term gain.
                           No profile → today's rule exactly, and the line says so.
  hifo / highest_cost      highest unit cost first (the same ordering under
                           specific identification; both names kept because
                           custodians use both); lowest_cost the reverse;
  fifo / lifo              oldest / newest acquisition first.
  specific                 the adviser's picked lots (validated: an open lot of
                           this account in this security, enough quantity, not
                           wash-held, not unknown-cost against a budget), used
                           as given; a shortfall is filled by today's rule, stated.
Gain preference: prefer_long_term (ties → long-term first), st_losses_first
(short-term losses ahead of everything), realize_gains (gain lots, long-term
first, until the adviser's target gain is reached — the lot that crosses it is
sold only as far as the target; it only chooses lots for sales the proposal
already makes, never adds one). AVERAGE COST (a security in
``LotMethod.average_cost``): no lot choice — every share carries the average
cost; holding periods first-in-first-out; the line names no lots. The method
selects WHICH lots fill a sale; the budget then decides now or later (losses
among the selected go now; selected gains in the method's order while they fit).

WHAT IS BOUGHT — :func:`build`
──────────────────────────────────────────────────────────────────────────────
To the target weight, from the cash the sales raise above the cash target —
scaled down pro rata when the budget held the sales back. A priced security is
bought in whole shares (fractional only when the custodian's fractional_shares
is 'yes'); an UNPRICED one by notional (dollars) — never a guessed quantity. A
holding the account cannot value is never traded. Trades under the minimum
trade size are dropped as dust (counted in a warning).

WASH-SALE CONTROL (rep6 — replaced rep5's flag) — services.wash_sale
──────────────────────────────────────────────────────────────────────────────
The engine's part is rule (b): ``SaleSettings.wash_hold`` names the securities
whose LOSS lots must wait (a household buy within 30 days); :func:`plan_sales`
holds those lots back — they never add gain-budget room — and the plan sells
the account's other lots instead where it wants them. The held lots come back
on ``BuildResult.wash_held`` (and in "sell later" from their clear day); the
buy rules (a, c, d) run on the built trades in services.wash_sale.control.
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


METHODS = ("tax_optimized", "hifo", "fifo", "lifo", "lowest_cost", "highest_cost", "specific")
GAIN_PREFERENCES = ("prefer_long_term", "st_losses_first", "realize_gains")
DEFAULT_METHOD = "tax_optimized"
DEFAULT_PREFERENCE = "prefer_long_term"
TODAY_RULE = ("today's rule: losses first (largest loss per share first), then the lowest gain per dollar raised; "
              "long-term before short-term at equal cost; then oldest")
METHOD_LABELS = {"tax_optimized": "Tax-optimized — lowest estimated tax per dollar raised",
                 "hifo": "HIFO — highest cost first", "fifo": "FIFO — oldest first", "lifo": "LIFO — newest first",
                 "lowest_cost": "Lowest cost first", "highest_cost": "Highest cost first",
                 "specific": "Specific lots — the adviser picks them on each proposal",
                 "average_cost": "Average cost — no lot choice", "harvest_loss": "Tax-loss harvest — each qualifying "
                 "loss lot, whole"}
PREFERENCE_LABELS = {"prefer_long_term": "Prefer long-term", "st_losses_first": "Short-term losses first",
                     "realize_gains": "Realize gains up to a target"}


class LotPickError(ValueError):
    """An adviser's picked lot the rules refuse (closed, too small, wash-held, …)."""


@dataclass
class LotMethod:
    """Which lots a sale relieves. ``LotMethod()`` with no rates = today's rule."""
    method: str = DEFAULT_METHOD
    gain_preference: str = DEFAULT_PREFERENCE
    realize_target: Decimal | None = None
    #: tax_profiles.combined_rates of the tax subject — None: no profile (or no method set: today's rule)
    rates: dict | None = None
    #: method 'specific' — {security: [(lot_id, quantity), …]} in the adviser's order
    picks: dict = field(default_factory=dict)
    #: securities carried at average cost in this account (no lot choice)
    average_cost: frozenset = frozenset()
    source: str = "default"

    def is_today_rule(self) -> bool:
        return (self.method == DEFAULT_METHOD and self.rates is None and self.gain_preference == DEFAULT_PREFERENCE
                and not self.picks)


@dataclass
class SaleSettings:
    today: date
    gain_budget: Decimal | None          # None = unlimited
    realized_ytd: Decimal = ZERO
    avoid_short_term: bool = True
    short_term_wait_days: int = 30
    fractional: bool = False
    #: rep6 — {security: {until, reason, matches, forfeit}}: its loss lots wait (wash sale)
    wash_hold: dict = field(default_factory=dict)
    #: lotmethod — the lot-relief method (default: today's rule)
    method: LotMethod = field(default_factory=LotMethod)


@dataclass
class LotSale:
    lot_id: str | None
    quantity: Decimal
    unit_cost: Decimal | None
    term: str
    gain: Decimal | None
    acquired: date | None
    why: str = ""
    average_cost: bool = False


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
    held: list = field(default_factory=list)
    #: lotmethod — {security: (method key, plain note)}: what chose each holding's lots
    method_of: dict = field(default_factory=dict)


def _units(fractional: bool) -> Decimal:
    return QTY if fractional else ONE


def _floor(q: Decimal, unit: Decimal) -> Decimal:
    return (q / unit).to_integral_value(rounding=ROUND_DOWN) * unit if q > 0 else ZERO


def plan_sales(requests: list[tuple[Holding, Decimal]], s: SaleSettings) -> SalePlan:
    """``requests`` [(holding, quantity wanted)] — which lots go now, which
    later (by year, or by the date a short-term lot turns long-term).

    The default method is TODAY'S RULE, run by :func:`_plan_today_rule`
    exactly as it was before lotmethod; any other method (or an average-cost
    holding) goes through :func:`_plan_by_method`, which keeps the same guards
    and budget and changes only which lots are selected."""
    m = s.method or LotMethod()
    if m.is_today_rule() and not any(h.key in m.average_cost for h, _ in requests):
        plan = _plan_today_rule(requests, s)
        note = _today_note(m)
        for h, _ in requests:
            plan.method_of[h.key] = (m.method, note)
        for portions in plan.now.values():
            for p in portions:
                p.why = _today_why(p)
        return plan
    return _plan_by_method(requests, s)


def _today_note(m: LotMethod) -> str:
    if m.source == "default":
        return f"no lot method set — {TODAY_RULE}"
    return f"tax-optimized: no tax profile, so not estimated — {TODAY_RULE}"


def _today_why(p: LotSale) -> str:
    if p.gain is None:
        return "cost basis unknown — sold only because no gain budget applies"
    if p.gain <= 0:
        return "a loss (or no gain) — losses first, the largest loss per share first"
    return f"a {p.term}-term gain — the lowest gain per dollar raised that fits the budget"


def _plan_today_rule(requests: list[tuple[Holding, Decimal]], s: SaleSettings) -> SalePlan:
    """TODAY'S RULE — the pre-lotmethod plan_sales body, unchanged."""
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
    held: list[dict] = []  # rep6: loss lots held back by wash-sale control
    for h, _ in requests:
        price = h.price
        for lot in sorted(h.lots, key=lambda x: lot_order_key(x, price, s.today)):
            g = None if lot.unit_cost is None else price - lot.unit_cost
            term = term_of(lot, s.today)
            if g is not None and g < 0 and h.key in s.wash_hold:
                held.append({"holding": h, "lot": lot, "gain": g, "term": term})
                continue
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
    # 4. rep6: wash-held loss lots wait for their clear day (they never used the budget)
    for x in held:
        h, lot = x["holding"], x["lot"]
        q = min(lot.quantity, wanted[h.key]) if wanted[h.key] > 0 else ZERO
        if q <= 0:
            continue
        wanted[h.key] -= q
        r = s.wash_hold[h.key]
        x["quantity"] = q
        later.append(LaterSale(h.key, lot.id, q, r["until"].year, r["until"], q * x["gain"],
                               f"wash sale: loss lot held until {r['until'].isoformat()}"))
    return SalePlan(now=now, later=later, blocked=blocked, net_gain_now=net,
                    room_now=None if room is None else room, held=[x for x in held if x.get("quantity")])


# ── The method (lotmethod) ──────────────────────────────────────────────────
def today_key(lot: Lot, price: Decimal, on: date):
    """Today's rule as ONE total order (the order _plan_today_rule ends up
    selecting in): losses by lot_order_key, then unknown-cost lots, then gains
    by gain per dollar raised (long-term first at equal, then oldest, then id)."""
    cost = lot.unit_cost
    lt = 0 if term_of(lot, on) == "long" else 1
    acq, lid = lot.acquired or date.max, lot.id or ""
    if cost is None:
        return (1, ZERO, lt, acq, lid)
    g = price - cost
    if g > 0:
        return (2, g / price, lt, acq, lid)
    return (0, -cost, lt, acq, lid)


def tax_per_dollar(lot: Lot, price: Decimal, on: date, rates: dict) -> Decimal | None:
    """Estimated tax per dollar raised: (price − cost) ÷ price × the lot's
    combined rate (ST when short-term on ``on``, else LT). None when the cost
    or the term is unknown — never a guessed rate."""
    if lot.unit_cost is None or price is None or price <= 0:
        return None
    term = term_of(lot, on)
    if term not in ("short", "long"):
        return None
    rate = rates["st"] if term == "short" else rates["lt"]
    with localcontext() as ctx:
        ctx.prec = 60
        return (price - lot.unit_cost) / price * rate


def method_key(m: LotMethod, lot: Lot, price: Decimal, on: date):
    """The method's order (smallest first). Long-term before short-term breaks
    every tie (prefer_long_term), then oldest, then id."""
    cost = lot.unit_cost
    lt = 0 if term_of(lot, on) == "long" else 1
    acq, lid = lot.acquired or date.max, lot.id or ""
    k = m.method
    if k in ("hifo", "highest_cost"):
        base = (cost is None, -(cost if cost is not None else ZERO), lt, acq, lid)
    elif k == "lowest_cost":
        base = (cost is None, cost if cost is not None else ZERO, lt, acq, lid)
    elif k == "fifo":
        base = (lot.acquired is None, acq, lt, lid)
    elif k == "lifo":
        base = (lot.acquired is None, -(lot.acquired.toordinal()) if lot.acquired else 0, lt, lid)
    elif k == "tax_optimized" and m.rates is not None:
        t = tax_per_dollar(lot, price, on, m.rates)
        base = (0, t, lt, acq, lid) if t is not None else (1, today_key(lot, price, on))
    else:                      # tax_optimized with no profile, and the fill for 'specific'
        base = (0, today_key(lot, price, on))
    if m.gain_preference == "st_losses_first":
        st_loss = cost is not None and price - cost < 0 and term_of(lot, on) == "short"
        return (0 if st_loss else 1, base)
    return base


def _why(m: LotMethod, lot: Lot, price: Decimal, on: date, how: str) -> str:
    term = term_of(lot, on)
    cost = lot.unit_cost
    if how == "picked":
        return "picked by the adviser"
    if how == "fill":
        return "not picked — the rest of the sale filled by today's rule"
    if how == "target":
        return f"a {term}-term gain taken to realize the adviser's target (long-term first)"
    if how == "average":
        return "average cost — no lot choice; holding period first in, first out"
    if m.gain_preference == "st_losses_first" and cost is not None and price - cost < 0 and term == "short":
        return "a short-term loss — short-term losses first"
    k = m.method
    if k in ("hifo", "highest_cost"):
        return f"highest cost first (unit cost {cost.quantize(QTY) if cost is not None else 'unknown'})"
    if k == "lowest_cost":
        return f"lowest cost first (unit cost {cost.quantize(QTY) if cost is not None else 'unknown'})"
    if k == "fifo":
        return f"oldest first (acquired {lot.acquired.isoformat() if lot.acquired else 'unknown'})"
    if k == "lifo":
        return f"newest first (acquired {lot.acquired.isoformat() if lot.acquired else 'unknown'})"
    if k == "tax_optimized" and m.rates is not None:
        t = tax_per_dollar(lot, price, on, m.rates)
        if t is None:
            return "cost or term unknown — not estimated; ranked after every estimated lot"
        rate = m.rates["st"] if term == "short" else m.rates["lt"]
        return (f"est. tax per $ raised {t.quantize(Q, rounding=ROUND_HALF_EVEN)} ({term}-term at {rate}, "
                "profile rates — an estimate)")
    return _today_why(LotSale(lot.id, ZERO, cost, term, None if cost is None else price - cost, lot.acquired))


def _average_lots(h: Holding) -> tuple[list[Lot], Decimal | None]:
    """An average-cost holding as first-in-first-out portions, every share at
    the average cost (None when any lot's cost is unknown)."""
    lots = sorted(h.lots, key=lambda x: (x.acquired or date.max, x.id or ""))
    q = sum((x.quantity for x in lots), ZERO)
    avg = None
    if lots and q > 0 and all(x.unit_cost is not None for x in lots):
        with localcontext() as ctx:
            ctx.prec = 60
            avg = sum((x.quantity * x.unit_cost for x in lots), ZERO) / q
    return [Lot(None, x.quantity, avg, x.acquired, x.term_hint) for x in lots], avg


def validate_picks(m: LotMethod, requests: list[tuple[Holding, Decimal]], s: SaleSettings) -> None:
    """The adviser's picked lots (method 'specific'): each an open lot of this
    account in a security this sale sells, with enough quantity, not wash-held,
    not unknown-cost against a gain budget, the picks not over the sale."""
    sold = {h.key: (h, q) for h, q in requests}
    for key, picks in (m.picks or {}).items():
        if key not in sold:
            raise LotPickError(f"lots were picked for {key}, which this proposal does not sell")
        h, want = sold[key]
        if key in m.average_cost:
            raise LotPickError(f"{h.label}: held at average cost — there is no lot choice to make")
        by_id = {x.id: x for x in h.lots if x.id}
        seen, total = set(), ZERO
        for lot_id, qty in picks:
            lot = by_id.get(lot_id)
            if lot is None:
                raise LotPickError(f"{h.label}: lot {lot_id} is not an open lot of this account in this security")
            if lot_id in seen:
                raise LotPickError(f"{h.label}: lot {lot_id} is picked twice")
            seen.add(lot_id)
            if not isinstance(qty, Decimal) or qty <= 0:
                raise LotPickError(f"{h.label}: a picked quantity is a decimal above zero")
            if qty > lot.quantity:
                raise LotPickError(f"{h.label}: lot {lot_id} holds {lot.quantity}, fewer than the {qty} picked")
            g = None if lot.unit_cost is None else h.price - lot.unit_cost
            if g is not None and g < 0 and key in s.wash_hold:
                raise LotPickError(f"{h.label}: lot {lot_id} is a loss lot held back by wash-sale control until "
                                   f"{s.wash_hold[key]['until'].isoformat()} — a method never overrides the hold")
            if g is None and s.gain_budget is not None:
                raise LotPickError(f"{h.label}: lot {lot_id} has no cost basis — never sold against a gain budget")
            total += qty
        if total > want:
            raise LotPickError(f"{h.label}: the picked lots ({total}) are more than this sale sells ({want})")


def _plan_by_method(requests: list[tuple[Holding, Decimal]], s: SaleSettings) -> SalePlan:
    """Any method other than today's rule: the same guards and budget as
    _plan_today_rule; only the SELECTION of lots differs (see the module doc)."""
    m = s.method
    validate_picks(m, requests, s)
    unit = _units(s.fractional)
    budget = s.gain_budget
    room = None if budget is None else budget - s.realized_ytd
    now: dict[str, list[LotSale]] = {}
    later: list[LaterSale] = []
    blocked: list[dict] = []
    net = ZERO
    wanted = {h.key: q for h, q in requests}
    deferred, held, selected = [], [], []
    method_of: dict = {}
    target_left = m.realize_target if m.gain_preference == "realize_gains" else None
    seq = 0
    for h, _ in requests:
        price = h.price
        avg = h.key in m.average_cost
        if avg:
            lots, avg_cost = _average_lots(h)
            method_of[h.key] = ("average_cost", "average cost — no lot choice: every share at the average cost "
                                f"{'unknown' if avg_cost is None else avg_cost.quantize(QTY)}; holding periods first "
                                "in, first out; the instruction names no lots")
            order = lots
        else:
            lots = list(h.lots)
            order = sorted(lots, key=lambda x: method_key(m, x, price, s.today))
            method_of[h.key] = (m.method, _method_note(m))
        portions = []
        for lot in order:
            g = None if lot.unit_cost is None else price - lot.unit_cost
            term = term_of(lot, s.today)
            if g is not None and g < 0 and h.key in s.wash_hold:
                held.append({"holding": h, "lot": lot, "gain": g, "term": term})
                continue
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
            portions.append([lot, g, term, lot.quantity])        # remaining quantity available to select
        # selection, in the method's order (picks first for 'specific'; target gains first for realize_gains)
        rem = wanted[h.key]

        def pick(entry, q, how):
            nonlocal rem, seq
            lot, g, term, _ = entry
            entry[3] -= q
            rem -= q
            seq += 1
            selected.append((h, lot, q, g, term, _why(m, lot, price, s.today, "average" if avg else how),
                             method_key(m, lot, price, s.today), seq, avg))

        if m.method == "specific" and not avg:
            by_id = {e[0].id: e for e in portions}
            for lot_id, q in m.picks.get(h.key, []):
                e = by_id.get(lot_id)
                if e is None:          # picked but deferred by the short-term guard: the guard wins (stated later)
                    continue
                q = min(q, e[3], rem)
                if q > 0:
                    pick(e, q, "picked")
            fill = sorted(portions, key=lambda e: today_key(e[0], price, s.today))
            for e in fill:
                if rem <= 0:
                    break
                q = min(e[3], rem)
                if q > 0:
                    pick(e, q, "fill")
            if not m.picks.get(h.key):
                method_of[h.key] = ("specific", f"specific: no lots picked for {h.label} — {TODAY_RULE}")
            continue
        if target_left is not None and target_left > 0 and not avg:
            gains = sorted((e for e in portions if e[1] is not None and e[1] > 0),
                           key=lambda e: (0 if e[2] == "long" else 1, method_key(m, e[0], price, s.today)))
            for e in gains:
                if rem <= 0 or target_left <= 0:
                    break
                q = min(e[3], rem, _floor(target_left / e[1], unit))
                if q > 0:
                    pick(e, q, "target")
                    target_left -= q * e[1]
        for e in portions:
            if rem <= 0:
                break
            q = min(e[3], rem)
            if q > 0:
                pick(e, q, "method")

    def take(h: Holding, lot: Lot, q: Decimal, g, term, why, avg):
        nonlocal net
        gain = None if g is None else q * g
        rows = now.setdefault(h.key, [])
        same = next((x for x in rows if lot.id is not None and x.lot_id == lot.id), None)
        if same is not None:          # one lot selected twice (target gain + the method's fill): one instruction lot
            same.quantity += q
            same.gain = None if gain is None or same.gain is None else same.gain + gain
            if why not in same.why:
                same.why = f"{same.why}; {why}"
        else:
            rows.append(LotSale(lot.id, q, lot.unit_cost, term, gain, lot.acquired, why, avg))
        wanted[h.key] -= q
        if gain is not None:
            net += gain

    # 1. every selected loss (and zero-gain, and unknown-cost when unlimited) portion goes now — they add room
    gains = []
    for h, lot, q, g, term, why, key, order_no, avg in selected:
        if g is None or g <= 0:
            take(h, lot, q, g, term, why, avg)
        else:
            gains.append((h, lot, q, g, term, why, key, order_no, avg))
    # 2. selected gains in the method's order (picks / target first: selection order) while they fit the budget
    if m.method == "specific" or m.gain_preference == "realize_gains":
        gains.sort(key=lambda x: x[7])
    else:
        gains.sort(key=lambda x: (x[6], x[7]))
    leftover = []
    for h, lot, q, g, term, why, _, _, avg in gains:
        if room is None or net + q * g <= room:
            take(h, lot, q, g, term, why, avg)
            continue
        fit = _floor((room - net) / g, unit) if room - net > 0 else ZERO
        fit = min(fit, q)
        if fit > 0:
            take(h, lot, fit, g, term, why, avg)
        if q - fit > 0:
            leftover.append((h, lot, q - fit, g))
            wanted[h.key] -= q - fit
    # 3. what waits — exactly as today's rule schedules it
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
    for x in held:
        h, lot = x["holding"], x["lot"]
        q = min(lot.quantity, wanted[h.key]) if wanted[h.key] > 0 else ZERO
        if q <= 0:
            continue
        wanted[h.key] -= q
        r = s.wash_hold[h.key]
        x["quantity"] = q
        later.append(LaterSale(h.key, lot.id, q, r["until"].year, r["until"], q * x["gain"],
                               f"wash sale: loss lot held until {r['until'].isoformat()}"))
    return SalePlan(now=now, later=later, blocked=blocked, net_gain_now=net,
                    room_now=None if room is None else room, held=[x for x in held if x.get("quantity")],
                    method_of=method_of)


def _method_note(m: LotMethod) -> str:
    label = METHOD_LABELS.get(m.method, m.method)
    if m.method == "tax_optimized":
        if m.rates is None:
            note = _today_note(m)
        else:
            note = (f"{label}: (price − cost) ÷ price × the combined rate — ST {m.rates['st']}, LT {m.rates['lt']} "
                    f"(profile {m.rates.get('profile_id')}, tax year {m.rates.get('tax_year')}, as of "
                    f"{m.rates.get('as_of')}) — an estimate")
    elif m.method == "specific":
        note = f"{label}: the lots the adviser picked, as given; any rest filled by today's rule"
    else:
        note = label + (" (the same ordering as HIFO under specific identification)" if m.method == "highest_cost"
                        else "")
    if m.gain_preference == "st_losses_first":
        note += "; short-term losses first"
    elif m.gain_preference == "realize_gains":
        note += f"; realize gains up to the adviser's target ${money(m.realize_target)} (long-term first)"
    return note + " — wash-sale holds, hard blocks and the guards come first"


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
    #: lotmethod — what chose this sale's lots (None on a buy)
    lot_method: str | None = None
    method_note: str | None = None


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
    wash_held: list[dict] = field(default_factory=list)


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
        mk, mnote = sp.method_of.get(h.key, (None, None))
        trades.append(TradeOut(h.key, h.label, "sell", q, None, h.price, h.price_source, h.price_as_of,
                               [{"lot_id": p.lot_id, "quantity": p.quantity, "unit_cost": p.unit_cost,
                                 "term": p.term, "est_gain": p.gain,
                                 "acquired_date": p.acquired.isoformat() if p.acquired else None, "why": p.why,
                                 **({"average_cost": True} if p.average_cost else {})}
                                for p in portions if p.lot_id or p.average_cost],
                               st, lt, reason, mk, mnote))
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
    wash_held: dict[str, dict] = {}
    for x in sp.held:
        h, lot = x["holding"], x["lot"]
        e = wash_held.setdefault(h.key, {"key": h.key, "label": h.label, "price": h.price,
                                         "price_source": h.price_source, "price_as_of": h.price_as_of,
                                         "restriction": s.sale.wash_hold[h.key], "lots": []})
        e["lots"].append({"lot_id": lot.id, "quantity": x["quantity"], "unit_cost": lot.unit_cost, "term": x["term"],
                          "est_gain": x["quantity"] * x["gain"],
                          "acquired_date": lot.acquired.isoformat() if lot.acquired else None})
    if wash_held:
        warnings.append({"code": "wash_sale_held", "message": f"loss lots of {len(wash_held)} security(ies) are "
                         "held back by wash-sale control (a household buy within 30 days) — see the deferred lines"})
    return BuildResult(
        account_value=A, cash=cash, target=tgt_q, before=before, after=after,
        drift_before=qw(max_drift(before, tgt_q)), drift_after=qw(max_drift(after, tgt_q)), trades=trades,
        later=sp.later, warnings=warnings, notes=notes, plan=plan, est_st_gain=st_total, est_lt_gain=lt_total,
        total_embedded_gain=embedded, years_to_complete=ytc,
        plan_by_year=[by_year[y] for y in sorted(by_year)], wash_held=list(wash_held.values()))


# ── After the trades that will actually happen (rep6) ─────────────────────
def after_weights(holdings: list[Holding], cash: Decimal, trades: list[TradeOut], account_value: Decimal
                  ) -> dict[str, Decimal]:
    """Weights after ONLY ``trades`` (the lines wash-sale control lets
    through), valued as :func:`build` values them."""
    vals = {h.key: h.market_value for h in holdings if h.price is not None and h.market_value is not None}
    c = cash
    for t in trades:
        amount = t.notional if t.notional is not None else t.quantity * t.price
        if t.side == "sell":
            vals[t.key] = vals.get(t.key, ZERO) - amount
            c += amount
        else:
            vals[t.key] = vals.get(t.key, ZERO) + amount
            c -= amount
    return _weights({**{k: v for k, v in vals.items() if v != 0}, CASH: c}, account_value)
