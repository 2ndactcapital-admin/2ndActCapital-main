# Portfolio Reporting — Design V6

> **Provenance.** This file did not exist in the repository or anywhere in git
> history when Portfolio A1 was built (A1 reported it missing), and it still did
> not exist at the start of Portfolio A2. No original was recoverable — `git log
> --all -- '*PORTFOLIO_REPORTING_DESIGN*'` returns nothing.
>
> What follows is therefore an **as-built reconstruction**, written during A2 from
> the two sources that *are* authoritative: the deployed schema (introspected
> live) and the A1/A2 sprint briefs. It is not the original V6 document. Where it
> states a rule, that rule was measured against the database, not remembered.
> Treat it as the design of record going forward; if the original ever surfaces,
> reconcile rather than overwrite.

---

## 1 · What this subsystem is

A tenant-scoped portfolio reporting layer sitting on top of a **global,
org-less security master**. Two schemas, two different security postures:

| Layer | Tables | `org_id`? | RLS shape |
|---|---|---|---|
| **Global security master** (A1) | `portfolio.securities_global` + `_identifiers` / `_prices` / `_relationships` | **No** | 4 policies: `USING (true)` global read, `app.is_super_admin` for insert/update/delete |
| **Tenant portfolio** (A2) | `portfolio.assets`, `asset_identifiers`, `positions`, `valuations`, `transactions`, `external_references` | **Yes** | 1 policy: `org_id = app.current_org_id OR app.is_super_admin`, `cmd=ALL` |

A CUSIP means the same thing to every tenant, so the security master is shared.
*What a tenant owns* is nobody else's business, so the portfolio layer is
org-isolated. **Copying A1's four-policy global-read shape onto a tenant table
would be a silent cross-org read**, which is why the A2 verification asserts the
policy count is exactly one per table.

## 2 · The non-negotiable operational rule

**`portfolio` is not on the `search_path`.**

`app_service` has no `pg_db_role_setting` row, so it inherits `"$user", public`.
An unqualified `FROM assets` raises `UndefinedTableError` under the production
role while working fine in a psql session that happened to `SET search_path` —
invisible in development, total in production. Every table reference in every
portfolio service module is schema-qualified through a module constant, and both
verify scripts AST-parse the module and fail on any bare `FROM`/`INTO`/`UPDATE`.

The RLS GUCs (`app.current_org_id`, `app.is_super_admin`) are connection-level
`SET LOCAL` and have no schema binding, so policies in a non-`public` schema read
them with no extra work. That part needs nothing.

## 3 · The object model

```
                      portfolio.securities_global        (global, no org_id)
                                  ▲
                                  │ global_security_id (nullable)
                                  │
  entities ──owner_entity_id──▶ portfolio.positions ──asset_id──▶ portfolio.assets
  (incl. entity_type='account')       │                                 ▲
                                      │ position_id                     │ asset_id
                                      ▼                                 │
                            portfolio.transactions            portfolio.valuations
                                      │
                            transaction_type_code ──▶ public.transaction_types
```

### 3.1 · `assets` — the tenant's *thing*

A tenant-local record of a holdable thing. `global_security_id` is **nullable**:
a listed equity points at the security master; a rental property, a private LLC
interest or a painting does not. `asset_class ∈ {financial, hard_asset}` and
`valuation_method ∈ {market_price, nav, appraisal, mark_to_model,
amortized_cost}` between them carry whether the thing has a listed price.

`asset_type` is `NOT NULL` with **no CHECK constraint** — deliberately open, and
therefore validated only for non-emptiness in the service layer.

### 3.2 · `positions` — the edge, and the three ownership bases

A position is the **edge** between an owner entity and an asset at a date. It is
not a row on the asset and not a row on the entity.

`ownership_basis` selects which measure is authoritative, and the three are
mutually exclusive in exactly one direction:

| basis | required | must be NULL | notes |
|---|---|---|---|
| `units` | `quantity` | `ownership_pct` | listed securities, fund units |
| `percent` | `ownership_pct` | `quantity` | LLC/LP interests, undivided real-property shares |
| `value` | `market_value` | `quantity`, `ownership_pct` | appraised things with no unit and no percentage |

`market_value` is permitted (and normal) on all three — it is the valued amount,
not the basis. **The database does not enforce any of this**; there is no CHECK
constraint covering the combination. It is enforced in
`services/portfolio_assets.py::create_position` and nowhere else, which is why
the A2 verification round-trips all three bases *and* proves each one rejects the
wrong field.

### 3.3 · `valuations` — append-only, with an explicit supersession edge

`supersedes_valuation_id` is a **forward pointer on the new row**. Restating a
valuation inserts a new row pointing back at the old one; **the old row is never
updated.** Both remain independently queryable forever, which is the whole point
— a restatement that mutated the original would destroy the audit trail that
makes the restatement meaningful.

`status ∈ {estimated, preliminary, final, audited, restated}`.

**Current-value resolution ladder** (`resolve_current_value`): latest
`valuation_date` first; within a date, `audited > final > preliminary >
estimated`; and **any valuation that something else supersedes is demoted below
all four**, regardless of its own status. That demotion is what makes
supersession do work without an in-place update.

When nothing qualifies, the resolver returns a value of `None` **with a reason
string** — never `0`. A silent zero for "we have no mark" is indistinguishable
from a genuine zero position and propagates into every rollup downstream.

`value_basis ∈ {per_unit, total}`: a `per_unit` mark is not a market value on its
own. The resolver returns `None` with that reason unless a quantity is supplied.

### 3.4 · `transactions` — and the `market` axis

`transaction_type_code` FKs `public.transaction_types.code`. That table gained a
nullable `market ∈ {public, private, both}` column, backfilled in A2 (§4).

The service-layer compatibility check is deliberately narrow: an asset with
`valuation_method='market_price'` is public-market; `nav`/`appraisal`/
`mark_to_model` are private; `amortized_cost` is compatible with both; a
transaction type marked `both` (or left `NULL`) is always allowed. This is a real
check, not a rules engine — it catches "capital call against a listed equity",
which is the mistake that actually happens.

### 3.5 · `external_references` — the ingestion idempotency key

`(source_system, external_id, record_type) → record_id`. **Known defect: the
UNIQUE constraint is not org-scoped**, so two tenants ingesting from the same
source with colliding external ids will hard-conflict, and the loser gets a
unique violation on a row RLS will not let it see. Must be widened to include
`org_id` before Phase B ingestion goes multi-tenant.

## 4 · `transaction_types.market` — the A2 backfill

Capital calls and distributions are ILPA private-markets constructs; buy / sell /
dividend are public-market constructs; adjustment, fee/expense and interest occur
in both books. `valuation` is private: a valuation *mark as a transaction* is how
an illiquid holding's NAV moves, whereas a listed position is marked by its price
series, not by a transaction row. See §Task 2 of the A2 sprint log for the
per-row table.

## 5 · The `account` node

`entity_type` gained `'account'` (and `'spv'`). An account — a custodial account
at Altruist, Schwab, Fidelity — is a **real entity**, so a position can be owned
by it, and account-level reporting is a graph query rather than a special case.

It is also **optional**. A position may name a trust, an LLC or an individual as
its `owner_entity_id` with no account node in between; nothing in the schema or
the service layer requires an intervening account.

**Accounts are excluded from every CRM-facing surface** — `GET /entities`,
`GET /entities/search`, `find_entity_dupes` — because a CRM list is a list of
*people and vehicles you have a relationship with*, and a brokerage account is
neither. The exclusion is opt-outable per call (`include_operational=true`) so
portfolio surfaces can still see them. The same exclusion covers `spv`, for the
same reason.

## 6 · Standing rules

- `org_id` never comes from a request body. Service functions take it as an
  explicit keyword argument sourced from JWT claims.
- Any monetary value crossing an API boundary is a `Decimal`. `float` is refused
  outright, not converted — `Decimal(0.1)` silently preserves the binary error
  and nothing downstream ever raises.
- Bitemporal columns are `valid_from` / `valid_to` / `system_from` / `system_to`.
  Business facts are superseded by closing the old row and inserting a new one
  (CLAUDE.md Rule 3), never updated in place.

## 7 · Phase map

| Phase | Scope | State |
|---|---|---|
| **A1** | Global security master + service layer | Shipped (39/39) |
| **A2** | Tenant assets / positions / transactions / valuations, the `account` node, `market` backfill | Shipped — this sprint |
| **B** | Real ingestion (reporting-tool file import; source precedence / `superseded_by_source` logic) | Shipped — Altruist BLOCKED on absent credentials, Chancery consumption not built |
| **C** | S21 sunburst rollup into `entity_holdings` | Shipped (22/22) |
| **D** | SPV derivation view, cash modelling, document drill-through | Shipped (56/56) — this sprint |
| **E** | Chancery-sourced alts / hard assets, commitments, tax-doc tracking | Shipped (39/39) — this sprint |
| **F** | Corporate actions — recorded GLOBALLY (§10 correction below), applied per org | Shipped (57/57) — this sprint |
| **G** | UDFs | Next |
| **later** | Reconciliation / performance / cross-client analysis (H), UI | Later |

> **§10 correction, applied in Phase F.** The original §10 sketch keyed corporate
> actions to `asset_id`, which is tenant-scoped. A split is ONE real-world event
> about ONE security, not a fact recorded once per tenant that happens to hold
> it — so the record lives in `portfolio.securities_global_corporate_actions`,
> **global, no `org_id`**, with A1's four-policy RLS shape. RECORDING is global
> and Super-Admin-gated; APPLYING is tenant-scoped and every org applies the same
> recorded event to its own rows independently. `transactions.corporate_action_id`
> gained a real FK, and `transactions.is_corporate_action_adjustment` was added.
> Full rationale in `docs/PROJECT_STATUS.md` §7n.

> **Note on this document's sections.** Phase E's brief cited "§12, §13" and
> Phase F's cited "§10"; this design has never had sections past §9. The
> specification actually in force for both is the phase-map row above plus the
> brief itself, and the findings are recorded in `docs/PROJECT_STATUS.md` §7m and
> §7n rather than back-filled here as sections that were never written.

## 8 · The SPV derivation view (Phase D)

`portfolio.spv_derived_positions` projects CURRENT `spv_subscriptions` into
position shape — `authority='internal'`, `source_system='spv_subscriptions'`,
`ownership_basis='percent'`. It is a **view**: nothing is stored twice, and
`spv_subscriptions` remains the book of record, so a correction goes there
(through `routers/spv.py`, which already implements the Rule 3 supersede) and
never through the projection.

**Where an SPV interest's current value comes from.** Nowhere, before Phase D.
`commitment_amount` / `funded_amount` are a commitment and a cost; `spvs` has no
NAV column at all; `member_investments` repeats the same two figures. The
Sprint-22 GL's `v_capital_accounts` is structurally right and **not connected** —
it groups by `journal_lines.dim_member_series_id`, which has no FK, no referent
relation anywhere in the database, and is NULL on every deployed row; and
`member_series` is a `spvs.vehicle_type`, so that dimension is grained at the
series, not the subscriber. The path Phase D uses is the one join that already
existed:

```
spv_subscriptions → spvs.id → portfolio.assets.internal_spv_id (ONE per SPV)
                  → portfolio.valuations, resolved by §3.3's ladder
                  × ownership_pct / 100
```

If the GL ever writes a real per-subscriber capital-account dimension, that
becomes the better source and this join should be revisited.

**Which subscriptions project.** `valid_to IS NULL` — unlike every `portfolio.*`
table, `spv_subscriptions` has only ONE temporal axis: no `system_from` /
`system_to`, no trigger, and no history or audit table anywhere, so `valid_to IS
NULL` alone means "current" — plus
`subscription_status IN ('committed','funded')` and `ownership_pct IS NOT NULL`
— all three lifted verbatim from `services/spv_allocation.py`. The last is also
required by §3.2: a `percent` position without `ownership_pct` is not a valid
position. `unprojected_subscriptions()` reports every subscription that does not
project, with its reason, because silently dropping a row is a derived view's
one failure mode that a stored table does not have.

**`security_invoker = true` is mandatory, not optional.** Every base table is
RLS-protected and owned by `postgres`, which has `rolbypassrls`. A view built the
default way executes as its owner and would return every tenant's subscriptions
to every tenant, through a relation that looks exactly like the org-isolated
table it derives from. Read-only is enforced three ways: not auto-updatable,
write grants explicitly revoked (the schema's `ALTER DEFAULT PRIVILEGES` would
otherwise grant them), and v5-UUID row ids under a namespace of their own so an
id reaching a write function is refused rather than matching something.

## 9 · Cash, and document drill-through (Phase D)

**Cash is a position, not a special case.** One asset per `(org, currency_code)`,
`asset_type='cash'`, `ownership_basis='value'` (no unit, no percentage — the
amount *is* the fact), `valuation_method='amortized_cost'`, which maps to `both`
markets so public and private transaction types are both legal against it. Cash
is the settlement leg of every transaction in either book; `market_price` would
have made every capital call against cash illegal.

A **bank account** is an `entity_type='account'` entity as the `owner_entity_id`
of a cash position — the identical call to a trust holding cash directly, with a
different owner. Nothing inspects `entity_type`, and per §5 no account node is
required.

Idempotency is enforced by partial unique indexes on the current-row predicate
(`assets_cash_active_uniq`, `assets_internal_spv_active_uniq`), not by a Python
`SELECT` alone — otherwise two concurrent callers both miss and both insert, and
the failure is silent. `assets.currency_code` is nullable and NULL is not equal
to itself in a unique index, so `ensure_cash_asset` refuses a NULL currency to
close the hole the index cannot.

**Document drill-through** adds four `document_record_links.record_type` values —
`portfolio_position`, `portfolio_valuation`, `portfolio_transaction`,
`portfolio_asset`. That column is unconstrained text with **no CHECK**, so no
migration was needed; the vocabulary lives in `services/portfolio_documents.py`
so a typo fails at the call. Values are **prefixed** because `record_type` is a
namespace shared with Chancery's `entity` / `spv` / `deal` / `transaction`, and a
bare `'transaction'` would collide with links that already exist. Reads go
through `document_linkage.list_documents_for_panel` — the same function behind
Phase 9's `DocumentsPanel` — so links render with no UI work.
