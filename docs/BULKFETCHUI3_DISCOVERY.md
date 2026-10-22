# BULKFETCHUI3 — Task 1 discovery findings (2026-10-22)

All numbers below were read from the live database read-only on 2026-10-22
(project mmgwmcinimzuhargsazs). Stored text was read from R2 read-only. No row
was changed and no SEC request was made.

## 1a. Where "stored in R2" is recorded; Downloaded / Stored SQL

**Where it is recorded.** `portfolio.edgar_index_filings.reference_filing_id` →
`portfolio.reference_filings` (`r2_key` = the gzipped document,
`text_r2_key` = the gzipped extracted text; the 200 rows of the 2025Q1 corpus
also carry `extracted_text` in the database). Only
`edgar_pipeline.store_fetched` writes a `reference_filings` row and uploads to
R2. The preliminary cleanup (`edgar_bulk.apply_preliminary_cleanup`) clears both
keys but keeps the row.

**How a run keeps or discards by `--kinds`.** `edgar_bulk.peek_decision(kind,
wanted, keep_preliminaries)`: a cover the peek recognises as a kind the run did
not ask for → `record_peek_skip` (status `not_pricing_supplement`,
`status_reason 'peek: …'`, no download, no R2 write, no reference row). An
unreadable cover (`other`) is fully downloaded; if the full text is an unwanted
kind → `record_kind_skip` (same status, `'peek: … (full text …; not stored)'`,
no R2 write). Everything else → `store_fetched` (R2 write + reference row).

Live shape of the handled rows (status, has reference row, `peek:` reason):

| pipeline_status | reference row | peek reason | rows |
|---|---|---|---|
| fetch_failed | no | no | 1 |
| fetched | yes (R2 + DB text) | no | 200 |
| not_pricing_supplement | no | yes | 10,209 |
| not_pricing_supplement | yes (R2) | no | 4,223 |
| prefilter_skipped | yes (R2) | no | 2,047 |
| ready_for_extraction | yes (R2) | no | 5,366 |

**(i) Downloaded** = every filing the fetch stage has handled — peeked and
skipped, fetched and decided, or failed. Status set:
`fetched, fetch_failed, not_pricing_supplement, prefilter_skipped,
ready_for_extraction` plus the later extraction statuses
(`extraction_submitted, extracted, needs_review, extraction_failed` — zero rows
today; a row only reaches them after it was fetched). Excluded: `selected`
(still to do — that is the Total column), `discovered` and `not_selected`
(never offered to a fetch). The status does not say WHICH process fetched a row
(the nightly pipeline and the bulk runner both use `store_fetched`), so
"Downloaded" means handled by the fetch stage, whichever ran it.

**(ii) Stored** = the filing's `reference_filings` row still holds an R2 object
(`r2_key IS NOT NULL OR text_r2_key IS NOT NULL`).

```sql
SELECT f.primary_issuer_cik AS cik,
       count(*)::int AS downloaded,
       count(*) FILTER (WHERE r.r2_key IS NOT NULL OR r.text_r2_key IS NOT NULL)::int AS stored
  FROM portfolio.edgar_index_filings f
  LEFT JOIN portfolio.reference_filings r ON r.id = f.reference_filing_id
 WHERE f.pipeline_status = ANY($3::text[])          -- the Downloaded status set above
   AND f.form_type = ANY($2::text[])                -- the source's form types (structured_notes: 424B2)
   AND f.primary_issuer_cik = ANY($1::text[])       -- the institution catalog's CIKs
 GROUP BY 1;
```

(`services/edgar_bulk_queue.DOWNLOADED_STORED_SQL`.)

**Live totals (structured_notes form types, all years):**

| Institution | Downloaded | Stored |
|---|---|---|
| Goldman Sachs | 12,496 | 6,120 |
| Wells Fargo | 1,092 | 1,092 |
| Jefferies | 1,217 | 602 |
| TD | 5,251 | 2,477 |
| Deutsche Bank | 901 | 456 |
| **All institutions** | **21,877** | **11,667** |

**Index.** The existing indexes serve it: a BitmapAnd of
`edgar_index_filings_queue_idx (pipeline_status, …)` and
`edgar_index_filings_primary_issuer_idx (primary_issuer_cik, filing_date)`,
then a hash join to `reference_filings` (11,836 rows, seq scan). EXPLAIN ANALYZE
for the three biggest programs: 12,069 rows, 478 ms cold. With the 60 s cache
that is acceptable; no new index added. If it grows slow, the index to add is a
partial `(primary_issuer_cik, form_type) WHERE pipeline_status IN (<the set>)`.

## 1b. The 3,243 'other' / not_pricing_supplement rows

By issuer group: Goldman Sachs 3,184; (no group) 16; Wells Fargo 8; Nomura 7;
US Bancorp 6; Scotiabank 5; HSBC 4; Royal Bank of Canada 4; Deutsche Bank 2;
TD 2; CIBC 2; Marex 1; Morgan Stanley 1; Jefferies 1. **All 3,243 have stored
text** (none came from a peek: every one was fully downloaded and stored).

**GS samples (10, including 0001193125-26-410827).** None has a title phrase
in the 4,000-character opening: the cover title "Pricing Supplement" is an
image. The opening reads "424B2 … GS Finance Corp. $2,260,000 Leveraged
Buffered S&P 500® Index-Linked Notes due 2029 guaranteed by The Goldman Sachs
Group, Inc. …". The text DOES carry the cover's bottom line, just past the
opening:

- 0001193125-26-410827: "Pricing Supplement No. 26,690 dated September 29, 2026." at 4,221
- 0001193125-26-230908: "Pricing Supplement No. 24,246 dated May 15, 2026" at 4,020
- 0001193125-26-138073: "Pricing Supplement No. 23,598 dated March 30, 2026" at 4,393
- 0001193125-26-041209: "Pricing Supplement No. 22,086 dated February 4, 2026" at 3,995
- 0000950103-26-006519: "Pricing Supplement No. 24,142 dated April 28, 2026" at 7,118
- the equity-linked series (same counter, called a prospectus supplement):
  0001193125-26-203976 "Prospectus Supplement No. 23,980 dated April 30, 2026" at 4,251;
  -267914 No. 25,216 at 5,559; -008800 No. 21,544 at 4,660; -204373 No. 24,166 at 4,042;
  -221826 No. 24,634 at 5,309. All have a trade date, an issued amount and an
  initial price: finals.

**Other banks' 'other' samples (8 read; 5+ required).** True others:
Marex 0001193125-25-111621 (senior notes prospectus supplement); US Bancorp
0001104659-22-011404 (preferred depositary shares); Wells Fargo
0001193125-21-220520 (preferred stock); Nomura 0001193125-21-209213 (senior
notes); (no group) 0001193125-22-120761 (auto-loan ABS FWP); HSBC
0001104659-25-109994 (an FWP whose text is 54 characters); CIBC
0001918704-26-008797 (MITTS FWP); RBC 0000950103-25-009744 (FWP that cites "the
accompanying preliminary pricing supplement").

**The rule.** When the opening has no title phrase, look in the first 12,000
characters (whitespace collapsed, lowercased) for
`(pricing|prospectus) supplement no. <number> dated <Month D, YYYY>`; if found
and there is no preliminary marker in that window → `pricing_supplement`,
otherwise still `other` (`edgar_pipeline._classify_cover_line`, constant
`COVER_LINE_CHARS = 12000`).

**Result.** Samples: 10/10 GS → pricing_supplement, 8/8 others stay other.
Measured over ALL 3,243 stored 'other' texts (read-only): GS 2,210 match
"Pricing Supplement No." (position 3,983–8,504) and 919 match "Prospectus
Supplement No." (2,267–6,834); 55 GS rows match neither (FWPs, structured
products summary supplements, one two-offering document — they stay 'other');
**zero** matches from any other issuer; zero matches with a preliminary marker.
So the reclassify command is expected to move about 3,129 GS rows.

## 1c. The workflow scheduler's Render cron service — and the choice

- `render.yaml` declares `2ndactcapital-workflow-scheduler` (`type: cron`,
  `*/5 * * * *`, `plan: starter`, `python workflow_scheduler_tick.py`) with
  DATABASE_URL, EDGAR_USER_AGENT, R2_*, AWS_*, VOYAGE, LITELLM_*,
  ANTHROPIC_API_KEY. A cron service has outbound internet, so it can reach SEC.
- docs/PROJECT_STATUS.md contradicts itself over time: the schedulercore entry
  says "the Render cron service has not been applied"; the later RLS-cutover
  entry (2026-09-10) names "Render's two live services (2ndactcapital-api,
  2ndactcapital-workflow-scheduler)", and the EDGAR pipeline A entry calls it a
  "paid instance, apps/api, Doppler-synced".
- `docs/WORKFLOW_SCHEDULER_DESIGN_V1.md` does not exist (a known gap).
- Live DB: `workflow_triggers` has one scheduled trigger (inactive, never fired)
  and one event trigger, so the database holds no evidence either way that the
  tick runs. No `edgar_pipeline_runs` row has trigger_source 'nightly'.
- Note: one manual `refresh_index` run DID succeed today (19:38 UTC) — the
  prompt's "zero refresh_index runs" was true when it was written.

**Choice: (A)**, a step in the existing tick process. The docs' later, more
specific entries say the service is live and Doppler-synced with
EDGAR_USER_AGENT and DATABASE_URL, and the step records its own run row, so it
proves itself: if no `trigger_source = 'schedule'` row appears on the next
weekday after 10:30 UTC, the tick is not running.

The step is in `workflow_scheduler_tick.py` (the entrypoint), NOT in
`run_scheduler_tick`: the scheduler's verify scripts call that function
directly and must never reach the SEC.

**Operator, in every case:**
1. Deploy this commit to `2ndactcapital-workflow-scheduler` (Render dashboard →
   the cron service → Manual Deploy, or merge to the branch it builds).
2. Check in Render that the service exists and its recent runs succeed (every
   5 minutes). If it does not exist, create it from `render.yaml` (Blueprint)
   and give it its OWN Doppler sync (project hollisworks, config prd).
3. Confirm EDGAR_USER_AGENT and DATABASE_URL are set on it (Render →
   Environment). EDGAR_INDEX_AUTO_REFRESH is optional (unset = on).
4. The migration `apps/api/migrations/bulkfetchui3_scheduled_index_refresh.sql`
   was applied by the sprint (trigger_source 'schedule' + the per-day unique
   index); nothing to do unless re-creating the database.
5. Next weekday after 10:30 UTC: the Bulk download runs table shows "Refresh
   index (Scheduled)".

(B) a new cron service costs another $1+/month and a second Doppler sync for
the same thing; (C) a laptop queue only runs while the laptop worker is up.

## 1d. The zero-byte download

`edgar_pipeline.store_fetched` raised `EdgarPipelineError("the main document
downloaded as zero bytes")`. In the bulk runner this is caught per filing by
`Runner._one` → `record_fetch_failed`, which set `next_attempt_at = NULL`: the
row was NOT retried by the next bulk run (scope = 'selected' only, unless
`--retry-failed`) nor by the nightly queue (which needs a due
`next_attempt_at`). The nightly path (`_record_failure`) already retried at
6/12/24/48 h. **So it was not retried; Task 5 fixes it**: a new
`EmptyDocumentError` (subclass of EdgarPipelineError) is recorded with
`retry=True` — `attempt_count + 1`, `next_attempt_at = now() + 15 min`, giving up
after `MAX_FETCH_ATTEMPTS` (5) — and the bulk scope (`_SCOPE_WHERE`) now takes
`fetch_failed` rows whose `next_attempt_at` is due. Other bulk failures keep
`next_attempt_at = NULL` (unchanged).
