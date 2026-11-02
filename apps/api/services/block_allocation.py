"""Block allocation arithmetic (rep9) — pure, Decimal only, no I/O.

THE RULES (the mechanics of the firm's written allocation policy; the policy
TEXT is the firm's own words and is not interpreted here)

* Method ``pro_rata_requested`` (the only one this build implements): before
  execution each account's ALLOCATED quantity is its requested quantity
  (what its approved instruction still needs). It is FIXED when the block is
  approved.
* Average price = Σ(fill quantity × fill price) / Σ(fill quantity), computed —
  never entered — and stated to 6 decimal places, ROUND_HALF_UP (the price
  column is numeric(20, 6)).
* Full fill: every account gets exactly its fixed allocation.
* Partial fill (``pro_rata_allocated``): each account's exact share is
  filled × allocated / Σ allocated. An account whose EXACT share is below the
  policy's minimum allocation gets ZERO, with the reason, and the shares are
  re-apportioned among the others (repeated until none is below it).
* Rounding (``largest_remainder_then_account``): each share is rounded DOWN to
  its account's unit (1 for a whole-share account, 0.00000001 for a
  fractional one); the residual goes out one whole share at a time in order
  of the largest remainder, ties by account id (then allocation id), never
  above an account's fixed allocation; a sub-share residual goes to the first
  fractional account in the same order. The total always equals the filled
  quantity exactly — or the allocation is REFUSED with the reason (e.g. a
  fractional fill no account can take).
* Lots (sells): each account relieves only ITS OWN instructed lots; when it
  receives less than it asked for, those lots are relieved pro rata by the same
  largest-remainder rule (ties by lot id), in the account's unit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, localcontext

WHOLE = Decimal(1)
FRACTIONAL = Decimal("0.00000001")      # numeric(28, 8)
PRICE_Q = Decimal("0.000001")           # numeric(20, 6)
ZERO = Decimal(0)

METHODS = ("pro_rata_requested",)
ROUNDING_RULES = ("largest_remainder_then_account",)
PARTIAL_RULES = ("pro_rata_allocated",)
PRICE_ROUNDING_NOTE = "average price = Σ(quantity × price) / Σ quantity, to 6 decimal places, rounded half up"


class AllocationError(ValueError):
    """The arithmetic cannot be done under the policy (a refusal with the reason)."""


@dataclass(frozen=True)
class Slot:
    key: str            # allocation id
    account_id: str
    cap: Decimal        # the FIXED pre-trade allocation
    unit: Decimal       # WHOLE or FRACTIONAL


@dataclass(frozen=True)
class Share:
    key: str
    quantity: Decimal
    exact: Decimal
    zero_reason: str | None = None


def unit_for(fractional: bool) -> Decimal:
    return FRACTIONAL if fractional else WHOLE


def floor_to(q: Decimal, unit: Decimal) -> Decimal:
    return (q / unit).to_integral_value(rounding=ROUND_DOWN) * unit


def on_unit(q: Decimal, unit: Decimal) -> bool:
    return floor_to(q, unit) == q


def average_price(fills: list[tuple[Decimal, Decimal]]) -> Decimal:
    """Σ(q × p) / Σ q, quantized to 6 dp half up. Refuses an empty list."""
    qty = sum((q for q, _ in fills), ZERO)
    if qty <= 0:
        raise AllocationError("no fills — there is no average price")
    with localcontext() as ctx:
        ctx.prec = 60
        notional = sum((q * p for q, p in fills), ZERO)
        return (notional / qty).quantize(PRICE_Q, rounding=ROUND_HALF_UP)


def apportion(items: list[tuple[str, str, Decimal, Decimal, Decimal]], total: Decimal) -> dict[str, Decimal]:
    """``items`` = [(key, tiebreak, weight, cap, unit)]. Each item's exact share
    is total × weight / Σ weight, rounded down to its unit (never above its
    cap); the residual by the largest remainder, ties by (tiebreak, key) —
    whole units first, then a sub-unit residual to the first fine-unit item.
    Raises when the residual cannot be placed."""
    if total < 0:
        raise AllocationError("cannot apportion a negative quantity")
    weight_sum = sum((w for _, _, w, _, _ in items), ZERO)
    if total == 0:
        return {k: ZERO for k, *_ in items}
    if weight_sum <= 0:
        raise AllocationError("nothing to apportion over")
    with localcontext() as ctx:
        ctx.prec = 60
        exact = {k: total * w / weight_sum for k, _, w, _, _ in items}
    base = {k: min(floor_to(exact[k], u), c) for k, _, _, c, u in items}
    residual = total - sum(base.values(), ZERO)
    order = sorted(items, key=lambda it: (-(exact[it[0]] - base[it[0]]), it[1], it[0]))
    while residual >= 1:
        moved = False
        for k, _, _, c, _ in order:
            if residual >= 1 and base[k] + 1 <= c:
                base[k] += 1
                residual -= 1
                moved = True
        if not moved:
            break
    if residual > 0:
        for k, _, _, c, u in order:
            if u >= 1 or residual <= 0:
                continue
            give = min(floor_to(residual, u), c - base[k])
            if give > 0:
                base[k] += give
                residual -= give
    if residual != 0:
        raise AllocationError(f"{residual} of the {total} cannot be placed: no account can take it in its unit "
                              "(whole-share accounts take whole shares only) without exceeding its fixed allocation")
    return base


def fixed_allocation(requested: list[tuple[str, str, Decimal, Decimal]], minimum: Decimal
                     ) -> tuple[dict[str, Decimal], dict[str, str]]:
    """Pre-trade (``pro_rata_requested``): allocated = requested. Returns
    (allocated, refused-with-reason) — an account below the minimum, or a
    fractional request for a whole-share account, is refused (never rounded)."""
    out, refused = {}, {}
    for key, _acct, q, unit in requested:
        if q <= 0:
            refused[key] = "nothing left to trade on this instruction"
        elif not on_unit(q, unit):
            refused[key] = (f"{q} is not a whole number of shares and this account trades whole shares only "
                            "(its custodian's fractional_shares is not an evidenced yes)")
        elif minimum > 0 and q < minimum:
            refused[key] = f"{q} is below the policy's minimum allocation of {minimum}"
        else:
            out[key] = q
    return out, refused


def allocate_fill(slots: list[Slot], filled: Decimal, minimum: Decimal) -> dict[str, Share]:
    """The filled quantity across the fixed allocations (see the module rules)."""
    total = sum((s.cap for s in slots), ZERO)
    if not slots:
        raise AllocationError("a block has no allocations")
    if filled <= 0:
        raise AllocationError("nothing was filled")
    if filled > total:
        raise AllocationError(f"filled {filled} is more than the block's {total}")
    if filled == total:
        return {s.key: Share(s.key, s.cap, s.cap) for s in slots}
    active = sorted(slots, key=lambda s: (s.account_id, s.key))
    zero: dict[str, Share] = {}
    while True:
        a = sum((s.cap for s in active), ZERO)
        with localcontext() as ctx:
            ctx.prec = 60
            exact = {s.key: filled * s.cap / a for s in active}
        below = [s for s in active if minimum > 0 and exact[s.key] < minimum]
        if not below:
            break
        for s in below:
            zero[s.key] = Share(s.key, ZERO, exact[s.key],
                                f"its pro-rata share of the partial fill ({exact[s.key].quantize(FRACTIONAL)}) is below "
                                f"the policy's minimum allocation of {minimum} — it receives none")
        active = [s for s in active if s.key not in zero]
        if not active:
            raise AllocationError(f"the filled {filled} is too small to give any account the policy's minimum "
                                  f"allocation of {minimum}")
    got = apportion([(s.key, s.account_id, s.cap, s.cap, s.unit) for s in active], filled)
    out = dict(zero)
    for s in active:
        out[s.key] = Share(s.key, got[s.key], exact[s.key])
    if sum((x.quantity for x in out.values()), ZERO) != filled:
        raise AllocationError("internal: the allocations do not add up to the filled quantity")
    return out


def relieve_lots(lots: list[tuple[str, Decimal]], final: Decimal, requested: Decimal, unit: Decimal
                 ) -> list[tuple[str, Decimal]]:
    """The account's own instructed lots relieved for ``final`` of a
    ``requested`` sale: all of them when it got everything; otherwise pro rata
    (largest remainder, ties by lot id), in the account's unit."""
    if not lots or final <= 0:
        return []
    lot_total = sum((q for _, q in lots), ZERO)
    if final >= requested:
        return [(k, q) for k, q in lots]
    with localcontext() as ctx:
        ctx.prec = 60
        target = floor_to(min(lot_total, lot_total * final / requested), unit)
    got = apportion([(k, k, q, q, unit) for k, q in lots], target)
    return [(k, got[k]) for k, _ in lots if got[k] > 0]
