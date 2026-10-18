# BULKFETCH — Task 1 discovery (2026-10-16)

Read from the live database (Supabase project `mmgwmcinimzuhargsazs`) and the code. No SEC, R2 or
model request was made. Wherever a number below was measured, the query or run that produced it is
named.

## 1a. One filing's fetch today: path, requests, where the 1.9 s goes

**Path** (`services/edgar_pipeline.fetch_one`; the EDGAR Pipeline screen's Run button launches a
Render one-off job, `edgar_pipeline_job.py`, which calls `run_job` → `fetch_stage` → `fetch_one`):

1. `edgar_fetch.resolve_filing_documents`. **Request 1:** the folder's `index.json`. The primary
   document is the largest `.htm`/`.html`/`.txt` in it. **Request 2:** `<accession>-index-headers.html`,
   read for the `333-` shelf file number.
2. `edgar_fetch.fetch_filing`. **Request 3:** the primary document. Exhibits are never fetched.
3. In-process steps: sha256 of the raw bytes, gzip level 9 of the raw bytes, text extraction
   (`html_text`, stdlib `html.parser`), gzip of the text, `classify_document`, the keyword prefilter
   and `detect_cusip`.
4. Two R2 PUTs, one after the other: `<…>.gz` and `<…>.txt.gz`.
5. Database writes. The filer-name lookup, the existing-row check and the INSERT reference_filings +
   UPDATE manifest each run in their own `platform_scope` transaction (BEGIN, set_config, statement,
   COMMIT). A cohort run adds a fourth transaction, `mark_selected_by_cohort`.

**Measured.** Gold cohort run `3915068c`, 2026-10-08 15:18:23 → 15:28:11 UTC:
- 308 filings, `sec_requests` 924 (exactly **3 requests per filing**).
- 588.3 s, so **1.91 s per filing**.
- 15,511,736 bytes uploaded, about 50 KB of gzip per filing.

Run `7dc44bff` (500 filings) gives the same figures: 1,500 requests and 1.89 s per filing.

**Where the time goes.** This is an inference, because no per-step timer existed before this sprint:
- **The rate limiter is NOT the bottleneck.** Serial work at 3 requests per 1.9 s is 1.6 requests/s,
  far under the 8/s cap. There is no fixed sleep anywhere on the success path.
- About 0.4–0.9 s is SEC round-trip latency (three serial GETs).
- About 0.2–0.5 s is the two serial R2 PUTs, uploaded from Render (Render bills that outbound
  traffic).
- About 0.3–0.6 s is roughly a dozen Render→Supabase-pooler round trips across 3–4 transactions.
- About 0.1–0.3 s is gzip-9 plus HTML-to-text on a small instance.

The time is dominated by latency, not by the rate cap. That is why parallel workers sharing one 8/s
limiter cut wall-clock time; they do not cut per-filing work. The new runner records seconds per
step (`sec` / `extract` / `upload` / `db`) on every run, so the operator's first run replaces this
estimate with measurements.

**ETA for the ~124,800-filing scope.** Calculated by `edgar_bulk.estimate`.
- **With the peek:** a kept filing costs 4 requests (index.json + peek + document + header), a
  peek-skipped preliminary 2 (index.json + peek). With ⅓ preliminaries that averages 3.33 per filing,
  so 416,000 requests at 8/s = **≈14.4 h**. The latency bound with 6 workers is 11.0 h, so the run is
  rate-bound.
- **Without the peek:** 3 per filing, 374,400 requests = **≈13.0 h**.
- The peek costs about 1.4 h of wall time and saves about ⅓ of the R2 storage and upload (≈8.3 GB
  new instead of 12.5 GB), keeping the corpus under R2's 10 GB free tier.

**[FIND]** The serial path today is 2.7 days. Either bulk mode is ≈13–15 h.

## 1b. Can it run outside the API process? What does it need?

Yes. It already does: the fetch functions are pure async service functions that take a raw asyncpg
connection. Nothing reads a `Request`. `edgar_pipeline_job.py` runs them on a Render one-off job, not
in the API.

They need:
- an asyncpg connection or pool (`statement_cache_size=0`) whose writes set `app.is_super_admin`
  LOCAL (`platform_scope`);
- the R2 credentials `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`, plus a bucket
  from `EDGAR_R2_BUCKET`, else `R2_BUCKET_NAME`;
- `EDGAR_USER_AGENT`. An unset value raises; there is no anonymous default.

All of these were checked by name only (no value printed) in Doppler `prd` today: present. The one
exception is `EDGAR_R2_BUCKET`, which is absent; `R2_BUCKET_NAME` is now a VALID bucket name. (It was
invalid on 2026-10-02; `r2_bucket()` validates it either way.)

**[FIND]** `fetch_one` fused "download" with "store and classify", so a caller that downloads the
document itself (the peek) could not reuse the store path. It is now split: `store_fetched(conn,
meta, raw, …)` is the second half, unchanged, and `fetch_one` calls it.

## 1c. The document_kind classifier

`edgar_pipeline.classify_document` uses **rules only**: it imports no model and makes no network
call. It reads the first `OPENING_CHARS` = 4,000 characters of the extracted text, whitespace
collapsed and lowercased.

Rules, in order:
1. Empty opening → `other` ("no text in the opening").
2. A preliminary marker anywhere in the opening wins outright: `preliminary pricing supplement`,
   `subject to completion` or `preliminary terms` → `preliminary_pricing_supplement`.
3. Otherwise, the title phrase that appears FIRST wins: `pricing supplement` → `pricing_supplement`,
   `product supplement` → `product_supplement`, `underlying supplement` → `underlying_supplement`,
   `term sheet` → `term_sheet`.
4. No title phrase → `other`.

The live CHECK allows exactly these kinds: `pricing_supplement`, `preliminary_pricing_supplement`,
`product_supplement`, `underlying_supplement`, `term_sheet`, `other`.

**[FIND]** There is no "prospectus_supplement" kind. A base prospectus supplement has none of the
title phrases and lands in `other`. The runner's `--kinds prospectus_supplement` therefore selects
`other`, and this is documented in the code. `decide_status` turns every non-pricing kind into the
live status `not_pricing_supplement`. That is the status the peek records for a skipped preliminary.

**Can it run on the first ~16 KB?** Yes. It only reads 4,000 characters of text. The risk is a filer
agent's cover that spends most of its first 16 KB on inline CSS, leaving too little text. The cover
then classifies as `other`, and the runner treats `other` as "unrecognised" and falls through to the
full download, so no filing is lost. The `--peek-test 20` run measures how often this happens; the
verdict line reports "Cover recognised from the first 16 KB: x/20".

## 1d. Why the 200 'fetched' filings were never classified

All 200 are the **2025Q1 reference corpus** from the earlier edgarcorpus sprint:
- status_reason: "pre-existing reference_filings document (2025Q1 corpus), linked without
  re-download";
- filed 2025-01-02 … 2025-03-31;
- `document_kind` NULL and `selection_policy_version` NULL;
- text in the DATABASE (`reference_filings.extracted_text`, `content_encoding` identity), NOT in R2:
  `text_r2_key` is NULL on all 200. These are exactly the 1,058 − 858 rows without a text key.

edgarpipelinea's backfill linked them to the manifest at 'fetched'. `fetch_stage` only ever queues
`selected` and due `fetch_failed` rows, so no stage ever looked at them again. B1's
`selection.corpus_notes` classifies them in memory but deliberately never writes the manifest.

Breakdown:

| Form | Issuer | Count |
|---|---|---|
| 424B2 | include_status `yes` | 169 |
| 424B2 | no listed issuer | 2 |
| FWP | include_status `yes` | 24 |
| FWP | no listed issuer | 5 |

`--reprocess-fetched` classifies them from the database text, so it needs zero SEC requests and zero
R2 reads. The 31 rows the active policy would not have selected (FWP, or no listed issuer) become
`not_selected`, with their document_kind still recorded. The decided-has-policy CHECK requires a
policy stamp.

## 1e. Wells Fargo and Deutsche Bank "misfiled as plain notes"

**[FIND] The premise is wrong in the place it names.** Selection policy v1 has NO keyword filter. Its
rules are `form_types ['424B2']`, `issuer_include_status ['yes','review']`, `filing_date_from
2019-01-01` and order. The live data shows **zero WF or DB 424B2 filings in `not_selected`**. The
only WF/DB rows in `not_selected` are FWPs ("form FWP not in policy"), which is by design.

The keyword filter that drops their real pricing supplements is the post-fetch **prefilter**
(`edgar_fetch.passes_prefilter`). It requires one STRONG term (`barrier`, `buffer`, `autocall`,
`contingent coupon`, `participation rate`, `initial level`) or two terms counting the weak
`underlying`. **19** WF/DB final pricing supplements scored ZERO hits and sit at `prefilter_skipped`
with status_reason "pricing supplement; prefilter hits []":

| Group | Accession | Filed | Rule that excluded it |
|---|---|---|---|
| Deutsche Bank | 0000950103-26-010687 | 2026-07-16 | prefilter v1, hits [] |
| Deutsche Bank | 0000950103-26-002035 | 2026-02-12 | prefilter v1, hits [] |
| Deutsche Bank | 0000950103-24-016045 | 2024-11-06 | prefilter v1, hits [] |
| Deutsche Bank | 0000950103-24-000964 | 2024-01-22 | prefilter v1, hits [] |
| Deutsche Bank | 0000950103-23-000994 | 2023-01-25 | prefilter v1, hits [] |
| Wells Fargo | 0001839882-25-063090 | 2025-11-03 | prefilter v1, hits [] |
| Wells Fargo | 0001839882-25-005275 | 2025-01-31 | prefilter v1, hits [] |
| Wells Fargo | 0001839882-24-040822 | 2024-11-22 | prefilter v1, hits [] |
| Wells Fargo | 0001839882-24-025178 | 2024-08-07 | prefilter v1, hits [] |
| Wells Fargo | 0001839882-23-033519 | 2023-12-12 | prefilter v1, hits [] |

There are 10 DB and 9 WF in total; the rest are 2019–2023.

**The text the filter saw is in R2 only** (`text_r2_key`; `extracted_text` is NULL for these rows),
and this sprint made no R2 request, so it is not quoted here. `run_bulk_fetch.py --selection-v2
--dry-run` reads exactly that text and reports, per year, which filings the v2 terms would rescue.
A filing that stays put under v2 is a genuine plain note.

The v2 terms are the vocabulary of WF's "Market Linked Securities" (starting / ending / threshold
value, maximum return, upside participation) and DB's covers (trigger level / value, knock-in,
digital return). They apply ONLY to the two named issuer groups.

**[FIND] The implausibly low WF 2021–23 counts are in the SEC index itself, not in selection.** WF's
424B2 index rows by year are 11 / 106 / 156. Every one of them is selected or past it; nothing was
excluded. A name search of the whole index (`company_name ILIKE '%wells fargo%'`) finds no other WF
structured-note CIK; the only other WF entities are the CMBS trusts. WF's quarterly 424B2 volume
collapses from 585 (2020Q1) to 6, 3, 2, 0 through 2021, and only recovers from 2022Q2.

The likely explanation is that WF filed its notes under a form this index does not load. The index
holds only 424B2 and FWP; 424B3 is a candidate. Proving it needs an SEC request. Operator follow-up:
open WF's EDGAR filing list (CIK 72971 / 1738143) for 2021 and see which form its "Market Linked
Securities" pricing supplements use. If it is not 424B2, add that form to the `structured_notes`
source (a data change, see Task 2) and run `--refresh-index` over 2021.

## 1f. How the EDGAR index is loaded today

There are two loaders, and both share `services/edgar_index.parse_index_lines` + `load_records`. Both
use a TEMP table, INSERT … ON CONFLICT DO NOTHING, and `primary_issuer_cik` computed in the same
transaction.

- **Quarterly:** `scripts/load_edgar_index.py <folder>` reads pre-filtered `filtered_YYYY_QN.txt`
  files from the operator's disk. They were downloaded from `full-index/{year}/QTR{q}/master.idx`
  outside the code. This loaded 716,953 rows through **2026-10-01**: `max(filing_date)` =
  2026-10-01, and 2026Q4 holds 1,087 424B2 rows.
- **Daily:** `edgar_pipeline.discover_incremental` reads
  `daily-index/{year}/QTR{q}/master.{YYYYMMDD}.idx` for every weekday from the newest loaded
  filing_date (inclusive) to today. A 403/404 is treated as "no index that day".

**A refresh from 2026-10-02 to today needs no new loader.** `run_bulk_fetch.py --refresh-index`:
- reads the daily index files with `discover_day`, extended with a `forms` set (the union of active
  sources' form types, so a future source's forms load too) and a `collect` list of the accessions
  actually inserted;
- then runs `select_stage(accessions=collect)`, so the active policy is applied to the new rows only.

From the last date (2026-10-01, inclusive: a re-read is a no-op) through yesterday (2026-10-15) that
is **11 weekday index files**. `--dry-run` prints this and makes no request.

## 1g. product_supplement_ref today, and resolving it to an accession

`product_supplement_ref` is a scalar TEXT field with `extraction_method 'model'`. Its registry
description is "The name and date of the program document… (e.g. "EQUITY-1, Dec 8 2025")". It is
never linked to anything. There is no rules extractor and no link column.

**Measured on the 171 424B2 texts stored in the database** (the 2025Q1 corpus), using this sprint's
rules (`edgar_bulk.find_supplement_link` / `find_supplement_name_date`):

| Outcome | Notes |
|---|---|
| sec.gov link labelled "Product supplement …" | **76** |
| Name + date only | 41 |
| Neither | 54 (preliminaries / other kinds among them) |

The 76 links point to only **20 distinct supplements**:
- 24 notes cite JPMorgan's product supplement no. 4-I (0001213900-23-029539);
- 21 cite BofA's EQUITY-1 (0001193125-22-315473).

That is the case for fetching each supplement **once per program**. The pricing supplements print
the URL on its own line after a label line such as "Product supplement no. 4-I dated April 13,
2023:". The rule takes the URL whose own label line says "product supplement", so a paragraph that
merely mentions "the related product supplement" above a prospectus link does not count.

**[FIND]** JPMorgan links to the PDF rendition; the rule keeps the accession and lets the folder
index pick the HTML body.

**[FIND]** Product supplements are often filed as 424B3 or 424B5 (TD's MLN-ES-ETF-1 is `…_424b3.htm`).
`reference_filings.form_type` was CHECK-limited to 424B2/FWP, so such a supplement could not be
stored at all. That CHECK is widened, additively, to add 424B3/424B5.

The link is stored in a new `portfolio.reference_filing_supplements` table, one row per (note,
supplement kind), with resolution_method `link` | `name_date` | `unresolved`.

## 1h. How the gold review screen renders and saves list fields

The EDITOR already had a per-row table:
- `GoldReviewScreen.jsx` `Editor` builds one column per `field.members` entry. The members come from
  the API's `_member_fields`, which introspects `schema.Underlying` / `Observation` /
  `DistributionMember`.
- It has Add row / Remove row, and `parseList` validates each row.
- Saving sends the whole list as one gold value, and `gold.canonical_value` → `schema.validate_list`
  checks it on the server.

**Why the reviewer could not see or add separate rows:**
1. The editor only opens after pressing **E / Correct**. Until then a list showed as ONE line
   (`showValue` joins members with "; " and prints only `name`), so tickers, kinds and levels were
   invisible and two underlyings looked like a sentence.
2. The gold value of a reviewed list showed the same way.
3. No row showed its quote.

Fixed (Task 8):
- A list field now always shows as a table, pre-fill rows first, then the gold rows once reviewed.
  Each row shows its quote when the member's name occurs in the field's quote, and distribution rows
  show their normalised match.
- The editor table gains a quote column.
- Tab moves across the cells (plain inputs). C / E / A still act on the whole list.

## 1i. Where the review API validates a reviewer's value

- `routers/note_extraction_admin.put_gold_value`: `_require_super_admin` first, then the field spec
  lookup, then `gold.record_gold_value`.
- `record_gold_value` calls `canonical_value`, which runs the range model / `validate_list`, then
  `schema.normalize`. A `GoldWriteError` becomes a 422.
- The CUSIP / ISIN checks and the issuer-name normalisation hook into `put_gold_value` BEFORE
  `record_gold_value`. A bad CUSIP or a malformed ISIN is a 422. An ISIN that does not carry the
  note's CUSIP, or an issuer / guarantor that does not match the issuer table's credit entity, is
  saved with `metadata.needs_review_reasons` on the gold row (that column already existed and is
  NOT NULL, default '{}') and returned as `warnings`.
- `record_gold_value` gained an optional `metadata` argument. The human-guard trigger is untouched.
