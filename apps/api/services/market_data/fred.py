"""FRED client — series metadata and observation history.

SECRET HANDLING — non-negotiable
──────────────────────────────────────────────────────────────────────────────
FRED takes the API key as a QUERY-STRING parameter, so every request URL
contains it. Therefore nothing in this module prints, logs, stores or raises
a URL, a params dict, or an upstream exception's own text:

  * the transport converts every httpx failure into a ``TransportError`` that
    carries the exception's TYPE NAME only — httpx's own messages embed the
    full request URL;
  * every message that leaves this module goes through :func:`scrub`, which
    removes the literal key and any ``api_key=…`` fragment, belt and braces,
    in case FRED ever echoes the request back in an error body.

TRANSPORT
──────────────────────────────────────────────────────────────────────────────
The client takes its transport as a dependency: anything with
``async get(path, params) -> FredResponse`` that raises ``TransportError`` on
a network failure or timeout. :class:`HttpxTransport` is the real one; the
verify script injects a fake, so the whole adapter is provable with no
network.

ERROR CLASSES — the distinction that matters
──────────────────────────────────────────────────────────────────────────────
  * :class:`FredNotFound`     — FRED itself says the series does not exist.
    The ONLY error that may mark a registry row ``invalid_code``.
  * :class:`FredTransientError` — 429 / 5xx / timeout after retries. Never
    marks anything invalid.
  * :class:`FredRequestError` — any other 4xx. FRED answers a bad API key
    with HTTP 400 as well, so "400" alone is NOT "series does not exist";
    treating it that way would mark all 57 FRED rows invalid on a key typo.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

import httpx

FRED_BASE_URL = "https://api.stlouisfed.org"
SERIES_PATH = "/fred/series"
OBSERVATIONS_PATH = "/fred/series/observations"

# FRED's documented cap is ~120 requests/minute per key. 0.6s spacing is
# 100/minute, leaving headroom for a concurrent manual call on the same key.
DEFAULT_MIN_INTERVAL = 0.6
MAX_RETRIES = 3  # retries AFTER the first attempt, so at most 4 requests
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
REQUEST_TIMEOUT = 60.0
# FRED's maximum page size for /fred/series/observations.
OBSERVATION_PAGE_LIMIT = 100_000
MAX_ERROR_CHARS = 500

# FRED frequency_short → registry frequency. Anything else (A, BW, …) is
# 'irregular' — the registry CHECK has no annual value.
FREQUENCY_MAP = {
    "D": "daily",
    "W": "weekly",
    "M": "monthly",
    "Q": "quarterly",
    "SA": "semiannual",
}

_NOT_FOUND_RE = re.compile(r"series\b.*\b(does not|doesn't) exist", re.IGNORECASE)
_API_KEY_FRAGMENT_RE = re.compile(r"(api_key=)[^&\s'\"]*", re.IGNORECASE)


def map_frequency(frequency_short: str | None) -> str:
    """FRED ``frequency_short`` → the registry's frequency vocabulary."""
    return FREQUENCY_MAP.get((frequency_short or "").strip().upper(), "irregular")


def scrub(text: Any, api_key: str | None = None) -> str:
    """Remove the API key from ``text`` and bound its length.

    Removes the literal key wherever it appears AND any ``api_key=<value>``
    fragment, so a key FRED echoes back (or one the caller never told us
    about) still never reaches stdout or a database column.
    """
    out = str(text)
    if api_key:
        out = out.replace(api_key, "***")
    out = _API_KEY_FRAGMENT_RE.sub(r"\1***", out)
    if len(out) > MAX_ERROR_CHARS:
        out = out[: MAX_ERROR_CHARS - 3] + "..."
    return out


# ── Errors ──────────────────────────────────────────────────────────────────
class FredError(Exception):
    """Base class. Every message is already scrubbed when constructed."""


class FredNotFound(FredError):
    """FRED says the series does not exist."""


class FredTransientError(FredError):
    """429 / 5xx / timeout that persisted through every retry."""


class FredRequestError(FredError):
    """A non-transient refusal that is NOT 'series does not exist'."""


class TransportError(Exception):
    """A network-level failure (timeout, connection reset). Message is
    deliberately the exception type name only — never a URL."""


# ── Transport ───────────────────────────────────────────────────────────────
@dataclass
class FredResponse:
    status: int
    payload: Any  # parsed JSON body, or None if the body was not JSON
    retry_after: float | None = None


class FredTransport(Protocol):
    async def get(self, path: str, params: dict[str, Any]) -> FredResponse: ...


class HttpxTransport:
    """The real transport. httpx is the repo's declared HTTP dependency."""

    def __init__(self, client: httpx.AsyncClient | None = None,
                 base_url: str = FRED_BASE_URL):
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        self._base_url = base_url

    async def get(self, path: str, params: dict[str, Any]) -> FredResponse:
        try:
            response = await self._client.get(self._base_url + path, params=params)
        except httpx.HTTPError as exc:
            # httpx exception text includes the request URL — and so the key.
            # Keep the type name only, and drop the chained cause.
            raise TransportError(type(exc).__name__) from None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        retry_after = None
        header = response.headers.get("Retry-After")
        if header:
            try:
                retry_after = float(header)
            except ValueError:
                retry_after = None
        return FredResponse(response.status_code, payload, retry_after)

    async def aclose(self) -> None:
        await self._client.aclose()


# ── Throttle ────────────────────────────────────────────────────────────────
class Throttle:
    """Minimum spacing between requests, enforced with a real sleep."""

    def __init__(self, min_interval: float = DEFAULT_MIN_INTERVAL,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._min_interval = min_interval
        self._sleep = sleep
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            delay = self._min_interval - (time.monotonic() - self._last)
            if delay > 0:
                await self._sleep(delay)
            self._last = time.monotonic()


# ── Client ──────────────────────────────────────────────────────────────────
@dataclass
class SeriesMetadata:
    code: str
    title: str | None
    frequency_short: str | None
    units: str | None
    seasonal_adjustment: str | None
    notes: str | None

    @property
    def frequency(self) -> str:
        return map_frequency(self.frequency_short)


def _error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("error_message") or "")
    return ""


class FredClient:
    def __init__(self, api_key: str, transport: FredTransport | None = None, *,
                 min_interval: float = DEFAULT_MIN_INTERVAL,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 max_retries: int = MAX_RETRIES):
        if not api_key:
            raise FredRequestError("FRED_API_KEY is not set")
        self._api_key = api_key
        self._transport = transport or HttpxTransport()
        self._throttle = Throttle(min_interval, sleep)
        self._sleep = sleep
        self._max_retries = max_retries

    def scrub(self, text: Any) -> str:
        return scrub(text, self._api_key)

    async def aclose(self) -> None:
        close = getattr(self._transport, "aclose", None)
        if close is not None:
            await close()

    async def _request(self, path: str, code: str, extra: dict[str, Any]) -> Any:
        query = {"series_id": code, "api_key": self._api_key, "file_type": "json", **extra}
        last_problem = "no attempt made"
        for attempt in range(self._max_retries + 1):
            await self._throttle.wait()
            try:
                response = await self._transport.get(path, query)
            except TransportError as exc:
                last_problem = f"network error ({exc})"
                if attempt < self._max_retries:
                    await self._sleep(2 ** attempt)
                continue
            except Exception as exc:  # noqa: BLE001 — a fake/foreign transport
                # Unknown exception text may carry the URL: type name only.
                last_problem = f"transport failure ({type(exc).__name__})"
                if attempt < self._max_retries:
                    await self._sleep(2 ** attempt)
                continue

            status = response.status
            if status in RETRY_STATUSES:
                last_problem = f"HTTP {status}"
                if attempt < self._max_retries:
                    delay = float(2 ** attempt)
                    if response.retry_after is not None:
                        delay = max(delay, response.retry_after)
                    await self._sleep(delay)
                continue

            message = self.scrub(_error_message(response.payload))
            if status == 404 or (status == 400 and _NOT_FOUND_RE.search(message)):
                raise FredNotFound(self.scrub(
                    f"FRED says series {code} does not exist (HTTP {status}): "
                    f"{message or 'no message'}"))
            if status >= 400:
                raise FredRequestError(self.scrub(
                    f"FRED refused the request for {code} (HTTP {status}): "
                    f"{message or 'no message'}"))
            if not isinstance(response.payload, dict):
                raise FredRequestError(self.scrub(
                    f"FRED returned a non-JSON body for {code} (HTTP {status})"))
            return response.payload

        raise FredTransientError(self.scrub(
            f"FRED request for {code} failed after {self._max_retries + 1} "
            f"attempts: {last_problem}"))

    async def get_series(self, code: str) -> SeriesMetadata:
        payload = await self._request(SERIES_PATH, code, {})
        rows = payload.get("seriess") or []
        if not rows:
            raise FredNotFound(self.scrub(f"FRED returned no series record for {code}"))
        row = rows[0]
        return SeriesMetadata(
            code=code,
            title=row.get("title"),
            frequency_short=row.get("frequency_short"),
            units=row.get("units"),
            seasonal_adjustment=row.get("seasonal_adjustment"),
            notes=row.get("notes"),
        )

    async def get_observations(self, code: str, *, sort_order: str = "asc",
                               limit: int = OBSERVATION_PAGE_LIMIT,
                               observation_end: str | None = None,
                               max_pages: int | None = None) -> list[tuple[str, str]]:
        """Every observation as raw ``(date, value)`` strings, paged by offset.

        Values are returned UNPARSED — parsing (and the '.' missing marker) is
        the ingest layer's job, with Decimal, never float.
        """
        out: list[tuple[str, str]] = []
        offset = 0
        pages = 0
        while True:
            extra: dict[str, Any] = {"sort_order": sort_order, "limit": limit, "offset": offset}
            if observation_end:
                extra["observation_end"] = observation_end
            payload = await self._request(OBSERVATIONS_PATH, code, extra)
            batch = payload.get("observations") or []
            for row in batch:
                out.append((str(row.get("date")), str(row.get("value"))))
            pages += 1
            offset += len(batch)
            try:
                total = int(payload.get("count", 0))
            except (TypeError, ValueError):
                total = 0
            if not batch or offset >= total:
                break
            if max_pages is not None and pages >= max_pages:
                break
        return out
