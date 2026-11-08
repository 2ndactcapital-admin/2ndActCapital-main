# REP14 — Task 1 discovery (2026-11-08)

Read from the code at `be844bf` (rep14-work's base, disclosures1 merged) and the live database (read-only
queries; the service's own `candidates()` run read-only against the default org). Nothing here was assumed from the
sprint prompt. Counts are as of 2026-11-08 and are printed, never frozen into a check.

## 1a. Lots — where lot-level basis and acquisition dates live

- **Where.** `portfolio.tax_lots` (one row per lot: `position_id`, `acquired_date`, `quantity`, `original_cost`,
  `adjusted_cost`, `term`, `status` `open|closed`, `realized_gain_loss`, `source_system`, `external_ref`; both
  temporal axes). A lot belongs to an account through its position (`positions.account_id`); the live join is
  `services.execution._LOT_SQL` / `account_lots` (position `system_to IS NULL` — **not** `valid_to`, so a lot keeps
  its account when its position is restated). `account_models.load_holdings` turns them into
  `rebalance_engine.Lot(id, quantity, unit_cost, acquired, term_hint)`; a holding with **no** lot rows gets one
  synthetic lot with `id None` (average cost in effect).
- **Average cost.** Declared per account on `account_lot_method_settings.custodian_cost_basis_method` /
  `average_cost_security_ids` (lotmethod) and surfaced as `LotMethod.average_cost`. rep14 treats a holding as
  "average cost only" when it is declared so OR has no lot rows.
- **Live counts (non-test orgs; there are no accounts in test orgs right now).** 34 open accounts; 32 hold
  positions; 24 have open lots; **8 hold positions with no lots at all**; 0 declare average cost through lotmethod.
  The service's screen lists **9 accounts "average cost only"** (an account counts when ANY of its priced holdings
  has no lot rows, so it can exceed the 8 with no lots at all). **[FIND] All 55 live open lots have `acquired_date` NULL** (none lacks a cost). A §351 basis record
  needs each lot's date (the tacked holding period), so rep14 adds the ineligibility reason `lot_dates` (it flags 7
  live accounts): today **no live account has 351-ready lot data**. The screen's reasons over the 34 accounts:
  not a replica account 34, held-away 26, authority not managed 26, no holdings 19, average cost only 9, lot dates
  missing 7 — **0 eligible**.
- **Writers.** Only the held-away import (rep7), the options sleeve (rep8) and now rep14's confirmation write
  `portfolio.tax_lots`; rep2 fills never do. There is **no `transfer` transaction type** (`transaction_types`:
  adjustment, buy, call_*, dist_*, dividend, fee_expense, interest, sell, valuation) — rep14 records the transfer on
  its own instruction row and does not invent a type.
- **How a lot closes with no gain.** `loss_bank.ingest` and `rebalance_proposals.realized_ytd` read only closed lots
  with a **non-NULL** `realized_gain_loss` (realized YTD also needs a closing transaction). rep14 closes a
  transferred lot with `realized_gain_loss` NULL, so a §351 close can never be counted as a sale; the instruction
  row states realised gain 0.

## 1b. "Harvesting has run dry" — what rep6 already measures

- **Harvestable loss.** `services.harvest.scan_account` evaluates every open loss lot against the org's % / $
  thresholds (`replication.tax.harvest_loss_pct`, `_min`, trading cost) and records `harvest_runs` /
  `harvest_candidates`. Live: **0 harvest runs**. rep14 measures the *remaining* harvestable loss directly from the
  lots (Σ |(price − unit cost) × qty| over loss lots at the account's own valuation), so the signal does not depend
  on a scan having run.
- **Benefit decay.** **[FIND] rep6 states decay only as a note** (`loss_bank.DECAY_NOTE`: a harvested loss is mostly
  a deferral); nothing computes it as a number. rep14 shows it next to year 1 as an estimate: losses harvested in
  the model link's first tax year vs this tax year (`loss_bank.harvested`, approved harvest proposals),
  decay = 1 − this year ÷ year 1; "not estimated" when year 1 harvested nothing. Live: 0 loss-bank entries.
- **Years since inception.** No field holds it; rep14 reads the earliest `account_model_links.valid_from` (status
  active / ended) of the account.
- **Unrealised gain / appreciated value.** Not stored; computed from lots × the account's valuation (the same
  `load_holdings` prices every proposal uses — position value, or the fresher internal EOD close).
- **The rule (rep14, thresholds per org, versioned in `public.section351_settings`).** Run dry when ALL three hold:
  harvestable loss ≤ 2 % of the account, unrealised gain ≥ 25 % of the account, link ≥ 3 years old (defaults;
  unknown inception → not met).

## 1c. Issuer identity, government securities, cash, ETF constituents

- **Issuer.** The master links a security to its issuer only through the SEC CIK reference attribute
  (`security_reference_attributes.field = 'cik'`, provider `sec_tickers`): 10,251 rows, 7,866 distinct CIKs, **1,418
  CIKs carry more than one security** (share classes — GOOG / GOOGL, FOX / FOXA, per rep13). `canonical_id` is the
  security itself for every equity (rep13), and `portfolio.assets.issuer_entity_id` is set on **0** rows. No LEI is
  stored. rep14's issuer key = `cik:<n>` (share classes grouped), else the security's own id.
  **[FIND] Controlled groups (§1563(a): parent + subsidiaries with different CIKs) cannot be grouped** — an open
  item for counsel.
- **Government securities.** **[FIND] The master has no issuer-kind field**: 187 `fixed_income` securities, none
  marked government. rep14 adds `public.section351_asset_classes` (platform rows by Super Admin, org rows by the
  adviser, each with a source and as-of) — class `government` is in the total but never an issuer. The same table
  carries the classes the master does not know: restricted, illiquid, physical commodity, commodity pool, grantor
  trust, partnership interest (and derivative / crypto overrides).
- **Derivatives / crypto.** An option is `securities_global.security_type = 'option'` + a platform
  `portfolio.option_contracts` row (rep8; 0 live). Crypto is `portfolio.assets.asset_type = 'digital_asset'`.
  Shorts (rep13) are derived from fills and are never lots, so they are never contributed.
- **Cash.** `load_holdings` returns cash (the latest `account_balances_daily.cash_value` + cash-type positions)
  outside the holdings. The sponsor's default cash rule keeps it in the account.
- **ETF constituents (look-through).** rep1's `portfolio.replica_holdings_snapshots` (platform, `as_of`, status
  `live` / `needs_review`) + `replica_holdings_rows` (`asset_type` equity / cash / future / other,
  `resolution_state`, `constituent_global_security_id`, `weight`); `replica_holdings.latest_snapshot` picks the live
  snapshot with the latest as-of. **[FIND] 0 snapshots are stored** (rep1's fetchers were blocked — Invesco 406 /
  iShares HTML), while 6,133 funds and 10,996 `is_etf` flags exist: every fund held today would FAIL CLOSED with
  "holdings needed". N-PORT parsing exists in rep1 but has produced no stored snapshot.

## 1d. Proposals — building toward a constraint

- `rebalance_proposals.generate(trigger='manual', maker_kind='user', target_override=…, lot_override=…)` is THE
  generator: a person's proposal with an explicit target (rep7b / rep10 use the same hook) and a per-proposal lot
  method. With `lot_method 'specific'` and picked lots (`lot_method.clean_override`), `rebalance_engine` sells exactly
  those lots (`validate_picks`), and the engine still applies the gain budget, the cash floor, the short-term guard
  and household wash-sale control. The proposal then goes through rep5's own submit / decide (maker-checker:
  `RP.decide` refuses the maker; the task's CHECK backs it).
- The engine floors every sale and buy to whole shares from target weights, so rep14 sizes each target half a share
  inside its floor (deterministic), then **compares the stored trades with its plan** and runs the test on the
  proposal's actual trades — a mismatch (gain budget, cash floor) is stated, never hidden.
- `generate_one_off` is the ADVISE path (rep7b) and is refused for a managed account — not usable here.
- Tax per dollar raised: `rebalance_engine.tax_per_dollar` at `tax_profiles.combined_rates` (never a guessed
  bracket; no profile → "not estimated" and the plan orders by gain per dollar).

## 1e. Human tasks / workflow engine; document storage

- **Human tasks** (`services.human_tasks`, rep2): a governed task is a run of the per-org system definition
  `system:human_task:<permission>`; `register_kind(key, outcomes, maker_checker, on_complete)`,
  `register_precheck`, `open_task` (one open task per kind + subject, todos to the permission's holders),
  `complete_task` (maker-checker refusal, permission check, prechecks, then `on_complete`). rep14 registers
  `section351_plan_approval` (approve / reject, maker-checker, precheck = every ready gate again) and
  `section351_project_step` (done) — the tracker's eight steps are eight tasks.
- **Documents.** `public.documents` is the vault (Chancery); a consent is recorded by `document_id` (the member's
  signed consent uploaded through Chancery, any org document). Generated files: `services.storage.upload_bytes`
  (R2; called as a module attribute so a verify stub replaces it) + a `documents` row (`source 'generated'`,
  `doc_family 'section351_basis_record'`); reads are proxied and hash-checked. The CSV + PDF basis record is a new
  version per export (`public.section351_basis_records`).
- **PDF.** reportlab via `services.deal_room_pdf` (as rep9 / rep10's letters): the basis record PDF and the
  on-demand in-kind instruction sheet.
- **Custodian capability.** `in_kind_transfers` is already a rep2 capability key; live rows: `altruist = unknown`,
  `manual = unknown`. No adapter declares it, so the adapter route refuses at submit and the manual route is
  offered.

## 1f. The disclosure library (disclosures1)

- Call `services.disclosures.resolve(conn, 'section351.exit', org_id=…, account_id=…)` (one query: platform → org →
  household → account, published, in force on the date). `acknowledge(conn, …, resolved=…, subject=Subject(…))`
  records (user, version id + hash, subject). `require_current` is per user; rep14 checks "acknowledged at the
  version in force for THIS plan account" by any adviser (same table, same id + hash rule), with subject type
  `section351_plan` (already in `SUBJECT_TYPES`) and subject id = the plan-account row (acknowledgements are
  idempotent per user / version / subject, so the account must be the subject id).
- **`section351.exit` has NO fallback text** (it never existed in code): with no published version the resolver
  returns source `none`, and rep14 refuses "ready" — the disclosure is never text in code.
- **Live:** the key exists with **one platform DRAFT** (the default "have counsel review" text, not published), so
  today no plan could become ready until counsel reviews and a second Super Admin publishes it.
