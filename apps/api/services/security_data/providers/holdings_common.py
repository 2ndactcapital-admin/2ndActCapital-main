"""Shared by the regulator_filing (N-PORT) and issuer_file providers: rep1's
holdings path RE-HOMED behind the provider contract, not rewritten. read() is
the latest LIVE snapshot fetched through this source; fetch() is
services.replica_holdings.ingest with the provider's injected HTTP client
(rep1's own daily refresh, services.replica_schedule, keeps running it too —
the nightly here does not fetch holdings a second time)."""

from __future__ import annotations

from datetime import date

from services.security_data.providers import store
from services.security_data.providers.base import BaseProvider, FetchOutcome


class HoldingsProvider(BaseProvider):
    capabilities = frozenset({"fund_holdings"})
    fetches = frozenset()          # rep1's schedule owns the daily holdings fetch

    def __init__(self, key: str, *, http=None):
        self.key = key
        self.http = http           # an httpx.AsyncClient (tests: transport=httpx.MockTransport(fixture))

    async def read(self, conn, ctx, data_type, subjects, *, end: date, start: date | None = None) -> dict:
        if data_type != "fund_holdings":
            return {}
        return await store.holdings_snapshots(conn, self.key, subjects, end=end)

    async def fetch_fund(self, pool, *, fund_gsid: str | None, ticker: str, series_id: str | None,
                         dry_run: bool = False):
        """One fund through rep1's ingest (fetch → parse → resolve → validate → diff → snapshot)."""
        from services import replica_holdings as H

        async with pool.acquire() as conn:
            sources = {s["key"]: s for s in await H.list_sources(conn)}
        source = sources.get(self.key)
        if source is None:
            raise LookupError(f"holdings source {self.key!r} is not registered in replica_holdings_sources")
        http = self.http or H.make_http()
        try:
            return await H.ingest(pool, fund_global_security_id=fund_gsid, ticker=ticker, source=source,
                                  series_id=series_id, http=http, dry_run=dry_run)
        finally:
            if self.http is None:
                await http.aclose()

    async def fetch(self, pool, data_type, subjects, *, today, budget_left):
        return FetchOutcome(skipped=[f"{self.key}: fund holdings are refreshed by rep1's daily holdings schedule"])
