# CASH05 — discovery notes: what a family sees day to day (overview, holdings, activity, earnings, documents)

Checked against the repo at `ee91269` (merge cash04) and the database reachable to the sprint (read-only catalog and
count queries, 2026-11-09). Standing rule (Joe, 2026-11-08): **use the app's existing constructs; a new one only where
none can carry it**, named and justified below. This sprint is READS over existing data: **no new table, no new
column, no migration.**

Source documents: `CASH_TREASURY_DESIGN_v1.md` §3.1, §3.3, §3.4, §3.8, §5, §9.2; `CASH00_DISCOVERY.md`,
`CASH_OBLIGATIONS.md`, `CASH02_NOTES.md`, `CASH_POLICY.md`, `CASH03_NOTES.md`, `CASH_LADDER.md`, `CASH04_NOTES.md`,
`CASH_PROPOSALS.md`. **[FIND]** `CASH_TRACK_PLAN_v1.2.md` is still not in the repo (cash01–cash04 found the same);
nothing is invented from it.

---

## 1. Earlier cash decisions that bind this sprint

| Decision (sprint) | What cash05 does with it |
|---|---|
| Liquid balance = the net-worth core's Cash & Equivalents buckets (`taxonomy_mc_8_1`, `taxonomy_sub_8_1_<n>`, matched exactly — `cash_coverage.is_cash_key`); household at look-through shares, entity at its own (cash01) | The balance series IS the core evaluated as of each day (§2). "+ the sleeves": a position whose asset is a sleeve instrument (cash04's classes) also counts, once. |
| Coverage, months, covered-through, next gap, per-entity flags: `cash_coverage.household_report` (cash01) | Passed through to the overview unchanged (the household's card, or the filtered entity's row) — never recomputed. |
| Sleeve windows: `cash_policy.windows(N, R, H, today)` of the ACTIVE policy (cash02); a held Treasury's sleeve by maturity: `cash_proposals.classify` (cash04) | Holdings classify through exactly those two functions. No active policy → the default 3 / 12 / 24 boundaries, said so on screen. |
| Yields on their own basis; licence per caller (`member_display` for a member); `read_mmfs`, `read_auctions` (cash03) | Holdings and the blended yield read through cash03's own reads with the caller's purpose. |
| The ladder is DERIVED, never stored (cash03 D3) | Holdings show held rungs + the planned rungs from `cash_instruments.scope_plans` (the obligation each serves). No rung table. |
| Read gate `view_portfolio | manage_portfolio` + `authz.member_entity_scope` + VISIBILITY1 path guard; a member reads their own family read-only with library labels (cash01) | Every cash05 route: the same gate, the same scope, `editable: []`. Another family's household (or an entity the household does not reach) is a 404. |
| Money Decimal; fixed-point text at the JSON boundary | Same. Yields are Decimal percent text. |
| Read-only on money; the Custody Cliff | Nothing in cash05 writes a row. No transfer, no instruction, no proposal. |

## 2. Where a liquid-balance HISTORY comes from — **derived; no daily series exists**

- **[FIND]** There is no stored daily balance series for a household. `account_balances_daily` exists (per account,
  custodian-fed) but holds **0 rows** in the database the sprint can read (cash03 found the same for the Olivo demo).
  It is also account-level cash only — not the household's look-through liquid figure.
- `portfolio.positions` IS a history: each row is a statement **as of** its `as_of_date`, current on both temporal axes.
  `portfolio_rollup._current_positions(conn, org, as_of, owners)` picks, per (owner, asset, account), the latest
  statement on or before `as_of` (precedence winners only, disposals honoured). `networth.load(as_of=D)` values those
  lines (PRICES1 price → entered value → valuation ladder). So **the core as of D is the liquid balance on D**.
- **Decision D1.** The series is DERIVED by evaluating THE core as of each sampled day, narrowed to the liquid rows.
  A small additive change makes that possible without a second formula: `networth.value_lines(..., keep=None)` /
  `networth.load(..., keep=None, edges=None)` — `keep` filters WHICH rows are valued (never how); `edges` lets a series
  read the ownership edges once. Every existing caller passes neither, so nothing else changes.
- Sampling: every day for ranges up to 120 days; beyond that every k-th day — ALWAYS plus the range's first day, today,
  each transfer-marker date and each restatement date of a liquid position (so no step is skipped). Drawn as a step.

## 3. Transaction types and the six ledger types

Live vocabulary (`public.transaction_types`, 16 platform codes, all active): `buy`, `sell` (category transfer);
`call_investment`, `call_mgmt_fee`, `call_org_cost`, `call_partnership_expense` (call); `dist_gain`, `dist_income`,
`dist_recallable`, `dist_roc`, `dist_stock`, `dividend`, `interest` (distribution); `fee_expense` (fee); `adjustment`
(other); `valuation` (valuation).

**[FIND]** There is no deposit / withdrawal, no maturity and no credit code. **Decision D2** (`cash_activity.TYPE_MAP`,
every code maps to exactly one type; the sign is the effect on the HOLDING the row is booked on):

| Ledger type | Codes | Sign |
|---|---|---|
| transfer | `buy` (+), `sell` (−), `call_investment` / `call_org_cost` / `call_partnership_expense` (+), `dist_roc` / `dist_recallable` / `dist_stock` (−) | as listed |
| maturity | a `sell` of a Treasury (bill / note, cash04's class) on or after its `maturity_date` | − |
| dividend | `dividend`, `dist_income`, `dist_gain` | + |
| interest | `interest` | + |
| fee | `fee_expense`, `call_mgmt_fee` | − |
| credit | `adjustment` (and any future code, never dropped) | the stored sign |
| — excluded | `valuation` — a mark moves no money; listed as excluded, never an entry | |

Amount = `COALESCE(net_amount, gross_amount, quantity × price)` (stored positive by every writer). Only rows current
on both axes are read (a correction supersedes; `portfolio_transactions` is append-only).

## 4. Pending vs settled

`portfolio.transactions.settle_date` (nullable). **[FIND]** No live transaction carries one (0 of 5 in the readable
database). **Decision D3:** pending = `settle_date > today`; no settle date = settled on the trade date (said so on the
entry). Settlement guidance per instrument (`cash_activity.GUIDANCE`): a money-market fund T+0 before the fund's
cut-off; a Treasury T+1; cash when the custodian posts it; anything else the custodian's standard cycle.

## 5. Coupon / discount data for ladder interest

- **[FIND]** A note's coupon is not recorded anywhere (cash04 found the same; `security_reference_attributes` has no
  coupon field — its field CHECK lists 19 fields, none a rate). So a note's YTM at today's price is NOT derived; the
  holding shows its auction high yield (Treasury Fiscal Data) when read, else "not estimated — needs …".
- A bill's discount: par = the position's quantity (cash04's stated convention), price = market value ÷ par, cost =
  `positions.cost_basis` (nullable).
- **Decision D4 — earnings are CASH BASIS**, so they reconcile to the activity ledger to the cent: ladder interest =
  coupons received (`interest` entries on Treasuries) + discount REALISED at maturity (the maturity entry's proceeds −
  the matured par's cost basis, pro rata). A bill still held accretes straight-line ((par − cost) × days ÷ life) — shown
  beside the total ("accreting, not yet realised"), never in it. A maturity without a cost basis is listed "not
  estimated — needs the matured position's cost basis".

## 6. The fee engine's read — "accrued fee attributable to cash"

- Which schedule governs an account: `fee_schedules.resolve_assignment_for_account(conn, org, account, as_of)`
  (fee34's precedence — never re-implemented).
- Whether cash is charged: `fee_schedules.cash_treatment` ∈ `INCLUDE` / `EXCLUDE` / `EXCLUDE_ABOVE_PCT` (+
  `cash_exclusion_pct`) — the exact switch `fee_calc.apply_exclusions` applies.
- What the engine billed: `fee_run_lines` (`net_fee`, `billable_value`) of runs in `POSTED` / `EXPORTED` /
  `RECONCILED`. The engine's own cash input is `account_balances_daily.cash_value` at the period end.
- **Decision D5.** Fee on cash for a period = Σ over those lines overlapping it of `net_fee × billed cash ÷ billable ×
  overlap days ÷ period days`, billed cash after the schedule's treatment (EXCLUDE → 0; EXCLUDE_ABOVE_PCT → min(cash,
  total × pct); INCLUDE → cash). Every schedule EXCLUDES → "fee on cash: not charged" (0.00). No schedule assigned →
  "not estimated". The blended yield's fee rate = the latest posted line per account, net fee ÷ billable × runs a year
  (billable-weighted). Nothing is re-priced; `fee_calc` is not called.
- **[FIND]** In the readable database: 0 fee runs, 0 assignments — so for real families today the line reads
  "not estimated — no fee schedule is assigned". The verify proves both the charged and the not-charged paths on
  fixtures.

## 7. Document types per account

- Chancery stores documents in `public.documents` (no category column; `doc_family` is tabular / narrative / other) and
  links them through `document_record_links (document_id, record_type text, record_id uuid)` — polymorphic, NO CHECK
  (Portfolio Phase D).
- **[FIND]** The `doc_category` reference list has 12 codes (LLC formation, trust instrument, will, estate plan,
  operating agreement, subscription doc, tax return, K-1, financial statement, ID, accreditation, other) — **no
  statement, confirmation or 1099**. `held_away` already links a filed custodian statement to an account with record
  type `held_away_account`.
- **Decision D6.** A document's role for an account is its LINK's record type (`cash_documents.RECORD_TYPES`):
  `account_statement` / `held_away_account` → statement; `account_confirmation` / `portfolio_transaction` (Phase D, a
  document on a ledger transaction) → confirmation; `account_tax_form` → 1099. The three new values are a Python-side
  vocabulary only (the column is polymorphic); links are written through the existing `POST
  /documents/{id}/record-links`. cash05 writes none. Readable database: 0 documents, 0 links.
- Open / download: the existing `GET /documents/{id}/download` (presigned R2; visibility via
  `document_embedding.visible_entity_ids` + `document_visible`) — cash05 lists only what that route will open.
- Dates: `documents.created_at` (timestamptz) shown in America/New_York (`zoneinfo`) — a UTC-midnight filing shows the
  prior day ET.

## 8. Prospectus links in the security master

**[FIND]** None. `security_reference_attributes_field_chk` allows exchange, cik, sector, industry, industry_group,
country, is_etf, fund_category, listing_status, first_seen, last_seen, absent_runs, fund_category_group,
fund_structure, maturity_date, issue_date, security_term, treasury_type, mmf_category — no URL. Holdings show
"prospectus: not recorded in the security master" (the field `prospectus.href` is published as `null`, ready for a
source). No URL is guessed.

**[FIND] Fund cut-off times are not recorded either.** **Decision D7:** the cut-off is read from the org setting
`cash.mmf_cutoff_et` ("HH:MM", Eastern; `org_settings`, no new table). Absent → "Same day if instructed by the fund's
cut-off — the cut-off time is not recorded (needs the org setting cash.mmf_cutoff_et)".

## 9. Reuse inventory

| Piece | Construct used | New? |
|---|---|---|
| Liquid balance on a day | `networth.load(as_of=…)` → `figures`, narrowed by `keep` | `keep` / `edges` parameters (additive) |
| What is liquid | `cash_coverage.is_cash_key` + cash04's classes (`mmf_category`, `treasury_type` + `maturity_date`) + `kind = cash` assets | `cash_holdings.is_liquid` (one rule, five screens) |
| Coverage card + per-entity table | `cash_coverage.household_report` | — |
| Sleeves | `cash_policy.windows` + `cash_proposals.classify` | — |
| Planned rungs + obligations served | `cash_instruments.scope_plans` | — |
| Fund / note yields | `cash_instruments.read_mmfs`, `cash_ladder.read_auctions` | — |
| After-tax | `tax_profiles.approved_profile` + `cash_instruments.rates / applicable_rate / treatment` | — |
| Ledger | `portfolio.transactions` × `transaction_types` × positions × assets × accounts | `cash_activity.TYPE_MAP` |
| Drawer links | `GET /portfolio/transactions/{id}`, `/portfolio/positions/{id}`, `/execution/instructions/{id}` (an instruction reconciled to the transaction: `execution_instruction_events` action `reconcile`, `detail.transaction_ids`), `/proposals/{id}` (the instruction's `source_ref`), `/documents/{id}/download` | — |
| Fee on cash | `fee_schedules.resolve_assignment_for_account`, `fee_schedules.cash_treatment`, `fee_runs` / `fee_run_lines`, `account_balances_daily` | — |
| Documents | `documents` + `document_record_links` + `document_embedding` visibility + the download route | 3 record-type values (Python vocabulary) |
| Cut-off wording | `org_settings` | key `cash.mmf_cutoff_et` |
| Labels | disclosure library: `cash.coverage_estimate`, `cash.yield_estimate`, `cash.tax_estimate` | key `cash.sweep_rate` RESERVED (cash07; registered with a fallback, never resolved or shown by cash05) |
| Visibility | `authz.member_entity_scope` + VISIBILITY1 path guard + `require_any_permission(view_portfolio | manage_portfolio)` | — |
| Screens | the EXISTING Household → Treasury tab (`TreasuryPanel`) and `/treasury` for a member | `TreasuryDaily` + 4 panels inside it; no second tab |
| Services | — | `cash_overview`, `cash_holdings`, `cash_activity`, `cash_earnings`, `cash_documents` (reads only, no table) |

## 10. Decisions

- **D1 — series derived from the core** (§2). No balance table.
- **D2 — ledger types by code** (§3); `valuation` excluded and listed.
- **D3 — pending** by `settle_date` (§4).
- **D4 — earnings on the cash basis**, reconciling to the ledger; accrual shown beside (§5).
- **D5 — fee on cash from the engine's own posted lines** (§6); "not charged" when every governing schedule excludes
  cash.
- **D6 — document role = link record type** (§7).
- **D7 — cut-off from an org setting** (§8).
- **D8 — amounts.** The overview's balance weighs each entity at its household share (cash01's household figure); the
  ledger and earnings list the records' own amounts (100 %) with their owner — what happened, not a share of it. An
  entity filter uses that entity's own positions (share 1) everywhere.
- **D9 — one read per screen.** `GET /cash/households/{id}/overview | holdings | activity | activity/{entry} |
  earnings | documents` — each gated exactly as the treasury envelope; `entity_id` (query) filters; a member reads
  read-only with the library labels and purpose `member_display`; CUSIPs and staff links (instruction, proposal) are
  never sent to a member.
- **D10 — no write.** No route here writes; the verify counts every touched table by fixture ids and fingerprints real
  rows (the Olivo demo's included).
