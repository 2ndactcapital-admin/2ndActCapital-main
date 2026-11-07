# SECMASTER2 — Discovery (Task 1, 2026-11-07)

Repo at `6379e28` (secmaster2-work @ merge visibility1) and the live database, read 2026-11-07. Network: small samples only
(HTTP HEAD / byte ranges, the GitHub contents listing, and the one 1.2 MB SEC file through `services/edgar_fetch`).

## 1a. The FinanceDatabase 404

**What the job requests.** `services/security_data/providers/finance_database.py` builds
`https://raw.githubusercontent.com/JerBouma/FinanceDatabase/main/database/<kind>/<EXCHANGE>.csv` for
`US_EQUITY_FILES = (NMS, NGM, NCM, NYQ, ASE, PCX, BTS)` and `US_ETF_FILES = (PCX, NGM, NMS, BTS, NYQ, ASE)`.

**The held run.** `workflow_runs 12611760-722c-40b0-872c-a84fdc8f82a6` ("system:Security master — attributes",
2026-11-06 20:10 UTC), status **held**:
`PlatformJobError: security_master_attributes: Client error '404 Not Found' for url
'https://raw.githubusercontent.com/JerBouma/FinanceDatabase/main/database/etfs/ASE.csv'`.

**What the source publishes today** (GitHub contents API, `database/`): `cryptos.csv`, `currencies.csv`,
`indices.csv`, `moneymarkets.csv` and three directories — `equities/`, `etfs/`, `funds/` — one CSV per Yahoo exchange
code. Every configured file answers **200 except `etfs/ASE.csv`**: FinanceDatabase publishes `equities/ASE.csv`
(NYSE American stocks, 516 KB) but **no** `etfs/ASE.csv` (its NYSE American ETFs are under PCX). ASE was the LAST ETF
file in the list, so the held run had already applied every other file (the matcher is idempotent: a re-run rewrites
nothing for them). `etfs/NCM.csv` IS published (1.2 KB) and was never read. Formats (sampled by byte range):

    equities/<EX>.csv  symbol,name,summary,currency,sector,industry_group,industry,exchange,mic,market,country,state,
                       city,zipcode,website,market_cap,isin,cusip,figi,composite_figi,shareclass_figi,delisted
    etfs/<EX>.csv      symbol,name,currency,summary,category_group,category,family,exchange,mic,isin,delisted
    funds/NAS.csv      symbol,name,currency,summary,category_group,category,family,exchange,mic      (13.8 MB)

`funds/NAS.csv` is **US mutual funds** (Nasdaq's mutual-fund symbols: AAAAX, AAAGX …) with a category — not read
before. No path change, no file split, no compressed file: the fix is the file LIST (ETF: ASE out, NCM in; a new
`funds` kind with `US_FUND_FILES = (NAS,)`), plus making a missing file or column a clear, held failure
(`SourceFormatChanged`) instead of httpx's raw text.

**Close it and re-run.** The existing path: Super Admin → Security master → Platform jobs → "Close held run" (POST
`/admin/security-data/master/runs/{id}/close`, `security_master_jobs.close_held_run` → `cancelled`, alert todo
closed). The trigger is inactive, so nothing fires on its own; the operator re-runs the load with
`scripts/secmaster_initial_load.py --source finance_database --dry-run` then without `--dry-run` (it has `--resume`).

## 1b. SEC mutual-fund tickers

`https://www.sec.gov/files/company_tickers_mf.json` (fetched through `services/edgar_fetch` — the declared
`EDGAR_USER_AGENT`, whose contact is a company address, not a personal one; a bare client name is refused with 403):
1,237,746 bytes, Last-Modified 2026-11-04.

    {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[2110, "S000009184", "C000024954", "LACAX"], …]}

* **28,647 rows = 28,647 distinct share classes** (classId unique, symbol unique; ONE row has a blank symbol),
  12,304 series, 1,160 CIKs. Symbol lengths: 22,811 five-letter, 4,845 four, 818 three, 31 two, 82 six, 59 seven;
  23,194 end in X.
* **It is not only mutual funds**: the ETF share classes of open-end funds are in it — VTI (C000007808), VOO, BND,
  IVV, and QQQ (S000101292, since its conversion). SPY and GBTC (UITs / grantor trusts) are not.
* **no names, no type.** The SEC's series / class name files
  (`investment-company-series-class-<year>.csv`, ≈700 KB each) are annual deltas, not a complete list.
* **Licence**: a U.S. government work — public domain (17 U.S.C. 105): may be shown and redistributed. The SEC
  fair-access policy: a declared User-Agent naming the company and a company contact (never a personal address), at
  most 10 requests per second — both enforced by `services/edgar_fetch` (its limiter; `user_agent()` refuses to run
  without one).
* **How a class maps to the master**: one security per share class; identifiers `ticker` + `sec_series_id` +
  `sec_class_id` (the CHECK admitted neither — widened by `docs/secmaster2_part1.sql`, with a partial unique index on
  the live `sec_class_id`); the CIK as the `cik` attribute. Security type: the CHECK has no `mutual_fund` /
  `money_market` (and X-ray, options and the replica builder read `'fund'`), so the type is `fund` and the structure
  is the new attribute `fund_structure` (`mutual_fund` / `money_market` / `etf`). A five-letter ticker ending in X is
  a mutual fund (Nasdaq's Mutual Fund Quotation Service); `…XX` a money market fund — 21,854 / 938 of today's rows;
  5,854 other classes (ETF and other exchange-traded classes) are matched only, never created by this source.
* **Other free identifiers**: OpenFIGI maps a ticker to a FIGI and a share-class FIGI (`idType TICKER`,
  `exchCode US`), within the existing keyless budget (25 jobs/request). Not wired for mutual funds in this sprint
  (`sync_openfigi_new` asks only for LISTED tickers): a follow-up, so 23k classes do not eat the 13(f) mapping's
  budget. CUSIPs are not free.

## 1c. Fund categories by source; the taxonomy tree

* **FinanceDatabase** — ETFs: `category_group` / `category` / `family`; mutual funds (`funds/NAS.csv`): the same
  three. The loader kept only `category` (falling back to the group), and `category` alone is ambiguous: sampled
  today, "Emerging Markets" is filed under Equities AND **Fixed Income** (EM debt), "Large Cap" under Equities AND
  Derivatives (leveraged / inverse), "Developed Markets" holds VTI, SCHB, SCHD (US) next to VEA, EFA (international),
  and mutual-fund categories are styles ("Value", "Growth", "Blend") whose group decides. ETFs and mutual funds carry
  **no country** (only equities do). So the group is now its own attribute (`fund_category_group`) and the fund's
  name decides region / cap where the category does not.
* **SEC N-PORT** (`sec_nport.py`, rep1's holdings parser, `assetCat` per holding): `portfolio.replica_holdings_snapshots`
  holds **0** snapshots today — no asset mix is loaded to classify from (honest "nothing there").
* **The tree** (2nd Act `config.asset_taxonomy`, active): taxonomy_sc_1 Public Equity → taxonomy_mc_1_1 US Large Cap
  (sub_1_1_1 blend / 1_1_2 growth / 1_1_3 value / 1_1_4 US Dividend / Income), taxonomy_mc_1_2 US Small/Mid Cap
  (sub_1_2_1 mid / 1_2_2 small blend / 1_2_3 small growth / 1_2_4 micro), **taxonomy_mc_1_3 International Developed**
  (sub_1_3_1 Europe / 1_3_2 Japan / 1_3_3 Asia Pacific ex-Japan / **taxonomy_sub_1_3_4 Global ex-US Blend** /
  1_3_5 International Small Cap), taxonomy_mc_1_4 Emerging Markets (sub_1_4_1 … 1_4_5 Frontier); Fixed Income
  taxonomy_mc_2_1 IG, 2_2 HY, 2_3 Government & Rates, **2_4 Emerging Market Debt**, 2_5 Securitized.
* **fund_category_taxonomy**: **0 rows** live — the effective map is rollup2's `DEFAULT_CATEGORY_MAP` (code) alone;
  "Developed Markets" → taxonomy_sc_1 (super class only) is why the US / international split never happened.

## 1d. The load summary

What `/admin/security-data/master` showed (`security_master_jobs.status`): securities by type (equity 8,666 · fund
6,133 · other 917 · fixed_income 187 · structured_note 54 · index 13 = 15,970), identifiers by type (ticker 15,916 ·
figi 12,515 · cusip 10,724 · isin 10,673), securities per source, listing status, open review items by kind (715),
13(f) unmapped by reason, the jobs with their last run.

Missing: **funds by structure** (mutual fund / money market / ETF); **securities WITHOUT each identifier**; **attribute coverage**
per field (sector 6,375, country 6,389, fund_category 5,114 of 15,970 — and the new fields 0);
**price coverage** (all 15,970 'unknown', 0 with a platform price); the **review queue** linked to its list; the **last run
per job** as a list; and **no count linked to the securities it counts** — there was no master-wide list at all
(the securities grid lists one org's assets). GET `/admin/security-data/master/summary` + `/securities` add them, one
query per section.
