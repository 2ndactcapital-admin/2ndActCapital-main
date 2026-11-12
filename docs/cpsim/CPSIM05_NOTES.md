# cpsim05 — a SIMULATED FIX counterparty: discovery, decisions as built, message catalogue, reuse inventory

Sprint cpsim05 (2026-11-11), branch `cpsim05-work`. When a Hollisworks rebalance proposal is approved (the proposal's own
maker-checker — Joe, 2026-11-11: "we don't need a separate approval to execute the FIX transaction — fully automatic for
demo purposes"), the trades of accounts at the SIMULATED custodian are sent automatically as FIX 4.4 tag=value messages
carried over HTTP. The counterparty answers each with ExecutionReports (filled at the platform's last close on its
clock). Hollisworks books the fills through the existing `enter_fill`, syncs the account, reconciles each fill, and
shows realized / unrealized P/L. Deliberately simple: one message in, the reply message(s) out. No FIX sessions, no
heartbeats, no sequence-number recovery. **Simulated counterparty only.** Real custodians stay manual (a person sends,
a person imports).

Paths are relative to `apps/api/` unless they start with `docs/` or `apps/web/`.

## 1. Findings (Task 1, HEAD `e62f871`)

### 1a. Prerequisite

- **altread1 is merged**: `git log main` → `e62f871 merge altread1`.
- Fast regression mode is present: `scripts/_verify_total.py:177-220`. `VERIFY_REGRESSIONS` unset / `fast` → each
  nested verify runs `--self-checks-only`; `full` → the old behaviour.

### 1b. The "confirm" items

| Item | Finding (file:line) |
|---|---|
| The choke point | `services/rebalance_proposals.execute` `:1272`. It builds a `TradeList` (`source "proposal"`, `source_ref`, `executor_for`, `execute_by = today + execute_within`, `if_not_filled "ask"`) and calls `route_trade_list(..., upstream=…, manual=…)` at `:1340`. Callers: `_apply_outcome` `:1172` (inside the approval decision — the approver's request), `POST /proposals/{id}/execute` (`routers/account_models.py:466`, a retry), and `services/long_short.py:1375` (a long/short proposal's route choice). The other `route_trade_list` caller is `routers/execution.py:210` (a person's ad-hoc instruction — never automatic). |
| Proposal kinds at the choke point | `PROPOSAL_KIND_LABELS` (`rebalance_proposals.py:152`): `model`, `one_off`, `options`, `cash`. Triggers (`:118`): `join`, `model_change`, `drift`, `cash_flow`, `manual`, `harvest`, `cash_waterfall`. **Harvest** is a SYSTEM proposal (`services/harvest.py:499`, `maker_kind="system"`). **Tilts / completion** (rep10) are not a kind: they adjust a model proposal's target (`_rep10_adjust` `:390`), so a tilted model proposal reaches the choke point like any model proposal. **Cash** (`services/cash_proposals.write_proposal` `:587`): trigger `manual` = a person's (human maker); `cash_waterfall` = system. **One-off** proposals are advise (member / other adviser executes). `route_trade_list` sends advise to manual before any adapter (`custody/trading.py`: `e["authority"] != "managed"`), so they never reach the simulator. |
| Options (rep8) | `execute` hands `proposal_kind == 'options'` to `services/options_proposals.execute` (`:229`). That function calls `create_instructions(routed_via="manual")` directly (`:266-267`) and never reaches an adapter. The CHECK `execution_instructions_option_terms_chk` makes option terms manual-only. |
| Upstream → instruction approval | `services/execution.create_instructions` `:426` with `upstream` → `_verify_upstream` `:397` re-reads the proposal: approved, `maker_kind 'user'`, maker ≠ checker (`sod.gate`), approval task completed 'approve' by the checker. Then **every instruction is created `approved`** (`:455-458`): maker = the proposal's maker, `approved_by` = its checker, `approved_at` = `decided_at`. The `create` event's detail carries `upstream {proposal_id, task_id, maker_id, checker_id}`. **So no second approval exists for a human-maker proposal today**. cpsim05 adds an explicit approval-of-record event (`approve_upstream`, D4) so the automatic path never relies on an implied approval. A **system** proposal: drafts by the reviewer, `X.submit` each (`rebalance_proposals.py:1347-1351`) → rep2's own human task `execution_approval` → `execution._apply_outcome` `:620` (approve → `approved`, `approved_by` = the second person). |
| Long / short (rep13) | A SELL SHORT / BUY TO COVER reaching the Altruist adapter is refused at `TradeAdapter.refuse_undeclared_shorts` (`custody/trading.py` — capability `short_selling` of `capability_custodian`; it raises `ShortSaleNotDeclared` → 409). The person then chooses the manual route (`long_short.py:1375`, `manual=route == "manual"`). `custodian_execution.trade_refusal` also keeps any non buy/sell side manual. |
| Cash fixed-income lines (cash04) | `rebalance_proposals._trade_line` `:1248`: a par line is `price_kind limit` per 100 of par. Its security type is `fixed_income`, so the simulator refuses it ("unsupported instrument"; `cpsim/orders.py SUPPORTED_TYPES = equity, fund`). A fund line of the same proposal is an ordinary buy / sell. |
| Lots and realized gains today | Fills never write `portfolio.tax_lots` (rep2 / rep9). The tax engine's realized figure **per fill** is the loss bank's `fill` source (`services/loss_bank.ingest` `:173-205`): for each lot the fill NAMES, q × (fill price − lot adjusted cost / lot quantity), with the term from acquired → fill date. It is PROVISIONAL until the custodian's closed lot arrives (`lot_close` supersedes it). A fill that names no lots waits (`fill_without_lots`). Year-to-date realized: `rebalance_proposals.realized_ytd` `:435` (closed lots). Lots the engine reads: `services/custodian_lots.lots_for_engine` `:236` (Hollisworks lots, or altread1's custodian lots when the approved lot source is `custodian`). |
| Unrealized | `services/harvest.evaluate_lot` `:147` (pure, exact): `(price − unit_cost) × quantity` per open lot. |
| Where P/L is shown today | The proposal page (`apps/web/components/proposals/ProposalDetail.jsx`) shows the engine's ESTIMATES only (`est_st_gain` / `est_lt_gain` per line). The tax overview (`services/tax_overview.account_panel`) shows harvestable losses. **Nothing shows a proposal's realized result after its fills.** cpsim05 adds that panel (D7). |
| Price the counterparty fills at | `cpsim/book.platform_price` `:192`: `portfolio.securities_global_prices`, `org_id IS NULL`, `price_type 'close'`, `price_date <= clock`, live, newest first. Hollisworks' unrealized P/L reads the SAME predicate (D7), so the numbers agree exactly. |
| Post-fill sync | `routers/altruist_connection._run_sync` `:170` = `require_active_connection` → `resolve_identity` → `sync_resolved_accounts` (`services/altruist_positions_sync.py:608`). It is a router helper, so the automatic step calls the same three service functions directly (D4). |
| Reconcile | `execution.reconcile` `:826`: `filled` only; `custodian_data` needs a reference; every transaction id must be this account's, in this security (the transaction's asset must be LINKED to the security master — cpsim03 finding 7: the sync creates unlinked assets). |

### 1c. FIX session fields, executing account, block route

- **FIX session (cpsim01)**: `cpsim.counterparty_fix_sessions` (`docs/cpsim01_part1.sql:163-187`, VALID axis, one live
  row per counterparty): `begin_string` (FIX.4.2 / FIX.4.4 / FIXT.1.1), `sender_comp_id`, `target_comp_id`, `host`,
  `port`, `heartbt_int`, `reset_on_logon`, `username`, `password_credential_id`. A new counterparty gets
  `FIX.4.4 / HOLLISWORKS / <SLUG_UPPER> / fix.<slug>.cpsim.invalid / 9878` (`cpsim/registry.py:220-228`). The
  orientation is Hollisworks': **sender = Hollisworks, target = the counterparty**.
  **[FIND] The `altruist` institution type has NO `fix` field group** (`docs/cpsim01_part1.sql:98-100`: `connection,
  behaviour, clock`). So the simulated custodian has no stored FIX session today, and `set_fix` refuses it ("has no
  FIX session"). cpsim05 adds `fix` to that type's groups (a cpsim reference-data change, D1). An existing
  Altruist-compatible counterparty without a session row gets one only when a Super Admin saves the FIX tab. Until
  then its FIX endpoint answers a Reject "no FIX session configured".
- **Executing account (rep9)**: `public.execution_accounts.account_reference_masked` (`docs/rep9_part1.sql:62`,
  `^\*{2,}[0-9A-Za-z]{0,4}$`). It is used ONLY by broker / trader blocks; a custodian-desk block has none
  (`services/blocks.py:880`). **[FIND]** A simulated account is integrated + managed, so its executor is
  `custodian_desk` (`rebalance_proposals.executor_for` `:1430`). A simulated block therefore has no executing account.
  Its FIX Account (1) is the executing account's masked reference when the block has one, else `CUSTODIAN-DESK` (D6).
- **`trade_blocks_route_chk`**: `CHECK (route = 'manual')` (`docs/rep9_part1.sql:230`). `services/execution_routers`:
  `ROUTES = ("manual", "fix")`; `fix` is a `NotConnectedRouter`; `route_refusal` refuses anything not connected.
- **Labels / CHECKs on the instruction** (cpsim03, `docs/cpsim03_part1.sql:147-213`): no CHECK on `sent_channel` /
  `routed_via`. `execution_instructions_custodian_order_chk` ties a custodian order id to `sent_channel = routed_via =
  'custodian_api'`. `execution_instruction_events_action_chk` has 16 actions. `execution_fills.custodian_exec_id` has a
  partial UNIQUE index per instruction.
- **Unattended declarations**: `services/workflow_autonomy.py:103-106` declares `execution.custodian_send` /
  `execution.custodian_import` NEVER (trades); `services/sod.py:700-701` gives both the duty `write`. Neither is a
  registered action.

## 2. Decisions as built (D1–D8)

| D | As built | Where |
|---|---|---|
| D1 | `POST /cpsim/c/<slug>/fix`: access-token auth exactly as the orders API. The body is ONE FIX 4.4 tag=value message; SOH or `\|` delimiter (the reply uses the request's). 8=FIX.4.4 first, 9 BodyLength second and verified, 10 CheckSum last and verified (3 digits). 49 / 56 must equal the counterparty's live FIX session (Hollisworks orientation). 34 is recorded, not enforced. A missing session → Reject. Malformed → **35=3 Reject** with 58 Text (and 373 SessionRejectReason / 371 RefTagID where one tag is at fault), HTTP 200, never a 500. Every message in and out → `cpsim.fix_messages` (append-only, cpsim_app SELECT / INSERT only, in `_cpsim_fixtures.CPSIM_APP_WITHHELD`); a Password (554) / RawData (96) value is redacted before it is stored. Admin → Counterparties → FIX tab (session fields + message log). The `altruist` institution type gains the `fix` field group. | `cpsim/fix.py`, `cpsim/app.py`, `docs/cpsim05_part1.sql`, `routers/admin_counterparties.py` |
| D2 | NewOrderSingle 35=D → the body cpsim03's `orders.submit` takes (ClOrdID → client_order_id, 1 → account, 55 / 48+22=1 → symbol / cusip, 54 1/2 → buy/sell, 54=5 → Reject "sell short: no shorting declared", 38 / 152, 40 1/2, 44, 59=0 day). **The outcomes are cpsim03 D6's own code**, with one addition: an order placed over FIX (`cpsim.orders.protocol = 'fix'`) has its partial remainder re-evaluated on clock advance, as D2 states. The REST rule (a partial's remainder stays working) is unchanged for REST orders. The reply = one ExecutionReport 35=8 **per execution** (150=F Trade: 17 ExecID, 31 LastPx, 32 LastQty) plus a final status report (150 = 0 New / 8 Rejected / 4 Canceled / I Order Status; 39 OrdStatus; 14 CumQty, 151 LeavesQty, 6 AvgPx; 58 the reject reason). Idempotent on ClOrdID: a repeat answers the same order's current reports, never a second order. OrderCancelRequest 35=F → 35=8 150=4 / 39=4, or **35=9 OrderCancelReject** (102 CxlRejReason, 58). **OrderStatusRequest 35=H** (FIX 4.4 standard; added — the collect step after a clock advance needs it) → every trade report + the status report. AllocationInstruction 35=J (blocks) → **35=P AllocationInstructionAck** (87 AllocStatus 0 accepted / 2 rejected). Book moves: exactly cpsim03 D7 (positions + transactions) and cpsim04 (the cash leg on settle). | `cpsim/fix.py`, `cpsim/orders.py` |
| D3 | `services/fix_sim_client.py`: build / parse / checksum / readable, the session (hosts + access token, per call, `order_session`'s refusals), transport threaded on every call through `_transport_for`, connection released before HTTP. `services/execution_routers.py`: `SimulatedFixRouter` (`fix_sim`, connected True, label "FIX — simulated counterparty"); the real `fix` stays NotConnected. `AltruistTradeAdapter`: when cpsim03's D2 route holds, the instructions are routed `fix_sim` (the class attribute `simulator_protocol = "fix"`). cpsim03's REST route stays callable: a subclass sets `simulator_protocol = "rest"` (verify_cpsim03's fixture adapter does). | as named |
| D4 | `services/fix_sim_auto.py`. At the end of `rebalance_proposals.execute` (after its commits), a human-maker proposal routed `fix_sim` schedules the automatic step as a detached task. The request returns at once. The step, per instruction: **(1)** the approval of record — event `approve_upstream` ("approved via proposal maker-checker (simulated counterparty)": proposal id, maker, checker, task id; actor = the checker); **(2)** NewOrderSingle; **(3)** accepted → `mark_sent(channel 'fix_sim', custodian_order_id = 37 OrderID, actor = the checker, note "automatic — simulated counterparty")`; rejected → a `custodian_reject` event + a to-do for the checker (the instruction stays approved); **(4)** each 150=F report → `enter_fill` (actor the checker, note `fix_sim exec <ExecID>`, `custodian_exec_id` = ExecID, the instruction's named lots relieved in order). Then, after the batch: **(5)** the post-fill sync (simulator environment); **(6)** `reconcile(custodian_data, reference = OrderID, the synced `sim-x-<ExecID>` transactions)`; **(7)** `loss_bank.ingest` (so the tax engine's own realized figures exist for D7). Every step writes an instruction event + audit row and carries `on_behalf: "automatic"` and the FIX messages. A failure → an `automatic_failed` event + a to-do for the checker; the instruction stays in its true state. A SYSTEM proposal keeps rep2's second-person approval: `execution._apply_outcome` (approve) of a proposal-sourced `fix_sim` instruction schedules steps 2–7 (actor = that approver, no `approve_upstream`). Fills that arrive after a clock advance (delay, unmarketable limit, a partial's rest): `routers/admin_counterparties.advance_clock` hands the orders it filled to `fix_sim_auto.collect_after_advance`, which sends 35=H and runs steps 4–7. `drain()` awaits in-flight steps (verify; shutdown). | `services/fix_sim_auto.py`, `services/rebalance_proposals.py`, `services/execution.py`, `routers/admin_counterparties.py` |
| D5 | The guard, in the step itself: route `fix_sim` AND cpsim03 D2 (`custodian_execution.simulator_route`, all 7 conditions, re-read) AND the router registry's `fix_sim` connected AND source `proposal` AND an approval of record. Anything else → refused with the reason; the instruction is left for a person (to-do). Database backstop (`docs/cpsim05_part1.sql`): (a) CHECK `execution_instructions_fix_sim_chk` — `sent_channel = 'fix_sim'` only when `routed_via = 'fix_sim'`; (b) BEFORE INSERT trigger `execution_instruction_events_fix_sim_guard` — an event with `detail.channel = 'fix_sim'`, `detail.on_behalf = 'automatic'`, or action `approve_upstream` / `automatic_failed` is refused unless the instruction's live `routed_via = 'fix_sim'`. INSERT-only, so teardown deletes are unaffected (no teardown path needed). Declared `execution.fix_sim_auto` NEVER for unattended runs (workflow_autonomy) and not registered as an action. The scan proves the scheduling functions have exactly two call sites (the choke point; rep2 approval of a proposal-sourced instruction) and collect exactly one (the admin clock advance). | as named |
| D6 | `trade_blocks_route_chk` widened to `route IN ('manual', 'fix_sim')`; `'fix'` is still refused by `route_refusal`; manual is still the default and always offered; `RT.any_connected()` counts REAL routers only (the simulated one never makes a manual block need a reason). `build_block(route='fix_sim')` is refused (409, every member's reason) unless EVERY member's instruction is routed `fix_sim` and passes D2 (`fix_sim_blocks.member_refusal`). Approval is unchanged (the block's own maker-checker) and IS the approval of record (Joe's rule): `blocks._apply_block_outcome` (approve) of a fix_sim block schedules `fix_sim_blocks.run` after the request, as the approver. Steps: NewOrderSingle for the block (591=0 PreallocMethod marks a block order, ASSUMED convention; Account = the executing account's masked reference or `CUSTODIAN-DESK`) → `blocks.mark_sent(channel 'fix_sim', via_fix=True)` (the channel is refused by hand) → each 150=F → `blocks.enter_fill` (venue `SIMULATED FIX`, note `fix_sim exec <ExecID>` — the idempotency key; the average price is recomputed by Hollisworks, AvgPx never read) → once FILLED `blocks.allocate` (the EXISTING allocation path) → AllocationInstruction 35=J (6 AvgPx = the Hollisworks average, each allocation's crosswalked account + final quantity) → 35=P accepted, naming each account's booked transaction (79 / 467 IndividualAllocID = `sim-a-…`) → each allocation `blocks.set_settlement(settled, custodian_data, reference = AllocID)` → post-fill sync → each member `reconcile(custodian_data, reference = the 467 id, its synced transaction)` → `loss_bank.ingest`. A partial waits for a clock advance (`collect_after_advance` → `fix_sim_blocks.collect_blocks`). A person may re-run it: `POST /execution/blocks/{id}/fix-sim` (manage_blocks) — idempotent (ClOrdID, ExecIDs, AllocID). Failure → audit row `block.fix_sim_failed` + a to-do for the approver; the block keeps its true state. | `services/fix_sim_blocks.py`, `services/blocks.py`, `routers/blocks.py`, `services/execution_routers.py` |
| D7 | `services/fix_sim_pl.proposal_pl(conn, org_id, proposal)`: per instruction — quantity, average fill price (fills), proceeds / cost, realized ST / LT = the loss bank's live entries for those fills (source `fill`, or the `lot_close` that superseded one); the account's unrealized P/L = `harvest.evaluate_lot` over `custodian_lots.lots_for_engine` at the last platform close. Totals; every number labelled SIMULATED. Served in the proposal detail (`detail['fix_sim_pl']`, only when an instruction is routed `fix_sim`). | as named, `ProposalDetail.jsx` |
| D8 | Covered: every kind that reaches the choke point and routes to the simulator (model — every trigger incl. tilts; harvest (system → rep2 approval → automatic); cash fund lines). Refused, staying manual with the reason: option legs (never routed — rep8), fixed-income par lines (simulator "unsupported instrument" → `custodian_reject` + to-do), shorts (refused at the adapter — no shorting declared). | as named |

## 3. FIX message catalogue (every tag used)

Standard = FIX 4.4 as published by FIX Trading Community. ASSUMED = a convention of this simulator. No Altruist FIX
contract has been seen.

| Tag | Name | Used in | Status |
|---|---|---|---|
| 8 | BeginString | all; `FIX.4.4` | standard |
| 9 | BodyLength | all; bytes from after 9's SOH to before 10= | standard |
| 35 | MsgType | D, F, H, J → 8, 9, P, 3 | standard |
| 49 / 56 | SenderCompID / TargetCompID | all; checked against the stored session | standard |
| 34 | MsgSeqNum | all; recorded, not enforced | standard (enforcement omitted) |
| 52 | SendingTime | all; `YYYYMMDD-HH:MM:SS.sss` UTC | standard |
| 10 | CheckSum | all; sum of bytes mod 256, 3 digits | standard |
| 11 | ClOrdID | D, F, H, 8, 9; = the Hollisworks instruction id (or block id) | standard |
| 41 | OrigClOrdID | F, 9 | standard |
| 1 | Account | D (the crosswalked external account id; a block's executing reference); 8 | standard |
| 55 / 48 / 22 | Symbol / SecurityID / SecurityIDSource (1 = CUSIP) | D, 8 | standard |
| 54 | Side (1 buy, 2 sell, 5 sell short → refused) | D, F, 8, J | standard |
| 38 / 152 | OrderQty / CashOrderQty | D, F, 8 | standard |
| 40 | OrdType (1 market, 2 limit) | D, 8 | standard |
| 44 | Price (limit) | D | standard |
| 59 | TimeInForce (0 day) | D | standard (the simulator does not expire day orders — cpsim03 finding 9) |
| 60 | TransactTime | D, F, 8, J | standard |
| 591 | PreallocMethod | D (present → a block order; allocation to follow) | ASSUMED convention |
| 37 | OrderID | 8, 9, F, H | standard |
| 17 | ExecID | 8 | standard |
| 150 | ExecType (0, 4, 8, F, I) | 8 | standard |
| 39 | OrdStatus (0, 1, 2, 4, 8) | 8, 9 | standard |
| 31 / 32 | LastPx / LastQty | 8 (150=F) | standard |
| 14 / 151 / 6 | CumQty / LeavesQty / AvgPx | 8 | standard (Hollisworks never takes AvgPx as its average) |
| 64 | SettlDate | 8 (150=F), J | standard |
| 58 | Text | 3, 8 (reject reason), 9 | standard |
| 102 | CxlRejReason (0 too late, 1 unknown order) | 9 | standard |
| 434 | CxlRejResponseTo (1 = cancel request) | 9 | standard |
| 45 / 371 / 373 | RefSeqNum / RefTagID / SessionRejectReason | 3 | standard |
| 70 | AllocID | J, P | standard |
| 71 | AllocTransType (0 new) | J | standard |
| 626 | AllocType (1 calculated) | J | standard |
| 75 | TradeDate | J | standard |
| 53 | Quantity | J | standard |
| 78 / 79 / 80 | NoAllocs / AllocAccount / AllocQty | J | standard |
| 87 | AllocStatus (0 accepted, 2 rejected) | P | standard |
| 88 | AllocRejCode (0 unknown account, 7 other) | P | standard |
| 554 / 96 | Password / RawData | never sent; redacted if received | standard |

## 4. Reuse inventory

| Piece | Existing construct | Used how |
|---|---|---|
| Routing | `route_trade_list`, `AltruistTradeAdapter`, `ManualTradeAdapter`, `custodian_execution.simulator_route` / `trade_refusal` | unchanged order of decisions; the adapter passes `routed_via='fix_sim'` to the same `create_instructions` |
| Approval | `_verify_upstream` (human maker), rep2 `submit` / `decide` / `_apply_outcome` (system) | unchanged; the approval of record is an extra event, never a new approval path |
| Send | `execution.mark_sent` | channel `fix_sim` with the FIX OrderID in `custodian_order_id` |
| Fills | `execution.enter_fill` (+ `custodian_exec_id`, the unique index) | every 150=F report |
| Reconcile | `execution.reconcile(custodian_data)` | reference = the OrderID; the synced `sim-x-<ExecID>` transactions |
| Sync | `resolve_identity` + `sync_resolved_accounts` (the `_run_sync` sequence) | after each batch |
| Gains | `loss_bank.ingest` (fill source), `harvest.evaluate_lot`, `custodian_lots.lots_for_engine` | D7, no new P/L engine |
| Blocks | `build_block`, block approval, `blocks.mark_sent`, `blocks.enter_fill`, `blocks.allocate`, `blocks.set_settlement` | D6; route `fix_sim` admitted |
| Router registry | `execution_routers._REGISTRY`, `route_refusal` | `fix_sim` connected; `fix` NotConnected |
| Simulator | `cpsim.orders.submit / cancel / reevaluate / get`, `_book_move`, `ledger.settle_fills`, `book.platform_price` | the FIX endpoint translates and calls them; allocations reuse `_book_move` |
| To-dos | `custodian_execution._todo` (member_todos) | failures, rejects, unmatched reconciles |
| After-response | `cpsim.webhooks.schedule_* / drain` pattern | `fix_sim_auto.schedule / drain` |
| Unattended guard | `workflow_autonomy.VERB_AUTONOMY`, `sod.VERB_DUTIES` | `execution.fix_sim_auto` declared NEVER, not registered |

## 5. Differences from the prompt, and findings

1. **[FIND]** The simulated (Altruist-compatible) custodian had no FIX session fields: the `altruist` institution type
   lacks the `fix` group (§1c). Added.
2. **[FIND]** A human-maker proposal's instructions already START approved (`create_instructions` with `upstream`). There
   was no rep2 approval to "carry forward". The approval of record is therefore an explicit event (`approve_upstream`)
   written before the first automatic step. A system proposal (harvest, drift, model_change, cash_waterfall) keeps
   rep2's second-person approval.
3. **[FIND]** Simulated accounts execute at the custodian desk, so a simulated block has no rep9 executing account (§1c).
4. **D2's partial rule differs from cpsim03 D6** ("then the rest on advance" vs "the remainder stays working"). FIX
   orders take D2's rule and REST orders keep cpsim03's (`cpsim.orders.protocol`).
5. **OrderStatusRequest (35=H) was added.** A request-response transport cannot push unsolicited ExecutionReports, so
   after a clock advance Hollisworks asks for them.
6. `routers/altruist_connection._run_sync` is a router helper. The automatic step calls the same service functions in
   the same order.
7. **[FIND] The cpsim boundary check FAILED on main** — 14 B1 / B2 hits, all in `scripts/verify_altread1.py`. That
   verify drives the simulator in-process exactly as `verify_cpsim0N` does, but ALTREAD1 never added it to
   `B1_ALLOWED_RX`. Added (cpsim05 Task 3); the scan is now 0 violations.
8. **Earlier verifies pinned "a FIX session only for the schwab type"** (`verify_cpsim01.py:924`, `:945-947`,
   `:1408`). Re-pinned to the types whose field groups carry `fix` (schwab + altruist); the "no FIX session → 400"
   example is now a bank type.
9. **Two codecs, deliberately.** The boundary (B3) forbids the simulator importing a services codec, and services
   never import `cpsim`. So `cpsim/fix.py` and `services/fix_sim_client.py` each build / parse / checksum on their own;
   the conformance `fix` group proves the two agree.
10. **[FIND] A person's ad-hoc instruction on a simulated account is also routed `fix_sim`** (the adapter decides by
    account, not by source). It is NEVER automatic (D5: proposals only): after rep2's approval a person sends it with
    the existing "Send to custodian" (`custodian_execution.send_to_custodian` → `fix_sim_auto.run_person`, mode
    `person`) and collects with "Import fills". These are the same person actions and the same NEVER declarations as
    cpsim03. This is also what makes a fix_sim BLOCK possible: a proposal's instructions are sent at once, so they
    are never left approved to join a block.
11. **The automatic step starts after the response is SENT, not merely after the commit.** `services.fix_sim_auto
    .AfterResponseGate` is main.py's outermost middleware. It sets an Event per request once the response finishes;
    a step scheduled inside that request copies the context and waits for the Event. `fix_sim_auto.timings` records
    both times (the verify's timing proof). Outside a request (a script, the verify calling services directly)
    there is no gate and the step starts at once.
12. **Fills that arrive after a clock advance** are collected by `routers/admin_counterparties.advance_clock` →
    `fix_sim_auto.collect_after_advance` (OrderStatusRequest 35=H; never a new order). The admin action itself is a
    Super Admin's; the step books as the instruction's `sent_by` (the checker who sent it).
13. **Unrealized P/L** reads the lots the tax engine reads as of the last sync. Fills never write `portfolio.tax_lots`
    (rep2 / rep9), so a just-filled BUY is not in Hollisworks lots until an import / custodian lots bring it. That is
    documented, not invented: D7 makes no new P/L engine.
14. **The conformance `fix` group (F01–F20) runs in-process: 75 PASS, 0 FAIL** (2026-11-11, with cpsim02–04's groups;
    0 leftover rows). It found one real defect on its first run: a Reject's Text contained `'|'`, which broke the
    readable framing. Both codecs now strip delimiters from every value.
15. `cpsim.orders` webhooks still fire for FIX orders on a clock advance. The receiver's order to-do skips a `fix_sim`
    instruction (its fills are collected over FIX), so nothing is booked twice and no stray to-do appears.
16. **Found while writing the verify (CODE, fixed):** `collect_after_advance` would have run the automatic guard on a
    PERSON-sent fix_sim instruction and recorded `automatic_failed` ("not from a proposal"). It now leaves such an
    instruction's fills for its sender: a "Fills available — import them" to-do. The automatic path books proposals'
    fills only (D5), and the person presses Import fills.

## 6. The verify (Task 9) — `apps/api/scripts/verify_cpsim05.py`, WRITTEN, NOT RUN

Built on verify_cpsim03's harness (the same output, DB helpers, shared cleanup, stubs, in-process transports). Its own
fixture ids are `…c955…`, the slug prefix is `cpsim5verify-`, and the capability code is `zz_cpsim5v_sim`. The clock
dates are four business days before today, so the sync window and the `<= today` price reads see them.
`--self-checks-only`: 28 PASS, 0 FAIL (2026-11-11). Sections: task1 · schema · endpoint · auto (model sell with LT + ST
lots and a fund buy; a cash proposal's fund line and par line) · outcomes (reject, partial + advance, delay + advance,
no price, oversell, a person's limit + advance + Import) · idempotency · system (harvest → rep2 second person → automatic)
· guard (every D2 condition, the DB trigger + CHECK, the call-site scan, NEVER, cpsim03 REST still callable) · blocks ·
unsupported (options static + CHECK, sell short refused) · isolation · timing (the after-response gate, the 15 s budget,
the message log with one query) · unchanged · conformance · safety · routes · front end · teardown · regressions (fast).
Honest limits, each printed as a [FIND] by the script: tilts are proven through the model path (they are not a kind);
the demo-household condition is proven by verify_cpsim03's rolled-back non-test org section (same `simulator_route`).
Proposals are seeded as DRAFTS through the privileged insert path (the generator is rep5's, not under test); submit,
approval, execute and the hook all run the real path.

## 7. Fix 1 findings (first verify run `sprint_prompts/logs/cpsim05_run.out`: 187 PASS, 27 FAIL)

Leftover fixture rows before fix 1: **0** in every counted table (a direct per-org count of positions / proposals
found none; `--cleanup-only` read 0 before and 17 PASS / 0 FAIL after).

| FAIL | Verdict | Evidence | Fix |
|---|---|---|---|
| **E** — every proposal submit 409 "the proposal has expired" (log 260, 267, 273, 275, 278, 281, 300) and everything downstream (261, 263–266, 268–269, 274, 276–277, 279–280, 282, 369, 370) | **VERIFY** | Not a TTL: `make_proposal` sets `expires_at = now() + 7 days`. `_expire_if_due` (rebalance_proposals.py:1052–1059) also expires when `AM.valuation_as_of(account)` (max live position `as_of_date`) is later than the proposal's `valuation_as_of`. The verify stamped `valuation_as_of = D0` (the counterparty clock, ≥4 business days back), but `/altruist/connect`'s sync — and every post-fill sync — writes positions at `as_of = date.today()` (altruist_positions_sync.py:630, 391). So latest (wall today) > D0 → "valuation moved" → expired at submit. The product is consistent with itself: its generator stamps the same `AM.valuation_as_of` its expiry reads; only the verify mixed the counterparty clock into a valuation date. | `make_proposal` stamps `AM.valuation_as_of(conn, ORG_A, account)` read right before the insert. Expiry rule and TTL unchanged. |
| E (follow-on, surfaced once submit passed) — approval 409 "approval waits for an adviser to confirm stale held-away data … ****0004: data as of never" | **VERIFY** | rep7's `_approval_precheck` (held_away.py:1789): every held-away account of the household is a dependency; the seed made `ACC_HELD` (household H_A, `is_held_away`) with NO positions, so as-of "never" is stale for every proposal of H_A. The gate is right; the fixture lacked data it needed. `ACC_HELD` is used nowhere else in the verify. | Seed one fund position for `ACC_HELD` as of THU (inside the 30-day limit; the fund, not the equity — no wash-sale interaction). |
| **I** — idempotency "TypeError: 'async_generator' object is not an iterator" (log 288) | **VERIFY** | `next((i for i in ids if (await …)…))` — a genexpr containing `await` is an async generator; `next()` cannot consume it. The same shape sat unreached in y_system's `any(… await … for r in rows)`. | Both replaced by awaited loops / list comprehensions; an AST scan of the script finds no genexpr containing `await`. |
| **N** — "fix_sim_auto is declared NEVER … and is not a registered action — 0" (log 341) | **VERIFY** | The declaration EXISTS: `workflow_autonomy.VERB_AUTONOMY["execution.fix_sim_auto"]` class NEVER (line 113), `sod.VERB_DUTIES` 'write' (line 703). The `0` is the registry size: `REGISTRY` is filled by `assistant_actions.register_all()` in the app lifespan, which ASGITransport never runs — the same finding as cpsim04 fix 1 (G). | `register_all()` when empty (cpsim04 fix 1's helper); every registered action's handler + draft handler, every `*sweep*` function in services, and the scheduler tick are checked against the automatic entry points; positive controls: a planted action whose handler is `fix_sim_auto.run_one` IS caught, and the pattern finds the choke point's call in rebalance_proposals. The call-site scan (`got == want`) is unchanged. |
| **S** — "SELL SHORT … ValidationError: … carries short terms" (log 357) | **VERIFY** | rep13 `TradeLine._one_amount` (custody/trading.py:155): a `sell_short` line must carry `short_terms` with `position_effect: open`. The fixture had none, so the model refused it before the route was reached. | The line carries `short_terms = {"position_effect": "open"}`; a new check asserts the TradeList is valid, so the refusal asserted (`ShortSaleNotDeclared`, nothing sent) is the route's. |
| **R1** — nested verify_cpsim03 §8 "every row ASSUMED — 25 rows; not ASSUMED: []" (log 482) | **VERIFY** | The offender list is empty; the failing term is the exact heading words `"ASSUMED — no published Altruist trading API seen"`, which altread1 relabelled to `## 8. Orders — ASSUMED — not part of Altruist's published API (Realtime / FIX)`. | §8 is found by its number (`## 8. Orders`, unchanged); its HEADING must contain ASSUMED; ≥ 20 rows, every row ASSUMED — unchanged. |
| **R2** — nested verify_altread1 "PROJECT_STATUS carries the ALTREAD1 STATUS line" (log 484) | **DOCS** (cpsim05 damage) | `git diff 904d265 d017e9a -- docs/PROJECT_STATUS.md`: cpsim05 task 1 inserted its block by replacing the start of the line `## ALTREAD1 — the Altruist READ client`, leaving ` matches Altruist's PUBLISHED …` with no heading. The check was right. | Heading restored verbatim; the check is unchanged. |

## 8. Fix 1 proof (fix 2, 2026-11-12) — each one-off recorded and committed as it ran

Each run: `timeout 600 doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cpsim05.py <flag>` (one at a time,
nothing else heavy alongside).

| Run | Result | Notes |
|---|---|---|
| `--cleanup-only` (start) | **17 PASS, 0 FAIL** (183 s) | fixture rows before: **0** in all 55 counted tables (the killed fix 1 one-off left nothing); after: 0 per table, 0 leftovers; 15 real-row fingerprints (count + checksum) unchanged |
| `--self-checks-only` | **28 PASS, 0 FAIL** (14 s) | git / route / changed-line self-checks |
| verify_cpsim03 `--self-checks-only` | **37 PASS, 0 FAIL** (14 s) | incl. the R1 §8-by-number check |
| verify_altread1 `--self-checks-only` | **50 PASS, 0 FAIL** (14 s) | incl. R2 "PROJECT_STATUS carries the ALTREAD1 STATUS line" |
| `authz1_routes.py --check` / `visibility1_routes.py --check` | **CURRENT / CURRENT** | route tables up to date |
| `--only=auto` (end_to_end) | **34 PASS, 1 FAIL** — the FAIL is SIGTERM at the 600 s cap **during the end teardown**, after every section assertion | Section: **17 / 0**, all assertions ran: model proposal (human maker, different checker) → 2 instructions routed fix_sim; sell + buy each: approval of record via proposal maker-checker, sent automatically (channel fix_sim, actor the checker, OrderID kept), fills via the EXISTING enter_fill at the platform close (Decimal), post-fill sync + automatic reconcile; tax engine realized **LT 72.00 / ST −12.00**; proposal page P/L equals it, labelled SIMULATED; unrealized P/L = harvest.evaluate_lot = independent arithmetic (**−70.00**); instruction page shows the FIX exchange + "automatic — simulated counterparty"; no network / model call. The teardown did not finish: 326 fixture rows left in 55 counted tables |
| `--cleanup-only` (after auto) | **17 PASS, 0 FAIL** (226 s) | 326 counted fixture rows before (428 rows over 63 tables removed, 1177 queries in 127 s); 0 after; real fingerprints unchanged. Timing: a teardown with rows ≈ 127 s + ≈ 90 s of counts / fingerprints, on top of the start teardown (≈ 42 s) — ≈ 280 s of a one-off's 600 s is fixed overhead |
| `--only=cash` | **93 PASS, 0 FAIL** (361 s) | Section **2 / 0**: the cash proposal's FUND line sent, filled, synced and reconciled automatically; its PAR line refused by the simulator ('unsupported instrument') — left approved for a person with the reason and a to-do. Timing (new per-section print): pre-teardown 42 s, pre counts + fingerprints 66 s, schema 3 s, seed 81 s, cash 50 s, end teardown 75 s + counts / fingerprints 43 s |
| `--only=outcomes` | **100 PASS, 0 FAIL** (474 s) | Section **10 / 0** (227 s): reject → instruction stays APPROVED, reason (39=8 / 58) on a custodian_reject event, to-do for the checker, nothing booked; partial 40 % → 2 of 5 booked, rest fills on the D2 advance at the new close, booked + reconciled; delay → New, filled after the advance; no price → rejected 'no price', oversell → 'insufficient position', both left approved; a person's non-marketable limit (40 vs 50) → New, the advance fills it at the counterparty but books NOTHING (automatic path is proposals only) and the sender gets a 'Fills available' to-do; Import fills books at the D3 close with the person as actor. End teardown 790 rows, 0 left |
| `--only=idempotency` | **94 PASS, 0 FAIL** (312 s) | Section **3 / 0** (50 s) — the fix 1 I fix RAN: own approved model proposal executed automatically (fix 1's own_model_proposal); re-running the automatic step on a done instruction books nothing and sends nothing; the same ClOrdID sent again → the same order, one live order |
| `--only=system` | **93 PASS, 0 FAIL** (285 s) | Section **2 / 0** (51 s): a system (harvest) proposal reviewed → its instructions wait for rep2's second-person approval (nothing sent, no approval-of-record event); a second person (approve_execution) approves → sent, filled and reconciled automatically as that approver |
| `--only=guard` | **109 PASS, 1 FIND, 0 FAIL** (264 s) | Section **20 / 0** (37 s). Every cpsim03 D2 condition removed → manual with the reason, NOTHING sent (real Altruist crosswalk — reason the real capability 'unknown'; no simulator crosswalk; counterparty_sim.allowed off; no simulator connection (org B); simulator capability 'unknown'; a per-trade rule; authority advise → rep7b reason first); [FIND] demo_household proven by verify_cpsim03's rolled-back non-test org section (same `simulator_route`). The automatic sender on a MANUAL instruction refuses (audited), sends nothing. DB backstop: the trigger refuses a fix_sim event on a non-fix_sim instruction; the CHECK refuses the fix_sim channel on a non-fix_sim route. The call-site scan (`got == want`, unchanged); N fix ran: every registered action (28) and every sweep (9) + the scheduler tick name no automatic entry point — **both positive controls caught** (planted action with handler `fix_sim_auto.run_one`; the pattern finds the choke point's call). fix_sim_auto declared NEVER, not a registered action; cpsim03 REST route still callable; adapter default protocol 'fix' |
| `--only=unsupported` (the short) | **95 PASS, 0 FAIL** (228 s) | Section **4 / 0** — the fix 1 S fix ran: options legs routed 'manual' directly (rep8) and the DB ties option terms to manual; the SELL SHORT line is a VALID rep13 trade list (short terms carried), so the refusal is the route's: refused with the reason (no short_selling declared; manual offered), nothing sent |
| `--only=blocks` | **98 PASS, 0 FAIL** (330 s) | Section **8 / 0** (82 s): a block with one non-simulated member cannot take fix_sim (409, the member's reason); route 'fix' still refused (not connected); manual still the default and always offered, the simulated router not counted as a real automated route; the block's own maker-checker → block NewOrderSingle, fills via blocks.enter_fill, average price computed from the fills; allocated by the existing path, 35=J accepted (35=P), each allocation settled; both member instructions filled / reconciled |
| `--only=timing` | **94 PASS, 1 FAIL** (309 s) | Section **3 / 1**. PASS: own approved model proposal (fix 1's own_model_proposal) executed automatically; **every automatic step started only after its request's response was sent (the after-response gate)**; the FIX message log (63 rows) answers in 0.59 s with ONE query. **FAIL (open, NOT fixed, bound NOT loosened): "the approval request answered in 6.95 s"** against the verify's bound `CLIENT_TIMEOUT_S / 3` = 5 s. Evidence so far: the gate assertion passed, so the 6.95 s is NOT the automatic step — it is the approval + choke-point execute done synchronously inside the request. Every DB round-trip from this WSL host to Supabase costs ≈ 55–120 ms (this run's teardown: 1174 queries in 73 s; the empty start teardown 342 in 19–42 s), so 6.95 s ≈ 60–120 round-trips. That points to environment latency rather than the product, but fix 2 did NOT prove it (no query count of the request was taken). Verdict owed: count the request's queries (or time it on Render, next to the DB) before calling it CODE or VERIFY |
| `--only=unchanged` | **95 PASS, 0 FAIL** (239 s) | Section **4 / 0**: the real 'fix' router is still a NotConnectedRouter ('fix_sim' is the SimulatedFixRouter); real Altruist equity_trading_api still 'unknown'; no capability loader applied (no altruist_simulator row); the FIX client refuses sandbox / production before any request |

### 8a. Safety re-check (fix 2 Task 3) — from the one-offs above, all run on fix 1's HEAD
- **The automatic path engages only for the simulated route**: guard — each cpsim03 D2 condition removed (real Altruist
  crosswalk, no simulator crosswalk, counterparty_sim.allowed off, no simulator connection, simulator capability
  'unknown', a per-trade rule, authority advise) → manual with the reason, and NOTHING sent (no order, no FIX message);
  demo_household by verify_cpsim03's rolled-back non-test org section (same `simulator_route`), a [FIND] not a gap. The
  automatic sender called on a manual instruction refuses (audited) and sends nothing. The call-site scan is unchanged
  and both positive controls are caught.
- **DB backstop**: the D5 trigger refuses a fix_sim event on a non-fix_sim instruction; the CHECK refuses the fix_sim
  channel on a non-fix_sim route (guard).
- **Real `fix` router NotConnected** (unchanged; blocks: route 'fix' refused, not connected).
- **Real routes manual**: manual is still the default and always offered; the simulated router does not count as a real
  automated route (blocks); real Altruist capability 'unknown' (unchanged, guard).
- **Loaders not applied**: no altruist_simulator capability row (unchanged).
- Nothing in fix 2 changed product code; the only verify change is the per-section elapsed-seconds print.
