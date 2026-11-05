"""BUGFIX3 (MUSE-OLIVO-LOW, 1d) — one Decimal → JSON text rule: FIXED-POINT, never scientific notation.

``str(Decimal)`` is not a display format. asyncpg decodes a ``numeric`` whose trailing base-10000 digit groups are
zero into an exponent form (BUGFIX1 already hit a stored ``1.00E+6``), and Python arithmetic produces the same
(``Decimal('0.90') * Decimal('1E+6')`` is ``9.0E+5``). The front end's string-only money formatters split on the
decimal point, so ``"9.0E+5"`` rendered as ``$9.0E`` — the "$9.0E…" cost HollisMuse saw on the Olivo pass.

``format(d, "f")`` is exact (no rounding, no float) and always plain digits. Every portfolio serializer that used to
call ``str()`` on a Decimal now calls :func:`fixed`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def fixed(value: Any) -> str | None:
    """Decimal / int → exact fixed-point text; ``None`` stays ``None``; anything else ``str()``."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return str(value)
        return format(value, "f")
    return str(value)


__all__ = ["fixed"]
