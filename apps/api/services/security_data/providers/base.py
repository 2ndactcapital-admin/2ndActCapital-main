"""The provider contract and the shared HTTP plumbing.

CONTRACT
  key: str                      the data_providers row it serves
  capabilities: frozenset[str]  data types it can answer
  async read(conn, ctx, data_type, subjects, *, end, start=None) -> {subject: [Point, newest first]}
      DB only, never the network. start=None → the latest point on or before ``end``.
  async fetch(pool, data_type, subjects, *, today, budget_left) -> FetchOutcome
      The nightly's refresh: network through the INJECTED transport, at most
      ``budget_left`` calls, written into the canonical store. A provider with
      nothing to fetch (custodian, uploads, adviser entry) returns an empty outcome.
  scrub(text) -> str

Shared with services.market_data (no behaviour change there): the Throttle and
retry statuses from fred.py, scrub_error from adapters.py.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from services.market_data.adapters import scrub_error
from services.market_data.fred import RETRY_STATUSES, Throttle


@dataclass
class FetchOutcome:
    calls: int = 0
    written: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    budget_exhausted: bool = False


class ProviderNotConnected(RuntimeError):
    pass


class BaseProvider:
    key = ""
    capabilities: frozenset[str] = frozenset()
    fetches: frozenset[str] = frozenset()   # data types fetch() refreshes over the network

    def scrub(self, text: Any) -> str:
        return scrub_error(text)

    async def read(self, conn, ctx, data_type: str, subjects: list[str], *, end: date,
                   start: date | None = None) -> dict:
        return {}

    async def fetch(self, pool, data_type: str, subjects: list[str], *, today: date, budget_left: int | None):
        return FetchOutcome()

    async def aclose(self) -> None:
        return None


class HttpTransportBase:
    """Throttled, retried GET over an injectable transport: anything with
    ``async get(url, params) -> (status, body)``. A real vendor module passes a
    real one; tests pass recorded fixtures. Never logs a URL's query string."""

    def __init__(self, transport, *, min_interval: float = 0.5, max_retries: int = 3, sleep=asyncio.sleep):
        self.transport = transport
        self.throttle = Throttle(min_interval, sleep)
        self.max_retries = max_retries
        self.sleep = sleep
        self.calls = 0

    async def get(self, url: str, params: dict | None = None):
        if self.transport is None:
            raise ProviderNotConnected("no transport configured — the provider is not connected")
        last = None
        for attempt in range(self.max_retries + 1):
            await self.throttle.wait()
            self.calls += 1
            status, body = await self.transport.get(url, params or {})
            if status not in RETRY_STATUSES:
                return status, body
            last = status
            await self.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"HTTP {last} persisted through {self.max_retries} retries")
