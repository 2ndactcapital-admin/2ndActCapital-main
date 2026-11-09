# CASH02 — discovery notes: cash policies and the three sleeves (2026-11-08)

Checked against the repo at `77d539a` (merge cash01) and the live database, 2026-11-08. Standing rule (Joe,
2026-11-08): **use the app's existing constructs; create a new one only where an existing one cannot carry it**, named
and justified below.

Source documents: `CASH_TREASURY_DESIGN_v1.md` §2.3, §2.7, §3.5–3.6, §5, §7 (mirrored in `docs/cash/`),
`CASH00_DISCOVERY.md` and `CASH_OBLIGATIONS.md` (cash01). **[FIND]** `CASH_TRACK_PLAN_v1.2.md` is still not in the repo
(cash01 found the same); its §2.4 is taken as the sprint prompt states it, nothing is invented from it.

---

## 1. cash01 decisions that bind this sprint

| cash01 decision | What cash02 does with it |
|---|---|
| One merged obligation read: `services/cash_obligations.schedule` (stored + TA-projected calls + debt service + CRM payments), counted lines only, a superseded projection counted once | Sleeve targets are sums over **that** schedule's counted lines. No second obligation read, no second projection |
| Coverage per ENTITY (its own cash) and per HOUSEHOLD (share-weighted lines, the household's Cash & Equivalents) — `services/cash_coverage.household_report` | A household policy sizes its sleeves on the household's share-weighted lines and liquid; an entity policy on the entity's own lines and own cash. The same `Report` — two fields (`shares`, `liquid`) are exposed on it so the policy never recomputes them |
| Liquid balance = net-worth core Cash & Equivalents buckets (`taxonomy_mc_8_1`, `taxonomy_sub_8_1_<n>`) | The "% of the liquid total" denominator is exactly that figure |
| Horizon default 24 months, 1–120, month arithmetic `cash_obligations.add_months` (clamped) | Sleeve windows use the same `add_months` from the same "today"; the policy's horizon has the same 1–120 bounds |
| Read gate `view_portfolio | manage_portfolio` + `authz.member_entity_scope` + VISIBILITY1 path guard; a member reads their own family only, read-only, with library labels | The policy block rides inside the SAME treasury envelope (one read gate, one scope); the member gets the active policy only |
| Money Decimal, text at the JSON boundary (`money_text`) | Same |
| Read-only on money | A policy is a TARGET. Nothing here creates a proposal, an order or an instruction (cash04) |
| `cash_coverage_minimums` (entity / household minimum, default 6) | **Migrated** into `cash_policies.min_coverage_months` (§5) |

## 2. SoD item-type registration path (how S30 / S29b added theirs)

S29b (`docs/s29b_part1.sql` §1) is the template, applied as one migration:

1. `sod_duties` — a MAKE duty and a CHECK duty, each listing the permission(s) that grant it (`ON CONFLICT (duty_key)
   DO UPDATE`).
2. `sod_item_types` — the item type: maker duty, checker duty, `floor_class` (NULL unless trade / transfer /
   disclosure / role_grant), `self_approval_capable`, `pre_s30_rule = 'maker_ne_checker'`, the site.
3. `sod_rules` — the platform DEFAULT rule `default:<item_type>` (same_item / block) built from the duty labels, and
   the platform POLICY `policy:<item_type>` (`self_approval = 'never'`, `enforce_floor = true`), each only when absent.
4. Code: a constant + `ITEM_TYPES` entry in `services/sod.py` and the task kind → item type in
   `TASK_KIND_ITEM_TYPES` (so `human_tasks.complete_task` decides it through `sod.can_check`).

`services/sod_admin.py` lists item types, duties and rules by reading those tables — no code change is needed for the
SoD admin page to show the new item type.

**cash02:** item type `cash_policy`, duties `set_cash_policy` (make) / `approve_cash_policy` (check), both granted by
`manage_cash_policy` (§6). `floor_class` NULL (a policy is not money movement — the money-movement floor duties come
with cash07). `self_approval_capable = false`: the table's own CHECK forbids `approved_by ∈ maker_ids`, so a
disclosed self-approval cannot be recorded on it, and the platform policy is `never` with the floor enforced (the
S29b shape). The role-grant floor S29b turned on is untouched.

## 3. Approval inbox path for a new task kind

- `services/human_tasks.register_kind(key, maker_checker=True, on_complete=…)` + `register_precheck`. `open_task`
  starts a run of `system:human_task:<permission>` and notifies every holder except the maker; the task appears in
  `GET /human-tasks/mine` (`tasks_for_user`) for every eligible non-maker with no further code.
- `complete_task` decides a maker-checker kind through `sod.can_check` (item type from `TASK_KIND_ITEM_TYPES`),
  records a refusal (`sod.record_refusal` → `sod_decisions` + `audit_log 'sod.refused'`) and an allowed decision
  (`sod.record_decision`, same commit).
- The subject's `on_complete` re-checks with `sod.gate` (inner gate, refusals recorded) inside its own transaction —
  the `services/authority._apply` pattern.
- **Bulk approve** (`services/bulk_approvals.TYPES`): a new type = one `BulkType` entry + its pending-items reader +
  its single-decision function in `_decide_one` + its exception classes in `_refusal`; `routers/approvals.BulkDecision.
  type` is a `Literal` and gains the key. Each item is decided by the SAME single-approval function, so the SoD gate
  applies per item.

**cash02:** kind `cash_policy_approval` (outcomes approve / reject), bulk type `cash_policy`.

## 4. FRED series available, and their as-of (live registry, 2026-11-08)

`market_data.indicator_series`, provider `fred`, read through `services/market_data/read_service.read_series`:

| Need (v1 §2.3) | Series | Status |
|---|---|---|
| Near-term: 3-month T-bill | `fred.dgs3mo` / `fred.dtb3` | **[FIND] not registered.** `fred.t10y3m` (10y − 3m) and `fred.dgs10` are, so the 3-month yield is derived as `dgs10 − t10y3m` on the same date (FRED defines T10Y3M = DGS10 − DGS3MO). Labelled "derived" |
| Reserve: 6-month / 1-year | `fred.dgs6mo` / `fred.dgs1` | **[FIND] neither registered** → Reserve reads "not estimated — needs fred.dgs6mo or fred.dgs1" |
| Long-term: 2-year | `fred.dgs2` | active, last observation 2026-11-05 (4.46) |
| (context) | `fred.dgs10` 4.84, `fred.t10y3m`, `fred.dff`, `fred.sofr` | active, last 2026-11-05 |

The proxy specification is ONE table in `services/cash_policy.YIELD_PROXIES` (ordered candidates per sleeve); adding
`fred.dgs3mo` / `fred.dgs6mo` / `fred.dgs1` to the registry seed (`docs/market_data/…` seed, an operator load) makes them
the first candidates with no code change. The sprint writes no registry row. The as-of of each estimate is the
observation date read, never "today".

## 5. tax_profiles' read

`services/tax_profiles.approved_profile(conn, org_id, subject_type, subject_id, tax_year)` — the approved, live profile
of a household (`subject_type = 'household'`) or an entity with no household (`'entity'`), rates as decimal fractions
(`federal_ordinary_rate`, `niit_applies` / `niit_rate`, `state_rate`, `city_rate`, …). No profile → the module's own
label, never a default rate.

**cash02 after-tax (Treasury state-tax exemption only):** Treasury interest is federal ordinary income and exempt from
state and local tax, so after-tax = yield × (1 − (federal ordinary + NIIT when it applies)); the profile's state / city
rates are deliberately NOT applied. Shown only with an approved profile for the policy's subject and the current tax
year, labelled "after-tax estimate — federal ordinary + NIIT; Treasury interest is state- and local-tax exempt". No
profile → "after-tax: needs a tax profile". Instrument-level after-tax selection (munis vs Treasuries) is cash03.

## 6. Where min_coverage_months lives today, and the migration

- Today: `public.cash_coverage_minimums` (cash01), one live row per entity / household, read by
  `cash_coverage.minimums()`, written by `PUT /cash/households/{id}/minimums` (no maker-checker). **Live: 0 rows ever**
  (0 live, 0 history).
- **Migration (cash02):** the minimum's ONE home is `cash_policies.min_coverage_months` of the scope's ACTIVE policy.
  `cash_coverage.minimums()` reads the active policies (household + every entity override); no active policy →
  the default 6. `docs/cash02_part1.sql` refuses to run if `cash_coverage_minimums` holds any row (it would have to be
  re-entered as a policy draft and approved — never copied in as an active policy without a checker), then DROPS the
  table. `PUT …/minimums` and `cash_coverage.set_minimum` are removed; the minimum is edited in the policy editor and
  takes effect only when a second person approves the policy. No second copy remains.

## 7. Permissions

No existing permission fits the CHECK duty: `manage_portfolio` is the data-entry permission every portfolio author
holds (support staff included), and an approval circle must be narrowable without taking ledger entry away; the
`approve_*` keys all belong to other subjects (tax, authority, execution …). So **one new key `manage_cash_policy`**
(resource `cash_policy`, action `manage`) carries both duties — author and approve — with SoD making the approver a
different person. Added the way rep10 added `manage_hypothetical_policy`: the `permissions` row + a grant to every
existing `org_admin` role in the migration, and `services/rbac.ORG_ADMIN_DEFAULT_PERMISSIONS` for new orgs. The
migration also grants it to every existing `advisor` and `admin` role (the roles that hold `manage_portfolio` and
author cash01's ledger). Super Admin passes first (services.authz). A member never holds it (the write gate is
`require_internal_permission`).

| Role | Reads the policy | Authors a draft | Approves |
|---|---|---|---|
| Super Admin | yes | yes | yes (a different person from every maker) |
| org_admin | yes (view_portfolio) | yes | yes |
| advisor / admin | yes | yes | yes |
| support_staff / compliance / investment_staff | yes (view_portfolio) | no | no |
| member | their family's ACTIVE policy, read-only | no | no |

## 8. Reuse inventory

| Piece | Construct used | New? |
|---|---|---|
| The policy record (versioned, maker-checker, one active per scope) | **`public.cash_policies`** | NEW — `org_settings` is updated in place and org-keyed; `cash_coverage_minimums` held one number, not a versioned approvable record; `workflow_autonomy_settings` / `household_authority` are other subjects. Same shape as `household_authority` (status draft → approved, system-axis archive, approval task id) + execution_instructions' `maker_ids` / `approved_by ∉ maker_ids` CHECK |
| Sleeve targets | DERIVED at read time from (policy, cash01 schedule) | not stored (`cash_sleeve_targets` of v1 §7 is NOT built) |
| What the checker was asked to approve | the human task's own `payload` (derived sleeves + yields as of submit) | no snapshot column |
| Obligation schedule, liquid, shares | `cash_coverage.household_report` | two fields exposed |
| Yield proxies | `market_data.read_service.read_series` over the FRED registry | — |
| After-tax | `tax_profiles.approved_profile` | — |
| Trailing outflow cross-check | `portfolio.transactions` (`transaction_types.category IN ('call', 'fee')`, debit) of the scope's positions, last 3 months | — **[FIND]** no withdrawal / spending transaction type exists, so the cross-check sees calls and fees only and says so |
| Maker-checker | `services/sod` (item type `cash_policy`) + `services/human_tasks` (kind `cash_policy_approval`) | catalog rows only |
| Approval inbox | `/human-tasks/mine` + `services/bulk_approvals` (type `cash_policy`) | one TYPES entry |
| Labels | disclosure library: `cash.coverage_estimate` (cash01) + NEW key `cash.yield_estimate` | key only |
| Visibility | `authz.member_entity_scope` + `family_visibility.require_visible` | — |
| Audit | `services/audit.write_audit_log` (`cash.policy.*`) + human-task / SoD audit | — |
| Screens | Household → Treasury tab (cash01's `TreasuryPanel`), new `PolicyPanel` inside it; `/treasury` for a member | no new tab |
