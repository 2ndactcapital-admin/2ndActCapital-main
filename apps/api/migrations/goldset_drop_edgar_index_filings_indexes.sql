-- goldset.structural — bring the repo in line with two index drops made LIVE on
-- 2026-10-08 to save space. Both were already covered by wider indexes:
--
--   edgar_index_filings_form_date_idx  -> edgar_index_filings_form_sort_idx
--                                         (form_type, filing_date DESC, accession_number DESC)
--   edgar_index_filings_status_idx     -> edgar_index_filings_queue_idx
--                                         (pipeline_status, filing_date DESC, accession_number DESC)
--
-- ALREADY APPLIED LIVE. Idempotent: IF EXISTS makes a second (or first) run a no-op.

DROP INDEX IF EXISTS portfolio.edgar_index_filings_form_date_idx;
DROP INDEX IF EXISTS portfolio.edgar_index_filings_status_idx;
