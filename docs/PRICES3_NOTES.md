# PRICES3 — lean price of record + restatement history + compatibility view; R2 Parquet archive

Branch `prices3-work`. Sprint prompt: `sprint_prompts/prices3.structural.md`. Joe's decision 2026-11-12: two layers —
(1) the PRICE OF RECORD stays in Postgres in a lean shape; (2) a HISTORY ARCHIVE as Parquet in R2 read with DuckDB.

**The sprint never copies, renames or drops the real `portfolio.securities_global_prices`.** The new objects are
applied EMPTY; the cutover is `apps/api/scripts/prices3_migrate.py`, run by the operator (section 6).

## 1. Discovery (Task 1) — live DB 2026-11-13

### 1a. The table today

`portfolio.securities_global_prices`: 2,923,054 rows, **2,000 MB total = 1,268 MB heap + 732 MB indexes**
(pkey 117 MB, `idx_sec_global_price_current` 201 MB, `idx_sec_global_price_date` 20 MB, `uq_sec_global_price_point`
374 MB, `securities_global_prices_gsid_fk_ix` 20 MB) → **≈ 717 bytes per daily close** (2,000 MiB / 2,923,054; the
prompt's ~680 is the same figure in 10^6 bytes). Where the bytes go (pg_stats avg_width): `licence` jsonb **160**
(the identical Yahoo licence object on every row), `bar` jsonb **109**, `id` 16, `global_security_id` 16, `source` 12,
`provider_key` 12, `quality_flags` 13, four timestamps 8 each, plus a ~32-byte tuple header.

Profile (full scan, 2026-11-13):

| fact | value |
|---|---|
| rows closed on either axis (restated) | **0** (`valid_to` and `system_to` NULL everywhere) |
| org-uploaded rows (`org_id` / `upload_id`) | **0** |
| providers | one: `yahoo_chart` (source = provider_key), price_type `close`, currency `USD` |
| licence values | one object |
| quality_flags | `{}` 2,911,494 · `{outlier}` 11,560 |
| `bar` non-empty | all 2,923,054 |
| `adjustment_factor` non-null | 2,922,540 |
| `valid_from <> system_from` | 0 |
| `as_of <> system_from` | every row (2 % sample: 56,986 of 56,986; up to 81 s apart) — **both must be kept** |
| price scale / bytes | max scale 4, avg 6.9 bytes; adjustment_factor max scale 6, avg 6.1 bytes |
| dates | 2021-11-08 .. 2026-11-12 |
| PG | 17.6 (NULLS NOT DISTINCT, summarizing-index HOT available) |

Constraints: `sec_global_price_adj_factor_chk`, `sec_global_price_positive_chk`, `sec_global_price_type_chk`
(close / intraday_indicative / model), `sec_global_price_upload_chk` (upload ⇒ org + provider csv_upload), FKs to
securities_global and organizations. Nothing references the table's `id` (no inbound FK, no view depends on it).
Grants: postgres all, app_service SELECT/INSERT/UPDATE/DELETE, cpsim_app SELECT. RLS enabled (not forced):

* SELECT `org_id IS NULL OR org_id = NULLIF(app.current_org_id,'') OR app.is_super_admin = 'true'`
* INSERT / UPDATE: super admin OR (org_id NOT NULL AND org_id = current org)
* DELETE: super admin

### 1b. Writers (every one)

| writer | where | axis | columns |
|---|---|---|---|
| `writes.upsert_eod_bars` | receptor ingest (`prices.ingest`) ← backfill (`scripts/prices_backfill.py`), nightly (`prices.nightly`), `prices.prices_tick` | **SYSTEM** (changed close → `system_to = now()` + insert) | all incl. `bar`, `adjustment_factor`, `quality_flags`, `licence`, `as_of` |
| `writes.upsert_prices` | org uploads (`uploads.py` → csv_upload, on the org's own RLS context); `_rep10fix1_probe` | **VALID** (changed → `valid_to = now()` + insert) | gsid, close, provider, org, upload, currency, source, licence, as_of |
| `securities_global.add_price` | Super Admin manual price (`routers/portfolio_securities.py`) → provider `platform_entry` | insert only | gsid, date, price, currency, type, source, as_of; `RETURNING id` |
| verify fixtures | verify_portfolioa1, cpsim02–05, marketdata1, prices1, rep4/rep8… | raw INSERT / DELETE by gsid | old column names |

No `ON CONFLICT` and no `TRUNCATE` against the table anywhere.

### 1c. Readers (every one) and the columns each uses

All filter the LIVE row (`valid_to IS NULL AND system_to IS NULL`):

1. `providers/store.prices` (every store-backed provider through `SD.get_bulk` / `get_series` — yahoo_chart,
   csv_upload, platform_entry, tiingo…): gsid, price_date, price, currency_code, licence, quality_flags, org_id,
   upload_id; filters provider_key (NULL ⇒ platform_entry), close, date range, `org_id IS NULL OR = org`.
2. `prices.latest_dates` (max date per gsid, provider, platform).
3. `prices._context` (last 40 closes before a date, window function).
4. `prices._coverage` (provider_key, count, min/max date per gsid, platform).
5. `prices.refresh_status` (max date per gsid, platform).
6. `nightly.platform_quality` (gsid, provider_key, date, price for a date window, platform, all securities).
7. `tracking.estimate` (DISTINCT gsid priced) and `tracking.progress` (EXISTS live platform close).
8. `security_master_summary` — filter `has_price` EXISTS, the prices block count, the per-row `has_price`.
9. `portfolio_securities` — the asset grid's LATERAL latest platform price (price, price_date, currency_code,
   price_type, source; ORDER BY price_date DESC, **system_from DESC**) and the security detail's last 30 prices
   (**`id`**, price_date, price, currency_code, price_type, source; same order).
10. `fix_sim_pl.last_platform_closes` (DISTINCT ON latest close ≤ as_of).
11. `cpsim/book.platform_price` (latest close ≤ as_of).

Readers that only mention the table in prose: harvest, xray, market_data (separate store), portfolio_securities
docstring.

### 1d. As-of / bitemporal queries

**None in the platform.** No service, router or job reads a closed row (`system_to`/`valid_to` NOT NULL) or asks
"the price as known at T". Only verify scripts look at closed rows (verify_prices1's restatement proof). PRICES3 still
keeps the answer available: the compatibility view reconstructs every closed row exactly, and
`portfolio.price_as_known(...)` answers "as known at T" over it (proven identical to the same predicate on the old
table).

### 1e. The `bar` column

Written by `upsert_eod_bars` only (`EodBar.bar_json()`: open/high/low/volume/adjusted_close). **Read by nobody** —
no service, router, job or front-end file reads it. It moves to the archive only; anyone who later wants OHLCV reads
`services.security_data.price_archive.history_frame(..., field="bar")`.

### 1f. The org-upload path

`uploads.py` (the price sources screen's approved file) → `writes.upsert_prices(None, gsid, pts,
provider_key="csv_upload", org_id=<org>, upload_id=<upload>, conn=<org RLS conn>)`. RLS: an org's rows are visible to
that org only; the reader SQL also says `org_id IS NULL OR org_id = $org`.

### 1g. R2

`services/storage.py`: `get_s3_client()` (boto3, `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`),
bucket `R2_BUCKET_NAME` (memory: Doppler's value was once invalid; the real bucket is `hollisworks-docs`; the archive
takes `PRICES_ARCHIVE_BUCKET` first, then `storage.DEFAULT_BUCKET`).

### 1h. Size math (target ≤ ~100 bytes / row incl. indexes)

Today 717 B/row. Per row: tuple header 24 (+8 when > 8 columns and a NULL is present), line pointer 4, then data.
The lean row (section 2) is 8 columns, so the header stays 24 even with a NULL:

`as_of 8 + recorded_at 8 + series_id 4 + price_date 4 + provenance_id 4 + quality_bits 2 + price ~7 + adjustment
~6 = 43 → 24 + 43 = 67 → MAXALIGN 72 + 4 = 76 B heap` (107 rows / 8 kB page).
Primary key `(series_id, price_date)`: 8 + 8 + 4 = 20 B per entry at 100 % → ~22–23 B at the build's 90 % leaf fill.
`price_date` btree (dedup): ~7 B (today's identical index: 20 MB / 2.92 M = 7.2 B). `recorded_at` BRIN: ~0.
**Estimate ≈ 106 B / row ≈ 310 MB for today's rows (6.7× smaller).** Measured on the verify fixture: section 5.

## 2. Design decisions

### 2a. Lean table (price of record)

* `portfolio.price_series` (int id) — one row per (security, price_type, provider_key, org_id); UNIQUE NULLS NOT
  DISTINCT. Carries `org_id` (NULL = platform). ~2.4 k rows. Holds `sec_global_price_type_chk`.
* `portfolio.price_provenance` (int id) — (provider_key, source, currency_code, licence jsonb, upload_id, org_id),
  UNIQUE NULLS NOT DISTINCT; the 160-byte licence is stored ONCE. Holds `sec_global_price_upload_chk`.
* `portfolio.price_quality_flags` (bit 0..14, name) — `quality_flags text[]` ⇄ `quality_bits smallint`.
* `portfolio.security_prices` — the lean row: `as_of, recorded_at, series_id, price_date, provenance_id,
  quality_bits, price, adjustment_factor`; PK `(series_id, price_date)` = "one live close per (security, date, type,
  provider, org)" (the old `uq_sec_global_price_point`). `recorded_at` = the old `system_from` = `valid_from`.
  Holds `sec_global_price_positive_chk` / `sec_global_price_adj_factor_chk` (same names, same predicates).
* **`org_id` is NOT a column of the lean row** — 8 columns keep the header at 24 bytes. RLS on the lean row (and the
  history row) is `series_id IN (SELECT id FROM portfolio.price_series)`: the series table carries the SAME policies
  as today's table, so a lean row is visible exactly when its series is — one hashed subplan per query, not per row.
* Indexes: PK, `price_date` btree (the nightly quality scan over a date window), `recorded_at` BRIN (the archive's
  daily append). No uuid `id`, no separate gsid index (the series lookup + PK cover it).
* **No partitioning.** Readers 1–5, 9–11 ask "latest close" with NO lower date bound, so a yearly partition set would
  probe every partition on every call; at ~310 MB the whole price of record is one modest btree; the long scans the
  partitioning would help are exactly what the archive takes.
* Physical order: the migration copies **by security, then date** (whole securities per batch), so one security's
  5 years sit on ~12 pages instead of ~1,250 (today's backtest-shaped read touched 4,612 pages for 36,778 rows).

### 2b. Restatements

`portfolio.security_price_history`: the superseded value (price, adjustment, flags, provenance, as_of) with its
`valid_from/valid_to/system_from/system_to` exactly as they were, the original row `id` (uuid), `run_id`, and
`superseded_at`. CHECK: never live (`valid_to IS NOT NULL OR system_to IS NOT NULL`) — which also lets the planner
skip the history branch of the view for live-row predicates (constraint exclusion on UNION ALL). A system-axis
restatement (receptor) writes `system_to = now()`; a valid-axis one (org upload) writes `valid_to = now()`; the lean
row is updated to the new value with `recorded_at = now()`.

### 2c. Compatibility view

`portfolio.securities_global_prices_v3` (security_invoker = true) = lean rows (live, `valid_from = system_from =
recorded_at`, `bar = '{}'`) UNION ALL history rows, joined to series / provenance, flags rebuilt from bits — the old
19 columns, same names, same types, same order. **Swapped in under the old name by the operator script**; until then
it is applied empty. The view's `id` for a lean row is a deterministic uuid of (series, date, recorded_at)
(`portfolio.prices3_row_id`) — the old uuids of live rows are not kept (nothing references them, 1a); history rows
keep their real ids. INSTEAD OF INSERT / UPDATE / DELETE triggers make the view accept every old-shaped write
(verify fixtures, `add_price` in legacy mode): INSERT routes to lean or history, an UPDATE that closes a live row
moves it to history, DELETE removes the lean or history row. `quality_flags` come back in bit order (set semantics —
every real row holds ≤ 1 flag).

### 2d. The switch

ONE switch read at runtime: `portfolio.prices3_migration.store_mode` ('legacy' | 'lean', single row). Writers read
it per write call; the operator script flips it **in the swap transaction** (and back on `--rollback`). Absent table
⇒ legacy.

### 2e. Archive

Platform rows only (org-uploaded prices never leave Postgres — the R2 prefix has no RLS). Parquet under
`prices/archive/v1/eod/year=YYYY/` — `compact.parquet` (one per year) + `append-<stamp>-<id>.parquet` parts. The
nightly prices job step appends rows whose `recorded_at` is past the watermark (`portfolio.price_archive_state`)
with their OHLCV bars (`portfolio.price_bars_pending`, drained once archived); on the first run of a month it
compacts each year that has parts (dedup = latest `recorded_at` per key). Reader
`price_archive.history_frame(gsids, start, end, field=...)` → a date × security frame; DuckDB in-process when
installed, pyarrow otherwise (DuckDB is NOT in the venv and the sprint has no network: `duckdb` is added to
requirements.txt; the verify proves parity on whichever engine is present and says which). R2 is read with boto3 over
the S3 API with the existing R2 credentials (objects cached locally by ETag), so no DuckDB httpfs extension download
is needed at run time.

### 2f. Backtest

`backtest.histories` → `rep10_common.longest_history` → `SD.get_series` → reader 1. Measured on the live table:
30 securities × 5 y = 36,778 rows, an index scan touching 4,612 pages (~2.2 s cold). That is an indexed range read,
not a long scan — and it must see org-uploaded prices and the rank walk, which the platform-only archive cannot.
**Left on Postgres**; the lean layout makes the same read ~350 pages.

## 3. Schema (Task 2) — APPLIED EMPTY 2026-11-13

`services/security_data/price_store.py` `schema_sql(schema, fks=)` holds every object (parameterised by schema so the
verify builds identical objects in a fixture schema). Applied with `python3 apps/api/scripts/_apply_prices3.py --all`
(operator DSN, postgres): first into a scratch schema twice (idempotent, 18/0, dropped), then `portfolio`:
**SCHEMA OK — 19 PASS, 0 FAIL** (the first read-back said 18/1 — a test bug: `relkind` arrives as bytes; cast to text,
re-read 19/0). Read back: 9 tables with their expected rows (flags 1 seed row, migration 1 row, the rest 0), RLS on
all, 4 policies per org/series table and 2 per platform table, the view is security_invoker with the old 19 columns
in order and the old types, the INSTEAD OF trigger, the grants (app_service RW, cpsim_app R), lean indexes = PK +
date btree + recorded_at BRIN, the switch = 'legacy', and **portfolio.securities_global_prices is still the table**.
`_apply_prices3.py` refuses to re-apply once the migration has swapped.

## 4. Writers (Task 3)

ONE switch, read at run time on every write call: `price_store.store_mode(conn)` → `portfolio.prices3_migration.
store_mode` ('legacy' until the operator swap flips it in the swap transaction; table absent ⇒ legacy).

| writer | legacy (unchanged behaviour) | lean |
|---|---|---|
| `writes.upsert_eod_bars` (receptor → backfill, nightly, prices_tick) | old table via `PS.old_name()` | `PS.lean_upsert_eod_bars`: equal close → nothing; changed → history row (system_to = now(), keeps the view id) + lean update; new → lean insert; bar → `price_bars_pending` |
| `writes.upsert_prices` (org uploads, valid axis) | `_restate` on `PS.old_name()` | `PS.lean_restate_point`: changed → history (valid_to = now()) + lean update with the new provenance (upload) |
| `securities_global.add_price` (platform_entry) | old INSERT … RETURNING id | `PS.lean_add_price` → the view's id |

`PS.old_name()` = the old table before the swap and the compatibility view after it, so a legacy-mode write after the
swap (a stale process, a verify fixture) still lands correctly through the view's INSTEAD OF trigger.
