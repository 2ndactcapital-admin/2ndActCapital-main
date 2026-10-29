"""csv_upload — values from an APPROVED file upload (services.security_data.uploads).
Each value carries the upload's own licence (entered by the uploader), which
is what the licence rule judges. Nothing is in the store before approval."""

from __future__ import annotations

from datetime import date

from services.security_data.providers import store
from services.security_data.providers.base import BaseProvider


class CsvUploadProvider(BaseProvider):
    key = "csv_upload"
    # fund_holdings: NOT from uploads this sprint — rep1's holdings store is platform-only
    # (replica_holdings_snapshots_platform_chk), so an org's file would publish to every org.
    capabilities = frozenset({"eod_price", "corporate_action", "index_level"})

    async def read(self, conn, ctx, data_type, subjects, *, end: date, start: date | None = None) -> dict:
        if data_type == "eod_price":
            return await store.prices(conn, ctx.org_id, self.key, subjects, end=end, start=start)
        if data_type == "index_level":
            return await store.index_levels(conn, ctx.org_id, self.key, subjects, end=end, start=start)
        if data_type == "corporate_action":
            return await store.corporate_actions(conn, ctx.org_id, self.key, subjects, end=end, start=start)
        return {}
