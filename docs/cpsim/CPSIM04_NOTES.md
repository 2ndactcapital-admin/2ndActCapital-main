# cpsim04 — discovery notes, decisions as built, reuse inventory

Sprint cpsim04 (2026-11-11), branch `cpsim04-work`. The simulated custodian MOVES MONEY and the simulated BANKS answer:
an APPROVED payment instruction between a family's own accounts is sent by a PERSON ("Send to custodian") to the
simulator's transfers API (contract §9, every field ASSUMED); the simulator's clock acknowledges, settles and (by its
behaviour) returns it with an ACH code; a PERSON refreshes the status ("Refresh status from custodian"), each new
custodian status applied through the EXISTING payments person function with that person as the actor; the cpsim02 sync
brings the cash transactions and the EXISTING `matches()` / `reconcile(custodian_data)` close the loop. Design:
`COUNTERPARTY_SIMULATOR_DESIGN_v1.md`; notes of the earlier sprints: `CPSIM02_NOTES.md`, `CPSIM03_NOTES.md`; operator
guide: `CPSIM_ADMIN.md`. This completes the simulator's v1.

Paths are relative to `apps/api/` unless they start with `docs/` or `apps/web/`.

## 1. Findings (Task 1)

### 1a. Prerequisite and confirmed facts (HEAD `b767e2a`)

- **cpsim03 is merged**: `git log main` shows `b767e2a merge cpsim03`.
- Payments (cash07) — `services/payments.py`: `RAILS` / `RAIL_KEYS` `:100-101` = `ach, wire, journal, check`;
  `STATUS_LABELS` `:102`; `load_account` `:314`; `same_owner_refusal` `:357`; `declared_rails` `:369` (reads
  `PA.custodian_key(account)` → `custodian_capabilities`); `rail_refusal` `:382`; `authority_refusals` `:392` (BOTH: SOC5
  `TA.assert_can_propose(… MONEY_ACTION)` and rep7b `AU.payment_refusal`); `_update` `:460` (archive on the system
  axis, then `set_config('payments.*', …, true)` so the DB trigger writes the event); `_transition` `:880`;
  `mark_sent` `:899` (channel ∈ `PA.CHANNEL_LABELS`; re-checks the adapter, rail, value date and both authority checks;
  **writes `adapter = 'manual'` unconditionally** — `:925`); `consent_requested` `:930`; `acknowledge(external_ref)`
  `:938`; `settle(settled_on)` `:948` (refuses a date after `payments.today()`; ACH → `provisional_until`);
  `custodian_reject(reason)` `:969`; `record_return(return_code, reason)` `:979` (ACH only, `R\d\d`); `MATCH_SQL`
  `:998`; `leg_of` `:1012` (direction from `services.cash_activity.TYPE_MAP` signs); `matches` `:1026`; `reconcile`
  `:1043`; `close` `:1090`; `cancel` `:1106`; `sweep_tick` `:1222` (expiry + unmatched only, OFF unless
  `PAYMENT_SWEEP_ENABLED=1`); `actions_for` `:1402`; `vocabularies` `:1436`.
- Adapters — `services/payment_adapters.py`: `CHANNEL_LABELS` `:39`, `SendResult` `:52`, `StatusResult` `:62`,
  `ManualPaymentAdapter` `:81`, `NotConnectedPaymentAdapter` `:102`, `_CONNECTED = {}` `:120`, `custodian_key` `:131`
  (`trade_custodian_key(custodian_code)`), `adapter_for` `:146`, `enabled_for_org` `:155`. The 5-step checklist `:21-29`.
- Simulator (cpsim01–03): package `cpsim/` mounted at `/cpsim` (`cpsim/__init__.py`); own auth (`cpsim/auth.py`); role
  `cpsim_app` via `SET LOCAL ROLE` (`cpsim/db.py`); `request_log` + SIMULATED header by the ASGI wrapper
  (`cpsim/app.py SimulatedMarker`); registry, behaviour (`cpsim/registry.behaviour` `:116`, fields `:34`), clock
  (`advance_clock` `:388`, admin `routers/admin_counterparties.py:334` re-evaluates working orders); fake bank details
  (`cpsim.counterparty_bank_details` CHECKs: ABA `^99[0-9]{7}$` failing the real check digit, account `^CPSIM[0-9]{6,17}$`,
  BIC with country `XX`, IBAN check digits `00|01|99` failing the real check — read from the catalog 2026-11-11);
  institution types `altruist` (rest_oauth), `schwab` (fix), `domestic_bank` (bank_wire_ach, group `bank_domestic`),
  `international_bank` (bank_swift, group `bank_international`).
- cpsim02: environment `simulator` (`altruist_oauth.ENVIRONMENTS` `:101`), `_transport_for` `:146`,
  `simulator_counterparty` `:169`, `resolve_hosts` `:190`, `check_destination` `:210`; crosswalk source
  `ALTRUIST_SIMULATOR`; ledger tag `altruist_simulator`; demo rule `public.counterparty_sim_may_connect`; webhook
  receiver `services/altruist_webhook.process_event` `:218` (simulator secret forces `simulator`; cpsim03 adds the order
  to-do `:283-292`).
- cpsim03: `services/altruist_trading.py` (the client conventions — `order_session` / `submit_with_session` release the
  connection before the HTTP call), `services/custodian_execution.py` (the D2 route, the person actions, the to-do),
  `scripts/cpsim03_capabilities.py` (the loader), `cpsim/webhooks.schedule_orders` + `drain()` (after-response
  delivery).
- The client's `TRANSACTION_TYPE_MAP` — `services/altruist_positions_sync.py:130-138`: `buy, sell, dividend, interest,
  fee, fee_expense, adjustment`; anything else is a per-row error (`:373-379`). The simulator's book refuses any other
  type too (`cpsim.book_transactions_type_chk`, `cpsim/book.TXN_TYPES` `:33`).
- Boundary: `scripts/cpsim_boundary_check.py` B1–B4; allowed service imports `us_business_days`, `bitemporal` (`:39`).

### 1b. Rails, channels, CHECKs, the columns D5 / D7 write, the S30 floor (catalog read 2026-11-11, as postgres)

| Item | Finding |
|---|---|
| `RAIL_KEYS` | `ach, wire, journal, check` (`payments.py:100`); `payment_instructions_rail_chk` the same four. The transfers contract has `ach, wire, journal` — **check stays manual** (a D4 condition). |
| `CHANNEL_LABELS` | `custodian_portal, email, secure_message, fax, phone, in_person, other`. **No CHECK on `sent_channel` or `adapter`** — only `payment_instructions_sent_chk` (both non-blank once sent). `custodian_api` is added in Python only (no DDL). |
| `external_ref` | EXISTS (`payment_instructions.external_ref`), in the guard's lifecycle list (`docs/cash07_part1.sql:289-295`), written by `mark_sent` and `acknowledge`. D5 stores the custodian's transfer id there — **no new column**. |
| `adapter` | EXISTS, lifecycle column, no CHECK; `mark_sent` hard-codes `'manual'` → it gains an internal `adapter=` argument (default `manual`, byte-identical for every existing caller). |
| `payment_events_action_chk` | 18 actions incl. `mark_sent, consent_requested, acknowledge, settle, custodian_reject, return`. D7 applies EXISTING actions only — **no CHECK change**. A refresh that changes nothing writes no event (nothing happened). |
| `payment_events_human_chk` | `actor_id IS NOT NULL OR action IN ('expire','unmatched_alert')` — unchanged; every D7 step names the refreshing person. |
| Status graph (`payment_instructions_guard`) | `approved>sent`, `sent>awaiting_client_consent`, `sent>acknowledged`, `sent>rejected`, `awaiting_client_consent>acknowledged/rejected`, `acknowledged>settled/rejected`, `settled>reconciled/returned`, `reconciled>closed/returned`. D7 walks this graph one edge at a time; it never jumps. |
| S30 floor | `sod_item_types` `payment_instruction` (floor class `transfer`): it governs **approval** (`decide` → `sod.gate`, `HT.complete_task` refuses the maker, `payment_instructions_maker_checker_chk`). `mark_sent` and every later step have **no sod gate** (initiate_payment + both authority checks on `mark_sent`; initiate_payment alone on acknowledge / settle / reject / return). So Send is gated exactly like `mark_sent` and Refresh like the steps it applies; no new floor is invented. |

### 1c. The D2 mapping — the cash vocabulary and `matches()`

`public.transaction_types` (catalog): `adjustment, buy, call_investment, call_mgmt_fee, call_org_cost,
call_partnership_expense, dist_gain, dist_income, dist_recallable, dist_roc, dist_stock, dividend, fee_expense,
interest, sell, valuation`. **There is no contribution / withdrawal / transfer code.** `services.cash_activity.TYPE_MAP`
signs are the effect on THE HOLDING the row is booked on: `buy` +1 ("a buy moves money INTO the sleeve"), `sell` −1,
both ledger type `transfer` (label "Transfer"); `adjustment` 0 = "the stored sign". `matches()` (`MATCH_SQL`) selects
either account's live transactions in value date −2 … +5 business days with `COALESCE(net_amount, gross_amount,
quantity × price) = amount`, not used by another payment; `leg_of` keeps a SOURCE row only if its sign < 0 and a
DESTINATION row only if its sign > 0. So the codes that fit by sign, with no new vocabulary row:

| feed type (D1) | platform code | sign on the holding | why |
|---|---|---|---|
| `transfer_out`, `ach_out`, `wire_out` | `sell` | −1 | money leaves the source account's cash holding → `leg_of` = out |
| `transfer_in`, `ach_in`, `wire_in` | `buy` | +1 | money arrives at the destination's cash holding → `leg_of` = in |
| `ach_return` | `adjustment` | stored sign | a return moves money back on BOTH legs (+ on the source, − on the destination); one type can only carry that as a signed amount — and a return must never be matched as a payment leg (sign 0 → `leg_of` None) |

[FIND] The transactions grid will label a transfer row "Buy" / "Sell" (the vocabulary's labels); the cash ledger labels
both "Transfer" (`cash_activity.TYPES`). Adding `transfer_in` / `transfer_out` codes would also need a
`cash_activity.TYPE_MAP` change — a second construct for the same sign; not done (the prompt: never a new code if one
fits). Recorded for v2.

### 1d. The cpsim02 book, cash rows, and banks (D6)

- Cash: `cpsim.book_positions` `row_type = 'cash'` (no security, a `market_value`, price_source `cash`) — the documented
  exception; a cash transaction (`row_type cash`) needs a cash position in the same account. The client's sync keys a
  cash row by `name:<name>` (`_security_key`), so a transfer transaction must carry its cash position's NAME — the
  ledger writes the account's cash row name on every cash transaction.
- `book_transactions_type_chk` admits the client's 7 types only → **widened** with the D1 cash types (cpsim only).
- **Banks reuse the book tables unchanged** (`book_households` / `book_accounts` / `book_positions` cash rows /
  `book_transactions`): nothing in them is custodian-specific. The admin page enabled the book tabs for
  `rest_oauth` types only (`book_institution_types`); banks get Import / Export and a new **Received** tab.
- `portfolio.external_references`: `source_system` has **no CHECK** (only `ext_ref_record_type_chk` and the UNIQUE
  `(org_id, source_system, external_id, record_type)`). D6 decision → source **`CPSIM_BANK`** (no DDL).
- `custodian_capabilities_key_chk` already admits `ach, wire, journal, check, payment_status_feed, payment_sheet`
  (cash07); no `altruist_simulator` row exists (the cpsim03 loader was not applied).

### 1e. Unattended registration points (the D10 scan targets)

`services.workflow_autonomy.VERB_AUTONOMY` (`cash.payment_draft` / `submit` / `approve` / `send` all NEVER, money);
`services.sod.VERB_DUTIES` (`:689-700`); the assistant / workflow action registry (`services.assistant_actions` +
`services.workflow_actions` REGISTRY — no `cash.payment_*` key registered); `scripts/workflow_scheduler_tick.py`
(calls `payments.sweep_tick` — expiry + unmatched only, OFF by default); `services/cash_rules.py` (draft_and_submit
only — S29b `eligible_with_cap`); domain-event subscribers (`services.cash_rules.publish_incoming_credits`); the
webhook receiver (`process_event`). cpsim04 declares `cash.payment_refresh` NEVER (money) + VERB_DUTIES `write` and
registers neither Send nor Refresh anywhere; the verify scans every one of these files.

## 2. Decisions as built (D1–D11)

| D | As built | Where |
|---|---|---|
| D1 | `ALTRUIST_CONTRACT.md` §9 — every field ASSUMED: `POST/GET {api_base}/v2/transfers`, `GET /v2/transfers/{transfer_id}`, `DELETE /v2/transfers/{transfer_id}` (while received / pending_consent only); body `client_transfer_id` (= the payment instruction id), `from_account_id`, `to_account_id` XOR `to_bank {institution_slug, account_id, routing_last4 | bic + iban_last4}`, `amount` (decimal string), `currency` USD, `rail` ach / wire / journal, `value_date`; statuses `received, pending_consent, acknowledged, settled, rejected, returned, cancelled` with `reject_reason` / `return_code`; idempotent on `client_transfer_id` (the ORIGINAL returned, 200, whatever the body — §9.1, the §8.1 rule); webhook `transfer.updated`; feed types `transfer_in/out, ach_in/out, wire_in/out, ach_return`. | docs |
| D2 | `TRANSACTION_TYPE_MAP` += the 7 cash types → `buy` / `sell` / `adjustment` (§1c). Additive: types that were per-row errors are now recognised; every other entry unchanged. | `services/altruist_positions_sync.py` |
| D3 | `cpsim/ledger.py`: the account's USD cash row in its latest snapshot is the running balance; every movement is a `cpsim.cash_movements` row (append-only, UNIQUE per source + leg — idempotent) plus the cash row moved (snapshot copy-forward, valid axis) plus a book transaction with an explicit id (`sim-t-…`) and ISO dates. Transfers: debit at acknowledgement (insufficient → rejected "insufficient funds"), credit at settlement; journal / wire settle on the value date, ACH value date + 1 business day; an ACH return reverses both legs `ach_return_after_days` business days after settlement. Fills: a settled buy debits / sell credits the account's cash row on the settle date (clock advance), only when the account's book has a cash row. | `cpsim/ledger.py`, `cpsim/transfers.py`, `cpsim/registry.advance_clock` caller |
| D4 | `services/payment_custodian.simulator_route(conn, org_id, source, row)` — ALL of: not crosswalked `ALTRUIST`; crosswalked `ALTRUIST_SIMULATOR`; `counterparty_sim.allowed` + a counterparty; an active `simulator` connection; `counterparty_sim_may_connect`; `altruist_simulator` in `cash.payment_adapters`; `payment_status_feed` evidenced 'yes'; the rail ach / wire / journal evidenced 'yes'; the destination crosswalked to the same simulated custodian (journal) or to a simulated bank (`CPSIM_BANK`; ACH / wire domestic, wire international). Else today's route with the reason. | as named |
| D5 | `AltruistSimulatorPaymentAdapter` (`_CONNECTED['altruist_simulator']`); `adapter_for(account, enabled, route=None)` resolves a simulator-crosswalked source to key `altruist_simulator` (the only change there). Send = `payment_custodian.send_to_custodian` (initiate_payment, approved only, D4 re-checked, the client `services/altruist_transfers.py`, connection released before the HTTP call) → the EXISTING `mark_sent(channel='custodian_api', adapter='altruist_simulator', external_ref=<transfer id>)`; `CHANNEL_LABELS` += `custodian_api`, refused by the manual "Confirm sent". | as named |
| D6 | Banks: the cpsim02 book tables; `cpsim/transfers._bank_receive` validates `routing_last4` against the bank's fake ACH ABA (ACH) or wire ABA (wire), or `bic` + `iban_last4` (international; wire only), and the account in its book; mismatch / bank behaviour reject → rejected with the bank's reason; the receiving bank's `ach_return_code` returns an ACH too. Crosswalk: a Super Admin links a platform account to a bank book account (Admin → Counterparties → bank → Received → Link), written through `services.portfolio_assets.upsert_external_reference` as source `CPSIM_BANK`, external id `<bank slug>:<account id>:<reference>` where the reference is LAST FOUR digits only (`a1234` ACH ABA, `w1234` wire ABA, `i1234` + the fake BIC). | as named |
| D7 | `payment_custodian.refresh_from_custodian` (initiate_payment or approve_payment): GET the transfer, then apply each NEW status in graph order through `consent_requested` / `acknowledge(external_ref)` / `settle(settled_on = the custodian's settle date)` / `custodian_reject(reason)` / `record_return(code)` with the refresher as the actor; never re-applies, never jumps. The receiver's `transfer.updated` → a to-do for the sender only. | `services/payment_custodian.py`, `services/altruist_webhook.py` |
| D8 | `cpsim.counterparty_behaviour.consent_required boolean NOT NULL DEFAULT false`; "Record member consent" on the custodian's Transfers tab (admin, Super Admin). | schema, `cpsim/transfers.record_consent`, admin router |
| D9 | `scripts/cpsim04_capabilities.py` — `--dry-run` default; `--apply --actor-email` writes ach / wire / journal / payment_status_feed 'yes' for `altruist_simulator` through `set_capability`. Run once `--dry-run` (§5). | script |
| D10 | Send / Refresh are person routes only; `cash.payment_refresh` declared NEVER, not registered; the verify scans every registration point. Third-party payments, cash08 tables, the manual route untouched. | — |
| D11 | `services/altruist_conformance.py` `transfers` group, simulator only; `--target sandbox` → "no published Altruist money-movement contract". | as named |

(§3 reuse inventory and §4 differences follow; §5 the loader dry-run; §6 the diff review; §7 the conformance run.)

## 3. Reuse inventory

| Piece | Existing construct | Used how |
|---|---|---|
| Lifecycle | `payments.mark_sent / consent_requested / acknowledge / settle / custodian_reject / record_return / reconcile / close` | Send and Refresh call them — no second transition path, every event written by the DB trigger with the person as actor |
| Governance | `payments.decide`, human task, `services.sod`, `authority_refusals` | unchanged; Send re-runs `mark_sent`'s checks (it IS mark_sent) |
| Adapter registry | `payment_adapters._CONNECTED`, `adapter_for`, `enabled_for_org`, `SendResult` / `StatusResult` | the simulator adapter registered there; manual still the default |
| Reconcile | `payments.matches` / `reconcile(custodian_data)` | unchanged; the UI preselects the matched rows |
| Client conventions | `altruist_oauth.require_active_connection`, `get_decrypted_access_token`, `resolve_hosts`, `check_destination`, `_transport_for`, `AltruistOAuthError`; `altruist_trading`'s session-then-release shape | `services/altruist_transfers.py`, call for call |
| Book / sync | `cpsim.book_*`, `cpsim02` sync, `TRANSACTION_TYPE_MAP` | cash rows are the ledger; the sync brings transfer rows like any other |
| Clock | `cpsim.registry.advance_clock` + the admin advance | also settles / returns transfers and settles fills' cash |
| Webhooks | `cpsim.webhooks.send_event` / `schedule_orders` / `drain` | `transfer.updated` scheduled after commit (same task set, same drain) |
| To-dos | `member_todos` (cpsim03 `_todo` shape) | the transfer to-do |
| Capabilities | `custodian_capabilities.capability / available / set_capability`; the cpsim03 loader shape | D4 conditions; the cpsim04 loader |
| Crosswalk | `portfolio.external_references`, `upsert_external_reference` | `CPSIM_BANK` links |
| Unattended guard | `workflow_autonomy.VERB_AUTONOMY`, `sod.VERB_DUTIES` | `cash.payment_refresh` declared NEVER |

## 4. Differences from the prompt, and findings

1. **No cash-transfer vocabulary exists**: D2 maps to `buy` / `sell` / `adjustment` by sign (§1c) — no vocabulary row.
2. **`mark_sent` hard-coded `adapter = 'manual'`**: it gains an internal `adapter` argument; the manual "Confirm sent"
   refuses `custodian_api` (only Send records it, with the transfer id) — the cpsim03 pattern.
3. **`custodian_key` for payments**: a simulator-crosswalked source's custodian is `altruist_simulator` (its declared
   rails are the simulated custodian's, not real Altruist's). `services.payments.load_account` attaches it; every other
   account resolves exactly as before.
4. **The send is accepted whenever the custodian CREATED the transfer** (201 / 200), whatever its status: the
   instruction is `sent`; a transfer the custodian rejected at receipt (insufficient funds, a bank's refusal) comes back
   through Refresh as `custodian_reject` — the real lifecycle (`sent>rejected`). A 4xx (malformed, unknown account) is
   refused and the instruction stays approved.
5. **Bank references**: the platform stores no bank numbers. The link keeps the LAST FOUR of the bank's ABA / IBAN (and
   the fake BIC) in the crosswalk's external id; a bank whose details are regenerated afterwards rejects the stale
   reference ("routing number does not match") — exactly the real "wrong ABA" failure.
6. **`settle` refuses a date after `payments.today()`**: a custodian settle date ahead of Hollisworks' today stops the
   refresh at `acknowledged` with that reason (the next refresh continues). Clocks are kept aligned in the demo.
7. **Fills move cash only for an account whose book has a cash row** (an account uploaded without one has no cash
   ledger; creating one would add a position cpsim03's books never had). Cash may go negative from fills (no buying
   power check in v1); transfers never overdraw.
8. **Same counterparty for two orgs shares a book** (cpsim03 finding 12) — unchanged; the verify gives each org its own.
9. **A bank's legs are booked on the SENDING custodian's clock** (its credit, a return's debit): the bank has a clock of
   its own (cpsim01), but a transfer is driven by the custodian that moved the money. A bank's read API (its own clock)
   can therefore lag the ledger until its clock is advanced too. The Received tab reads the movements directly. v2.
10. **The send-time webhook can arrive before the payment is marked sent** (the same race cpsim03 logged for orders):
    the receiver then finds no payment carrying that transfer id and raises no to-do; the Send response already tells
    the sender. Later events (the clock's settlement / return, consent) do raise the to-do.
11. **A fill's cash moves only where the account's book has a cash row.** If an account gains a cash row later (a
    transfer credit creates one), fills that settled before then are settled in cash at the next advance. Documented,
    not changed (v1 has no buying-power model).
12. **cpsim01's Bank details tab shows the generated fake numbers in full** (its own design: they fail every real
    check). cpsim04 never shows more than the last four anywhere it added (transfer responses, payment pages, Transfers /
    Received tabs, crosswalks, audit rows).
13. **Refresh also runs on a reconciled payment** (`reconciled → returned` is a legal edge, so a late ACH return can be
    recorded), beyond the four statuses the prompt named. A custodian-side **cancel** is reported, never applied (the
    payment is cancelled by a person).
14. **Checks stay manual**: the transfers contract has ACH, wire and journal (D4 condition "rail").
15. **Every D4 condition is re-checked at Send** (the route is read again, then mark_sent's checks run BEFORE the HTTP
    call). If mark_sent then fails (a race), the transfer exists at the custodian and the payment stays approved; a
    repeat Send re-POSTs the same `client_transfer_id` and gets the same transfer (§9.1) — never a second one.

## 9. Final state (Task 10)

All ten tasks committed on `cpsim04-work`. The verify (`apps/api/scripts/verify_cpsim04.py`) is WRITTEN, NOT RUN.
Proven during the sprint without the verify: the schema read back 22/22 (`_apply_cpsim04.py --verify`); the in-process
conformance run 51 PASS / 0 FAIL with 0 leftovers; node tests 45/45 (cpsim04 8 + cash07 + cpsim01–03); the boundary
check OK; `authz1_routes.py --check` and `visibility1_routes.py --check` CURRENT; both loaders' dry runs (cpsim03 still
22 to set; cpsim04 6 to set). Not run: `npm run build`, the regressions, the verify.

## 5. The capability loader — `--dry-run` (Task 5, 2026-11-11, run once)

`python3 apps/api/scripts/cpsim04_capabilities.py` (the default `--dry-run`; reads `public.custodian_capabilities`,
writes nothing):

```
custodian code 'altruist_simulator' — evidence 'simulated counterparty — cpsim04' (assumed); mode DRY RUN
  set   ach                                (no row) -> yes
  set   wire                               (no row) -> yes
  set   journal                            (no row) -> yes
  set   check                              (no row) -> no
  set   payment_status_feed                (no row) -> yes
  set   payment_sheet                      (no row) -> no
6 to set, 0 already as planned
real altruist ach = 'unknown', wire = 'unknown', journal = 'unknown', payment_status_feed = 'no' (never changed by this script)
DRY RUN — nothing written
```

NOT applied (operator step). cpsim03's loader covers every key (its own dry-run still prints "22 to set"); it now
SKIPS a payment key whose live row carries the cpsim04 evidence, so applying the two loaders in either order never
resets the payment rails to 'no'.

## 6. Sandbox / production / manual unchanged — diff review against main (Task 5)

`git diff main -- services/altruist_oauth.py services/altruist_identity.py routers/altruist_connection.py` is
**empty**. The transfers client is a NEW module (`services/altruist_transfers.py`) that refuses any environment but
`simulator` before a request (`NO_CONTRACT`). Changed shared files, line group by line group:

- `services/altruist_positions_sync.py` — `TRANSACTION_TYPE_MAP` gains 7 entries (D2). Additive: a sandbox /
  production row of one of those types was a per-row error ("unmapped Altruist transaction type") and is now booked as
  `sell` / `buy` / `adjustment`; every other type, the error path for any other unknown type, and every other line
  are unchanged.
- `services/altruist_webhook.py` — after the existing order hook, a `transfer.updated` event SIGNED BY THE SIMULATOR
  adds a to-do (`payment_custodian.transfer_event_todo`). A real-secret payload never reaches it (the `signed_by`
  guard, as cpsim03's), and it applies nothing.
- `services/payment_adapters.py` — `CHANNEL_LABELS` + `custodian_api` (refused by "Confirm sent": `MANUAL_CHANNELS`
  excludes it and the vocabulary offers only those); `StatusResult.detail` (default None); the simulator adapter
  registered in `_CONNECTED`; `custodian_key` reads `payment_custodian` first (set only for a simulator-crosswalked
  account); `adapter_for(…, route=None)`: for key `altruist_simulator` only, the simulator adapter when the route holds,
  else MANUAL with `route_reason`. Every other key resolves exactly as before — `altruist` enabled is still
  NotConnected, nothing enabled is still manual.
- `services/payments.py` — `load_account` / `household_accounts` attach `payment_custodian` for a simulator-crosswalked
  source (one batched query); `declared_rails` reads the simulator's capability code for it (every other account reads
  the same code as before); `adapter_of` passes the D4 route; `sheet` always renders the MANUAL sheet (the payment
  sheet stays downloadable beside the custodian API); `mark_sent(adapter=None, via_custodian=False)` — every existing
  caller writes `adapter = 'manual'` exactly as before and may not use `custodian_api`; `detail` adds `custodian` (None
  for every non-simulator payment; no network); `actions_for` adds Send / Refresh only when that panel allows them;
  the channels vocabulary lists the manual channels (the same seven as before).
- `scripts/cpsim03_capabilities.py` — the payment-key skip above.

## 7. The in-process conformance run with the `transfers` group (Task 8, 2026-11-11)

`python3 apps/api/scripts/cpsim_altruist_conformance.py --target simulator` (fixture custodian `cpsimconf-…` with two
accounts — the second the journal's destination — plus a fixture DOMESTIC and INTERNATIONAL bank `cpsimconf-dome-…` /
`cpsimconf-inte-…`, each active with a one-account cash book; all created and torn down by the run): **51 PASS, 0
FAIL** — cpsim02's C01–C20 and cpsim03's O01–O15 unchanged, plus T01 POST a journal (client `post_transfer`) 201 + every
§9.3 field, same-day received → acknowledged → settled · T02 the cash rows moved exactly (−100 / +100) and the feed
serves `transfer_out` / `transfer_in` (`<transfer>-out` / `-in`), types the client maps (sell / buy — D2) · T03 a
DIFFERENT-body repeat → 200, the original unchanged, one transfer · T04 GET one · T05 list by either leg, 400 without
account_id · T06 a JSON-number amount 422 naming the field, nothing created · T07 ACH to a domestic bank with its ACH
routing reference → acknowledged, debited, settle the next business day · T08 a wire with the wrong routing reference →
rejected with the bank's reason, nothing debited · T09 an international wire (BIC + IBAN reference) settles; an ACH to
it 422 · T10 a wrong IBAN reference → rejected · T11 insufficient funds → rejected, nothing moved · T12 DELETE a
settled transfer 409 naming its status, unknown 404 · T13 transfers need an access token · T14 a FULL routing number
where the last four belong → 422 naming the field · T15 SIMULATED header · T16 the transfer webhook body + signature
verified by the receiver's own code. Leftover fixture rows after the run: **0** (`cpsim.counterparties`
`cpsimconf-%`: 0; `cpsim.transfers`: 0; `cpsim.cash_movements`: 0).

`--target sandbox` prints `orders group: skipped — no published Altruist orders contract` and `transfers group: skipped
— no published Altruist money-movement contract` before anything else (and, without `--i-have-sandbox-access`, refuses
to start — unchanged). In the suite core: `transfers_group_skip(environment, transfers)`.

## 8. Operator steps (cpsim04)

1. When ready to demo payments: `python3 apps/api/scripts/cpsim04_capabilities.py --apply --actor-email <a Super Admin>`
   (and cpsim03's loader for trading).
2. Add `altruist_simulator` to `cash.payment_adapters` for the DEMO org only (never `altruist`).
3. Run `apps/api/scripts/verify_cpsim04.py` (written, not run by the sprint).
