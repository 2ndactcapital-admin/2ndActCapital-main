"""sec_tickers — CIK + exchange per ticker: sec.gov/files/company_tickers_exchange.json.

FORMAT (sampled 2026-11-04): {"fields": ["cik", "name", "ticker", "exchange"], "data": [[320193, "Apple Inc.",
"AAPL", "Nasdaq"], …]} — ~10.4k rows; exchange ∈ Nasdaq, NYSE, CBOE, TXSE, OTC or null. Share classes and
preferreds are hyphenated (BRK-B, PCG-PA) → canonical BRK.B / PCG.PRA. One request, through
services.edgar_fetch (the SEC's declared User-Agent and rate limit). The body is ~0.5 MB: read whole, then the
rows are yielded one at a time.

Each record states: cik (10 digits, zero-padded, the EDGAR form) and exchange (MIC) when the SEC names one.
A row with no exchange is kept for its CIK (it may match an existing security) but never creates one."""

from __future__ import annotations

import json
from typing import Iterator

from services import security_master_symbols as SYM
from services.security_data.providers.reference_common import (Digest, ParseStats, ReferenceProvider, SourceRecord,
                                                              SourceTransport, read_text)

KEY = "sec_tickers"
URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FILE_KEY = "company_tickers_exchange"


class SecTickersProvider(ReferenceProvider):
    def __init__(self) -> None:
        super().__init__(KEY)


def parse(doc: dict, stats: ParseStats, *, as_of=None) -> Iterator[SourceRecord]:
    fields = [str(f).lower() for f in (doc.get("fields") or [])]
    try:
        i_cik, i_name, i_tic, i_ex = (fields.index(k) for k in ("cik", "name", "ticker", "exchange"))
    except ValueError:
        raise ValueError(f"company_tickers_exchange.json: unexpected fields {fields!r}") from None
    for row in doc.get("data") or []:
        stats.rows += 1
        try:
            cik, name, tic, ex = row[i_cik], row[i_name], row[i_tic], row[i_ex]
        except (IndexError, TypeError):
            stats.exclude("malformed row")
            continue
        ticker = SYM.normalize_symbol(tic)
        if not ticker or cik in (None, ""):
            stats.exclude("no ticker or CIK")
            continue
        mic = SYM.sec_exchange_mic(ex)
        attrs = {"cik": str(int(cik)).zfill(10)}
        if mic:
            attrs["exchange"] = mic
        stats.kept += 1
        yield SourceRecord(KEY, ticker=ticker, name=(name or "").strip() or ticker, exchange=mic,
                           security_type="equity", raw_symbol=tic, attrs=attrs, as_of=as_of,
                           note=None if mic else "the SEC names no exchange")


async def fetch(transport: SourceTransport, *, as_of=None, budget=None) -> tuple[list[SourceRecord], ParseStats]:
    if budget is not None:
        await budget.take()
    digest = Digest()
    text = await read_text(transport, URL, digest)
    stats = ParseStats(sha256=digest.hexdigest())
    return list(parse(json.loads(text), stats, as_of=as_of)), stats
