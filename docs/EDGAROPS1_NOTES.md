# EDGAROPS1 — notes

Branch `edgarops1-work`. (A) nightly capped download of NEW selected filings, (B) an
Institutions table that adds up + run summaries, (C) UIT ETFs located by CIK.

## Task 1 — discovery (2026-11-13, code + live DB, read-only)

### [FIND] 1. A nightly EDGAR download ALREADY runs — and it downloads the backlog

The prompt's facts say bulk downloads ran only on the laptop and that the 529k
`selected` backlog must not be downloaded by a nightly. The live database says
otherwise:

* Workflow trigger **"EDGAR pipeline — nightly"** (`public.workflow_triggers`
  `8d665425-…`, Hollisworks org, cron `0 6 * * *` UTC, created 2026-10-02,
  `occurrence_count` 1 — it fired for the FIRST time on 2026-11-13 06:00 UTC).
* It launched `services.edgar_pipeline` as a Render one-off job on the
  workflow-scheduler cron service (`render_service_id crn-daln4obl550s73brmiq0`),
  run row `bc2e4ee6-…`: `trigger_source 'nightly'`, `run_kind 'fetch'`, stages
  discover → select → fetch, **fetch_cap 5000**, 06:01 → 08:18 UTC.
* Result: discovered 742 new (2026-11-12), selected 570 — then **fetched 5,000**
  (ready_for_extraction 2,123 · not_pricing_supplement 2,673 · prefilter_skipped
  204 · failed 0), 238 MB to R2, 15,123 SEC requests. Its fetch queue is
  `edgar_pipeline._queue`: `selected` (+ due `fetch_failed`), **newest filing date
  first**, so ~4,430 of the 5,000 were BACKLOG.
* That is why the live counts no longer match the prompt's "confirmed" numbers:
  2026-11-13 read: selected **524,442** (prompt 528,934), not_selected 182,257,
  not_pricing_supplement 17,712, ready_for_extraction **8,012** (prompt 5,885),
  prefilter_skipped 2,290, fetched 200, fetch_failed 1. Laptop bulk runs on
  2026-11-12 19:30 (1,107 done) and 2026-11-13 03:12 (62 done) also moved them.
* It also explains why the 10:30 scheduled refresh found **0 new** on 2026-11-13:
  the 06:00 nightly had already discovered 2026-11-12. (2026-11-12's 0 is
  Veterans Day — EDGAR published no index for 2026-11-11.)

NOT CHANGED BY THIS SPRINT (live config, an operator decision): if the backlog
really must not be downloaded, deactivate that trigger (Workflow Manager →
"EDGAR pipeline — nightly" → off) or set `EDGAR_PIPELINE_FETCH_CAP` low. The
new step below coexists with it either way: it never lists a filing the nightly
already fetched (it lists `selected` rows only), it skips rows another runner has
claimed, and its SEC rate is divided by the live-runner lease count, which
includes the nightly's `edgar_pipeline` lease.

Consequence for "new": a definition of "new = first indexed by the 10:30
scheduled refresh" would list NOTHING on every day the 06:00 nightly ran first.
So "new" is defined by the index row's own insert time instead (below).

### 2. Definition of "new" (asserted in the verify)

A filing is NEW for the nightly download when

    pipeline_status = 'selected'
    AND loaded_at >= the switch-on cutoff
    AND form_type ∈ the source's form types AND primary_issuer_cik ∈ the source's catalog CIKs
    AND it is not already listed by a scheduled download run that is still queued or running

`loaded_at` is `edgar_index_filings.loaded_at` (DEFAULT now() at INSERT, never
updated) — the moment the filing was FIRST indexed, by whichever refresh did it
(10:30 schedule, 06:00 nightly, screen, CLI). The switch-on cutoff is recorded on
the run rows themselves: the first scheduled download run stores
`details.auto_download.new_since` = its day at 00:00 UTC, and every later day
uses the earliest `new_since` of the scheduled download runs on or before its own
day (fixture runs live in 2001 and can never move it). `EDGAR_AUTO_DOWNLOAD_SINCE`
(YYYY-MM-DD) overrides it. Everything loaded before the cutoff is BACKLOG and is
never listed. Today the newest backlog rows were loaded 2026-11-13 by the 06:00
nightly, so the first day's cutoff excludes all of them.

Order: oldest first-indexed first (`loaded_at, filing_date, accession_number`) —
FIFO, so a carried-over filing is listed ahead of the next day's new ones and can
never be starved by a run of busy days.

### 3. The queue is the hand-off

The day's list (≤ cap) is ONE `bulk_fetch` run in the existing queue
(`portfolio.edgar_pipeline_runs`, `trigger_source 'schedule'`, `params.accessions`
= the list, `details.auto_download` = date, cutoff, counts, carried-over, waiting
reason). Once per day by a partial unique index on
`details->'auto_download'->>'date'` (same pattern as
`edgar_pipeline_runs_schedule_day_uq`). `params.accessions` narrows the SAME
Runner scope (`edgar_bulk._SCOPE_WHERE`, one extra `$7` term, NULL for every
other run) — the per-filing routine is the Runner's, unchanged.

* Render can run it (R2 credentials + a valid bucket + EDGAR_USER_AGENT present on
  the scheduler, and today's index refresh succeeded = SEC reachable): target
  `render`; each tick claims it with the queue's own `claim_next_run`
  (`restrict_to` = the scheduled runs), runs the Runner until the chunk deadline,
  then puts it back to `queued` (or closes it when no listed filing is left).
* Render cannot: target `laptop`, status `queued`, `details.auto_download.waiting`
  = the reason. `run_bulk_fetch.py --worker --target laptop` claims it like any
  other queued run. If Render later can, the tick re-targets waiting scheduled
  runs to `render` and works them.

### 4. Chunk bound

`EDGAR_AUTO_DOWNLOAD_TICK_SECONDS`, default **120 s** of fetching per tick. The
tick already carries the prices slice (≤ 180 s) and the daily-reading slice
(`AI_DAILY_RUN_SLICE_SECONDS` 240 s); Render cron never overlaps runs (a running
job delays the next one) and its hard limit is 12 h, so the bound is about not
starving the steps after it and not delaying the next 5-minute tick much, not
about a kill. In-flight filings finish after the deadline (each ≤ 4 SEC requests
+ backoff ≤ 60 s). At SCHEDULE_MAX_RPS = 2 (divided by live runners) a chunk does
~80–120 filings (peek skip ≈ 2 requests, a kept final ≈ 4); 2,000 filings ≈
20–25 ticks ≈ 2 hours after 10:30 UTC.

### 5. R2 credentials on the cron service

`render.yaml` declares `R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY /
R2_BUCKET_NAME` (all `sync: false`) on `2ndactcapital-workflow-scheduler`; their
values come from that service's Doppler sync (project hollisworks, config prd).
Doppler prd has all four (names checked only) and `R2_BUCKET_NAME` passes
`edgar_pipeline.r2_bucket()` (it is the corpus bucket). Strong live evidence they
reach the cron service: the 06:00 nightly ran as a one-off job ON that service
and uploaded 238 MB to R2. The step still checks at run time
(`can_render_download`) and records the reason on the run if anything is missing.

### 6. Status mapping for the Institutions table (all 12 CHECK values)

| column | pipeline_status |
|---|---|
| To fetch | `selected` |
| Downloaded | `fetched`, `ready_for_extraction`, `extraction_submitted`, `extracted`, `needs_review`, `extraction_failed` |
| Skipped | `not_pricing_supplement` (peek or full text), `prefilter_skipped` |
| Failed | `fetch_failed` |
| not eligible (never offered to a fetch) | `discovered`, `not_selected` |

Eligible = To fetch + Downloaded + Skipped + Failed. Stored = Downloaded rows whose
reference_filings row still has an R2 key. % done = (Downloaded + Skipped +
Failed) / Eligible. Year columns = To fetch by filing year (latest 3 present) +
Earlier = To fetch before them, so the year columns sum to To fetch.
The old `downloaded` / `stored` payload fields (bulkfetchui3: "handled") stay, as
`handled` / `handled_stored` — same set, same numbers.

### 7. The reader's pick order (reported, not changed)

`services.ai_daily_runs._note_queue` (note_terms_extraction, 00:05 UTC):
1. Model-2-only re-reads (readerfix1), newest first;
2. then `selection.ready_notes` — every `ready_for_extraction` row with a
   reference filing, public_reference 424B2/FWP only, no staging row yet —
   ordered by `selection.stratify`: round-robin over (issuer group, filing year)
   strata sorted alphabetically by issuer then year, each stratum in
   reference_filing_id (UUID) order. So it is NOT newest-first: every
   issuer × year (2019 …) gets one document per round.

Throughput at today's budgets: the 2026-11-13 scheduled run read **36
documents** (36 calls per model; ~12.1k input tokens each) before gpt-5-nano's
500,000 tokens/day stopped it (481,065 used). At ~36/day the 8,012 ready
documents are ~220 days of reading, and every day adds hundreds (the 06:00
nightly alone added 2,123). The download side is no longer the bottleneck;
Joe's priority rule (e.g. newest first) matters more than the download cap.

## Task 3b — unit investment trust ETFs (SPY / DIA / MDY / QQQ)

* Live (read-only): SPY's two registry entries (draft + active) carry `sec_cik`
  NULL and `sec_series_id` NULL; the security master holds only ticker / CUSIP /
  ISIN / FIGI for all four (no CIK identifier type exists). The add step's
  lookup (`replica_holdings.resolve_fund` → `sec_fund_ids`) reads only
  `company_tickers_mf.json`, where a UIT is absent — so the add step had nothing
  to store.
* [FIND] The platform's own EDGAR index cannot answer "which prospectus forms
  does SPY file": it indexes 424B2 / FWP only (`edgar_index.FORMS`, plus the
  sources' forms) — zero rows for SPY's CIK. The form choice below is therefore
  from how a UIT registers, not measured on SPY (no network in this sprint).
* CIK: `replica_holdings.sec_trust_cik` reads `company_tickers_exchange.json`
  (the file the security master's `sec_tickers` provider already reads; a UIT is
  its own registrant and is listed there), cached a day. Asked ONLY when the
  mutual-fund file has no row for the ticker, so an ordinary fund's series path
  never changes. `resolve_fund` now returns `{"cik", "series_id": None,
  "class_id": None, "uit": True}` for a UIT, so a NEW entry stores its CIK; an
  EXISTING entry (SPY's) resolves it at extraction time.
* Prospectus by CIK (`replica_terms.locate_uit_prospectus`), newest of, in order:
  **497K** (summary prospectus) → **485BPOS** (the full prospectus in the
  trust's annual post-effective amendment) → **497** (a definitive prospectus).
  Not S-6: it is the trust's initial registration (1990s for these four; every
  later prospectus is a 485BPOS amendment of it) and
  `replica_source_documents_form_chk` accepts only 497K / 485BPOS / 497. Located
  through the same EDGAR atom feed (`CIK=` takes a CIK or a series id). Same
  storage, same readers, same fields. None of the three → `ExtractionBlocked:
  SPY is a unit investment trust (CIK …) with no 497K, 485BPOS, 497 prospectus
  on EDGAR`; no CIK → `… no SEC series id and no trust CIK in the SEC ticker
  files …`.
* Holdings: an issuer file still wins (SPY / DIA / MDY: `ssga_spdr`). With no
  issuer file (QQQ), `choose_source` now offers N-PORT for a UIT with a trust CIK
  and `fetch_holdings` reads the trust's newest NPORT-P by CIK (ETFs organised
  as UITs file N-PORT); none → `NoHoldingsSource: no holdings source for QQQ: no
  issuer file lists it and its trust (CIK …) has no NPORT-P filing on EDGAR`.
* Retry: the nightly fund-terms queue (`ai_daily_runs._replica_queue`) takes
  every live fund with no model reading under the current prompt + models; a
  blocked run writes no reading, so SPY was already re-offered every night (and
  blocked each time). After the fix the next nightly run reads it; the queue
  item now carries the entry's `sec_cik` too.

## Proof log — fix 2 (2026-11-13)

Earlier legs: t 34/0, u 25/0, s 14/0, f 16/0, w 21/0. This leg, one section at a time (`timeout 600 … --section X`):

* q (queueing / once per day / new vs backlog / cap 2,500 → 2,000 + 500): **31 PASS, 0 FAIL** (teardown zero rows).
