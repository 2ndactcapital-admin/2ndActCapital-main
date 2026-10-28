"""vendor — the SLOT a paid vendor plugs into. Not connected: registered as
'not_connected', no rights, and it makes NO network call — read() returns
nothing and fetch() refuses before touching its transport. A real vendor is
a copy of this module that implements read() over the canonical stores
(provider_key = its key) and fetch() over HttpTransportBase. See "Add a
vendor" in docs/SECURITY_DATA_DESIGN.md."""

from __future__ import annotations

from datetime import date

from services.security_data.providers.base import BaseProvider, FetchOutcome


class VendorStubProvider(BaseProvider):
    capabilities = frozenset({"eod_price", "corporate_action", "index_level", "option_quote", "fx"})
    fetches = frozenset()
    connected = False

    def __init__(self, key: str, *, transport=None):
        self.key = key
        self.transport = transport     # held, never called while not connected

    async def read(self, conn, ctx, data_type, subjects, *, end: date, start: date | None = None) -> dict:
        return {}

    async def fetch(self, pool, data_type, subjects, *, today: date, budget_left: int | None) -> FetchOutcome:
        return FetchOutcome(skipped=[f"{self.key}: not connected — no network call made"])
