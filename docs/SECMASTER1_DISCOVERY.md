# SECMASTER1 — Task 1 discovery (2026-11-04)

Small samples only (first lines / one page / one request each). Two servers ignored a Range header and returned
the whole file (nasdaqtrader `otherlisted.txt` 543 KB, sec.gov `company_tickers_exchange.json` 522 KB); samples
were kept, the full copies deleted, and every later request was truncated client-side. Recorded fixtures:
`apps/api/scripts/fixtures/secmaster1/`.

## 1a — formats

**Nasdaq Trader symbol directory.** `nasdaqlisted.txt` (348 KB) `Symbol|Security Name|Market Category|Test Issue|
Financial Status|Round Lot Size|ETF|NextShares`; `otherlisted.txt` (543 KB, ~7.7k rows) `ACT Symbol|Security Name|
Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol`, exchange A (NYSE American) / N (NYSE) / P (Arca) /
Z (Cboe BZX) / V (IEX) / M (Chicago) / F (Texas Stock Exchange). Footer line `File Creation Time: 1104202612:11|||…`
(MMDDYYYYHH:MM, US Eastern). Test issues `Test Issue = Y` (ZZZT*, ATEST.*, …; 29 in otherlisted). Last-Modified
17:11 UTC on a weekday → refreshed intraday; the nightly runs at 18:45 New York.

**SEC company tickers.** `{"fields":["cik","name","ticker","exchange"],"data":[…]}`, 10,409 rows; exchange Nasdaq
4,358 / NYSE 3,268 / OTC 2,569 / null 158 / CBOE 44 / TXSE 12. Classes and preferreds hyphenated (BRK-B, PCG-PA).

**FinanceDatabase.** github.com/**JerBouma**/FinanceDatabase (not "JetBrains"). MIT licence on the repository;
data compiled from third-party sources; bot commits several times a week. NOT one CSV per asset class: one CSV per
Yahoo exchange code under `database/equities/` and `database/etfs/` (US: NMS, NGM, NCM, NYQ, ASE, PCX, BTS; OTC
PNK is ~14 MB). Equity columns include sector, industry_group, industry, country, isin, cusip, figi, composite_figi,
delisted; ETF columns category_group, category, family. `summary` is quoted and spans lines.

**OpenFIGI v3 /mapping.** Without a key: 10 jobs per request, `ratelimit-policy: 25;w=60`. With
`OPENFIGI_API_KEY` (header `X-OPENFIGI-APIKEY`): 100 jobs per request, 25 requests / 6 s. A miss answers
`{"warning": "No identifier found."}`. Tickers are Bloomberg form (`BRK/B`). With `exchCode: "US"` one composite
FIGI per security. `OPENFIGI_API_KEY` is **absent** from Doppler (checked by name).

**SEC Official List of Section 13(f) Securities.** The list page links each quarter's text file as
`/files/investment/13flist{YYYY}q{N}.txt` — some quarters `…-txt.txt` — so the link is read from the page. Fixed
width 80, no header: [0:9] CUSIP, [9] `*` = options on it are listed, [10:40] issuer, [40:67] description
(CALL / PUT rows are the options), [67:70] `*A*` added / `*D*` deleted, [79] constant `E`.

## 1b — the existing master

67 securities: 54 structured notes (51 CUSIPs), 13 indices (13 tickers: AS51, INDU, MXEA, NDX, NDXE, NDXT, RTY,
SMI, SPX, SPXFP, SX5E, TPX, UKX). No equity, no duplicate ticker, no ticker with a separator (global or tenant).
None of the 13 index tickers appears in `otherlisted.txt`; any listed equity that shares a ticker with an index
becomes a `ticker_conflict` review item (indices carry no exchange), never a match.

## 1c — platform jobs on the workflow engine

No platform scope exists on workflows / triggers / runs (RLS `org_id = current OR is_super_admin`). The Hollisworks
org (bb347258-…) exists and its only user is a Super Admin → the jobs are `system:` workflows there. A 2nd Act org
admin's Runs list filters `r.org_id = <own org>`; a Super Admin's lists all orgs. Held-run alerts go to
`started_by` (the trigger creator = the platform Super Admin) and the platform org's org admins. Service Task →
`workflow_invocable` action (tier ≥ 2, else it suspends for approval); the engine re-checks
`required_permission` against the starter (Super Admin bypass). 0 active scheduled triggers existed. A step runs
inside the 5-minute cron tick → bounded steps (deadline 200 s, max new per night); the initial load is the
operator's script. There is no un-hold path for a held run → `POST …/master/runs/{id}/close` (Super Admin).

## 1d — symbols

Canonical: dotted. Classes `BRK.B`; preferreds `ASB.PRE` (`ASB$E`, `ASB-PE`); warrants `.WS`; units `.U`; rights
`.R`; Nasdaq's own fifth-letter suffixes (AACIU) unchanged. `normalize_identifier_value('ticker', …)` applies it
to every ticker write and lookup.

## 1e — search

Muse's failing lookup (BUGFIX2 REP-05) is the model-upload resolver → `get_by_identifiers_bulk` (exact ticker).
Pickers and the securities grid use `list_global_securities` (`ILIKE '%term%'` on name / short_name / identifier
value). `pg_trgm` was not installed → trigram GIN indexes on all three columns, and the correlated identifier
`EXISTS` became one uncorrelated `ANY(ARRAY(…))`.
