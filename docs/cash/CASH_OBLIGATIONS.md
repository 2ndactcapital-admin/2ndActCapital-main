# Cash obligations and coverage (CASH01)

Read-only on money. Code: `services/cash_obligations.py` (the ledger and the one merged read),
`services/cash_coverage.py` (coverage, minimums, alerts), `routers/cash.py` (8 routes), schema `docs/cash01_part1.sql`.
Screens: Household → Treasury (`/portfolio/households/<id>?tab=treasury`) and `/treasury` (a member's own family).
Discovery and reuse decisions: `CASH00_DISCOVERY.md`.

## Sources

| Source type | Stored or derived | From | Certainty |
|---|---|---|---|
| `capital_call_projected` | DERIVED (never stored; the table's CHECK refuses it) | TA model on `portfolio.commitments` (override params) | estimated |
| `capital_call_noticed` | stored | a notice, matched to a commitment (named, or the payer's ONE live commitment with enough unfunded); unmatched → a to-do | firm |
| `debt_service` | stored schedule (`source_ref` = liability lineage) OR derived interest-only | `portfolio.liabilities` | firm / estimated |
| `trust_payment` | derived from CRM (GRAT annuity, CRUT unitrust) or stored | `detail.*` fields | firm / estimated |
| `insurance_premium` | derived from the ILIT policy fields or stored | `detail.policy_premium_*` | firm |
| `charitable_payout` | derived (min distribution % × value) or stored | `detail.min_distribution_pct`, `distribution_due` | estimated |
| `tax_estimate`, `property_carrying`, `tuition`, `lifestyle_recurring`, `one_off` | stored (manual) | Treasury form / operator loader | as entered |
| `vendor_invoice` | stored, linked to a Chancery `documents` row | — | as entered |

**Every line carries:**

- date, amount (Decimal; text at the JSON boundary) and currency;
- certainty and source type;
- the payer entity and an as-of;
- an explanation, `derived` / `derived_from`, and `counted`.

Anything that cannot be dated or priced is listed under **not estimated — needs <x>** and is never a guessed number.

**Rules.**

- **Projection supersession.** A notice matched to a commitment supersedes that commitment's projected line NEAREST
  its date. The projected line stays in the list (`counted = false`, `superseded_by` = the notice lineage), so the
  call is counted once.
- **A stored row wins** over the derived line of the same kind for the same entity (a debt schedule for its
  liability; a manual GRAT / ILIT / foundation row).
- **SPV assets.** Commitments on SPV assets are excluded from projection: the SPV ledger is their book (CASH00 1a).

## Recurrence

An RRULE subset on the stored row, expanded at read time over the horizon (default 24 months, 1–120), never stored as
rows:

- `FREQ=MONTHLY|YEARLY|WEEKLY`;
- `INTERVAL`, `COUNT`, `UNTIL`;
- `BYMONTH` (with YEARLY; e.g. estimated taxes `FREQ=YEARLY;BYMONTH=1,4,6,9`).

Dates clamp from the anchor (`due_date`):

- Jan 31 monthly → Feb 28/29, Mar 31, Apr 30.
- Feb 29 yearly → Feb 28 in a common year, Feb 29 in a leap year.

`end_date`, or ending the obligation, stops it.

## Bi-temporal

Valid axis: nothing references an obligation's id (a to-do keys on `lineage_id`).

- **Revise.** Close the live row (`valid_to = now()`), insert the merged row on the same `lineage_id`. The merged row
  is validated.
- **End.** Close it with an `end_reason`; nothing is inserted.
- **No deletes.** Nothing is ever deleted, and demo rows are protected by the crmdetail1 delete guard.

## Coverage formulas

- **Liquid balance.** The net-worth core's Cash & Equivalents buckets (`taxonomy_mc_8_1`,
  `taxonomy_sub_8_1_<n>`):
  - an entity's OWN cash (`networth.figures(ctx, weights={entity: 1})`);
  - a household's household figure (`networth.figures(ctx, members)`).
  - No second formula.
  - An approved line's undrawn amount (limit = its `original_amount` − drawn) is shown SEPARATELY and never added.
- **Monthly gross outflow** = Σ counted obligations dated in [today, today + 12 months) ÷ 12.
- **Months of coverage** = liquid ÷ monthly outflow, to one decimal. With no outflow: "no obligations in the next 12
  months".
- **Covered through.** Walk the dated schedule forward (date, then key), drawing the liquid balance down. The first
  obligation the balance cannot pay in full is the **next gap** (name, ~amount, date, shortfall). Covered through =
  the day before it, or "beyond" the horizon end when there is no gap.
  - Per entity: on the entity's own balance.
  - Household: on the household total, over every reached entity's obligations at the household's share of that
    entity.
- **Flags:**
  - `below_minimum` — months < the minimum: since CASH02 the `min_coverage_months` of the scope's ACTIVE cash
    policy (`cash_policies`; an entity override, else the household's), default 6 (`cash_coverage_minimums` is retired);
  - `uncovered_90d` — the next gap within 90 days;
  - `no_payer_cash` — obligations but no cash position at all.

## Tasks and alerts

`member_todos` through `workflow_todos._upsert_todo`: one to-do per flagged subject per author (the explicit holders
of `manage_portfolio`), de-duplicated while open.

| Source | Subject |
|---|---|
| `cash_below_minimum` | entity / household |
| `cash_uncovered_90d` | entity / household |
| `cash_unmatched_notice` | the notice's lineage |

Every write route re-syncs the household, and so does POST `…/alerts/sync`. An open to-do whose subject is no longer
flagged is closed (`done`, "[Closed: resolved]", kept).

## Permissions

- **Read.** `view_portfolio` or `manage_portfolio`, then the member / visibility scope:
  - a member sees their own family only, read-only, with the estimate labels `cash.coverage_estimate` +
    `tax.estimate_label`;
  - a restricted staff member sees their families only.
- **Write.** `require_internal_permission(manage_portfolio)`. It is audited, and there is no maker-checker: an
  obligation record moves nothing.

## Operator — the Olivo demo

```bash
doppler run -- python3 apps/api/scripts/load_olivo_obligations.py --dry-run          # lists rows + coverage, writes nothing
doppler run -- python3 apps/api/scripts/load_olivo_obligations.py --operator-email <you>   # writes as the operator; idempotent
```

The source is `docs/demo/olivo/data/obligations.csv` (18 rows; the foundation payout is `pct_of_value:5` of its core
value at load time). The loader keys each row with `external_key = demo-olivo:<key>`: a re-run skips identical rows
and revises changed ones.
