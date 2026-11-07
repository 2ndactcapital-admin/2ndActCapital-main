# VISIBILITY1 — Task 1 discovery (2026-11-07)

Repo at `ade331f` (merge rollup2) + the live database (read-only catalog queries). The route table is generated
from the real app: `docs/VISIBILITY1_ROUTES.md` (`apps/api/scripts/visibility1_routes.py --check`).

Live data, 2026-11-07: **2 `staff_assignments` rows, 1 team, 1 team member, 0 users with a `manager_id`, 0 entities
with a `relationship_manager_id`, 0 entities with a `primary_household_id`, 1 household, 119 entity rows, and no
`staff_visibility.*` row in `org_settings`.** Every RLS policy on `staff_assignments`, `teams`, `team_members`,
`households`, `household_memberships`, `entity_ownership`, `org_settings`, `restricted_access_grants` is one ALL
policy scoped to `app.current_org_id` (or super admin) — the one-statement graph read below works under the
request's own org context.

## 1a. The unit of visibility — a family

A **family is a household**: its member entities (`household_memberships`, plus any entity whose
`primary_household_id` is the household) and everything they own or benefit from, transitively, through BOTH
ownership tables — `entity_relationships` (`ownership` and `beneficiary` edges) and `entity_ownership` — the same
edges `ownership_read.ownership_edges` and the member engine's `resolve_entity_set` subtree read. Everything else
hangs off an entity: accounts (`accounts.primary_entity_id` / `household_id`), positions
(`portfolio.positions.owner_entity_id`), transactions (via their position), commitments (via their position),
documents (`documents.entity_id`), notes (`entity_notes.entity_id`), liabilities
(`portfolio.liabilities.owner_entity_id`), relationships (`entity_relationships.from/to`), proposals and
instructions (via their account), gifts (`gift_proposals.household_id` / `member_entity_id`).

A live entity that no household reaches is a **standalone family**: a root (no standalone owner) plus its own
closure; an entity in an ownerless cycle roots itself. (Today nearly every CRM record is standalone: one household,
no `primary_household_id`.)

**The closure rule — an assignment to any one record grants the whole family:**
- an entity that is a household MEMBER grants each household it is a member of;
- otherwise an entity in exactly ONE family grants that family;
- an entity in TWO OR MORE families (a vehicle several families own — an SPV, a shared LLC) grants only its own
  downward closure. Assigning a shared vehicle must never hand someone every investor family.

**An entity in two households**: a person who is a member of both — assigning them grants both families; seeing
either household shows them. A shared vehicle (in two families' closures, a member of neither) is SEEN by staff of
either family, and an assignment on it covers it and what it owns only. The Team panel says so on such a record.

Superset note: beneficiary edges are followed from every family entity (the member engine follows them from the
root only) and an ownership edge without a percentage counts — the family is never smaller than what a member of it
would see.

## 1b. Owner / creator

Columns that mean it today: `households.created_by`, `entities.created_by`, `entities.relationship_manager_id`
(0 rows set live), `accounts.created_by` and `accounts.advisor_of_record_id` (accounts sit inside a family through
their owner, so neither is used as a family owner — an account's advisor is an attribute, not an assignment).

The rule for a family: its **creator(s)** = the household's `created_by` plus the `created_by` of every entity
whose grant (1a) is that family; its **relationship manager(s)** = `relationship_manager_id` of the same entities.
A relationship manager keeps visibility after the family is assigned to someone else; a creator does not (Task 2,
recorded in PROJECT_STATUS as a decision for Joe). Manager inheritance does NOT pass through ownership: a manager
sees their reports' ASSIGNMENTS, not the families their reports merely created or manage.

## 1c. Every internal staff-data read

`docs/VISIBILITY1_ROUTES.md` lists all 851 `/api/v1` routes with their treatment (generated; `--check`). Summary:

| treatment | how | routes |
|---|---|---|
| detail / child routes | app-wide `family_path_guard` (main.py): `{entity_id}`, `{household_id}`, `{account_id}` everywhere (not the GL chart `/ledger/accounts/…`, not the firm's executing-accounts settings); `{position_id}`, `{transaction_id}`, `{liability_id}`, `{document_id}`, `{rel_id}`, `{proposal_id}`, `{instruction_id}`, `{gift_id}`, `{commitment_id}` under their own URL prefixes; the same query-string ids as filters → the SAME 404 as a missing id | 193 |
| lists / search / counts already member-scoped | `member_entity_scope` / `entity_scope` / `require_entity_access` now return the staff scope for a restricted caller (CRM list + search, portfolio positions / transactions / securities / UDF / TA, investment profile, entity documents, document links, allocation lens, SPV reads, portentry) — one `= ANY($n::uuid[])` in SQL | 43 |
| households list + net-worth list, X-ray households, approvals summary + bulk decide, notifications list + unread count | explicit filters (`visible_household_ids`, `scope_for_principal`, `row_visible`, `notification_visible`) | — |
| every other JSON response | `scrub` in `rls_context_middleware`: any list item whose `id` / `entity_id` / `owner_entity_id` / `household_id` / `account_id` / … names a record of the org that the caller cannot see is removed. The backstop for tax, gifts, instructions, proposals, held-away, replication accounts, dashboards, graphs | all |
| assistant counts, semantic + keyword document search, document review / download, ownership-tree staff route, `/portfolio/commitments` | the legacy resolver `get_staff_visible_entity_ids` now follows THE rule when the org enforces | legacy callers |

**[FIND] "Today every internal user sees every family" is not quite true.** Five surfaces already restrict
internal staff to the legacy assignment set (`get_staff_visible_entity_ids`, assignments only, org admins
excluded): semantic search, document review / download, the assistant's `entities.count` / `investments.count`,
`GET /portfolio/commitments`, and the ownership-tree staff route. With 2 live assignments, a support user searching
documents today sees almost nothing. With the switch OFF they are unchanged (the sprint's rule: OFF = exactly
today); with it ON they follow the new rule (org admins see all).

**[FIND] Counts that the scrubber cannot fix** — a count computed in SQL over the whole org and returned as a
number: `GET /dashboard/brief` blocks, `GET /tax/overview` totals, `GET /households-by-margin` aggregates. Their row
lists are scrubbed; a bare total is not. Listed in PROJECT_STATUS as open.

## 1d. Writes that must also require visibility

Every write with a family id in its path or query is refused by the same guard (all methods): notes, addresses,
contacts, liabilities, ownership edges by id, household members / accounts, account models / proposals,
instructions, gifts, tax approvals by account, held-away imports, authority changes, lot-method changes, the Team
panel. Writes naming family ids in their BODY call `require_visible`: `POST /entity-ownership` (parent + child),
`POST /admin/households/{id}/members` (the entity), `POST /admin/households/{id}/accounts` (the owner),
`POST /admin/staff/assignments` (the entity). `POST /approvals/bulk` refuses an item of an invisible family.
`DELETE /admin/staff/assignments/{id}` and `DELETE /staff-visibility/assignments/{id}` 404 an assignment of an
invisible family. Creating a new record (`POST /entities`, `POST /admin/households`) has no family yet — its creator
owns it (1b).

## 1e. Existing UI for teams, membership, managers, assignments

`/admin/staff-visibility` (SOC Phase 2, `components/admin/StaffVisibilityManager.jsx`): create team, add / remove
member, assign a user or team to an ENTITY with a role label, delete an assignment. Gated by
`services.rbac.require_permission(manage_members)` via `ensure_user`. **[FIND]** removing a team member and deleting
an assignment wrote no audit row; both now do. There was NO UI or route for `users.manager_id` (set nowhere but
SQL) and no Team panel on a household or CRM record.

## 1f. Where the visible set is computed

- The **switch** rides on the Principal's ONE statement (`PRINCIPAL_COLUMNS` gains a scalar subquery on
  `org_settings`) — no new statement for anyone, switch on or off.
- The **visible set** is computed lazily, at most ONCE per request, cached on `request.state.visibility_scope`,
  only for a restricted caller (enforcing org, internal, not super / org admin): ONE statement (`GRAPH_SQL`, the
  org's entities, households, memberships, edges, assignments, teams, users, restricted grants and accounts as JSON),
  then pure Python. Not on the Principal (the Principal must stay one statement and the graph is not needed by most
  requests). previewperf's budget is untouched: a non-restricted caller runs zero extra statements; a restricted
  caller runs one.
