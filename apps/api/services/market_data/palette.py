"""Server-side colours for the market data catalog (mkt03). Pure.

The API response is the source of truth for colours (CLAUDE.md Rule 1): the
frontend never picks one. Moving the base palette into the config table later
is a separate, optional change.

Each category has a base colour. Each series in a category gets a shade:
convert the base to HSL, keep hue and saturation, and spread lightness evenly
from (base L − 0.10) to (base L + 0.18), in sort_order. A category with one
series uses the base colour itself.

The clamp to [0.28, 0.68] is applied to the two ENDS of that range, and the
shades are spread evenly between the clamped ends. Clamping each shade
separately (the literal reading) piles every shade past the bound onto the
same colour — the gold note ramp, base L 0.637, would put most of its 54 notes
on L = 0.68 — which breaks "distinct within a category". If two shades still
round to the same #RRGGBB, the later one steps its lightness by 1/510 at a time
(alternating up and down, inside the clamp) until it is unique; that is
deterministic, so every call returns identical colours.

dailyops1: an 8-bit colour has only about 300 distinct shades of one hue and
saturation inside the clamp (measured: 297–309 across the bases here). A
larger group used to raise RuntimeError — and the security master's equity /
fund groups (thousands of rows) took the whole catalog down with it. ``ramp``
now NEVER raises: when the distinct shades run out it cycles through the
largest distinct ramp that fits (``CYCLE_MAX`` shades, halved until it fits),
so the same input always gives the same colours. Every size that fitted before
gets exactly the colours it got before.
"""
from __future__ import annotations

import colorsys
from typing import Sequence

CATEGORY_BASE = {
    "Equities": "#2B5F9E",
    "Rates & credit": "#C8641E",
    "Growth & labor": "#3F8A5F",
    "Inflation": "#9B5A8A",
    "Housing": "#6F5E4E",
    "Commodities": "#17707A",
    "FX": "#4A5568",
}
NEUTRAL = "#64748B"
INDEX_SECURITY_BASE = "#1B2B4B"   # navy ramp
NOTE_SECURITY_BASE = "#C5A880"    # gold ramp

L_BELOW = 0.10
L_ABOVE = 0.18
L_MIN = 0.28
L_MAX = 0.68
_STEP = 1 / 510


def category_base(category: str) -> str:
    return CATEGORY_BASE.get(category, NEUTRAL)


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(rgb: Sequence[float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02X}" for c in rgb)


def _hls_hex(h: float, l: float, s: float) -> str:
    return _rgb_to_hex(colorsys.hls_to_rgb(h, l, s))


class _Exhausted(Exception):
    """Internal: no distinct shade is left for the next member."""


def _distinct_ramp(base: str, count: int) -> list[str]:
    """The mkt03 algorithm, unchanged: ``count`` distinct shades or _Exhausted."""
    h, l, s = colorsys.rgb_to_hls(*_hex_to_rgb(base))
    lo = min(max(l - L_BELOW, L_MIN), L_MAX)
    hi = min(max(l + L_ABOVE, L_MIN), L_MAX)
    out: list[str] = []
    used: set[str] = set()
    for i in range(count):
        target = lo + (hi - lo) * i / (count - 1)
        color = _hls_hex(h, target, s)
        k = 1
        while color in used:
            delta = _STEP * ((k + 1) // 2) * (1 if k % 2 else -1)
            candidate_l = target + delta
            if L_MIN <= candidate_l <= L_MAX:
                color = _hls_hex(h, candidate_l, s)
            k += 1
            if k > 4000:  # the band is full: no distinct shade left
                raise _Exhausted
        used.add(color)
        out.append(color)
    return out


CYCLE_MAX = 256


def ramp(base_hex: str, count: int) -> list[str]:
    """``count`` shades of ``base_hex``, in order. count==1 → [base].

    Distinct while the band has room (every size that fitted before keeps its
    exact colours); past that, a deterministic cycle. Never raises."""
    if count <= 0:
        return []
    base = "#" + base_hex.lstrip("#").upper()
    if count == 1:
        return [base]
    try:
        return _distinct_ramp(base, count)
    except _Exhausted:
        pass
    n = min(count - 1, CYCLE_MAX)
    while n > 1:
        try:
            pool = _distinct_ramp(base, n)
            break
        except _Exhausted:
            n //= 2
    else:
        pool = [base]
    return [pool[i % len(pool)] for i in range(count)]


def assign(groups: dict[str, list[str]], base_of) -> dict[str, str]:
    """{member: colour} for {group: [members in display order]}.

    ``base_of(group)`` gives the group's base colour.
    """
    colors: dict[str, str] = {}
    for group, members in groups.items():
        for member, color in zip(members, ramp(base_of(group), len(members))):
            colors[member] = color
    return colors
