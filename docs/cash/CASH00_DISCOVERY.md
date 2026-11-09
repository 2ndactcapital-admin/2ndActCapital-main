# CASH00 — discovery and design confirmation (folded into CASH01, 2026-11-08)

The cash design (`CASH_TREASURY_DESIGN_v1.md`, 2026-11-04) checked against the live app (repo at `8551137` and the
live database, 2026-11-08). Standing rule (Joe, 2026-11-08): **use the app's existing constructs.** Create a new one
only where no existing one can carry it, and name and justify it here.

**Source documents.**

- `CASH_TREASURY_DESIGN_v1.md` is mirrored in `docs/cash/`.
- **[FIND]** `CASH_TREASURY_ADDENDUM_v1.1_EXECUTION_ADAPTERS.md` and `CASH_TRACK_PLAN_v1.2.md` were not available to
  this sprint. They are not in the repo or in Downloads. They are NOT mirrored, and nothing here is invented from them.
  The v1.2 §2 decisions the sprint prompt listed are recorded in 1e as the prompt gave them, then checked against the
  code.

---

## 1a. Commitments and calls — one source of truth

There are two books for two different kinds of vehicle. They do not overlap on any live row.

| Question | Third-party fund (an LP interest held by a family entity) | House SPV (our own vehicle) |
|---|---|---|
| (i) the commitment and its unfunded amount | `portfolio.commitments` (`commitment_amount`, `unfunded`), hanging off the holder's `portfolio.positions` row. `called_to_date` is derived from `portfolio.transactions` (`call_*` types, by flag) by `services/portfolio_commitments.recompute_commitment` | `spv_subscriptions` (`commitment_amount`, `funded_amount`). The position is the derived view `portfolio.spv_derived_positions`, which is never copied |
| (ii) a noticed call | `cash_obligations` row, `source_type = capital_call_noticed`, `source_ref = commitment` (NEW, CASH01; manual or Chancery) | `spv_transactions` (a call transaction) allocated per subscriber in `spv_transaction_allocations` |
| (iii) a paid call | `portfolio.transactions` `call_investment` / `call_mgmt_fee` / …, which then re-derive `called_to_date` | `spv_transaction_allocations.settled_at`, which moves `spv_subscriptions.funded_amount` |

**Evidence (live DB).**

- 4 live `portfolio.commitments` rows.
- **0** of them hang off an SPV asset (`portfolio.assets.internal_spv_id IS NOT NULL`).
- 3 live `spv_subscriptions`.
- `spv_transactions` carries only `buy` / `call_mgmt_fee` types today.

**Decision.** Both books stay. The join is `portfolio.assets.internal_spv_id → spvs.id`. When it exists, **the SPV
ledger wins**: `services/cash_obligations._projected_calls` excludes every commitment whose asset is an SPV asset, so
an SPV call can never be counted twice. Obligations READ both books and never copy either.

**SPV calls in CASH01.** They have no projection: a house SPV's calls are noticed by the SPV desk, not modelled. A
noticed SPV call recorded on Treasury names `source_ref_type = spv_subscription`. Wiring
`spv_transactions` / `spv_transaction_allocations` straight into the ledger is cash02+ work. No live call transaction
exists to prove it on.

**The TA projection's API.**

- `services/ta_model.project_cash_flows(committed_capital, called_to_date, distributed_to_date, current_nav, params,
  horizon_periods)` is pure, Decimal, and never persisted.
- A commitment's state and active parameters come from `services/ta_state.resolve_commitment_state`. CASH01 moved it
  out of `routers/modeling_ta._resolve_commitment_state` unchanged; the router now wraps it, and its error maps back
  to the identical HTTPException. GET `/modeling/ta/projection`, GET `/modeling/ta/obligations` and the cash ledger
  therefore read ONE state.
- Parameters come from the commitment's override (`portfolio.ta_model_params`), else from a
  `liquidity_terms.ta_strategy_key`, else nothing.
- **[FIND]** There are **0 live TA overrides**, so the two Olivo commitments ($8M Harbor Point IV, $4M Caldera II) read
  "not estimated — needs a TA strategy". The operator sets one on the commitment's projection screen. A strategy is
  never guessed.

## 1b. Liabilities — what debt service can be computed today

`portfolio.liabilities` (crmdetail1) carries:

- `kind` and `current_balance`;
- `original_amount`;
- `rate_type` (fixed / floating);
- `rate_text` (free text, e.g. "5.35% fixed, 2049");
- `maturity` (a date column; **NULL on all 7 live rows** — the year sits only inside `rate_text`).

There is **no payment amount, no frequency and no interest-only flag** anywhere.

**What CASH01 computes** (`services/cash_obligations._debt_service`):

| Case | Result |
|---|---|
| A stored schedule exists (`cash_obligations`, `source_type = debt_service`, `source_ref = the liability's lineage_id`) | The schedule is used and nothing is derived. This is how a real amortising payment is entered. |
| Only a fixed rate is readable | Interest-only estimate: balance × rate ÷ 12, monthly from the 1st of next month to `maturity` (or the horizon). Labelled "estimated — interest only, rate X% from terms '…'". |
| A floating rate on an index the app does not carry (SOFR + spread) | "not estimated — needs payment terms (… floats on an index the app does not carry)". The app has no SOFR series. |
| No readable rate | "not estimated — needs payment terms". |

**Olivo today.** 5 of the 7 liabilities are estimable (the four fixed mortgages and the farm loan). The two lines
(Goldman SBL "SOFR + 1.10%", the art line "SOFR + 1.75%") need terms.

**Lines of credit.** A line's UNDRAWN amount is shown separately, never counted. CASH01 reads the line's approved
limit from its `original_amount`. The Olivo lines have none, so they read "needs the line's approved limit". A
dedicated limit column is left for cash02, if the advisers want it distinct from "original amount".

## 1c. Liquid balance — the net-worth core

- **The read.** `services/networth.py` (rollup2) is the one core: `load` → `figures`, with `by_bucket` keyed by
  taxonomy key. Cash & Equivalents = the buckets `taxonomy_mc_8_1` and `taxonomy_sub_8_1_<n>` (cash, money-market,
  T-bill funds), matched exactly (Rule 4 key patterns), never by `startswith('taxonomy_sub_8_1')` (that would catch
  `8_10`).
  - Per entity: the entity's OWN cash, `figures(ctx, weights={entity: 1})`. The payer pays from its own accounts.
  - Per household: `figures(ctx, members)`, the household figure (look-through, each position once).
- **Held-away cash.** It is a position like any other: `services/portfolio_cash.record_cash_balance`, a `kind = cash`
  asset (one per org and currency, `assets_cash_active_uniq`) or a named `cash_holding` line. It is counted the same
  way. There is no `cash_balances` table, and none is added.
- **Demo coverage today** (`load_olivo_obligations.py --dry-run`, 2026-11-08).
  - Household liquid: $33,004,800.00.
  - With the 18 manual obligations plus the derived interest: 25.7 months of coverage, covered beyond the 24-month
    horizon.
  - **But** the ILIT, the GRAT and every property LLC hold **no cash of their own**: each is below its minimum and
    uncovered within 90 days.
  - The foundation ($1.3M) cannot meet its own 5% payout ($2.2475M on 2026-12-31).
  - This is exactly the per-entity point of v1 §2.2.

## 1d. Trust / charitable / insurance terms in the CRM

crmdetail1's `detail.*` fields (`entity_attributes`, typed in `services/crm_vocab`). Present live:

- GRAT: `annuity_term_years` = 2, `annuity_end` = 2027-12-31.
- CRUT: `unitrust_rate_pct` = 5.
- Foundation: `min_distribution_pct` = 5.
- ILIT: `policy_face_amount` = 25,000,000.

**Missing for a dated amount:** the GRAT's annuity AMOUNT, every next-payment DATE, and the ILIT's PREMIUM. CASH01
adds six detail fields to the SAME registry (no schema change; the existing construct):

- `annuity_amount` and `annuity_payment_date` (GRAT);
- `unitrust_payment_date` (CRUT);
- `policy_premium_amount` and `policy_premium_due` (ILIT policy group);
- `distribution_due` (foundation).

**Derivation** (`_crm_payments`):

| Source | Line |
|---|---|
| GRAT | amount yearly from the next payment through `annuity_end` (firm) |
| CRUT | rate × the trust's value from the core, yearly (estimated) |
| ILIT | premium yearly (firm) |
| Foundation | rate × value, yearly (estimated) |

- Missing pieces read "not estimated — needs …".
- A STORED row of the same kind for the same entity wins, and nothing is derived for that pair, so a manual stand-in
  (the Olivo loader's GRAT / ILIT / foundation rows) is never counted twice.

## 1e. Reuse inventory

**Every piece of the cash design.** Each piece maps to the existing construct it uses, or a stated reason a new one
is needed.

| Design piece | Construct used | New? |
|---|---|---|
| Obligation records (manual, noticed, invoice, schedule) | **`public.cash_obligations`** | NEW — no table carries a dated FUTURE outflow. `portfolio.transactions` / `spv_transactions` record what happened; `portfolio.commitments` an amount with no dates; `portfolio.liabilities` a balance and free-text terms; `member_todos` reminders. Bi-temporal (valid axis, `lineage_id`), RLS on every op, the crmdetail1 demo delete guard |
| Recurrence | an RRULE subset on the row, expanded at read time | no rows per occurrence |
| Projected calls | `ta_model` + `services/ta_state` + `portfolio.ta_model_params` | moved, not new |
| Debt service | `portfolio.liabilities` (+ an optional stored schedule row) | — |
| Trust / charitable / insurance payments | `entity_attributes` `detail.*` + `services/crm_vocab` (6 fields added) | fields only |
| Liquid balance | `services/networth.py` (rollup2) buckets; positions via `portfolio_cash` | — |
| Minimum coverage per entity / household | **`public.cash_coverage_minimums`** | NEW — `org_settings` is updated in place (not bi-temporal) and keyed by org only; `entity_attributes` cannot hold a household; `household_lot_method` / `household_authority` are the precedent for a per-household setting table. One table for both scopes. Default 6 months |
| Tasks and alerts | `member_todos` via `services/workflow_todos._upsert_todo` (de-dup on user + source + subject), closed `done` when resolved | — |
| Estimate labels | the disclosure library `services/disclosures.py`: `tax.estimate_label` + a new key `cash.coverage_estimate` (fallback text "… have counsel review …") | key only |
| Visibility | `services/authz.member_entity_scope` (bugfix2) + VISIBILITY1 `family_visibility` (path guard + `require_visible`) | — |
| Permissions | `view_portfolio` (read; a member holds it) and `manage_portfolio` (author, via `require_internal_permission`) — reused from the portfolio set | — |
| Audit | `services/audit.write_audit_log` (`cash.obligation.*`, `cash.notice.recorded`, `cash.minimum.set`) | — |
| Documents (invoices) | Chancery `documents` (`source_ref_type = document`) | — |
| Screens | Household page tab ("Treasury") + `/treasury` for a member | — |

**Later decisions** (v1.2 §2 as given by the prompt; RECORDED, nothing for payments is built in CASH01):

- **`payment_instructions` vs `execution_instructions`.**
  - `execution_instructions` (rep2) is the trade path: lifecycle draft → pending_approval → approved → sent →
    partially_filled → filled → reconciled → closed, plus cancelled / expired. It has an approval human task
    (maker ≠ checker), append-only `execution_instruction_events`, a system-axis archive, and `instruction_settlements`.
  - Money movement gets a parallel `payment_instructions` table that REUSES that lifecycle's names and the same
    human-task approval and events pattern. It is not a new lifecycle, and not rows in `execution_instructions` (whose
    lines are trades: `account_id`, `price_kind`, lots). Built cash04+.
- **Authority.** `trading_authority_grants` (who may instruct for an entity) and the rep7b matrix
  (`services/authority.py`, `household_authority`, `account_authority_settings`) gate any future instruction. Cash
  adds no authority store of its own.
- **Adapters.** `custodian_capabilities` (versioned facts per custodian) plus the `services/custody/` adapter registry
  are the home of a future `PaymentAdapter` (v1.1's execution adapters). There is no payment adapter today.
- **Bank details.** Stored only as an adapter reference plus the **last four** digits, the
  `services/custody/base.AccountNumber` rule (mask + salted hash, never the number). Nothing is stored in CASH01.
- **The Custody Cliff** (CLAUDE.md) is unchanged: money movement never gets an agent. CASH01 is read-only on money.

## 1f. Altruist — what the read integration returns vs what must be asked

What the integration returns today:

- **Returned** (`altruist_positions_sync`, `account_balances_daily`): positions and daily balances, including a cash
  balance, tagged `source_system = 'altruist'`. They count as cash once they are classified under the cash taxonomy
  keys.
- **Not returned:**
  - the cash rate (negotiated or scheduled) or any spread on cash;
  - the sweep program's composition (sweep vs high-yield). `services/altruist_one.py` documents this as **not
    separable**, a caller-supplied input;
  - the money-market fund menu;
  - brokered CDs and Treasury purchase support;
  - any write endpoint or sandbox.

**Must be asked of Altruist** (v1 §9.1, unchanged): the cash rate and the spread (ADV Item 5 either way); the sweep
vs HY split per account; the MMF menu; brokered CDs; Treasury purchase; the write-endpoint sandbox.
