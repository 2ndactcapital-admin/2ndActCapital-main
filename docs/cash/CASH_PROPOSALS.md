# Cash proposals, approval → instructions, and the maturity waterfall (CASH04)

A cash proposal is a **trade list for securities**, for ONE account, that moves today's cash / sleeve / ladder holdings
toward the targets of the ACTIVE cash policy version (cash02) and its ladder and instrument plan (cash03). It is a KIND of
Replication Studio proposal — `rebalance_proposals.proposal_kind = 'cash'` — so its lifecycle, screens, maker-checker
and route to execution instructions are rep5's, unchanged. Nothing here moves money between accounts (cash07).

- Code: `services/cash_proposals.py` (builder + writes + the panel), `services/cash_waterfall.py` (the job),
  `routers/cash.py` (2 routes + the `proposals` block of the Treasury envelope).
- Additive edits: `rebalance_proposals` (labels, `_trade_line` reads a cash leg's limit, the cash-only trigger refused by
  `generate`), `authority.TRIGGERS` (+ `cash_waterfall`), `workflow_autonomy.VERB_AUTONOMY`, `sod.VERB_DUTIES`,
  `security_master_jobs.JOBS`, `execution_letter` (par wording), `cash_instruments.scope_plans` (cash03's plan loop,
  moved).
- Catalog: `docs/cash04_part1.sql` (APPLIED). Screens: Household → Treasury → **Proposed trades**
  (`components/treasury/ProposedTradesPanel.jsx`, `lib/cashProposals.mjs`).
- Discovery and decisions: `CASH04_NOTES.md`.

## Builder rules

| # | Rule |
|---|---|
| 1 Targets | Per account: cash03's per-account sleeve value (the sleeve target × the account's cash-balance share). An account in an entity override's scope is proposed from THAT policy, every other account from the household's. An account whose share is "not allocated" gets no proposal (the reason is shown). |
| 2 Holdings | The account's own valuation (`account_models.load_holdings`). A **money-market fund** = a security with an N-MFP `mmf_category`; a **Treasury** = `treasury_type` Bill / Note with a `maturity_date`. Everything else is **non-cash: listed untouched, never sold**. |
| 3 Sleeve of a holding | A Treasury counts in the sleeve its maturity falls in (cash02's `windows`, half-open months), **at par**. A fund counts at its value: it fills Near-term up to what the bills there leave of the target; the rest is Reserve. A Treasury past maturity counts as cash at par (pending redemption). |
| 4 Sells | What is over target is sold in **withdrawal order** (v1 §2.5): Near-term, then Reserve, then Long-term. Inside a sleeve: the fund first (same day), then the bill / rung **closest to maturity first**. Every early sale of a bill / note carries **"early sale before maturity — may lose principal"**. |
| 5 Buys | What is under target is bought Near-term → Reserve → Long-term with the **available cash** = cash + estimated sale proceeds + matured par. Near-term / Reserve buy the instrument cash03 chose for that sleeve × account (a fund, or the Reserve T-bill). Long-term buys the **ladder's rungs**: the household rung × the account's Long-term share, less any par of that issue already held, maturity order. |
| 6 Shortfall | What the cash does not cover is the **shortfall** — shown (per sleeve and in total), **never borrowed**, never taken from a non-cash holding. |
| 7 Orders | A fund: buy by **notional** at market; sell by **shares** at the account's own price. A bill / note: by **par** (USD face, $100 increments) with a **limit per 100 of par**. A bill's buy limit = `100 ÷ (1 + y × days ÷ 365)` from the yield the plan used, rounded down. A note's buy limit = 100.0000 (its coupon is not recorded, so the limit never pays above par). A sale's limit = the account's own valuation, rounded up. |
| 8 Indicative | A rung or bill with no CUSIP in the security master is **not bought** — "needs an issue". No instruction ever names an indicative security. |
| 9 Minimum | Lines under `replication.proposals.min_trade_size` (default $100) are left out and noted. |
| 10 After-tax effect | The value-weighted after-tax yield of the buys (net where the owner has no approved tax profile) and the estimated annual after-tax income. |

Every line carries: side, security, quantity (par / shares) or notional, price kind and limit, the sleeve, the rung and
the obligations it funds, the reason, any warning, and the estimated net / after-tax yield (with basis and as-of). The
proposal's `trigger_ref` snapshots the policy version, the targets, holdings by sleeve, the rungs (cash03 D3), the
shortfall and the plan's fingerprint.

## Approval → instructions (rep5's path, unchanged)

```
POST /api/v1/cash/households/{id}/proposals        (manage_proposals)  a DRAFT per account, maker = the caller
POST /api/v1/proposals/{id}/submit                 (manage_proposals)  the maker → awaiting approval
                                                                       (task rebalance_proposal_approval)
POST /api/v1/proposals/{id}/approve                (approve_proposals) a DIFFERENT person — services.sod
                                                                       item rebalance_proposal; a refusal is recorded
 └─ rebalance_proposals.execute → custody.trading.route_trade_list → execution.create_instructions(routed_via=…)
    instructions start APPROVED (the proposal's maker-checker is the approval; _verify_upstream re-reads it)
/portfolio/instructions/{id}                                           mark sent, fills, reconcile — rep2, as today
```

- **Routing by custodian**: `route_trade_list`. Held-away → refused. Advise / other custodian → manual. No adapter for
  the custodian → manual. Altruist → manual while `equity_trading_api` is not an evidenced 'yes', and it says so.
  - `execution_routers.route_refusal` is honoured: `manual` is always offered; `fix` is shown refused with its reason.
  - Nothing is ever sent to a custodian.
- **Authority**: the account must be MANAGED (rep7b `authority.refusal`, paths `proposal:manual` /
  `proposal:cash_waterfall`). A held-away or advise account is refused with the reason, at generation and again at
  routing.
- **One open proposal per account**: a cash proposal supersedes only an older cash draft / pending one. An open model /
  one-off / options proposal blocks it.
- **The manual sheet** is `execution_letter.letter_rows` (the one renderer). It prints a bill / note as "par (USD face
  value)" with "Limit … per 100 of par".
- `execution_instructions`' CHECKs are untouched: a cash line is buy / sell, exactly one of quantity / notional, market
  / limit.

## The maturity waterfall (v1 §2.4)

A platform job on the workflow engine, `cash_maturity_waterfall` ("system:Cash — maturity waterfall", the
`cash.maturity_waterfall` service task), in the Hollisworks platform org — the way secmaster1's and S28's jobs run.

**Schedule.** Weekdays 07:15 New York. Per household with an ACTIVE household cash policy, per account, the PERIOD is:

- the month (`2026-11`) when the account has no waterfall row for it yet — the monthly run;
- else `maturity:<date>` when a rung matured since the last run;
- else nothing.

**What it drafts.**

1. The matured proceeds **top up any sleeve below target, Near-term first** (then Reserve).
2. The remainder is **reinvested as a new rung**. It goes on the rung of the next **uncovered** Long-term obligation (one
   no held rung covers), else at the **ladder's end**.
3. **Earnings** (dividend / interest transactions since the last run) are reinvested **pro rata to the target
   allocation**. The Long-term share joins the new rung.

**Output.** One **SYSTEM DRAFT** cash proposal per account (trigger `cash_waterfall`, no task) and **one review task**
per household (`cash_waterfall_review`, de-duplicated while open). It is idempotent per (account, period), backed by
`rebalance_proposals_cash_waterfall_uq`. Each household is audited `cash.waterfall.household`; history, holds and alerts
are the engine's run rows.

**S29b — draft only.**

- `cash.maturity_waterfall` is `eligible` + `platform_job` + `draft_only`. Its only write is `write_draft`, which asserts
  a system draft with no task.
- The verbs that would submit or route (`cash.proposal_submit`, `cash.proposal_route`) are `never` (trades) and are not
  registered actions.

**Adoption.** A PERSON adopts a draft (`POST /api/v1/cash/proposals/{id}/adopt`, manage_proposals). That creates a new
proposal with the same lines, that person its maker, supersedes the draft and is submitted — a DIFFERENT person approves
it.

## Permissions and audit

| Action | Permission |
|---|---|
| See the Proposed-trades panel | `view_proposals` (staff; a member's envelope is unchanged — no trade detail) |
| Generate / adopt | `require_internal_permission(manage_proposals)` + the visibility rule |
| Submit / execute | `manage_proposals` (existing routes) |
| Approve | `approve_proposals` (existing route; SoD: a different person) |
| Instructions | `manage_execution` / `approve_execution` (rep2, as today) |
| Switch the job on | Super Admin, Admin → Security master (BUGFIX4's switch) |

Audited: `proposal.generated` / `submitted` / `approved` / `routed` (rep5), `cash.proposals.generated` (per household),
`cash.waterfall.household`, `sod.refused` / `sod_decisions`, the instruction events.

## Operator steps

```bash
python3 apps/api/scripts/_apply_cash04.py --all                                  # done by the sprint (SCHEMA OK)
doppler run -- python3 apps/api/scripts/_apply_cash04.py --seed-jobs            # the waterfall job, trigger INACTIVE
# switch it on: Admin → Security master → "Cash — maturity waterfall" → On (Super Admin)
doppler run -- python3 apps/api/scripts/olivo_cash_proposal.py --dry-run        # the demo family's proposal + sheet
doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_cash04.py       # written, not run by the sprint
```

`olivo_cash_proposal.py --dry-run` needs the Olivo household's cash policy APPROVED (cash02's step). Without it, it
prints "no ACTIVE cash policy" and exits 0. It never writes and never routes.
