PREVIEWPERF — make the EDGAR bulk-download live preview fast again: the
preview for one institution-year now takes ~2.4 s server-side and 3.7–4.1 s
end to end, against bulkfetchui's 2 s limit, because the real filing index
has grown. Find where the time goes, fix it with an index or a query /
serialisation rewrite, and prove the preview is under the limit with the
same answers. The limit is NOT raised. 5 tasks + verification.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- verify_bulkfetchui.py "live preview returns under 2 s": 2026-11-02 —
  "live preview JPMorgan 2025 timed calls: 4095 ms, 4560 ms, 4350 ms; best
  4095 ms end to end (server-side 2416 ms), total 18860, SQL 18860";
  2026-11-03 — best of 3 3.754 s with 1-min load per CPU 0.06 (an idle
  machine). The count is right (equals a direct SQL count); only the time
  fails. Everything else in verify_bulkfetchui passes (105 PASS).
- The route: routers/edgar_pipeline_admin.py:546 edgar_bulk_preview →
  services/edgar_bulk_queue.preview(conn, raw): validate_params →
  edgar_bulk.plan_scope(conn, scope, rate, concurrency, peek,
  prelim_share) → per-institution counts by year and filer →
  measured_rate(conn) → edgar_bulk.last_indexed_date(conn). It reports
  its own elapsed_ms.
- portfolio.edgar_index_filings: 729,903 rows, 368 MB. Indexes: pkey
  (accession_number); primary_issuer_idx (primary_issuer_cik, filing_date
  DESC); quarter_status_idx (index_quarter, pipeline_status); date_idx
  (filing_date); queue_idx (pipeline_status, filing_date DESC,
  accession_number DESC); form_sort_idx (form_type, filing_date DESC,
  accession_number DESC); kind_sort_idx (document_kind, …); quarter_sort_idx;
  claimed_idx (partial); selected_count_idx (primary_issuer_cik, form_type,
  filing_date) WHERE pipeline_status = 'selected'. The table has no `id`
  column.
- A live bulk run's claims dirty the visibility map (institutions() caches
  60 s for that reason) — an index-only scan may degrade while a worker
  runs.
- WSL memory on the operator's laptop is limited: do NOT run npm run build
  or any full verify script; the laptop bulk worker must not be running
  during proofs (check portfolio.edgar_fetch_workers heartbeats first, as
  run_regressions.sh does).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts; run every command synchronously in the
foreground; no network beyond Doppler hydration; never print a secret.
Read-only against real data except a new index (CREATE INDEX CONCURRENTLY,
through Doppler, via scripts/_apply_previewperf.py; saved in
docs/previewperf_part1.sql; VERIFY it landed and is valid). The preview's
answers must not change. Commit ALL changed files on the current branch at
the end with message "previewperf: <cause> → <fix>, <before> ms → <after>
ms".

=== TASK 1: DISCOVER === time each step of preview() for JPMorgan 2025
(server-side, warm, 3 runs): validate_params, each SQL in plan_scope
(EXPLAIN (ANALYZE, BUFFERS) — plan, rows, time), the Python per-row work,
measured_rate, last_indexed_date, JSON size; and the end-to-end gap
(~1.7 s between server-side and end to end — where is it: serialisation,
response size, the test client?). Report a table.
=== TASK 2: FIX === the biggest costs first: an index matching plan_scope's
predicates (e.g. (primary_issuer_cik, form_type, pipeline_status,
filing_date) or a partial index), aggregate in SQL instead of Python, drop
unneeded columns, cache what is safe (state the invalidation), trim the
response. No change to totals, years, filers or estimates.
=== TASK 3: PROVE === before / after timings (3 runs each, warm, worker
off); every institution-year's preview identical before and after (diff of
the JSON minus elapsed_ms); the index valid; nothing else's plan made worse
(EXPLAIN of institutions(), DOWNLOADED_STORED_SQL and the queue claim query
before / after).
=== TASK 4: REGRESSION === run verify_bulkfetchui.py --self-checks-only (and
note the full run happens in the verify).
=== TASK 5: UPDATE PROJECT STATUS === PROJECT_STATUS.md entry; add
verify_previewperf.py to scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_previewperf.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only. MUST print 'TOTAL: N PASS, M FAIL'
and exit non-zero on failure. Hydrate secrets from Doppler over HTTPS. It:
(1) confirms no laptop bulk worker is heartbeating (else one [FAIL] naming
it); (2) the new index exists and is valid; (3) the preview for JPMorgan
2025 and two other institution-years equals a direct SQL count; (4) best of
3 server-side elapsed_ms and end-to-end time both under 2 s; (5) runs
verify_bulkfetchui.py in full as a subprocess (VERIFY_NESTED=1, 1800 s)
→ 0 FAIL. With VERIFY_NESTED=1 it skips (5) with one '[SKIP] nested run'.
Supports --self-checks-only and --cleanup-only (nothing to clean: prints
TOTAL 1 PASS). check(passed, label, detail): condition first, a real bool;
every import at the top; a failing check prints the timings.

Assertions:
  [Y] Task 1 timing table
  [Y] Index valid; answers unchanged for every institution-year
  [Y] Preview under 2 s server-side and end to end
  [Y] verify_bulkfetchui.py in full → 0 FAIL
