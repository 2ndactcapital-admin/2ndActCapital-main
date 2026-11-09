# Treasury — the day-to-day screens (CASH05)

Household → Treasury (staff) and `/treasury` (a member, read-only) open with five screens, each ONE read. All are
read-only on money: nothing on them writes a row. Discovery and decisions: `CASH05_NOTES.md`.

| Screen | Route (GET, under `/api/v1`) | Service |
|---|---|---|
| Overview | `/cash/households/{id}/overview?range=&entity_id=` | `services/cash_overview.py` |
| Holdings | `/cash/households/{id}/holdings?entity_id=` | `services/cash_holdings.py` |
| Activity | `/cash/households/{id}/activity?type=&status=&start=&end=&entity_id=` | `services/cash_activity.py` |
| Activity drawer | `/cash/households/{id}/activity/{entry_id}?entity_id=` | `services/cash_activity.drawer` |
| Earnings | `/cash/households/{id}/earnings?period=&start=&end=&entity_id=` | `services/cash_earnings.py` |
| Documents | `/cash/households/{id}/documents?type=&entity_id=` | `services/cash_documents.py` |

**Gate (every route).** `view_portfolio | manage_portfolio`, then `authz.member_entity_scope` (a member: their own
family; a restricted staff caller: their families) and the VISIBILITY1 path guard on `household_id`. Another family's
household, or an entity the household does not reach, is a **404** (the same as a missing one). Each read publishes
`permissions` (`can_read: true`, `can_write: false`) and `vocabularies.editable = []`. The web renders nothing from a
read without that envelope (`lib/cashTreasury.mjs`, fails closed). Web proxies: `apps/web/app/api/cash/households/[id]/…`.

## What is liquid (one rule — `cash_holdings.is_liquid`)

A live position counts when its effective taxonomy key is Cash & Equivalents (`taxonomy_mc_8_1` /
`taxonomy_sub_8_1_<n>`, exact), or its asset is a money-market fund with an N-MFP `mmf_category`, or a Treasury Bill /
Note / Bond with a readable `maturity_date`, or a `kind = cash` asset. Each position once.

## Overview

- **Balance series.** Value on day D = `networth.load(as_of=D, keep=is_liquid)` → `figures(weights)`.`gross`; the
  household at its look-through shares (cash01's household figure), an entity filter at that entity's own (share 1).
- **Range** (both ends inclusive): 30d = today − 30 days; 90d = today − 90 days; 6m / 12m = cash01 `add_months(today,
  −6 / −12)` (clamped); YTD = 1 January; all = the scope's first liquid statement.
- **Points**: every day when the range spans < 120 days, else every ⌈days ÷ 119⌉-th day; always + the first day,
  today, each transfer date and each restatement date of a liquid position. Drawn as a step.
- **Markers**: the activity entries of type `transfer` in the range (date, change, direction, pending).
- **Tooltip**: value; pending in / out = Σ positive / negative changes of entries with `trade_date ≤ D < settle_date`.
- **Blended net yield** = Σ (holding value × its yield) ÷ Σ value of the holdings WITH a yield − the advisory fee's
  annual rate on cash. Holdings without a yield are listed ("not in the blend"), never guessed.
- **After-tax** (an APPROVED household tax profile for the year): each position × (1 − the rate that applies to its
  instrument — `cash_instruments.applicable_rate` — when its ACCOUNT's `tax_status` is taxable; 0 when tax-deferred /
  exempt), then − the fee rate. Labelled with the profile's year and as-of; else "after-tax: needs a tax profile".
- **Coverage card + per-entity table**: `cash_coverage.household_report` passed through (the filtered entity's row when
  filtered).
- **Actions** (links into existing screens, server-decided): Documents (everyone); Edit cash policy (holders of
  `manage_cash_policy`); Proposed trades (holders of `view_proposals`). Never shown to a member except Documents.
- Labels: `cash.coverage_estimate`, `cash.yield_estimate`, `cash.tax_estimate`.

## Holdings

- **Groups**: Cash · Near-term · Reserve · Long-term (· Other cash & equivalents). A money-market fund → Near-term; a
  Treasury by maturity through `cash_proposals.classify` on `cash_policy.windows` of the ACTIVE policy (the entity's own
  for an entity filter, else the household's; none → 3 / 12 / 24 months, said so); matured → Near-term "matured —
  awaiting proceeds".
- **Group value** = Σ holding values = exactly the overview's balance for today (the same core read).
- **Yield, basis, as-of**: fund — 7-day net yield (SEC Form N-MFP, `read_mmfs`); bill — YTM from the account's own
  valuation, simple bond-equivalent `(100 − P) ÷ P × 365 ÷ days` (P per 100 of par = market value ÷ par); note —
  auction high yield (`read_auctions`; the coupon is not recorded); cash — "not estimated — needs the custodian's cash
  rate".
- **Liquidity**: fund "Same day if instructed by HH:MM ET" (org setting `cash.mmf_cutoff_et`, else "not recorded");
  Treasury "At maturity (date); an early sale settles the next business day and may lose principal"; cash "Available
  now".
- **Months of obligations covered** = value ÷ the scope's average monthly gross outflow (cash01, next 12 months).
- **Prospectus**: not recorded in the security master (`href: null`, said so).
- **Rungs** (Long-term, expandable): every held Treasury (maturity, type, par, market value, par vs market, YTM + basis,
  the obligation it serves — the planned rung matched by security, else by maturity) + every planned rung not held
  (`cash_instruments.scope_plans`, marked "planned — not held"). Member: no CUSIP.

## Activity

- **Source**: `portfolio.transactions` current on both axes, of every position of an entity in scope, whose asset /
  key is liquid. Newest first.
- **Type mapping** (`cash_activity.TYPE_MAP`; change = the effect on the holding the row is booked on):

| Type | Codes |
|---|---|
| transfer | buy (+), sell (−), call_investment / call_org_cost / call_partnership_expense (+), dist_roc / dist_recallable / dist_stock (−) |
| maturity | a sell of a Treasury on or after its maturity date (−) |
| dividend | dividend, dist_income, dist_gain (+) |
| interest | interest (+) |
| fee | fee_expense, call_mgmt_fee (−) |
| credit | adjustment (stored sign); any unknown code |
| excluded | valuation (moves no money) |

- **Pending**: `settle_date > today`, with guidance (fund T+0 before the cut-off; Treasury T+1; cash when posted). No
  settle date: settled on the trade date (said so).
- **Filters**: type (any of the six), status (pending / settled), trade-date window.
- **Empty state**: "No activity for these cash holdings yet." / "No activity matches these filters."
- **Drawer**: source record (table, id, code), account (masked, last four only; custodian, registration, tax status),
  security (name, kind, ticker, maturity; CUSIP staff-only), settlement (trade / settle date, text). Links — each with
  the API path that opens it AND the screen, offered only when the caller's own permissions open it: transaction
  (`view_portfolio`), position (`view_portfolio`), instruction (`view_execution`; an instruction whose `reconcile` event
  names the transaction), proposal (`view_proposals`; that instruction's `source_ref`), documents linked to the
  transaction with a stored file (the download route's own visibility rule). A member gets no instruction / proposal
  link.

## Earnings (cash basis)

`total = dividends + ladder interest + cash interest − advisory fee on cash`

- **Dividends per fund** = Σ ledger dividend / interest entries on fund holdings.
- **Ladder interest** = coupons (interest entries on Treasuries) + discount realised at maturity (each maturity entry's
  proceeds − the matured par's `cost_basis`, pro rata to quantity). Bills still held: straight-line accretion shown
  beside, NOT in the total.
- **Cash interest** = dividend / interest entries on cash and other Cash & Equivalents lines.
- **Reconciliation**: dividends + ladder interest + cash interest == Σ (ledger dividend + interest entries) + realised
  discount, to the cent (`reconciles: true`). The ledger's own fee debits on cash holdings are shown beside the fee.
- **Advisory fee on cash** (the fee engine's records, never re-priced): Σ over POSTED / EXPORTED / RECONCILED
  `fee_run_lines` of the scope's accounts overlapping the period: `net_fee × billed cash ÷ billable value × overlap
  days ÷ period days`; billed cash = `account_balances_daily.cash_value` at the run's period end after
  `fee_schedules.cash_treatment` (EXCLUDE → 0; EXCLUDE_ABOVE_PCT → min(cash, total × pct); INCLUDE → cash). Every
  governing schedule (`resolve_assignment_for_account`) EXCLUDES cash → **"fee on cash: not charged"** (0.00); none
  assigned → "not estimated — no fee schedule is assigned".
- Periods: month to date, quarter to date, year to date, last 12 months, custom (start ≤ end ≤ today).

## Documents

- **Cash accounts** = the scope's open accounts holding a liquid position or a positive latest cash balance.
- **Listed**: documents linked to one of them by `account_statement` / `held_away_account` (statement),
  `account_confirmation` (confirmation), `account_tax_form` (1099), or to one of their ledger transactions by
  `portfolio_transaction` (confirmation). Only documents the download route would open for this caller.
- **Dates**: `documents.created_at` in America/New_York (date + "HH:MM ET").
- **Open / download**: the existing `GET /documents/{id}/download` (a presigned URL); a document with no stored file
  says so and offers no link.

## Member view

The same five reads, read-only: `member_view: true`, `editable: []`, purpose `member_display` (THE licence rule), no
CUSIP, no instruction / proposal link, labels from the disclosure library. `cash.sweep_rate` is RESERVED for cash07 and
is never shown.
