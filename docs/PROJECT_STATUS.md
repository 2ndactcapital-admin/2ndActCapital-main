# Project Status — open blockers and tracked follow-ups

Last updated: 2026-08-30 (Fee module fee39 — profitability views)

## About this file

This file records work that is **blocked on something outside the codebase** —
an AWS console change, a vendor contract, a credential only Joe can provision —
so that a blocked item is tracked in one place instead of living in a code
comment that the next sprint deletes.

**A note on this file's own history, since it matters for how much to trust
older references to it:** several earlier sprints
(`verify_litellmreloadaction.py`, `verify_portfolioc.py`, `verify_portfolioux1.py`,
`verify_superadminmenu.py`) state that a follow-up was "recorded as a tracked
follow-up in docs/PROJECT_STATUS.md". **The file did not exist** — it was never
committed and git shows no deletion. Those sprints' follow-ups are therefore
*not* recorded here yet and have not been back-filled by this sprint. If you are
looking for one of them, it is in that sprint's verify script and log, not here.
This file starts with the email item below.

---

## 00000. Fee module fee39 — profitability views BUILT; ONE fix applied, FOUR items owed (2026-08-30)

`87/91 PASS, 4 FIND, 0 FAIL, 0 BLOCKED` — `apps/api/scripts/verify_fee39.py`.
`services/profitability.py` is the module; `routers/profitability.py` and
`apps/web/app/profitability/` are the read-only surface. Nothing in fee39 is
blocked on anything outside the codebase.

### [A] A REAL CROSS-ORG LEAK WAS FOUND AND FIXED — AND THE SAME BUG IS STILL OPEN ELSEWHERE

`v_profitability_events` was deployed by Part 1 with **no `security_invoker`**,
and its owner (`postgres`) has `rolbypassrls = TRUE`. A view without that
option evaluates its base tables' RLS as the VIEW OWNER, so the view handed any
`app_service` caller every org's revenue and cost rows — while both base tables
were correctly locked down and looked it. The sprint prompt asked for this to
be verified rather than assumed, and the assumption was false.

Fixed live with `ALTER VIEW public.v_profitability_events SET (security_invoker
= true)`. `verify_fee39` [7f] REPRODUCES the leak on a twin view built from the
original definition before [7h] proves the real view now refuses the same read,
so the fix is demonstrated against the actual defect rather than in isolation.

**Still owed, not fixed here:** `v_trial_balance` and the other GL views carry
the identical defect. They were flagged during the portfolio-D sprint and are
still `security_invoker: FALSE` in `docs/schema_snapshot.sql`, which now
records the flag per view. Anything reading them through a non-superuser
connection is reading across orgs today.

### [B] TWO PRODUCT TYPES SHARE ONE REVENUE TYPE

`revenue_events_type_check` admits eight values; three of them cannot come from
a fee run (`SPV_CARRY` is deferred and event-driven, `PASS_THROUGH_MARKUP` is
fee37's cost engine, `INTEREST_SHARE` has no fee-run source). That leaves five
revenue types for six `fee_schedules.product_type` values, so
`STRUCTURED_INVESTMENT` and `TRANSACTION` both map to `PLACEMENT_FEE`.

Nothing is lost for reporting — `revenue_events.product_type` is on the row and
the product cut still separates them — but the two cannot be told apart by
`revenue_type` alone, which matters the moment they need different GL
treatment. Fixing it means adding a value to the deployed CHECK first.

### [C] THE ADVISOR CUT GROUPS ON AN UNCONSTRAINED COLUMN

Neither `revenue_events.advisor_id` nor `cost_events.advisor_id` has a FOREIGN
KEY, unlike `account_id` / `household_id` / `billing_group_id` on both tables.
A typo'd advisor id inserts cleanly and appears in an advisor roll-up as its
own silent, empty-named bucket. Adding `REFERENCES users(id)` to both is a
small migration nobody has done.

### [D] cost_events IS STILL NOT PROVABLY DUPLICATE-FREE (inherited fee37 F4)

`cost_events_dedupe_uq` indexes `account_id`/`household_id`/`billing_group_id`,
and a UNIQUE index does not constrain rows where those are NULL — which is
exactly every firm-level and provider-level cost. `verify_fee39` [5o]
reproduces it: two byte-identical `cost_events` insert without complaint.

fee39 does not fix the index; it makes the consequence visible instead.
`profitability.duplicate_cost_scan` finds such groups and `profit_and_loss`
attaches a warning naming the surplus, so a doubled cost line is reported
rather than silently reducing a client's margin. The real fix is a partial
unique index per NULL-combination, or a generated discriminator column.

### [E] THE RATES BEHIND PASS-THROUGH COSTS ARE STILL UNVERIFIED (inherited fee37 F6)

Any P&L containing a pass-through cost carries
`profitability.UNVERIFIED_RATE_CAVEAT`, attached only when such a row is
genuinely in the cut ([5m] proves it fires, [5n] that it does not fire
otherwise). The underlying `cost_schedules` / `provider_benefit_schedules`
`source_url`s still have not been re-checked against a primary source. Until
they are, the cost side of every margin here is an order-of-magnitude figure.

---

## 0000. Fee module fee38 — Altruist One evaluator BUILT; FIVE items owed (2026-08-29)

`64/67 PASS, 3 FIND, 0 FAIL, 0 BLOCKED` —
`apps/api/scripts/verify_fee38.py`. `services/altruist_one.py` is the module.
Nothing in fee38 is blocked on anything outside the codebase. The section
numbering in this file has drifted (000 / 00 / 0 / 1); this entry follows the
established prepend-a-zero pattern rather than renumbering everyone else's
sections. **fee37 has no entry in this file at all** — its findings live in
`verify_fee37.py` and its sprint log, and fee38 did not back-fill them.

### [A] DECISION NEEDED — which reading of the Altruist One subscription line?

fee37 seeded BOTH readings of one ambiguous rate-card line and a guard rail
(`assert_no_ambiguous_overlap`) stopping anyone summing them. fee38 had to
pick one, and picked **FLOOR** — `max(0.0012 x household_value,
12 x account_count)` — because that is what the design doc states.

**fee37's own seeded note argues the opposite**: that ADDITIVE is the
conservative choice because it is the more expensive one. Both readings are
implemented, `subscription_reading` is a parameter, and every persisted
evaluation records which reading produced its number, so nothing is silently
resolved. But a human still has to read altruist.com and settle it. Until
then, every stored `annual_cost` is conditional on a coin-flip that has been
recorded rather than made. (Verify check `8h`/`8i`.)

### [B] THE DESIGN DOC'S "10% IN SWEEP CASH → ENROLL" HEURISTIC IS WRONG

At the seeded rates, sweep cash alone must be **48% of household value** to
break even against the subscription — 25 bps of uplift on cash against 12 bps
of cost on total value. Ten percent gets you 2.5 bps. A $2M household with
exactly 10% in sweep cash and nothing else recommends **DO_NOT_ENROLL**.

This is not a bug in the evaluator; it is a false premise in the doc, and the
sprint's own acceptance criterion was written from it. Verify check `2a`
computes the counterexample explicitly before `2b` builds a household that
genuinely does recommend ENROLL (cash plus margin plus model discount plus a
counted trade figure). **The doc's heuristic should be corrected or dropped**
— if it reaches an advisor as a rule of thumb it will produce wrong advice.

Note this conclusion is only as good as the rates behind it — see [C].

### [C] THE RATES ARE STILL UNVERIFIED (inherited fee37 F6, now wider)

fee38 seeds five NEW rate rows into `provider_benefit_schedules` (sweep uplift,
HY uplift, model-marketplace discount, per-ticket saving, TLH tax alpha). They
carry `source_url` and `source_verified_on` exactly as fee37's cost rows do,
and they carry the **identical limitation**: nobody has re-read the source.

The evaluator attaches `UNVERIFIED_CAVEAT` to the persisted
`benefit_breakdown` of every evaluation, so the caveat travels with the number
to whatever screen displays it. That is a mitigation, not a fix. **Someone has
to read altruist.com and stamp a real `source_verified_on`** before any of
these figures is quoted to a client.

Two of the five rows are fee37's `UNSEEDED_RATE_CARD_ITEMS` finally given a
home: `CASH_SPREAD` (a benefit, so it could not live in `cost_schedules`
without being summed as an expense) and the model-marketplace discount (whose
base was unstated — fee38 resolves that by CAPPING it at the fee actually
being paid, which is a defensible reading but still a reading).

### [D] DATA GAPS — three inputs the deployed schema cannot supply

Measured in Task 1, reported on every evaluation in `data_gaps` rather than
papered over:

1. **Sweep vs high-yield cash is not separable.** `account_balances_daily` has
   one `cash_value` numeric and no cash-type dimension. The split is a
   caller-supplied `sweep_share_of_cash`, defaulting to "all sweep".
2. **Model-allocated AUM does not exist.** `accounts.service_model` is free
   text with no allocated-value column behind it. Caller-supplied, default $0,
   so the model-discount benefit is simply absent unless someone types a number.
3. **No account-level trade count exists.** `portfolio.transactions` reaches an
   account only through `positions.account_id`. The ticket-savings line is
   **omitted** (not zeroed) when no counted figure is supplied — a zero would
   read as "counted, and it was nothing".

These are inputs a real deployment needs a source for. Until then the evaluator
is running on two and a half of its six intended inputs.

### [E] SCHEDULED RE-EVALUATION IS NOT WIRED — waiting on S29b

`next_review_on` is accepted, persisted, and covered by the deployed
`altruist_one_evaluations_review_idx`. `due_for_review()` is the query a
scheduled trigger will call. **Nothing calls it on a schedule.** That needs a
Workflow Manager trigger, which is the standing fee-module-external dependency
on S29b landing. Recorded as `NEXT_REVIEW_WORKFLOW_TODO` in the module.

### Two smaller things worth knowing

**MARGINAL can never be MATCHED by a decision.** The deployed `decision` CHECK
admits only `ENROLL` and `DO_NOT_ENROLL`, so the `override_requires_reason`
CHECK treats every decision on a MARGINAL evaluation as a divergence needing a
written reason and a named decider. That is correct — a near-breakeven call is
exactly the one that should carry a reason — but it is invisible from the
column list, so `record_decision` says it in the error text. (Check `1k`/`6h`.)

**`account_balances_daily` double-counts on a naive SUM.** Its primary key
includes `source_system`, so one account can hold several rows for the same
day. `load_household_inputs` takes one row per account via `DISTINCT ON`
restricted to `is_billing_source`, and separately counts accounts that still
have more than one billing-source row on their latest date, reporting that as
a data gap. The verify fixture plants a second AGGREGATOR feed on every account
specifically so the dedupe is exercised against a real duplicate — without it a
plain SUM would read $5,000,000 where the answer is $2,000,000. Same shape as
fee37's F4.

---

## 000. Fee module fee36 — runs & approvals BUILT; ONE decision owed, TWO findings for a later sprint (2026-08-28)

`87/90 PASS, 3 FIND, 0 FAIL` — `apps/api/scripts/verify_fee36.py`. Nothing in
fee36 is blocked; it closed fee35's F1 and F4 and fixed two live trigger
defects. Four items are recorded here.

### [A] DECISION NEEDED — which books does RIA fee revenue post to?

The GL hook in `services/fee_runs.py::post_to_ledger` is a **deliberate,
clearly-marked stub** that writes nothing and returns `posted: False` with the
reason. It was not guessed, for a concrete reason measured live:
`journal_entries.vehicle_id` is `NOT NULL`, every deployed `posting_templates`
row (including `MANAGEMENT_FEE`) posts *inside a vehicle's* books, and
`chart_of_accounts` has no advisory-revenue account — `5000 Management Fee
Expense` is the **payer's** side. Wiring it would either invent a vehicle id
for the firm's own revenue or book that revenue as somebody's expense.
`fee_run_lines` are emitted regardless; posting is additive when the answer
exists. Design doc open question #3.

### [B] FINDING F36-C — a POSTED run can never reach status `'REVERSED'`

`fee_runs_status_check` admits `'REVERSED'`, but
`fee_runs_immutable_once_posted` refuses every UPDATE on a POSTED row — by
design, and the sprint deliberately did not weaken it. The reversal link is
therefore read *backwards*, through `fee_runs.reverses_run_id` on the new run.
`'REVERSED'` is currently an unreachable value. Not a bug; worth knowing before
someone writes a screen that filters on it.

### [C] FINDING F36-D — a group minimum silently leaves the group when an account refunds

fee35's `_minimum_step` short-circuits on `run.amount < 0` ("applying a minimum
here would turn a refund into a charge") **before** it reaches the
HOUSEHOLD/BILLING_GROUP branch, so `minimum_deferred_to_group` is never set and
`calculate_group_fees` never puts that account in a bucket. In a group where
one account refunds (a credit exceeding its fee) and others bill, the group
subtotal is computed **without** the refund, so the shortfall charged to the
remaining accounts is too large. The per-account skip is right; dropping the
account out of the group aggregation is probably not. This is fee35's
arithmetic and fee36 deliberately did not change it — pinned by check `6f`/`6g`
in `verify_fee36.py` so a future edit has to decide about it on purpose.

### [D] Closed here, for the record

* **fee35 F1 (`fee_credits` has no amount column)** — resolved. Only
  `SPV_MGMT_FEE_OFFSET` has a real source in the deployed schema: the sum of
  the account's owning entity's `spv_transaction_allocations.allocated_amount`
  across *posted* `call_mgmt_fee` transactions dated inside the period — the
  investor's own share, not the vehicle-level amount. The other four sources
  (`12B1`, `SUB_TA`, `SI_EMBEDDED_FEE_OFFSET`, `MODEL_FEE_OFFSET`) have **no
  source table anywhere** and raise `CreditBasisUnavailableError` rather than
  crediting zero. **A trail/revenue-receipt table is still owed before those
  four credit sources can be billed.**
* **fee35 F4 (`accounts` has no `billing_group_id`)** — resolved via
  `billing_group_members` × `billing_groups(group_type='BREAKPOINT')`, as of the
  date billed. Absent → `None`, which lets the engine raise its own
  `GroupScopeMissingError`; ambiguous → `AmbiguousBillingGroupError`.
* **Two live trigger defects fixed** — `docs/fee36_part1_fix.sql`. See the
  fee35 section below for context on what Part 1 originally applied.

---

## 001. Fee module fee35 — calculation engine BUILT, three decisions owed by fee36 (2026-08-28)

**Status: built and verified, 22/22 PASS, 0 BLOCKED, 9 FIND.**
`apps/api/scripts/verify_fee35.py` — a pure unit suite that opens no database
connection and needs no credentials. `services/fee_calc.py` (the pipeline) and
`services/fee_calc_inputs.py` (the plain-data contracts). Nothing here is
blocked outside the codebase; the three items below are real decisions fee36
must make before a single invoice is produced.

1. **`fee_credits` has no amount column.** Its only numeric column is
   `offset_pct`, confined to `[0,1]`. A credit of "50% of the SPV management
   fee" has nowhere to record what the SPV management fee *was*.
   `CreditInput.basis_amount` is therefore a **required, caller-supplied**
   field with no column behind it. **fee36 must decide where that number comes
   from** — a `fee_runs` input, a second lookup, or a new column on
   `fee_credits`. Defaulting it to zero would make every credit silently
   worthless, which is why the engine refuses to construct a credit without it.

2. **`fee_discounts.value` has no scale and no CHECK constraint.** A `PCT_OFF`
   of `20` and one of `0.20` differ by 100x and both satisfy the column. The
   engine reads `PCT_OFF` as a **percent in [0,100]** and refuses anything
   outside that range. Note the deliberate contrast with
   `fee_credits.offset_pct`, which the deployed constraint confines to `[0,1]`
   — two adjacent tables express a proportion on two different scales and
   nothing in the schema says so. **A CHECK constraint on
   `fee_discounts.value`, scoped by `discount_type`, is the durable fix** and
   is not applied yet.

3. **No holiday calendar exists anywhere in this codebase.**
   `proration_method='BUSINESS_DAYS'` is a deployed, valid value and is
   currently calculated on a plain Mon–Fri count. A market holiday inside the
   period is counted as a business day, overstating the denominator and
   slightly understating a partial-period fee. The engine declares this in
   every affected result's `assumptions`, so it will appear on the fee line
   rather than only in a docstring — but a real NYSE calendar is owed before
   any client is billed on a BUSINESS_DAYS schedule.

**Two smaller things fee36 inherits rather than owes.** `POSITION_TAG` is a
deployed `basis_type` but `portfolio.positions` has no tag column (tags live
in `portfolio.udf_values`), so `PositionInput.tags` is caller-supplied; and
`accounts` has no `billing_group_id` (membership is `billing_group_members`),
so a `BILLING_GROUP`-scoped `minimum_fee` needs the caller to resolve it —
a missing one raises `GroupScopeMissingError` rather than silently degrading
to an account-scoped minimum.

**One bug this sprint's own suite caught and fixed.** An `ASSET_CLASS`
exclusion cannot use `startswith`. Under Rule 4's key scheme
`taxonomy_mc_3_2` is a child of `taxonomy_sc_3` and is *not* a string prefix
of it, while `taxonomy_sc_30` *is* a string prefix and is an unrelated class —
so prefix matching was wrong in both directions at once. Keys are now parsed
into numeric components and compared component-wise (`taxonomy_covers`), with
both directions asserted.

**Deliberately not built:** anything that writes (`fee_runs`/`fee_run_lines`
is fee36), any resolution of *which* schedule/exclusions/discounts/credits
apply (fee32/fee34 own that; the engine consumes their output), and SPV
carry/waterfall, which the design doc defers to its own sprint.

---

## 002. Fee module fee34 — schedule catalog BUILT, four follow-ups owed (2026-08-27)

**Status: built and verified, 49/49 PASS, 0 BLOCKED, 3 FIND.**
`apps/api/scripts/verify_fee34.py`. Nothing here is blocked on anything outside
the codebase — the four items below are real, deliberate gaps a later fee
sprint has to close, recorded so they are not rediscovered as bugs.

### What now exists

- `services/fee_validation.py` — pure, zero database access. Tier contiguity,
  the `minimum_fee`/`minimum_fee_scope` pair, the `REDUCED_RATE`/`FLAT`
  exclusion rules, non-empty `reason`, `approved_by`, and the
  `ordering_policy` permutation. fee35 must **re-run this module**, never
  re-implement the checks.
- `services/fee_schedules.py` — create (always DRAFT v1), edit, submit,
  retire, assign, end, and precedence resolution.
- `routers/fee_schedules.py` — registered in `main.py` at
  `/api/v1/fee-schedules`.

**Part 1 was genuinely applied this time.** Confirmed live on the app's own DSN
before any code was written (`scripts/discover_fee34.py`), not on the MCP
endpoint alone. This is worth stating because fee33's prompt carried the
identical "already applied by Joe" sentence and it was **not** applied.

### Follow-ups owed

1. **`fee_assignments` has NO unique index.** Nothing in the database stops two
   active assignments on the same `scope_id` with overlapping effective dates,
   which would make precedence ambiguous *within* a rung.
   `create_assignment(replace_existing=True)` closes the incumbent first, so the
   application never creates one — but an import path or a manual SQL fix can.
   A partial unique index on `(org_id, scope_type, scope_id) WHERE valid_to IS
   NULL AND system_to IS NULL` would close it properly.
2. **No CRUD for `fee_exclusions` / `fee_discounts` / `fee_credits`.**
   Deliberately out of scope for fee34, which builds the catalog only. The
   validators for all three exist and are tested; the write paths do not. A
   later sprint must call `validate_exclusion` / `validate_discount` /
   `validate_credit` at those rows' own write time — they are **not** reachable
   from the schedule-approval gate (see finding 3 below).
3. **`ENTITY` and `ORG` scopes are unreachable on three of the six tables.**
   `fee_exclusions.scope_type` admits `ORG` (not `ORG_DEFAULT`) and no `ENTITY`;
   `fee_discounts` and `fee_credits` admit neither. Three different scope
   vocabularies, kept as three constants in `fee_validation.py` precisely so
   they cannot be collapsed into one. If the fee engine needs an entity-level
   discount, that is a schema change, not a code change.
4. **Nothing reads a schedule to produce a dollar.** By design — that is fee35.

### The three findings

1. `fee_schedules_code_version_uq` is `UNIQUE (org_id, code, version)` with **no
   partial predicate**, so a Rule 3 valid-axis restatement of a schedule is
   impossible: closing a row and re-inserting the same `(code, version)`
   collides with the row just closed. Versioning goes through `version+1` and a
   DRAFT edit is an in-place `UPDATE`. Not a style choice.
2. `fee_assignments.precedence` is `NOT NULL` with no default and **no tie to
   `scope_type` anywhere in the database**. A body carrying `precedence: 1` on
   an `ORG_DEFAULT` assignment would outrank every account-specific agreement in
   the org, silently. It is derived server-side from `scope_type` and the
   request model declares no such field.
3. **The exclusion rules are not reachable from the schedule-approval gate, and
   the fee34 prompt assumes they are.** `fee_exclusions` has no
   `fee_schedule_id` — only `alt_fee_schedule_id`, the REDUCED_RATE *target*.
   There is no join path from a schedule to "its" exclusions because a schedule
   does not have any. Folding them into `validate_schedule` would have produced
   a gate that always passes vacuously on an empty list.

---

## 00. LiteLLM Phase B — routing BUILT, three real blockers to a first success (2026-08-26)

**Status: built and verified, 68/68, with 5 BLOCKED.**
`apps/api/scripts/verify_litellmphaseb.py`. The routing change is complete and
the transport is proven against the live proxy. What is blocked is a *successful*
generation, and it is blocked on three things outside the codebase.

### What now exists

- `services/extraction.py` — the platform's single AI chokepoint — now sends its
  HTTP calls to the self-hosted LiteLLM proxy instead of straight to Anthropic.
  It is a **transport swap at one function** (`_build_ai_client`): the fallback
  chain, retry walk, cost model, `ai_decision_log` writes and error handling are
  untouched, and `ai_decision_log` gained no columns.
- **How, and why this way:** the Anthropic SDK is *pointed at* LiteLLM's base URL
  rather than replaced. LiteLLM serves a real Anthropic-shaped
  `POST /v1/messages` (confirmed live). Keeping the SDK means the response
  objects reaching every `extract()` closure and `_compute_cost` stay genuine
  Anthropic types, so `message.content[0].text`, `stop_reason`,
  `block.model_dump()` and `usage.input_tokens` all keep working unchanged. The
  OpenAI-shaped `/v1/chat/completions` route is *also* live and was measured, but
  using it would have required hand-writing an OpenAI→Anthropic response adapter
  (including `tool_calls`→`tool_use`) on the most load-bearing path in the
  platform. That is a rewrite, not a transport swap.
- **`LITELLM_ROUTING_DISABLED=1` — a real, tested rollback path.** Set it and
  every AI call goes straight back to Anthropic, never contacting LiteLLM.
  Verified both ways: the client's real `base_url` becomes `api.anthropic.com`,
  and **zero rows appear in LiteLLM's own spend log** after waiting the full
  flush window. It is an **environment variable, not an org_settings key**, on
  purpose — it must keep working when the database is the unhappy thing, and an
  `org_settings` read would need a working DB to report that the DB-independent
  fallback is on. This is **not** design §7.5's future per-org `force_anthropic`.
- **A wrong master key now fails loud.** `AILiteLLMAuthError` names the variable,
  the endpoint, the HTTP status and the remedy, is still recorded in
  `ai_decision_log`, and does **not** walk the chain (every model shares the one
  key). It deliberately propagates through all three `call_claude_*` wrappers
  instead of being flattened into their usual `None`.

### GAP CLOSED — `LITELLM_BASE_URL` now exists in Doppler

The item below (and `render.yaml`'s note) recorded `LITELLM_BASE_URL` as the one
`LITELLM_*` variable genuinely missing from Doppler. **It was still missing, and
this sprint added it** to `hollisworks/prd`, pointing at the live
`https://hollisworks-litellm.onrender.com`. Verified by re-reading it back.

### ACTION REQUIRED — three blockers to a first successful call

None of these are code. All three need console access.

1. **The proxy has ZERO model deployments.** `GET /v1/models` returns
   `{"data":[]}`; `GET /model/info` returns HTTP 500 *"LLM Model List not loaded
   in"*. LiteLLM cannot route any model name. Corroborated independently: **every
   row LiteLLM has ever written to its own spend log is `status=failure`** — the
   proxy has never successfully served a single call.
2. **Doppler's `LITELLM_MASTER_KEY` is NOT the proxy's master key.** LiteLLM
   reports `role=internal_user` and refuses `POST /model/new` with HTTP 403
   *"only if you are a PROXY_ADMIN"*. It is a virtual/internal-user key. So this
   sprint could not fix blocker 1 either. **This also corrects the note below:**
   adding `LITELLM_BASE_URL` does *not* by itself unblock
   `litellm.reload_model_cost_map` — that admin endpoint needs PROXY_ADMIN, so it
   will still fail, now with a 403 rather than a `LiteLLMConfigError`.
3. **`ANTHROPIC_API_KEY` exists nowhere** — not in Doppler `prd` (all 35 secret
   names enumerated), not in `~/.bashrc`, not in `apps/api/.env`. Neither
   LiteLLM's upstream nor the direct-Anthropic rollback path has a provider
   credential. AWS Bedrock is not an alternative: the existing `AWS_*` creds are
   the Textract-only IAM user and `bedrock:ListFoundationModels` returns
   `AccessDeniedException`.

**Order to unblock:** obtain the real PROXY_ADMIN master key (2) → store a
provider key (3) → register a model deployment (1) → re-run
`verify_litellmphaseb.py`, which will convert the 5 BLOCKED items to PASS.

### What IS proven, against the live proxy

- Requests genuinely reach LiteLLM. The error text returned — *"Invalid model
  name passed in model=…"* — is generated by LiteLLM itself; neither our code nor
  `api.anthropic.com` emits that string.
- The fallback chain still walks every model, in order, via LiteLLM, proven with
  a real forced-failure primary.
- **Dual visibility is real.** The same call appears in *both* `ai_decision_log`
  and `litellm."LiteLLM_SpendLogs"`, naming the same models in the same order and
  agreeing on the outcome. **Measured, and it matters: LiteLLM flushes that table
  asynchronously, seconds after answering.** A before/after count taken around
  the call sees nothing — an earlier draft of the verify script reported a false
  negative for exactly this reason. Any assertion about that log, presence *or*
  absence, must poll across the flush window.
- `ai_decision_log`'s shape is unchanged, compared column-by-column and
  type-by-type against a real pre-sprint row.

### Deploy note

`LITELLM_ROUTING_DISABLED` is **not** declared in `render.yaml` — it is an
break-glass switch, and a declared-but-unset variable invites someone to set it
permanently. Set it directly in the Render dashboard if the proxy misbehaves.
Until blockers 1–3 clear, production is on the **degraded** path: LiteLLM is
configured, so calls route to it, and they will fail. **If Phase B is deployed
before those blockers clear, set `LITELLM_ROUTING_DISABLED=1` in Render at the
same time** — but note blocker 3 means direct Anthropic has no key either, so AI
features are non-functional in production regardless, exactly as they were
before this sprint.

---

## 0. Workflow scheduler — core engine BUILT (2026-08-26)

**Status: built and verified, 65/65.** `apps/api/scripts/verify_schedulercore.py`.
Not blocked on anything. Recorded here because it closes two items this file's
predecessors kept referring to, and because it opened one new deployment action.

### What now exists

- **`workflow_triggers.schedule_cron` is no longer dead code.** Before this
  sprint a repo-wide grep found exactly two readers — a `SELECT` in
  `routers/workflows.py` and a display cell in `WorkflowTriggerScheduler.jsx`.
  **Nothing fired anything on a schedule.** The one `scheduled` row in the
  database had been inserted by a verify script, because there was no API path
  to create one.
- `docs/schedulercore_part1.sql` adds six columns to `workflow_triggers`:
  `timezone` (IANA, `NOT NULL DEFAULT 'UTC'`), `start_date`, `end_date`,
  `max_occurrences`, `occurrence_count`, `last_fired_at`.
- `services/workflow_schedule.py` — pure recurrence evaluation. Translates the
  stored cron expression into a real `dateutil.rrule` (preserving cron's
  day-of-month **OR** day-of-week semantics, which rrule ANDs) and resolves it
  in the trigger's **own** timezone.
- `services/workflow_scheduler.py` — the firing loop. Scans all orgs, evaluates,
  checks workflow-level overlap, claims the occurrence atomically, and fires
  through the **real** `workflow_engine.start_workflow_run`.
- `apps/api/workflow_scheduler_tick.py` — the minimal process entrypoint.
- `POST /admin/workflow-triggers` now accepts `trigger_type='scheduled'` plus
  the recurrence fields, with the cron expression and IANA zone validated at the
  boundary. The pre-existing three-field event body is unchanged and still works.

**Per-org timezone lives in our code, not in Render.** Render cron schedules are
UTC-only and cannot be made timezone-aware. The service ticks every 5 minutes in
UTC and each trigger's local schedule is resolved in Python. The 5-minute cadence
and the evaluator's 60-minute lookback window are a matched pair — change one and
re-check the other.

### `render.yaml`'s LiteLLM section — CORRECTED

The blueprint asserted that "the LiteLLM proxy is NOT DEPLOYED … there is no
LiteLLM service in this blueprint, and Doppler holds no `LITELLM_*` secret."
**Both halves were out of date.** `hollisworks-litellm.onrender.com` answers HTTP
200 on `/health/liveliness`, and Doppler holds four `LITELLM_*` secrets against a
migrated 77-table `litellm` schema. `LITELLM_BASE_URL` and `LITELLM_MASTER_KEY`
are now declared on the API service. **`LITELLM_BASE_URL` is still absent from
Doppler**, which is why `litellm.reload_model_cost_map` still raises
`LiteLLMConfigError` even though the proxy is up — see item 2.

> **SUPERSEDED by item 00 (LiteLLM Phase B, same day).** `LITELLM_BASE_URL` has
> now been added to Doppler `prd`. And the inference in the last sentence was
> wrong: adding it does **not** unblock `litellm.reload_model_cost_map`. The
> stored `LITELLM_MASTER_KEY` is an `internal_user` virtual key, not the proxy's
> PROXY_ADMIN master key, so that admin endpoint still fails — now with HTTP 403
> instead of `LiteLLMConfigError`.

### ACTION REQUIRED — deploy the cron service

`render.yaml` now declares a third service, `2ndactcapital-workflow-scheduler`
(`type: cron`, `plan: starter`, `schedule: "*/5 * * * *"`). **Until the blueprint
is applied in Render, nothing fires in production** — the engine is built and
proven, but no process is running it. A Render cron job has no free plan and a
**$1/month minimum**, billed by the second of active runtime.

Also unresolved and recorded rather than papered over: the `hollisworks-litellm`
service is live but is **not** declared as a block in `render.yaml`. It was
created outside the blueprint, so adopting it is a migration, not an edit. That
is a real remaining gap in this manifest's coverage.

### Two bugs this sprint found and fixed

**1. A held run vanished entirely when started through the real pool.**
`workflow_engine.start_workflow_run` documents that the run row is persisted "in
their own committed transaction … rather than vanishing on rollback". It was not.
`services.database._RLSPool.acquire()` opens an **outer** transaction (that is
how the RLS `SET LOCAL` GUCs are scoped), so asyncpg nested the engine's
`conn.transaction()` as a **savepoint**. When `start_workflow_run` re-raised
after holding, the exception escaped `pool.acquire()`, the outer transaction
rolled back, and the `workflow_runs` row, its `error_detail` and every
`create_held_run_alerts` todo were erased together — a failed run left **no trace
at all**.

Every prior verify script built a **raw** `asyncpg.create_pool`, where the inner
commit is real; that is precisely why this never surfaced. The deployed
event-trigger path (`chancery_workflow_bridge`) passes the actual RLS pool and
has always been exposed to it. Fixed by `_independent_acquire()`: the up-front
persist and the hold/alert now run on a connection that is not enlisted in any
caller's transaction, with the RLS context re-applied.

**2. The scheduler process had an empty action registry.**
`services.assistant_actions.register_all()` was called from exactly one place —
`main.py`'s FastAPI `startup` hook. The cron process never starts FastAPI. An
empty registry does **not** fail loudly: `_execute_service_task` resolves every
action to `None` and the engine marks the step **completed**. Every scheduled
workflow would have reported success having invoked nothing. `run_scheduler_tick`
now registers the actions itself, once per tick.

### Deliberately out of scope

Cost/duration correlation to a run. Zero workflow run steps have ever invoked
AI, `ai_decision_log` carries no run identifier, and there is nothing to
correlate yet. **Still true after Sprint 4** — the Run History screen ships with
no cost column for exactly this reason.

### Sprint 3 (CRUD UX) and Sprint 4 (Run History) are since complete

- **Sprint 3 — scheduler CRUD UX**, 91/91 (`ec6ef24`). Not blocked.
- **Sprint 4 — Run History**, 82/82 (`schedulerhistory.structural`). Not blocked.
  Server-side status and time-period filters, a step timeline, scheduled-vs-manual
  origin read from the run's stored context, and a held run's real `error_detail`
  plus the exact `member_todos` alert set.

**One correction this file's readers should carry forward:** *per-run duration*
appeared in the Sprint 4 plan as though it were sound data. It is not. Postgres
`now()` is the transaction timestamp; the engine inserts the run row on an
independent connection and completes it on the caller's, whose transaction opened
first — so a run that finishes inside its own `start_workflow_run` call has
`completed_at` **before** `started_at`. Measured at **-0.36s** on a real run
during verification. Both the API and the screen now report "not measured" for any
non-positive interval instead of a number. Anything downstream that plans to
aggregate run durations needs to know this before it starts averaging.

### Next

**Sprint 5 — notifications.** Largely satisfied already: `create_held_run_alerts`
really fires, really reaches the starter plus every `org_admin`, and Sprint 4
verified the exact recipient set against `member_todos`. The genuinely missing
pieces are enumerated in `docs/OUTSTANDING_TODO_LIST.md` §2 — the largest being
that a **User Task with no `assigned_role_profile_id` notifies nobody, silently**,
and that the only out-of-band channel (email) is blocked on §1 of this file.

**Still the binding blocker for this whole subsystem: the Render cron service has
not been applied.** Everything above is proven in verification and fires nowhere
in production until the blueprint is applied.

---

## 1. Email sending (invites) — BLOCKED on two AWS-side actions

**Status: built, wired, verified — and NOT working end-to-end.** Real delivery
is blocked on AWS-account changes that cannot be made from this codebase.

### What now exists in the code (done, verified)

Before this sprint there was **no email-sending code anywhere in the API** — no
SES, SMTP, SendGrid, Postmark or Resend client in `services/` or `routers/`.
That blocked an already-shipped feature: `POST /admin/invites` mints a real
invite and returns an `enrollment_url` for the admin to share **by hand**,
because there was no way to mail it.

Now shipped:

- `apps/api/services/email.py` — the single AWS SES choke point. Credential gate
  (`credential_state()` / `probe()`, following the `portfolio_altruist.py`
  pattern), one `send_email()` that returns SES's own `MessageId`, and an error
  taxonomy that classifies a failure as `credentials` / `iam` /
  `identity_or_sandbox` / `paused` / `transport`.
- `render_invite_email()` — a plain-text + minimal-HTML invite carrying the
  **inviting org's own** name, that org's `enrollment_url`, and the expiry
  derived from that org's configurable `invite.expiry_days` setting.
- `services/invites.py` — `create_invite()` now attempts a real send and records
  the outcome in `result["email_delivery"]` on **every** path.
- `routers/invites.py` — the create response returns `email_delivery`, and
  `GET /admin/email/status` re-probes SES live so the AWS actions below can be
  confirmed done without a redeploy.

**The fallback is announced, not silent.** When a send cannot happen the invite
still succeeds and `enrollment_url` is still returned — but `email_delivery`
carries `status: "blocked"`, `manual_share_required: true`, and a reason naming
the exact AWS action to take. Returning the URL as though mail had gone out is
the failure mode this sprint exists to prevent.

### Why it does not work today (measured live, not assumed)

Verified against the credentials **Doppler** actually serves to Render, on
2026-08-26:

1. **The IAM permission does not exist.** The credentials are valid and live —
   `sts:GetCallerIdentity` resolves them — but they belong to
   `arn:aws:iam::645767464372:user/Texttrac-Ripasso`, the **Textract-only** IAM
   user. A real authorization probe on the send action returns:

   ```
   AccessDeniedException: User '…:user/Texttrac-Ripasso' is not authorized
   to perform 'ses:SendEmail'
   ```

   This is the **same gap as the earlier invite sprint**. Tonight's Doppler
   credential rotation restored *working keys*; it did not change *what those
   keys are allowed to do*. Rotating them again will not help.

2. **The SES sandbox state cannot even be read.** `ses:GetAccount`,
   `ses:GetAccountSendingEnabled` and `sesv2:ListEmailIdentities` are **all**
   denied for this principal, so this deployment cannot determine whether the
   AWS account is out of sandbox. The code reports that as *unknown* rather than
   guessing. This is an **independent second blocker**: a sandboxed SES account
   can only deliver to verified addresses, so granting `ses:SendEmail` alone
   could still cause invites to real prospective members to be rejected.

3. **No verified sender is configured.** Doppler holds `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY` and `AWS_DEFAULT_REGION` — and **no** `SES_*`
   variable at all. `SES_FROM_EMAIL` is unset.

### ACTION ITEMS — Joe, outside this sprint

| # | Action | Where |
|---|--------|-------|
| 1 | Attach an IAM policy granting `ses:SendEmail` and `ses:SendRawEmail` (and `ses:GetAccount` so the status endpoint can report sandbox state) to the principal the deployment uses — either to `Texttrac-Ripasso` or, preferably, to a **new dedicated IAM user for mail**, whose keys then replace `AWS_*` in Doppler. | AWS IAM console |
| 2 | Verify a sender identity in SES — the address or, better, the sending domain (DKIM). | AWS SES console, `us-east-1` |
| 3 | **Request SES production access** (exit sandbox) for this account/region. Until this is done, delivery is restricted to verified addresses and real member invites will fail. | AWS SES console → Account dashboard |
| 4 | Set `SES_FROM_EMAIL` (and optionally `SES_FROM_NAME`, `SES_CONFIGURATION_SET`) in Doppler; confirm they reach Render. | Doppler |

**To confirm when done:** call `GET /admin/email/status` as an admin. It makes
one real SES call and reports `ok`, the gap, and `sandbox_known` /
`production_access`. Or re-run `apps/api/scripts/verify_smtpservice.py`, which
exits non-zero while this is blocked and will attempt a real send — and assert a
real `MessageId` — the moment the gate reports usable.

### Verification result (2026-08-26)

`apps/api/scripts/verify_smtpservice.py` → **9 PASS, 0 FAIL, 1 BLOCKED**, exit 2.

Everything that can be verified is: the discovery findings, the loud-failure
messages (including a **real** refused SES call proving the IAM message names
`ses:SendEmail`), the announced manual-URL fallback, cross-org content
correctness, output safety, and zero-leftover teardown. The single BLOCKED line
is real delivery, per the action items above. It is deliberately *not* reported
as a pass: "we correctly reported that we cannot send email" is not "email
works".

### One real bug this sprint found and fixed

`DEFAULT_SETTINGS["brand.name"]` is the literal string `"2nd Act Capital"`, and
**Hollisworks has no `brand.name` row**. Resolving the sender name with the
ordinary `get_setting()` would therefore have signed **every Hollisworks invite
email with 2nd Act Capital's name**. `resolve_org_display_name()` uses
`get_setting_with_origin()` instead and falls back to `organizations.name` (which
is per-org and `NOT NULL`), so the platform default is unreachable on this path.

This is the same "silently inherit the other tenant's value" shape as the Auth0
`domain ?? AUTH0_DOMAIN`, `appBaseUrl ?? APP_BASE_URL` and `audience` bugs — and
worse here, because the recipient sees it.

---

## 2. Notes for whoever runs the verify scripts

Working database credentials live in **Doppler**. The copies in `apps/api/.env`
and `~/.bashrc` are **stale** and their passwords are rejected by Postgres for
both the `postgres` and `app_service` roles; that stale copy is what produced
several sprints of false "blocked on credentials" results.

`apps/api/scripts/_doppler_env.py` hydrates `os.environ` from Doppler over its
**HTTPS API** using `DOPPLER_TOKEN` (stdlib only, no CLI, never prints a value).
`verify_smtpservice.py` uses it and overwrites the ambient values deliberately —
deferring to what is already set would preserve exactly the stale-copy bug.

## fee38 — subscription-reading decision (accepted, revisit before real reliance)

Altruist One subscription cost: FLOOR reading — max(0.0012 × household
value, 12 × account count) — accepted as the interim default for the
evaluator, per the design doc's own stated formula. fee37 also seeded
the ADDITIVE reading (subscription + per-account minimum, both apply)
and argued it as the more conservative choice. subscription_reading is
a parameter on every evaluation, so the choice is recorded per row,
not silently baked in.

Revisit before any recommendation from this evaluator is relied on for
a real client decision — the two readings can diverge meaningfully on
low-AUM/high-account-count households, and which one actually matches
Altruist's real billing behavior has not been confirmed.

## Security — cross-tenant RLS bypass on views without security_invoker

fee39 discovered and fixed a real cross-tenant data leak in its own
Part 1 view (v_profitability_events): a view owned by `postgres`
(rolbypassrls=TRUE) bypasses the RLS policies on its underlying
tables entirely unless security_invoker=true is set — the base
tables' own RLS is irrelevant once queried through such a view.

Auditing all views in public/portfolio found two more with the
identical exposure: v_capital_accounts and v_trial_balance (both GL
views). Fixed same-day: ALTER VIEW ... SET (security_invoker = true)
on both. All views in public/portfolio now carry security_invoker=true.

Any future view creation must set security_invoker=true explicitly —
this is not a Postgres default.
