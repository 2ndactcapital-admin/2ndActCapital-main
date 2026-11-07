"""vendor — the SLOT a paid vendor plugs into, and (PRICES1) the PAID VENDOR TEMPLATE.

VendorStubProvider ('vendor_slot', marketdata1): registered 'not_connected', no rights, and it makes NO network call —
read() returns nothing and fetch() refuses before touching its transport. Unchanged.

VendorTemplateProvider (PRICES1): a complete end-of-day adapter behind the receptor contract
(services/security_data/providers/eod.py) for a generic paid vendor's JSON. To plug FMP (or any vendor) in:

  1. copy this class to providers/<key>.py; set SPEC (key, keyed_by, budget, licence facts FROM THE CONTRACT,
     secret_env = the Doppler variable) and rewrite ``parse`` / the request in ``fetch_eod`` for the vendor's format;
  2. one line in providers/__init__.py::build_providers; the secret's NAME in adapters.SECRET_ENV_NAMES;
  3. one data_providers row (eod.registry_row(SPEC, status='not_connected') gives its columns) and its
     data_source_rank entry for 'eod_price';
  4. prove it with recorded responses through the injected transport (the verify's contract tests), then switch the
     row to active.

Nothing else changes: the ingest, the store, coverage, the backfill, the nightly job and every reader work through
the contract. verify_prices1 registers THIS template under a fixture key with a fake transport and runs the same
contract tests as the free adapter — the plug-in proof.

The template's wire format (what a vendor's daily-bars endpoint typically returns):
  GET <base_url>/eod/<symbol>?from=YYYY-MM-DD&to=YYYY-MM-DD   →   200 {"symbol": "...", "currency": "USD",
      "bars": [{"date": "2026-11-06", "open": "1", "high": "1", "low": "1", "close": "1", "adj_close": "1",
                "volume": "100"}]}
  unknown symbol → 404 {"error": "unknown symbol"}"""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from urllib.parse import quote

from services.security_data.providers.base import BaseProvider, FetchOutcome
from services.security_data.providers.eod import (EodBar, EodLicence, EodPriceProvider, EodSeries, EodSourceError,
                                                  EodSpec, ticker_symbol)


class VendorStubProvider(BaseProvider):
    capabilities = frozenset({"eod_price", "corporate_action", "index_level", "option_quote", "fx", "factor_score"})
    fetches = frozenset()
    connected = False

    def __init__(self, key: str, *, transport=None):
        self.key = key
        self.transport = transport     # held, never called while not connected

    async def read(self, conn, ctx, data_type, subjects, *, end: date, start: date | None = None) -> dict:
        return {}

    async def fetch(self, pool, data_type, subjects, *, today: date, budget_left: int | None) -> FetchOutcome:
        return FetchOutcome(skipped=[f"{self.key}: not connected — no network call made"])


# ── PRICES1: the paid vendor template ──────────────────────────────────────
TEMPLATE_SPEC = EodSpec(
    key="vendor_template", name="Paid vendor (template)", keyed_by="ticker", max_symbols_per_call=1,
    daily_call_budget=10000, min_interval_s=0.2, history_years=30, gives_adjustment=True,
    secret_env="VENDOR_TEMPLATE_API_KEY",
    licence=EodLicence(internal_use=True, member_display="allowed", redistribution=False,
                       retention_after_cancel="delete", attribution="Prices: <vendor>",
                       terms_note="TEMPLATE — replace with the signed contract's terms."))
Q4 = Decimal("0.0001")
Q6 = Decimal("0.000001")


def _dec(v):
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


class VendorTemplateProvider(EodPriceProvider):
    spec = TEMPLATE_SPEC

    def __init__(self, key: str = TEMPLATE_SPEC.key, *, base_url: str = "https://vendor.invalid/v1",
                 licence: EodLicence | None = None, **kw):
        self.spec = dataclasses.replace(TEMPLATE_SPEC, key=key, licence=licence or TEMPLATE_SPEC.licence)
        self.base_url = base_url.rstrip("/")
        super().__init__(**kw)

    def symbol_for(self, identifiers: dict) -> str | None:
        return ticker_symbol(identifiers, class_sep=".")

    def url_for(self, symbol: str) -> str:
        return f"{self.base_url}/eod/{quote(symbol, safe='')}"

    def parse(self, text: str, symbol: str, start: date, end: date) -> EodSeries:
        try:
            doc = json.loads(text, parse_float=Decimal)
        except ValueError:
            raise EodSourceError(f"{self.key}: {symbol}: not JSON") from None
        if not isinstance(doc, dict) or ("bars" not in doc and "error" not in doc):
            raise EodSourceError(f"{self.key}: {symbol}: unexpected payload")
        if "error" in doc:
            return EodSeries(symbol, False, reason=f"{self.spec.name} has no data for {symbol}: {doc['error']}")
        cur = doc.get("currency") or None
        bars = {}
        for b in doc.get("bars") or []:
            day, close = date.fromisoformat(str(b["date"])), _dec(b.get("close"))
            if close is None or day < start or day > end:
                continue
            close = close.quantize(Q4, ROUND_HALF_EVEN)
            adj = _dec(b.get("adj_close"))
            bars[day] = EodBar(day=day, close=close, currency=cur, open=_dec(b.get("open")), high=_dec(b.get("high")),
                               low=_dec(b.get("low")), volume=_dec(b.get("volume")),
                               adjusted_close=adj, adjustment_factor=(adj / close).quantize(Q6, ROUND_HALF_EVEN)
                               if adj and close > 0 and adj > 0 else None)
        out = [bars[d_] for d_ in sorted(bars)]
        return EodSeries(symbol, True, out, cur, None if out else "no daily bar in the range")

    async def fetch_eod(self, symbols: list[str], start: date, end: date) -> dict[str, EodSeries]:
        out = {}
        for sym in symbols:
            status, body = await self.http.get(self.url_for(sym), {"from": start.isoformat(), "to": end.isoformat()})
            if status == 404:
                out[sym] = self.parse(body, sym, start, end)
                continue
            if status != 200:
                raise EodSourceError(f"{self.key}: {sym}: HTTP {status}")
            out[sym] = self.parse(body, sym, start, end)
        return out
