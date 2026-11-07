# Staff visibility — which staff see which families (VISIBILITY1)

The one implementation: `apps/api/services/family_visibility.py`. Never re-implement it per route.

## The rule

| caller | sees |
|---|---|
| Super Admin | every family |
| Org admin of the org (`manage_org_settings` by role) | every family of the org |
| Member / client role | their own family (BUGFIX2, unchanged) |
| Every other internal staff member | families assigned to them, to a team they are on, or to anyone who reports to them (`users.manager_id`, transitively) · families they are the relationship manager of · families they created **that no one is assigned to** |

On top: the restricted-access filter (`entities.access_restricted` needs a `restricted_access_grants` row).

## The family and the closure

A family is a household: its members (`household_memberships`, `entities.primary_household_id`) and everything they
own or benefit from, through both ownership tables. Accounts, positions, transactions, commitments, documents,
notes, liabilities, relationships, proposals, instructions and gifts belong to the family of the entity that owns
them. A record no household reaches is a standalone family (its top owner and what it owns).

**An assignment to any one record grants the whole family.** A household member grants its household(s). A record
in exactly one family grants that family. A vehicle shared by several families grants only itself and what it owns —
assigning a shared SPV never hands someone every investor family; staff of any family that owns it can see it.

## Owner semantics (decision recorded for Joe)

- **Relationship manager** (`entities.relationship_manager_id`): keeps visibility always, assigned or not.
- **Creator** (`households.created_by`, `entities.created_by`): sees the family only while NO ONE is assigned to it.
  The first assignment hands it over; the creator no longer sees it unless assigned too.
- A manager inherits their reports' assignments, never their reports' created or relationship-managed families.

## What is enforced where

Only when the org's switch is on, and only for a restricted caller (internal staff who is not Super Admin / org
admin). Everyone else: exactly the pre-VISIBILITY1 behaviour, zero extra statements.

- Detail and child routes: one app-wide dependency (`main._family_path_guard`) returns the same 404 for another
  family's id as for a missing one — on every method, so writes are refused too. Path and query ids.
- Lists and search: the shared scope (`services.authz.member_entity_scope` / `require_entity_access`) filters in
  SQL; households, X-ray, approvals, notifications filter explicitly.
- Everything else: the response scrubber removes any JSON list row naming an invisible family.
- Writes naming family ids in their body: `require_visible`.
- Assistant counts, document search / review, ownership tree, commitments: the legacy resolver follows the rule.
- Notifications about a family reach only people who can see it (read side: list and unread count).
- Cost: the switch rides on the Principal's one statement; the visible set is one statement, once per request.

The full route table: `docs/VISIBILITY1_ROUTES.md` (generated). Known gaps: `docs/PROJECT_STATUS.md`.

## How to turn it on

1. **Teams** — Admin → Staff visibility: create teams, add members, set each person's manager (People and managers).
2. **Assign families** — on each household or CRM record, the Team panel (person or team + role label); or Admin →
   Staff visibility → Unassigned families → select → Assign selected.
3. **Check the preview** — "What each person will see": every internal user, how many families, which, and why.
   Fix anyone listed under "Would see no family" before going on.
4. **Switch on** — "Turn enforcement on…" (org admins only). The confirm step restates the preview; the server
   refuses the switch if the "sees no family" count changed since you looked. Audited (`staff_visibility.enforcement`,
   with the preview's numbers). Turning it off is the same control.

The switch is `org_settings` `staff_visibility.enforced`; the generic settings screen refuses to change it.

## The Olivo demo

The Olivo demo family (tag `demo-olivo`, household "Olivo Family") is real data in production. For demos, before
switching on: open the Olivo Family household → Team → assign the demo presenter (or a "Demo" team the presenters
are on) as **Lead adviser**. One assignment on the household covers every Olivo entity, account, position,
document, note and liability. Leave it unassigned and, once enforcement is on, only org admins and its creator see
it.
