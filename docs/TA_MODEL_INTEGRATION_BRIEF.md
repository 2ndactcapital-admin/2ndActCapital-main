# TA Model Integration Brief

**Status as of this document's creation (Sprint 1 / "tamodel1.structural"):** this
brief did not exist before this sprint. The sprint prompt that requested Sprint 1
asserted it already existed, alongside three "already built and verified
standalone (93/93)" modules (`ta_model.py`, `ta_config.py`, `ta_calibrate.py`). A
full search of both git worktrees' history found none of that — no brief, no
modules, no prior verify script, no mention in `docs/PROJECT_STATUS.md`. That
premise was false. This document is written **now**, as part of landing the
modules for the first time, not as a record of prior work. Where the sprint
prompt referred to "the brief's own §8" or "the brief's stated lean," those
sections are written below to reflect the real decisions made in this sprint —
not reconstructed from a document that never existed.

## 1. What the TA Model is

"TA Model" = the **Takahashi-Alexander model**, a standard private-equity /
venture-capital cash-flow projection model. Given a commitment's known state —
committed capital, cumulative called-to-date, cumulative distributed-to-date,
and current NAV — it projects forward, period by period:

```
contribution(t) = RC * uncalled(t-1)
distribution(t) = RD * bow(t) * nav(t-1)
nav(t)           = nav(t-1) + contribution(t) - distribution(t) + nav(t-1) * G
```

- **RC** (`rate_of_contribution`) — the fraction of remaining uncalled capital
  drawn each period. Uncalled capital decays asymptotically toward zero, which
  is the real shape of a capital-call schedule.
- **RD** (`rate_of_distribution`) at full bow, scaled by **bow(t)** — a factor
  that ramps linearly from 0 at inception to `bow_factor` at the end of
  `fund_life_years`, producing the classic J-curve: distributions are
  near-zero early and heaviest near harvest.
- **G** (`growth_rate`) — NAV growth applied to the prior period's balance.

Eight strategies are seeded with distinct starting parameter sets, because the
J-curve shape genuinely differs by strategy: `buyout`, `growth_equity`,
`venture_capital`, `real_estate`, `real_assets`, `private_credit`,
`fund_of_funds`, `secondaries`.

## 2. The three modules

### `apps/api/services/ta_model.py`
Pure function. No DB import, no config import, no I/O of any kind — every
input (`TAParams`, `committed_capital`, `called_to_date`,
`distributed_to_date`, `current_nav`, `horizon_periods`) is supplied
explicitly by the caller. `project_cash_flows()` returns a list of `TAPeriod`,
never persisted anywhere. Decimal throughout; `float` is refused (never
coerced) at every boundary — `TAParams.__post_init__` and
`project_cash_flows`'s own argument checks. `to_json()` on both `TAParams` and
`TAPeriod` renders decimals as **fixed-point strings**, not `str(decimal)` —
see `_fixed()` and its docstring for why a bare `str()` on a DB-round-tripped
Decimal can silently emit scientific notation (`"3.5E+5"` instead of
`"350000"`) even though the two are numerically equal. This was a real bug
caught during this sprint's own end-to-end verification, not a hypothetical.

### `apps/api/services/ta_config.py`
Resolves a strategy's `TAParams` from a settings dict the **caller** already
fetched once from `org_settings` this request — this module never touches the
database itself. `DEFAULT_TA_STRATEGY_PARAMS` holds the 8 strategies' starting
parameters, expressed as **per-period rates at the default frequency**
(quarterly, `DEFAULT_PERIODS_PER_YEAR = 4`) — not annual rates relabeled. An
annual target of 11% NAV growth compounds to roughly 52%/year if applied
directly as a quarterly rate; the seeded values are the actual
compound-equivalent quarterly rate for each strategy's annual target (see the
module's own comments for the per-strategy annual targets these were derived
from).

### `apps/api/services/ta_calibrate.py`
Fits `rate_of_contribution`, `rate_of_distribution` and `growth_rate` to a
commitment's realized cash-flow history (`bow_factor`/`fund_life_years` are
**not** calibrated — see the module docstring for why). Enforces the
frequency-aware minimum-history floor (§3 below). Also pure — the caller
supplies the realized periods already queried from `portfolio.transactions`.

### `apps/api/services/ta_params.py`
The one module that touches the database. Bi-temporal persistence for
parameter overrides (`portfolio.ta_model_params`), an append-only calibration
log (`portfolio.ta_calibration_results`), and
`realized_periods_from_transactions()` — the real query that buckets a
commitment's actual `portfolio.transactions` rows into calendar periods for
calibration input.

## 3. Task 3 — the frequency-aware calibration floor

A flat minimum of 3 realized periods treats 3 quarters the same as 3 years,
which are not equivalent evidence: 3 quarters is 9 months of a fund whose life
is typically 6–12 years; 3 years is a real, multi-cycle slice of it. The floor
is expressed as a **minimum number of years** of history
(`MIN_CALIBRATION_YEARS = 3`, overridable per-org via
`modeling.ta.calibration_min_years`) and converted to a period count at the
series' own frequency: `minimum_realized_periods(periods_per_year) =
ceil(min_years * periods_per_year)`. Annual (`periods_per_year=1`): floor stays
3. Quarterly (`periods_per_year=4`): floor becomes 12 — four times as many
data points required, because each one represents a quarter of a year's
evidence rather than a whole year's. Proven both ways, through the real
`/calibrate` endpoint, in `verify_tamodel1.py` assertions 5.4 (and at the
pure-function level in assertions 1.6).

## 4. Task 2 — schema decision

A **separate table keyed by `commitment_id`**, not new columns on
`portfolio.commitments` — bi-temporal restatement of assumptions ("what did we
assume in Q2, and why did the projection change") is a real, anticipated
question, and mutating a parameter value in place would destroy the ability to
answer it.

`portfolio.ta_model_params` — one **active** row per `(org_id, commitment_id)`,
enforced by a **partial unique index**
(`WHERE valid_to IS NULL AND system_to IS NULL`) — the identical shape
CLAUDE.md documents for `member_target_allocations` (Sprint 8). A restatement
follows Rule 3: close the current row, insert the new one, in one transaction
(`services.ta_params.set_override_params`).

`portfolio.ta_calibration_results` — a separate, **append-only** log of every
calibration *run* (not the params themselves), carrying
`realized_periods_used` and `periods_per_year` so a later reader can see the
evidentiary basis a given calibration had, even after a future sprint changes
the floor.

Migration: `docs/tamodel1_part1.sql`. Both tables carry the single-policy
tenant-isolation RLS shape used across `portfolio.*`
(`org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid OR
current_setting('app.is_super_admin', true) = 'true'`, `cmd=ALL`).

### org_settings — 4 seeded keys, category `modeling`

| key | purpose |
|---|---|
| `modeling.ta.strategy_defaults` | the 8 strategies' default `TAParams`, as a JSON object |
| `modeling.ta.projection_horizon_years` | default projection horizon, in years |
| `modeling.ta.default_periods_per_year` | default frequency (quarterly = 4) |
| `modeling.ta.calibration_min_years` | the frequency-aware floor's year count |

Greenfield — confirmed no `modeling.*` category existed before this sprint
(Task 1c). There is no reusable `seed_rows()` helper anywhere in this
codebase (the sprint prompt's Task 2 instruction to seed "via `seed_rows()`"
does not match anything deployed); the real, existing precedent is
`services.org_settings.DEFAULT_SETTINGS` + `set_setting`'s own upsert, which
is what `services.ta_config.default_settings_seed()` follows. The same 4 keys
were also added to `DEFAULT_SETTINGS` itself (sourced from `ta_config`, not
duplicated as literals) — otherwise an org that has never explicitly seeded or
overridden these keys would resolve `None` from `GET /modeling/ta/defaults`
instead of a usable default. This was a real bug caught during this sprint's
own verification (see `verify_tamodel1.py`, assertion 5.5's history).

## 5. Task 1 — discovery findings

**1a. No `ta_strategy` field, and no PE-strategy taxonomy, existed anywhere.**
`docs/schema_snapshot.sql` and every seed SQL file were searched for all 8
strategy keys: zero hits. The closest existing thing,
`config.category='asset_taxonomy'`, is a generic SC/MC/Sub asset-*class* tree
(`services/taxonomy.py`), not a PE-strategy vocabulary, and
`portfolio.commitments` carries no strategy column. The 8 keys are new — a
CHECK-constraint vocabulary on the two new tables, not a column on
`commitments` itself. A commitment's strategy is therefore either supplied
explicitly (`?strategy_key=` on `GET /projection/{id}`, or the
`ta_strategy_key` body field on `/calibrate`) or resolved from whatever
override row already exists for it — there is no automatic
taxonomy-key-to-strategy mapping, because no real field to derive one from
exists yet.

**1b. Chancery does *not* source commitment financial data.**
`services/portfolio_chancery.py`'s own `COMMITMENT_EXTRACTION_GAP` constant
states plainly that no deployed Chancery extractor produces
`commitment_amount` / `called_to_date` / `distributed_to_date` /
`recallable_amount` — narrative extraction has no monetary keys, and template
extraction (`k1`) maps five income-statement boxes, not commitment figures.
The real, deployed source of commitment data is
`services.portfolio_commitments.create_commitment` /
`recompute_commitment`, against `portfolio.commitments` +
`portfolio.transactions` — that is what this sprint's endpoints and
verification use as "a real commitment's real data," since it is what is
actually deployed, not what the original sprint prompt assumed.

**1c. `org_settings` had zero `modeling.*` keys before this sprint.**
Confirmed both statically (no matching entries in `DEFAULT_SETTINGS` /
`CATEGORY_BY_PREFIX` before this sprint's edits) and by direct query against
the live database after seeding (`verify_tamodel1.py` assertion 3.1).
Genuinely greenfield.

## 6. Endpoints

```
GET  /api/v1/modeling/ta/defaults                   — open read (any org member)
PUT  /api/v1/admin/modeling/ta/defaults              — can_manage_org_settings
GET  /api/v1/modeling/ta/projection/{commitment_id}  — view_portfolio
POST /api/v1/modeling/ta/projection/preview          — view_portfolio
POST /api/v1/modeling/ta/calibrate/{commitment_id}   — manage_portfolio
```

Settings are fetched exactly once per request
(`services.org_settings.get_all_settings`, called once per handler and
threaded through) — never re-fetched mid-handler, which is the real
precedent the original sprint prompt cited (a documented `/admin/platform`
theme-caching bug elsewhere in this project). The admin write route lives
under the literal `/api/v1/admin/` prefix, confirmed against this codebase's
real router-mounting convention (`apps/api/main.py`'s ~30 `include_router`
calls, all at prefix `/api/v1`, with `/admin/...` baked into each admin
router's own route strings) rather than assumed.

The read/write permission split on the admin config-write endpoint reuses
`org_settings`'s own real pattern exactly: reads open to any authenticated org
member (no permission check — `services/org_settings.py`'s own documented
rule), writes gated by `can_manage_org_settings` (enforced inside
`services.org_settings.set_settings`, not duplicated in the router).
`GET`/`POST projection` and `POST calibrate` reuse the existing
`view_portfolio` / `manage_portfolio` pair from `services.portfolio_assets`,
the same split `portfolio_ingest.py`'s tax-chase route already uses.

`org_id` is resolved server-side via `routers.entities.get_org_id(request)`
(JWT claims) on every route, never accepted from a request body.

Projected cash flows are computed inline in the handler and returned — never
written to any table. Proven in `verify_tamodel1.py` assertion 5.2 by calling
the same projection twice and confirming the row counts of both new tables
are unchanged, and by confirming no other table under the `ta_` prefix exists.

## 7. Residual items (honest, not swept under the rug)

- **NAV-per-period in `realized_periods_from_transactions`** falls back to a
  running `cumulative_paid_in - cumulative_distributed` estimate when no
  `portfolio.valuations` row exists for a period — a documented
  simplification for this sprint's substrate, not a mark-to-market claim.
  Real valuation history, when present, is used and takes priority.
- **`calibrate_strategy`'s method is a simple per-period-ratio mean**, not a
  fitted regression. Defensible for proving the floor mechanics; a later
  sprint may replace it with a least-squares fit without changing the
  function's signature.
- **The 8-strategy-key vocabulary has no automatic source.** Per §5 (1a),
  there is no taxonomy field to derive a commitment's strategy from yet. Every
  caller (endpoint or admin) supplies it explicitly until a real mapping
  exists — a genuine, not a deferred-and-forgotten, gap.

## 8. Sequencing (Task 6)

Sprint 1 (this sprint) — substrate: the three modules, the two new tables, the
5 endpoints, the frequency-aware calibration floor, real end-to-end
verification. **Complete.**

Sprint 2 — admin UX: the settings editor for `modeling.ta.*` at
`/admin/modeling/ta`. **Complete** — 31/31 verified
(`apps/api/scripts/verify_tamodel2.py`). Built on the real permission-envelope
pattern (not the older `OrgSettingsEditor.jsx` shape, which this brief
originally pointed at — that screen turned out to lack a real
`permissions.can_write` envelope entirely; the Workflow Triggers screen was
the correct template instead). Found and fixed a real clobber bug in Sprint
1's PUT endpoint along the way: a partial per-strategy write would have
silently discarded every other strategy's prior override, since
`modeling.ta.strategy_defaults` is one jsonb blob for all 8 — the router now
merges rather than replaces.

Sprint 3 — commitment projection UX (the member/staff-facing projection
view) is next, independent of Sprint 2, depending only on Sprint 1 per this
brief's own sequencing.
