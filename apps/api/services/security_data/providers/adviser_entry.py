"""adviser_entry — rep8's adviser-entered quotes (portfolio.adviser_quotes) as
a provider. option_quote ← kind 'premium'; index_level ← 'index_level';
eod_price ← 'underlying_price'. The quote's own source text and entered_by
travel in the detail. Internal use only (provider licence)."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from services.security_data.providers.base import BaseProvider
from services.security_data.types import Point, d

KIND_FOR = {"option_quote": "premium", "index_level": "index_level", "eod_price": "underlying_price"}


class AdviserEntryProvider(BaseProvider):
    key = "adviser_entry"
    capabilities = frozenset(KIND_FOR)

    async def read(self, conn, ctx, data_type, subjects, *, end: date, start: date | None = None) -> dict:
        kind = KIND_FOR.get(data_type)
        ids = [s for s in subjects if _is_uuid(s)]
        if kind is None or not ids:
            return {}
        end_ts = datetime.combine(end, time.max, tzinfo=timezone.utc)
        start_ts = datetime.combine(start, time.min, tzinfo=timezone.utc) if start else None
        rows = await conn.fetch(
            "SELECT id, global_security_id, value, source, as_of, entered_by FROM portfolio.adviser_quotes "
            "WHERE org_id = $1::uuid AND global_security_id = ANY($2::uuid[]) AND kind = $3 AND as_of <= $4 "
            "AND ($5::timestamptz IS NULL OR as_of >= $5::timestamptz) AND valid_to IS NULL AND system_to IS NULL "
            "ORDER BY global_security_id, as_of DESC", str(ctx.org_id), ids, kind, end_ts, start_ts)
        out: dict = {}
        for r in rows:
            k = str(r["global_security_id"])
            if start is None and k in out:
                continue
            out.setdefault(k, []).append(Point(d(r["value"]), r["as_of"].date(), self.key, {}, [], {
                "quote_id": str(r["id"]), "quote_kind": kind, "source_text": r["source"],
                "entered_by": str(r["entered_by"])}))
        return out


def _is_uuid(s: str) -> bool:
    import uuid
    try:
        uuid.UUID(str(s))
        return True
    except ValueError:
        return False
