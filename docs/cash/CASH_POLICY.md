# Cash policies and the three sleeves (CASH02)

A cash policy is a **target**, never a trade: nothing here creates a proposal, an order or an instruction (cash04).
Code: `services/cash_policy.py` (pure functions + the maker-checker writes), `routers/cash.py` (5 routes), schema
`docs/cash02_part1.sql`. Screens: Household → Treasury (`/portfolio/households/<id>?tab=treasury`, the cash01 tab) and
`/treasury` (a member's own family, read-only). Discovery and reuse: `CASH02_NOTES.md`.

## Scope

- **Household** — the default. Sized on the household's share-weighted obligation lines and its Cash & Equivalents
  (`cash_coverage.scope_lines` / `scope_liquid`, the same figures cash01's coverage walks).
- **Entity override** — an entity that pays its own obligations (an ILIT, a property LLC). Sized on that entity's own
  lines and its own cash.
- One ACTIVE policy per scope and one OPEN version (draft or awaiting approval) per scope — both enforced by unique
  indexes.

## The fields

| Field | Meaning | Bounds |
|---|---|---|
| `near_months` (N) | the Near-term / Reserve boundary | 0 ≤ N ≤ R |
| `reserve_months` (R) | the Reserve / Long-term boundary | R ≤ 120 |
| `horizon_months` | where Long-term ends | 1–120 (default 24) |
| `rung_count` | ladder rungs (cash03 builds the ladder) | 1–52 (default 8) |
| `buffer_months` | extra Near-term, in months of average outflow | 0–12 (default 0) |
| `objective_score` / `objective` | 1 most stable … 5 most yield; 1–2 stability, 3 balanced, 4–5 yield | the DB CHECKs they agree |
| `preset` | stay_liquid / balanced / recommended / custom | |
| `min_coverage_months` | the minimum coverage — **its ONE home** (cash01's `cash_coverage_minimums` is retired) | 0–120 |

## Sleeve windows

Months from today, **half-open**; month *m* starts on `cash_obligations.add_months(today, m)` (the ledger's own
clamped month arithmetic):

| Sleeve | Window |
|---|---|
| Near-term | [0, N) |
| Reserve | [N, R) |
| Long-term | [R, horizon) |

- An obligation exactly **on** a boundary belongs to the **later** sleeve (an obligation dated today + 3 months, with
  N = 3, is Reserve).
- Each end is clipped to the horizon. A horizon shorter than R cuts Reserve at the horizon and leaves Long-term empty;
  each window says so in its `note`.

## Sleeve targets (derived, never typed, never stored)

- **Target** = Σ counted obligations dated in the window (the cash01 merged schedule: stored + TA-projected +
  debt service + CRM payments; a superseded projection is not counted).
- **Near-term buffer** = `buffer_months` × the average monthly gross outflow (`cash_coverage.monthly_outflow`: the next
  12 months ÷ 12), added to Near-term.
- **% of the liquid total** = target ÷ liquid × 100, one decimal, half-up. No cash position → "not computed — no cash
  position".
- **Unallocated** = liquid − Σ targets (negative = short).
- **Coverage vs minimum** = `cash_coverage.months_of_coverage` against `min_coverage_months`.
- Every view carries `as_of` (today) and its `source`. Money is Decimal, fixed-point text at the JSON boundary.

## Presets

| Preset | Boundaries (the server applies them; stored N / R always equal the mapping) |
|---|---|
| Stay liquid | N = R = horizon (everything Near-term) |
| Balanced | N = 0, R = horizon (everything Reserve; a buffer stays Near-term) |
| Recommended | from the questionnaire's objective (below) |
| Custom | the boundaries as entered |

Each preset is shown with its derived sleeves and its blended estimated yield.

## Questionnaire (3 steps)

1. **Outflows.** Prefilled with the scheduled next-12-month outflow. The cross-check is the trailing 3 months of
   ACTUAL outflows — capital calls and fees paid (`portfolio.transactions`, `transaction_types.category IN ('call',
   'fee')`), at each owner's household share — annualised × 4.
   - Entered figure < 80 % of that → "well below" warning.
   - No transaction at all for the scope → "no outflow history".
   - Entered figure above the schedule → the unscheduled difference is named ("add it to the ledger").
   - **[FIND]** No spending / withdrawal transaction type exists, so lifestyle outflows are not seen by the cross-check.
2. **Objective** — the 5-point scale positions N and R (bounds N ∈ [1, 6], R ∈ [6, 18]; monotone):

   | Score | Label | N | R |
   |---|---|---|---|
   | 1 | Most stable | 6 | 18 |
   | 2 | More stable | 4 | 15 |
   | 3 | Balanced (v1's defaults) | 3 | 12 |
   | 4 | More yield | 2 | 9 |
   | 5 | Most yield | 1 | 6 |

3. **Recommendation** — the editor view at those boundaries. **Applying it saves a DRAFT, never an active policy.**

## Estimated yields

`services/cash_policy.YIELD_PROXIES` — ordered candidates per sleeve; the first with an observation in the 31 days to
today wins. Read through `services/market_data/read_service.read_series` (the FRED registry).

| Sleeve | Proxy | Live status (2026-11-08) |
|---|---|---|
| Near-term | `fred.dgs3mo`; else derived `fred.dgs10 − fred.t10y3m` on their latest common date | derived (no 3-month series registered) |
| Reserve | `fred.dgs6mo`, then `fred.dgs1` | **not estimated — needs fred.dgs6mo or fred.dgs1** |
| Long-term | `fred.dgs2` | 4.46, as of 2026-11-05 |

- Label: "estimate — Treasury curve proxy, as of <observation date>". Missing → "not estimated — needs <series>".
- Net = the proxy (no advisory fee on cash is configured; v1 §9 question 2 is open).
- Blended = Σ(target × yield) ÷ Σ target over the sleeves that hold money; any such sleeve unestimated → not estimated.
- **After-tax** only with an APPROVED tax profile (`services/tax_profiles`, rep6) for the policy's subject and the
  current tax year: yield × (1 − (federal ordinary + NIIT when it applies)). Treasury interest is exempt from state
  and local tax, so the profile's state / city rates are NOT applied. Without a profile: "after-tax: needs a tax
  profile". Instrument-level after-tax selection (munis vs Treasuries) is cash03.
- Disclosure label: `cash.yield_estimate` (fallback text "Estimates use the Treasury curve; actual yields differ; have
  counsel review."), beside cash01's `cash.coverage_estimate`.

Adding `fred.dgs3mo`, `fred.dgs6mo` and `fred.dgs1` to the market-data registry seed (an operator load + backfill) makes
them the first candidates with no code change.

## Approval flow (maker-checker on every save)

```
save   → DRAFT              editing an open draft keeps its id (system-axis archive) and adds the editor to maker_ids;
                            editing an ACTIVE policy creates a NEW draft version (version_no + 1)
submit → PENDING_APPROVAL   opens the human task (kind cash_policy_approval, maker_checker) for holders of
                            manage_cash_policy; its payload = the policy + its derived view as of submit
approve→ ACTIVE             a DIFFERENT eligible person (services.sod item type cash_policy: every maker —
                            drafter, editors, submitter — excluded); the prior active version → RETIRED
                            (valid_to = now, superseded_by), bi-temporally
reject → REJECTED           a reason is required
```

- Refusals (a maker approving, also through bulk approve) are recorded in `sod_decisions` and audited
  (`sod.refused`); nothing changes.
- The database refuses `approved_by ∈ maker_ids` (`cash_policies_maker_checker_chk`) and a second active policy per
  scope (`cash_policies_active_*_uq`).
- Self-approval is impossible by construction (`self_approval_capable = false`; the CHECK above). The S29b floor rules
  stay on.
- The task appears in `/human-tasks/mine` and in Portfolio → Approvals (bulk type `cash_policy`).
- An approved policy's minimum moves cash01's coverage flags at once (the approve route re-syncs the to-dos).

## Permissions

`manage_cash_policy` authors and approves (SoD makes the approver a different person); reads ride the Treasury
envelope (`view_portfolio | manage_portfolio`). Granted to org_admin (also `rbac.ORG_ADMIN_DEFAULT_PERMISSIONS` for a
new org), advisor and admin by the migration. Super Admin passes first. A member sees their family's ACTIVE policies
only, read-only, with the library labels.

## Operator — the Olivo demo

```bash
doppler run -- python3 apps/api/scripts/load_olivo_cash_policy.py --dry-run                      # writes nothing
doppler run -- python3 apps/api/scripts/load_olivo_cash_policy.py --operator-email <you>          # draft + submit
```

Then a DIFFERENT holder of `manage_cash_policy` approves it in the app (Household → Treasury, or Portfolio →
Approvals). Idempotent (the draft's reason carries `demo-olivo: recommended cash policy`); a demo policy is never
deleted (`cash_policies_demo_guard_trg`).
