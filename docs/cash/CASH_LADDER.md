# The obligation-matched ladder and after-tax instrument selection (CASH03)

A ladder and an instrument plan are a **TARGET**, never a trade. Nothing here creates a proposal, an order or an
instruction (cash04). Both are **derived** at read time from the ACTIVE cash policy (cash02) and nothing is stored.

- Code: `services/cash_ladder.py` (the ladder), `services/cash_instruments.py` (the after-tax plan),
  `services/us_business_days.py` (the calendar), `routers/cash.py` (`plan` in the treasury envelope +
  `POST …/plan/regenerate`).
- Providers: `services/market_data/treasury_fiscal.py`, `services/edgar_nmfp.py`.
- Catalog: `docs/cash03_part1.sql`.
- Screens: Household → Treasury → "Ladder and instrument plan" (`components/treasury/LadderPanel.jsx`,
  `lib/cashPlan.mjs`), and `/treasury` for a member.
- Discovery and decisions: `CASH03_NOTES.md`.

## Ladder rules

| # | Rule |
|---|---|
| 1 Window | The Long-term window is cash02's `windows(...)['long_term']`: months [R, horizon) from today, half-open, month *m* starting on `cash_obligations.add_months(today, m)`. Only COUNTED obligations of the policy's scope (`cash_coverage.scope_lines`) dated inside it are laddered. |
| 2 Large | An obligation is LARGE when its amount is at least **10 %** of the Long-term sleeve target (Σ the window's obligations), or it is FIRM and at least **$250,000**. Large obligations on the same date share ONE targeted rung. |
| 3 Rung count | At most the policy's `rung_count` rungs. More large dates than rungs → the largest (then earliest) keep their targeted rung; the rest are funded as small obligations, and a note says so. |
| 4 Fillers | The remaining rungs are MONTHLY fillers, on the first day of each Long-term month that holds no targeted rung. With more such months than free rungs they are spread evenly: indices ⌊i × n ÷ slots⌋, which always includes the window's first month. |
| 5 Maturity | On or before the date the rung funds. A REAL issue is used when one exists: the Treasury bill / note in the security master maturing LATEST on or before the target, and within 35 days of it. Otherwise an **indicative** maturity: the target moved to the prior business day, labelled "indicative maturity — no matching issue". |
| 6 Type | The issue's own (bill / note). An indicative rung is a bill up to 364 days out, else a note. A muni rung needs a muni yield source; none is registered, so no rung is a muni. |
| 7 Sizing | A targeted rung's par = Σ the large obligations it targets. Each SMALL obligation is funded by the rung with the latest maturity on or before its date (that rung's par grows). One that falls before every rung is listed "unfunded by the ladder — fund it from Reserve". |
| 8 Leftover | Leftover = the sleeve amount (default: the Long-term target) − Σ par. It is spread across the fillers equally to the cent, odd cents to the earliest. With no filler it goes to the last rung. A negative leftover is reported as a shortfall and never cut from a rung. |
| 9 Yield | A matched issue's own auction yield when that auction is at most 35 days old. Otherwise the FRED Treasury curve, interpolated linearly in months at the rung's remaining term (flat beyond the curve's ends, said so). No curve → "not estimated — needs the Treasury curve". |

- **Business days** (`us_business_days`) are the U.S. federal holidays the Federal Reserve and the Treasury observe:
  - New Year's Day, MLK Day, Washington's Birthday, Memorial Day, Juneteenth (from 2021), Independence Day, Labor
    Day, Columbus Day, Veterans Day, Thanksgiving, Christmas.
  - A Saturday holiday is observed on the Friday, a Sunday holiday on the Monday. New Year's Day on a Saturday is not
    observed.
  - Weekends are never business days.
- Money is Decimal; yields are Decimal percent. Both are fixed-point text in JSON.

## After-tax method

**Candidates per sleeve**, each with its NET yield, basis and as-of:

| Sleeve | Candidates |
|---|---|
| Near-term | Treasury / Government / muni money-market funds (SEC N-MFP 7-day net yield). Per category, the class with the highest fresh yield. |
| Reserve | A T-bill (the issue maturing latest on or before the Reserve window's midpoint, else indicative) and the same funds. The ultra-short bond fund reads "30-day SEC yield: needs a source". |
| Long-term | The Treasury ladder (par-weighted rung yields). The muni ladder reads "muni yield: needs a source". |

**Treatment** comes from `accounts.tax_status`; rates from `tax_profiles.profile_for_account` (the APPROVED profile
of the account's household, else its primary entity).

| Instrument | Federal | State / local |
|---|---|---|
| Treasury MMF, bill, Treasury ladder | taxed | exempt |
| Government MMF | taxed | taxed — the exemption depends on the fund's US-obligation share, not applied |
| Muni MMF | exempt | taxed — the owner's domicile and the fund's state are not recorded |
| Muni ladder / ultra-short fund | — | no yield source: never chosen |

- Federal = federal ordinary + NIIT when it applies; state = `state_rate`; city = `city_rate`.
- After-tax = net × (1 − the rates that apply).

| Account | Chosen on |
|---|---|
| taxable + an approved profile | the highest AFTER-TAX yield |
| taxable, no profile | NET yield. After-tax reads "after-tax: needs a tax profile", and the why says so |
| tax-deferred / tax-exempt (IRA, 401(k), Roth, 529, DAF, foundation) | NET yield: tax does not apply now, whatever the exemption |

- **Ties.** The lower applicable rate first, then the kind order (Treasury MMF, bill, Treasury ladder, Government
  MMF, muni MMF), then the security.
- Each row shows the chosen instrument and WHY (one sentence), the runner-up, and the gap in basis points on the score
  used.
- **CD hurdle (the roadmap fact).** A fully taxable CD must yield `y × (1 − fed) ÷ (1 − fed − state − city)` to match
  a Treasury yielding `y` after tax. At federal 37 % + NIIT 3.8 %, NY 10.9 %, NYC 3.876 % and y = 4.00 %, that is
  5.330 %: a hurdle of **133 bp**. It is shown on a taxable account's Treasury choice.
- **Allocation.** A sleeve's $ (cash02's target) is split across the scope's open accounts by each account's latest
  cash balance (`account_balances_daily.cash_value`: the latest date, then the first source system — never a SUM).
  - One account → all of it.
  - Several accounts with no balance → "not allocated — needs each account's cash balance", unless every account's
    chosen yield is the same (the split cannot change the blend).
- **Blended** per sleeve and household = Σ value × yield ÷ Σ value, net and after-tax. A sleeve with an instrument
  blend REPLACES cash02's Treasury-curve proxy; a sleeve without one keeps cash02's estimate and label.

## Yield bases

| Basis | Source | As-of |
|---|---|---|
| 7-day net yield (SEC Form N-MFP) | `index_levels` `nmfp:<class>:7d` (provider sec_nmfp) | the yield's own date (≤ 45 days old to be used) |
| auction high investment rate (bond-equivalent) — bills | `index_levels` `tfd:<cusip>:yield` (treasury_fiscal) | the auction date (≤ 35 days old for a rung's own yield) |
| auction high yield — notes | the same | the same |
| FRED Treasury curve, interpolated at *n* months | `index_levels` DGS1MO … DGS10 (provider fred, marketdata1's nightly) | the oldest point used (≤ 10 days old) |

**Licence (THE rule, `security_data.registry.point_allowed`).**

- Staff read with purpose `internal`; a member with `member_display`.
- `fred` is `lic_member_display = not_allowed`, so a member never sees a curve-interpolated yield: the rung says
  "its licence does not allow showing values to members".
- Treasury Fiscal Data and N-MFP are public records (`allowed`).
- A member never sees a CUSIP.
- **Labels** from the disclosure library: `cash.yield_estimate` (cash02) and `cash.tax_estimate` (NEW). Its default
  text: "After-tax figures use your tax profile and are estimates; have counsel review."

## Providers and cadence

| Provider (`portfolio.data_providers`) | Kind | What it writes | Cadence | Stale after | Shipped |
|---|---|---|---|---|---|
| `treasury_fiscal` — Treasury Fiscal Data `auctions_query` | public_api, public domain | issues → security master (`fixed_income`, CUSIP + ISIN, `treasury_type`, `security_term`, `issue_date`, `maturity_date`) via `Matcher`; yields → `tfd:<cusip>:yield` | daily (the security-data nightly's `cash_sources` step) | 10 days | **not_connected** |
| `sec_nmfp` — SEC Form N-MFP2 / N-MFP3 via the EDGAR index + `edgar_fetch` | regulator_filing, SEC public | fund classes → security master (`fund`, `sec_series_id` / `sec_class_id`, `fund_structure`, `mmf_category`) via `Matcher` (created by class id); 7-day yields, WAM / WAL → `index_levels` | monthly, from the 8th | 45 days | **not_connected** |

- **Off means off.** Both are in the platform rank for `index_level` (4, 5) and `reference` (7, 8).
  - While `not_connected`, the read path skips them ("not connected") and `CallBudget` refuses a fetch.
  - The nightly step leaves them alone.
- **Switching on.** A Super Admin does it in Admin → Security data → Providers (`security_data.admin.update_provider`;
  both modules report `connected`).
- **Staleness** uses THE rule (`settings.is_stale`, strictly more than the limit) with these per-provider limits in
  the platform-quality step (`nightly.stale_limit`).
- **Refusals** (with a reason, never half-read):
  - Treasury: a non-JSON payload, a partial page, no `data` list (whole page refused); a bad CUSIP / type / date /
    yield (that row skipped).
  - N-MFP: a DOCTYPE / ENTITY, no series / class id, an unknown category, a class without a 7-day yield, a yield
    that is not a fraction (whole filing refused).

## Permissions and audit

- The plan rides in the treasury envelope: cash01's read gate `view_portfolio | manage_portfolio`, the one visibility
  rule, VISIBILITY1.
- `POST /api/v1/cash/households/{id}/plan/regenerate` needs `require_internal_permission(manage_cash_policy)`
  (cash02's authoring permission). It recomputes and AUDITS `cash.plan.regenerated` with the policy versions, rung
  counts and the plan's fingerprint.
- The screen's only control (Regenerate) shows for `can_author_policy` AND the server's `actions`, never in a member
  view, and fails closed.

## Operator steps

```bash
python3 apps/api/scripts/_apply_cash03.py --all                                         # done by the sprint
# switch the providers on: Admin → Security data → Providers → treasury_fiscal / sec_nmfp → Active (Super Admin)
doppler run -- python3 apps/api/scripts/load_cash_market_data.py --dry-run --fixtures   # recorded payloads only
doppler run -- python3 apps/api/scripts/load_cash_market_data.py --dry-run              # real fetch, prints, writes nothing
doppler run -- python3 apps/api/scripts/load_cash_market_data.py --real                 # writes (refused while switched off)
doppler run -- python3 apps/api/scripts/load_olivo_ladder.py --dry-run                  # the demo family's ladder and plan
```
