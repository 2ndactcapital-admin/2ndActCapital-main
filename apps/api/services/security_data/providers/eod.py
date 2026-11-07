"""THE END-OF-DAY PRICE RECEPTOR (PRICES1) — the plug-in contract every EOD source fills, free or paid.

Adding a vendor = ONE adapter file (a subclass of :class:`EodPriceProvider`) + ONE ``data_providers`` row (its
licence facts) + its ``data_source_rank`` entry. Nothing else in the platform changes: the ingest
(``services.security_data.prices``), the store, the coverage flags, the backfill, the nightly job and every reader
are written against THIS contract and never name a vendor.

THE CONTRACT
  spec: EodSpec                         declared, checked by :func:`contract_problems`
      key                 the data_providers row it serves (also provider_key on every stored price)
      name                display name (the registry row's own name wins on screens)
      keyed_by            'ticker' | 'figi' | 'cusip' — which security-master identifier it asks by
      max_symbols_per_call how many symbols ONE request carries (1 for per-symbol APIs)
      daily_call_budget   the vendor's own limit (the registry row's daily_call_budget is the one enforced)
      min_interval_s      seconds between requests (the HttpTransportBase throttle)
      history_years       how far back it serves daily history
      gives_adjustment    True when it supplies an adjusted close / adjustment factor
      secret_env          the Doppler variable it needs (None = keyless); listed in adapters.SECRET_ENV_NAMES
      licence             EodLicence — the facts the registry row records (internal use, member display,
                          redistribution, retention after cancellation, attribution, terms)
  symbol_for(identifiers) -> str | None  the vendor's symbol for a security (identifiers: {id_type: value}); None =
                                         this source cannot ask for it (→ no_public_source with the reason)
  async fetch_eod(symbols, start, end) -> {symbol: EodSeries}
      network ONLY through ``self.http`` (an HttpTransportBase over the INJECTED transport — tests pass recorded
      fixtures); one EodSeries per requested symbol, ALWAYS (found or not, with the reason); bars ascending, dated
      in the exchange's own calendar, inside [start, end]; ``close`` is the UNADJUSTED close; ``calls`` counted.
      A transport / format failure for the whole request RAISES (EodSourceError) — the caller holds the run; a
      symbol the source does not know is EodSeries(found=False, reason=...), never an exception.

What the base class gives every adapter for free: read() over the canonical store (provider_key = spec.key),
capabilities / fetches = {eod_price}, connected flag, scrub(), and fetch() for the security-data nightly (which does
NOT refresh prices: the prices job does, see services.security_data.prices).

Decimal everywhere; JSON is parsed with parse_float=Decimal by every adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from services.security_data.providers import store
from services.security_data.providers.base import BaseProvider, FetchOutcome, HttpTransportBase

KEYED_BY = ("ticker", "figi", "cusip")
MEMBER_DISPLAY = ("allowed", "own_holdings_only", "not_allowed")
RETENTION = ("keep", "delete", "unknown", "not_applicable")


class EodSourceError(RuntimeError):
    """The request as a whole failed (HTTP error, unparseable body, changed format). The ingest records it and the
    platform job HOLDS — a broken feed must not silently write nothing every night."""


@dataclass(frozen=True)
class EodLicence:
    internal_use: bool
    member_display: str            # allowed | own_holdings_only | not_allowed
    redistribution: bool
    retention_after_cancel: str    # keep | delete | unknown | not_applicable
    attribution: str
    terms_note: str

    def as_dict(self) -> dict:
        return {"internal_use": self.internal_use, "member_display": self.member_display,
                "redistribution": self.redistribution, "retention_after_cancel": self.retention_after_cancel,
                "attribution": self.attribution}


@dataclass(frozen=True)
class EodSpec:
    key: str
    name: str
    keyed_by: str
    max_symbols_per_call: int
    daily_call_budget: int | None
    min_interval_s: float
    history_years: int
    gives_adjustment: bool
    secret_env: str | None
    licence: EodLicence


@dataclass
class EodBar:
    day: date
    close: Decimal                          # UNADJUSTED close
    currency: str | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume: Decimal | None = None
    adjusted_close: Decimal | None = None
    adjustment_factor: Decimal | None = None   # adjusted_close / close when the source gives it

    def bar_json(self) -> dict:
        out = {}
        for k in ("open", "high", "low", "volume", "adjusted_close"):
            v = getattr(self, k)
            if v is not None:
                out[k] = format(v, "f")
        return out


@dataclass
class EodSeries:
    symbol: str
    found: bool
    bars: list[EodBar] = field(default_factory=list)
    currency: str | None = None
    reason: str | None = None               # why not found / what was dropped
    dropped: dict = field(default_factory=dict)   # {"null_close": n, "incomplete_today": n, ...}
    events: dict = field(default_factory=dict)    # {"splits": [...], "dividends": [...]} when the source gives them


class EodPriceProvider(BaseProvider):
    """Subclass, set ``spec``, implement ``symbol_for`` and ``fetch_eod``. ``transport`` is anything with
    ``async get(url, params) -> (status, body)``; None = not connected (any fetch raises ProviderNotConnected)."""

    spec: EodSpec
    capabilities = frozenset({"eod_price"})
    fetches = frozenset()          # the marketdata1 nightly never refreshes prices; the prices job does

    @property
    def connected(self) -> bool:
        """A keyless adapter is connected; a keyed one only when its secret is present (the provider admin refuses to
        switch a not-connected adapter to active)."""
        return self.spec.secret_env is None or bool((self.environ.get(self.spec.secret_env) or "").strip())

    def __init__(self, *, transport=None, environ=None, sleep=None, now=None, min_interval: float | None = None):
        """``now`` (a callable returning an aware datetime) and ``min_interval`` / ``sleep`` are injectable so a
        verify runs on recorded fixtures with no wall clock and no throttle wait."""
        import asyncio
        import os

        self.key = self.spec.key
        self.environ = os.environ if environ is None else environ
        self.now = now
        self.http = HttpTransportBase(transport, min_interval=self.spec.min_interval_s if min_interval is None
                                      else min_interval, sleep=sleep or asyncio.sleep)

    def _now(self):
        from datetime import datetime, timezone
        return self.now() if self.now else datetime.now(timezone.utc)

    @property
    def calls(self) -> int:
        return self.http.calls

    def secret(self) -> str | None:
        if not self.spec.secret_env:
            return None
        v = (self.environ.get(self.spec.secret_env) or "").strip()
        if not v:
            from services.market_data.adapters import MissingConfiguration
            raise MissingConfiguration(self.spec.secret_env)
        return v

    def symbol_for(self, identifiers: dict[str, Any]) -> str | None:   # pragma: no cover — every adapter overrides
        raise NotImplementedError

    async def fetch_eod(self, symbols: list[str], start: date, end: date) -> dict[str, EodSeries]:  # pragma: no cover
        raise NotImplementedError

    async def read(self, conn, ctx, data_type, subjects, *, end: date, start: date | None = None) -> dict:
        if data_type != "eod_price":
            return {}
        return await store.prices(conn, ctx.org_id, self.key, subjects, end=end, start=start)

    async def fetch(self, pool, data_type, subjects, *, today: date, budget_left: int | None) -> FetchOutcome:
        return FetchOutcome(skipped=[f"{self.key}: end-of-day prices are refreshed by the prices job, not here"])


def contract_problems(provider) -> list[str]:
    """Static half of the contract tests (the behavioural half runs the adapter on a fake transport — the verify).
    [] = the adapter declares everything the platform reads."""
    out = []
    if not isinstance(provider, EodPriceProvider):
        return [f"{type(provider).__name__} is not an EodPriceProvider"]
    s = getattr(provider, "spec", None)
    if not isinstance(s, EodSpec):
        return ["no EodSpec"]
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", s.key or ""):
        out.append(f"key {s.key!r} does not match the registry's key shape")
    if s.keyed_by not in KEYED_BY:
        out.append(f"keyed_by {s.keyed_by!r} not in {KEYED_BY}")
    if not (isinstance(s.max_symbols_per_call, int) and s.max_symbols_per_call >= 1):
        out.append("max_symbols_per_call must be >= 1")
    if s.daily_call_budget is not None and s.daily_call_budget < 0:
        out.append("daily_call_budget must be >= 0 or None")
    if s.min_interval_s < 0 or s.history_years < 1:
        out.append("min_interval_s >= 0 and history_years >= 1")
    lic = s.licence
    if lic.member_display not in MEMBER_DISPLAY or lic.retention_after_cancel not in RETENTION:
        out.append("licence member_display / retention_after_cancel outside the registry's vocabulary")
    if lic.member_display != "not_allowed" and not lic.internal_use:
        out.append("member display without internal use (the registry CHECK refuses it)")
    if lic.redistribution and lic.member_display != "allowed":
        out.append("redistribution without member display (the registry CHECK refuses it)")
    if not (lic.attribution or "").strip() or not (lic.terms_note or "").strip():
        out.append("attribution and terms_note are required")
    if provider.capabilities != frozenset({"eod_price"}):
        out.append("capabilities must be exactly {eod_price}")
    if type(provider).fetch_eod is EodPriceProvider.fetch_eod or type(provider).symbol_for is EodPriceProvider.symbol_for:
        out.append("fetch_eod and symbol_for must be implemented")
    return out


def registry_row(spec: EodSpec, *, status: str) -> dict:
    """The data_providers row an adapter's spec implies (the operator's insert; the verify's fixture row)."""
    return {"key": spec.key, "name": spec.name, "kind": "public_api" if spec.secret_env is None else "vendor",
            "status": status, "capabilities": ["eod_price"], "lic_internal_use": spec.licence.internal_use,
            "lic_member_display": spec.licence.member_display, "lic_redistribution": spec.licence.redistribution,
            "retention_after_cancel": spec.licence.retention_after_cancel, "attribution": spec.licence.attribution,
            "terms_note": spec.licence.terms_note, "daily_call_budget": spec.daily_call_budget}


def ticker_symbol(identifiers: dict, *, class_sep: str = "-") -> str | None:
    """The master's ticker (BRK.B / BRK/B) in the common vendor spelling (BRK-B). None without a ticker."""
    t = identifiers.get("ticker")
    if isinstance(t, (list, tuple)):
        t = t[0] if t else None
    if not t:
        return None
    t = str(t).strip().upper()
    for sep in (".", "/", " "):
        t = t.replace(sep, class_sep)
    return t or None


# ── transports (anything with ``async get(url, params) -> (status, body text)``) ───────────────────────────────
class RealHttpGet:
    """The real network, for an adapter's production use only (the verify never builds one: httpx is blocked
    there). Headers are the adapter's own (a declared User-Agent). Keeps no URL's query string in any error."""

    def __init__(self, *, headers: dict | None = None, timeout: float = 30.0):
        import httpx

        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=dict(headers or {}))
        self.requests = 0

    async def get(self, url: str, params: dict):
        import httpx

        self.requests += 1
        try:
            r = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise EodSourceError(f"{type(exc).__name__} reaching {url.split('?')[0]}") from None
        return r.status_code, r.text

    async def aclose(self) -> None:
        await self._client.aclose()


class RecordedHttpGet:
    """Recorded responses only (tests). ``responses``: url → (status, body) | body; or ``responder(url, params) ->
    (status, body)``. Any other URL raises — together with httpx being blocked, a verify proves it made no network
    call. ``seen`` lists (url, params) in order; ``fail_after`` raises on the N+1-th call (a forced stop)."""

    def __init__(self, responses: dict | None = None, responder=None, *, fail_after: int | None = None):
        self.responses = dict(responses or {})
        self.responder = responder
        self.fail_after = fail_after
        self.seen: list[tuple[str, dict]] = []
        self.requests = 0

    async def get(self, url: str, params: dict):
        if self.fail_after is not None and self.requests >= self.fail_after:
            raise EodSourceError(f"recorded transport: forced stop after {self.fail_after} call(s)")
        self.requests += 1
        self.seen.append((url, dict(params or {})))
        if self.responder is not None:
            return self.responder(url, dict(params or {}))
        if url not in self.responses:
            raise EodSourceError(f"recorded transport: no recorded response for {url}")
        r = self.responses[url]
        return r if isinstance(r, tuple) else (200, r)

    async def aclose(self) -> None:
        return None
