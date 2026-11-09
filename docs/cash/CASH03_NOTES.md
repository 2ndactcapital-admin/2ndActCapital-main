# CASH03 — discovery notes: the obligation-matched ladder and after-tax instrument selection (2026-11-09)

Checked against the repo at `7030b41` (merge cash02) and the live database, 2026-11-09 (read-only queries). Standing
rule (Joe, 2026-11-08): **use the app's existing constructs; create a new one only where none can carry it**, named
and justified below.

Source documents: `CASH_TREASURY_DESIGN_v1.md` §2.3, §2.6, §3.4, §5, §6, §7; `CASH00_DISCOVERY.md`,
`CASH_OBLIGATIONS.md` (cash01); `CASH02_NOTES.md`, `CASH_POLICY.md` (cash02). **[FIND]** `CASH_TRACK_PLAN_v1.2.md` is
still not in the repo (cash01 and cash02 found the same). Nothing is invented from it.

---

## 1. Earlier cash decisions that bind this sprint

| Decision (sprint) | What cash03 does with it |
|---|---|
| ONE merged obligation read, `cash_obligations.schedule`, via `cash_coverage.household_report` → `scope_lines` / `scope_liquid` (cash01, cash02) | The ladder sizes rungs on exactly those lines. No second schedule and no second projection. |
| Sleeve windows are half-open months from today; a boundary obligation goes to the LATER sleeve; `cash_policy.windows` (cash02) | The Long-term window is `CP.windows(...)`'s `long_term` entry, never re-derived. Rung count = the policy's `rung_count`. |
| Sleeve $ are derived, never stored; what a checker approved lives in the human task payload (cash02) | The ladder and the instrument plan are DERIVED too (§9 D3). Nothing in dollars is stored. |
| Read gate `view_portfolio | manage_portfolio` + `member_entity_scope` + VISIBILITY1 path guard; a member reads their own family read-only with library labels (cash01) | The plan rides in the SAME treasury envelope, built only for the ACTIVE policies the caller can already see. |
| Authoring permission `manage_cash_policy`, `require_internal_permission` (cash02) | Regenerating the plan uses the same permission (§9 D9). |
| Money in Decimal, fixed-point text at the JSON boundary (`money_text`) | Same. Yields are Decimal percent and are text in JSON. |
| cash02 after-tax = Treasury exemption only, through `tax_profiles.approved_profile` | Replaced, per instrument, by the exemption table in §6. cash02's proxy labels stay wherever no instrument yield exists. |
| Read-only on money | The ladder is a TARGET plan. No proposal, order, instruction or trade is created (cash04). |

## 2. The provider registration shape

**[FIND] Two registries exist, and the prompt names the wrong one.**

| | `services/market_data/registry.py` (mkt01) | `portfolio.data_providers` + `services/security_data` (marketdata1) |
|---|---|---|
| What it is | Seed loader for `market_data.indicator_series` (macro series), keyed by `series_key` | THE provider registry. One row per provider with `kind`, `status`, `capabilities`, licence facts, `daily_call_budget`. A CODE module per provider sits in `services/security_data/providers/__init__.build_providers()` |
| Licence facts | `license_class` / `cost_tier` text on each series | `lic_internal_use`, `lic_member_display`, `lic_redistribution`, `retention_after_cancel`, `attribution`, `terms_note`, `confirmed_on`; judged by THE licence rule `registry.point_allowed` |
| Switch on / off | `ingest_status` per series | `status` ∈ active / not_connected / retired. **Super Admin** flips it in the existing provider admin (`security_data.admin.update_provider`, `/admin/security-data`); `active` is refused unless the code module reports `connected` |
| Read path | `market_data.read_service.read_series` | the rank walk `security_data.layer.get_bulk(conn, org, data_type, subjects, as_of, purpose)` |
| Nightly | `market_data.nightly.run_nightly` (adapter per `source_provider`) | `security_data.nightly.run_nightly` (refresh → platform quality → org quality → one run row) |

`fred.py`'s shape, which the new providers follow: an injectable transport, `HttpTransportBase`-style throttle and
retry, `scrub()`, `Decimal`-only parsing, and a provider class with `key`, `capabilities`, `fetches`, `read()` (DB
only) and `fetch()` (network through the injected transport only). `services/security_data/providers/fred.py` is
the marketdata1 twin of that client; it writes `portfolio.index_levels` under provider `fred`.

**Decision.** Both new sources are `portfolio.data_providers` rows plus code modules in the security-data code
registry. They are inserted `not_connected` (switched OFF), with licence facts and a call budget, by
`docs/cash03_part1.sql`.

- `services/market_data/treasury_fiscal.py` (the file the prompt names): parser, client and provider.
- `services/edgar_nmfp.py`: the N-MFP extractor on the EDGAR pipeline.

**[FIND] The full Treasury curve already exists**: `portfolio.index_levels` holds DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2,
DGS5, DGS7, DGS10, DGS20, DGS30 and DFF under provider `fred` (last 2026-11-04, from marketdata1's nightly). cash02's
"no 3m / 6m / 1y Treasury" was true only of the mkt01 indicator registry. **cash03 reads the curve from
`index_levels` through the rank walk.** cash02's `YIELD_PROXIES` are left as they are (its labels stay, per the
prompt).

**[FIND] Licence consequence.** `data_providers.fred` says `lic_member_display = not_allowed`. Through THE licence
rule, a curve-interpolated yield is therefore internal-only. A member's plan shows:

- auction yields from Treasury Fiscal Data (public domain, `allowed`);
- N-MFP 7-day yields (SEC public record, `allowed`);
- otherwise "not shown — its licence does not allow showing values to members".

cash02 shows indicator-store FRED proxies to members. That inconsistency is recorded, not changed: changing a
provider's licence facts is a Super Admin decision.

## 3. N-MFP and the EDGAR pipeline

**[FIND] The EDGAR pipeline does NOT know N-MFP today.**

- `portfolio.edgar_index_filings` holds 558,563 `424B2` and 173,777 `FWP` rows, and nothing else.
- `portfolio.edgar_sources` has one source, `structured_notes`.
- `edgar_index.FORMS` is the structured-note set.

What the pipeline provides, and what N-MFP reuses:

| Piece | Reused as |
|---|---|
| `edgar_index.parse_index_lines(lines, forms=…)` | discovery of `N-MFP2` / `N-MFP3` filings from an EDGAR master / daily index (the `forms` override bulkfetch added) |
| `edgar_fetch._get` / `make_client` (declared User-Agent, the 10/s limiter) | every sec.gov request (the real transport) |
| `edgar_fetch.FilingMeta.archive_base` | the filing folder; an XML-form filing's document is `primary_doc.xml` |

The filings are NOT inserted into `edgar_index_filings`: that table's pipeline status machine (select, R2 store,
classify) is the structured-note pipeline's own. N-MFP is a small, monthly, per-fund fetch whose output is a yield,
not a stored document.

**What a filing yields** (Form N-MFP2 until June 2024, N-MFP3 after the 2023 money-market-fund amendments; monthly,
filed within 5 business days after month end; XML):

| Element (local name) | Level | Used as |
|---|---|---|
| `reportDate` | general | the as-of of WAM / WAL |
| `seriesId` (`S\d{9}`) | general | security-master link: identifier `sec_series_id` (shared) |
| `moneyMarketFundCategory` | series | reference attribute `mmf_category` → instrument kind (Treasury / Government / Prime / tax-exempt) |
| `averagePortfolioMaturity` (WAM, days) | series | `index_levels` `nmfp:<series>:wam` |
| `averageLifeMaturity` (WAL, days) | series | `index_levels` `nmfp:<series>:wal` |
| `classesId` (`C\d{9}`) | class | security-master link: identifier `sec_class_id` (unique) — matched first |
| `sevenDayNetYield` | class | `index_levels` `nmfp:<class>:7d` (percent), dated by its own date when the form gives one, else `reportDate` |

**Caveat, stated plainly.** No live filing was fetched (no network in the sprint). The element names above come from
the forms' published XML technical specifications as the sprint knows them. The parser therefore:

- matches local names, whatever the namespace;
- accepts BOTH a bare value and a list of dated values (the latest date wins);
- REFUSES, with a reason, any filing it cannot read: no series id, no class id, no yield, or a yield outside
  [−5 %, 25 %] once scaled.

It never guesses. The fixtures under `apps/api/tests/fixtures/cash03/` are hand-built to those specifications. The
operator's first `load_cash_market_data.py --real` prints the parse of each real filing before it is used.

N-MFP reports yields as **decimal fractions** (0.0512 = 5.12 %). The parser multiplies by 100. A value already above
1 is refused as "not a fraction".

## 4. Treasury Fiscal Data — endpoints and licence

| Need | Endpoint (base `https://api.fiscaldata.treasury.gov/services/api/fiscal_service`) |
|---|---|
| Bills / notes: CUSIP, type, term, auction / issue / maturity dates, results | `GET /v1/accounting/od/auctions_query` — `fields=cusip,security_type,security_term,auction_date,issue_date,maturity_date,high_yield,high_investment_rate,high_discount_rate,int_rate`, `filter=auction_date:gte:<date>`, `sort=-auction_date`, `page[size]=…`, `page[number]=…` |
| Upcoming auctions (not used by cash03; noted for cash04's reinvestment) | `GET /v1/accounting/od/upcoming_auctions` |

- **Response**: `{"data": [...], "meta": {"count", "total-count", "total-pages"}, "links": {...}}`. Values are TEXT;
  the source's missing-value marker is the string `"null"`.
- **The yield used**:
  - `high_yield` for notes and bonds;
  - for bills, `high_investment_rate` (the bond-equivalent "investment rate"), because a bill's `high_yield` is
    `"null"`. The basis is labelled accordingly.
  - The discount rate is never shown as a yield.
- **Licence**: a work of the U.S. Government, **public domain** (17 U.S.C. §105). No key, no registration. Free to
  use, redistribute and keep. Registered with `lic_internal_use = true`, `lic_member_display = allowed`,
  `lic_redistribution = true`, `retention_after_cancel = keep`, attribution "U.S. Department of the Treasury,
  Fiscal Data (fiscaldata.treasury.gov)".
- **N-MFP licence**: an SEC public filing, the same facts, attribution "U.S. Securities and Exchange Commission, EDGAR
  (Form N-MFP)", requests under the SEC's fair-access rules (declared User-Agent, ≤ 10 requests/s, via
  `edgar_fetch`).
- **Muni yields: [FIND] no free registered source.** Muni bond yields read "muni yield: needs a source" and are never
  chosen. Tax-exempt money-market fund 7-day yields DO come from N-MFP (category "Single State" / "Other Tax Exempt").

## 5. How account tax treatment is stored today

`public.accounts`:

- `tax_status`: config `account_tax_statuses` — `taxable`, `tax_deferred`, `tax_exempt`.
- `registration_type`: config `account_registration_types` — individual, joint, trust, llc, lp, corporation,
  foundation, ira, roth_ira, 401k_rollover, 529, daf, insurance, other.
- Both are Rule 1 vocabularies (portentry2).
- The owner's rates come from `tax_profiles.profile_for_account(conn, org, account, tax_year)`, i.e.
  `subject_of(account)`: the account's household, else its primary entity. Only an APPROVED live profile counts.

| Treatment (from `tax_status`) | After-tax rule |
|---|---|
| `taxable` + an approved profile | yield × (1 − the rates that apply to the instrument, §6) |
| `taxable`, no profile | net only, labelled "after-tax: needs a tax profile"; the choice is made on net yield and says so |
| `tax_deferred` (IRA, 401(k), annuity / insurance) | tax does not apply now: after-tax = net, the highest net yield wins regardless of exemption |
| `tax_exempt` (Roth, 529, DAF, foundation, a charitable trust) | the same as tax-deferred |

**[FIND]** A trust's grantor / non-grantor status is not recorded anywhere. A taxable trust account uses the
profile of its subject (its household), labelled with the registration type.

**[FIND]** No state of domicile is stored on the account. A single-state muni fund's state is not in N-MFP's category
either. A muni MMF is therefore treated as federally exempt and state- / city-TAXABLE (conservative), with that
stated in its why.

**Live (Olivo demo, 2026-11-09).**

- 36 accounts: taxable trusts / LLCs / LPs / an individual; tax-deferred IRAs, a 401(k) rollover and an insurance
  account; tax-exempt Roths, 529s, a DAF, the foundation and one trust.
- **0 rows in `account_balances_daily`.**
- **No cash policy, no tax profile.**

## 6. The exemption table (instrument × tax)

| Instrument kind | Federal | State / local | Source of its yield |
|---|---|---|---|
| `treasury_mmf` (N-MFP category Treasury / Exempt Government) | taxed | **exempt** | N-MFP 7-day net yield |
| `government_mmf` (Government / Agency) | taxed | taxed — the exemption depends on the fund's US-obligation share, not applied (stated) | N-MFP |
| `muni_mmf` (Single State / Other Tax Exempt) | **exempt** | taxed (state of domicile unknown, §5) | N-MFP |
| `bill`, `note`, `treasury_ladder` | taxed | **exempt** | auction yield (Treasury Fiscal Data), else the FRED curve interpolated (labelled) |
| `muni_bond` / `muni_ladder` | exempt | exempt in-state | **none — "muni yield: needs a source"**, never chosen |
| `prime_mmf` | taxed | taxed | N-MFP — NOT a candidate (v1 §2.3 names government or muni funds only) |
| `cd` | taxed | taxed | none registered. Used only for the T-bill-vs-CD hurdle (the roadmap fact) |

- Federal rate = `federal_ordinary_rate` + `niit_rate` when `niit_applies`.
- State = `state_rate`; city = `city_rate`. Interest is ordinary income, so the LT rates never apply.
- **The roadmap fact, as a test case.** For a NY-domiciled taxable member, a CD (fully taxable) must yield
  `y × (1 − fed) ÷ (1 − fed − state − city)` to match a T-bill yielding `y`. At federal 37 % + NIIT 3.8 %, NY 10.9 %,
  NYC 3.876 % and y = 4.00 %, that is 5.3304 %: a hurdle of **133 bp**.

## 7. The security-master add path for bills, notes and money-market funds

`services/security_master_load.Matcher(provider_key, as_of).process(pool, records)` is the ONE bulk load path:

- match order CUSIP → FIGI → ISIN → `sec_class_id` → ticker;
- create only for `CREATORS`;
- identifiers per `IDENTIFIER_SOURCES`;
- reference attributes per (security, field, provider), restated on the valid axis;
- conflicts become `security_master_review` items, never overwrites.

cash03 extends it (no side table):

| Provider | Creates? | Identifiers | security_type | Attributes (new fields) |
|---|---|---|---|---|
| `treasury_fiscal` | yes (by CUSIP) | `cusip`, `isin` (derived) | `fixed_income` | `treasury_type` (Bill / Note / Bond …), `security_term`, `issue_date`, `maturity_date`, `country` = US |
| `sec_nmfp` | yes — by `sec_class_id` (a money-market class need not have a ticker; a new rule) | `sec_series_id`, `sec_class_id` | `fund` | `fund_structure` = money_market, `mmf_category` |

The reference-field CHECK (`security_reference_attributes_field_chk`) gains `maturity_date`, `issue_date`,
`security_term`, `treasury_type` and `mmf_category`. A date-shape CHECK covers the two date fields (`cash03_part1.sql`).

A class N-MFP names that is already in the master (from `sec_mutual_funds`) is MATCHED on its class id and gains its
category. Nothing is ever created outside `Matcher`.

## 8. Reuse inventory

| Piece | Construct used | New? |
|---|---|---|
| Provider rows, licence facts, switch-on | `portfolio.data_providers` + `security_data.admin.update_provider` (Super Admin) | 2 rows, inserted `not_connected` |
| Rank (who answers a read) | `portfolio.data_source_rank`: `index_level` gains treasury_fiscal (4), sec_nmfp (5); `reference` gains treasury_fiscal (7), sec_nmfp (8) | rank rows |
| Treasury issues, MMF classes | `portfolio.securities_global` / `_identifiers` / `security_reference_attributes` through `Matcher` | 5 reference fields |
| Auction yields, 7-day yields, WAM / WAL | `portfolio.index_levels` through `security_data.writes.upsert_index_levels` (valid-axis restatement) | — |
| The Treasury curve | `portfolio.index_levels` (fred, marketdata1) through `layer.get_bulk` | — |
| Nightly cadence | `security_data.nightly.run_nightly`: a new `cash_sources` step (daily Treasury, monthly N-MFP), acting only on ACTIVE providers | step |
| Staleness | `security_data.settings.is_stale` (THE rule) with per-provider limits (Treasury 10 days, N-MFP 45 days) used by the platform-quality step | limits |
| Call budget / log | `security_data.writes.budget_left` / `record_calls` (`provider_call_log`) | — |
| EDGAR access | `edgar_fetch._get` / `make_client`, `edgar_index.parse_index_lines(forms=…)` | — |
| Obligation schedule, windows, liquid | `cash_coverage.household_report`, `scope_lines`, `cash_policy.windows` | — |
| Business days | **`services/us_business_days.py`** | NEW — no holiday calendar exists (`authority.CALENDAR_NOTE` and `fee_calc` both record its absence). US federal-holiday rules with the observed-date shifts; pure |
| Ladder | **`services/cash_ladder.py`** (pure + the curve / issue reads) | NEW module, no table |
| Instrument plan | **`services/cash_instruments.py`** (pure + reads) | NEW module, no table |
| Tax rates | `tax_profiles.profile_for_account` / `approved_profile` | — |
| Per-account allocation weights | `account_balances_daily.cash_value` (latest date per account; one row per date, the first `source_system` alphabetically — never a SUM across sources, which double-counts) | — |
| Labels | disclosure library: `cash.yield_estimate` (cash02) + NEW key `cash.tax_estimate` | key only |
| Visibility | `authz.member_entity_scope` + `family_visibility.require_visible` | — |
| Audit | `audit.write_audit_log` (`cash.plan.regenerated`) | — |
| Screens | Household → Treasury tab: `LadderPanel` beside cash02's `PolicyPanel`; `/treasury` for a member | no new tab |

## 9. Decisions

- **D1 — registry.** `portfolio.data_providers` + the security-data code registry (§2). The prompt's
  `services/market_data/registry.py` is the indicator seed loader and carries no provider licence facts.
- **D2 — stores.** No new table. Issues and fund classes go to the security master; yields to `index_levels`
  (`tfd:<cusip>:yield`, `nmfp:<class>:7d`, `nmfp:<series>:wam|wal`). Every key fits `index_levels_key_chk`
  (`^[A-Za-z0-9_.^:-]{1,60}$`).
- **D3 — the ladder is NOT stored.** It is a derived plan for a policy version, recomputed at read, like cash02's
  sleeve targets. Nothing in cash03 approves a plan for execution, so `public.cash_ladder_rungs` is not built. cash04
  snapshots the rungs it proposes, at proposal time, with the policy version id.
- **D4 — switched off.** Both providers ship `not_connected`. The nightly step skips a provider that is not `active`.
  So does the read path (`registry.source_allowed` → "not connected"), so stored values are not used until a Super
  Admin switches the provider on.
- **D5 — licence per caller.** The plan reads with purpose `internal` for staff and `member_display` for a member
  (§2).
- **D6 — the threshold.** A Long-term obligation is LARGE when its amount ≥ 10 % of the Long-term sleeve target, or
  it is FIRM and ≥ $250,000. Large obligations on the same date share one targeted rung.
- **D7 — candidates.**
  - Near-term: Treasury / Government / muni MMFs.
  - Reserve: a T-bill (target date = the Reserve window's midpoint) and the MMFs. The ultra-short bond fund reads
    "30-day SEC yield: needs a source".
  - Long-term: the Treasury ladder (par-weighted rung yields) and the muni ladder ("needs a source").
- **D8 — the plan per account.** Every open account in the scope gets a choice per sleeve. A sleeve's $ is split
  across accounts by each account's latest cash balance. One account → weight 1. Several accounts and no balance →
  the split and the blended figure read "not allocated — needs each account's cash balance" (the per-account
  choices still show).
- **D9 — permissions / audit.** The plan is viewed through the treasury read gate (as cash02's policy block).
  `POST /cash/households/{id}/plan/regenerate` needs `manage_cash_policy` (cash02's authoring permission) and audits
  `cash.plan.regenerated` with the policy versions, rung counts and a hash of the plan. A provider is switched on in
  the existing provider admin (Super Admin only).
