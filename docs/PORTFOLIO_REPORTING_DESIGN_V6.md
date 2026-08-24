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
| **C** | S21 sunburst rollup into `entity_holdings` | Next |
| **D** | SPV derivation view | Later |
| **later** | Cash modelling, corporate actions, commitments, UDFs, UI | Later |
