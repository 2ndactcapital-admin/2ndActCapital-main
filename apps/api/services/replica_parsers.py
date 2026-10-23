"""Holdings-file parsers for Replication Studio (rep1).

Parsers are CODE; which source a fund uses is DATA
(``portfolio.replica_holdings_sources.parser`` names one of ``PARSERS``).
Every parser takes the raw bytes exactly as published and returns a
:class:`ParsedHoldings` — the as-of date the FILE states (never the fetch
date) and one :class:`HoldingRow` per published line, identifiers kept
verbatim. Nothing here touches the database or the network.

WHAT WAS CONFIRMED LIVE (rep1 Task 1b, 2026-10-22), AND WHAT WAS NOT
──────────────────────────────────────────────────────────────────────────────
* ``ssga_xlsx`` — CONFIRMED against SPY's real daily file: one sheet
  ("holdings"); "Holdings: | As of 21-Oct-2026" in the preamble; header row
  Name, Ticker, Identifier (the CUSIP), SEDOL, Weight (PERCENT), Sector,
  Shares Held, Local Currency; a blank row then a disclaimer after the data;
  cash appears as "US DOLLAR" with ticker "-" and identifier 999USDZ92.
* ``invesco_csv`` / ``ishares_csv`` — NOT confirmed: Invesco answered 406 and
  iShares answered an HTML product page to a non-browser client. Both follow
  the issuers' documented layouts and are tested only on fixtures shaped from
  those layouts; their sources are seeded INACTIVE.
* ``sec_nport`` — the N-PORT-P XML schema (``invstOrSec`` elements under
  ``invstOrSecs``; ``genInfo/repPdDate``; ``pctVal`` in PERCENT). Public N-PORT
  lags 2-5 months; it is the fallback for any fund without an issuer parser.

WEIGHTS are stored as a DECIMAL FRACTION (0.08 for 8%). Every source above
publishes percent, so every parser divides by 100 — in one place, ``_pct``.
Floats never touch a weight: openpyxl hands back a float for a numeric cell,
so it is converted through ``repr`` (shortest round-trip string) to Decimal.
"""
from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

ASSET_TYPES = ("equity", "cash", "future", "other")


class HoldingsParseError(ValueError):
    """The bytes are not the format this parser reads. Names what was missing."""


@dataclass
class HoldingRow:
    row_num: int
    name: str | None
    ticker: str | None
    cusip: str | None
    isin: str | None
    sedol: str | None
    weight: Decimal | None          # fraction
    shares: Decimal | None
    market_value: Decimal | None
    currency: str | None
    asset_type: str
    raw: dict = field(default_factory=dict)

    def identity(self) -> str | None:
        """The key duplicate detection uses: the strongest identifier present."""
        for v in (self.cusip, self.isin, self.sedol, self.ticker):
            if v:
                return v.upper()
        return None


@dataclass
class ParsedHoldings:
    as_of: date
    rows: list[HoldingRow]
    fund_ticker: str | None = None
    fund_name: str | None = None
    meta: dict = field(default_factory=dict)


# ── value helpers ────────────────────────────────────────────────────────────
_BLANKS = {"", "-", "--", "n/a", "na", "none", "null"}


def _clean(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in _BLANKS else s


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(repr(v))     # shortest round-trip, never Decimal(float)
    s = str(v).strip().replace(",", "").replace("$", "").replace("%", "")
    if s.lower() in _BLANKS:
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _pct(v) -> Decimal | None:
    d = _dec(v)
    return None if d is None else d / Decimal(100)


_CUSIP_RE = re.compile(r"^[0-9A-Z]{9}$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{9}[0-9]$")


def _cusip(v) -> str | None:
    s = _clean(v)
    if not s:
        return None
    s = s.upper()
    return s if _CUSIP_RE.match(s) else None


def _isin(v) -> str | None:
    s = _clean(v)
    if not s:
        return None
    s = s.upper()
    return s if _ISIN_RE.match(s) else None


_CASH_NAMES = re.compile(r"\b(US DOLLAR|U\.S\. DOLLAR|CASH|CURRENCY|MONEY MARKET|RECEIVABLE|PAYABLE)\b", re.I)
_FUTURE_NAMES = re.compile(r"\b(FUTURE|FUTURES|FUT)\b", re.I)


def classify(name: str | None, *, hint: str | None = None, cusip: str | None = None) -> str:
    """Asset type from the issuer's own asset-class column when there is one,
    else from the row's name. Cash includes currency and money-market sweep
    lines; anything else that is not a stock is 'other' — never silently
    'equity'."""
    h = (hint or "").strip().lower()
    if h:
        if h in ("equity", "common stock", "stock", "ec", "ep"):
            return "equity"
        if "cash" in h or "money market" in h or h in ("stiv", "currency"):
            return "cash"
        if "future" in h:
            return "future"
        return "other"
    n = name or ""
    if (cusip or "").startswith("999USD") or _CASH_NAMES.search(n):
        return "cash"
    if _FUTURE_NAMES.search(n):
        return "future"
    return "equity"


# ── State Street (SPDR) daily xlsx — CONFIRMED live ──────────────────────────
def parse_ssga_xlsx(data: bytes) -> ParsedHoldings:
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 — any reader failure is "not this format"
        raise HoldingsParseError(f"not an xlsx workbook: {type(exc).__name__}") from exc
    ws = wb[wb.sheetnames[0]]
    rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
    wb.close()

    as_of = fund_ticker = fund_name = None
    header_idx = None
    for i, r in enumerate(rows[:30]):
        first = _clean(r[0]) if r else None
        if first == "Fund Name:":
            fund_name = _clean(r[1])
        elif first == "Ticker Symbol:":
            fund_ticker = _clean(r[1])
        elif first == "Holdings:":
            m = re.search(r"As of\s+(\d{1,2}-[A-Za-z]{3}-\d{4})", str(r[1] or ""))
            if m:
                as_of = datetime.strptime(m.group(1), "%d-%b-%Y").date()
        elif first == "Name" and r[1] == "Ticker":
            header_idx = i
            break
    if header_idx is None:
        raise HoldingsParseError("ssga_xlsx: no header row starting 'Name', 'Ticker'")
    if as_of is None:
        raise HoldingsParseError("ssga_xlsx: no 'Holdings: As of DD-Mon-YYYY' line")
    header = [(_clean(h) or "") for h in rows[header_idx]]
    col = {h: j for j, h in enumerate(header) if h}
    for need in ("Name", "Ticker", "Identifier", "Weight", "Shares Held"):
        if need not in col:
            raise HoldingsParseError(f"ssga_xlsx: header has no '{need}' column")

    out: list[HoldingRow] = []
    for r in rows[header_idx + 1:]:
        name = _clean(r[col["Name"]]) if r else None
        if not name:
            break                                  # the blank row before the disclaimer
        raw = {h: (None if r[j] is None else str(r[j])) for h, j in col.items() if j < len(r)}
        cusip = _cusip(r[col["Identifier"]])
        ticker = _clean(r[col["Ticker"]])
        out.append(HoldingRow(
            row_num=len(out) + 1, name=name, ticker=ticker, cusip=cusip, isin=None,
            sedol=_clean(r[col["SEDOL"]]) if "SEDOL" in col else None,
            weight=_pct(r[col["Weight"]]), shares=_dec(r[col["Shares Held"]]), market_value=None,
            currency=_clean(r[col["Local Currency"]]) if "Local Currency" in col else None,
            asset_type=classify(name, cusip=_clean(r[col["Identifier"]])), raw=raw))
    if not out:
        raise HoldingsParseError("ssga_xlsx: header found but no holdings rows")
    return ParsedHoldings(as_of=as_of, rows=out, fund_ticker=fund_ticker, fund_name=fund_name,
                          meta={"parser": "ssga_xlsx"})


# ── Invesco CSV — documented layout, NOT confirmed live ─────────────────────
def _read_csv(data: bytes) -> list[list[str]]:
    text = data.decode("utf-8-sig", errors="replace")
    if text.lstrip().lower().startswith(("<!doctype", "<html")):
        raise HoldingsParseError("the server returned an HTML page, not a CSV file")
    return list(csv.reader(io.StringIO(text)))


def _parse_date_any(s: str | None) -> date | None:
    s = (s or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%b %d, %Y", "%d-%b-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_invesco_csv(data: bytes) -> ParsedHoldings:
    rows = _read_csv(data)
    if not rows:
        raise HoldingsParseError("invesco_csv: empty file")
    header = [h.strip() for h in rows[0]]
    col = {h: j for j, h in enumerate(header)}
    for need in ("Fund Ticker", "Security Identifier", "Holding Ticker", "Shares/Par Value", "Weight", "Name", "Date"):
        if need not in col:
            raise HoldingsParseError(f"invesco_csv: header has no '{need}' column")
    out: list[HoldingRow] = []
    dates: set[date] = set()
    fund_ticker = None
    for r in rows[1:]:
        if not r or not any(c.strip() for c in r):
            continue
        get = lambda k: r[col[k]] if col.get(k) is not None and col[k] < len(r) else None  # noqa: E731
        name = _clean(get("Name"))
        if not name:
            continue
        d = _parse_date_any(get("Date"))
        if d:
            dates.add(d)
        fund_ticker = fund_ticker or _clean(get("Fund Ticker"))
        ident = _clean(get("Security Identifier"))
        out.append(HoldingRow(
            row_num=len(out) + 1, name=name, ticker=_clean(get("Holding Ticker")), cusip=_cusip(ident),
            isin=_isin(ident), sedol=None, weight=_pct(get("Weight")), shares=_dec(get("Shares/Par Value")),
            market_value=_dec(get("MarketValue")), currency="USD",
            asset_type=classify(name, hint=get("Class of Shares") if _clean(get("Class of Shares")) in
                                ("Cash", "Currency", "Futures", "Money Market") else None),
            raw={h: (r[j] if j < len(r) else None) for h, j in col.items()}))
    if not out:
        raise HoldingsParseError("invesco_csv: no holdings rows")
    if len(dates) != 1:
        raise HoldingsParseError(f"invesco_csv: expected one as-of date, found {sorted(dates)}")
    return ParsedHoldings(as_of=dates.pop(), rows=out, fund_ticker=fund_ticker, meta={"parser": "invesco_csv"})


# ── iShares CSV — documented layout, NOT confirmed live ─────────────────────
def parse_ishares_csv(data: bytes) -> ParsedHoldings:
    rows = _read_csv(data)
    as_of = None
    fund_name = rows[0][0].strip() if rows and rows[0] else None
    header_idx = None
    for i, r in enumerate(rows[:20]):
        if r and r[0].strip() == "Fund Holdings as of" and len(r) > 1:
            as_of = _parse_date_any(r[1])
        if r and r[0].strip() == "Ticker" and len(r) > 1 and r[1].strip() == "Name":
            header_idx = i
            break
    if header_idx is None:
        raise HoldingsParseError("ishares_csv: no header row starting 'Ticker', 'Name'")
    if as_of is None:
        raise HoldingsParseError("ishares_csv: no 'Fund Holdings as of' line")
    header = [h.strip() for h in rows[header_idx]]
    col = {h: j for j, h in enumerate(header)}
    wcol = "Weight (%)" if "Weight (%)" in col else None
    qcol = "Quantity" if "Quantity" in col else ("Shares" if "Shares" in col else None)
    if not wcol or not qcol:
        raise HoldingsParseError("ishares_csv: header has no 'Weight (%)' or 'Quantity'/'Shares' column")
    out: list[HoldingRow] = []
    for r in rows[header_idx + 1:]:
        if not r or not r[0].strip() or len(r) < len(header) // 2:
            break                                   # the blank line before the disclaimer
        get = lambda k: r[col[k]] if col.get(k) is not None and col[k] < len(r) else None  # noqa: E731
        name = _clean(get("Name"))
        out.append(HoldingRow(
            row_num=len(out) + 1, name=name, ticker=_clean(get("Ticker")), cusip=_cusip(get("CUSIP")),
            isin=_isin(get("ISIN")), sedol=_clean(get("SEDOL")), weight=_pct(get(wcol)), shares=_dec(get(qcol)),
            market_value=_dec(get("Market Value")), currency=_clean(get("Currency")),
            asset_type=classify(name, hint=get("Asset Class")),
            raw={h: (r[j] if j < len(r) else None) for h, j in col.items()}))
    if not out:
        raise HoldingsParseError("ishares_csv: no holdings rows")
    return ParsedHoldings(as_of=as_of, rows=out, fund_name=fund_name, meta={"parser": "ishares_csv"})


# ── SEC Form N-PORT-P XML — the fallback ────────────────────────────────────
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text(el, name) -> str | None:
    c = _child(el, name)
    return _clean(c.text) if c is not None and c.text else None


def parse_nport_xml(data: bytes) -> ParsedHoldings:
    if b"<!DOCTYPE" in data[:2000] or b"<!ENTITY" in data[:5000]:
        raise HoldingsParseError("sec_nport: DTD / entity declarations are refused")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise HoldingsParseError(f"sec_nport: not XML ({exc})") from exc
    gen = next((e for e in root.iter() if _local(e.tag) == "genInfo"), None)
    if gen is None:
        raise HoldingsParseError("sec_nport: no genInfo element")
    as_of = _parse_date_any(_text(gen, "repPdDate"))
    if as_of is None:
        raise HoldingsParseError("sec_nport: no genInfo/repPdDate")
    series_id = _text(gen, "seriesId")
    out: list[HoldingRow] = []
    for inv in (e for e in root.iter() if _local(e.tag) == "invstOrSec"):
        idents = _child(inv, "identifiers")
        isin = ticker = None
        if idents is not None:
            for c in idents:
                if _local(c.tag) == "isin":
                    isin = _isin(c.get("value"))
                elif _local(c.tag) == "ticker":
                    ticker = _clean(c.get("value"))
        cat = _text(inv, "assetCat")
        is_deriv = _child(inv, "derivativeInfo") is not None
        lending = _child(inv, "securityLending")
        collateral = lending is not None and (_text(lending, "isCashCollateral") or "").upper() == "Y"
        if collateral or cat == "STIV":
            atype = "cash"
        elif is_deriv:
            deriv = _child(inv, "derivativeInfo")
            atype = "future" if deriv is not None and _child(deriv, "futrDeriv") is not None else "other"
        elif cat in ("EC", "EP"):
            atype = "equity"
        else:
            atype = "other"
        name = _text(inv, "name") or _text(inv, "title")
        out.append(HoldingRow(
            row_num=len(out) + 1, name=name, ticker=ticker, cusip=_cusip(_text(inv, "cusip")), isin=isin,
            sedol=None, weight=_pct(_text(inv, "pctVal")), shares=_dec(_text(inv, "balance")),
            market_value=_dec(_text(inv, "valUSD")), currency=_text(inv, "curCd"), asset_type=atype,
            raw={"name": name, "title": _text(inv, "title"), "cusip": _text(inv, "cusip"), "lei": _text(inv, "lei"),
                 "assetCat": cat, "units": _text(inv, "units"), "pctVal": _text(inv, "pctVal")}))
    if not out:
        raise HoldingsParseError("sec_nport: no invstOrSec rows")
    return ParsedHoldings(as_of=as_of, rows=out, meta={"parser": "sec_nport", "series_id": series_id})


PARSERS = {
    "ssga_xlsx": parse_ssga_xlsx,
    "invesco_csv": parse_invesco_csv,
    "ishares_csv": parse_ishares_csv,
    "sec_nport": parse_nport_xml,
}


def parse(parser: str, data: bytes) -> ParsedHoldings:
    fn = PARSERS.get(parser)
    if fn is None:
        raise HoldingsParseError(f"no parser named {parser!r} (known: {sorted(PARSERS)})")
    return fn(data)
