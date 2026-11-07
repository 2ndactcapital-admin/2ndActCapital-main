"""yahoo_chart — the first FREE end-of-day price adapter behind the receptor (PRICES1, Discovery 1b: primary).

Source: query1.finance.yahoo.com/v8/finance/chart/<symbol> — keyless, daily bars for US stocks, ETFs, mutual funds and
money-market funds (one bar per NAV day), history to the listing date. Recorded responses (2026-11-07):
apps/api/scripts/fixtures/prices1/yahoo_chart_*.json.

LICENCE (the registry row records it; the layer enforces it): Yahoo's terms allow personal, non-commercial use.
Internal adviser analysis only, under the owner's recorded assumption (Joe, 2026-10-03 — the same one the
market-indicators module rests on). member_display = not_allowed, redistribution = false. A launch blocker: a
licensed vendor replaces it (one adapter file, see vendor_stub.VendorTemplateProvider) before any external customer.

WHAT IT STORES
  * close = the UNADJUSTED close. Yahoo's ``close`` is already SPLIT-adjusted (NVDA's 2024-06-07 close reads 120.89,
    not 1208.88). Every request therefore runs to NOW (period2 = now + 1 day) so every later split is in the
    payload's ``events.splits``, and close × Π(numerator ÷ denominator of each split dated AFTER the bar) restores
    the price that traded. Open / high / low / volume are restored the same way.
  * adjustment_factor = adjclose ÷ unadjusted close (splits + dividends), 6 dp, when Yahoo gives adjclose — the
    factor AS OF the first store (a later dividend shifts every factor; it does not restate the stored close).
  * Decimal from the JSON parser (parse_float=Decimal); prices quantized to 4 dp HALF_EVEN (Yahoo's numbers carry
    binary noise, 116.43699645996094, that differs between calls — without this every night would "restate").
  * Dates are exchange-local (meta.gmtoffset). Today's bar is dropped while the regular session is still open.
  * Zero / negative closes are PASSED THROUGH: the ingest's quality check turns them into an invalid_value event and
    never stores them (a money-market fund's zero OPEN is simply dropped from the bar).

ERRORS: Yahoo's explicit "Not Found" → EodSeries(found=False) with its description (→ no_public_source). Any other
non-200 (401 / 403 = a blocked IP, 429, 5xx after retries), a body that is not the chart JSON, or a granularity
other than 1d → EodSourceError (the run holds; nothing is written for that request)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from urllib.parse import quote

from services.security_data.providers.eod import (EodBar, EodLicence, EodPriceProvider, EodSeries, EodSourceError,
                                                  EodSpec, RealHttpGet, ticker_symbol)

BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
# Yahoo refuses obvious script clients; the market-indicators adapter sends the same browser string.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")
Q4 = Decimal("0.0001")
Q6 = Decimal("0.000001")   # the adjustment factor: 6 dp (adjclose carries ~1e-8 binary noise)

SPEC = EodSpec(
    key="yahoo_chart", name="Yahoo Finance chart (end-of-day)", keyed_by="ticker", max_symbols_per_call=1,
    daily_call_budget=2000, min_interval_s=1.0, history_years=30, gives_adjustment=True, secret_env=None,
    licence=EodLicence(internal_use=True, member_display="not_allowed", redistribution=False,
                       retention_after_cancel="unknown", attribution="Prices: Yahoo Finance",
                       terms_note="Yahoo terms: personal, non-commercial use; internal adviser analysis only under the "
                                  "owner's recorded assumption (2026-10-03); never shown to members; a launch "
                                  "blocker."))


def url_for(symbol: str) -> str:
    return BASE_URL + quote(symbol, safe="")


def _epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return v if isinstance(v, Decimal) else Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def parse_chart(text: str, symbol: str, start: date, end: date, *, now: datetime) -> EodSeries:
    """Pure: one chart payload → EodSeries (bars ascending, inside [start, end])."""
    try:
        doc = json.loads(text, parse_float=Decimal)
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise EodSourceError(f"yahoo_chart: {symbol}: the response is not Yahoo's chart JSON") from None
    err = chart.get("error")
    if err:
        if str(err.get("code")) == "Not Found":
            return EodSeries(symbol, False, reason=f"Yahoo has no data for {symbol}: {err.get('description')}")
        raise EodSourceError(f"yahoo_chart: {symbol}: chart error {err.get('code')}")
    res = (chart.get("result") or [None])[0]
    if not res:
        return EodSeries(symbol, False, reason=f"Yahoo returned no chart for {symbol}")
    meta = res.get("meta") or {}
    gran = meta.get("dataGranularity")
    if gran not in (None, "1d"):
        raise EodSourceError(f"yahoo_chart: {symbol}: granularity {gran!r}, expected 1d")
    currency = (meta.get("currency") or None)
    off = int(meta.get("gmtoffset") or 0)
    ts = res.get("timestamp") or []
    ind = res.get("indicators") or {}
    quote_ = (ind.get("quote") or [{}])[0] or {}
    adj = ((ind.get("adjclose") or [{}])[0] or {}).get("adjclose") or []
    splits = []
    for s in ((res.get("events") or {}).get("splits") or {}).values():
        num, den = _dec(s.get("numerator")), _dec(s.get("denominator"))
        if num and den and num > 0 and den > 0:
            splits.append(((datetime.fromtimestamp(int(s["date"]) + off, timezone.utc)).date(), num / den))
    dividends = [{"ex_date": datetime.fromtimestamp(int(dv["date"]) + off, timezone.utc).date().isoformat(),
                  "amount": format(_dec(dv.get("amount")) or Decimal(0), "f")}
                 for dv in ((res.get("events") or {}).get("dividends") or {}).values()]
    local_today = (now + timedelta(seconds=off)).date()
    reg_end = (((meta.get("currentTradingPeriod") or {}).get("regular") or {}).get("end"))
    session_open = reg_end is not None and now.timestamp() < int(reg_end)
    dropped = {"null_close": 0, "incomplete_today": 0, "duplicate_day": 0, "outside_range": 0}
    by_day: dict[date, EodBar] = {}

    def col(name, i):
        arr = quote_.get(name) or []
        return _dec(arr[i]) if i < len(arr) else None

    for i, t in enumerate(ts):
        day = datetime.fromtimestamp(int(t) + off, timezone.utc).date()
        close = col("close", i)
        if close is None:
            dropped["null_close"] += 1
            continue
        if day >= local_today and (day > local_today or session_open):
            dropped["incomplete_today"] += 1
            continue
        if day < start or day > end:
            dropped["outside_range"] += 1
            continue
        f = Decimal(1)
        for sd, ratio in splits:
            if sd > day:
                f *= ratio
        raw = (close * f).quantize(Q4, ROUND_HALF_EVEN)
        ac = _dec(adj[i]) if i < len(adj) else None

        def px(name):
            v = col(name, i)
            return None if v is None or v <= 0 else (v * f).quantize(Q4, ROUND_HALF_EVEN)

        vol = col("volume", i)
        bar = EodBar(day=day, close=raw, currency=currency, open=px("open"), high=px("high"), low=px("low"),
                     volume=None if vol is None or vol <= 0 else (vol / f).quantize(Decimal(1), ROUND_HALF_EVEN),
                     adjusted_close=None if ac is None else ac.quantize(Q4, ROUND_HALF_EVEN),
                     adjustment_factor=(ac / raw).quantize(Q6, ROUND_HALF_EVEN) if ac is not None and raw > 0
                     and ac > 0 else None)
        if day in by_day:
            dropped["duplicate_day"] += 1
        by_day[day] = bar
    bars = [by_day[d_] for d_ in sorted(by_day)]
    return EodSeries(symbol, True, bars, currency, None if bars else "no daily bar in the range",
                     {k: v for k, v in dropped.items() if v},
                     {"splits": [{"date": d_.isoformat(), "ratio": format(r, "f")} for d_, r in sorted(splits)],
                      "dividends": sorted(dividends, key=lambda x: x["ex_date"])})


class YahooChartProvider(EodPriceProvider):
    spec = SPEC

    def symbol_for(self, identifiers: dict) -> str | None:
        return ticker_symbol(identifiers, class_sep="-")

    @staticmethod
    def real_transport() -> RealHttpGet:
        return RealHttpGet(headers={"User-Agent": USER_AGENT, "Accept": "application/json"})

    async def fetch_eod(self, symbols: list[str], start: date, end: date) -> dict[str, EodSeries]:
        now = self._now()
        out: dict[str, EodSeries] = {}
        for sym in symbols:
            params = {"period1": str(_epoch(start)), "period2": str(int(now.timestamp()) + 86400), "interval": "1d",
                      "events": "div,split", "includeAdjustedClose": "true"}
            status, body = await self.http.get(url_for(sym), params)
            if status == 404:
                try:
                    out[sym] = parse_chart(body, sym, start, end, now=now)
                except EodSourceError:
                    out[sym] = EodSeries(sym, False, reason=f"Yahoo answered 404 for {sym}")
                if out[sym].found:   # a 404 carrying a chart is not a format we know
                    raise EodSourceError(f"yahoo_chart: {sym}: HTTP 404 with a chart body")
                continue
            if status != 200:
                raise EodSourceError(f"yahoo_chart: {sym}: HTTP {status}"
                                     + (" (Yahoo refuses this network address)" if status in (401, 403) else ""))
            out[sym] = parse_chart(body, sym, start, end, now=now)
        return out
