"""platform_entry — prices a Super Admin entered in the security master
(services.securities_global.add_price, A1). A row written before marketdata1
carries no provider_key and is read as this provider. Internal use only."""

from __future__ import annotations

from datetime import date

from services.security_data.providers import store
from services.security_data.providers.base import BaseProvider


class PlatformEntryProvider(BaseProvider):
    key = "platform_entry"
    capabilities = frozenset({"eod_price"})

    async def read(self, conn, ctx, data_type, subjects, *, end: date, start: date | None = None) -> dict:
        if data_type != "eod_price":
            return {}
        return await store.prices(conn, ctx.org_id, self.key, subjects, end=end, start=start)
