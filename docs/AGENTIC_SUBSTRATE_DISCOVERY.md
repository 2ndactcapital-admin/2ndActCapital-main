# Agentic Substrate Discovery — Findings Only

Discovery-only sprint (`agenticdiscovery.lowrisk`). No code, schema, or
config was changed. All facts below were re-verified live against the
`supabase-2ndact-dev` project and the current `apps/api` source tree on
2026-09-12 — none are carried over from prior chat summaries without
re-checking.

Purpose: establish real facts needed before making two schema decisions
flagged in the agentic-methods design review (`docs/
CROSS_PROJECT_STATUS_RECONCILIATION.md` §"Implementing agentic methods at
Hollisworks", items #3 and #5) — capability annotation on the action
registry, and `review_role` on the proposal queue.

---

## Task 1 — The SOC Vocabulary (S30)

**1a. What S30 actually built.** "SOC" in this codebase means the
Segregation-of-Concerns/RBAC access-control design (`sprint_prompts/
soc1..soc6.structural.md`), not "Standard Occupational Classification."
Six phases, all shipped and HELD (standalone, not wired into enforcement —
see `[[soc-sprints-are-standalone-held]]`):

1. Profiles + Permission Sets (`profiles`, `permission_sets`,
   `profile_permissions`, `permission_set_permissions`)
2. Staff visibility resolver (`services/staff_visibility.py`)
3. Households (`households`, `household_memberships`)
4. Restricted-access accounts (`services/restricted_access.py`)
5. Trading authority tiers + maker-checker (`services/trading_authority.py`,
   `trading_authority_grants`)
6. Member-side relationships (`trusted_contacts`, `delegate_grants`,
   `external_access_grants`)

There is **no single table or column named a "capability" or "SOC
vocabulary."** S30 actually produced three separate, partial vocabularies,
none of which is what the agentic design doc's phrase "capability (SOC
vocabulary)" cleanly names:

| Candidate | Table.column | Shape | Live values |
|---|---|---|---|
| Persona/job-function | `profiles.name` | free text, no CHECK | `Adviser`, `Community Member`, `CSA / Ops`, `Member`, `Org Admin` (5 seeded, `is_seed=true`) |
| Generic RBAC permission | `permissions.(resource, action, name)` | reference table, `UNIQUE(resource,action)` | 30 rows, e.g. `deals.score`→`score_deal`, `spv.manage`→`manage_spv` |
| Money-movement authority tier | `trading_authority_grants.authority_tier` | **CHECK constraint enum**: `'inquiry'\|'limited'\|'full'` | **0 rows — schema exists, zero real grants; `trading_authority.py` docstring confirms "referenced by zero code" before Phase 5** |

Also relevant: `permissions` (30 rows) is what `assistant_action_catalog.
required_permission` actually points to today for 9 of 16 real verbs (7
have `required_permission = NULL`).

**1b. Right granularity for ~85 verbs? No — coarser, on every axis.**
- `profiles.name` describes **job functions/personas** (5 values), not
  verb-level capabilities — exactly the "wrong shape" the task anticipated.
- `permissions` (30 rows) is resource-action grained, closer to verb-level,
  but is a **flat RBAC permission list**, not a capability taxonomy with
  tiers/idempotency semantics — and only covers the 9/16 real verbs that
  bother to set `required_permission`.
- `trading_authority_grants.authority_tier` is the only genuinely
  **tiered** vocabulary (inquiry/limited/full), but it is scoped
  specifically to money-movement/custody actions (2 real verbs classified
  in `trading_authority.MONEY_MOVEMENT_ACTIONS`: `spv.record_transaction`,
  `spv.subscribe`), not general-purpose.

None of the three is a ready-made "capability (SOC vocabulary)" that could
be dropped onto ~85 registry verbs unchanged.

**1c. The real gap.** A verb-level capability vocabulary needs, and none of
the three above provides together:
- A **flat, verb-appropriate set of capability tags** (not 5 personas, not
  30 ad-hoc RBAC permissions, not a 3-value custody tier) that can express
  "what kind of thing this verb does" independent of who's allowed to call
  it.
- A place to encode **tier + idempotency** alongside capability, per the
  design doc's `capability (SOC vocabulary) + tier + idempotent` shape —
  today tier-like data is split across three unrelated fields
  (`assistant_action_catalog.default_autonomy`, `workflow_steps.
  autonomy_tier`, `trading_authority_grants.authority_tier`), and
  idempotency isn't tracked anywhere on the registry at all
  (`AssistantAction` dataclass in `services/action_registry.py` has no such
  field).
- The self-supervision exclusion rule cited in the design doc ("real S30
  SOC-matrix constraint" — Compliance agent can't surveil its own runs) has
  **no matrix or enforcement in code today.** The only "matrix" hit in the
  codebase is an unrelated widening-only type-change matrix in
  `services/portfolio_udf.py:1285`. The self-supervision rule is a written
  design principle in `CROSS_PROJECT_STATUS_RECONCILIATION.md`, not a
  built mechanism.

**Scale mismatch worth flagging directly**: the design doc's "map the
~85-verb taxonomy onto the eight allowlists" assumes ~85 real registry
verbs exist. **They don't yet** — see Task 5d: the live registry has
exactly **16** rows, all in 2nd Act's org. The 85-verb figure is a planned
future surface, not the current one.

---

## Task 2 — The Real Autonomy Tier State

**2a. `assistant_action_catalog.default_autonomy` — real distinct values.**
No CHECK constraint and no enum type — plain `text`. Live data:

| value | count |
|---|---|
| `auto` | 10 |
| `confirm` | 6 |
| `suggest` | **0** |

The Python-side `AssistantAction.default_autonomy` type hint (`services/
action_registry.py`) is `Literal["suggest", "confirm", "auto"]` — a
type-checker-only constraint, not enforced by the DB. `suggest` is a valid
value nothing currently uses.

**2b. No Tier-1 (draft-and-approve) value in real use — confirmed.**
Zero `assistant_action_catalog` rows use `suggest`. Separately,
`workflow_steps.autonomy_tier` is a genuinely different, unrelated
vocabulary:

- **Type**: `integer`, no CHECK constraint found (`pg_constraint` on
  `workflow_steps` shows only FK/PK/unique constraints — the 1/2/3
  convention is enforced only by application code, e.g. `services/
  workflow_steps_deriver.py`).
- **Scope**: per BPMN **workflow step**, not per registry verb.
- **Live data** (2 real rows, from a workflow-manager verify fixture that
  never got torn down): `autonomy_tier=2` on a `service` step
  (`action_registry_key='marketplace.show_new_deals'`), `autonomy_tier=1`
  on a `user` step (`assigned_role_profile_id` → a `profiles` row).

**These are two different tier systems with no shared vocabulary and no
mapping table between them.** `assistant_action_catalog.default_autonomy`
is text (`auto`/`confirm`/`suggest`); `workflow_steps.autonomy_tier` is an
integer (1/2/3). The only connective tissue is `workflow_steps.
action_registry_key`, a soft text reference to `assistant_action_catalog.
action_key` with **no FK constraint** — validated only at the app layer
(comment in `action_registry.py`: `ActionRegistry.all()` is "the closed
reference list a workflow generator draws valid Service Task action keys
from"). A workflow step's `autonomy_tier` does not derive from, or get
validated against, the action's own `default_autonomy` — they can disagree
freely today.

**2c. Human caller vs. agent caller — no distinction exists today.**
Nothing in `assistant_action_catalog`, `AssistantAction`, or the
`list_for_user`/`to_tool_specs` methods on `ActionRegistry` (`services/
action_registry.py`) branches on caller type. `list_for_user(user_id,
permissions)` filters purely on `required_permission ∈ permissions` — the
same filter would run whether the "user" is a human session or a
hypothetical agent principal. There is no agent principal concept in the
live schema at all (no `agent_id`, `agent_runs`, or similar table exists —
confirmed by absence from `information_schema.tables`).

---

## Task 3 — The Two Permission Axes, Precisely

**3a. Real, current contents of `roles` and `profiles`.**

`roles` (RBAC axis — lowercase snake_case, 13 real rows + 2 verify
fixtures, all in org `00000000-0000-0000-0000-000000000001`):

`admin`, `advisor`, `compliance_jr`, `compliance_sr`, `fund_finance`,
`investment_committee`, `investment_staff`, `ir_member_relations`,
`member`, `member_manager`, `next_gen`, `super_admin`, `support_staff`.

`profiles` (SOC axis — Title Case display names, 5 real seeded rows
(`is_seed=true`) + 5 verify fixtures, same org):

`Adviser`, `Community Member`, `CSA / Ops`, `Member`, `Org Admin`.

**Mismatch confirmed, and it's worse than a spelling difference.** There is
a **third** vocabulary in play: `users.role` is a free-standing text field
(not FK'd to the `roles` table) carrying literals like `super_admin` and
`org_admin`, checked by `services/rbac.py`'s `is_super_admin`/
`is_org_admin` helpers (`_field(user, "role") == SUPER_ADMIN_ROLE`). Note
`org_admin` is **not a row in `roles`** at all (the closest `roles` row is
named `admin`) — this exact drift was already found and is load-bearing
context, see `[[workflow-perms-zero-grants-fix]]`. So there are really
**three** role-shaped vocabularies (`users.role` literal, `roles` table,
`profiles` table), not two.

**No mapping exists in schema or code today.** No FK between `roles` and
`profiles`; no lookup table; no code (`rbac.py`, `services/profiles.py`)
that translates one into the other. `services/profiles.py` explicitly
documents this isolation: "this is the profile-layer check only; it does
not consult roles or the [permission-set axis]."

**3b. `resolve_field_access_bulk` / `resolve_tab_visibility` — real
signature and reuse fit.**

```python
async def resolve_field_access_bulk(
    conn, *, definition_ids: list[str], tab_id: str | None, org_id: str, user_id: str,
) -> dict[str, str]:   # {definition_id: access}

async def resolve_tab_visibility(conn, *, tab_id: str, org_id: str, user_id: str) -> bool
```

(`services/portfolio_udf_field_permissions.py:272`, `services/
portfolio_udf_tabs.py:407`.)

Both answer **"how much access does this specific user have to this
specific, already-identified resource?"**, binding to `profile_permissions`
and `permission_set_permissions` with most-restrictive-wins. Routing a
proposal to a reviewer is a **different question**: "which
profile/permission-set should this new row be assigned to?" — a
resolve-forward, not a resolve-access, operation. Reuse would be **forced**:
- The dual-path most-restrictive-wins logic only makes sense when both
  paths are independently answering the *same* access question about the
  *same* object; a reviewer-routing decision isn't an access check at all,
  it's an assignment.
- `resolve_tab_visibility`'s own docstring flags that its no-grant-row
  default is **open-by-default** ("absence of a restriction does not
  manufacture one"), explicitly the *opposite* polarity from `profiles.py`
  ("an absent `profile_permissions`/`permission_set_permissions` row means
  NOT granted"). A routing mechanism copying this pattern verbatim would
  inherit whichever polarity it borrowed from, which may not be the right
  default for "which reviewer should see this."

**Do not reuse these functions directly for review-routing; reuse the
established most-restrictive-wins* concept* only if a routing decision
genuinely needs to reconcile two independent grant sources** — it likely
doesn't, since routing needs exactly one answer (who), not a
narrowest-of-two access level.

**3c. Plausible reviewer role, per proposed agent — against the real
`roles` + `profiles` rows above:**

| Agent | Plausible existing role today? | Which axis |
|---|---|---|
| Compliance Analyst | **Yes, exact** | `roles.compliance_jr` / `roles.compliance_sr` |
| Hollis (member, read-only) | **Yes, exact** | `roles.member` and `profiles.Member` (both axes) |
| Authoring (internal) | **Yes, exact** | `profiles.Org Admin` ("Authors workflow definitions, configures triggers") |
| Portfolio & Suitability | Plausible, not exact | `roles.advisor` / `profiles.Adviser` |
| Deal & SPV | Plausible, not exact | `roles.fund_finance` ("SPV management, capital calls, distributions") or `roles.investment_staff` |
| Fund Admin & Billing | Plausible, not exact | `roles.fund_finance` — same row as Deal & SPV, would collide |
| Custodial Ops | **No** | closest is `roles.support_staff` ("read-only, basic CRM updates") — not an ops/custody role |
| Chancery (document ingestion) | **No** | closest is `profiles.CSA / Ops` ("documents and read visibility") — a persona, not a document-specialist reviewer role |

Two real findings worth calling out: (1) Deal & SPV and Fund Admin &
Billing would currently collide on the same `fund_finance` role if mapped
naively — they are distinct agents in the design but have no distinct
existing role; (2) Custodial Ops and Chancery have no plausible existing
role on **either** axis and would need new ones.

---

## Task 4 — The Proposal Queue, As It Really Exists

**4a. Real, current proposed-state mechanisms — three candidates, live row
counts:**

| Table | Live rows (dev) | Real current use |
|---|---|---|
| `assistant_activities` | **0** | Exists, has `proposed_by`/`approved_by`/maker-checker CHECK (`assistant_activities_maker_checker_chk`), is the mechanism `services/trading_authority.py` writes to — but it is **standalone/HELD**, never wired into a live endpoint, and has zero real rows in dev. |
| `member_todos` | **10** (7 `open`, 1 `done`, 2 `dismissed`) | The only one with real, non-fixture data. Populated by 3 real `source` values: `entity_stub` (3, category `crm`), `workflow_run_held` (6, category `workflow`), `workflow_user_task` (1, category `workflow`). |
| `workflow_run_steps` | **0** | Exists (`status`, `proposed_by`, `approved_by`, `result` columns — same maker-checker shape), zero rows in dev. |

**The real answer is `member_todos`** — it's the only one of the three
mechanisms with genuine, non-verify-fixture rows today, even though it
wasn't purpose-built as an agent-proposal queue (see `[[scheduler-notify-
orphan-sweep]]`: these "todos" already land on real `org_admin` users, not
just members, despite the table's name).

**4b. Can `member_todos` carry every agent output object type?**
Columns: `kind`, `category`, `title`, `detail` (text), `action_key`,
`action_params` (jsonb), `due_date`, `expected_window`, `priority`,
`status`, `source`, `related_type`, `related_id`. The `related_type` +
`related_id` polymorphic pair plus `action_params` jsonb gives it real
flexibility — it is **not** hard-shaped to one object kind the way, e.g.,
`document_link_proposals` was found to be in prior work (`[[underlying-
resolution]]`: "UNREUSABLE for global data (NOT NULL FKs)"). But nothing in
its schema or its 3 live `source` values proves it can carry a
document/adjustment/memo/score/obligation today — the only proven shapes
are "held workflow run," "workflow user task," and "CRM entity stub."
Whether it fits every future agent-output type is **untested**, not
disproven.

`assistant_activities` is shaped more narrowly and more deliberately: its
columns (`payload`, `result`, `reversible`, `undo_token`, `undone_at`,
`proposed_by`, `approved_by`) are purpose-built for a single kind of
proposal — "an assistant action that was proposed, may be approved, and
may be undone" — closer to the Tier-1 shape the design doc assumes, but it
has zero production usage to validate that shape against real object
variety.

**4c. Routing/assignment today — genuinely none, on any of the three.**
- `member_todos.user_id` — single target user, not a role.
- `assistant_activities.user_id`/`proposed_by`/`approved_by` — same
  pattern, no role.
- `workflow_run_steps` — same pattern.

No column on any of the three names a reviewer role, permission set, or
profile. `grep` for `review_role`/`reviewer_role`/`reviewer_profile` across
`apps/api` returns zero hits outside this discovery doc. **This is a flat,
per-user, global queue on every existing mechanism** — the design doc's
item #5 (`review_role` on the proposal queue) genuinely doesn't exist in
any form yet, confirming it as real, unstarted scope rather than a
mis-remembered detail.

---

## Task 5 — The Three Known-Bad Registry Rows

Full registry (Task 5d) confirms the catalog has **exactly 16 rows**, all
in org `00000000-0000-0000-0000-000000000001` (2nd Act) — see the table at
the end of this section.

**5a. `crm.draft_note` — confirmed as reported.**
```
action_key=crm.draft_note, module=crm, access_type=write,
default_autonomy=confirm, reversible=false, required_permission=NULL
description="Draft a CRM note for a contact or entity. The member
reviews the draft before it is saved."
```
It is genuinely a draft-and-confirm action (`default_autonomy=confirm`,
description explicitly says the member reviews before saving) yet is
marked `reversible=false`. Confirmed as reported — a draft that requires
explicit confirmation before persisting is exactly the kind of action one
would expect to be reversible (discard the draft), and it isn't flagged as
such.

**5b. Module/action_key prefix drift — confirmed, exactly 4 rows, no
more:**

| action_key | module | drift |
|---|---|---|
| `entity.link_ownership` | `entity_graph` | prefix `entity.` vs module `entity_graph` |
| `entity.show_hierarchy` | `entity_graph` | same |
| `entities.count` | `queries` | prefix `entities.` vs module `queries` |
| `investments.count` | `queries` | prefix `investments.` vs module `queries` |

No other rows in the 16-row registry exhibit this drift — every other
action's key prefix matches its module (`crm.*`↔`crm`, `spv.*`↔`spv`,
`spv_carry.*`↔`spv_carry`, `portfolio.*`↔`portfolio`,
`marketplace.*`↔`marketplace`, `litellm.*`↔`litellm_ops` — note this last
one is its own, fifth, milder drift not previously reported:
`litellm.reload_model_cost_map` has module `litellm_ops`, prefix
`litellm.`, dropping the `_ops` suffix).

**5c. `spv.subscribe` / `spv.record_transaction` — real current tiering,
checked against real code.**

Live registry rows:
```
spv.subscribe:            required_permission=NULL, default_autonomy=confirm,
                           reversible=true, access_type=write
spv.record_transaction:   required_permission=manage_deals, default_autonomy=confirm,
                           reversible=true, access_type=write
```

`services/trading_authority.py` (SOC Phase 5) already independently
classifies both as money-movement and distinguishes them by custody
exposure:
```python
MONEY_MOVEMENT_ACTIONS = {
    "spv.record_transaction": {"third_party": True},   # requires FULL tier
    "spv.subscribe":          {"third_party": False},  # requires LIMITED tier
}
```
`spv.record_transaction`'s own description confirms real behavior: "Record
a transaction against an SPV (capital call, distribution, fee, etc.)...
Optionally allocates the transaction to subscribers immediately" —
distributions and fees pay out to investors/managers, i.e. genuinely
third-party fund direction. The mis-tiering argument **holds**: both
registry rows carry the *same* `default_autonomy=confirm` value with no
distinction between them, while `trading_authority.py` (built later, in a
completely separate, unwired module) demonstrably needed to split them
into different authority tiers to be regulatorily correct. The action
registry's own tiering does not reflect this real distinction — the two
verbs look identical at the registry layer and only differ once you cross
into the separate, HELD `trading_authority.py` module. Note also: per
`CLAUDE.md`'s Custody Cliff rule, both of these are money-movement actions
that must remain permanently unreachable by agent-initiated writes,
regardless of how any future capability annotation resolves this tiering
gap.

**5d. Full current registry (16 rows) — the real baseline:**

| action_key | module | access_type | required_permission | default_autonomy | reversible |
|---|---|---|---|---|---|
| `crm.draft_note` | crm | write | NULL | confirm | false |
| `entity.link_ownership` | entity_graph | write | staff | confirm | true |
| `entity.show_hierarchy` | entity_graph | read | NULL | auto | false |
| `litellm.reload_model_cost_map` | litellm_ops | write | author_workflows | confirm | false |
| `marketplace.show_new_deals` | marketplace | read | NULL | auto | false |
| `portfolio.find_my_investment` | portfolio | read | NULL | auto | false |
| `portfolio.show_allocation` | portfolio | read | NULL | auto | false |
| `entities.count` | queries | read | NULL | auto | false |
| `investments.count` | queries | read | NULL | auto | false |
| `spv.list_open` | spv | read | NULL | auto | false |
| `spv.record_transaction` | spv | write | manage_deals | confirm | true |
| `spv.show_captable` | spv | read | manage_deals | auto | false |
| `spv.show_ledger` | spv | read | manage_deals | auto | false |
| `spv.subscribe` | spv | write | NULL | confirm | true |
| `spv_carry.propose_from_realization` | spv_carry | write | manage_billing | confirm | false |
| `tasks.my_todos` | tasks | read | NULL | auto | false |

Registration source files (`apps/api/services/assistant_actions/*.py` for
8 of 9 modules, plus `services/spv_carry_runs.py` for the `spv_carry`
module — the latter is registered from outside the `assistant_actions/`
package, a minor structural inconsistency worth noting since it means "grep
`assistant_actions/`" alone would miss one real module).

Every `access_type=write` row uses `default_autonomy=confirm` and every
`read` row uses `auto` — **default_autonomy in the live registry currently
correlates 1:1 with access_type**, with zero exceptions across all 16 rows.
Whether that correlation is a deliberate invariant or coincidental (n=16)
is not something this discovery can determine — worth confirming before
assuming it as a rule.

---

## Summary of gaps this discovery surfaces (facts only, no proposals)

- No existing table/column is a ready-made "capability (SOC vocabulary)."
- The 85-verb taxonomy the design doc maps agents onto does not exist yet —
  today's real registry is 16 verbs.
- Two unrelated autonomy-tier vocabularies (`default_autonomy` text,
  `workflow_steps.autonomy_tier` int) have no shared values and no mapping.
- Three role-shaped vocabularies (`users.role` literal, `roles` table,
  `profiles` table) exist with zero mapping between any pair.
- The dual-axis most-restrictive-wins pattern (UDF field/tab access) does
  not naturally extend to reviewer routing — different problem shape.
- Two of eight proposed agents (Custodial Ops, Chancery) have no plausible
  existing reviewer role on either axis; two others (Deal & SPV, Fund Admin
  & Billing) would collide on the same role if mapped naively.
- The only mechanism with real production-shaped data today
  (`member_todos`) was not purpose-built as an agent-proposal queue and has
  no `review_role`-equivalent column; the two mechanisms that structurally
  look more like a Tier-1 proposal queue (`assistant_activities`,
  `workflow_run_steps`) have zero live rows.
- `spv.record_transaction` and `spv.subscribe` are tiered identically at
  the registry layer despite a real, already-built (but unwired)
  regulatory distinction between them in `trading_authority.py`.
