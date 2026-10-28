"""MARKETDATA1 — the vendor-neutral security-data layer.

Readers ask for a DATA TYPE, never a vendor:

    from services import security_data as SD
    r = await SD.get(conn, org_id, "eod_price", gsid, as_of=today, purpose="internal")
    r.found, r.value, r.provider_key, r.as_of, r.quality, r.licence, r.missing

The layer walks the rank for that type (org override, else platform default),
skips every source whose status, entitlement or licence does not allow the
purpose, and returns the first allowed value — or a "not priced" result naming
what is missing. It reads DB rows only; network fetches happen in the nightly
(services.security_data.nightly) through each provider's injectable transport.

Separate on purpose from services.market_data (the platform indicator history,
mkt01–03): that package's store, read gate and nightly are untouched. This one
reuses its scrub_error and FRED client.
"""

from services.security_data.types import (  # noqa: F401
    DATA_TYPES, PURPOSES, NOT_PRICED, Point, Result, ReadContext, SecurityDataError,
)
from services.security_data.layer import get, get_bulk, history  # noqa: F401
