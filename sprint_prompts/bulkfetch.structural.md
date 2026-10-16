BULKFETCH + REVIEW FIXES — (A) a resumable, rate-limited bulk downloader for
EDGAR filings, run from the command line by institution, date range and
document kind, with a rules-based cover-page peek that skips preliminaries
before the full download; product supplements fetched once per program; filers
and form types as configuration so defined-outcome ETF filings can plug in
later; an EDGAR index refresh; the Wells Fargo / Deutsche Bank selection gap;
the 200 filings stuck at 'fetched'. (B) the gold review screen fixes Joe found
while reviewing: list fields as editable tables, the underwriting-discount
label, CUSIP/ISIN validation, calculated dollar totals with their check, and
name normalisation shown on screen. 10 tasks + verification.

Part A context: first run is 2025-01-01 → 2026-10-01 for JPMorgan, UBS,
Goldman Sachs, Morgan Stanley, Wells Fargo and Bank of America — about 125,000
selected 424B2 filings. Downloading needs no model; the peek and the
classification are rules only. Today's fetch path (the EDGAR Pipeline screen's
Run button on a cohort) took 1.9 seconds per filing for the 308-filing gold
cohort (2026-10-08 15:18–15:28 UTC), about 2.7 days for 125k, and it runs inside
the API service on Render's FREE plan (spins down, kills long work). This
sprint builds a CLI runner the operator starts on the laptop (tmux); it can
later move to a Render worker and get a screen picker.

Part B context: reviewing gold-1 note #1 (BofA, CUSIP 09709TJV4) Joe found:
no visible "underwriting discount" field (it is agent_commission_pct); the
filing's dollar totals ($27,950 discount, $832,050 proceeds) are not stored;
underlyings (SPX, RTY) and their tickers cannot be entered as separate rows;
no CUSIP validation when a reviewer types one; issuer and agent names are not
shown normalised.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (checked live 2026-10-16):
- portfolio.edgar_index_filings: 716,953 rows, index loaded through
  2026-10-01. Selected 424B2 counts by issuer group
  (structured_note_issuers.issuer_group via primary_issuer_cik), 2025 / 2026:
    JPMorgan 18,904 / 17,499 · Morgan Stanley 14,585 / 12,415 ·
    Goldman Sachs 9,077 / 12,416 · UBS 11,094 / 10,381 ·
    Bank of America 9,476 / 7,876 · Wells Fargo 277 / 798.  Total ≈ 124,800.
- An issuer group spans several filer CIKs (e.g. JPMorgan Chase & Co. and
  JPMorgan Chase Financial Company LLC). The group mapping exists in
  structured_note_issuers; there is no bulk download by group yet.
- Preliminaries and finals are both form 424B2 — the index cannot tell them
  apart. Of 1,058 filings fetched so far, 362 were preliminaries and 80 were
  prefilter_skipped.
- pipeline_status values (live CHECK): discovered, selected, not_selected,
  fetched, fetch_failed, prefilter_skipped, not_pricing_supplement,
  ready_for_extraction, extraction_submitted, extracted, extraction_failed,
  needs_review.
- 200 filings sit at pipeline_status 'fetched' with document_kind NULL (never
  classified); reference_filings has 1,058 rows, 858 with text_r2_key.
- Documents and text are stored gzip-compressed in R2 bucket hollisworks-docs
  (reference_filings.r2_key, text_r2_key); average 84 KB document + 21 KB text
  per filing. reference_filings.extracted_text is NOT used for new rows — text
  lives in R2 only. Keep it that way (database size).
- edgar_cohorts.member_count CHECK is 1..50000, so the runner selects by
  institution/date directly, not through a cohort.
- SEC fair access: at most 10 requests/second per user with a declared
  User-Agent; the agreed ceiling is 8/s across ALL workers.
- Wells Fargo (CIKs 72971, 1738143) and Deutsche Bank (1159508) structured
  notes are misfiled as plain notes by the selection keyword filter; Wells
  Fargo 2021–23 counts (11 / 106 / 156) are implausibly low.
- Render bills outbound bandwidth (R2 uploads), which is why files are gzipped.
- Registry (live): agent_commission_pct (range, pct) is described as "agent's
  commission / underwriting discount"; underlyings and distribution are 'list'
  fields; issue_size (usd), denomination (usd) are scalars;
  product_supplement_ref is a scalar text field. All 49 gold-1 pre-filled
  CUSIPs pass the check digit; note #9's CUSIP is missing.
- portfolio.distribution_participants is seeded with the 10 issuer-affiliated
  broker-dealers and has affiliated_issuer_group (notefix2).
- note_gold_values: the reviewer is entering values now — this sprint must not
  change, migrate or delete any existing gold value.
- Baselines (do NOT re-run except as stated): verify_edgarpipelinea 102/102,
  verify_edgarcohorts 157/157, verify_notefix2 108/108, verify_goldset
  128/128 (standalone, ~7 minutes).

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a script
finished. Never wait for one. Run every command synchronously in the
foreground. Do NOT run the bulk fetch, the index refresh or the peek test for
real and make NO real SEC, R2 or model requests — build the tools; the operator
runs them. No AI model anywhere in Part A. Write the verify script but DO NOT
RUN IT. Never print a secret value. Column names come from the live database,
not this prompt. UI: light theme, whites and creams, matching the existing
admin screens.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The exact code path of one filing's fetch today (index page? document?
      exhibits?), how many SEC requests it makes, and where the 1.9 s goes
      (SEC latency, R2 upload, text extraction, classification, DB writes, a
      fixed sleep).
  1b. Whether that code can run outside the API process (pure service functions
      vs request-bound) and what it needs (DB pool, R2 client, User-Agent).
  1c. The existing document_kind classifier: confirm it is rules-only, list its
      rules and the kinds it emits (final / preliminary / product supplement /
      prospectus supplement / other), and whether it can run on the first
      ~16 KB of a document.
  1d. Why the 200 'fetched' filings were never classified.
  1e. Why Wells Fargo and Deutsche Bank pricing supplements land in
      not_selected: 5 real examples each (accession, the text the filter saw,
      the rule that excluded them).
  1f. How the EDGAR index is loaded today (source, script, last date) and what
      a refresh from 2026-10-02 to today needs.
  1g. How product_supplement_ref is captured today and whether the referenced
      product supplement can be resolved to an accession (link in the pricing
      supplement, or name + date).
  1h. How the gold review screen renders and saves 'list' fields today
      (underlyings, distribution, observation_schedule), and why a reviewer
      cannot see or add separate rows.
  1i. Where the review API validates a reviewer's value, so CUSIP/ISIN checks
      and normalisation hooks fit there.

=== PART A — BULK DOWNLOAD ===

=== TASK 2: SOURCES AS CONFIGURATION ===
Filers and form types are data, not code. Add (or extend, per 1a/1f) a source
configuration: a source has a name (e.g. 'structured_notes'), its form types
(['424B2'] today), its filer list (resolved from structured_note_issuers
issuer groups for notes) and its classifier. The runner takes --source
(default structured_notes). Do NOT add the ETF source in this sprint; document
in PROJECT_STATUS how a 'defined_outcome_etfs' source (Innovator, First
Trust/FT Vest, AllianzIM; forms 497/497K/N-1A) would be added with its own
filer list and classifier.

=== TASK 3: INDEX REFRESH ===
apps/api/scripts/run_bulk_fetch.py --refresh-index [--to YYYY-MM-DD]: load
EDGAR index rows after the last loaded date (default to yesterday) using the
existing loader from 1f, idempotent (no duplicate accessions), then apply the
current selection policy to the new rows only. Its --dry-run prints the date
span and the number of index files it would read.

=== TASK 4: THE BULK RUNNER ===
apps/api/scripts/run_bulk_fetch.py
  --banks "JPMorgan,UBS,Goldman Sachs,Morgan Stanley,Wells Fargo,Bank of America"
  --from 2025-01-01 --to 2026-10-01
  --kinds final[,product_supplement,prospectus_supplement,preliminary,other]
  --concurrency 6 --max-rps 8 [--limit N] [--dry-run] [--order newest|oldest]
  [--stop-after-failures 50] [--no-peek] [--keep-preliminaries]
  - --banks takes issuer-group names (case-insensitive); an unknown name is an
    error listing the valid ones. The dry run prints, per institution, every
    filer entity and CIK included and its count, so the operator sees exactly
    which legal entities are in scope.
  - --from / --to are filing dates (inclusive). If --to is after the last
    indexed date, stop and tell the operator to run --refresh-index.
  - Scope: selected filings of the source's form types for those groups and
    dates, ordered by institution (order given), then newest first.
  - PEEK (default on, rules only, no model): request only the first ~16 KB of
    the primary document (HTTP Range header) and run the existing classifier
    from 1c on it. If the kind is not in --kinds, record document_kind and a
    status (preliminaries → not_pricing_supplement or the existing equivalent
    from 1c — a live CHECK value; never invent one) with no full download and no
    R2 write. If the cover is unrecognised, or the server ignores Range (returns
    200 with the full body), fall through to the normal full fetch — never
    lose a filing. Peek requests count against the rate limit. --no-peek
    disables it. --keep-preliminaries downloads preliminaries in full anyway.
  - Workers share ONE global token-bucket limiter: never more than --max-rps SEC
    requests per second across all workers, and never above 8 without an
    explicit override flag. Declared User-Agent from Doppler.
  - Each kept filing: reuse the existing fetch → gzip → R2 → text → classify →
    status code from 1a/1b (no reimplementation of classification). Text goes
    to R2 only; extracted_text stays NULL.
  - Resumable and safe to run twice at once: claim rows with SELECT … FOR
    UPDATE SKIP LOCKED (or an equivalent claim column), skip anything already
    past 'selected'. Ctrl-C finishes in-flight filings and exits cleanly.
  - 429 / 503 / connection errors: exponential backoff; a filing that fails 3
    times becomes fetch_failed with the reason; stop the whole run after
    --stop-after-failures consecutive failures and say why.
  - Progress line every 100 filings: done / remaining, rate per second, ETA,
    counts by document_kind, peek-skipped count. Final summary by institution
    and document_kind, total bytes uploaded to R2, bytes saved by peeking.
  - --dry-run prints the scope per institution and year, the ETA at the given
    rate, and the estimated R2 storage and monthly cost ($0.015/GB-month beyond
    10 GB) assuming about a third are preliminaries skipped by the peek; it
    writes nothing.
  - --peek-test N: peek N filings (operator runs it with 20), print for each the
    HTTP status (206 vs 200), bytes received and the kind found, then a verdict
    on whether SEC honours Range. Writes nothing.
  - Record each run (start, source, scope, kinds, counts, peek-skipped,
    failures, bytes) in an existing run table if 1a/1b finds a suitable one;
    otherwise a small edgar_fetch_runs table (migration, RLS global pattern).
  - Report the expected ETA for the 125k scope from 1a's numbers.

=== TASK 5: PRODUCT SUPPLEMENTS ONCE PER PROGRAM ===
When --kinds includes product_supplement: per 1g, resolve each final's
referenced product supplement (link first, then name + date) and download each
distinct one ONCE; store the link from every note to it (existing column or a
small link table, per 1g). Already-stored supplements are never re-fetched.
The summary reports distinct supplements found, fetched and unresolved.

=== TASK 6: WELLS FARGO / DEUTSCHE BANK SELECTION ===
Fix the keyword filter per 1e as selection_policy_version 2 (do not edit v1 in
place): it must select the real WF / DB structured-note pricing supplements and
still exclude plain medium-term notes. Re-run selection for those two issuer
groups only, with a --dry-run that prints how many filings move from
not_selected to selected, by year, before anything changes. Never alter other
banks' selection.

=== TASK 7: STUCK FILINGS ===
Add --reprocess-fetched to the runner: classify the 200 'fetched' filings from
their stored R2 text (no new SEC requests where the document is already in R2)
and move them to their proper status.

=== PART B — REVIEW SCREEN FIXES ===

=== TASK 8: LIST FIELDS AS EDITABLE TABLES ===
Per 1h, render underlyings, distribution and observation_schedule as small
tables in the review screen: one row per member, one column per member field
(underlyings: name, ticker, kind, weight_pct, initial_level, return_basis,
fx_treatment), with Add row / Remove row, the pre-filled rows shown first, and
the quote for each row when available. Saving writes the whole list as one gold
value (existing shape and validation). Keyboard: Tab moves across cells; the
existing C / E / A shortcuts still act on the whole list field.

=== TASK 9: LABELS, VALIDATION, CALCULATED TOTALS ===
  - Display label for agent_commission_pct becomes "Underwriting discount /
    agent commission" (registry display_label and NOTE_FIELDS.md). No key change.
  - CUSIP: the review API and screen refuse a reviewer value that fails the
    check digit or is not 9 characters, with a clear message. ISIN: 12
    characters, valid check digit; a US/CA ISIN must contain the note's CUSIP
    (positions 3–11); a mismatch is shown as a warning and recorded as a
    needs_review reason (not a hard refusal).
  - Calculated totals (extraction_method 'derived', new registry rows):
    total_fees_usd = issue_size × total_fees_pct / 100 and
    proceeds_to_issuer_usd = issue_size × proceeds_to_issuer_pct / 100 (range
    ends computed separately). Shown in the economics section as calculated,
    never edited. New self-check: when the filing's totals column states the
    dollar totals (e.g. "$27,950" / "$832,050"), compare with the calculation
    (±$1 or ±0.01%) and flag needs_review on a mismatch, naming both numbers.

=== TASK 10: NAMES SHOWN NORMALISED + STATUS ===
On the review screen, beside issuer_entity, guarantor_entity, calculation_agent
and each distribution row, show the matched canonical record and whether it is
an affiliate of the note's issuer group (structured_note_issuers for
issuers/guarantors, distribution_participants for agents), or "no match". The
reviewer's value is still what is saved; the match is display plus a
needs_review reason when a guarantor or issuer does not match the issuer
table's credit entity. No CRM linkage in this sprint.
Then update docs/PROJECT_STATUS.md: sources-as-configuration and the ETF
source outline, index refresh, the runner and its flags, the peek and the
peek-test result to be filled by the operator, product supplements, measured
per-filing cost, selection v2 and its counts, the operator commands (tmux), and
the Part B changes.

=== VERIFICATION: apps/api/scripts/verify_bulkfetch.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Hydrate secrets from
Doppler over HTTPS. Mock SEC and R2 entirely (no network) and all model calls.
Wrap setup, teardown and every section so an exception is a [FAIL], never a
crash; teardown at start and end in FK child-before-parent order read from
pg_constraint, then re-read every touched table for its exact pre-test count.
Fixture users are created explicitly with the auth0_sub the mocked token
carries (the app auto-provisions unknown subs as 'member' — that leaked users
before); teardown finds them by auth0_sub and email pattern too.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; isinstance(passed, bool).
  - Every fixture value satisfies live CHECK and FK constraints (pg_constraint,
    contype read as ::text). Never invent a status, origin, kind or role.
  - Never derive a unique value from a slice of a fixture UUID.
  - RLS context on every read. Expected counts from what the test created. An
    empty list never passes a check.
  - Regex checks against docs match the doc's real formatting; print the match.
  - Nested regressions run only after this script's own teardown, 600 s timeout.
  - Never touch real note_gold_values rows.

Assertions:
  [Y] Task 1's nine findings reported, with the measured request count, the
      per-filing time breakdown and the classifier's rule list
  [Y] Sources: the runner reads form types and filers from configuration; a
      fixture source with a different form type selects only that form
  [Y] Institutions: --banks "jpmorgan" resolves every fixture CIK in that
      group; an unknown name errors and lists valid names; dry-run prints each
      filer entity with its count
  [Y] Dates: --from/--to selects exactly the fixture filings in the range
      (inclusive both ends); a --to past the last indexed date stops with the
      refresh message
  [Y] Index refresh: mocked index files after the last date load once; a
      second run adds zero rows; only the new rows get selection applied
  [Y] Scope and order: --dry-run returns the selected filings of the named
      institutions, in institution-then-newest order, and writes nothing
  [Y] Peek: a mocked 206 cover reading "Preliminary Pricing Supplement" is
      recorded with its kind and a live-CHECK status, no full GET, no R2 write;
      a final continues to full fetch; an unrecognised cover falls through to
      full fetch; a mocked 200 (Range ignored) is handled as a full fetch;
      --no-peek and --keep-preliminaries behave as stated; peek requests are
      counted by the rate limiter
  [Y] --kinds: with --kinds final, a fixture product supplement is not
      downloaded; with product_supplement added, two finals referencing the
      same supplement cause exactly ONE fetch and two links
  [Y] Rate limit: with 6 workers and an instant mocked SEC, no 1-second window
      exceeds --max-rps requests (timestamps recorded); above 8 is refused
      without the override flag
  [Y] Resume: a run killed after K fixture filings, restarted, processes only
      the rest; two runners together never process the same filing
  [Y] Failures: a mocked 429 is retried with backoff; three failures make the
      filing fetch_failed with a reason; N consecutive failures stop the run
  [Y] Storage: documents and text go to (mocked) R2 gzipped; extracted_text
      stays NULL; byte totals in the summary equal the mocked uploads
  [Y] Selection v2: a fixture WF structured-note supplement moves to selected,
      a fixture WF plain MTN stays not_selected, other banks untouched; dry-run
      prints counts and changes nothing
  [Y] --reprocess-fetched classifies fixture 'fetched' rows with zero SEC requests
  [Y] Part A makes zero model calls (mocked model client records none)
  [Y] List editor (API level): saving two underlyings rows (SPX 2643.85, RTY
      1473.536) stores both; removing one and saving supersedes correctly;
      a row with an invalid kind is refused
  [Y] CUSIP 09709TJV4 accepted; 09709TJV5 refused with a message; an ISIN not
      containing the CUSIP is a warning plus needs_review, not a refusal
  [Y] Totals: issue_size 860000 × 3.25% = 27,950 and × 96.75% = 832,050 are
      computed and marked derived; a fixture stating $27,950 passes, one stating
      $29,000 flags needs_review naming both numbers
  [Y] Normalisation: "BofA Finance LLC" matches the issuer table; "MLPF&S" on a
      BofA note shows as an issuer affiliate; an unknown dealer shows "no match"
  [Y] Label: agent_commission_pct display_label is the new text (registry and
      NOTE_FIELDS.md agree)
  [Y] Permission gate on the review API changes: org admin refused, super-admin
      admitted, identical request
  [Y] Regression: verify_edgarpipelinea.py, verify_edgarcohorts.py and
      verify_notefix2.py each run ONCE as flat subprocesses AFTER teardown;
      all 0 FAIL
  [Y] Existing note_gold_values rows unchanged (count and checksum before/after)
  [Y] Teardown leaves zero fixture rows; every touched table at its pre-test count
