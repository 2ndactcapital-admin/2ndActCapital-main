"""Provider-adapter registry (mkt02).

A registry is a plain ``dict[source_provider, adapter]``. Everything that
fetches data — ``market_data_ingest.py validate|backfill --provider X`` and the
nightly orchestrator — looks the adapter up by the registry row's
``source_provider``, and takes the registry as an ARGUMENT, so tests pass
fakes and never touch a real source.

ADAPTER CONTRACT
──────────────────────────────────────────────────────────────────────────────
  provider: str
  async fetch_series(series_row) -> FetchResult
      Full history as ``points: list[(obs_date, Decimal)]`` — missing and
      invalid points ALREADY filtered out and counted (``skipped`` for the
      source's own "no value" marker, ``rejected`` for values that are not
      numbers). At most one point per date. Raises on failure; the caller
      records the failure on that series only.
  async validate_series(series_row) -> ValidateResult
      ``outcome='active'`` with the registry fields the source knows
      (units, seasonal_adjustment, frequency); ``'invalid_code'`` ONLY when
      the source explicitly says the code does not exist; ``'error'`` for
      everything else (transient or not), which never changes ingest_status.
  scrub(text) -> str
      Removes this provider's secrets from text.
  async aclose()

Registered today: 'fred' and 'yahoo'. Shiller / World Bank / IMF / BIS / OECD
are mkt02b; their rows stay 'deferred' and the nightly skips them as
"no adapter" rather than failing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

from services.market_data import fred, ingest
from services.market_data.fred import FredClient, FredError, FredNotFound

REAL_PROVIDERS = ("fred", "yahoo")

# Environment variables whose VALUES must never reach stdout or a database
# column. scrub_error removes each one it finds set, wherever it appears.
SECRET_ENV_NAMES = ("FRED_API_KEY", "DATABASE_URL", "DB_PASSWORD", "APP_SERVICE_DATABASE_URL")
_MIN_SECRET_LEN = 6
_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s'\"<>]+")


class ProviderError(Exception):
    """Base for adapter errors whose message is already safe to store."""


@dataclass
class FetchResult:
    points: list[tuple[date, Decimal]] = field(default_factory=list)
    skipped: int = 0
    rejected: list[str] = field(default_factory=list)
    # Provider-specific counters worth printing (e.g. Yahoo's dropped
    # incomplete bar). Never used for control flow.
    notes: dict[str, int] = field(default_factory=dict)


@dataclass
class ValidateResult:
    outcome: str  # 'active' | 'invalid_code' | 'error'
    fields: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    finds: list[str] = field(default_factory=list)


class Adapter(Protocol):
    provider: str

    async def fetch_series(self, series_row) -> FetchResult: ...
    async def validate_series(self, series_row) -> ValidateResult: ...
    def scrub(self, text: Any) -> str: ...
    async def aclose(self) -> None: ...


# ── Scrubbing ───────────────────────────────────────────────────────────────
def _strip_url(match: re.Match) -> str:
    """Keep scheme://host/path; drop userinfo (passwords) and the query
    string (FRED's api_key lives there)."""
    try:
        parts = urlsplit(match.group(0))
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except ValueError:
        return "<url>"


def scrub_error(text: Any, adapter: Any = None) -> str:
    """Make error text safe to print or store, whatever raised it.

    Layered on purpose: the adapter's own scrub (it knows its key), then every
    secret-bearing environment value, then any URL's userinfo and query
    string, then FRED's ``api_key=`` fragment rule and the length bound. A
    fake or third-party adapter that forgets to scrub still cannot leak the
    FRED key or the database password through the orchestrator.
    """
    out = str(text)
    if adapter is not None:
        try:
            out = str(adapter.scrub(out))
        except Exception:  # noqa: BLE001 — a broken scrub must not stop scrubbing
            pass
    for name in SECRET_ENV_NAMES:
        value = os.environ.get(name) or ""
        candidates = [value]
        if "://" in value:
            try:
                candidates.append(urlsplit(value).password or "")
            except ValueError:
                pass
        for secret in candidates:
            if len(secret) >= _MIN_SECRET_LEN:
                out = out.replace(secret, "***")
    out = _URL_RE.sub(_strip_url, out)
    return fred.scrub(out)


# ── FRED ────────────────────────────────────────────────────────────────────
class FredAdapter:
    """mkt01's FredClient behind the adapter contract. Behaviour is mkt01's:
    same requests, same parse (ingest.parse_observations), same error classes."""

    provider = "fred"

    def __init__(self, client: FredClient):
        self.client = client

    def scrub(self, text: Any) -> str:
        return self.client.scrub(text)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def fetch_series(self, series_row) -> FetchResult:
        raw = await self.client.get_observations(series_row["source_code"])
        parsed = ingest.parse_observations(raw)
        return FetchResult(parsed.points, parsed.skipped, parsed.rejected)

    async def validate_series(self, series_row) -> ValidateResult:
        key, code = series_row["series_key"], series_row["source_code"]
        try:
            meta = await self.client.get_series(code)
        except FredNotFound as exc:
            return ValidateResult("invalid_code", error=self.client.scrub(exc))
        except FredError as exc:
            return ValidateResult("error", error=self.client.scrub(exc))

        finds: list[str] = []
        if meta.frequency != series_row["frequency"]:
            finds.append(f"{key}: frequency seed={series_row['frequency']} FRED={meta.frequency} "
                         f"(FRED frequency_short={meta.frequency_short!r}) — updated to FRED's")
        if "copyright" in (meta.notes or "").lower() and series_row["license_class"] == "public_domain":
            finds.append(f"{key}: FRED notes mention copyright but seed license_class="
                         "public_domain — review; license_class NOT changed")
        return ValidateResult("active", fields={
            "units": meta.units,
            "seasonal_adjustment": meta.seasonal_adjustment,
            "frequency": meta.frequency,
        }, finds=finds)


# ── Registry ────────────────────────────────────────────────────────────────
class MissingConfiguration(Exception):
    """A provider's required environment variable is absent. The message
    names the VARIABLE, never a value."""


def build_adapter(provider: str, environ: Mapping[str, str] | None = None):
    environ = os.environ if environ is None else environ
    if provider == "fred":
        api_key = (environ.get("FRED_API_KEY") or "").strip()
        if not api_key:
            raise MissingConfiguration("FRED_API_KEY")
        return FredAdapter(FredClient(api_key))
    if provider == "yahoo":
        from services.market_data.yahoo import YahooAdapter
        return YahooAdapter()
    raise KeyError(f"no adapter is registered for source_provider {provider!r}")


def build_registry(environ: Mapping[str, str] | None = None,
                   providers: tuple[str, ...] = REAL_PROVIDERS) -> dict:
    """The REAL registry. Constructing it makes no network call."""
    return {p: build_adapter(p, environ) for p in providers}


async def close_registry(registry: Mapping[str, Any]) -> None:
    for adapter in registry.values():
        close = getattr(adapter, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001 — closing must not mask the run's result
                pass
