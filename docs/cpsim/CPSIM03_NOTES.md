# cpsim03 — discovery notes, decisions as built, reuse inventory

Sprint cpsim03 (2026-11-11), branch `cpsim03-work`. The simulated custodian TRADES: an APPROVED execution instruction is
sent by a PERSON ("Send to custodian") to the simulated Altruist-compatible orders API, the simulator answers with its
scripted outcome, and a PERSON imports the executions through the existing `enter_fill` and reconciles them against the
synced custodian feed. Design: `COUNTERPARTY_SIMULATOR_DESIGN_v1.md`; contract: `ALTRUIST_CONTRACT.md` §8 (new);
cpsim02 notes: `CPSIM02_NOTES.md`; operator guide: `CPSIM_ADMIN.md`.

Paths are relative to `apps/api/` unless they start with `docs/` or `apps/web/`.

## 1. Findings (Task 1)

### 1a. Prerequisite and confirmed facts (HEAD `594b82d`)

- **cpsim02 is merged**: `git log main` shows `594b82d merge cpsim02` (on top of `508eb01`, the fix-1 HELD commit).
- `route_trade_list` — `services/custody/trading.py:352-397`. Held-away → `services.held_away.refuse` (`:376`);
  authority × custody (`:377-383`): not integrated + managed → `ManualTradeAdapter` with "never a custodian API";
  `manual=True` → manual (`:384-388`); no registered adapter → manual (`:389-393`); `refuse_undeclared_shorts`
  (`:229-243`, reads the adapter's `capability_custodian`); `_downgrade_notes` (`:330-349`).
- `trade_custodian_key` / `trade_adapter_for` — `services/custody/registry.py:367-395`; `'ALTRUIST'` → `'altruist'`;
  registered in `services/custody/__init__.py:54-55`.
- `SubmissionResult.sent_externally` — `trading.py:210` (always False; still False after cpsim03 — submit sends
  nothing, D4).
- `AltruistTradeAdapter` — `trading.py:291-327`; `capability_custodian = "altruist"`, `capability_key =
  "equity_trading_api"`; always `ManualTradeAdapter().submit` (`:319`). `ManualTradeAdapter.submit` →
  `services.execution.create_instructions(routed_via=self.custodian_key, routing_reason=why)` (`:263-271`).
- Execution: `services/execution.py` — `create_instructions` `:420`, `submit` / approval human task
  (`HT.register_kind(TASK_KIND …)` `:646`, `_apply_outcome` `:614` → `sod.gate(item_type=EXECUTION_INSTRUCTION)`),
  `decide` `:692`, `_transition` `:724`, `mark_sent` `:742`, `enter_fill` `:754`, `reconcile` `:808`, `cancel` `:849`,
  `_refuse_in_block` `:364`, `detail` `:1101`, `actions_for` `:1165`. Every action: `_event` (events row + audit_log)
  and `publish` (domain event); the row archived on the system axis (`_archive`).
- `services/execution_routers.py` — the FIX router is a `NotConnectedRouter`; blocks route 'manual' only. Unchanged.
- `services/custodian_capabilities.py` — `available()` `:116` ('unknown' → False); `capability()` `:131` (a missing row
  → 'unknown'); `set_capability()` (Super Admin, closes the live version + inserts version_no + 1 + audit_log). Live:
  `altruist.equity_trading_api = 'unknown'`; no `altruist_simulator` row exists.
- cpsim02 (from its notes and code): environment `simulator` (`altruist_oauth.ENVIRONMENTS` `:101`); per-org counterparty
  `simulator_counterparty` (`:169-187`, refuses when `counterparty_sim.allowed` is off or no slug); hosts
  `simulator_hosts` (`:156-166`) = `CPSIM_BASE_URL` + `/cpsim/c/<slug>` for BOTH bases; `check_destination` (`:210`);
  `require_active_connection` (`:746`); `get_decrypted_access_token` (`:639`); `fetch_resource` (`:900`, Bearer +
  Accept, 15 s, `transport=`, ≥ 400 → `AltruistOAuthError`); `_transport_for(environment, transport)` (`:146`) — the
  given transport, else the injected simulator transport for `simulator` only. **The transport threads** through every
  `call_*` via `_call` (`:946-955`) and through the token calls; a DIRECT `fetch_resource` uses only the transport it
  is given (cpsim02 fix 1 finding A). cpsim03's client follows the `_call` shape exactly.
- Crosswalk source `ALTRUIST_SIMULATOR` (`services/altruist_identity.crosswalk_source`); ledger tag
  `altruist_simulator`; demo rule `public.counterparty_sim_may_connect(org, household)` (definer wrapper over
  `cpsim.may_connect`).
- Webhook receiver `services/altruist_webhook.process_event` (`:218-282`): dedup by event_id (`record_event`, ON
  CONFLICT), simulator secret forces environment simulator, account lookup by crosswalk source, then
  `resolve_identity` + `sync_resolved_accounts`. cpsim03 adds the order to-do AFTER that (D9).
- Behaviour row `cpsim.counterparty_behaviour` (`cpsim/registry.behaviour` `:116`); `OUTCOMES = accept, reject, partial,
  delay`; clock `cpsim.counterparty_clock` + `advance_clock` (`cpsim/registry.py:388`, called by the admin router
  `routers/admin_counterparties.py:327`).
- Boundary: `scripts/cpsim_boundary_check.py` B1–B4 (`:8-17`); allowed service imports = `us_business_days`,
  `bitemporal` (`:39`).

### 1b. Labels, CHECKs, where an order id lives, the S30 duty, unattended paths (catalog read 2026-11-11, as postgres)

| Item | Finding |
|---|---|
| `SENT_CHANNEL_LABELS` (`execution.py:80`) | `email, phone, custodian_portal, secure_message, letter, in_person, other`. **No CHECK on `sent_channel`** — only `execution_instructions_sent_chk` (non-blank when sent). cpsim03 adds `custodian_api` in Python only. |
| `RECONCILE_METHOD_LABELS` (`:84`) | `custodian_data, manual`; `execution_instructions_reconciled_chk` (custodian_data needs a reference). Unchanged. |
| `routed_via` | **No CHECK** of its own; `execution_instructions_option_terms_chk` requires `routed_via = 'manual'` for an option leg (so option legs can never take the simulator route — the adapter routes them manual, D2). cpsim03 records `custodian_api`. |
| `execution_instruction_events_action_chk` | 14 actions; **does not admit** a custodian reject / cancel event → extended with `custodian_reject`, `custodian_cancel` (the only events CHECK change). |
| Custodian order id | **No column exists.** `execution_instructions_guard()` freezes every column outside its `lifecycle` list once submitted → a new nullable `custodian_order_id text` is added AND appended to the guard's lifecycle list (it is written at `approved → sent`). |
| Exec-id idempotency | `execution_fills` has no reference column; `execution_fills_guard()` makes fills insert-only. A new nullable `custodian_exec_id text` + a partial UNIQUE index `(instruction_id, custodian_exec_id) WHERE custodian_exec_id IS NOT NULL AND system_to IS NULL` (a re-import can never book an execution twice, even racing). |
| S30 floor | `sod_item_types` `execution_instruction`: maker `propose_trade`, checker `approve_trade`, floor class `trade` (platform `floor` + `policy` rules live). It governs **approval only**; `mark_sent` has **no** sod gate (manage_execution only). So "Send to custodian" is gated exactly like `mark_sent` — manage_execution, a human actor — and the approver MAY be the sender (no existing rule says otherwise; no new floor invented). |
| Approval path | `submit` → `HT.open_task(kind=execution_approval, eligible_permission=approve_execution)` → `decide` (`sod.gate`, app) → `HT.complete_task` (engine refuses the task's maker) → `_apply_outcome` (`sod.gate` again) + `execution_instructions_maker_checker_chk`. Unchanged. |
| Unattended | `mark_sent` / `enter_fill` are called only by `routers/execution.py` (person routes) and `services/blocks.py` (block person actions). `workflow_scheduler_tick.py` calls `execution.expire_tick` only (expire / carry / ask — never sends). No registered assistant / workflow action calls either. `services.workflow_autonomy.VERB_AUTONOMY` has no execution verb. cpsim03 DECLARES `execution.custodian_send` and `execution.custodian_import` as `never` (trades) + `sod.VERB_DUTIES` 'write', and registers NEITHER as an action (the CASH07 pattern) — the verify asserts both. |

### 1c. Security types and the price reader

`portfolio.securities_global.security_type` values live: `equity, fixed_income, fund, index, other, structured_note`.
There is no separate ETF / mutual-fund type: both are `fund`. **The simulator trades `equity` and `fund` only** (D6);
anything else → rejected "unsupported instrument". Platform price reader: `cpsim/book.platform_price` (close,
`org_id IS NULL`, `price_date <= as_of`, live) — reused for the fill price.

### 1d. The UI today

`apps/web/components/execution/InstructionDetail.jsx`: `Summary`, `ActionBar` (submit / mark sent / reconcile /
close / cancel, each from `instructionControls(envelope)` = `can_write` AND the server's `actions`), `TaskPanel`,
`Fills` + `FillForm`, `LetterPanel`, `OrderSheetPanel`, `Timeline`. All calls go through
`/api/execution/[...path]` (the Next.js proxy). Replication Studio's proposal → instructions flow
(`services/rebalance_proposals.execute` → `route_trade_list(upstream=…)`) lands instructions APPROVED (rep5) on the
same instruction page — so "Send to custodian" / "Import fills" go on the instruction page only (ActionBar + a new
`CustodianOrderPanel`), and appear for a proposal's instructions too with no change to the proposal screens.
Admin → Counterparties (`components/counterparties/Counterparties.jsx`) gains an **Orders** tab (read-only) and the
clock tab shows what an advance filled.

## 2. Decisions as built (D1–D11)

| D | As built | Where |
|---|---|---|
| D1 | `ALTRUIST_CONTRACT.md` §8 — every field ASSUMED; paths `POST/GET {api_base}/v2/orders`, `GET /v2/orders/{order_id}`, `DELETE /v2/orders/{order_id}`; statuses, executions, webhooks, idempotency on `client_order_id`. | docs |
| D2 | `services/custodian_execution.simulator_route(conn, org_id, account)` → `(ok, reason, detail)`: (1) the account is crosswalked `ALTRUIST_SIMULATOR` and NOT `ALTRUIST`; (2) `simulator_counterparty` (allowed flag + slug) and an active `simulator` connection; (3) `counterparty_sim_may_connect(org, household)`; (4) authority × custody — already decided by `route_trade_list` before any adapter runs (unchanged); (5) `equity_trading_api` for the simulator capability code (`altruist_simulator`) is an evidenced 'yes'. Plus per-trade route rules: buy / sell only, no option leg, no range price (each → the reason). Any one missing → today's manual behaviour with the reason on the SubmissionResult. | `services/custodian_execution.py`, `services/custody/trading.py` |
| D3 | `scripts/cpsim03_capabilities.py` — `--dry-run` (default) prints the plan; `--apply --actor-email` writes through `custodian_capabilities.set_capability` as a Super Admin. Run once `--dry-run` (§5). | script |
| D4 | `AltruistTradeAdapter.submit`: D2 holds → `ManualTradeAdapter`'s `create_instructions` with `routed_via='custodian_api'` and a routing reason naming the counterparty slug; else exactly today's reasons. Nothing is sent at submit; `sent_externally` stays False. | `trading.py` |
| D5 | `custodian_execution.send_to_custodian` — a person (manage_execution); approved only; `routed_via = custodian_api`; D2 re-checked at send; `POST /v2/orders` via `services/altruist_trading.py`; accepted / working / partially_filled / filled → the EXISTING `execution.mark_sent(channel='custodian_api', custodian_order_id=…)`; rejected → instruction stays approved + a `custodian_reject` event + a to-do for the sender. The generic "Mark sent" refuses `custodian_api` (only Send records it). Never automatic. | `services/custodian_execution.py`, `services/execution.py`, `routers/execution.py` |
| D6 | `cpsim/orders.py` — outcome from the behaviour row at receipt; price = `book.platform_price` on the clock date; accept / limit / reject / partial / delay; no price / unsupported instrument / insufficient position; notional at the fill price (6 dp, fractional); settle = clock + 1 US business day; working orders re-evaluated on every clock advance. | `cpsim/orders.py`, `cpsim/app.py`, `cpsim/registry.advance_clock` |
| D7 | Each execution: a `buy` / `sell` book transaction `sim-x-<exec_id>` and the account's position moved (a snapshot on the clock date). No cash, no corporate actions, no fees. | `cpsim/orders.py` |
| D8 | `custodian_execution.import_fills` — GET the order, book each NEW execution through `execution.enter_fill` (entered_by the importer, note `custodian_api exec <id>`, `custodian_exec_id`), idempotent (column + unique index). Reconcile unchanged (`custodian_data`, reference = the order id, the synced transaction ids). | as named |
| D9 | `cpsim/webhooks.order_event` (`order.updated` / `order.filled`, signed as cpsim02); the receiver adds `custodian_execution.order_event_todo` after the resync. Nothing booked. | `cpsim/webhooks.py`, `services/altruist_webhook.py` |
| D10 | `services/altruist_conformance.py` `orders` group (O01–O12), simulator only; `--target sandbox` skips it. | as named |
| D11 | `execution.cancel` → `custodian_execution.cancel_custodian_order` first when the instruction has a working order: 409 (filled) → refused with the reason; cancelled with executions not all booked → the instruction stays and a `custodian_cancel` event says so; else the existing cancel. | as named |

(§3 reuse inventory and §4 differences follow §7 below; §5 the loader dry-run; §6 the diff review.)

## 5. The capability loader — `--dry-run` (Task 5, 2026-11-11, run once)

`python3 apps/api/scripts/cpsim03_capabilities.py --dry-run` (the default; reads `public.custodian_capabilities`, writes
nothing):

```
custodian code 'altruist_simulator' — evidence 'simulated counterparty — cpsim03' (assumed); mode DRY RUN
  set   fractional_shares (no row) -> yes        set   equity_trading_api (no row) -> yes
  set   model_api (no row) -> no                 set   trade_list_upload (no row) -> no
  set   options_level (no row) -> 0              set   flex_options / index_options (no row) -> no
  set   margin / short_selling / stock_borrow (no row) -> no
  set   in_kind_transfers / custodian_tax_loss_harvesting / lot_level_cost_basis_feed / held_away_aggregation -> no
  set   margin_initial_pct / margin_maintenance_pct (no row) -> unknown
  set   ach / wire / journal / check / payment_status_feed / payment_sheet (no row) -> no   (payments: cpsim04)
22 to set, 0 already as planned
real altruist equity_trading_api = 'unknown' (never changed by this script)
DRY RUN — nothing written
```

NOT applied (operator step, `--apply --actor-email <Super Admin>`). Until it is, D2 condition 7 refuses every account
("altruist_simulator equity_trading_api is 'unknown' …") and the simulator route is never taken. The verify seeds its
OWN fixture custodian code and substitutes it on a fixture adapter subclass (`simulator_capability_custodian`).

## 6. Sandbox / production unchanged — diff review against main (Task 5)

`git diff main -- services/altruist_oauth.py services/altruist_identity.py services/altruist_positions_sync.py
routers/altruist_connection.py` is **empty**: the read client, identity, sync and the connect routes are unchanged by
one byte. The orders client is a NEW module (`services/altruist_trading.py`) that refuses any environment but
`simulator` before a request (`NO_CONTRACT`). Changed shared files, line group by line group:

- `services/custody/trading.py` — `SubmissionResult.simulator` (default None; `as_dict` publishes it only when set:
  every non-Altruist route's dict is byte-identical); `ManualTradeAdapter.submit(routed_via=None)` → the old
  `self.custodian_key`; `AltruistTradeAdapter.submit`: when D2 is NOT met, the instructions, routing reason, adapter
  and notes are exactly the old ones (the decision is added as `simulator: {route: false, condition, reason}`);
  `status` adds the custodian order's status only when an instruction carries a custodian order id (none can for a
  real account: the CHECK ties the id to `custodian_api`).
- `services/execution.py` — `SENT_CHANNEL_LABELS` + `custodian_api` (refused by "Mark sent": `mark_sent` admits it
  only with an order id, which only Send passes); `enter_fill(custodian_exec_id=None)` (NULL for a person's fill — the
  old insert plus one NULL column); `cancel` calls the custodian hook only when `custodian_order_id` is set;
  `detail` adds `custodian` (no network); `actions_for` adds two actions only when that panel allows them.
- `services/custodian_capabilities.py` — one label (`altruist_simulator`); the matrix therefore lists that code
  (every cell 'unknown', `recorded: false`) until the loader is applied.

## 7. The in-process conformance run with the `orders` group (Task 8, 2026-11-11)

`python3 apps/api/scripts/cpsim_altruist_conformance.py --target simulator` (fixture counterparty `cpsimconf-…`, created
and torn down by the run; the fixture security is read at run time — a live equity / fund with a unique CUSIP and a
platform close on or before the clock date): **34 PASS, 0 FAIL** — cpsim02's C01–C20 unchanged, plus O01 POST (client
`post_order`) 201 + every §8.3 field · O02 market accept fills in full, execution fields, decimal strings, settle a later
US business day, ISO UTC · O03 idempotent repeat POST (200, same order, one order) · O04 GET one (client `get_order`) ·
O05 list by account, one response, 400 without account_id · O06 a JSON-float quantity 422 naming the field, nothing
created · O07 unmarketable limit working → DELETE (client `delete_order`) cancelled → second DELETE 409 · O08 DELETE a
filled order 409 naming its status · O09 oversell rejected "insufficient position" · O10 orders need an ACCESS token
(none / the cpsim_ key → 401) · O11 unknown order 404 · O12 the fill moved the book (position + 2, transaction
`sim-x-<exec_id>` served) · O13 SIMULATED header on order responses · O14 order webhook body + signature verified by the
receiver's own code. Leftover fixture rows after the run: **0**.

`--target sandbox` prints `orders group: skipped — no published Altruist orders contract` before anything else (and,
without `--i-have-sandbox-access`, refuses to start — exit 2, unchanged). In the suite core the skip is
`orders_group_skip(environment, orders)`: any real environment → that reason; the admin page's run against a live
counterparty passes no fixture and reports O00 "not run: the orders group places orders and moves the book".

## 3. Reuse inventory

| Piece | Existing construct | Used how |
|---|---|---|
| Routing | `custody.trading.route_trade_list`, `AltruistTradeAdapter`, `ManualTradeAdapter` | unchanged order of decisions (held-away → authority × custody → manual flag → adapter); the Altruist adapter adds the D2 decision and passes `routed_via='custodian_api'` to the SAME `ManualTradeAdapter.submit` → `create_instructions` |
| Drafts, approval | `execution.create_instructions`, `submit`, `decide`, human task `execution_approval`, `services.sod` | unchanged — a custodian-API draft is approved exactly like a manual one |
| Sending | `execution.mark_sent` | the accepted order moves the instruction to `sent` through it (channel `custodian_api` + the order id) — no second transition path |
| Fills | `execution.enter_fill` | every imported execution, `entered_by` the importer; over-quantity / future time / status rules unchanged |
| Reconcile | `execution.reconcile` (`custodian_data`) | unchanged; the UI preselects the synced transactions |
| Cancel | `execution.cancel` | calls the custodian hook first when a custodian order exists; otherwise byte-identical |
| Events, audit, domain events | `execution._event`, `publish` | the two new event actions use them |
| To-dos | `member_todos` (the `expire_due` 'ask' shape) | the reject and fills-available to-dos |
| Capabilities | `custodian_capabilities.capability / available / set_capability` | D2 condition 7; the loader writes only through `set_capability` |
| Client conventions | `altruist_oauth.require_active_connection`, `get_decrypted_access_token`, `resolve_hosts`, `check_destination`, `_transport_for`, `AltruistOAuthError` | the orders client is the read client's `_call` shape, call for call |
| Simulator book | `cpsim.book.platform_price`, `resolve_security`, `generated_id`, `fmt`; `services.bitemporal.archive_live_row` | fill price, security resolution, snapshot ids, decimal strings; orders archived on the system axis |
| Webhooks | `cpsim.webhooks.send_event` / `event_body` / `sign` | extended with an optional `order` (account events byte-identical); `after_orders` |
| Receiver | `altruist_webhook.process_event` | the order to-do added after the existing resync, simulator-signed events only |
| Conformance | `services/altruist_conformance.run` + runner + `_cpsim_fixtures` | the `orders` group added to the same suite and the same fixture run |
| Unattended guard | `workflow_autonomy.VERB_AUTONOMY`, `sod.VERB_DUTIES` | the two verbs declared NEVER and not registered (CASH07's pattern) |

## 4. Differences from the prompt, and findings

1. **No CHECK limits `sent_channel` or `routed_via`** (catalog). `custodian_api` needed no CHECK change there; a new
   CHECK instead ties `custodian_order_id` to `sent_channel = routed_via = 'custodian_api'`.
2. **The instruction guard freezes every non-lifecycle column after submit** — so the new `custodian_order_id` had to
   be added to `execution_instructions_guard()`'s lifecycle list (re-created identically plus that one element).
3. **The events action CHECK** needed `custodian_reject` and `custodian_cancel` (a refused order and a custodian
   cancel that leaves fills to import are both logged human actions with no status change).
4. **S30 governs approval only.** `mark_sent` has no sod gate, so "Send to custodian" is gated exactly like it
   (manage_execution, a human actor). The approver MAY send — no rule says otherwise and none was invented.
5. **ETFs and mutual funds are both `security_type = 'fund'`** — the simulator trades `equity` and `fund`.
6. **The webhook can arrive before Hollisworks has recorded the order.** The simulator sends `order.updated` inside
   the POST that Hollisworks is still waiting on, so the receiver finds the instruction still `approved` with no order
   id and raises no to-do (the send's own response already tells the sender the outcome). The to-do is for events
   AFTER the send (a delayed or limit order filled at the next clock advance). Logged, not changed (FIND).
7. **The sync creates UNLINKED tenant assets** (`create_asset` without `global_security_id`), and reconcile's rule
   requires the transaction's asset to be the instruction's security. Reconciling a custodian-API fill against the
   synced transaction therefore needs the org's tenant asset to be linked to the security master (an import or a
   manual link does that; the verify seeds one). Not changed — sandbox / production sync is out of scope. Logged.
8. **Idempotency on `client_order_id` makes a refused order final**: a second Send re-POSTs the same id and gets the
   same rejected order. To retry, cancel the instruction and route a new one. Documented, deliberate (D1).
9. **The partial outcome's remainder never fills on its own** (re-evaluation covers delay orders and unmarketable
   limits only); a partial order whose limit was not marketable stays working. A person cancels it (D11 then leaves
   the instruction partially filled). `day` orders are not expired by the simulator (the client sends `gtc`).
10. **`execution.expire_due` does not cancel a custodian order** — an expired instruction can leave a working order
    at the simulator (expiry is the only unattended step and must not reach a custodian). Logged.
11. The capability matrix lists `altruist_simulator` (all 'unknown', not recorded) from the label alone until the loader
    is applied.
12. **Isolation at the simulator**: two orgs pointed at the SAME counterparty would share its book (a counterparty is
    a platform object). The verify gives each org its own counterparty; Hollisworks-side isolation (RLS, org-scoped
    loads, `client_order_id` = the org's own instruction id) holds either way.

## 8. Operator steps (cpsim03)

1. When ready to demo trading: `python3 apps/api/scripts/cpsim03_capabilities.py --apply --actor-email <a Super
   Admin>` (writes the `altruist_simulator` matrix through the capability write path; audited). Nothing else changes.
2. Run `apps/api/scripts/verify_cpsim03.py` (written, not run by the sprint).

Schema `docs/cpsim03_part1.sql` APPLIED 2026-11-11 (`scripts/_apply_cpsim03.py --all`: 20/20 read back).

## 9. Fix 1 findings (cpsim03.fix1, 2026-11-11)

Source: `sprint_prompts/logs/cpsim03_run.out` — TOTAL 170 PASS, 22 FAIL (19 own + verify_cpsim02 1 FAIL + verify_rep2
1 FAIL; verify_authz1 PASSED). Teardown was CLEAN in that run (zero fixture rows; every table at its pre-test count;
every real fingerprint unchanged), so no leftover rows needed removing. Each FAIL decided CODE or VERIFY:

| FAIL (log line) | Verdict | Evidence | Fix |
|---|---|---|---|
| G 214 governance raised `text = uuid` | VERIFY | `sod_decisions.item_id` is `text` (schema snapshot); verify_cpsim03.py:1253 compared `$2::uuid`. The only `item_id` site in the script (grep). | Compare `$2::text` with `str(iid)`. The governance section now runs to its end, the checker's approval included. |
| 262/263 accept + mark_sent; 288 book tx; 290/291 sync; 294–296 import; 299 view; 300/301 reconcile | VERIFY (cascade of G) | Every detail names the instruction still `awaiting approval` / no custodian order: outcomes reused governance's `routed` instruction, which G left pending. | Outcomes routes, submits and approves its OWN instruction (`approved(…, "accept")`); `ONLY_NEEDS` no longer makes outcomes/cancel/clock/book/fills/isolation depend on routing + governance. |
| I 264 repeat client_order_id → 201 | VERIFY (cascade of G) | `cpsim/orders.submit` looks up the live order by (counterparty, client_order_id) FIRST and returns it unchanged with `created False` (→ 200) whatever the body says; `orders_client_order_live_uq` is UNIQUE (counterparty_id, client_order_id) WHERE system_to IS NULL. The 201 was the FIRST order for that id: the accept Send had been refused (409) before any order existed. | None to the code. The rule as stated in contract §8 O-1, the conformance check and the verify: a repeat with a DIFFERENT body → 200, the ORIGINAL order unchanged, one order. Conformance O03 already sends the different-body repeat (quantity 3 on a 2-share order) and asserts 200, same id, filled_quantity still 2, one order. |
| W 304 Send → ReadTimeout; 306 no order.updated; 307 no to-do; 308 | **CODE** | Reproduced with a one-off probe (seed + webhooks only). Webhooks OFF: 200 in 7.4 s. Webhooks ON: 409 ReadTimeout in 17.2 s, plus "Resetting connection with an active transaction". `pg_stat_activity` was sampled every second: **no lock wait at any sample** (`blocked_by` empty, no `Lock` wait events), so not a deadlock. The send's connection (pid A) sat `idle in transaction` throughout. The receiver's connection (pid B) was busy with the full account resync (savepoints, set_config, org_settings…), with the simulator's read API (pid C) serving it. The simulator delivered `order.updated` INLINE before answering the order, and the receiver's resync alone outran the client's 15 s read timeout. That refutes the deadlock half of the hypothesis and confirms the rest: the order response waited on a webhook while the API held a transaction open. In production that is the same API calling itself synchronously through a resync. | `cpsim/webhooks.schedule_orders`: order events start as a detached task AFTER the order's transaction commits, and the response returns at once. Same events, secret, signature, retries (1 + 2) and request_log rows. `drain()` awaits in-flight deliveries (verify; shutdown). `send_to_custodian` reads the session (`altruist_trading.order_session`) and RELEASES its connection before the HTTP call (`submit_with_session`); `mark_sent` re-checks every gate as before. The admin clock-advance still delivers inline: it is a Super Admin action that holds no transaction, and its response lists what was delivered. |
| — (found while proving W) | VERIFY | Run alone, the webhooks section's `PUT /behaviour {delay, webhooks on}` got **400** (`counterparty_behaviour_delay_chk`: delay needs delay_seconds). In the full run it silently inherited `delay_seconds = 0` from outcomes. The helper ignored the status code. | The section sets `delay_seconds = 0` itself. `behaviour()` now FAILs on any non-200. Send is timed (< half the 15 s timeout), and `order.updated` is proven delivered AFTER the send (drained). |
| R 309 real-secret claims simulator | VERIFY expectation correct, no change (cascade of W) | The receiver answered exactly cpsim02 D6 (CPSIM02_NOTES §2 D6; verify_cpsim02:1904): 200 `refused_environment`, never dispatched. The check failed only on `n_todo == 1`: W left zero to-dos. | No change to the receiver or the expectation; the detail now prints the to-do count. |
| X1 312 B's order counts "2 0" | VERIFY | `cpsim.orders` is SYSTEM-axis: accepted → filled archives a copy (`_set_status` → `archive_live_row`), so one order is two rows. The count had no `system_to IS NULL`. **No cross-org leak**: org A's counterparty held 0 rows for B's id. | Count LIVE rows (= 1) AND distinct order_id over every version (= 1); org A's counterparty: no row of any version. |
| X2 315 "B asks for A's order — the order exists" | VERIFY (cascade of G) | `S["orders"]["accept"]` was empty: the accept Send was refused. | Fixed by outcomes owning its instruction. |
| P verify_cpsim02 exact grants: missing executions UPDATE/DELETE, orders DELETE | VERIFY | Catalog = docs/cpsim03_part1.sql:141-142 (orders S/I/U; executions S/I), deliberately stricter. cpsim02's exact-set check knew only the upload-log exception. | `_cpsim_fixtures.CPSIM_APP_WITHHELD` / `cpsim_app_privileges()` is now the ONE list. verify_cpsim02 builds its EXACT expected set from it, and verify_cpsim03's `NEW_CPSIM_PRIVS` derives from it (asserted equal to S/I/U and S/I). No DELETE grant was added: teardown of both tables stays on the operator (owner) DSN with `cpsim.allow_teardown = 'fixture'`, as the upload log does (the log's teardown removed 7 executions that way). |
| Q verify_rep2 "every seeded capability carries evidence" | VERIFY (in rep2; not cpsim03-caused) | Read-only catalog query: current Altruist rows lack ONLY key `payment_sheet`, added to `CAPABILITY_KEYS` by cash07 (c283a4a) with no Altruist row. `matrix()` serves the gap as a synthesized cell (`recorded: false`, `as_of None`), which rep2's live scan counted as seeded. (a) fixture rows `zz_%`: 0; (b) `altruist_simulator` rows, any version: **0** (loader not applied); (c) live-state scan → VERIFY. | rep2 checks evidence on RECORDED cells (non-empty set). Unrecorded cells must be `unknown`, not available and version-less, and each is listed as a [FIND]. No capability row was written. |
