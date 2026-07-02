"""FX rate lookup service (Sprint 19).

Provides a simple point-in-time rate lookup from fx_rates.
Not used for live multi-currency conversion yet — scaffolding only.
"""

from datetime import date
from typing import Optional


async def get_rate(
    pool,
    base: str,
    quote: str,
    as_of: Optional[date] = None,
) -> Optional[float]:
    """Return the exchange rate base → quote on or before as_of.

    Returns 1.0 when base == quote.
    Returns None when no rate is found (caller decides how to handle).
    as_of defaults to today's date when not provided.
    """
    if base.upper() == quote.upper():
        return 1.0

    as_of_date = as_of or date.today()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT rate FROM fx_rates
            WHERE base_ccy = $1 AND quote_ccy = $2 AND as_of_date <= $3
            ORDER BY as_of_date DESC
            LIMIT 1
            """,
            base.upper(),
            quote.upper(),
            as_of_date,
        )

    if row is not None:
        return float(row["rate"])
    return None
