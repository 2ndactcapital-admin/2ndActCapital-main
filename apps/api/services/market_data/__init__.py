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

Read side (mkt03) — read-only, request connection, never platform_scope():
  read_repository.py — the read queries (series, observations, stats, securities)
  transforms.py      — as_of, index, sigma, level, yoy, mom (pure, Decimal)
  resample.py        — frequencies, resample, coarser_frequency (pure)
  correlation.py     — Pearson over changes, lag, ordering (pure)
  palette.py         — server-side category/series/security colours (pure)
  access.py          — THE read gate + permissions envelope
  read_service.py    — catalog / series / grid / correlations, request parsing
"""
