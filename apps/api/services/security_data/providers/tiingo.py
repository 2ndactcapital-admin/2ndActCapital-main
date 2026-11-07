"""tiingo — the FALLBACK free end-of-day adapter (PRICES1, Discovery 1b). Written and registered NOT CONNECTED:
TIINGO_API_KEY is absent from Doppler (checked by name, 2026-11-07). The operator adds the key, then switches the
registry row to active (Super Admin → Security data → providers); nothing else changes.

Source: GET https://api.tiingo.com/tiingo/daily/<ticker>/prices?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD with the key
in the ``Authorization: Token <key>`` HEADER (never the query string, so no URL in a log carries it). Response: a JSON
list of {date "2026-11-06T00:00:00.000Z", close, high, low, open, volume (UNADJUSTED), adjClose, adjHigh, adjLow,
adjOpen, adjVolume, divCash, splitFactor}. An unknown ticker → 404 {"detail": "Error: Ticker 'X' not found"}.
No key → 403 {"detail": "Please supply a token"} (recorded 2026-11-07). The verify's Tiingo fixture is SYNTHETIC in
this documented format (no key, so no real recording) and says so.

Licence: free tier for personal / internal use; display or redistribution needs Tiingo's commercial licence →
internal use only, member_display not_allowed, retention 'delete' on cancellation. Budget ~1,000 requests a day and
500 distinct symbols a month on the free tier (the registry row's budget, 900, is what is enforced)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from urllib.parse import quote

from services.security_data.providers.eod import (EodBar, EodLicence, EodPriceProvider, EodSeries, EodSourceError,
                                                  EodSpec, RealHttpGet, ticker_symbol)

BASE_URL = "https://api.tiingo.com/tiingo/daily/"
Q4 = Decimal("0.0001")
Q6 = Decimal("0.000001")

SPEC = EodSpec(
    key="tiingo", name="Tiingo end-of-day (free tier)", keyed_by="ticker", max_symbols_per_call=1,
    daily_call_budget=900, min_interval_s=0.5, history_years=30, gives_adjustment=True, secret_env="TIINGO_API_KEY",
    licence=EodLicence(internal_use=True, member_display="not_allowed", redistribution=False,
                       retention_after_cancel="delete", attribution="Prices: Tiingo",
                       terms_note="Tiingo free tier: personal / internal use; display or redistribution needs the "
                                  "commercial licence."))


def url_for(symbol: str) -> str:
    return BASE_URL + quote(symbol.lower(), safe="") + "/prices"


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return v if isinstance(v, Decimal) else Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def parse_prices(text: str, symbol: str, start: date, end: date) -> EodSeries:
    try:
        rows = json.loads(text, parse_float=Decimal)
    except ValueError:
        raise EodSourceError(f"tiingo: {symbol}: the response is not JSON") from None
    if isinstance(rows, dict):
        detail = str(rows.get("detail") or "")
        if "not found" in detail.lower():
            return EodSeries(symbol, False, reason=f"Tiingo has no data for {symbol}: {detail}")
        raise EodSourceError(f"tiingo: {symbol}: {detail[:120] or 'unexpected object'}")
    if not isinstance(rows, list):
        raise EodSourceError(f"tiingo: {symbol}: unexpected payload")
    bars, splits, divs, dropped = {}, [], [], {}
    for r in rows:
        try:
            day = datetime.fromisoformat(str(r["date"]).replace("Z", "+00:00")).date()
        except (KeyError, ValueError):
            raise EodSourceError(f"tiingo: {symbol}: a row without a date") from None
        close = _dec(r.get("close"))
        if close is None:
            dropped["null_close"] = dropped.get("null_close", 0) + 1
            continue
        if day < start or day > end:
            continue
        adj = _dec(r.get("adjClose"))
        sf, dc = _dec(r.get("splitFactor")), _dec(r.get("divCash"))
        if sf is not None and sf != 1:
            splits.append({"date": day.isoformat(), "ratio": format(sf, "f")})
        if dc:
            divs.append({"ex_date": day.isoformat(), "amount": format(dc, "f")})
        q = close.quantize(Q4, ROUND_HALF_EVEN)

        def px(k):
            v = _dec(r.get(k))
            return None if v is None or v <= 0 else v.quantize(Q4, ROUND_HALF_EVEN)

        vol = _dec(r.get("volume"))
        bars[day] = EodBar(day=day, close=q, currency="USD", open=px("open"), high=px("high"), low=px("low"),
                           volume=vol if vol and vol > 0 else None,
                           adjusted_close=None if adj is None else adj.quantize(Q4, ROUND_HALF_EVEN),
                           adjustment_factor=(adj / q).quantize(Q6, ROUND_HALF_EVEN) if adj and q > 0 and adj > 0
                           else None)
    out = [bars[d_] for d_ in sorted(bars)]
    return EodSeries(symbol, True, out, "USD", None if out else "no daily bar in the range", dropped,
                     {"splits": splits, "dividends": divs})


class TiingoProvider(EodPriceProvider):
    spec = SPEC

    def symbol_for(self, identifiers: dict) -> str | None:
        return ticker_symbol(identifiers, class_sep="-")

    def real_transport(self) -> RealHttpGet:
        return RealHttpGet(headers={"Authorization": f"Token {self.secret()}", "Accept": "application/json",
                                    "User-Agent": "Hollisworks-Prices/1.0"})

    async def fetch_eod(self, symbols: list[str], start: date, end: date) -> dict[str, EodSeries]:
        out = {}
        for sym in symbols:
            status, body = await self.http.get(url_for(sym), {"startDate": start.isoformat(),
                                                              "endDate": end.isoformat(), "format": "json"})
            if status == 404:
                out[sym] = parse_prices(body, sym, start, end)
                if out[sym].found:
                    raise EodSourceError(f"tiingo: {sym}: HTTP 404 with a price body")
                continue
            if status != 200:
                raise EodSourceError(f"tiingo: {sym}: HTTP {status}"
                                     + (" (no or invalid API key)" if status in (401, 403) else ""))
            out[sym] = parse_prices(body, sym, start, end)
        return out
