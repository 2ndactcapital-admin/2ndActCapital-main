MKT02C2 — SPLICE GATE FIX: TOLERATE ISOLATED FEED ERRORS. 3 tasks + verification.

mkt02c built the S&P 500 history splice (services/market_data/splice.py and
apps/api/scripts/market_data_splice_history.py). Its overlap gate compares the
series' own FRED rows with Yahoo's ^GSPC over the dates both cover. The live
dry run on 2026-10-04 REFUSED, correctly applying the rule as written, but the
rule turned out to be too strict. This sprint changes the rule. Nothing else.

THE LIVE FINDING (confirmed, do not re-derive):
- Overlap days compared: 2,514 (2016-10-03 to 2026-10-02). 99.76% differ by no
  more than 0.02. The ten worst differences were:
    2021-08-11  series 4447.7   vs Yahoo 4442.4102  diff 5.2898
    2019-08-12  series 2883.75  vs Yahoo 2882.7000  diff 1.0500
    2020-05-11  series 2930.32  vs Yahoo 2930.1899  diff 0.1301
    2018-11-29  series 2737.76  vs Yahoo 2737.8000  diff 0.0400
    and six more at or below 0.0299.
- The two large days are isolated single-day disagreements between two feeds of
  the SAME index (relative differences about 0.12% and 0.04%), not a different
  or mis-scaled series. The old rule "no overlap day may differ by more than
  1.00" is an absolute number that is far too tight for an index near 5,000.
- mkt02c is NOT merged. It lives on branch mkt02c. The operator has already
  loaded the six new registry rows, validated, and backfilled; those steps are
  done and must not be repeated.

THE NEW GATE RULE (canonical). The gate passes only if ALL hold:
  1. At least 20 overlap days exist (unchanged).
  2. At least 99.0% of overlap days differ by no more than 0.02 (unchanged).
     This is the main protection against splicing a different index, a futures
     contract, or a differently scaled series: a wrong series fails it on most
     days.
  3. No overlap day differs by more than 0.5% of the series' own value, that
     is |yahoo - series| / |series| <= 0.005. This REPLACES the absolute 1.00
     limit. It still catches a catastrophic single-day break.
Days that differ by more than 0.02 are printed as [FIND] (worst ten) whether
the gate passes or fails. When the gate passes, the output says how many days
were tolerated and that the series' own value stands for each of them. The
splice still never writes to, revises or closes any row dated on or after F0.
The only change is how the gate decides.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue. If you have no database access in this
environment, say so explicitly and STOP.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every script SYNCHRONOUSLY in the
foreground and read its output directly.
Task-specific rules:
- Decimal-only arithmetic; never float. statement_cache_size=0 everywhere.
- No DDL. No writes to any database table from this session. No writes to
  portfolio.*. No live external calls. Do NOT run the splice script, the ingest
  script, the nightly script, or any verify script: write them and STOP; the
  operator runs them.
- Edit ONLY: services/market_data/splice.py, apps/api/scripts/
  market_data_splice_history.py (help text and output only), apps/api/scripts/
  verify_mkt02c.py (only the gate assertions named below), the new
  verify_mkt02c2.py, and the two docs. Do not edit CLAUDE.md or any other
  module.
- NEVER print or log a secret or request URL.

=== TASK 1: DISCOVER ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Read the gate code in splice.py: where the 1.00 limit and the 0.02 / 99%
      thresholds are defined and used, and what the result object reports.
  1b. In verify_mkt02c.py find every assertion that encodes the old rule
      (S3a, S3b, S3c, S3d and anything else that mentions 1.00 or "single day").
      Report file and line for each.
  1c. Search the repo docs and script help text for the old rule's wording
      ("1.00", "single overlap day") and list every place that must change.

=== TASK 2: CHANGE THE GATE ===
  2a. Implement THE NEW GATE RULE exactly. Name the thresholds as module
      constants (20 days, 99.0%, 0.02, 0.005) so a future change is one line.
  2b. The relative test divides by the series' own (FRED) value. A series value
      of zero cannot occur for an index but must not raise: treat it as a
      failed gate with a clear message.
  2c. Output: print the three rule values, the number of overlap days beyond
      0.02 ("tolerated" on a pass), and keep the worst-ten [FIND] list. The
      final gate line says PASSED or FAILED with the specific reason (which of
      rules 1 to 3 failed and the offending day for rule 3).
  2d. Docs: in docs/MARKET_DATA_DESIGN_V1.md replace the old rule with the new
      one, record the live finding above as a data-quality note (two days of
      disagreement between FRED and Yahoo; the series keeps its own FRED value
      on those days), and say that a day-level discrepancy inside FRED's own
      range is reported but never overwritten. In docs/PROJECT_STATUS.md append
      an `UPDATE 2026-10-04` under the mkt02c entry describing the gate change
      and why.

=== TASK 3: REAL PROOF (written into the verify scripts, NOT executed by you) ===
Phase A only, fixtures tagged 'verify.mkt02c.' (and 'verify.mkt02c2.' for any
new ones), fake transports only, never touching real series or calling Yahoo.
Edit verify_mkt02c.py minimally and create verify_mkt02c2.py as a focused
script that re-proves ONLY the gate, reusing the fixture helpers (import them
or copy them; do not run verify_mkt02c.py as a subprocess).
  - S3a (unchanged): more than 1% of overlap days differ by more than 0.02 ->
    refuses, exit 1, zero writes.
  - S3b (changed): a payload that is within tolerance on every day except ONE
    day off by more than 0.5% of the series value -> refuses, exit 1, zero
    writes, the offending day named.
  - S3e (new): a payload within tolerance except one day off by about 0.12% and
    one day off by about 0.04% (the live shape) -> PASSES, exit 0, every pre-F0
    date inserted, both days listed as tolerated, and the series' own rows for
    those two days unchanged (before/after comparison).
  - S3d (unchanged): fewer than 20 overlap days -> refuses, zero writes.
  - S3c (unchanged): a payload within tolerance everywhere passes.
  - The rule constants: 20, 99.0, 0.02 and 0.005 are the values in the module.
  - A zero series value on an overlap day -> refuses with a clear message, no
    exception.
  - Dry run with the new rule behaves like a real run except that nothing is
    written.
  - Teardown deletes fixtures in strict FK order (observations, then
    indicator_ingest_runs by fixture series_id and by any batch_id created, then
    series), confirmed against information_schema, and re-reads the market_data
    tables and portfolio.securities_global, securities_global_prices and
    public.fx_rates to confirm non-fixture counts are exactly as before. Fail
    loudly with SystemExit(2) if any fixture remains. Never TRUNCATE.

=== VERIFICATION: apps/api/scripts/verify_mkt02c2.py ===
Pass/fail only. No interactive prompts. WRITE it and make it runnable; do NOT
run it. Hydrate secrets internally using the same helper the other verify
scripts use. Output lines begin with [PASS], [FAIL], [FIND], or [SKIP]; the
last line is `TOTAL: <passed> passed, <failed> failed`. Exit non-zero on any
[FAIL]. Every assertion states why it matters. verify_mkt02c.py keeps its own
Phase B (live) assertions unchanged.

When finished, STOP and print exactly these operator commands, in this order:
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_splice_history.py --dry-run
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_splice_history.py
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_nightly.py
  doppler run -- apps/api/venv/bin/python apps/api/scripts/market_data_nightly.py
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02c2.py 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02c.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt03.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt02.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
  doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_mkt01.py --live 2>&1 | grep -E "^\[FAIL\]|^TOTAL"
