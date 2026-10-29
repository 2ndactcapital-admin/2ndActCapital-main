"""public_api — FRED rates and Treasury curves as index_level series.

REUSES services.market_data.fred.FredClient (its throttle, retries and scrub)
and ingest.parse_observations ('.' = no value), with the SAME FRED_API_KEY —
no new key. Writes portfolio.index_levels (provider 'fred'), NOT the mkt01
indicator store: the two stores stay separate on purpose.

A subject is a FRED series code (DGS10 …). Every request counts against the
provider's daily call budget; one series = one call (a full history page of up
to 100 000 observations is far more than any series here)."""

from __future__ import annotations

import os
from datetime import date
from typing import Mapping

from services.security_data.providers import store
from services.security_data.providers.base import BaseProvider, FetchOutcome

TREASURY_CURVE = ("DGS1MO", "DGS3MO", "DGS6MO", "DGS1", "DGS2", "DGS5", "DGS7", "DGS10", "DGS20", "DGS30", "DFF")


class FredProvider(BaseProvider):
    key = "fred"
    capabilities = frozenset({"index_level"})
    fetches = frozenset({"index_level"})

    def __init__(self, *, transport=None, environ: Mapping[str, str] | None = None, client=None):
        self.transport = transport
        self.environ = os.environ if environ is None else environ
        self._client = client

    def client(self):
        if self._client is None:
            from services.market_data.fred import FredClient
            key = (self.environ.get("FRED_API_KEY") or "").strip()
            if not key:
                from services.market_data.adapters import MissingConfiguration
                raise MissingConfiguration("FRED_API_KEY")
            self._client = FredClient(key, self.transport, min_interval=0.0 if self.transport else 0.6)
        return self._client

    def scrub(self, text) -> str:
        from services.market_data.adapters import scrub_error
        return scrub_error(text, self._client)

    async def read(self, conn, ctx, data_type, subjects, *, end: date, start: date | None = None) -> dict:
        if data_type != "index_level":
            return {}
        return await store.index_levels(conn, ctx.org_id, self.key, subjects, end=end, start=start)

    async def fetch(self, pool, data_type, subjects, *, today: date, budget_left: int | None) -> FetchOutcome:
        from services.market_data import ingest
        from services.security_data import writes

        out = FetchOutcome()
        if data_type != "index_level":
            return out
        for code in subjects:
            if budget_left is not None and out.calls >= budget_left:
                out.budget_exhausted = True
                out.skipped.append(code)
                continue
            out.calls += 1
            try:
                raw = await self.client().get_observations(code)
            except Exception as exc:  # noqa: BLE001 — one series failing never stops the others
                out.errors.append(f"{code}: {self.scrub(exc)}")
                continue
            parsed = ingest.parse_observations(raw)
            out.written += await writes.upsert_index_levels(pool, code, [(dt, v) for dt, v in parsed.points
                                                                        if dt <= today], provider_key=self.key)
        return out

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
