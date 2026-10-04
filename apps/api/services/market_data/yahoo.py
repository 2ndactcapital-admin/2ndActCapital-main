"""Yahoo Finance adapter — daily closes for the six Yahoo registry series
(^RUT, EFA, EEM, ACWX, GC=F, DX-Y.NYB).

LICENSING — a recorded assumption, NOT a resolved question
──────────────────────────────────────────────────────────────────────────────
Yahoo's chart endpoint is unofficial and, as far as we know, licensed for
personal use only. The owner enabled these series assuming personal/internal
use while there are no paid customers. docs/MARKET_DATA_DESIGN_V1.md lists
this as a LAUNCH BLOCKER: before any external customer sees the platform,
these series are replaced with a licensed source or removed.

DATA RULES
──────────────────────────────────────────────────────────────────────────────
  * JSON is parsed with ``parse_float=Decimal``: no float ever exists.
  * Every value is quantized to 4 dp with ROUND_HALF_EVEN before it is diffed
    or stored. Yahoo's numbers carry binary noise (2180.969970703125) that can
    differ between calls; without this every night would "revise" history.
  * RAW close, never adjclose: adjusted close restates the whole history at
    every dividend, which would revise every ETF row each quarter.
  * Dates are exchange-local, from the payload's ``meta.gmtoffset``.
  * A bar dated today-or-later (exchange-local) is an incomplete intraday bar
    (futures trade almost around the clock) and is dropped.
  * A null close is skipped and counted. A repeated date keeps the LAST bar.
  * Units (mkt02c): an instrument whose ``meta.instrumentType`` is 'INDEX'
    is stored as 'index points' — an index level is not an amount of money.
    Every other instrument type, or a missing one, keeps the currency code.

ERROR CLASSES
──────────────────────────────────────────────────────────────────────────────
  * :class:`YahooNotFound` — Yahoo's explicit "Not Found" chart error for the
    symbol. The ONLY error that may mark a series ``invalid_code``.
  * :class:`YahooTransientError` — 401 / 403 / 429 / 5xx / timeout /
    unparseable body / unexpected granularity. Never marks anything invalid.
    401 and 403 are here on purpose: Yahoo answers a blocked IP (Render's
    datacenter ranges) with them, and that says nothing about the symbol.
  * :class:`YahooRequestError` — any other refusal. Status stays unchanged.

No secret is involved (no key), but the transport still keeps only an
exception's type name, the same discipline as fred.py.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import quote

import httpx

from services.market_data.adapters import FetchResult, ProviderError, ValidateResult
from services.market_data.fred import Throttle, scrub

YAHOO_BASE_URL = "https://query1.finance.yahoo.com"
CHART_PATH_PREFIX = "/v8/finance/chart/"
# A realistic browser User-Agent: Yahoo refuses obvious script clients.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
DEFAULT_MIN_INTERVAL = 1.0  # about one request per second
MAX_RETRIES = 3             # retries AFTER the first attempt
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
TRANSIENT_STATUSES = frozenset({401, 403}) | RETRY_STATUSES
REQUEST_TIMEOUT = 30.0
QUANTUM = Decimal("0.0001")
# "Maximum range" is requested as period1=0..now rather than range=max:
# Yahoo can silently coarsen range=max to a weekly or monthly granularity,
# and the parser refuses anything that is not daily (see EXPECTED_GRANULARITY).
FETCH_PARAMS_BASE = {"interval": "1d", "period1": "0", "events": "history"}
VALIDATE_PARAMS = {"interval": "1d", "range": "5d"}
EXPECTED_GRANULARITY = "1d"
INDEX_INSTRUMENT_TYPE = "INDEX"
INDEX_UNITS = "index points"


def chart_path(symbol: str) -> str:
    """'^RUT' → '/v8/finance/chart/%5ERUT'; 'GC=F' → '.../GC%3DF'."""
    return CHART_PATH_PREFIX + quote(symbol, safe="")


# ── Errors ──────────────────────────────────────────────────────────────────
class YahooError(ProviderError):
    """Base class. Messages are safe to store."""


class YahooNotFound(YahooError):
    """Yahoo explicitly says the symbol does not exist."""


class YahooTransientError(YahooError):
    """Blocked, throttled, down, timed out, or an unusable body."""


class YahooRequestError(YahooError):
    """Any other refusal."""


class TransportError(Exception):
    """Network failure; message is the exception TYPE NAME only."""


# ── Transport ───────────────────────────────────────────────────────────────
@dataclass
class YahooResponse:
    status: int
    body: str  # raw text; parsed by the client with parse_float=Decimal
    retry_after: float | None = None


class YahooTransport(Protocol):
    async def get(self, path: str, params: dict[str, str]) -> YahooResponse: ...


class HttpxTransport:
    def __init__(self, client: httpx.AsyncClient | None = None, base_url: str = YAHOO_BASE_URL):
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        self._base_url = base_url
        self._headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    async def get(self, path: str, params: dict[str, str]) -> YahooResponse:
        try:
            resp = await self._client.get(self._base_url + path, params=params, headers=self._headers)
        except httpx.HTTPError as exc:
            raise TransportError(type(exc).__name__) from None
        retry_after = None
        header = resp.headers.get("Retry-After")
        if header:
            try:
                retry_after = float(header)
            except ValueError:
                retry_after = None
        return YahooResponse(resp.status_code, resp.text, retry_after)

    async def aclose(self) -> None:
        await self._client.aclose()


# ── Parsing ─────────────────────────────────────────────────────────────────
def _no_constants(name: str):
    # NaN / Infinity: treated as a missing close, never as a number.
    return None


def loads(text: str) -> Any:
    """JSON → Python with every non-integer number as Decimal. No float."""
    return json.loads(text, parse_float=Decimal, parse_constant=_no_constants)


def normalize(value: Any) -> Decimal:
    """A Yahoo number → Decimal quantized to 4 dp, ROUND_HALF_EVEN.

    Refuses float outright (one should never reach here) and bool.
    """
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"refusing {type(value).__name__} value")
    if isinstance(value, int):
        value = Decimal(value)
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError("non-finite value")
    return value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)


@dataclass
class ParsedChart:
    meta: dict = field(default_factory=dict)
    points: list[tuple[date, Decimal]] = field(default_factory=list)
    skipped_null: int = 0
    dropped_incomplete: int = 0
    duplicates_replaced: int = 0
    rejected: list[str] = field(default_factory=list)


def _chart_result(payload: Any, symbol: str) -> dict:
    """The single chart result, or the classified error Yahoo sent."""
    if not isinstance(payload, dict) or not isinstance(payload.get("chart"), dict):
        raise YahooTransientError(f"Yahoo returned an unrecognised body for {symbol}")
    chart = payload["chart"]
    error = chart.get("error")
    if error:
        code = str(error.get("code") or "") if isinstance(error, dict) else ""
        desc = str(error.get("description") or "") if isinstance(error, dict) else str(error)
        if code.strip().lower() == "not found":
            raise YahooNotFound(scrub(f"Yahoo says symbol {symbol} was not found: {desc or code}"))
        raise YahooRequestError(scrub(f"Yahoo refused {symbol}: {code or 'error'}: {desc}"))
    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise YahooTransientError(f"Yahoo returned no chart result for {symbol}")
    return results[0]


def parse_chart(payload: Any, symbol: str, *, now: datetime | None = None) -> ParsedChart:
    """Payload → daily raw closes, exchange-local dates, today's bar dropped."""
    result = _chart_result(payload, symbol)
    meta = result.get("meta") or {}
    out = ParsedChart(meta=meta)

    granularity = meta.get("dataGranularity")
    if granularity is not None and granularity != EXPECTED_GRANULARITY:
        raise YahooTransientError(
            f"Yahoo returned {granularity!r} bars for {symbol}, expected daily — not written")
    try:
        offset = int(meta.get("gmtoffset") or 0)
    except (TypeError, ValueError):
        raise YahooTransientError(f"Yahoo returned an unusable gmtoffset for {symbol}") from None
    tz = timezone(timedelta(seconds=offset))
    today_local = (now or datetime.now(timezone.utc)).astimezone(tz).date()

    timestamps = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])
    closes = (quotes[0] or {}).get("close") if quotes else None
    if closes is None:
        closes = []
    if len(closes) != len(timestamps):
        raise YahooTransientError(
            f"Yahoo returned {len(timestamps)} timestamps but {len(closes)} closes for {symbol}")

    by_date: dict[date, Decimal] = {}
    for ts, close in zip(timestamps, closes):
        if isinstance(ts, bool) or not isinstance(ts, int):
            out.rejected.append(f"bad timestamp {str(ts)[:20]!r}")
            continue
        obs_date = datetime.fromtimestamp(ts, tz).date()
        if obs_date >= today_local:
            out.dropped_incomplete += 1
            continue
        if close is None:
            out.skipped_null += 1
            continue
        try:
            value = normalize(close)
        except (ValueError, InvalidOperation) as exc:
            out.rejected.append(f"{obs_date}: {exc}")
            continue
        if obs_date in by_date:
            out.duplicates_replaced += 1
        by_date[obs_date] = value  # last bar for a date wins
    out.points = sorted(by_date.items())
    return out


# ── Client / adapter ────────────────────────────────────────────────────────
class YahooAdapter:
    provider = "yahoo"

    def __init__(self, transport: YahooTransport | None = None, *,
                 min_interval: float = DEFAULT_MIN_INTERVAL,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 max_retries: int = MAX_RETRIES,
                 now: Callable[[], datetime] | None = None):
        self._transport = transport or HttpxTransport()
        self._throttle = Throttle(min_interval, sleep)
        self._sleep = sleep
        self._max_retries = max_retries
        self._now = now or (lambda: datetime.now(timezone.utc))

    def scrub(self, text: Any) -> str:
        return scrub(text)

    async def aclose(self) -> None:
        close = getattr(self._transport, "aclose", None)
        if close is not None:
            await close()

    async def _request(self, symbol: str, query: dict[str, str]) -> Any:
        path = chart_path(symbol)
        last_problem = "no attempt made"
        for attempt in range(self._max_retries + 1):
            await self._throttle.wait()
            try:
                resp = await self._transport.get(path, dict(query))
            except TransportError as exc:
                last_problem = f"network error ({exc})"
                if attempt < self._max_retries:
                    await self._sleep(2 ** attempt)
                continue
            except Exception as exc:  # noqa: BLE001 — a fake/foreign transport
                last_problem = f"transport failure ({type(exc).__name__})"
                if attempt < self._max_retries:
                    await self._sleep(2 ** attempt)
                continue

            if resp.status in RETRY_STATUSES:
                last_problem = f"HTTP {resp.status}"
                if attempt < self._max_retries:
                    delay = float(2 ** attempt)
                    if resp.retry_after is not None:
                        delay = max(delay, resp.retry_after)
                    await self._sleep(delay)
                continue

            try:
                payload = loads(resp.body or "")
            except ValueError:
                payload = None

            if resp.status in TRANSIENT_STATUSES:
                raise YahooTransientError(
                    f"Yahoo refused {symbol} with HTTP {resp.status} "
                    "(blocked or throttled; says nothing about the symbol)")
            if resp.status >= 400:
                # Only Yahoo's explicit "Not Found" chart error is not-found;
                # a bare 404 (HTML, proxy page) is not proof.
                if isinstance(payload, dict):
                    _chart_result(payload, symbol)
                raise YahooRequestError(f"Yahoo refused {symbol} with HTTP {resp.status}")
            if payload is None:
                raise YahooTransientError(f"Yahoo returned an unparseable body for {symbol} "
                                          f"(HTTP {resp.status})")
            return payload

        raise YahooTransientError(
            f"Yahoo request for {symbol} failed after {self._max_retries + 1} attempts: {last_problem}")

    async def fetch_series(self, series_row) -> FetchResult:
        symbol = series_row["source_code"]
        query = {**FETCH_PARAMS_BASE, "period2": str(int(time.time()) + 86400)}
        payload = await self._request(symbol, query)
        parsed = parse_chart(payload, symbol, now=self._now())
        return FetchResult(parsed.points, parsed.skipped_null, parsed.rejected, notes={
            "dropped_incomplete": parsed.dropped_incomplete,
            "duplicates_replaced": parsed.duplicates_replaced,
        })

    async def validate_series(self, series_row) -> ValidateResult:
        symbol = series_row["source_code"]
        try:
            payload = await self._request(symbol, dict(VALIDATE_PARAMS))
            result = _chart_result(payload, symbol)
        except YahooNotFound as exc:
            return ValidateResult("invalid_code", error=scrub(exc))
        except YahooError as exc:
            return ValidateResult("error", error=scrub(exc))
        meta = result.get("meta") or {}
        currency = meta.get("currency")
        if not isinstance(currency, str) or not currency.strip():
            return ValidateResult("error", error=f"Yahoo returned no currency for {symbol}")
        finds = []
        tz_name = meta.get("exchangeTimezoneName")
        if tz_name:
            finds.append(f"{series_row['series_key']}: exchange timezone {tz_name}, currency {currency}")
        return ValidateResult("active", fields={"units": units_for(meta)}, finds=finds)


def units_for(meta: dict) -> str:
    """'index points' for an INDEX instrument, else the currency code.
    Callers have already refused a missing currency."""
    instrument = meta.get("instrumentType")
    if isinstance(instrument, str) and instrument.strip().upper() == INDEX_INSTRUMENT_TYPE:
        return INDEX_UNITS
    return str(meta.get("currency")).strip()
