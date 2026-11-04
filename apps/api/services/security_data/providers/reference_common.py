"""SECMASTER1 — what the five reference-data adapters share.

  SourceRecord        one security as one source states it (canonical symbol, identifiers, MIC, type, attributes)
  ReferenceProvider   the code-registry module for a 'reference' provider: read() answers the rank walk from
                      portfolio.security_reference_attributes (subject '<gsid>:<field>'); fetch() is never used by
                      marketdata1's nightly (no 'fetches') — these sources are pulled by services.security_master_sync
  SourceTransport     THE network boundary, injected. RealSourceTransport streams lines over httpx and sends EVERY
                      sec.gov request through services.edgar_fetch (its declared User-Agent and 10/s limiter);
                      FixtureTransport serves recorded files (verify: no network at all)
  CallBudget          the provider's daily call budget (provider_call_log) — checked before each request, and one
                      provider_call_log row written per stage with the calls made, ok / the scrubbed error
  stream_csv_rows     RFC 4180 rows from a line stream without holding the file (quoted fields may span lines)
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, AsyncIterator, Iterable

from services.security_data.providers import store
from services.security_data.providers.base import BaseProvider


# ── records ─────────────────────────────────────────────────────────────────
@dataclass
class SourceRecord:
    provider_key: str
    ticker: str | None = None          # canonical (security_master_symbols.normalize_symbol)
    name: str | None = None
    exchange: str | None = None        # MIC
    security_type: str | None = None   # securities_global_type_chk
    cusip: str | None = None
    isin: str | None = None
    figi: str | None = None
    attrs: dict = field(default_factory=dict)   # reference field -> text value
    as_of: date | None = None
    raw_symbol: str | None = None
    note: str | None = None

    @property
    def subject(self) -> str:
        return self.cusip or self.figi or (f"{self.ticker}@{self.exchange or '?'}" if self.ticker else "?")


@dataclass
class ParseStats:
    rows: int = 0
    kept: int = 0
    excluded: dict = field(default_factory=dict)
    as_of: Any = None          # the file's own stamp when it has one (Nasdaq footer)
    sha256: str = ""
    lines: int = 0

    def exclude(self, why: str) -> None:
        self.excluded[why] = self.excluded.get(why, 0) + 1


class Digest:
    """sha256 over the lines actually read, so 'the same file' is a fact, not a guess."""

    def __init__(self) -> None:
        self._h = hashlib.sha256()

    def update(self, line: str) -> None:
        self._h.update(line.encode("utf-8", "replace"))
        self._h.update(b"\n")

    def hexdigest(self) -> str:
        return self._h.hexdigest()


# ── the code-registry module ────────────────────────────────────────────────
class ReferenceProvider(BaseProvider):
    capabilities = frozenset({"reference"})
    fetches: frozenset[str] = frozenset()

    def __init__(self, key: str):
        self.key = key

    async def read(self, conn, ctx, data_type, subjects, *, end: date, start: date | None = None) -> dict:
        if data_type != "reference":
            return {}
        return await store.reference_attributes(conn, self.key, subjects)


# ── transports ──────────────────────────────────────────────────────────────
class SourceTransport:
    """``lines(url)`` → async iterator of text lines (no line terminators); ``post_json(url, payload, headers)``
    → (status, headers, parsed body). Implementations count their own requests in ``requests``."""

    requests: int = 0

    def lines(self, url: str) -> AsyncIterator[str]:  # pragma: no cover — interface
        raise NotImplementedError

    async def post_json(self, url: str, payload: Any, headers: dict | None = None):  # pragma: no cover
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class RealSourceTransport(SourceTransport):
    """httpx, streamed. sec.gov goes through services.edgar_fetch._get (declared User-Agent, the shared 10/s
    limiter, retry on 429/5xx) — the SEC files are small (≤ ~2 MB) and are split into lines after the fetch."""

    def __init__(self, *, timeout: float = 120.0):
        import httpx

        self._httpx = httpx
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self.requests = 0

    async def lines(self, url: str) -> AsyncIterator[str]:
        self.requests += 1
        if "sec.gov" in url:
            from services import edgar_fetch

            resp = await edgar_fetch._get(self._client, url)
            for ln in resp.text.splitlines():
                yield ln
            return
        async with self._client.stream("GET", url, headers={"User-Agent": "Hollisworks-SecurityMaster/1.0"}) as r:
            r.raise_for_status()
            async for ln in r.aiter_lines():
                yield ln.rstrip("\r\n")

    async def post_json(self, url: str, payload: Any, headers: dict | None = None):
        self.requests += 1
        r = await self._client.post(url, json=payload, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            body = r.json()
        except ValueError:
            body = r.text
        return r.status_code, dict(r.headers), body

    async def aclose(self) -> None:
        await self._client.aclose()


class FixtureTransport(SourceTransport):
    """Recorded responses only. ``files``: url → text (or a path's text, read by the caller); ``responder``:
    ``fn(url, payload) -> (status, headers, body)`` for POSTs. Any other URL raises — a verify proves it made no
    network call by this, plus httpx being blocked."""

    def __init__(self, files: dict[str, str] | None = None, responder=None, fail_urls: Iterable[str] = ()):
        self.files = dict(files or {})
        self.responder = responder
        self.fail_urls = set(fail_urls)
        self.requests = 0
        self.seen: list[str] = []

    async def lines(self, url: str) -> AsyncIterator[str]:
        self.requests += 1
        self.seen.append(url)
        if url in self.fail_urls:
            raise RuntimeError(f"fixture transport: forced failure for {url}")
        if url not in self.files:
            raise RuntimeError(f"fixture transport: no recorded response for {url}")
        for ln in self.files[url].splitlines():
            yield ln

    async def post_json(self, url: str, payload: Any, headers: dict | None = None):
        self.requests += 1
        self.seen.append(url)
        if url in self.fail_urls or self.responder is None:
            raise RuntimeError(f"fixture transport: no recorded response for POST {url}")
        return self.responder(url, payload)


async def read_text(transport: SourceTransport, url: str, digest: Digest | None = None) -> str:
    parts = []
    async for ln in transport.lines(url):
        if digest is not None:
            digest.update(ln)
        parts.append(ln)
    return "\n".join(parts)


# ── budgets + call log ──────────────────────────────────────────────────────
class BudgetExhausted(RuntimeError):
    pass


class CallBudget:
    """The provider's daily budget (data_providers.daily_call_budget minus today's provider_call_log). ``take()``
    before each request; ``flush()`` writes ONE call-log row for the calls made since the last flush."""

    def __init__(self, pool, provider_key: str, *, run_id=None, data_type: str = "reference", today: date | None = None):
        self.pool = pool
        self.key = provider_key
        self.run_id = run_id
        self.data_type = data_type
        self.today = today
        self.left: int | None = None
        self.used = 0
        self._unflushed = 0
        self._loaded = False

    async def load(self) -> "CallBudget":
        from datetime import datetime, timezone

        from services.security_data import registry as R
        from services.security_data import writes as W

        self.today = self.today or datetime.now(timezone.utc).date()
        async with self.pool.acquire() as conn:
            rows = await R.providers(conn)
            p = rows.get(self.key)
            if p is None:
                raise RuntimeError(f"provider {self.key!r} is not registered (apply docs/secmaster1_part1.sql)")
            if p["status"] != "active":
                raise RuntimeError(f"provider {self.key!r} is {p['status']} — not fetched")
            self.left = await W.budget_left(conn, p, self.today)
        self._loaded = True
        return self

    def remaining(self) -> int | None:
        return None if self.left is None else max(0, self.left - self.used)

    async def take(self, n: int = 1) -> None:
        if not self._loaded:
            await self.load()
        if self.left is not None and self.used + n > self.left:
            raise BudgetExhausted(f"{self.key}: daily call budget exhausted ({self.left} left today)")
        self.used += n
        self._unflushed += n

    async def flush(self, *, ok: bool = True, error: str | None = None) -> None:
        from services.market_data.adapters import scrub_error
        from services.security_data import writes as W

        if self._unflushed == 0 and ok:
            return
        n, self._unflushed = self._unflushed, 0
        await W.record_calls(self.pool, self.key, n, data_type=self.data_type, ok=ok,
                             error=scrub_error(error)[:2000] if error else None, run_id=self.run_id, day=self.today)


# ── CSV streaming ───────────────────────────────────────────────────────────
async def stream_csv_rows(lines: AsyncIterator[str], digest: Digest | None = None) -> AsyncIterator[dict]:
    """dict rows keyed by the header. A record whose quoted field spans lines is assembled by quote parity
    (RFC 4180: an escaped quote is doubled, so parity is preserved). Memory: one record at a time."""
    header = None
    buf: list[str] = []
    quotes = 0
    async for ln in lines:
        if digest is not None:
            digest.update(ln)
        buf.append(ln)
        quotes += ln.count('"')
        if quotes % 2:
            continue
        record = "\n".join(buf)
        buf, quotes = [], 0
        if not record.strip():
            continue
        values = next(csv.reader(io.StringIO(record)))
        if header is None:
            header = [h.strip() for h in values]
            continue
        yield dict(zip(header, values))


def dumps(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


async def sleep(seconds: float) -> None:
    if seconds > 0:
        await asyncio.sleep(seconds)
