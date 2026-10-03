"""Platform-global market and macro indicator history (mkt01).

Reference data, not tenant data: no org_id anywhere in this package. Separate
from security pricing (portfolio.securities_global_prices) on purpose — see
docs/MARKET_DATA_DESIGN_V1.md.

  registry.py — seed-file loader for market_data.indicator_series
  fred.py     — FRED HTTP client (injectable transport, throttle, retry, scrub)
  ingest.py   — validate + backfill, Rule 3 valid-axis restatement for points
"""
