"""One module per provider. ``build_providers()`` is THE code registry: a
provider key (a data_providers row) → its module's Provider instance. Adding a
vendor = a new module here + one line below + a registry row + a rank entry
(docs/SECURITY_DATA_DESIGN.md, "Add a vendor")."""

from __future__ import annotations

from typing import Any, Mapping


def build_providers(*, transports: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None) -> dict:
    """Constructing the registry makes no network call. ``transports`` injects
    a fake transport per provider key (tests use recorded fixtures)."""
    from services.security_data.providers import (adviser_entry, csv_upload, custodian, fred, issuer_file,
                                                  platform_entry, sec_nport, vendor_stub)
    # secmaster1 — the five reference-data sources (data type 'reference'); pulled by services.security_master_sync,
    # read through the rank walk from portfolio.security_reference_attributes
    from services.security_data.providers import (finance_database, nasdaq_trader, openfigi, sec_13f_list,
                                                  sec_tickers)
    # SECMASTER2 — registered-fund share classes (company_tickers_mf.json); rank 6 for 'reference'
    from services.security_data.providers import sec_mutual_funds
    # PRICES1 — end-of-day price adapters behind the receptor contract (providers/eod.py). The transport is injected;
    # without one an adapter can still READ the store (the rank walk) but any fetch raises ProviderNotConnected —
    # the prices job / backfill build their own real transport (services.security_data.prices.fetcher).
    from services.security_data.providers import tiingo, yahoo_chart

    t = dict(transports or {})
    return {
        "custodian": custodian.CustodianProvider(),
        "sec_nport": sec_nport.NportProvider(http=t.get("sec_nport")),
        "ssga_spdr": issuer_file.IssuerFileProvider("ssga_spdr", http=t.get("ssga_spdr")),
        "fred": fred.FredProvider(transport=t.get("fred"), environ=environ),
        "csv_upload": csv_upload.CsvUploadProvider(),
        "adviser_entry": adviser_entry.AdviserEntryProvider(),
        "platform_entry": platform_entry.PlatformEntryProvider(),
        "vendor_slot": vendor_stub.VendorStubProvider("vendor_slot", transport=t.get("vendor_slot")),
        "nasdaq_trader": nasdaq_trader.NasdaqTraderProvider(),
        "sec_tickers": sec_tickers.SecTickersProvider(),
        "openfigi": openfigi.OpenFigiProvider(),
        "sec_13f_list": sec_13f_list.Sec13fProvider(),
        "finance_database": finance_database.FinanceDatabaseProvider(),
        "sec_mutual_funds": sec_mutual_funds.SecMutualFundsProvider(),
        "yahoo_chart": yahoo_chart.YahooChartProvider(transport=t.get("yahoo_chart"), environ=environ),
        "tiingo": tiingo.TiingoProvider(transport=t.get("tiingo"), environ=environ),
    }
