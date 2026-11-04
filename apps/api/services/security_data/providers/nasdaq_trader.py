"""nasdaq_trader — the US listed universe: nasdaqlisted.txt + otherlisted.txt (Nasdaq Trader symbol directory).

FORMAT (sampled 2026-11-04, Task 1a). Pipe-delimited, one header line, one FOOTER line:

    nasdaqlisted.txt  Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
    otherlisted.txt   ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
    footer            File Creation Time: 1104202612:11|||||||      (MMDDYYYYHH:MM, US Eastern)

Excluded, and counted: the header, the footer, Test Issue = 'Y' rows (ZZZT*, ATEST.* …), blank lines and any row
with a malformed column count. otherlisted's exchange letter → MIC (A XASE, N XNYS, P ARCX, Z BATS, V IEXG,
M XCHI, F TXSE). The ACT symbol is the one normalised (ASB$E → ASB.PRE, AAC.W → AAC.WS); the CQS symbol's
lower-case suffixes are ambiguous once upper-cased and are not used.

Every record states: exchange, is_etf, listing_status 'listed'. The file is the universe the delisting check
compares against (services.security_master_load.mark_absent)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import AsyncIterator, Iterable, Iterator
from zoneinfo import ZoneInfo

from services import security_master_symbols as SYM
from services.security_data.providers.reference_common import (Digest, ParseStats, ReferenceProvider, SourceRecord,
                                                              SourceTransport)

KEY = "nasdaq_trader"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
FILE_KEY = "nasdaqlisted+otherlisted"
_FOOTER = re.compile(r"^File Creation Time:\s*(\d{2})(\d{2})(\d{4})(\d{2}):(\d{2})")
_ET = ZoneInfo("America/New_York")
_NAME_TAILS = (" - Common Stock", " Common Stock", " - Common Shares", " Common Shares", " - Ordinary Shares",
               " Ordinary Shares", " - Class A Common Stock")


class NasdaqTraderProvider(ReferenceProvider):
    def __init__(self) -> None:
        super().__init__(KEY)


def parse_footer(line: str):
    m = _FOOTER.match(line or "")
    if not m:
        return None
    mo, dd, yy, hh, mi = (int(x) for x in m.groups())
    return datetime(yy, mo, dd, hh, mi, tzinfo=_ET)


def tidy_name(name: str) -> str:
    n = (name or "").strip()
    for tail in _NAME_TAILS:
        if n.endswith(tail) and len(n) > len(tail) + 1:
            return n[: -len(tail)].strip()
    return n


def _record(sym_raw: str, name: str, mic: str, etf: str, stats: ParseStats, as_of) -> SourceRecord | None:
    ticker = SYM.normalize_symbol(sym_raw)
    if not ticker:
        stats.exclude("no symbol")
        return None
    is_etf = (etf or "").strip().upper() == "Y"
    stats.kept += 1
    return SourceRecord(KEY, ticker=ticker, name=tidy_name(name) or ticker, exchange=mic,
                        security_type=SYM.guess_type_from_name(name, etf=is_etf), raw_symbol=sym_raw,
                        attrs={"exchange": mic, "is_etf": "true" if is_etf else "false", "listing_status": "listed"},
                        as_of=as_of)


def parse_nasdaqlisted(lines: Iterable[str], stats: ParseStats, *, as_of=None,
                       expect_header: bool = True) -> Iterator[SourceRecord]:
    first = expect_header
    for ln in lines:
        stats.lines += 1
        if first:
            first = False
            if ln.startswith("Symbol|"):
                continue
        if not ln.strip():
            continue
        ft = parse_footer(ln)
        if ft is not None:
            stats.as_of = ft
            continue
        cols = ln.split("|")
        stats.rows += 1
        if len(cols) < 8:
            stats.exclude("malformed row")
            continue
        sym, name, _cat, test, _fin, _lot, etf, _ns = (c.strip() for c in cols[:8])
        if test.upper() == "Y":
            stats.exclude("test issue")
            continue
        rec = _record(sym, name, SYM.NASDAQ_LISTED_MIC, etf, stats, as_of)
        if rec is not None:
            yield rec


def parse_otherlisted(lines: Iterable[str], stats: ParseStats, *, as_of=None,
                      expect_header: bool = True) -> Iterator[SourceRecord]:
    first = expect_header
    for ln in lines:
        stats.lines += 1
        if first:
            first = False
            if ln.startswith("ACT Symbol|"):
                continue
        if not ln.strip():
            continue
        ft = parse_footer(ln)
        if ft is not None:
            stats.as_of = ft
            continue
        cols = ln.split("|")
        stats.rows += 1
        if len(cols) < 8:
            stats.exclude("malformed row")
            continue
        act, name, exch, _cqs, etf, _lot, test, _nq = (c.strip() for c in cols[:8])
        if test.upper() == "Y":
            stats.exclude("test issue")
            continue
        mic = SYM.OTHERLISTED_EXCHANGE.get(exch.upper())
        if mic is None:
            stats.exclude(f"unknown exchange code {exch!r}")
            continue
        rec = _record(act, name, mic, etf, stats, as_of)
        if rec is not None:
            yield rec


async def _alines(transport: SourceTransport, url: str, digest: Digest) -> AsyncIterator[str]:
    async for ln in transport.lines(url):
        digest.update(ln)
        yield ln


async def fetch_universe(transport: SourceTransport, *, as_of=None, budget=None) -> AsyncIterator[tuple]:
    """Streams ('record', SourceRecord) for both files, then ('done', ParseStats). Two requests (budget-checked).
    Parsed in chunks of 500 streamed lines; nothing holds a whole file. Only a file's FIRST chunk may carry the
    header line."""
    stats = ParseStats()
    digest = Digest()
    for url, parse in ((NASDAQ_LISTED_URL, parse_nasdaqlisted), (OTHER_LISTED_URL, parse_otherlisted)):
        if budget is not None:
            await budget.take()
        buf: list[str] = []
        first_chunk = True
        async for ln in _alines(transport, url, digest):
            buf.append(ln)
            if len(buf) >= 500:
                for rec in parse(buf, stats, as_of=as_of, expect_header=first_chunk):
                    yield "record", rec
                buf, first_chunk = [], False
        for rec in parse(buf, stats, as_of=as_of, expect_header=first_chunk):
            yield "record", rec
    stats.sha256 = digest.hexdigest()
    yield "done", stats
