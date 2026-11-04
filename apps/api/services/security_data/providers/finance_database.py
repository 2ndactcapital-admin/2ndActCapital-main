"""finance_database — sector / industry / industry group / country (equities) and fund category (ETFs).

SOURCE (Task 1a, 2026-11-04): github.com/JerBouma/FinanceDatabase (the prompt's "JetBrains" is a slip — the
repository is JerBouma's). NOT one CSV per asset class: one CSV PER YAHOO EXCHANGE CODE under
database/equities/ and database/etfs/. The bot commits updates several times a week; the repository is
MIT-licensed, the data is compiled from third-party sources — internal classification only (licence row).

    equities/<EX>.csv  symbol,name,summary,currency,sector,industry_group,industry,exchange,mic,market,country,
                       state,city,zipcode,website,market_cap,isin,cusip,figi,composite_figi,shareclass_figi,delisted
    etfs/<EX>.csv      symbol,name,currency,summary,category_group,category,family,exchange,mic,isin,delisted

``summary`` is a quoted field that often spans several lines (stream_csv_rows assembles it). Only the US
exchanges are read (US_EQUITY_FILES / US_ETF_FILES); OTC (PNK, ~14 MB) is off by default. Rows with
delisted = True are skipped and counted. FinanceDatabase never CREATES a security: its rows match existing ones
(CUSIP → FIGI → ticker + exchange) and add attributes; an unmatched row is counted 'unmapped'."""

from __future__ import annotations

from typing import AsyncIterator

from services import security_master_symbols as SYM
from services.security_data.providers.reference_common import (Digest, ParseStats, ReferenceProvider, SourceRecord,
                                                              SourceTransport, stream_csv_rows)

KEY = "finance_database"
BASE = "https://raw.githubusercontent.com/JerBouma/FinanceDatabase/main/database"
US_EQUITY_FILES = ("NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS")
US_ETF_FILES = ("PCX", "NGM", "NMS", "BTS", "NYQ", "ASE")


class FinanceDatabaseProvider(ReferenceProvider):
    def __init__(self) -> None:
        super().__init__(KEY)


def url(kind: str, code: str) -> str:
    return f"{BASE}/{kind}/{code}.csv"


def _clean(v) -> str | None:
    s = (v or "").strip()
    return s or None


def to_record(kind: str, row: dict, stats: ParseStats, *, as_of=None) -> SourceRecord | None:
    stats.rows += 1
    if (row.get("delisted") or "").strip().lower() == "true":
        stats.exclude("delisted in FinanceDatabase")
        return None
    ticker = SYM.normalize_symbol(row.get("symbol"))
    if not ticker or "." in (row.get("symbol") or "") and (row.get("symbol") or "").split(".")[-1] in ("L", "TO", "V"):
        stats.exclude("no US symbol")
        return None
    mic = SYM.FD_EXCHANGE.get((row.get("exchange") or "").strip().upper())
    attrs: dict[str, str] = {}
    if kind == "equities":
        for f in ("sector", "industry", "industry_group", "country"):
            v = _clean(row.get(f))
            if v:
                attrs[f] = v
    else:
        attrs["is_etf"] = "true"
        cat = _clean(row.get("category")) or _clean(row.get("category_group"))
        if cat:
            attrs["fund_category"] = cat
    if not attrs:
        stats.exclude("no attribute to add")
        return None
    cusip = _clean(row.get("cusip"))
    figi = _clean(row.get("composite_figi")) or _clean(row.get("figi"))
    stats.kept += 1
    return SourceRecord(KEY, ticker=ticker, name=_clean(row.get("name")), exchange=mic,
                        security_type="fund" if kind == "etfs" else None,
                        cusip=cusip.upper() if cusip and SYM.is_valid_cusip(cusip) else None,
                        isin=(_clean(row.get("isin")) or "").upper() or None, figi=figi, attrs=attrs, as_of=as_of,
                        raw_symbol=row.get("symbol"))


async def fetch(transport: SourceTransport, *, kinds=("equities", "etfs"), equity_files=US_EQUITY_FILES,
                etf_files=US_ETF_FILES, as_of=None, budget=None) -> AsyncIterator[tuple]:
    """('record', SourceRecord) … then ('done', ParseStats). One request per CSV (budget-checked); each CSV is
    streamed record by record."""
    stats = ParseStats()
    digest = Digest()
    for kind in kinds:
        for code in (equity_files if kind == "equities" else etf_files):
            if budget is not None:
                await budget.take()
            async for row in stream_csv_rows(transport.lines(url(kind, code)), digest):
                rec = to_record(kind, row, stats, as_of=as_of)
                if rec is not None:
                    yield "record", rec
    stats.sha256 = digest.hexdigest()
    yield "done", stats
