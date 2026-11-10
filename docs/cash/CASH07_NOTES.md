# CASH07 — payment instructions (stage A, manual): discovery, decisions, reuse inventory (2026-11-09)

Repo at `0b2c427` (merge cash05) and the live database, read 2026-11-09. Standing rule (Joe, 2026-11-08): use the app's
existing constructs; anything new is named and justified here. Decided up front (Joe, 2026-11-08): the v1.2 §2
resolutions, and ONE new table — `payment_instructions` (+ its event log).

## 1. Source documents and findings

- `CASH_TREASURY_DESIGN_v1.md` §4 (money movement, stage A) is in `docs/cash/`.
- **[FIND]** `CASH_TREASURY_ADDENDUM_v1.1_EXECUTION_ADAPTERS.md` and `CASH_TRACK_PLAN_v1.2.md` are still not in the repo
  or in Downloads (cash00 recorded the same). Nothing was invented from them; the §2.1–§2.10 decisions are taken as the
  sprint prompt states them.
- **[FIND]** There are **0** `trading_authority_grants` rows live. Until an operator grants `limited` (or higher) to a
  person for an entity, nobody can initiate a payment from that entity's accounts — by design (SOC5: no grant = no
  money movement). Operator step in docs/cash/CASH_PAYMENTS.md.
- **[FIND]** No custodian declares a payment rail. Altruist: the read integration (`accounts:read`) returns no rail
  evidence (cash00 1f) → every rail `unknown` (= not available). The Olivo accounts' custodian codes (`Schwab`,
  `Goldman Sachs PWM`, `Fidelity`, …) map to keys with no capability rows; two (`goldman sachs pwm`,
  `harbor charitable trust`, …) contain spaces and cannot hold a capability row at all
  (`custodian_capabilities_code_chk`). Rails are recorded per custodian by a Super Admin with evidence before the first
  real payment.
- **[FIND]** The transaction feed has **no transfer type**: the 16 `transaction_types` codes have `direction`
  (debit / credit) and cash05's `cash_activity.TYPE_MAP` gives each code's effect on its holding (a `sell` takes money
  out, a `buy` puts it in). Reconciliation reads that ONE sign table (§6).
- **[FIND]** `verify_s30.py` was 1 FAIL at cash05's merge: its raw-maker-comparison scan mistook cash02's
  comprehensions that iterate a maker list (to name it / hand it to `services.sod`) for comparisons. Two narrow
  allowances were added (a real `if x in r["maker_ids"]` is still flagged). Regression set: verify_s30 must be 0 FAIL.
- **[FIND]** verify_cash05's three since-base checks read the WORKING TREE (routers/cash.py, disclosures.py, `.sql`
  files). cash07 legitimately changes all three (the sweep-rate label for members, two disclosure keys, a migration);
  they now read cash05's OWN end point (`VS.own_text` / `VS.own_diff`). Its runtime check ("cash.sweep_rate in no
  response") became "only on a member's overview" — the cash07 behaviour, on purpose.

## 2. Why two new tables (and nothing else new)

`execution_instructions`' CHECKs protect every trade: `side IN (buy, sell)`, exactly one of quantity / notional, a
`global_security_id`, an `executor_type`, `if_not_filled`. A payment has none of these; widening them would weaken
the trade path for every trade (rep14's `section351_transfers` is the precedent for a parallel table).

- `public.payment_instructions` — the SAME spine: `maker_ids`, `approved_by ∉ maker_ids` (CHECK), status graph +
  frozen terms (trigger), sent / reconcile / cancel / expire columns, SYSTEM-axis archive (events, the task and
  `sod_decisions` point at the id).
- `public.payment_events` — append-only. Unlike `execution_instruction_events` (written by the service), the
  **database** writes one row per status change (`payment_instructions_event_trg`), from the action / actor / detail
  the service sets LOCAL (`payments.event_id / action / actor_id / detail`). A status change naming no actor records
  action `transition` with no actor, which `payment_events_human_chk` refuses — a direct UPDATE cannot move money
  state without a person on record.

Everything else is an existing construct: permissions (2 rows), the SoD catalog (2 duties, 1 item type, 2 rules),
custodian capability keys (6), disclosure keys (1 new + 1 reserved since cash05), a human-task kind (the approval) and a
system-open review kind (unmatched settlement), a bulk-approval type, org settings (3 keys).

## 3. execution_instructions' governance — mirrored, shared or copied

| Piece | execution_instructions (rep2) | payment_instructions (cash07) | Shared / copied |
|---|---|---|---|
| maker list | `maker_id = ANY(maker_ids)` | same CHECK | mirrored (DDL) |
| maker ≠ checker in the DB | `…_maker_checker_chk` | `payment_instructions_maker_checker_chk` | mirrored (DDL) |
| status graph + frozen terms | `execution_instructions_guard` | `payment_instructions_guard` (13 statuses, 24 edges) | mirrored (DDL) |
| teardown path | `execution.teardown` | `payments.teardown` (`payments_teardown_allowed()`) | mirrored |
| system-axis archive | `_archive` (one of 17 copies) | `services.bitemporal.archive_live_row` | **SHARED — extracted this sprint** (the 17 copies are unchanged; moving them is a follow-up) |
| maker-checker rule | `sod.gate` / `sod.enforce` | `sod.gate` (sites `payments.decide` / `._precheck` / `._apply`) | SHARED |
| approval task | `HT.open_task` / `complete_task` / `cancel_task`, kind `execution_approval` | same functions, kind `payment_instruction_approval` (`maker_checker=True`) | SHARED |
| events | `_event` (service INSERT) | DB trigger + `_plain_event` for non-status actions | new mechanism (§2) |
| sent | `mark_sent(channel)` | `mark_sent(channel, external_ref)` through the adapter | mirrored |
| reconcile | `custodian_data` (a reference) / `manual` (a note) | `custodian_data` (feed transaction ids + reference) / `manual` (a note, a person other than the sender — CHECK) | mirrored, stricter |
| cancel / expire | `cancel(reason)` / `expire_due` (cron, never a test org) | `cancel(reason)` / `expire_due` + `unmatched_due`, `sweep_tick` **switched off** (`PAYMENT_SWEEP_ENABLED`) | mirrored |
| letter | `execution_letter.build_letter` | `execution_letter.build_payment_sheet` (same module, same drawing; masked only, no full-number option) | SHARED module (variant) |

## 4. The floor duty (how S29b Task 0 left it)

`sod.Context.evaluate`: a checker who is a maker of an item whose type has a `floor_class` is REFUSED by the floor rule
— "no organization setting and no role (Super Admin included) relaxes it". `Context.policy()` ORs the platform
`enforce_floor` into any org policy (S29b Task 0: an org can only tighten). `sod_item_types_floor_selfapproval_chk`
forbids a floor type from being self-approval capable. cash07 registers (docs/cash07_part1.sql §2):
duties `initiate_payment` (make, permission `initiate_payment`) / `approve_payment` (check, `approve_payment`); item
type `payment_instruction`, floor class `transfer`; platform rules `floor:payment_instruction` and
`policy:payment_instruction` (never; enforce_floor true); `sod.PAYMENT_INSTRUCTION`; task kind map
`payment_instruction_approval → payment_instruction`. `human_tasks.complete_task` checks only the task's maker, so
`payments._precheck` (before the engine commits — the task stays open on refusal) gates against EVERY maker.

## 5. Authority reads for an account

- **Custody (SOC5)** — `services.trading_authority`: `trading_authority_grants (org, entity, user, tier)`;
  `assert_can_propose(pool, org, entity, user, action_key)` refuses below `required_tier(action_key)`. cash07 adds the
  action key `cash.payment_instruction` (`third_party: False` → `limited`); the entity is the source account's
  `primary_entity_id`. Checked for the maker (draft / edit / submit / mark sent) and the checker (approve), like
  `approve_money_movement`.
- **Account (rep7b)** — `services.authority`: `attach` (the approved override ?? the household ?? the default) +
  `effective`. cash07 adds `authority.payment_refusal(account)`: managed (at a custodian) only; read_only / advise
  refuse with what would allow it. Not a copy: it reads `effective` / `valid_setting` of the same module.

## 6. Custodian capabilities and the feed

- Keys today (rep2 + rep13): fractional_shares … margin_maintenance_pct (16). cash07 adds `ach`, `wire`, `journal`,
  `check`, `payment_status_feed`, `payment_sheet`; seeds Altruist (rails unknown, status feed no) and Manual (all no,
  `payment_sheet` yes). The rule stays `custodian_capabilities.available` ('unknown' is not available).
- **Feed fields for matching** (`portfolio.transactions` ⋈ `portfolio.positions`): `positions.account_id` (either
  account), `COALESCE(net_amount, gross_amount, quantity × price)` = the amount EXACTLY (Decimal), `COALESCE(settle_date,
  trade_date)` within value date − 2 … + 5 US business days, the direction per leg from `cash_activity.TYPE_MAP`
  (source: a negative-effect code = money out; destination: positive = money in), `external_ref` as the reference,
  and not already used by another payment (`reconciled_transaction_ids`).

## 7. The disclosure key path

`services/disclosures.KEYS` (catalog row created on first use) + `FALLBACK_SOURCES` (the code constant is the offline
text) + `docs/disclosures/defaults/<key>.md`, loaded as a platform DRAFT by `load_disclosure_defaults.py --drafter`; a
SECOND Super Admin publishes (maker-checker). `cash.payments` (new, risk, both) — fallback `payments.PAYMENTS_DISCLOSURE`;
`cash.sweep_rate` (reserved by cash05) — fallback now the Item 5 text "The cash sweep rate and any spread are disclosed
in our Form ADV."; shown on a MEMBER's Treasury overview.

## 8. Decisions

- D1 Same owner = both accounts in the instruction's household, the same `primary_entity_id`, the same live
  `account_owners` set. Ownership through `entity_relationships` (e.g. a grantor trust) is NOT same-owner in stage A —
  counsel. Anything else: "third-party payments come later" (service + DB trigger).
- D2 The checker's reject returns the instruction to DRAFT (as for trades); `rejected` is the CUSTODIAN's refusal.
- D3 Value date: a US business day, today or later; a weekend / holiday is REFUSED naming the next business day — never
  moved silently (moving a payment can miss the obligation it funds).
- D4 ACH settles provisionally: `provisional_until = settled_on + N business days` (setting
  `cash.payments.ach_return_window_business_days`, default 2); close is refused through the window; a return (R01 …) is
  accepted from settled or reconciled.
- D5 Unmatched: settled, not reconciled, `cash.payments.unmatched_business_days` (default 3) business days after
  settlement → ONE review task (kind `payment_reconcile_review`, system-opened, initiate_payment holders) + an
  `unmatched_alert` event; reconciling withdraws it.
- D6 Adapter registry: account → custodian key → the org's enabled direct adapter (`cash.payment_adapters`) → else
  manual. Enabled but unimplemented → NotConnected (refuses with the reason; never a silent fallback).
- D7 Member e-consent: recorded when the custodian asks (`sent → awaiting_client_consent`); no rail is assumed to need it.
- D8 Permissions: new `initiate_payment` / `approve_payment` (no existing name fits: `manage_execution` /
  `approve_execution` are the TRADE desk's, and money movement must not ride a trade grant). org_admin by default
  (migration + `rbac.ORG_ADMIN_DEFAULT_PERMISSIONS`). Reads: `view_portfolio | initiate_payment | approve_payment` +
  the member / VISIBILITY1 scope.
- D9 Agents: the assistant may at most DRAFT — `cash.payment_draft` is declared (`never` in VERB_AUTONOMY, money; duty
  `propose`) but NOT registered as an action; submit / approve / send are `never` and unregistered.
- D10 2nd Act has one real org-admin checker today: a payment waits for a second eligible person. No bypass.

## 9. Bank details

None stored. Destinations are platform accounts; every response, audit payload, event detail and sheet carries the
stored mask (`account_number_masked`, last four) only — never `custodian_account_id` (which can hold a full number) and
never `account_number_hash`.

## 10. Reuse inventory

| Need | Construct |
|---|---|
| maker-checker (floor) | `services.sod` (gate / enforce / record), the S30 catalog |
| approval + inbox | `services.human_tasks` (open / complete / cancel, `task_makers` from the payload's `maker_ids`), `/human-tasks/mine` |
| bulk approve | `services.bulk_approvals` type `payment` → `payments.decide` (the single path) |
| custody authority | `services.trading_authority.assert_can_propose` (+ action key) |
| account authority | `services.authority.attach` / `effective` (+ `payment_refusal`) |
| rails | `services.custodian_capabilities.capability` / `available` (+ 6 keys) |
| business days | `services.us_business_days` (cash03) |
| feed | `portfolio.transactions` / `positions`, `cash_activity.TYPE_MAP` |
| sheet | `services.execution_letter` (variant `build_payment_sheet`) |
| adapter shape | `services.execution_routers` (ABC, not-connected registry) → `services.payment_adapters` |
| disclosures | `services.disclosures` (+ `cash.payments`; `cash.sweep_rate` shown) |
| archive | `services.bitemporal.archive_live_row` (NEW shared helper) |
| visibility | `authz.member_entity_scope` + VISIBILITY1 path guard |
| audit / events | `audit_log` (`cash.payment.*`), `domain_events` (`payment.*`) |
| screens | the existing Treasury tab (`TreasuryDaily` → `TreasuryPayments`) |
