SECMASTER2 — finish the security master: SEC mutual-fund tickers into the
master (so mutual funds resolve like stocks and ETFs), the FinanceDatabase
attributes load working again (its job run is HELD on a 404), sharper fund
categories (US vs international equity; a category the name contradicts goes
to review), and a load summary that says what the master holds. 8 tasks +
verification. Branch secmaster2-work.

Context: rollup2 (2026-11-07) classifies listed funds by the master's
fund_category (security_reference_attributes, provider finance_database).
The Olivo demo showed the gaps: the 7 mutual funds have NO master link
(manual assets) so no category; FinanceDatabase's "Developed Markets" puts US
equity ETFs (QQQ, SCHB, SCHD, VTI, ITOT) in the general Equity bucket with no
US / international split; BTC (Grayscale Bitcoin Mini Trust) is filed
"Corporate Bonds". Prices come next (prices1), and need every held security
in the master first.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE (repo + live DB 2026-11-07; Task 1
confirms signatures):
- secmaster1 built the master from free public sources: five reference-data
  providers, the matcher, the 13(f) list, a one-time load (15,959
  securities: tickers / CUSIPs / FIGIs), four platform jobs as system
  workflows in the Hollisworks org (triggers inactive; bugfix4 added the
  Super Admin on / off switch), the admin page /admin/security-data/master
  (status, jobs with Run now / close held run, review queue). Design and
  discovery: docs/SECMASTER1_DISCOVERY.md, docs/SECURITY_DATA_DESIGN.md.
- The attributes job's one run is HELD (FinanceDatabase 404), so attributes
  loaded 2026-11-06 came from the one-time load only.
- rollup2: services/fund_classification.py, table
  portfolio.fund_category_taxonomy (provider_key, category → taxonomy_key,
  Super Admin GET / PUT /admin/fund-categories, versions kept),
  backfill_taxonomy_keys.py --funds (proposals with reasons; applies only
  'apply'; 'review' when the name contradicts the category). Live 2nd Act:
  23 funds keyed by category, BTC / Fidelity U.S. Bond / SGOV keyed by hand.
- Taxonomy keys in use: taxonomy_sc_1 (Public Equity), taxonomy_mc_1_1
  (Large Cap), taxonomy_mc_1_2 (Small Cap), taxonomy_mc_1_4 (Emerging
  Markets), taxonomy_sc_2 (Fixed Income), taxonomy_mc_2_1 (Investment Grade),
  taxonomy_sub_2_3_1 (Treasuries), taxonomy_sub_2_3_2 (TIPS),
  taxonomy_sub_2_3_4 (Municipals), taxonomy_mc_7_3 (Digital Assets),
  taxonomy_mc_8_1 (Cash & Equivalents), taxonomy_sub_8_1_1 (Money Market).
  Task 1 lists the full tree (any US / international developed equity key).
- held_away.NO_MASTER_ASSET_TYPES (cash, cash_holding, digital_asset) never
  resolve through the master — keep it (visibility1 fix 1).
- relink_manual_assets (nightly, ~07:00 UTC) links an org's manual asset when
  its identifier resolves to exactly one security; every relink is reported in
  portfolio.asset_relinks + audit log. New mutual-fund tickers WILL make the
  Olivo mutual funds relink on the next nightly — that is intended, but it
  must be reported, and the sprint must not trigger it on a real org.
- Regression: this changes the master every resolver reads → re-run
  verify_secmaster1.py ONCE, flat, after teardown, VERIFY_NESTED=1, 2700 s.
- WSL memory is limited: the sprint must NOT run npm run build, any full
  verify script, or a full load. Network ONLY for small samples from the
  sources in Task 1.

THERE IS NO HUMAN AVAILABLE. Report findings, then continue immediately in the
same response. If uncertain, continue.

STANDING RULES: no interactive prompts. There is NO background-process
notification mechanism in this tool — nothing will ever notify you that a
script finished. Never wait for one. Run every command synchronously in the
foreground; never start a script in the background. Write the verify but DO
NOT RUN IT. Never print a secret. Decimal everywhere. Bi-temporal rule 3; RLS
for every operation. Do not switch on any job; do not run the nightly relink
on a real org. Light / cream UI. Commit ALL sprint files (excluding
sprint_prompts/logs) on the current branch at the end with message
"secmaster2: <one line per change>".

=== TASK 1: DISCOVER === Report findings, then continue immediately.
  1a. The FinanceDatabase 404: the exact URL the attributes job requests, what
      the source publishes today (small sample), and the fix (path / file
      split / compressed file). The held run: how to close it and re-run.
  1b. SEC mutual-fund tickers (company_tickers_mf.json: cik, seriesId,
      classId, symbol): format, size, licence (public domain, SEC fair-access
      rate limit and User-Agent rule — a generic client name, never a personal
      address); how a class maps to the master (one security per share class;
      ticker + SEC series / class ids as identifiers); what other identifiers
      can be had free (OpenFIGI for the ticker → FIGI / share-class FIGI).
  1c. Fund categories available from each source for ETFs and mutual funds
      (FinanceDatabase category_group / category / family; SEC N-PORT asset
      mix where already loaded by sec_nport.py); the taxonomy tree's US /
      international / developed / emerging equity keys; the current
      fund_category_taxonomy rows.
  1d. The load summary today: what the admin page shows; what is missing
      (counts per security type, per identifier type, per provider, attribute
      coverage per field, price coverage, review queue size, last run per
      job).

=== TASK 2: FINANCEDATABASE FIX ===
The attributes provider fetches what the source publishes today (1a); a 404
or format change HOLDS the run with a clear reason and alerts Super Admin
(as now) — never a silent empty load. Close the held run through the
existing path. An operator script (or the existing load script with a flag)
re-runs the attributes load with --dry-run (counts) and --resume.

=== TASK 3: SEC MUTUAL-FUND TICKERS ===
A provider for company_tickers_mf.json behind the providers/base.py
contract: one security per share class (type mutual_fund, or money_market
where the SEC / name says so), identifiers ticker + sec_series_id +
sec_class_id (+ CIK), licence facts recorded in data_providers, rank set.
Through the matcher: an existing security with the same ticker is matched,
not duplicated; conflicts go to the review queue. A one-time load script
(--dry-run counts / --resume) and a monthly platform job "Security data —
mutual funds" (OFF). The sprint runs only --dry-run against the live master
(read-only) and records the counts, plus how many of the 2nd Act org's manual
mutual-fund assets WOULD relink (list them; do not relink).

=== TASK 4: SHARPER FUND CATEGORIES ===
fund_category_taxonomy gains the split the tree supports: US equity vs
international developed vs emerging; large / small cap where the source says
so. Where a category is ambiguous ("Developed Markets"), the fund's own data
decides (FinanceDatabase country / exchange / name tokens such as "U.S.",
"Total Stock Market", "S&P 500", "International", "EAFE", "ex US",
"Emerging") — every proposal carries its reason, and a contradiction (the
name says bitcoin, the category says Corporate Bonds) is REVIEW, never
applied. Mutual funds get categories once linked (Task 3) from the same
sources. backfill_taxonomy_keys.py --funds proposes the refined key for
already-keyed funds too (a REFINE line: old → new, reason) and applies only
when --refine is passed. The sprint runs only --funds --dry-run on the live
org and records the proposals.

=== TASK 5: LOAD SUMMARY ===
The master admin page gains a summary (1d's missing counts), each count a
link to the filtered list; GET /admin/security-data/master/summary (Super
Admin). One query per section, not per row.

=== TASK 6: OPERATOR STEPS ===
docs/SECURITY_DATA_DESIGN.md and PROJECT_STATUS: in order — close the held
attributes run; attributes re-load --dry-run then real; mutual-fund load
--dry-run then real; backfill --funds --dry-run then real (and --refine);
let the nightly relink the mutual funds (or POST
/portfolio/manual-assets/relink) and read its report; household Recalculate.

=== TASK 7: REAL PROOF ===
  - FinanceDatabase: a recorded fixture of today's format parses; a 404
    fixture HOLDS with the reason and alerts; the closed held run lets the
    trigger fire again (injected instant).
  - Mutual funds: a recorded fixture of company_tickers_mf.json loads N share
    classes with their identifiers; a ticker already in the master is matched
    not duplicated; a conflict reaches the review queue; idempotent re-run
    writes nothing; licence facts and rank present; the job is OFF.
  - A fixture org's manual mutual-fund asset relinks to the new security
    (reported in asset_relinks), while a fixture digital asset with ticker BTC
    and a cash_holding still do NOT relink.
  - Categories: fixture SPY / VXUS / EEM / IWM / BND-style funds get US large
    cap / international developed / emerging / small cap / investment grade
    with reasons; a "Developed Markets" US fund is split by its data; the
    bitcoin-named fund with a bond category is REVIEW; --refine applies only
    with the flag; a re-run changes nothing.
  - Summary: every count equals independent SQL over the fixture master.
  - Org isolation both directions (rolbypassrls = false); real rows and the
    live master unchanged (count + checksum of the master's live rows).
  - Own routes via _verify_self; authz1_routes.py --check CURRENT.
  - Regression: verify_secmaster1.py ONCE, flat, after teardown,
    VERIFY_NESTED=1, 2700 s.

=== TASK 8: UPDATE PROJECT STATUS ===
docs/PROJECT_STATUS.md entry (findings, dry-run counts, the would-relink list,
the refine proposals, operator steps); add verify_secmaster2.py to
scripts/regression_set.txt.

=== VERIFICATION: apps/api/scripts/verify_secmaster2.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only, no prompts. MUST print
'TOTAL: N PASS, M FAIL' and exit non-zero on failure. Set AUTHZ_TEST_MODE=1
before importing the app. Hydrate secrets from Doppler over HTTPS. NO network
to any data source: recorded fixtures only, the transports stubbed at their
CURRENT signatures, and any LLM / background task stubbed (assert no network
call). Wrap every section so an exception is a [FAIL]; teardown via
scripts/_verify_cleanup.py at start and end, by fixture id; re-read every
touched table for its exact pre-test count, counting audit_log and other
tables real activity writes to BY FIXTURE IDS ONLY (the nightly runs ~07:00
UTC). Fixture orgs and users is_test = true (assert it). Read UNIQUE / CHECK /
FK constraints from the catalog before seeding (FAIL if none are read).
Reserved fixture prefixes that match no real row (assert it). Support
--self-checks-only and --cleanup-only. The regression subprocess gets
VERIFY_NESTED=1, after teardown, 2700 s; with VERIFY_NESTED=1 skip it with
one '[SKIP] nested run' line.

Verify-script rules — each one broke a real script:
  - check(passed, label, detail): condition FIRST; a real bool.
  - Every import at the top; never `x, y = (await f(a) for a …)`.
  - RLS context on every read (the same org context the route sets);
    rolbypassrls = false asserted; an empty list never passes a check.
  - A refusal check sends a VALID body, so only the gate can refuse.
  - A recursive CTE has ONE anchor and ONE recursive term.
  - A test stub must match the CURRENT signature of what it replaces.
  - Never seed a state the database forbids (read the unique indexes first).
  - Key a real table's fingerprint by its real key; exclude only columns
    real activity updates, and name them.
  - No check frozen against live state.
  - Parse a nested script's OWN final ^TOTAL line (shared helper).
  - A failing check's detail prints the response body / culprit rows.

Assertions:
  [Y] Task 1 findings 1a–1d
  [Y] FinanceDatabase format fixed; 404 holds + alerts; held run closable
  [Y] Mutual-fund provider: load, match not duplicate, review, idempotent,
      licence, rank, job OFF; dry-run counts and would-relink list recorded
  [Y] Relink: mutual fund links; digital asset / cash never
  [Y] Categories: US / intl / EM / cap split with reasons; contradictions to
      review; --refine only with the flag
  [Y] Load summary == SQL
  [Y] Isolation; own routes; --check CURRENT; real rows + live master unchanged
  [Y] node --test tests/ passes; npm run build exits 0 (run by this script)
  [Y] Regression: verify_secmaster1.py ONCE, flat, after teardown,
      VERIFY_NESTED=1, 2700 s; 0 FAIL
  [Y] Teardown leaves zero fixture rows
