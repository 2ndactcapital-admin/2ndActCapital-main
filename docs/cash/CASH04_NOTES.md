# CASH04 — discovery notes: from PLAN to ORDERS (cash proposals, approval → instructions, the maturity waterfall)

Checked against the repo at `af56a5e` (merge cash03) and the live database, 2026-11-09 (read-only catalog queries as
`app_service`). Standing rule (Joe, 2026-11-08): **use the app's existing constructs; a new one only where none can
carry it**, named and justified below.

Source documents: `CASH_TREASURY_DESIGN_v1.md` §2.4 (waterfall), §2.5 (withdrawal order), §4 stage A, §7;
`CASH00_DISCOVERY.md`, `CASH_OBLIGATIONS.md`, `CASH_POLICY.md`, `CASH_LADDER.md`, `CASH02_NOTES.md`, `CASH03_NOTES.md`.
**[FIND]** `CASH_TRACK_PLAN_v1.2.md` is still not in the repo (cash01–cash03 found the same); its §1 / §2.1 / §2.3 /
§2.4 are known only through the sprint prompt's own quotations. Nothing is invented from it.

---

## 1. Earlier cash decisions that bind this sprint

| Decision (sprint) | What cash04 does with it |
|---|---|
| ONE obligation read (`cash_coverage.household_report` → `scope_lines` / `scope_liquid`), sleeve windows from `cash_policy.windows`, targets derived never stored (cash01, cash02) | The proposal sizes on the SAME derived plan cash03 shows. The windows that classify a held bill into a sleeve are `CP.windows(N, R, horizon, today)` — never re-derived. |
| The ladder and the instrument plan are DERIVED; "cash04 snapshots the rungs it proposes, at proposal time, with the policy version id" (cash03 D3) | `rebalance_proposals.trigger_ref` carries the snapshot: policy id + version, the account's rung shares, targets, holdings by sleeve, the plan fingerprint. No `cash_ladder_rungs` table. |
| Per-account sleeve $ = the sleeve target × the account's cash-balance weight (cash03 D8); "not allocated" when several accounts have no balance | Each account's targets are cash03's `plan.sleeves[*].accounts[*].value`. An account whose value is "not allocated" gets **no proposal**, with that reason. |
| After-tax choice per sleeve × account (cash03 §6) | The instrument a proposal buys is cash03's `chosen` for that sleeve × account. cash04 never re-chooses. |
| Read gate `view_portfolio | manage_portfolio` + the one visibility rule; a member reads their own family read-only (cash01) | The Proposed-trades panel rides the Treasury envelope for STAFF only. A member's envelope is unchanged (§10 D10). |
| Maker-checker through `services.sod` on every approval (cash02, S30) | Unchanged: a cash proposal is approved on `rebalance_proposal_approval` (`sod.REBALANCE_PROPOSAL`). |
| Money Decimal, fixed-point text at the JSON boundary (`money_text`) | Same. |
| Read-only on money (cash01–cash03) | Still: a proposal is a trade list for securities. Nothing moves money between accounts (cash07). |

## 2. How a rebalance proposal is stored, approved and turned into instructions (rep5 → rep13)

| Step | Function | Tables / kinds |
|---|---|---|
| Generate | `rebalance_proposals.generate` / `_generate` → `_write_generated` (one transaction: supersede the open draft, insert proposal + trades, audit `proposal.generated`), under `account_lock` (advisory lock per account) | `public.rebalance_proposals` (one OPEN per account: `rebalance_proposals_open_uq`), `public.rebalance_proposal_trades` (`line_uq`) |
| Submit | `rebalance_proposals.submit` → `HT.open_task(kind=…)` | person's: `rebalance_proposal_approval` (maker-checker, eligible `approve_proposals`); system's: `rebalance_proposal_review` (`system_open`) — `workflow_run_steps` |
| Approve | `rebalance_proposals.decide` → `sod.gate(item_type=sod.REBALANCE_PROPOSAL, …)` → `HT.complete_task` → `_apply_outcome` (sod.gate again, under the lock) | SoD item type **`rebalance_proposal`**; refusals recorded in `sod_decisions` |
| Route | `_apply_outcome` → `rebalance_proposals.execute` → `custody.trading.route_trade_list(…, upstream=…)` → adapter → **`execution.create_instructions(…, routed_via=…, routing_reason=…, upstream=…)`** | `public.execution_instructions` (+ `_lots`, `_events`); a PERSON's proposal → instructions start `approved` (`_verify_upstream` re-reads the proposal, `sod.gate` again); a SYSTEM proposal → drafts made by the reviewer, then `execution.submit` (a second person) |
| Executed | `sync_executed` when every instruction is closed / cancelled / expired | `status = 'executed'` |
| Screens | `/portfolio/proposals/{id}` (approve / reject / execute), `/portfolio/instructions/{id}` (mark sent, fills, letter / order sheet) | routes `GET /proposals/{id}` (view_proposals), `POST /proposals/{id}/submit|execute` (manage_proposals), `POST /proposals/{id}/approve|reject` (approve_proposals) |

**[FIND] The prompt names the wrong router registry.** `services/execution_routers.py` (`registry()`, `route_refusal()`,
`NotConnectedRouter`) is rep9's **FIX block router** (`manual` / `fix`, nothing connected). It is not "Manual / Altruist by
custodian". The per-custodian choice is `services/custody/trading.route_trade_list`: held-away refused; advise / other
custodian → `ManualTradeAdapter`; no adapter registered for the custodian → manual; Altruist → `AltruistTradeAdapter`,
which routes to manual while `equity_trading_api` is not an evidenced 'yes' (it is 'unknown'), and **says so**.
cash04 routes through `route_trade_list` (the ONE path every proposal uses). It also reads `execution_routers.route_refusal`
for the route a cash proposal is offered: `manual` (always None) — a `fix` request is refused with its reason, honoured,
never bypassed.

**[FIND] SOC5 trading authority is not on the trade path.** `services/trading_authority.py` gates the assistant's
money-movement actions (`spv.record_transaction`, `spv.subscribe`) only; `execution.create_instructions`,
`route_trade_list` and `rebalance_proposals.execute` never call it. The trade path's authority check is rep7 / rep7b
`services.authority.refusal` (via `held_away.refuse`): an account must be **managed** (authority) at an **integrated** or
other custodian for any proposal path, and the executor must fit the custody. cash04 relies on exactly that check (it
is "the existing execution path") and does not wire SOC5 into trades — that would be a new rule, not a reuse.

## 3. Does a cash proposal fit as a kind? — YES (decision D1, with evidence)

`rebalance_proposals.proposal_kind` already has three kinds (`model`, `one_off`, `options`), each a strict branch of
`rebalance_proposals_kind_chk`. A cash proposal is one account's trade list, approved by a different person, routed to
execution instructions — the same lifecycle, the same screens, the same SoD item type. What it needs that the table
does not yet admit:

| Need | Today | cash04 (docs/cash04_part1.sql) |
|---|---|---|
| A kind with no model | `one_off` requires `trigger = 'manual'` AND `maker_kind = 'user'` — the waterfall job is the system | NEW branch `proposal_kind = 'cash'`: no link / model / version; `trigger_ref ? 'cash_policy_id'`; trigger `manual` ⇒ a person, `cash_waterfall` ⇒ the system. The `model` branch is TIGHTENED to refuse `cash_waterfall` |
| A trigger for the job | `rebalance_proposals_trigger_chk` has 6 triggers | `+ 'cash_waterfall'` |
| A limit price and par on a line | `rebalance_proposal_trades` has no price kind; `execute()` always routes **at market** | NEW column `cash_leg jsonb` (the rep8 `option_leg` / rep13 `short_leg` pattern) with `rebalance_proposal_trades_cash_leg_chk`: an object, a sleeve, a price kind (market / limit), a positive limit when limit, buy / sell only |
| Idempotency per (household, period) | — | `rebalance_proposals_cash_waterfall_uq` (account, period) for live `cash_waterfall` rows — the backstop, as `model_change_uq` is for model changes |

Nothing else changes: `execution_instructions`' CHECKs (side, exactly one of quantity / notional, price kind) are
**untouched** — a cash line uses `buy` / `sell` only and satisfies them as they are. One open proposal per account
(`open_uq`) holds for cash too (decision D4).

## 4. How a bill / note order is expressed today — **[FIND] it is not**

There is no fixed-income order convention anywhere: `execution_letter._amount` prints "N shares", `order_sheet` "N
shares", `rebalance_proposals._trade_line` always builds `PriceGuidance(kind="market")`. cash04 decides (D5):

- **MMF** — buy by **notional** at market (`price_source 'none'`, as the table already allows); sell by **quantity**
  (shares = amount ÷ the account's own price; `rebalance_proposal_trades_sell_qty_chk` requires a quantity on a sell).
- **Bill / note** — by **par**: `quantity` = par in USD face, in $100 increments (Treasury's minimum increment);
  `limit_price` = price **per 100 of par** (the bond-market quote). The trade row's `price` is per $1 of par
  (`limit ÷ 100`), so `quantity × price` is the cost. A buy limit for a **bill** = `100 ÷ (1 + y × d ÷ 365)` from the
  yield the plan used (rounded DOWN to 0.0001 — never pay more than that yield implies); for a **note** the coupon is
  not recorded, so the limit is **100.0000** (never above par — stated on the line). A sell limit = the account's own
  valuation per 100 par (rounded UP — never sell below it).
- The instruction's notes say it in words ("quantity is PAR (USD face); the limit is per 100 of par"); the letter /
  sheet print "par (USD face)" for a fixed-income security instead of "shares".
- **[FIND]** Positions record a bill as quantity × price; cash04 assumes a Treasury position's quantity IS par in USD
  (market_value ÷ quantity is then the price per $1 par). Stated on every fixed-income line.

## 5. The scheduler job registration path (secmaster1's jobs as the model)

`services/security_master_jobs`: a `JobSpec` in `JOBS` (system definition `system:<name>`, one service task per step,
`action_registry_key`), seeded in the **platform org** (Hollisworks) by an apply script `--seed-jobs` with the trigger
**INACTIVE** (`ensure_platform_jobs(..., active=False)`); the action is a `workflow_invocable` write verb registered by
the owning module's `register_actions()` (called from `services/assistant_actions/__init__.register_all`, which the
scheduler process calls every tick — `ensure_actions_registered`). The Super Admin switches it on with BUGFIX4's
`set_schedule` on Admin → Security master (the existing workflow admin for platform jobs); a held run blocks the schedule
until closed; history is `workflow_runs` + `job_rows`. `ai_drift` is the model for a platform job that reads ACROSS orgs
(a test platform org's run may touch test orgs only).

cash04: `cash_maturity_waterfall` (`system:Cash — maturity waterfall`), action **`cash.maturity_waterfall`**
(services/cash_waterfall.py), weekdays 07:15 New York — it acts on a household only when a new month starts (the
monthly run) or a rung matured since its last run (on-maturity). Seeded INACTIVE by `_apply_cash04.py --seed-jobs` —
an OPERATOR step (the sprint writes no real row). **[FIND]** verify_secmaster1 asserts every JOBS entry exists in the real platform
org (the other 7 are seeded, inactive), so it needs this seed before it runs.

## 6. The S29b verb class for the waterfall's write

`workflow_autonomy.VERB_AUTONOMY["cash.maturity_waterfall"] = {"class": "eligible", "platform_job": True, "draft_only":
True, "writes": "draft cash proposals … never submits or routes"}`; `sod.VERB_DUTIES[...] = "propose"`. The two verbs
that WOULD act on a proposal are declared **never** with `trades: True` (`cash.proposal_submit`, `cash.proposal_route`)
and are deliberately NOT registered as actions — so no workflow (and no agent) can reach them; `decide_step` on either
SUSPENDS for a person, and an unregistered write is `never` anyway (fail closed). The job's own write path
(`cash_proposals.write_draft`) asserts the row is a `draft` with no task and refuses anything else. The custody cliff
(trade execution never gets an agent) is untouched: the job drafts; a PERSON adopts, a DIFFERENT person approves, the
instructions are carried out by people.

## 7. Withdrawal order and the early-sale warning (v1 §2.5)

Sells of what is over target run **Near-term first, then Reserve, then Long-term**; inside a sleeve the money-market
fund first (same-day), then the bill / rung **closest to maturity first**. Any sale of a bill or note before maturity
carries the warning "early sale before maturity — may lose principal" (`code: early_sale`). Non-cash holdings (anything
that is not a money-market fund with an N-MFP category or a Treasury with `treasury_type`) are listed as untouched and
**never sold**.

## 8. The maturity waterfall (v1 §2.4)

Per household with an ACTIVE household cash policy, per account with a plan: (1) proceeds of rungs that matured since
the last run first **top up any sleeve below target, Near-term first** (then Reserve, then Long-term); (2) the rest is
**reinvested as a new rung** — on the next **uncovered** Long-term obligation date when there is one (an obligation the
ladder lists as unfunded, or a rung of the plan the account does not yet hold), else at the **ladder's end**; (3)
**earnings** (dividend / interest transactions since the last run — `transaction_types` `dividend`, `interest`) are
reinvested **pro rata to the target allocation**. Output: ONE draft cash proposal per account and ONE human task per
household (`cash_waterfall_review`, de-duplicated while open); idempotent per (household, period) — the period is the
month (`2026-11`) for the monthly run and `maturity:<date>` for a maturity.

## 9. Reuse inventory

| Piece | Construct used | New? |
|---|---|---|
| Proposal lifecycle, one open per account, expiry, supersede, audit, events | `rebalance_proposals` (`account_lock`, `_update`, `_audit`, `_publish`, `open_proposal`, `_expire_if_due`) | kind `cash` + trigger `cash_waterfall` (CHECK branches) |
| Trade lines | `rebalance_proposal_trades` | column `cash_leg` |
| Approval | `rebalance_proposals.submit` / `decide` → `rebalance_proposal_approval` → `sod.REBALANCE_PROPOSAL` | — |
| Instructions | `rebalance_proposals.execute` → `custody.trading.route_trade_list` → `execution.create_instructions(routed_via=…)`; drafts → `execution.submit` | `_trade_line` reads a cash leg's limit (additive) |
| Route offered | `execution_routers.route_refusal` (manual always; fix refused, reason shown) | — |
| Account authority | `held_away.refuse` / `authority.refusal` (path `proposal:manual`, `proposal:cash_waterfall` → managed only) | `cash_waterfall` added to `authority.TRIGGERS` |
| The plan | `cash_instruments.scope_plans` (cash03's `_plan_block` loop moved into the service, the router renders it) | refactor, no logic change |
| Holdings | `account_models.load_holdings` (own valuation, cash = latest balance + cash positions) | — |
| Instrument classes | `security_reference_attributes` `mmf_category` / `treasury_type` / `maturity_date` (cash03) | — |
| Earnings | `portfolio.transactions` × `transaction_types` (`dividend`, `interest`) | — |
| Minimum trade | `replication.proposals.min_trade_size` (org setting, default $100) | — |
| Job | `security_master_jobs.JOBS` + `ensure_platform_jobs` + BUGFIX4 `set_schedule` | job spec + action |
| Autonomy | `workflow_autonomy.VERB_AUTONOMY`, `sod.VERB_DUTIES` | 3 entries |
| Human task | `human_tasks.open_task` (one open per (kind, subject)) | kind `cash_waterfall_review` (system_open, outcome done) |
| Sheet | `execution_letter.letter_rows` / `order_sheet` | par wording for fixed income |
| Screens | Household → Treasury tab; links to `/portfolio/proposals/{id}` and `/portfolio/instructions/{id}` | `ProposedTradesPanel.jsx` + `lib/cashProposals.mjs` |

## 10. Decisions

- **D1 — kind, not table.** §3.
- **D2 — scope.** An account whose primary entity has its own ACTIVE entity policy is proposed from THAT policy; every
  other account of the household from the household policy. Never both.
- **D3 — targets.** cash03's per-account sleeve values; rung pars = the household rung × the account's Long-term share
  (to the cent). An account with no allocated value gets no proposal (reason shown).
- **D4 — one open proposal per account.** A cash proposal supersedes only an older CASH draft / pending one; an open
  model / one-off / options proposal blocks it (`OpenProposalExists`, shown) — it never supersedes another kind. A later
  model proposal superseding a cash draft is rep5's own rule (logged, task withdrawn) — recorded, not changed.
- **D5 — orders.** §4.
- **D6 — shortfall.** Buys run Near-term → Reserve → Long-term against the account's available cash (cash + estimated
  sale proceeds + matured par); what does not fit is the SHORTFALL, shown — never borrowed, never taken from non-cash.
- **D7 — indicative.** A rung or Reserve bill with no CUSIP in the security master is not bought; the line reads "needs
  an issue". No instruction ever names an indicative security.
- **D8 — waterfall drafts, a person adopts.** The job writes SYSTEM drafts. A person ADOPTS one (`POST
  /cash/proposals/{id}/adopt`): a new cash proposal with the same lines, that person its maker, supersedes the draft and
  is submitted — so the person-made maker-checker path (instructions start approved) is the only path to instructions.
- **D9 — permissions.** Generate / adopt: `manage_proposals` (internal); submit / approve / execute: the existing
  routes (`manage_proposals`, `approve_proposals`); instructions: `manage_execution` / `approve_execution` as today.
  Switching the job on: Super Admin, BUGFIX4's switch. Every proposal and every job run is audited (`proposal.generated`,
  `cash.proposals.generated`, `cash.waterfall.household`, the run's own `workflow_runs` row).
- **D10 — members.** A member's envelope is unchanged: no proposal, no trade detail (members see no proposals today).
- **D11 — routing.** Fixtures and the Olivo dry run route only to the manual adapter (an unregistered custodian code);
  `execution_routers.route_refusal('fix')` is honoured. Nothing is ever sent to a custodian.
