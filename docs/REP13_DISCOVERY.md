# REP13 — Task 1 discovery (2026-11-07)

Read from the code at `219f057` (rep13-work's base) and the live database
(`apps/api/scripts/_discover_rep13.py`, read only, operator DSN). Nothing here
was assumed from the sprint prompt.

## 1a. custodian_capabilities

- **Shape.** `public.custodian_capabilities` is a key–value platform table:
  one row per (custodian_code, capability_key). Each row has a value, an
  evidence kind (`document` / `url` / `assumed`), the evidence, an as-of date
  and a version_no. It is versioned on the valid axis (an edit closes the live
  row). Its constraints are `custodian_capabilities_key_chk` (the key list),
  `_value_chk` (`yes` / `no` / `unknown`; `options_level` 0–4) and the partial
  uniques `_live_uq` and `_version_uq`. Super Admin writes it
  (`services/custodian_capabilities.set_capability`, `PUT
  /custodians/capabilities/{code}/{key}`); every org reads it.
- **Margin and short selling were already declarable.** The rep2 keys include
  `margin` and `short_selling`. The prompt calls the second one `short_sell`;
  the existing key `short_selling` is kept. **Stock borrow was not
  declarable**: rep13 adds `stock_borrow`, plus `margin_initial_pct` and
  `margin_maintenance_pct`. These two hold the custodian's declared
  requirement as a fraction (`0.5000`) or `unknown`; `unknown` is shown as
  "custodian to confirm".
- **What is stored (live).** Only two custodian codes have rows: `altruist`
  (all 13 rep2 keys `unknown`) and `manual` (the trade APIs `no`, the rest
  `unknown`). For both, `margin` and `short_selling` are `unknown`. rep13
  seeded `stock_borrow` and the two margin-requirement keys as `unknown` for
  both.
- **Schwab, Fidelity, Pershing and IBKR have no rows at all.** Accounts do
  carry those codes: Schwab 9, Fidelity 6, Interactive Brokers 1, and others
  (13 distinct codes). `capability()` reads a missing row as `unknown`, so
  **no live custodian declares margin, short selling or stock borrow**, and no
  live account can go long/short.

## 1b. Models

- **Where weights are validated.** In `services/model_upload.py`:
  - `interpret_weights` reads the file's percent / fraction basis, applies the
    sum rule (100% ± `portfolio.models.weight_tolerance_pct`) and checks the
    bands.
  - `validate_positions` covers edits made in the UI.
  - `services/models.submit` re-checks the stored draft's effective total.
- **How a negative weight was refused.** A `weight_negative` error carrying
  the row, plus the database CHECK `model_version_positions_weight_chk`
  (0–1).
- **The "long/short" exception in rep3's text was a placeholder.** The
  message read "long/short models are not supported yet". No model had a kind
  or any gross figures.
- **rep3 reserved the "Holdings of" prefix for fund-replica models**
  (`models_reserved_name_chk`). Joe's rule is that every model is named
  "Holdings of …", so the CHECK now also admits a long/short model, and
  `models_long_short_name_chk` requires the prefix on one. A long-only upload
  named "Holdings of …" is still refused, so rep3's own check is unchanged.

## 1c. Proposals and the rebalance engine

- **How target weights become trades.**
  `services/rebalance_proposals.compute` → `rebalance_engine.build`:
  1. Exclusions and the cash floor.
  2. Sells: rep5's lot relief (`plan_sales`: the method, the gain budget, the
     short-term guard).
  3. Buys, scaled to the cash the sales raise.
  4. rep6 wash-sale control on the result.
- **A sale of shares not held.** The engine only ever asks for
  `min(q, holding.quantity)`, so a long-only proposal never sells shares it
  does not hold. A weight below zero had no meaning: the CHECK refused it at
  the model.
- **"The cost comparison's lines" exist only in rep4's viability report (Q5),
  not on a proposal.** Q5 has these lines: `fund_expense`, `strategy_fee`,
  `trading_cost`, `replica_total` and `tax_benefit` (the last says "estimated
  by the tax engine — not estimated here"). A rebalance proposal had no cost
  comparison at all. rep13 gives the long/short proposal its own: trading
  cost, "Borrow cost (long/short only) — estimated", "Short rebate", the
  overlay fee and the net. Q5 was not changed, because it is a long-only
  fund-versus-replica comparison.

## 1d. Tax engine (rep6) and lotmethod

- **How gains are classified.** `rebalance_engine.term_of(lot, on)` returns
  long when held more than a year (`long_term_from`), else short. A lot with
  no acquired date uses its `term` hint.
- **Where harvest candidates come from.** `services/harvest.scan_account`
  takes open LONG lots at a loss on the account's own valuation (the % and $
  thresholds, net of trading cost). It needs an approved, clear substitute
  (`wash_sale.load_context().substitutes`) or harvests to cash. It runs in
  `org_tick` before the drift scan.
- **What knows household-level holdings.**
  `rebalance_proposals.household_accounts` returns every account sharing the
  household, the primary entity, or an `account_owners` entity. This covers
  IRAs and held-away accounts. `account_models.load_holdings(account)` gives
  one account's priced holdings with their lots.
- **Held-away data is readable under every authority.** rep7b's read_only /
  advise / managed all allow READING an account's data; they differ only in
  trading. So rep13's §1259 check reads every account of the household, the
  held-away ones included.
- **Issuers.** The master's `canonical_id` is the security itself for all
  8,666 live equities, so it does not link share classes. The issuer is the
  `cik` reference attribute (`portfolio.security_reference_attributes`,
  provider `sec_tickers`, 10,251 rows). Checked on real data: GOOG / GOOGL
  share CIK 0001652044 and FOX / FOXA share 0001754301.

## 1e. Suitability profile

- **Nothing recorded a margin agreement or an options level.** No suitability
  table existed. Matching columns are only `account_balances_daily.margin_balance`,
  `entity_briefs.suitability_notes` (free text) and
  `fee_schedules.margin_treatment`.
- rep13 adds `public.suitability_records`, with record_kind
  `margin_agreement`: a date plus a document, maker-checker, system-axis
  archival. The record is keyed by the account's tax subject: its household,
  else its primary entity (rep6's `subject_of`).

## 1f. Execution (rep2 / rep9)

- **Side vocabulary was `buy` / `sell` only**, enforced by CHECKs on:
  - `rebalance_proposal_trades`
  - `execution_instructions`
  - `trade_blocks`
  - `TradeLine.side` (Literal)
  - `execution.SIDE_LABELS`
- **The manual order sheet** (`services/order_sheet.sheet_rows`, drawn by
  `blocks.instruction_sheet_dict` / the block sheet) prints
  `SIDE_LABELS[side]`. With the new labels it prints "SELL SHORT" / "BUY TO
  COVER", and a new "Position effect" row says whether the order opens or
  closes a short.
- **Fills** (`execution.enter_fill`, and rep9's block allocation) write
  `execution_fills` only. Neither rep2 nor rep9 ever writes
  `portfolio.tax_lots` or `positions`; the custodian's data is the record.
  No custodian feeds short positions, so rep13 DERIVES the short book from
  SELL SHORT / BUY TO COVER fills (`long_short.short_book`). Each opening
  fill is a short lot, and each cover closes the named lots, else the
  oldest. Block allocations book fills too, so they are covered.
- **Live counts:** 0 positions with a negative quantity; 0 execution
  instructions; 0 proposal trades; 34 held-away accounts; 0 models.

## Fix 1 discovery (2026-11-07) — the first full run: 175 PASS / 10 FAIL, all VERIFY

- **A — direct service reads ran with no org context (6 FAILs).** `services.database`'s
  wrapped pool applies `app.current_org_id` / `app.is_super_admin` /
  `app.current_auth0_sub` ONCE, inside `acquire().__aenter__`, from the task's
  contextvars. The verify's `_ctx` sets only those contextvars (`set_rls_context`),
  and it was entered AFTER `acquire()`, so each transaction carried
  `app.current_org_id = ''`: platform rows were visible and every org row was
  hidden. Proof (read-only, as `app_service`, `rolbypassrls = false`): acquire
  then `_ctx` gave GUC `''` and 3 users visible in 2nd Act's org (the users
  carve-out only). `_ctx` then acquire, the route's order (`rls_context_middleware`
  sets the context before the handler acquires), gave GUC = the org and 18 users.
  The services take the caller's connection by contract and set no org context of
  their own, exactly as the routes call them. So this is VERIFY, not CODE: all 6
  sites now enter `_ctx` before `acquire()`.
- **B — rep3's duplicate guard comes BEFORE validation (4 FAILs), by design and
  unchanged.** The key is (org, sha256 of the file bytes) against an upload whose
  outcome is still `received` and that a live model still references.
  `upload_file` logs a `duplicate` row and returns 201. `confirm` → `_parse_upload`
  then raises 409 "this upload is duplicate", before any validation. rep13_neg.csv,
  the excluded-short case and rep13_name.csv were byte-identical to rep13_m1.csv,
  the approved-to-be 130/30 model. Each now lists M1's exact rows in its own order:
  same holdings, so exactly its rule fires, but distinct bytes. A new check asserts
  the four hashes differ.
- **C — the render-test count matched title prefixes across the whole suite
  (1 FAIL: 12).** 4 other files' tests share a prefix:
  admin/userManagement "a view-only row …", bugfix4 "the panel renders …",
  bulkapprove1 "a view-only envelope …", portentry1 "a lost envelope yields …".
  The count now runs `tests/replication/rep13.test.mjs` by path and requires
  `# tests 8`, `# pass 8`, `# fail 0` and no "not ok".
- **Newly reached once A was fixed (VERIFY):** the account panel check compared
  `signed_quantity` with the text `"-375"`. The API returns the column's scale,
  `"-375.00000000"` (the same as `quantity` / `price`). It now compares the exact
  Decimal, −375, and requires the leading minus.
