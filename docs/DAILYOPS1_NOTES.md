# DAILYOPS1 — notes

Two production defects that stopped the platform's daily work. Branch `dailyops1-work`, 2026-11-12.

## 1. Discovery (Task 1) — every fact re-read in code at HEAD d0f7eb4, live DB read read-only

### A — Market Indicators page: "Market data could not be loaded."

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| A1 | The page GETs `/api/market/catalog` → `GET /market/catalog` → `build_catalog`, which lists EVERY active security and colours it with `palette.assign` → `palette.ramp` | CONFIRMED | `apps/api/routers/market_data.py:51` (`market_catalog`) → `services/market_data/read_service.py:334` (`build_catalog`) → `read_repository.py:133` (`catalog_securities`: all `securities_global` rows with `valid_to`/`system_to` NULL and `merged_into_id` NULL, LEFT JOIN the active series) → `read_service.py:378` `palette.assign(sec_groups, …)` → `palette.py:99` → `palette.ramp` (`palette.py:63`) |
| A2 | `ramp` raises once a group is large | CONFIRMED, CODE | `palette.py:85-86` `raise RuntimeError("palette: could not find a distinct shade")`. An 8-bit colour has only about 300 distinct shades of one hue and saturation inside the lightness clamp. Measured on every base colour: the largest count that works is 297 (navy) to 309 (teal); NEUTRAL `#64748B` = 300. The live master has equity 8,666 · fund 6,133 · other 917 on NEUTRAL, so every catalog request raises → 500 → the page's fallback message. |
| A3 | Only securities linked to an ACTIVE series can be selected; the other ~16,000 rows are sent as `selectable:false` | CONFIRMED (design defect) | `read_service.py:380-392` — `selectable = series_key is not None`, `unselectable_reason = "no_price_history"` |

**Callers of `palette` / `catalog_securities`:** there is only one. `read_service.build_catalog` calls `palette.assign` twice: for indicator series (grouped by category; at most a few dozen per category, safe) and for securities (the crash). `grid`, `correlations`, `series` and saved views (`saved_views.py`) never call the palette or `catalog_securities`; they resolve keys against `indicator_series` only. Saved views resolve security keys with their own query (`saved_views.py`), not with the catalog.

**Existing assertion that changes with the contract:** `verify_mkt03` C5 ("securities lists every active, non-merged security-master row") and C7 ("every other security is unselectable … and there is at least one") encode the old design — the design that crashes. The sprint changes the contract: the catalog lists ONLY selectable securities; the unselectable rows (with `no_price_history`) are reached through the new search route. C5/C7 are restated to the NEW contract with the same strictness (exact count against SQL; every unselectable security found by search carries the reason). This is a contract change, not a weaker check.

### B — the nightly AI run: `stopped_error` every night

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| B1 | 8 `ai_daily_runs` rows, all `note_terms_extraction`, all `stopped_error`, 0 documents | CONFIRMED (live, read-only) | `SELECT task_key, status, count(*) FROM public.ai_daily_runs GROUP BY 1,2` → `note_terms_extraction / stopped_error / 8` |
| B2 | `spend_cap()` returns `1e9` when unset → written to `numeric(12,6)` → overflow before any document is read | CONFIRMED, CODE | `services/ai_daily_runs.py:102-107` (`return … 1e9`), `:171` (`spend_cap_usd=spend_cap()`), `note_extraction/runner.py:114-115` (`store.create_run(…, spend_cap_usd=…)`), `note_extraction/store.py:106-116` (`Decimal(str(spend_cap_usd))`). Live column: `portfolio.note_extraction_runs.spend_cap_usd numeric(12,6) NOT NULL`, CHECK `spend_cap_usd >= 0`. So "store NULL" needs `DROP NOT NULL` (the CHECK already passes NULL). |
| B3 | `replica_terms_extraction` has never recorded a run | **NO-WORK, not CODE** | `portfolio.replica_securities` has **0 rows** (every `replica_*` table that holds per-fund work is empty: securities, source documents, term readings, versions). `_replica_queue` (`ai_daily_runs.py:183`) selects funds of live registry entries in non-test orgs — the empty set — so `daily_tick` returns `{"action": "queue_empty"}` for the job and, by design, writes no run row (`ai_daily_runs.py:503-505`). The config loads (`replica_terms.active_ensemble`, the same pool path the note job uses successfully every night) and the job is registered (`JOBS`, `DAILY_TASK_KEYS`). The job will run the first night after someone adds a fund to a Replication Studio registry in a real org. |

**Every writer of `spend_cap_usd`:**

| Writer | Column | Defect? |
|---|---|---|
| `ai_daily_runs._note_read` → `runner.run_notes` → `store.create_run` | `note_extraction_runs` `numeric(12,6) NOT NULL` | YES — the 1e9 sentinel (B2) |
| `note_extraction/prefill.py:319` `run_prefill` → `store.create_run` | same | Latent: `--spend-cap` is required, but a value ≥ 10^6, NaN or ∞ would overflow / be refused only by Postgres. Fixed in `store.create_run` (one check for every caller). |
| `scripts/run_note_extraction_pilot.py`, `run_note_extraction_eval.py`, `prefill_gold.py` → `store.create_run` | same | Same latent defect; same fix. |
| `services/edgar_inventory.py:934` `create_run` | `edgar_inventory_runs` `numeric` (unbounded) `NOT NULL` | NO overflow (no precision). `--spend-cap` is required by `run_edgar_inventory.py` before a run row is written. Not changed. |

**Public-data guard (B5) — what exists today:**

* Model side: `services/model_catalog.py` `PUBLIC_DATA_TASK_KEYS` = {note_terms_extraction, edgar_inventory, replica_terms_extraction}; `assert_model_allowed_for_task` / `refuse_public_data_only` refuse a `public_data_only` model (live: `gpt-5-mini`, `gpt-5-nano`) for any other task or any org-scoped call; the picker refuses it (`ai_ensembles.py:126`); `held_away._refuse_sharing_models` refuses it at the statement call. `statement_positions` is in `ai_daily_runs.NEVER_SCHEDULED`.
* Document side, at the database: `portfolio.reference_filings` (the note corpus) CHECKs `retention_classification = 'public_reference'` and `form_type IN (424B2, FWP, 424B3, 424B5)`; `portfolio.replica_source_documents` (the prospectus store) CHECKs `owner_scope = 'platform' AND org_id IS NULL` and `form_type IN (497K, 485BPOS, 497)`.
* Missing: nothing in the DAILY RUN itself refused a job that is not a public-data job, and the note queue did not state the corpus's public classification. Smallest addition (Task 4): `drive()` refuses any job whose task key is not in `PUBLIC_DATA_TASK_KEYS` before it selects a single item, and `_note_queue` keeps only `public_reference` filings.

## 2. Fixes

| Defect | Verdict | Fix | Where |
|---|---|---|---|
| A2 palette raises on a large group | CODE | `palette.ramp` never raises: the mkt03 distinct algorithm unchanged (every size that fitted keeps its exact colours); when the band is full it cycles deterministically through the largest distinct ramp that fits (≤ `CYCLE_MAX` = 256, halved until it fits) | `services/market_data/palette.py` |
| A3 catalog sends ~16,000 unselectable rows | CODE (design) | `catalog_securities` = INNER JOIN on an ACTIVE series (selectable only); new `GET /market/securities/search?q=` (2–100 chars, name / short name contains, ticker / CUSIP prefix, ≤ 25 rows, priced first; same gate + envelope; extra / duplicate / short / long / control-character params refused without echo); colours shared by catalog and search (`security_colors`); the catalog publishes `vocabularies.search` | `read_repository.py`, `read_service.py`, `routers/market_data.py` |
| A — the page | CODE | the existing SelectionPanel gains a search box (limits from `vocabularies.search`; no box without it — fail closed); priced results use the existing `SeriesRow`, unpriced ones the existing disabled row with `no price history`; errors show the server's message; forward `/api/market/securities/search` | `components/market/SelectionPanel.jsx`, `lib/market/catalogModel.mjs`, `lib/market/marketRoutes.mjs`, `app/api/market/securities/search/route.js` |
| Other palette / catalog callers | none | only `build_catalog` calls the palette; grid / series / correlations / saved views never do | — |
| B2 no-cap sentinel overflows | CODE + schema | `spend_cap()` returns None when unset; a configured cap is checked by `spend.checked_spend_cap` (finite, ≥ 0, ≤ 999999.999999) and the note job stops at its start with the reason; `store.create_run` checks every caller's cap before the INSERT; `SpendTracker(cap_usd=None)` = no dollar cap; `spend_cap_usd DROP NOT NULL` (docs/dailyops1_part1.sql, APPLIED 2026-11-12 via `_apply_dailyops1.py`) | `ai_daily_runs.py`, `note_extraction/spend.py`, `store.py`, `runner.py` |
| Other `spend_cap_usd` writers | latent → fixed | prefill / pilot / eval go through `store.create_run` (checked); `edgar_inventory_runs.spend_cap_usd` is unbounded `numeric` and its CLI requires a cap — no change | — |
| B3 replica job never ran | NO-WORK | asserted in the verify: no live registry fund in any real org → empty queue → `queue_empty`, no run row | — |
| Public-data guard | CODE (small) | `ai_daily_runs.public_job_refusal`: `drive()` refuses any job not in `model_catalog.PUBLIC_DATA_TASK_KEYS` (or in `NEVER_SCHEDULED`) before it loads a config or selects an item; `daily_tick` skips it (`not_public_data`); the note queue keeps only `public_reference` 424B2 / FWP filings | `ai_daily_runs.py` |

## 3. Verify-script changes in older verifies (none weakens an assertion)

* `verify_mkt03` C5 / C7 — restated to the new contract (C5: the catalog lists exactly the selectable securities, count by SQL; C7: no unselectable security in the catalog). The old C5/C7 asserted the design that crashed; the `no_price_history` reason on every unselectable security is proved on the search route (verify_dailyops1 [C]).
* `verify_mkt04a` / `tests/market/marketRoutes.test.mjs` — the exact route set is now twelve (the security search forward); only `series` and `securitySearch` pass a query string.
* `verify_mkt04b` — its "nothing else in read_service.py changed" diff is pinned to mkt04b's OWN commit (dfd5fe2) instead of the working tree (the `_verify_self` rule; it was self-contaminating with any later sprint's edit).
* `verify_mkt04c` — runs the new `dailyops1Render.test.mjs` suite too ("every suite on disk is run"); admits `searchUrl` only because SelectionPanel builds it with `securitySearchUrl`.
* `verify_mkt03`, `03b`, `04a`, `04b`, `04c` — new `--self-checks-only` (static / pure sections, standard `TOTAL: N PASS, M FAIL` line) so the fast regression mode can run them; their full modes are unchanged.
* `verify_aibudget1` — its fixture daily-job task keys are registered as public-data task keys for its own process (setup only), because the daily run now refuses non-public-data jobs.

## 4. [FIND]s

* `verify_mkt04a`'s three navigation checks (Sidebar `NAV_ITEMS` / `MENU_ITEMS` with `/market`) have failed since navregroup (e97718f, 2026-10-20) moved the menu to one definition; `/market` is still in the menu (`lib/menuVisibility.mjs` `main(50, "/market", "Market Indicators", …)`). Pre-existing, not touched here; reported by verify_dailyops1 [J] as pre-existing.
* The catalog's cold first call took ~3.5 s from WSL against the remote database (series bounds over ~300k observations); warm ~0.2 s. The securities part is ~0.04 s now.
* `ai_daily_runs` writes no row when a job's queue is empty (`queue_empty`), so an idle job is invisible in the run history — by design (aibudget1), and why B3 looked like a failure. The admin overview shows the queue size.
