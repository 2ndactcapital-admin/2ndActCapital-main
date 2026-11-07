"""sec_mutual_funds — one security per registered-fund SHARE CLASS: sec.gov/files/company_tickers_mf.json.

FORMAT (fetched 2026-11-07 through services.edgar_fetch, 1,237,746 bytes, Last-Modified 2026-11-04):
    {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[2110, "S000009184", "C000024954", "LACAX"], …]}
28,647 rows = 28,647 distinct share classes (classId unique; symbol unique, one row blank), 12,304 series, 1,160
CIKs. Symbol lengths: 22,811 five-letter, 4,845 four-letter, 818 three-letter … 23,194 end in X.

WHAT IT IS NOT. The file lists every share class of an open-end registrant that has a ticker — that includes the
ETF share classes of open-end funds (VTI, VOO, BND, IVV and, since its 2025 conversion, QQQ). It carries NO NAMES
and NO type. So:

  * a ticker the master already holds is MATCHED (ticker on the US composite family — a fund class is quoted, not
    exchange-listed, and 'US' matches every US exchange family but OTC), never duplicated: the class / series ids and
    the CIK are added to it; a ticker held on OTC or by an index is a ``ticker_conflict`` review item;
  * a NEW security is created only for a MUTUAL-FUND ticker — five letters ending in X (Nasdaq's Mutual Fund
    Quotation Service convention; "XX" = a money market fund). Anything else absent from the master is unmapped with
    its reason (an exchange-traded class is created by the listing sources, not here);
  * security_type 'fund' (the platform's ONE pooled-fund type: X-ray, options and the replica builder all read it;
    the CHECK admits no 'mutual_fund'), with the structure as the reference attribute ``fund_structure``:
    'money_market' (XX ticker, or a name that says money market) or 'mutual_fund';
  * the name of a NEW security states only SEC facts — "FXAIX (SEC share class C000100045)". FinanceDatabase has
    fund names but its licence is internal-only (lic_member_display not_allowed), and the SEC's series / class
    name files are annual deltas (≈700 KB each), not a complete list. A member never sees this name: their asset
    keeps its own. Names are a follow-up (docs/PROJECT_STATUS.md, SECMASTER2).

Identifiers: ticker, sec_series_id, sec_class_id (unique per live row); attributes: cik (10 digits, zero-padded),
fund_structure, exchange 'US' (on a NEW security only — so a later run can match it again). One request, through
services.edgar_fetch (the declared User-Agent and its 10/s limiter)."""

from __future__ import annotations

import json
import re
from typing import Iterator

from services import security_master_symbols as SYM
from services.security_data.providers.reference_common import (Digest, ParseStats, ReferenceProvider, SourceFormatChanged,
                                                              SourceHTTPError, SourceRecord, SourceTransport, read_text)

KEY = "sec_mutual_funds"
URL = "https://www.sec.gov/files/company_tickers_mf.json"
FILE_KEY = "company_tickers_mf"
FIELDS = ("cik", "seriesid", "classid", "symbol")
EXCHANGE = "US"       # the US composite family (security_master_symbols.FAMILY) — quoted, not listed
_SERIES = re.compile(r"^S\d{9}$")
_CLASS = re.compile(r"^C\d{9}$")
_MF_TICKER = re.compile(r"^[A-Z]{4}X$")
_MM_NAME = re.compile(r"\bmoney market\b|\bcash reserves\b|\bgov(ernmen)?t (cash|obligations|money)\b", re.I)


class SecMutualFundsProvider(ReferenceProvider):
    def __init__(self) -> None:
        super().__init__(KEY)


def is_mutual_fund_ticker(ticker: str | None) -> bool:
    return bool(ticker and _MF_TICKER.match(ticker))


def structure(ticker: str | None, name: str | None = None) -> str | None:
    """'money_market' / 'mutual_fund' for a mutual-fund ticker, else None (an ETF class: the listing sources say)."""
    if not is_mutual_fund_ticker(ticker):
        return None
    if ticker.endswith("XX") or (name and _MM_NAME.search(name)):
        return "money_market"
    return "mutual_fund"


def placeholder_name(ticker: str, class_id: str) -> str:
    return f"{ticker} (SEC share class {class_id})"


def parse(doc: dict, stats: ParseStats, *, as_of=None, names: dict | None = None) -> Iterator[SourceRecord]:
    """One SourceRecord per share class. ``names`` (optional, ticker → name) only ever sets a NEW security's name."""
    fields = [str(f).lower() for f in (doc.get("fields") or [])]
    try:
        idx = [fields.index(k) for k in FIELDS]
    except ValueError:
        raise SourceFormatChanged(f"company_tickers_mf.json changed its format: fields {fields!r}, expected cik, "
                                  "seriesId, classId, symbol — the run is held until the provider is updated") from None
    i_cik, i_ser, i_cls, i_sym = idx
    for row in doc.get("data") or []:
        stats.rows += 1
        try:
            cik, series, klass, sym = row[i_cik], row[i_ser], row[i_cls], row[i_sym]
        except (IndexError, TypeError):
            stats.exclude("malformed row")
            continue
        ticker = SYM.normalize_symbol(sym) if sym else None
        series = (series or "").strip().upper()
        klass = (klass or "").strip().upper()
        if not ticker:
            stats.exclude("no ticker")
            continue
        if not _CLASS.match(klass) or not _SERIES.match(series):
            stats.exclude("series / class id not in the SEC's S000000000 / C000000000 form")
            continue
        try:
            cik10 = str(int(cik)).zfill(10)
        except (TypeError, ValueError):
            stats.exclude("no CIK")
            continue
        nm = (names or {}).get(ticker)
        struct = structure(ticker, nm)
        attrs = {"cik": cik10}
        if struct:
            attrs["fund_structure"] = struct
        stats.kept += 1
        yield SourceRecord(KEY, ticker=ticker, name=nm or placeholder_name(ticker, klass), exchange=EXCHANGE,
                           security_type="fund", raw_symbol=sym, attrs=attrs, as_of=as_of,
                           extra_ids={"sec_class_id": klass, "sec_series_id": series},
                           note=None if struct else "not a mutual-fund ticker — matched only, never created")


async def fetch(transport: SourceTransport, *, as_of=None, budget=None,
                names: dict | None = None) -> tuple[list[SourceRecord], ParseStats]:
    if budget is not None:
        await budget.take()
    digest = Digest()
    try:
        text = await read_text(transport, URL, digest)
    except SourceHTTPError as exc:
        if exc.status == 404:
            raise SourceFormatChanged(f"the SEC no longer publishes {URL} (HTTP 404) — the run is held") from None
        raise
    try:
        doc = json.loads(text)
    except ValueError:
        raise SourceFormatChanged(f"{URL} is not JSON any more ({text[:80]!r}) — the run is held") from None
    stats = ParseStats(sha256=digest.hexdigest())
    recs = list(parse(doc, stats, as_of=as_of, names=names))
    if not recs:
        raise SourceFormatChanged(f"{URL} parsed to zero share classes — not recorded as a load")
    return recs, stats
