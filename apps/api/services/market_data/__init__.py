"""Platform-global market and macro indicator history (mkt01).

Reference data, not tenant data: no org_id anywhere in this package. Separate
from security pricing (portfolio.securities_global_prices) on purpose — see
docs/MARKET_DATA_DESIGN_V1.md.

  registry.py — seed-file loader for market_data.indicator_series
  fred.py     — FRED HTTP client (injectable transport, throttle, retry, scrub)
  ingest.py   — validate + backfill + ingest_series (the one write path),
                Rule 3 valid-axis restatement, race-safe under overlap
  adapters.py — provider-adapter registry (fred, yahoo), scrub_error  (mkt02)
  yahoo.py    — Yahoo chart adapter (injectable transport, Decimal-only)  (mkt02)
  nightly.py  — run_nightly: one batch, per-series isolation, summary row  (mkt02)
  staleness.py — stale_series(rows, today), first-pass limits  (mkt02)
"""
