"""custodian — account valuations as prices.

price = position market value ÷ quantity, on the position's as_of_date — the
custodian's own daily valuation (rep5 calls this source 'account_position';
held-away positions carry their statement's source + as-of, rep7). Never
stored as a price: read from portfolio.positions at the call, so it can never
drift from the account.

LICENCE: internal use for the org (any account the org holds the security in);
member display only for that member's own holdings — a member-display read
reads ONLY the accounts in ``ctx.account_ids`` and marks those values
``own_holdings``; with no account scope it reads nothing.

Several accounts valuing the same security on the same day: the largest
position's valuation (the most shares behind the number) wins, deterministically.
"""

from __future__ import annotations

from datetime import date

from services.security_data.providers.base import BaseProvider
from services.security_data.types import Point, d

_SQL = """
SELECT DISTINCT ON (p.account_id, p.asset_id, p.as_of_date)
       a.global_security_id, p.account_id, p.as_of_date, p.quantity, p.market_value, p.source_system
FROM portfolio.positions p
JOIN portfolio.assets a ON a.id = p.asset_id AND a.system_to IS NULL
WHERE p.org_id = $1::uuid AND a.global_security_id = ANY($2::uuid[]) AND p.account_id IS NOT NULL
  AND p.valid_to IS NULL AND p.system_to IS NULL AND p.as_of_date <= $3
  AND ($4::date IS NULL OR p.as_of_date >= $4::date)
  AND ($5::uuid[] IS NULL OR p.account_id = ANY($5::uuid[]))
  AND p.quantity > 0 AND p.market_value > 0
ORDER BY p.account_id, p.asset_id, p.as_of_date DESC, p.system_from DESC
"""


class CustodianProvider(BaseProvider):
    key = "custodian"
    capabilities = frozenset({"eod_price"})

    async def read(self, conn, ctx, data_type, subjects, *, end: date, start: date | None = None) -> dict:
        if data_type != "eod_price" or not subjects:
            return {}
        if ctx.purpose == "member_display" and not ctx.account_ids:
            return {}
        scope = [str(x) for x in ctx.account_ids] if ctx.account_ids else None
        rows = await conn.fetch(_SQL, str(ctx.org_id), list(subjects), end, start, scope)
        best: dict[tuple[str, date], dict] = {}
        for r in rows:
            k = (str(r["global_security_id"]), r["as_of_date"])
            q = d(r["quantity"])
            if k not in best or q > best[k]["q"]:
                best[k] = {"q": q, "mv": d(r["market_value"]), "acct": str(r["account_id"]),
                           "src": r["source_system"]}
        out: dict = {}
        for (gsid, day), b in sorted(best.items(), key=lambda kv: kv[0][1], reverse=True):
            if start is None and gsid in out:
                continue
            price = b["mv"] / b["q"]
            out.setdefault(gsid, []).append(Point(price, day, self.key, {}, [], {
                "account_id": b["acct"], "quantity": format(b["q"], "f"), "market_value": format(b["mv"], "f"),
                "source_system": b["src"], "own_holdings": ctx.purpose == "member_display" and scope is not None,
                "method": "position market value ÷ quantity on the position date"}))
        return out
