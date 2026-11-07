"""finance_database — sector / industry / industry group / country (equities) and fund category (ETFs).

SOURCE (Task 1a, 2026-11-04): github.com/JerBouma/FinanceDatabase (the prompt's "JetBrains" is a slip — the
repository is JerBouma's). NOT one CSV per asset class: one CSV PER YAHOO EXCHANGE CODE under
database/equities/ and database/etfs/. The bot commits updates several times a week; the repository is
MIT-licensed, the data is compiled from third-party sources — internal classification only (licence row).

    equities/<EX>.csv  symbol,name,summary,currency,sector,industry_group,industry,exchange,mic,market,country,
                       state,city,zipcode,website,market_cap,isin,cusip,figi,composite_figi,shareclass_figi,delisted
    etfs/<EX>.csv      symbol,name,currency,summary,category_group,category,family,exchange,mic,isin,delisted

``summary`` is a quoted field that often spans several lines (stream_csv_rows assembles it). Only the US
exchanges are read (US_EQUITY_FILES / US_ETF_FILES / US_FUND_FILES); OTC (PNK, ~14 MB) is off by default. Rows
with delisted = True are skipped and counted. FinanceDatabase never CREATES a security: its rows match existing
ones (CUSIP → FIGI → ticker + exchange) and add attributes; an unmatched row is counted 'unmapped'.

SECMASTER2 (Task 1a, sampled 2026-11-07 through the GitHub contents API):
  * THE 404. The attributes job's one run (12611760-…, 2026-11-06) was HELD on
    ``database/etfs/ASE.csv`` — FinanceDatabase publishes ``equities/ASE.csv`` but NO ``etfs/ASE.csv`` (its NYSE
    American ETFs are filed under PCX). Every other configured file answered 200; ASE was the LAST ETF file, so the
    run had already applied the others. US_ETF_FILES is now what the repository publishes (ASE out; NCM, published
    and previously unread, in). A file it no longer publishes raises SourceFormatChanged naming the file and the
    fix — the run HOLDS and a Super Admin is alerted (never a silent empty load); so does a missing column.
  * US MUTUAL FUNDS. ``database/funds/NAS.csv`` (~13.8 MB, Nasdaq's mutual-fund symbols):
        symbol,name,currency,summary,category_group,category,family,exchange,mic   (no isin, no delisted)
    read as kind 'funds' — category for the share classes the SEC mutual-fund provider (sec_mutual_funds) adds.
  * THE CATEGORY GROUP. ``category`` alone is ambiguous: "Emerging Markets" is filed under Equities AND Fixed
    Income, "Large Cap" under Equities AND Derivatives (leveraged / inverse). The group is now kept as its own
    attribute, ``fund_category_group`` (fund_category is unchanged, so a re-run rewrites nothing that was right).
  * NAMES are never taken from here for display: the provider row says lic_member_display = not_allowed."""

from __future__ import annotations

from typing import AsyncIterator

from services import security_master_symbols as SYM
from services.security_data.providers.reference_common import (Digest, ParseStats, ReferenceProvider, SourceFormatChanged,
                                                              SourceHTTPError, SourceRecord, SourceTransport,
                                                              stream_csv_rows)

KEY = "finance_database"
BASE = "https://raw.githubusercontent.com/JerBouma/FinanceDatabase/main/database"
US_EQUITY_FILES = ("NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS")
#: SECMASTER2 — what the repository publishes today (etfs/ASE.csv does not exist: the 404 that held the job)
US_ETF_FILES = ("PCX", "NGM", "NMS", "BTS", "NYQ", "NCM")
US_FUND_FILES = ("NAS",)
KINDS = ("equities", "etfs", "funds")
#: the columns each kind is read for — a file without one of them is a format change (the run holds)
REQUIRED_COLUMNS = {"equities": ("symbol", "name", "sector", "industry", "industry_group", "country", "exchange"),
                    "etfs": ("symbol", "name", "category_group", "category", "exchange"),
                    "funds": ("symbol", "name", "category_group", "category")}
LISTING_URL = "https://github.com/JerBouma/FinanceDatabase/tree/main/database"


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
        if kind == "etfs":
            attrs["is_etf"] = "true"
        cat = _clean(row.get("category")) or _clean(row.get("category_group"))
        if cat:
            attrs["fund_category"] = cat
        grp = _clean(row.get("category_group"))
        if grp:
            attrs["fund_category_group"] = grp
    if not attrs:
        stats.exclude("no attribute to add")
        return None
    cusip = _clean(row.get("cusip"))
    figi = _clean(row.get("composite_figi")) or _clean(row.get("figi"))
    stats.kept += 1
    return SourceRecord(KEY, ticker=ticker, name=_clean(row.get("name")), exchange=mic,
                        security_type="fund" if kind in ("etfs", "funds") else None,
                        cusip=cusip.upper() if cusip and SYM.is_valid_cusip(cusip) else None,
                        isin=(_clean(row.get("isin")) or "").upper() or None, figi=figi, attrs=attrs, as_of=as_of,
                        raw_symbol=row.get("symbol"))


def _header_check(kind: str, code: str):
    def check(cols: list[str]) -> None:
        miss = [c for c in REQUIRED_COLUMNS[kind] if c not in cols]
        if miss:
            raise SourceFormatChanged(
                f"FinanceDatabase changed its format: {kind}/{code}.csv has no column(s) {', '.join(miss)} "
                f"(header: {', '.join(cols[:14])}) — services/security_data/providers/finance_database.py reads "
                "them; the run is held until the provider is updated")
    return check


async def fetch(transport: SourceTransport, *, kinds=KINDS, equity_files=US_EQUITY_FILES,
                etf_files=US_ETF_FILES, fund_files=US_FUND_FILES, as_of=None, budget=None) -> AsyncIterator[tuple]:
    """('record', SourceRecord) … then ('done', ParseStats). One request per CSV (budget-checked); each CSV is
    streamed record by record. A file that is gone (404) or lacks a column it reads → SourceFormatChanged (the job
    holds); every file parsing to zero rows → SourceFormatChanged too (never a silent empty load)."""
    stats = ParseStats()
    digest = Digest()
    files = {"equities": equity_files, "etfs": etf_files, "funds": fund_files}
    for kind in kinds:
        for code in files[kind]:
            if budget is not None:
                await budget.take()
            u = url(kind, code)
            try:
                async for row in stream_csv_rows(transport.lines(u), digest, on_header=_header_check(kind, code)):
                    rec = to_record(kind, row, stats, as_of=as_of)
                    if rec is not None:
                        yield "record", rec
            except SourceHTTPError as exc:
                if exc.status == 404:
                    raise SourceFormatChanged(
                        f"FinanceDatabase no longer publishes {kind}/{code}.csv (HTTP 404 for {u}) — the files it "
                        f"publishes changed: correct the {kind} list in services/security_data/providers/"
                        f"finance_database.py against {LISTING_URL}/{kind}. Files read before this one were applied "
                        "(the matcher is idempotent: re-running them after the fix rewrites nothing).") from None
                raise
    if stats.rows == 0:
        raise SourceFormatChanged("FinanceDatabase: every file parsed to zero rows — not recorded as a load")
    stats.sha256 = digest.hexdigest()
    yield "done", stats
