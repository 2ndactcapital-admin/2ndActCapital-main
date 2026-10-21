"""BUGFIX1 (PORT-01) — why Positions / Securities are empty, when they are.

Positions and assets are built from custodian imports; none has run, so both
grids were empty while the assistant (reading member_investments) found the
member's Stonegate investment. An empty grid now says why and where the
member's investments are. The wording and the link come from here (the API
envelope), not the page (Rule 1).
"""
from __future__ import annotations

EMPTY_HOLDINGS_MESSAGE = (
    "Holdings appear here once account data is imported from the custodian.")
INVESTMENTS_HREF = "/portfolio"
INVESTMENTS_LABEL = "View your investments"


async def empty_holdings_hint(conn, org_id, user_id, total: int) -> dict | None:
    """None unless the grid is empty org-wide-for-this-caller AND the caller
    has at least one current member investment."""
    if total or not user_id:
        return None
    n = await conn.fetchval(
        "SELECT count(*) FROM member_investments WHERE org_id = $1 AND user_id = $2 "
        "AND valid_to IS NULL AND system_to IS NULL",
        org_id, user_id,
    )
    if not n:
        return None
    return {"message": EMPTY_HOLDINGS_MESSAGE, "href": INVESTMENTS_HREF,
            "href_label": INVESTMENTS_LABEL, "investment_count": int(n)}
