"""REP13 — the long/short extension's PURE rules (no database, no network, no model call).

Joe's design (spec "Replication Studio — ETF & Fund-to-SMA Spec", 2026-10-22, "Long/short and options overlays"):
$100 of capital holds $130 long and $30 short (150/50: $150 / $50). The shorts are the names the signal likes
least. Hollisworks has NO stock-selection model: the signal is a sub-adviser's TARGET WEIGHTS, uploaded as a model
whose weights may be negative and sum to the net exposure (100 %).

THE TWO BOOKS (:func:`build_books`)
  capital C = long market value − short market value + cash (net liquidation value; an unpriced holding is not
  valued and never traded). Each name's signed target weight w becomes a long target max(w, 0)·C and a short
  target max(−w, 0)·C, sized in whole shares (floor) unless the custodian declares fractional shares.
    long  — below target: BUY; above: SELL (lots chosen by rep5's plan_sales: method, gain budget, short-term
            guard). A sale is never larger than the shares held — a long book never sells what it does not own.
    short — below target: SELL SHORT (open); above: BUY TO COVER (close, oldest short lot first unless a harvest
            names the lots).
  Before sizing, the account's settings act on every short name, in this order:
    excluded (the account's or the model's never-short list)  → not shorted, reason stated;
    §1259 constructive sale (see below)                        → the line is BLOCKED (recorded, never routed);
    no borrow rate on record                                   → the line is kept and flagged 'borrow rate needed'
                                                                 (approval is refused until one is entered);
    borrow rate above the account's ceiling                    → not shorted, reason stated;
    a household cover at a loss of an identical name ≤ 30 days → DEFERRED (short-sale wash rule, below);
    |w| above the account's maximum short per name             → capped at the maximum, stated.
  A name the settings do not short simply has no short: the long book is unchanged, so the net exposure rises —
  stated as a warning with the figure, never re-allocated by guess. Buys are scaled to the cash the sales and the
  placed shorts raise (as rep5 does) and the scale is stated.

BORROW — an estimate from the ENTERED borrow-rate table (no live borrow feed exists): per short line, the annual
  fee = shares × price × rate_bps / 10,000 and the rebate likewise at rebate_bps; the cost comparison adds
  "Borrow cost (long/short only) — estimated" and "Short rebate" over the short book after the proposal.

TAX
  * Short-term rule — the gain or loss on closing a short sale is SHORT-TERM regardless of how long the short was
    open (IRC §1233(a)–(b): the property delivered to close is treated as held for the period it was held to
    close, i.e. ordinarily not more than one year). :func:`short_close_term` returns 'short' always.
  * §1259 constructive sale — shorting a security while the household holds the SAME or a SUBSTANTIALLY IDENTICAL
    security long at an unrealized GAIN is a constructive sale: the gain is taxed at once. Substantially identical
    here = the same security; or another share class of the same issuer (the same SEC CIK in the security master's
    reference data, both equities); or the same canonical security; or an active substitution denial pair. Every
    account of the household is read — held-away accounts included (their positions are readable under every
    authority: read_only / advise / managed all allow reading data). A lot whose gain cannot be shown NOT to be a
    gain (no cost basis, or no price) is treated as a gain — conservative. A lot at a LOSS never blocks. The block
    names the account and the lot and has NO override in this build.
  * Short-sale wash rule (IRC §1091(e)) — a loss on closing a short is disallowed if a substantially identical
    short is opened within 30 days before or after the close. So a re-established short goes into an approved
    substitute that is clear, and a plain sell-short of a name the household covered at a loss in the window is
    deferred to the first clear day.

DISCLOSURES — every long/short proposal shows the six risks (AQR's list, spec), the borrow-cost estimate, the
margin requirement as DECLARED by the custodian (else "custodian to confirm") and a member-facing summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal, localcontext

from services import rebalance_engine as E

ZERO = Decimal(0)
ONE = Decimal(1)
BPS = Decimal(10000)
CENT = Decimal("0.01")
WQ = Decimal("0.0000000001")
WASH_DAYS = 30

EXTENSIONS: dict[str, tuple[Decimal, Decimal]] = {
    "130_30": (Decimal("1.30"), Decimal("0.30")),
    "150_50": (Decimal("1.50"), Decimal("0.50")),
}
EXTENSION_LABELS = {"130_30": "130/30 — $130 long and $30 short per $100 of capital",
                    "150_50": "150/50 — $150 long and $50 short per $100 of capital",
                    "custom": "Custom pair (gross short above 0 and at most 50 %; net 100 %)"}
MAX_GROSS_SHORT = Decimal("0.50")

SIDES = ("buy", "sell", "sell_short", "buy_to_cover")
SIDE_LABELS = {"buy": "Buy", "sell": "Sell", "sell_short": "SELL SHORT", "buy_to_cover": "BUY TO COVER"}
POSITION_EFFECT = {"sell_short": "open", "buy_to_cover": "close"}

RISKS: tuple[tuple[str, str], ...] = (
    ("pretax_underperformance", "Pre-tax underperformance: the strategy can trail its benchmark before tax — the "
                                "short book can lose when the names the signal likes least rise."),
    ("varying_tax_benefit", "The tax benefit varies: harvestable losses depend on markets and are not assured in "
                            "any year."),
    ("lower_future_rates", "Lower future tax rates would reduce the value of losses carried forward."),
    ("unused_losses", "Losses may go unused if there are not enough gains (or income) to offset."),
    ("larger_tax_at_redemption", "A larger tax bill at redemption: harvesting lowers cost basis, so unwinding the "
                                 "strategy can realize large gains."),
    ("rule_changes_irs", "Tax rules may change, or the IRS may challenge the treatment (for example wash-sale, "
                         "constructive-sale or straddle rules)."),
)
RISK_KEYS = tuple(k for k, _ in RISKS)
MARGIN_TO_CONFIRM = "custodian to confirm"
NEEDS_PRICES = "needs prices"

SKIP_LABELS = {
    "excluded": "Excluded — never shorted in this account",
    "model_excluded": "Excluded by the model — never shorted",
    "over_ceiling": "Borrow rate above the account's ceiling — not shorted",
    "needs_price": "No price — a short is sized in shares, so it is not placed",
    "not_equity": "Not a listed equity — never shorted",
    "dust": "Below the minimum trade size",
}
LINE_FLAG_LABELS = {
    "borrow_rate_needed": "Borrow rate needed — enter one before approval",
    "constructive_sale": "Blocked — §1259 constructive sale",
    "short_wash": "Deferred — short-sale wash rule",
    "capped": "Capped at the account's maximum short per name",
}
BLOCKING_LABELS = {
    "borrow_rate_needed": "A short line has no borrow rate on record",
    "constructive_sale": "A short would be a constructive sale (IRC §1259) — it can never be approved",
    "tracking_error_over_limit": "The estimated tracking error is above the account's limit",
    "not_eligible": "The account is not eligible for long/short",
}


class LongShortRuleError(ValueError):
    """A value the rules refuse (422)."""


def q10(v: Decimal) -> Decimal:
    return v.quantize(WQ, rounding=ROUND_HALF_EVEN)


def cents(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_EVEN)


def pct_text(v: Decimal | None) -> str:
    if v is None:
        return "—"
    return f"{(v * 100).quantize(Decimal('0.01'))}%"


# ── Extension levels ────────────────────────────────────────────────────────
def spec_of(extension: str, gross_long: Decimal | None = None, gross_short: Decimal | None = None
            ) -> tuple[Decimal, Decimal]:
    """(gross long, gross short) of an extension. A custom pair needs 0 < short ≤ 50 % and long = 1 + short."""
    if extension in EXTENSIONS:
        gl, gs = EXTENSIONS[extension]
        if gross_long is not None and Decimal(gross_long) != gl or gross_short is not None and Decimal(gross_short) != gs:
            raise LongShortRuleError(f"{extension.replace('_', '/')} is {gl:.2f} long and {gs:.2f} short")
        return gl, gs
    if extension != "custom":
        raise LongShortRuleError(f"extension is one of {list(EXTENSION_LABELS)}")
    if gross_long is None or gross_short is None:
        raise LongShortRuleError("a custom extension names its gross long and gross short (fractions)")
    gl, gs = Decimal(gross_long), Decimal(gross_short)
    if not (ZERO < gs <= MAX_GROSS_SHORT):
        raise LongShortRuleError(f"gross short must be above 0 and at most {pct_text(MAX_GROSS_SHORT)}")
    if gl != ONE + gs:
        raise LongShortRuleError(f"gross long must be 100% + the gross short ({pct_text(ONE + gs)}) — net 100%")
    return gl, gs


def extension_of(gross_long: Decimal, gross_short: Decimal) -> str:
    for k, (gl, gs) in EXTENSIONS.items():
        if Decimal(gross_long) == gl and Decimal(gross_short) == gs:
            return k
    return "custom"


# ── Model weights (upload / edit / submit) ──────────────────────────────────
@dataclass
class WeightRow:
    row_num: int
    key: str | None          # resolved global security id (None: unresolved / cash)
    label: str
    weight: Decimal | None
    is_cash: bool = False


def validate_weights(rows: list[WeightRow], *, gross_long: Decimal, gross_short: Decimal, tolerance_pct: Decimal,
                     short_info: dict[str, dict], exclusions: set[str]) -> list[tuple[str, int | None, str]]:
    """A long/short model's weights: longs sum to the gross long, shorts to the gross short (± tolerance), net
    100 %, no name both long and short, every short a listed equity in the master, no short on the model's own
    never-short list, no short row unresolved. Returns [(code, row, text)] — errors only; empty = valid."""
    tol = Decimal(tolerance_pct) / 100
    out: list[tuple[str, int | None, str]] = []
    longs = [r for r in rows if not r.is_cash and r.weight is not None and r.weight > 0]
    shorts = [r for r in rows if not r.is_cash and r.weight is not None and r.weight < 0]
    gl = sum((r.weight for r in longs), ZERO)
    gs = -sum((r.weight for r in shorts), ZERO)
    if not shorts:
        out.append(("ls_no_shorts", None, "a long/short model needs at least one short (a negative weight)"))
    if abs(gl - gross_long) > tol:
        out.append(("ls_gross_long", None, f"the long weights sum to {pct_text(gl)}, not the declared gross long "
                                           f"{pct_text(gross_long)} (±{tolerance_pct}%)"))
    if abs(gs - gross_short) > tol:
        out.append(("ls_gross_short", None, f"the short weights sum to {pct_text(gs)}, not the declared gross "
                                            f"short {pct_text(gross_short)} (±{tolerance_pct}%)"))
    side_of: dict[str, tuple[str, int]] = {}
    for r in longs + shorts:
        if r.key is None:
            if r.weight < 0:
                out.append(("ls_short_unresolved", r.row_num, f"row {r.row_num}: a short must be a security the "
                                                              "master resolves — never guessed"))
            continue
        side = "long" if r.weight > 0 else "short"
        prev = side_of.get(r.key)
        if prev and prev[0] != side:
            a, b = (prev[1], r.row_num)
            out.append(("ls_both_sides", r.row_num, f"row {r.row_num}: {r.label} is both long (row "
                                                    f"{a if prev[0] == 'long' else b}) and short (row "
                                                    f"{b if prev[0] == 'long' else a}) — a name is on one side only"))
        side_of.setdefault(r.key, (side, r.row_num))
    for r in shorts:
        if r.key is None:
            continue
        info = short_info.get(r.key) or {}
        if not info.get("equity"):
            out.append(("ls_short_not_equity", r.row_num, f"row {r.row_num}: {r.label} cannot be shorted — "
                                                          f"{info.get('why') or 'not a listed equity in the master'}"))
        if r.key in exclusions:
            out.append(("ls_short_excluded", r.row_num, f"row {r.row_num}: {r.label} is on the model's never-short "
                                                        "list"))
    return out


# ── The short book ──────────────────────────────────────────────────────────
@dataclass
class ShortLot:
    id: str                   # the opening fill id
    quantity: Decimal         # still open (positive)
    open_price: Decimal       # proceeds per share
    opened: date
    account_id: str | None = None


@dataclass
class ShortPosition:
    key: str
    label: str
    quantity: Decimal                         # shares short (positive)
    price: Decimal | None
    price_as_of: date | None
    price_source: str
    lots: list[ShortLot] = field(default_factory=list)

    @property
    def market_value(self) -> Decimal | None:
        return None if self.price is None else self.quantity * self.price

    @property
    def unrealized(self) -> Decimal | None:
        """Gain (+) / loss (−) if covered at the price: Σ (open − price) × q."""
        if self.price is None:
            return None
        return sum(((lot.open_price - self.price) * lot.quantity for lot in self.lots), ZERO)


def short_close_term(opened: date | None, closed: date | None) -> str:
    """IRC §1233 — a short sale's gain or loss is SHORT-TERM whatever the holding period."""
    return "short"


def relieve_short_lots(lots: list[ShortLot], quantity: Decimal, named: list[tuple[str, Decimal]] | None = None
                       ) -> list[tuple[ShortLot, Decimal]]:
    """Which short lots a cover closes: the named lots first (in order), then the oldest (FIFO)."""
    left = Decimal(quantity)
    out: list[tuple[ShortLot, Decimal]] = []
    taken: dict[str, Decimal] = {}
    by_id = {lot.id: lot for lot in lots}
    for lid, q in named or []:
        lot = by_id.get(str(lid))
        if lot is None:
            raise LongShortRuleError(f"short lot {lid} is not an open short lot of this position")
        q = min(Decimal(q), lot.quantity, left)
        if q > 0:
            out.append((lot, q))
            taken[lot.id] = q
            left -= q
    for lot in sorted(lots, key=lambda x: (x.opened, x.id)):
        if left <= 0:
            break
        free = lot.quantity - taken.get(lot.id, ZERO)
        q = min(free, left)
        if q > 0:
            out.append((lot, q))
            left -= q
    if left > 0:
        raise LongShortRuleError(f"a cover of {quantity} is more than the {sum((x.quantity for x in lots), ZERO)} "
                                 "shares short")
    return out


def cover_gain(relief: list[tuple[ShortLot, Decimal]], price: Decimal) -> Decimal:
    return sum(((lot.open_price - price) * q for lot, q in relief), ZERO)


# ── §1259 constructive sale ─────────────────────────────────────────────────
@dataclass
class LongLot:
    account_id: str
    account: str              # masked
    is_held_away: bool
    key: str
    label: str
    lot_id: str | None
    quantity: Decimal
    unit_cost: Decimal | None
    price: Decimal | None
    acquired: date | None


def lot_gain(lot: LongLot) -> Decimal | None:
    if lot.unit_cost is None or lot.price is None:
        return None
    return (lot.price - lot.unit_cost) * lot.quantity


def constructive_sale_hits(short_key: str, long_lots: list[LongLot], identical) -> list[dict]:
    """Every household long lot that makes a short of ``short_key`` a constructive sale: the same security or
    one ``identical(a, b) -> str | None`` (the reason) calls substantially identical, at a gain — or a lot whose
    gain cannot be shown (no cost, no price), conservatively. A lot at a loss (or flat) never blocks."""
    hits = []
    for lot in long_lots:
        why = "the same security" if lot.key == short_key else identical(lot.key, short_key)
        if not why or lot.quantity <= 0:
            continue
        g = lot_gain(lot)
        if g is not None and g <= 0:
            continue
        hits.append({"account_id": lot.account_id, "account": lot.account, "held_away": lot.is_held_away,
                     "security_id": lot.key, "label": lot.label, "lot_id": lot.lot_id,
                     "quantity": str(lot.quantity), "identical_because": why,
                     "unrealized_gain": None if g is None else str(cents(g)),
                     "unknown": g is None,
                     "text": (f"{lot.account}{' (held away)' if lot.is_held_away else ''} holds {lot.quantity} "
                              f"{lot.label}" + (f" (lot {lot.lot_id})" if lot.lot_id else "") +
                              (f" at an unrealized gain of ${cents(g)}" if g is not None else
                               " whose gain cannot be shown not to be a gain (no cost basis or no price)") +
                              f" — {why}")})
    return hits


def constructive_sale_text(label: str, hits: list[dict]) -> str:
    return (f"BLOCKED — shorting {label} would be a constructive sale (IRC §1259): " + "; ".join(h["text"] for h in hits)
            + ". A constructive sale taxes the gain at once; this block cannot be overridden.")


# ── Short-sale wash rule ────────────────────────────────────────────────────
@dataclass(frozen=True)
class CoverEvent:
    key: str
    on: date
    loss: bool
    account: str
    ref: str | None = None


def short_wash(key: str, covers: list[CoverEvent], today: date, identical) -> dict | None:
    """A sell-short of ``key`` today is washed by a household cover AT A LOSS of the same or an identical name
    within 30 days before (the cover's own window runs 30 days after it). None = clear."""
    lo = today - timedelta(days=WASH_DAYS)
    hits = [c for c in covers if c.loss and lo <= c.on <= today
            and (c.key == key or identical(c.key, key))]
    if not hits:
        return None
    last = max(hits, key=lambda c: c.on)
    until = last.on + timedelta(days=WASH_DAYS + 1)
    return {"outcome": "deferred", "rule": "short_wash", "until": until.isoformat(),
            "matches": [{"security_id": c.key, "on": c.on.isoformat(), "account": c.account, "ref": c.ref}
                        for c in hits],
            "reason": f"short-sale wash rule (IRC §1091(e)): the household covered a short in this name (or an "
                      f"identical one) at a loss on {last.on.isoformat()} ({last.account}) — a new short now would "
                      f"disallow that loss; deferred until {until.isoformat()}, the first clear day"}


# ── Harvest (both books) ────────────────────────────────────────────────────
@dataclass
class HarvestPick:
    book: str                 # 'long' | 'short'
    key: str
    label: str
    substitute: str | None    # the re-established name (None: closed to cash)
    substitute_label: str | None
    loss: Decimal             # negative
    net_loss: Decimal         # after trading cost (negative)
    term: str
    reason: str


def harvest_passes(loss: Decimal, basis: Decimal, trading_cost: Decimal, *, pct: Decimal, minimum: Decimal
                   ) -> tuple[bool, Decimal, Decimal]:
    """rep6's rule on one name: net loss = loss − trading cost; taken when net loss ≥ BOTH the % (of the basis)
    and the $ threshold. Returns (passes, net_loss (negative), loss_pct)."""
    net = loss - trading_cost
    if basis <= 0:
        return False, net, ZERO
    lp = q10(-net / basis)
    return bool(net < 0 and -net >= minimum and lp >= pct), net, lp


# ── Building both books ─────────────────────────────────────────────────────
@dataclass
class Borrow:
    rate_bps: Decimal
    rebate_bps: Decimal | None
    as_of: date
    source: str
    scope: str
    id: str | None = None


@dataclass
class BookSettings:
    max_short_per_name: Decimal
    exclusions: dict[str, str]                  # key → why (account's list and the model's)
    ceiling_bps: Decimal
    fractional: bool
    min_trade: Decimal
    sale: E.SaleSettings
    trading_cost_bps: Decimal = ZERO


@dataclass
class BookResult:
    capital: Decimal
    cash_before: Decimal
    cash_after: Decimal
    target: dict[str, Decimal]                  # signed, after the settings (cash included)
    model_target: dict[str, Decimal]
    before: dict[str, Decimal]                  # signed weights of capital
    after: dict[str, Decimal]
    drift_before: Decimal
    drift_after: Decimal
    trades: list[E.TradeOut]
    legs: dict[int, dict]                       # trade index → short leg
    flags: dict[int, dict]                      # trade index → wash-style result (clear / deferred / blocked)
    later: list
    warnings: list[dict]
    skipped: list[dict]
    borrow_lines: list[dict]
    long_value_after: Decimal
    short_value_after: Decimal
    est_st_gain: Decimal
    est_lt_gain: Decimal
    blocking: list[str]


def _unit(fractional: bool) -> Decimal:
    return E.QTY if fractional else ONE


def _floor(q: Decimal, unit: Decimal) -> Decimal:
    return (q / unit).to_integral_value(rounding=ROUND_DOWN) * unit if q > 0 else ZERO


def signed_weights(longs: dict[str, Decimal], shorts: dict[str, Decimal], cash: Decimal, capital: Decimal
                   ) -> dict[str, Decimal]:
    if capital <= 0:
        return {}
    with localcontext() as ctx:
        ctx.prec = 60
        out = {k: q10(v / capital) for k, v in longs.items() if v}
        for k, v in shorts.items():
            if v:
                out[k] = q10(out.get(k, ZERO) - v / capital)
        out[E.CASH] = q10(cash / capital)
    return out


def borrow_fee(quantity: Decimal, price: Decimal, rate_bps: Decimal | None) -> Decimal | None:
    if rate_bps is None:
        return None
    return cents(quantity * price * Decimal(rate_bps) / BPS)


def build_books(longs: list[E.Holding], shorts: list[ShortPosition], cash: Decimal, model_target: dict[str, Decimal],
                labels: dict[str, str], prices: dict[str, tuple[Decimal, date, str]], borrow: dict[str, Borrow],
                s: BookSettings, *, blocked_1259: dict[str, list[dict]] | None = None,
                short_washes: dict[str, dict] | None = None, harvest: list[HarvestPick] | None = None
                ) -> BookResult:
    """Both books toward the long/short model within the account's settings (module docstring). Pure."""
    blocked_1259 = blocked_1259 or {}
    short_washes = short_washes or {}
    harvest = harvest or []
    unit = _unit(s.fractional)
    warnings: list[dict] = []
    skipped: list[dict] = []
    held = {h.key: h for h in longs if h.price is not None and h.market_value is not None}
    for h in longs:
        if h.key not in held:
            warnings.append({"code": "cannot_value", "key": h.key,
                             "message": f"{h.label}: cannot value — needs a price; not traded, left out of capital"})
    sp_ = {x.key: x for x in shorts}
    for x in shorts:
        if x.price is None:
            warnings.append({"code": "cannot_value", "key": x.key,
                             "message": f"{x.label} (short): cannot value — needs a price; not traded"})
    long_mv = sum((h.market_value for h in held.values()), ZERO)
    short_mv = sum((x.market_value for x in shorts if x.price is not None), ZERO)
    C = long_mv - short_mv + cash
    if C <= 0:
        raise LongShortRuleError("the account has no positive capital (long value − short value + cash) to build on")
    before = signed_weights({k: h.market_value for k, h in held.items()},
                            {x.key: x.market_value for x in shorts if x.price is not None}, cash, C)

    def price_of(k):
        if k in held:
            return held[k].price, held[k].price_as_of, held[k].price_source
        if k in sp_ and sp_[k].price is not None:
            return sp_[k].price, sp_[k].price_as_of, sp_[k].price_source
        return prices.get(k, (None, None, "none"))

    w = {k: Decimal(v) for k, v in model_target.items()}
    # harvest: a harvested name's target moves to its substitute (or to cash), so it is not bought / shorted back
    harvest_keys = {}
    for hp in harvest:
        moved = w.pop(hp.key, ZERO)
        harvest_keys[hp.key] = hp
        if hp.substitute:
            w[hp.substitute] = w.get(hp.substitute, ZERO) + moved
        else:
            w[E.CASH] = w.get(E.CASH, ZERO) + abs(moved)
    flags_by_key: dict[str, dict] = {}
    for k in sorted(w):
        wk = w[k]
        if k == E.CASH or wk >= 0:
            continue
        lab = labels.get(k, k)
        if k in s.exclusions:
            skipped.append({"key": k, "label": lab, "code": "excluded", "weight": str(wk),
                            "reason": f"{SKIP_LABELS['excluded']} ({s.exclusions[k]})"})
            w[k] = ZERO
            continue
        b = borrow.get(k)
        if k in blocked_1259:
            flags_by_key[k] = {"outcome": "blocked", "rule": "1259", "matches": blocked_1259[k],
                               "reason": constructive_sale_text(lab, blocked_1259[k])}
        elif b is not None and Decimal(b.rate_bps) > s.ceiling_bps:
            skipped.append({"key": k, "label": lab, "code": "over_ceiling", "weight": str(wk),
                            "rate_bps": str(b.rate_bps), "ceiling_bps": str(s.ceiling_bps),
                            "reason": f"{SKIP_LABELS['over_ceiling']}: {b.rate_bps} bps a year (as of "
                                      f"{b.as_of.isoformat()}) is above the ceiling of {s.ceiling_bps} bps"})
            w[k] = ZERO
            continue
        elif k in short_washes:
            flags_by_key[k] = short_washes[k]
        if -wk > s.max_short_per_name:
            warnings.append({"code": "short_capped", "key": k,
                             "message": f"{lab}: model short {pct_text(-wk)} capped at the account's maximum "
                                        f"{pct_text(s.max_short_per_name)} per name"})
            w[k] = -s.max_short_per_name
    T = {k: v * C for k, v in w.items()}

    # ── long sells (rep5's lot relief) and short covers ────────────────────
    requests: list[tuple[E.Holding, Decimal]] = []
    reasons: dict[str, str] = {}
    dust = 0
    for k, h in held.items():
        t = max(T.get(k, ZERO), ZERO)
        tq = _floor(t / h.price, unit)
        if tq < h.quantity:
            q = h.quantity - tq
            if q * h.price < s.min_trade:
                dust += 1
                continue
            requests.append((h, q))
            if k in harvest_keys:
                hp = harvest_keys[k]
                reasons[k] = (f"Harvest: sell at a loss of ${cents(-hp.loss)} (net ${cents(-hp.net_loss)})"
                              + (f" — re-established in {hp.substitute_label}" if hp.substitute else " — to cash"))
            elif t <= 0:
                reasons[k] = (f"Not long in the model — sold" if w.get(k, ZERO) == 0 else
                              "Short in the model — the long is sold first (a name is never long and short at once)")
            else:
                reasons[k] = f"Trim to the long target {pct_text(w.get(k, ZERO))}"
    sp = E.plan_sales(requests, s.sale)
    trades: list[E.TradeOut] = []
    legs: dict[int, dict] = {}
    flags: dict[int, dict] = {}
    later = list(sp.later)
    st_total = lt_total = ZERO
    proceeds = ZERO
    sold: dict[str, Decimal] = {}
    for h, asked in requests:
        portions = sp.now.get(h.key) or []
        q = sum((p.quantity for p in portions), ZERO)
        if q <= 0:
            continue
        st = sum((p.gain for p in portions if p.gain is not None and p.term != "long"), ZERO)
        lt = sum((p.gain for p in portions if p.gain is not None and p.term == "long"), ZERO)
        st_total += st
        lt_total += lt
        proceeds += q * h.price
        sold[h.key] = q
        reason = reasons[h.key] + ("; part of it waits (gain budget / short-term guard — see sell later)"
                                   if q < asked else "")
        mk, mnote = sp.method_of.get(h.key, (None, None))
        trades.append(E.TradeOut(h.key, h.label, "sell", q, None, h.price, h.price_source, h.price_as_of,
                                 [{"lot_id": p.lot_id, "quantity": p.quantity, "unit_cost": p.unit_cost,
                                   "term": p.term, "est_gain": p.gain,
                                   "acquired_date": p.acquired.isoformat() if p.acquired else None, "why": p.why}
                                  for p in portions if p.lot_id],
                                 st, lt, reason, mk, mnote))
        flags[len(trades) - 1] = {"outcome": "clear", "rule": None, "security_id": h.key, "label": h.label,
                                  "matches": [], "reason": "a long sale — no short-sale rule applies"}
    cover_cost = ZERO
    for k, x in sp_.items():
        if x.price is None:
            continue
        t = max(-T.get(k, ZERO), ZERO)
        tq = _floor(t / x.price, unit)
        if tq >= x.quantity:
            continue
        q = x.quantity - tq
        if q * x.price < s.min_trade and k not in harvest_keys:
            dust += 1
            continue
        relief = relieve_short_lots(x.lots, q)
        g = cover_gain(relief, x.price)
        st_total += g
        cover_cost += q * x.price
        if k in harvest_keys:
            hp = harvest_keys[k]
            why = (f"Harvest: cover at a loss of ${cents(-hp.loss)} (net ${cents(-hp.net_loss)})"
                   + (f" — re-established short in {hp.substitute_label}" if hp.substitute else " — to cash"))
        elif t <= 0:
            why = "Not short in the model — covered"
        else:
            why = f"Cover down to the short target {pct_text(-w.get(k, ZERO))}"
        trades.append(E.TradeOut(k, x.label, "buy_to_cover", q, None, x.price, x.price_source, x.price_as_of, [],
                                 g, ZERO, why + " (a short's gain or loss is short-term — IRC §1233)"))
        b = borrow.get(k)
        legs[len(trades) - 1] = {
            "position_effect": "close", "term": "short",
            "lots": [{"short_lot_id": lot.id, "quantity": str(qq), "open_price": str(lot.open_price),
                      "opened": lot.opened.isoformat(), "est_gain": str(cents((lot.open_price - x.price) * qq))}
                     for lot, qq in relief],
            "borrow_rate_bps": None if b is None else str(b.rate_bps)}
        flags[len(trades) - 1] = {"outcome": "clear", "rule": None, "security_id": k, "label": x.label,
                                  "matches": [], "reason": "a cover — closes short lots"}

    # ── new shorts ─────────────────────────────────────────────────────────
    short_proceeds = ZERO
    borrow_lines: list[dict] = []
    blocking: set[str] = set()
    for k in sorted(w):
        wk = w[k]
        if k == E.CASH or wk >= 0:
            continue
        lab = labels.get(k, k)
        p, as_of, src = price_of(k)
        if p is None:
            skipped.append({"key": k, "label": lab, "code": "needs_price", "weight": str(wk),
                            "reason": SKIP_LABELS["needs_price"]})
            continue
        cur = sp_[k].quantity if k in sp_ else ZERO
        tq = _floor(-T[k] / p, unit)
        q = tq - cur
        if q <= 0:
            continue
        if q * p < s.min_trade:
            dust += 1
            continue
        b = borrow.get(k)
        fl = flags_by_key.get(k)
        trades.append(E.TradeOut(k, lab, "sell_short", q, None, p, src, as_of, [], ZERO, ZERO,
                                 (f"Re-established short (harvest substitute) toward {pct_text(-wk)}"
                                  if any(hp.substitute == k and hp.book == "short" for hp in harvest) else
                                  f"Sell short to the short target {pct_text(-wk)} (holds {pct_text(-before.get(k, ZERO))})"
                                  if before.get(k, ZERO) < 0 else f"Sell short to the short target {pct_text(-wk)}")))
        i = len(trades) - 1
        fee = borrow_fee(q, p, b.rate_bps if b else None)
        rebate = borrow_fee(q, p, b.rebate_bps if b and b.rebate_bps is not None else None)
        leg = {"position_effect": "open", "term": "short",
               "borrow_rate_bps": None if b is None else str(b.rate_bps),
               "rebate_bps": None if b is None or b.rebate_bps is None else str(b.rebate_bps),
               "rate_as_of": None if b is None else b.as_of.isoformat(),
               "rate_source": None if b is None else b.source, "rate_scope": None if b is None else b.scope,
               "est_borrow_fee_annual": None if fee is None else str(fee),
               "est_rebate_annual": None if rebate is None else str(rebate),
               "flags": []}
        if b is None:
            leg["flags"].append("borrow_rate_needed")
            blocking.add("borrow_rate_needed")
        if fl is not None:
            flags[i] = {**fl, "security_id": k, "label": lab}
            leg["flags"].append("constructive_sale" if fl.get("rule") == "1259" else "short_wash")
            if fl.get("rule") == "1259":
                blocking.add("constructive_sale")
        else:
            flags[i] = {"outcome": "clear", "rule": None, "security_id": k, "label": lab, "matches": [],
                        "reason": "no constructive sale; no household cover at a loss within 30 days"}
            short_proceeds += q * p
        if -wk == s.max_short_per_name and -Decimal(model_target.get(k, ZERO)) > s.max_short_per_name:
            leg["flags"].append("capped")
        legs[i] = leg
        borrow_lines.append({"line": i, "key": k, "label": lab, "quantity": str(q), "price": str(p),
                             "value": str(cents(q * p)), "rate_bps": leg["borrow_rate_bps"],
                             "rebate_bps": leg["rebate_bps"], "est_borrow_fee_annual": leg["est_borrow_fee_annual"],
                             "est_rebate_annual": leg["est_rebate_annual"], "flags": list(leg["flags"])})

    # ── buys from what the sales and the placed shorts raise ───────────────
    cash_target = max(T.get(E.CASH, ZERO), ZERO)
    available = cash + proceeds + short_proceeds - cover_cost - cash_target
    wants = []
    for k in sorted(w):
        wk = w[k]
        if k == E.CASH or wk <= 0:
            continue
        if any(h.key == k for h in longs if h.key not in held):
            continue
        cur = held[k].market_value if k in held else ZERO
        need = T[k] - cur
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
        warnings.append({"code": "buys_scaled", "message": f"buys scaled to {pct_text(q10(scale))} of what the long "
                         "target needs — the sales and the shorts placed do not raise enough cash"})
    bought = ZERO
    for k, need in wants:
        dollars = need * scale
        if dollars < s.min_trade or dollars <= 0:
            dust += 1
            continue
        p, as_of, src = price_of(k)
        lab = labels.get(k, k)
        hp_sub = next((hp for hp in harvest if hp.substitute == k and hp.book == "long"), None)
        reason = (f"Harvest substitute for {hp_sub.label} — long target {pct_text(w[k])}" if hp_sub else
                  f"Buy to the long target {pct_text(w[k])} (holds {pct_text(max(before.get(k, ZERO), ZERO))})")
        if p is not None:
            q = _floor(dollars / p, unit)
            if q <= 0 or q * p < s.min_trade:
                dust += 1
                continue
            trades.append(E.TradeOut(k, lab, "buy", q, None, p, src, as_of, [], ZERO, ZERO, reason))
            bought += q * p
        else:
            n = dollars.quantize(CENT, rounding=ROUND_DOWN)
            trades.append(E.TradeOut(k, lab, "buy", None, n, None, "none", None, [], ZERO, ZERO,
                                     reason + " — unpriced, bought by amount"))
            bought += n
        flags[len(trades) - 1] = {"outcome": "clear", "rule": None, "security_id": k, "label": lab, "matches": [],
                                  "reason": "household wash-sale control (rep6) checked by the caller"}
    if dust:
        warnings.append({"code": "dust", "message": f"{dust} trade(s) under the minimum trade size were dropped"})
    for sk in skipped:
        warnings.append({"code": f"short_{sk['code']}", "key": sk["key"], "message": f"{sk['label']}: {sk['reason']}"})

    # ── after ─────────────────────────────────────────────────────────────
    routed = [i for i in range(len(trades)) if flags.get(i, {}).get("outcome") in (None, "clear", "replaced")]
    long_after = {k: h.market_value for k, h in held.items()}
    short_after = {x.key: x.market_value for x in shorts if x.price is not None}
    cash_after = cash
    for i in routed:
        t = trades[i]
        val = t.quantity * t.price if t.quantity is not None and t.price is not None else (t.notional or ZERO)
        if t.side == "buy":
            long_after[t.key] = long_after.get(t.key, ZERO) + val
            cash_after -= val
        elif t.side == "sell":
            long_after[t.key] = long_after.get(t.key, ZERO) - val
            cash_after += val
        elif t.side == "sell_short":
            short_after[t.key] = short_after.get(t.key, ZERO) + val
            cash_after += val
        else:
            short_after[t.key] = short_after.get(t.key, ZERO) - val
            cash_after -= val
    after = signed_weights(long_after, short_after, cash_after, C)
    tgt_out = {k: q10(v) for k, v in w.items()}
    tgt_out[E.CASH] = q10(w.get(E.CASH, ZERO))
    lv = sum((v for v in long_after.values()), ZERO)
    sv = sum((v for v in short_after.values()), ZERO)
    net = q10((lv - sv) / C)
    if abs(net - ONE + tgt_out[E.CASH]) > Decimal("0.02"):
        warnings.append({"code": "net_exposure", "message": f"net exposure after the proposal is {pct_text(net)} "
                         f"(long {pct_text(q10(lv / C))}, short {pct_text(q10(sv / C))}) — a short the settings "
                         "did not place leaves the long book above its share"})
    return BookResult(capital=C, cash_before=cash, cash_after=cash_after, target=tgt_out,
                      model_target={k: Decimal(v) for k, v in model_target.items()}, before=before, after=after,
                      drift_before=q10(E.max_drift(before, tgt_out)), drift_after=q10(E.max_drift(after, tgt_out)),
                      trades=trades, legs=legs, flags=flags, later=later, warnings=warnings, skipped=skipped,
                      borrow_lines=borrow_lines, long_value_after=cents(lv), short_value_after=cents(sv),
                      est_st_gain=st_total, est_lt_gain=lt_total, blocking=sorted(blocking))


def short_book_borrow(short_after: dict[str, tuple[Decimal, Decimal]], borrow: dict[str, Borrow]) -> dict:
    """Annual borrow cost and rebate over the short book AFTER the proposal: {key: (shares, price)}."""
    fee = reb = ZERO
    missing = []
    for k, (q, p) in short_after.items():
        b = borrow.get(k)
        if b is None:
            missing.append(k)
            continue
        fee += q * p * Decimal(b.rate_bps) / BPS
        if b.rebate_bps is not None:
            reb += q * p * Decimal(b.rebate_bps) / BPS
    return {"borrow_cost_annual": cents(fee), "rebate_annual": cents(reb), "missing_rates": missing}


def cost_comparison(*, traded_value: Decimal, trading_cost_bps: Decimal, borrow: dict, overlay_bps: Decimal | None
                    ) -> list[dict]:
    """The proposal's cost comparison (annual where stated). Borrow and rebate are estimates from entered rates."""
    trading = cents(traded_value * Decimal(trading_cost_bps) / BPS)
    lines = [
        {"key": "trading_cost", "label": "Estimated trading cost (this proposal)", "status": "estimate",
         "amount": str(trading), "basis": f"{trading_cost_bps} bps of ${cents(traded_value)} traded"},
        {"key": "borrow_cost", "label": "Borrow cost (long/short only) — estimated", "status": "estimate",
         "amount": str(borrow["borrow_cost_annual"]), "basis": "a year, over the short book after this proposal, "
                                                               "at the entered borrow rates"},
        {"key": "short_rebate", "label": "Short rebate", "status": "estimate",
         "amount": str(borrow["rebate_annual"]), "basis": "a year, at the entered rebate rates (none entered = 0)"},
    ]
    if borrow.get("missing_rates"):
        lines[1]["status"] = "incomplete"
        lines[1]["basis"] += f" — {len(borrow['missing_rates'])} name(s) have no borrow rate (borrow rate needed)"
    if overlay_bps is None:
        lines.append({"key": "overlay_fee", "label": "Long/short overlay fee", "status": "not_set", "amount": None,
                      "basis": "the overlay's own bps are an open decision (Joe) — not charged, not estimated"})
    else:
        lines.append({"key": "overlay_fee", "label": "Long/short overlay fee", "status": "set",
                      "amount": None, "basis": f"{overlay_bps} bps a year of capital"})
    net = Decimal(lines[0]["amount"]) + Decimal(lines[1]["amount"]) - Decimal(lines[2]["amount"])
    lines.append({"key": "net_cost", "label": "Net estimated cost (trading + borrow − rebate)", "status": "estimate",
                  "amount": str(cents(net)), "basis": "estimate"})
    return lines


# ── Tracking error (ex-ante, from daily closes; never invented) ─────────────
def te_pure(final: dict[str, Decimal], model: dict[str, Decimal], history: dict[str, list], *, min_obs: int) -> dict:
    """Signed weights. EVERY name with a weight on either side needs ≥ min_obs + 1 closes; otherwise
    "needs prices" naming the missing names. TE = sqrt(252 · aᵀΣa), a = final − model (rep10's helpers)."""
    from services import tilts as TL

    keys = sorted(k for k in set(final) | set(model) if k != E.CASH and (final.get(k) or model.get(k)))
    missing = [k for k in keys if len(history.get(k) or []) < min_obs + 1]
    if not keys:
        return {"estimated": False, "label": NEEDS_PRICES, "text": f"{NEEDS_PRICES} — no names", "missing": []}
    if missing:
        return {"estimated": False, "label": NEEDS_PRICES, "missing": missing,
                "text": f"{NEEDS_PRICES} — {len(missing)} of {len(keys)} names have no price series of "
                        f"{min_obs + 1} closes"}
    _, rets = TL.returns_on_common_dates({k: history[k] for k in keys})
    n = len(next(iter(rets.values()))) if rets else 0
    if n < min_obs:
        return {"estimated": False, "label": NEEDS_PRICES, "missing": keys,
                "text": f"{NEEDS_PRICES} — only {n} common daily returns (need {min_obs})"}
    cov = TL.covariance(rets)
    active = {k: Decimal(final.get(k, ZERO)) - Decimal(model.get(k, ZERO)) for k in keys}
    te = TL.te_from(active, cov)
    return {"estimated": True, "label": "estimate", "tracking_error": str(te), "observations": n,
            "text": f"{pct_text(te)} a year (estimate)"}


# ── Disclosures and the member summary ─────────────────────────────────────
def disclosures(*, borrow_total: Decimal, rebate_total: Decimal, margin: dict, gross_long: Decimal,
                gross_short: Decimal, risks=None) -> dict:
    """``risks`` — DISCLOSURES1: the (key, text) items of the library's ``long_short.risks`` version in force for the
    account; None → the built-in six (RISKS, the library's exact fallback)."""
    return {"risks": [{"key": k, "text": t} for k, t in (risks or RISKS)],
            "borrow_estimate": {"text": f"Estimated borrow cost ${borrow_total} a year (short rebate ${rebate_total})"
                                        " — an estimate from entered rates; the lender can recall shares or change "
                                        "the rate at any time.",
                                "borrow_cost_annual": str(borrow_total), "rebate_annual": str(rebate_total)},
            "margin": margin,
            "structure": f"{pct_text(gross_long)} long and {pct_text(gross_short)} short per 100 % of capital — "
                         "a margin account is required; short positions can lose more than their initial value."}


def margin_text(initial: str | None, maintenance: str | None, short_value: Decimal) -> dict:
    def one(v):
        if v is None or v == "unknown":
            return {"declared": False, "text": MARGIN_TO_CONFIRM}
        d = Decimal(v)
        return {"declared": True, "fraction": v, "text": f"{pct_text(d)} of the short value — "
                                                          f"${cents(short_value * d)} (as declared by the custodian)"}
    return {"initial": one(initial), "maintenance": one(maintenance)}


def member_summary(*, account: str, gross_long: Decimal, gross_short: Decimal, shorts: int, longs: int,
                   borrow_total: Decimal, est_tax: str) -> str:
    return (f"For account {account}: for every $100 of your capital, about ${(gross_long * 100).quantize(ONE)} is "
            f"invested in stocks the strategy favours and about ${(gross_short * 100).quantize(ONE)} is borrowed and "
            f"sold short in stocks it likes least. This proposal places {longs} long and {shorts} short trade(s). "
            f"Borrowing shares costs an estimated ${borrow_total} a year. {est_tax} Losses can come from both "
            "sides, and short positions can lose more than their starting value. Please read the risks listed "
            "with this proposal.")
