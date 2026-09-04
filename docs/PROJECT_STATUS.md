# Project Status — open blockers and tracked follow-ups
Last updated: 2026-09-03 (TA Model Sprint 3 — commitment projection UX; Fee module fee43 — invoices, reconciliation, GL posting)

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

## 00000000. Fee module fee43 — GL posting SHIPPED; ONE gap owed (2026-09-03)

`68/68 PASS, 1 FIND, 0 FAIL, 0 BLOCKED` — `apps/api/scripts/verify_fee43.py`.
HELD for manual review (`.structural`). Nothing here is blocked on anything
outside the codebase; this entry records the one real gap the sprint found and
deliberately did not invent its way around.

**Design-doc open question #3 is CLOSED.** RIA fee revenue posts to the
`RIA_OPERATING` ledger book and club dues to `CLUB_DUES`, via the new
`journal_entries.vehicle_kind='LEDGER_BOOK'`; SPV-scoped revenue and carry keep
posting inside their own SPV's books (`vehicle_kind='SPV'`, unchanged). This
closes fee36's F4o stub and fee42b's 6l.

**The one gap — no GP legal entity exists.** `entity_type` has a `gp` enum
value, but ZERO `entities` rows use it and `spvs` has no GP/manager/sponsor
column. Carry therefore books inside the SPV's own book as an expense
(`5500 Carried Interest`) credited to the existing `2100 Due to Affiliate` —
a payable to the manager, NOT an equity allocation to a GP capital account.
That is the correct achievable treatment today and it is what shipped. If the
GP ever needs its own capital account, that is a real modelling decision
(entity + capital-account plumbing), not a posting-template change.

**Two premises in the sprint brief were wrong, and the code follows what is
actually deployed, not the brief:**

1. The brief said `chart_of_accounts` "has no advisory-fee or club-dues revenue
   account". Four revenue accounts were needed, not the two it sketched —
   `4400/4500/4600/4700` — because fee39 already resolves fee lines to three
   different RIA revenue types (ADVISORY / PLANNING / PLACEMENT). Collapsing
   them would have made `revenue_events` and the GL impossible to reconcile
   line-for-line.
2. The brief expected the chart to have a `parent_code` hierarchy to slot into.
   It does not — all 20 pre-existing rows have `parent_code` NULL. The real
   convention is a flat 4-digit code banded by `account_type`, and the new
   accounts follow that.

**`v_capital_accounts` is still broken and fee43 did NOT fix it** — see the
entry at the bottom of this file, now updated with the measured answer.

**Not done, deliberately:** runs POSTED before fee43 have no journal entries
and do not acquire any. Backfilling history is an explicit decision, not an
oversight. `revenue_events.journal_entry_id` is also left NULL — that column is
exactly the revenue-to-GL link a reconciliation wants, and wiring it is fee39's
territory, scoped out of this sprint. Both are worth picking up.

---

## 00000000. Fee module fee42b — SPV carry BUILT; THREE items owed (2026-09-02)

`111/119 PASS, 8 FIND, 0 FAIL, 0 BLOCKED` —
`apps/api/scripts/verify_fee42b.py`. Nothing here is blocked on anything
outside the codebase. Recorded because it CLOSES the gap the event-emission
entry below opened, and OPENS three tracked follow-ups.

**What closed.** `spv_realization` now has its first real subscriber.
`apps/api/fixtures/spv_realization_carry_proposal.bpmn` + the
`spv_carry.propose_from_realization` registry action turn a posted `dist_gain`
into a DRAFT `spv_carry_run` with one priced line per allocated investor, with
no human in the loop and no path past DRAFT. `services/spv_carry.py` is the
pure four-tier waterfall (zero DB access, fee35's discipline);
`services/spv_carry_runs.py` is the DB half and the DRAFT → PREVIEW →
ADVISOR_APPROVED → COMPLIANCE_APPROVED → POSTED lifecycle, maker-checkered
through `assistant_activities` exactly as fee36 does it.

**A trigger row must still be created per org.** The verify script builds its
own `workflow_definitions`/`workflow_versions`/`workflow_triggers` fixture and
tears it down. **No production trigger row for `event_type='spv_realization'`
exists yet** — the BPMN and the action are shipped, the subscription is one
row, and creating it is a deliberate operational decision, not a code change.

### The three items owed

1. **A line can still be ADDED to a POSTED run.** Both deployed immutability
   triggers fire `BEFORE DELETE OR UPDATE` only; nothing covers INSERT.
   Measured live, not inferred (`verify_fee42b.py` FIND F9). A POSTED run's
   existing lines genuinely cannot be altered or removed (checks 7a–7e).
   Closing this is a Part-1 schema change — a `BEFORE INSERT` trigger on
   `spv_carry_run_lines` checking the parent run's status — and was outside
   this sprint's applied SQL.

2. **`v_capital_accounts` cannot supply cumulative capital, and structurally
   will not until GL posting ships.** It groups by
   `journal_lines.dim_member_series_id`: no `dim_member_series` table exists
   (nor any `dim_*` table), the column has no FK, it is NULL in every deployed
   row, and the view's own `WHERE` requires it NOT NULL — so it returns zero
   rows. Even populated there is no join path from that id to an SPV investor
   entity, and it also keys on `journal_entries.vehicle_id` while every
   deployed SPV has `vehicle_entity_id` NULL. fee42b reads the POSTED
   `spv_transaction_allocations` instead (not a second balance table — the
   actual transactions), and `spv_carry_runs.capital_account_probe` re-measures
   the view on every proposal so this finding goes stale visibly rather than
   silently. Depends on open question #3 (fee43).

3. **The preferred-return accrual convention is the simple one, deliberately.**
   `preferred_return_owed = hurdle_pct × cumulative_paid_in`, cumulative and
   NON-COMPOUNDING, not time-weighted — named in every `calc_detail` as
   `PREF_CONVENTION`. A time-weighted IRR-style accrual needs dated flows AND a
   compounding convention nobody has specified. `compute_carry` takes an
   explicit `preferred_return_owed` override so settling it later replaces one
   argument, not the waterfall.

### Two things worth knowing before extending this

**HARD vs SOFT was undefined anywhere in this repo and is now defined in one
place.** SOFT = the GP catches up on the WHOLE preferred return once the hurdle
clears (a *timing* preference); HARD = no catch-up tier at all, the GP carries
only above the hurdle (an *economic* preference, and strictly cheaper for the
LP). Stated once in `services/spv_carry.py` and proved in both directions on
one-field-apart fixtures.

**WHOLE_FUND is refused where it would be wrong, not approximated.**
`spv_transactions` carries no investment/position reference, so an SPV has no
grain below itself and on a standalone vehicle DEAL_BY_DEAL and WHOLE_FUND are
the same rows. On an `investment_series`/`member_series` vehicle under a
`master_entity_id` they are not, and no master-level rollup is deployed — that
case raises `WholeFundScopeError`.

---

## 0000000. Platform — domain event emission BUILT; nothing blocked (2026-08-31)

`54/55 PASS, 1 FIND, 0 FAIL, 0 BLOCKED` —
`apps/api/scripts/verify_event_emission.py`. Nothing here is blocked on
anything outside the codebase. Recorded because it CLOSES a standing
cross-module dependency and OPENS one new, deliberate gap.

**What closed.** `workflow_triggers` has carried `trigger_type='event'` since
the Workflow Manager shipped, and until now exactly one hard-wired publisher
existed (`services/chancery_workflow_bridge.py`, for `document_confirmed`).
`services/domain_events.py::publish_event` is now the generic publish side:
any code can record a fact and every active, matching trigger in that org gets
its own `workflow_runs` row and its own `domain_event_deliveries` audit row.
Adding a new event type requires no change to that module.

**The definition→version gap is resolved, not worked around.**
`workflow_triggers.workflow_definition_id` → `workflow_runs.workflow_version_id`
resolves via `workflow_versions.is_current = true`, scoped to
`(workflow_definition_id, org_id)`. That is the ONE mechanism already used by
both existing run-starters (`workflow_scheduler.load_due_candidates` and
`chancery_workflow_bridge`); this sprint reuses it rather than adding a second.
A trigger whose definition has no current version now produces a **`FAILED`
delivery naming that definition**, not a silent skip — verified with the broken
trigger deliberately ordered FIRST so the healthy one proves it was not aborted.

**The first emitter is SPV realization.** `services/spv_events.py` publishes
`spv_realization` from `spv_allocation.post_transaction` — the single writer of
`status='posted'` in the codebase, so every posting path emits and none of them
has to remember to. Payload carries per-investor
`spv_transaction_allocations` amounts as exact `Decimal`-valued strings,
because carry is owed per investor and any consumer re-deriving the split is a
mispayment waiting to happen.

### The one thing worth knowing before extending this

**Realization is matched on `transaction_types`' own accounting flags, not on
`code = 'dist_gain'`**: `category = 'distribution' AND performance_impact =
'gain'`. Both halves are load-bearing — `sell` also carries
`performance_impact='gain'` and is excluded only by `category='transfer'`. On
the live catalogue this resolves to exactly `['dist_gain']`, asserted by the
verify script so a future catalogue change that silently widens or narrows it
fails loudly. This follows `services/portfolio_commitments.py`'s standing rule
that these flag values are read, never re-derived.

### NEW, DELIBERATE GAP — nothing subscribes to `spv_realization` yet

**PARTLY CLOSED by fee42b (2026-09-02) — see the entry above.** The subscriber
now exists in code: the BPMN, the `spv_carry.propose_from_realization` registry
action and the whole carry engine shipped, and the end-to-end is proved against
a real posted `dist_gain`. What remains is operational, not a code gap — **no
production `workflow_triggers` row for `event_type='spv_realization'` has been
created**, so a posted `dist_gain` still writes its `domain_events` row and
fans out to nobody until somebody inserts that one row. The original reading
below still holds for that interval.

The mechanism is proven end-to-end against fixture triggers. Until a production
trigger row lands, a posted `dist_gain` writes its `domain_events` row — the
table is append-only and retains events with no subscriber, by design — and
fans out to nobody. That is the correct state, not a defect: a trigger added
later still finds the history intact.

---

## 000000. Fee module fee42 — SPV fee terms BUILT; FOUR items owed (2026-08-31)

`86/89 PASS, 3 FIND, 0 FAIL, 0 BLOCKED` — `apps/api/scripts/verify_fee42.py`.
`services/spv_fee_terms.py` is the module; `scripts/seed_fee42_backfill.py` is
the one-time migration and it has been RUN (1 SPV migrated). Nothing in fee42
is blocked on anything outside the codebase.

**The sprint's own premise was wrong, and this is the most important thing to
carry forward.** fee42's brief said fee36's `SPV_MGMT_FEE_OFFSET` basis
resolution reads `spvs.mgmt_fee_pct` and should be re-pointed at
`spv_fee_terms`. It does not, and never did.
`fee_run_inputs.resolve_credit_basis` resolves the basis from
`spv_transaction_allocations.allocated_amount` over POSTED `call_mgmt_fee`
transactions — an amount actually charged, not a rate. There was nothing to
re-point and **no fee36 code was modified.** `spv_fee_terms` is the truth about
what an SPV *will* charge; `spv_transaction_allocations` remains the truth about
what it *did*. Do not conflate them in a later sprint.

**Owed, in priority order:**

1. **`spv_fee_side_letters` has ZERO check constraints and no uniqueness index.**
   `overrides` is unconstrained jsonb, so the database will store an override
   that violates the invariants `spv_fee_terms`' own CHECKs enforce (e.g.
   `carry_pct` with no `hurdle_type`), and two overlapping active letters for
   one `(spv_id, entity_id)` are reachable. Both are closed in the application
   layer only (`apply_overrides` validates the MERGED row;
   `load_side_letter` raises `AmbiguousSideLetterError`). A partial unique index
   on `(org_id, spv_id, entity_id) WHERE system_to IS NULL` and a CHECK on
   `effective_to > effective_from` are the schema-level fixes.

2. **No wound-down status exists for an SPV.** The deployed vocabulary
   (`routers/spv.py`'s `SPV_STATUS_TRANSITIONS`) is
   `forming → open → closing → closed` plus `cancelled`. `closed` means the
   RAISE closed, which is when a management fee STARTS — so a fund sits at
   `closed` for its whole life and there is no way to say it has finished.
   `mgmt_fee_term_years` is currently the only thing that stops the clock.

3. **`mgmt_fee_basis`: only two of four are computable.** `COMMITTED`
   (`spv_subscriptions.commitment_amount`) and `FUNDED` (`funded_amount`, which
   is `0.00` on every deployed row today, so a FUNDED fee currently bills
   nothing). `NAV`'s path exists — Portfolio D's
   `portfolio.spv_derived_positions` → `portfolio.assets.internal_spv_id` →
   `portfolio.valuations` — but zero assets carry `internal_spv_id` and
   `portfolio.valuations` is empty, so it resolves to nothing.
   `INVESTED_COST` has no source at all: it would have to be summed from
   `spv_transactions`, whose `txn_type` has no CHECK constraint. fee42 stores
   and resolves the basis; it deliberately does not compute the basis AMOUNT.

4. **`offsets_advisory_fee` is a boolean; `fee_credits.offset_pct` is a
   fraction.** The boolean cannot supply the fraction, so
   `ensure_advisory_fee_offset_credit` takes `offset_pct` explicitly, defaulting
   to a FULL offset. A PARTIAL offset is a real term-sheet clause with nowhere
   to live in `spv_fee_terms` today. Related: fee34 shipped `fee_credits` and
   `validate_credit` but **no service or router ever inserted a credit** — this
   sprint's function is the first application write path the table has had.

**Carry is deliberately out of scope and stays that way.** fee42 stores carry
TERMS (`carry_pct`, `hurdle_pct`, `hurdle_type`, `catchup_pct`, `carry_basis`,
`clawback_applies`) so a future waterfall sprint has real data to read. It
computes no distribution. An active SPV with a known `carry_pct` and an unknown
`hurdle_type` is deliberately NOT backfilled — `SKIPPED_NEEDS_HURDLE`, for a
human to read the LPA, because `'NONE'` asserts the deal has no preferred return
and no deployed data supports that claim.

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

**Status: complete.** `docs/TA_MODEL_INTEGRATION_BRIEF.md` has the full design
writeup. In short: three pure-function modules
(`services/ta_model.py`, `services/ta_config.py`, `services/ta_calibrate.py`)
implement a Takahashi-Alexander PE cash-flow projection model with 8 seeded
strategy defaults and a frequency-aware minimum-history floor for calibration
(3 years of history required, converted to periods at the series' own
frequency — 12 quarters, not a flat 3). `services/ta_params.py` and two new
bi-temporal/append-only tables (`portfolio.ta_model_params`,
`portfolio.ta_calibration_results`, `docs/tamodel1_part1.sql`) persist
parameter overrides and calibration runs — never projected cash flows
themselves, which are computed at read time only. Five endpoints under
`/api/v1/modeling/ta/*` and `/api/v1/admin/modeling/ta/defaults`
(`routers/modeling_ta.py`).

The sprint prompt that requested this work asserted the three modules and an
integration brief already existed, "verified standalone (93/93)". Neither
existed anywhere in this repo or its git history — see the brief's own
opening section for the full discovery writeup. This sprint built all of it
for the first time rather than treating the false premise as blocking.

**Verification:** `apps/api/scripts/verify_tamodel1.py` — **77 PASS, 0 FAIL,
0 BLOCKED**, run against the real deployed database (Doppler-hydrated
credentials — see §2 below) through the real ASGI app, including a real
commitment's real data projected end-to-end, non-persistence of projected
cash flows proven by row-count before/after, a bi-temporal override
restatement proven by a closed `valid_to` on the superseded row, the
frequency-aware floor proven both ways (refuses 3 quarters, accepts 3 years)
through the real `/calibrate` endpoint, cross-org isolation, and the
view/write permission split on the admin endpoint. Three real bugs were
found and fixed during this sprint's own verification (not left as known
issues): a `Decimal` read back from Postgres for a round number can carry a
positive exponent and render as scientific notation (`"3.5E+5"`) through a
bare `str()` — fixed with fixed-point formatting in `ta_model.py`; a raw SQL
query in `ta_params.py` had a parameter-numbering gap (`$4` referenced with
no `$3` in the query text) that asyncpg cannot bind; and `GET
/modeling/ta/defaults` returned `None` for an org that had never been
explicitly seeded, because the 4 new settings keys were never added to
`org_settings.DEFAULT_SETTINGS`'s own fallback.

**Sprint 2 (admin settings UX) — complete.** A DataGrid + right-pane screen
at `/admin/modeling/ta` (`TaSettingsScreen.jsx`, gated `GATE_ORG_OR_SUPER_
ADMIN` in the nav — same tier as Organization settings) editing the 8
strategy defaults and the 3 platform-level settings, built on the real
Workflow-Triggers-style permission envelope (`permissions.can_write`, no
client fallback — a pattern `OrgSettingsEditor.jsx` still lacks, left
unfixed as out of scope). Two real backend gaps found and fixed in
`routers/modeling_ta.py` / `services/ta_config.py`:

1. Neither GET nor PUT published any signal for which of the 8 strategies an
   org had actually overridden vs. inherited from the seed — added
   `ta_config.strategy_overrides`, a real per-strategy Decimal-value
   comparison (the underlying org_settings row is ONE blob for all 8, so
   row-existence alone cannot answer this at strategy granularity).
2. **A real clobber bug**: PUT wrote `body.values` straight through with no
   merge step, so an admin editing just one strategy through the new screen
   would have silently discarded every other strategy's prior override.
   Fixed: the router now merges a partial per-strategy submission into the
   org's existing blob before writing — the reason this sprint is
   `.structural`, not `.lowrisk`, despite being "just a UI sprint" on paper.

A new read-only endpoint, `GET /modeling/ta/calibration-floor`, lets the
screen show the real, frequency-aware minimum-calibration-periods
requirement as an admin edits `periods_per_year`, by calling
`ta_calibrate.minimum_realized_periods` itself rather than re-deriving it in
the browser.

**Verification:** `apps/api/scripts/verify_tamodel2.py` — **31 PASS, 0 FAIL,
0 BLOCKED**, including a reproduction of the clobber bug's precondition and
proof of the fix, a real 400 confirmed as a plain string (verbatim-
renderable), view-only checked independently at both the API (403) and the
component source (every write control behind an unfallback-able `canWrite`
gate), cross-org isolation on both the settings values and the new
`strategy_overrides` signal, and `npm run build` exiting 0 with the new
routes present in the build output. `verify_tamodel1.py` re-run clean at
77/77 after this sprint's backend changes (no regression).

**Sprint 3 (commitment projection UX) — complete.** The member/staff-facing
projection view, at `/portfolio/commitments/[commitmentId]`
(`CommitmentProjectionScreen.jsx`), reached via a minimal id-lookup form
(`/portfolio/commitments`) rather than a tab on an existing screen — no
commitments list/detail screen, and no general list-commitments backend
endpoint, existed anywhere before this sprint (`services/portfolio_
commitments.py` had only `get_commitment` by id, `create_commitment`, and
`tax_chase_list` by tax year). Read-only: a real, saved projection (chart +
by-period table, both driven from the same API response) plus a live "what
if" panel against the real, non-persisting preview endpoint, clearly labeled
as an unsaved preview. Reuses `view_portfolio` verbatim — no new permission.
No charting dependency was added (`apps/web/package.json` has none); the
chart is a small inline SVG component. `lib/decimalString.js` formats every
monetary/rate value by string manipulation only (digit-grouping, decimal-
point shift) — no `Number()`/`parseFloat()` anywhere in the display path.

One real, additive backend fix: `GET /modeling/ta/projection/{commitment_id}`
now also publishes `committed_capital`/`called_to_date`/`distributed_to_date`
(already computed in the handler, never previously returned) — without them
the preview tool had no way to seed `committed_capital`, a required field on
`POST /projection/preview`.

**Verification:** `apps/api/scripts/verify_tamodel3.py` — **22 PASS, 0 FAIL,
0 BLOCKED**, including a real commitment's real projection end-to-end (chart
and table proven consistent by construction — both driven from the same
`periods` array), the preview tool proving a measurably different result for
a changed `bow_factor` (a uniform scale on the distribution ramp — NOT a
deferral, corrected from this sprint's own initial, wrong assumption once
measured against the real endpoint), preview non-persistence via a real
row-count check, permission enforcement proven with a REAL zero-permission
role grant (not a zero-roles fixture, which would default-allow and make the
refusal vacuous), cross-org isolation (404), an executed (not merely
grepped) proof that the exact money formatter preserves digits a JS `Number`
would corrupt, and `npm run build` exiting 0.

**Next:** Sprint 4 (calibration UX + obligation ledger integration).
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

## scripts/refresh_schema.py — stale-DATABASE_URL bug, fixed at the root

Fixed in the same commit that added revenue_events/v_profitability_events
to the schema snapshot (34d9a7e). The script previously read DATABASE_URL
straight out of apps/api/.env, a file that has repeatedly gone stale
(password rejected by Postgres) — documented by its own commit message
as having bitten this repo three separate times (fee34's manual
DB_PASSWORD substitution, fee36's refresh confusion, and this one).

Now resolves via apps/api/scripts/_db_connect.admin_dsn() — the same
probe-before-use resolver every verify script already relies on. It
actually opens a connection to confirm a candidate DSN works before
using it, rather than trusting that a variable's presence means it's
correct. Fails loud with the provenance chain on failure; prints which
source actually worked on success.

No further workaround (manual DB_PASSWORD substitution, a separate
_run_refresh_schema.py helper, etc.) should be needed going forward —
if stale-DATABASE_URL symptoms recur after this fix, that's a signal
something upstream of resolve_dsn itself has changed, not a reason to
re-introduce a bespoke substitution.

## v_capital_accounts is structurally broken (found by fee42b)

Returns ZERO rows unconditionally. It groups on
journal_lines.dim_member_series_id, a column with no backing table, no
FK, and NULL on every deployed row — while the view's own WHERE clause
requires that column NOT NULL. It also keys on journal_entries.vehicle_id,
and every deployed SPV has vehicle_entity_id NULL. Fixed nowhere yet;
fee42b worked around it by reading cumulative investor figures directly
from posted spv_transaction_allocations instead of this view.

**UPDATE 2026-09-03 — fee43 shipped GL posting and did NOT fix this.**
Measured, not assumed: every posting template fee43 added declares
`dimension_source='none'`, so no posting path populates
`dim_member_series_id`. It cannot, because there is still no
`dim_member_series` table for that id to reference — populating it would be
inventing a key. The earlier expectation on this line ("real fix requires
fee43's GL posting work") turned out to be wrong: this view's brokenness is a
DIFFERENT dimension than the vehicle-routing problem fee43 solved, and closing
open question #3 did nothing for it.

The real fix is its own piece of work: either create the missing
`dim_member_series` table and have a posting path populate it, or rewrite the
view to key on something that exists. Until then, do not build anything else
against v_capital_accounts expecting real data — check for yourself first, this
note is not a substitute for re-verifying.
