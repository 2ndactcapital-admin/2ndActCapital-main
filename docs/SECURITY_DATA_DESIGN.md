# Security data layer — design (MARKETDATA1, 2026-10-28)

One vendor-neutral layer for **security** data: end-of-day prices, corporate
actions, fund holdings snapshots, index levels / rates, option quotes (fx is a
reserved type with no source yet). Readers ask for a **data type**; they never
name a vendor. No paid vendor is connected. A vendor is a plug-in behind the
same interface, switched on by configuration (Joe, 2026-10-28: do not
architect around one vendor, do not pay for one until a real use case needs it).

This is **separate on purpose** from the mkt01–03 indicator history
(`services/market_data/`, `docs/MARKET_DATA_DESIGN_V1.md`): that store, its read
gate (`market_data/access.py`) and its nightly (`hollisworks-market-data-nightly`)
are unchanged. The layer reuses `adapters.scrub_error`, `fred.FredClient`
(injectable transport, `Throttle`, retry statuses) and
`ingest.parse_observations` as-is.

## Code

| Module | Role |
|---|---|
| `services/security_data/layer.py` | `get`, `get_bulk`, `history` — THE read path (the rank walk) |
| `services/security_data/registry.py` | providers, ranks, entitlements, **the licence rule** |
| `services/security_data/settings.py` | staleness per type, disagreement / outlier / gap settings |
| `services/security_data/quality.py` | pure quality rules (both edges provable without a DB) |
| `services/security_data/writes.py` | Rule-3 writes, call log, budgets, quality events |
| `services/security_data/uploads.py` | CSV uploads (rep3 machinery, maker-checker) |
| `services/security_data/nightly.py` + `scripts/security_data_nightly.py` | the nightly run |
| `services/security_data/admin.py` + `routers/security_data.py` | admin screens' API |
| `services/security_data/providers/<key>.py` | one module per provider |

## Stores (schema `docs/marketdata1_part1.sql`, applied)

| Data type | Store |
|---|---|
| eod_price | `portfolio.securities_global_prices` (+ provider_key, as_of, quality_flags, licence, org_id, upload_id; the live-point unique gains provider + org) |
| corporate_action | `portfolio.securities_global_corporate_actions` (Portfolio F's table + the same columns; `cash_dividend` / `stock_dividend` admitted — recorded, never applied) |
| index_level | `portfolio.index_levels` (new) |
| fund_holdings | `portfolio.replica_holdings_snapshots` (rep1; + provider_key, licence) |
| option_quote | `portfolio.adviser_quotes` (rep8, unchanged) |
| eod_price (custodian) | never stored — read from `portfolio.positions` at the call |

Registry tables: `data_providers` (platform), `data_source_rank` (platform
default + org override), `data_provider_entitlements` (org), `data_uploads`
(org, system axis), `data_quality_events`, `provider_call_log` (insert-only),
`security_data_runs` (insert-only summary). Every table has a policy for every
operation; an org's uploaded rows are visible to that org only.

## Providers today

| Key | Kind | Types | Internal | Members | Notes |
|---|---|---|---|---|---|
| custodian | custodian | eod_price | yes | own holdings only | market value ÷ quantity on the position date |
| csv_upload | csv_upload | eod_price, corporate_action, index_level | per file | per file | the uploader's licence governs |
| adviser_entry | adviser_entry | option_quote, index_level, eod_price | yes | no | rep8 quotes (premium / index_level / underlying_price) |
| platform_entry | adviser_entry | eod_price | yes | no | A1 `add_price`; rows with no provider read as this |
| ssga_spdr | issuer_file | fund_holdings | yes | no | rep1 parser re-homed |
| sec_nport | regulator_filing | fund_holdings | yes | yes | rep1 N-PORT re-homed |
| fred | public_api | index_level | yes | no | Treasury curve + fed funds, existing FRED key |
| vendor_slot | vendor | (all) | no | no | **not connected**, no network, in no rank |

Platform rank: eod_price custodian → csv_upload → adviser_entry → platform_entry;
corporate_action csv_upload; fund_holdings ssga_spdr → sec_nport; index_level
fred → csv_upload → adviser_entry; option_quote adviser_entry.

## The rank walk

For each subject, providers in rank order (the org's override for that type
if it has one, else the platform's). A provider is **skipped**, with its reason
in `tried`, when: not active; needs an entitlement the org lacks; no module or
capability; its licence (or the value's own licence) does not allow the
purpose; or its value is stale. The first allowed, fresh value wins. Nothing →
`found=False`, `missing` names the data type and every source's reason. **A
price is never invented.** `history()` returns one source's series, never a
splice of several.

## The licence rule

`purpose` is `internal` (adviser analysis) or `member_display`. Internal needs
`internal_use`. Member display needs `member_display = allowed`, or
`own_holdings_only` **and** a value the provider marked as the viewing member's
own holding (the custodian marks it only when the read is scoped to that
member's `account_ids`; unscoped member reads get nothing). A file upload's
value is judged by the file's licence. Recorded per provider: internal use,
member display, redistribution, retention after cancellation, attribution,
terms note + confirmed_on, daily call budget.

## Quality rules

- **stale**: age > the type's limit (eod_price / index_level 5 days, option_quote 1,
  fund_holdings 35; corporate actions never). Exactly at the limit is fresh.
  Setting `security_data.stale_days.<type>`.
- **gap**: more than `security_data.gap_weekdays` (3) weekdays with no point
  between two points.
- **outlier**: |daily move| > `security_data.outlier_multiple` (10) × the median
  |daily move|, with at least 20 moves.
- **disagreement**: another allowed source's value for the same date differs
  from the winner by more than `security_data.disagreement_pct` (2%). The
  winner is kept and flagged.
- **budget_exhausted**: a provider's calls today reached its daily budget. The
  nightly passes the items it could not fetch to the next fetching provider in
  the rank.

## The nightly

`scripts/security_data_nightly.py`: refresh within budgets → platform series
quality → org quality for securities on open proposals (test orgs skipped) →
to-dos for stale / disagreeing items → one summary row. Fund holdings stay on
rep1's own daily schedule. **Not yet scheduled on Render** (operator step).

## Add a vendor — the 5 steps

1. **Module**: copy `providers/vendor_stub.py` to `providers/<key>.py`. Implement
   `read()` over the canonical stores (`providers/store.py`, `provider_key = <key>`)
   and `fetch()` over `HttpTransportBase` (the transport is injected; scrub every
   error). Set `connected = True`.
2. **Code registry**: add one line to `providers/__init__.py::build_providers`.
   Put its secret in Doppler, and add the variable name to `adapters.SECRET_ENV_NAMES`
   so it is scrubbed.
3. **Registry row**: insert a `data_providers` row with kind `vendor`, the contract's
   licence facts (internal use, member display, redistribution, retention after
   cancellation, attribution, terms note, confirmed_on), the daily call budget, and
   `requires_entitlement = true`. Then add `data_provider_entitlements` rows for the
   orgs the contract covers.
4. **Rank entry**: Super Admin → Security data → set the platform rank for each type it
   supplies. Orgs may reorder only within that rank.
5. **Prove it**: a verify with recorded fixtures through the injected transport: the
   rank walk picks it up, its licence rule holds for both purposes, and
   budget fallback works. Then switch its status to `active` (the API refuses
   this while the module is not connected).

No reader changes. Readers ask for the data type.
