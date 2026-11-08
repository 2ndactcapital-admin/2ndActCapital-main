# Segregation of duties (S30) — the one maker-checker rule

**Platform rule for every later sprint:** an approval site never compares a maker with a checker on its own. It asks
`services.sod`. The database CHECK constraints stay underneath as the last line of defence.

## The model

| table | what it holds |
|---|---|
| `public.sod_duties` | a duty per action class (`propose_trade`, `approve_trade`, `set_authority`, `publish_disclosure`, `manage_roles`, …), with its kind (make / check / administer) and the permissions that grant it |
| `public.sod_item_types` | every kind of item an approval site decides: its maker duty, its checker duty, its **floor class** (`trade`, `transfer`, `disclosure`, `role_grant`, or none), whether its table can record a disclosed self-approval, the rule in force before S30, and the site |
| `public.sod_rules` | VERSIONED rules (valid-time axis): platform **floor** rules, platform **default** rules, platform default **policies**, and an org's own **org** rules and policy overrides. Every version is kept. |
| `public.sod_decisions` | append-only: every real decision (allowed / refused / self-approved), with the rule that decided it. This feeds the self-approval log and the refused-checks report. |
| `public.user_person_links` | a retired login row → the person it belongs to |

Labels for item types and duties come from these tables (Rule 1). `services.sod.ITEM_TYPES` is the list of keys code
may name, and the verify asserts that it equals the table.

## Persons, not logins

Two logins of one person are one `users` row (`user_identities`). Where a person once had a SECOND row (a placeholder
sign-in later linked by `POST /admin/users/{id}/link`, or a duplicate merged by `services.users`), that row resolves to
the person:
- through `user_person_links`, which both link paths now write;
- or through the live duplicate-merge rule: a retired row whose `auth0_sub` is another user's linked identity.

## The rule (`Context.evaluate`)

1. No catalog row for the item type → **refused** (fail closed).
2. The checker IS a maker (compared as persons when the org's `enforce_floor` is on for the item type, as raw login ids
   when it is off):
   - floor item type → **refused** by the floor rule. No setting and no role relaxes this; Super Admin included.
   - the item type can self-approve, the policy says `when_no_other_checker`, and the eligible set is genuinely empty →
     **self-approval allowed**, with a disclosure.
   - otherwise → **refused** by the default rule.
   - (The pre-S30 rule was `none` and the floor is not enforced → **allowed**, with a `floor_not_enforced` warning.
     Only role grants are in this state today.)
3. The org's own rules (`same_item` with extra related makers, `same_household`, `same_period` reciprocal approvals;
   block or warn) → **refused**, or allowed with a warning.
4. **Allowed.**

One read (`sod.load`) fetches the item type, its active platform + org rules, the person behind every id, the duty
labels and, only when an org rule needs it, the household / period history. After that read the evaluation is pure,
with no clock.

## Floor rules (cannot be relaxed by an org)

- Approve your own trade: trade block, allocation correction, execution instruction, rebalance proposal.
- Approve your own transfer: gift, Section 351 plan, money movement.
- Publish your own disclosure.
- Grant yourself a role or a permission set.

A floor item type can never be self-approval capable (a DB CHECK enforces it), and a policy can never turn
self-approval on for one: the service refuses with 403 before anything is written.

**No silent tightening (Task 7).** The seeded platform policies EQUAL the rules in force before S30:
- `self_approval = when_no_other_checker` only for agent proposals and Tier-1 workflow steps. Everything else is
  `never`.
- `enforce_floor = true` everywhere the old rule already refused the maker.
- `enforce_floor = false` for **role grants**, because no rule existed: an admin could change their own role.

An org admin can turn the role-grant floor ON. Only a Super Admin (platform policy) can turn a floor OFF. The person test
changes no live decision: no maker column references a retired row (docs/S30_DISCOVERY.md 1c).

## Self-approval, governed

Self-approval happens only where the old rule allowed it, the org's policy still allows it for that item type, and the
eligible-checker set (role holders of the checker duty's permission, every maker person excluded, never Super Admin) is
empty. When it happens:
- the item carries it (`agent_proposals.self_approved`, plus a reason);
- `sod_decisions` records `self_approved` with the reason "no other eligible checker" and the policy that allowed it;
- a compliance to-do (`member_todos.source = 'sod_self_approval'`) goes to the org's `manage_compliance` holders and
  org admins, never the self-approver unless nobody else exists.

An org that turns it off makes the item wait.

## Conflicting roles (Task 6)

A role or permission-set grant is checked:
- **granting yourself** is a floor conflict, refused with 403 once the floor is on;
- a combination that lets the person both make and check an item type returns `sod_warnings`, each naming the rules
  that will still stop them on any one item.

`GET /admin/sod` → `conflicts` lists every person whose roles combine conflicting duties. Expect every org admin there:
the matrix warns, and the per-item rule blocks.

## Workflows (Task 8, S29b readiness)

- Every registered action declares its duty (`AssistantAction.duty`, or `services.sod.VERB_DUTIES`): `read`,
  `propose`, `approve` or `write`. A write verb without one is refused by the shared validator
  (`workflow_nl_generator._validate`).
- A trigger names its owner (`workflow_triggers.created_by`, required to configure one).
- A Tier-1 step of a scheduled or event-triggered run is checked against BOTH the run's starter and the trigger's owner.

## How a NEW approval site calls the service

```python
from services import sod

# the layer that DECIDES (records allowed / self-approved / refused):
await sod.enforce(conn, org_id=org_id, item_type=sod.MY_ITEM, checker_id=actor_id, maker_ids=[row["maker_id"]],
                  item_id=row["id"], site="my_module.decide",
                  refuse=lambda d: MyMakerCheckerError("maker-checker: …your own text…"))
# an inner re-check (an apply callback, a pre-check before services.human_tasks decides) records refusals only:
await sod.gate(conn, …same arguments…)
# a screen's Approve offer:
show_approve = can_approve and sod.may_check_display(user_id, maker_ids)
```

1. Add the item type to `public.sod_item_types` (its maker / checker duty, floor class, site) and to
   `services.sod.ITEM_TYPES`. Add a platform floor or default rule and a platform policy equal to what the site did
   before.
2. A human-task kind maps to its item type in `services.sod.TASK_KIND_ITEM_TYPES`. `services.human_tasks.complete_task`
   then decides through the service for you.
3. Keep the table's own CHECK (`approved_by <> maker_id`) as the backstop.
4. Name the site in docs/S30_DISCOVERY.md's table and in `verify_s30.SITES`.

## Admin → Segregation of duties

`/admin/sod`:
- the duties;
- the item types, with the policy in force;
- the rules (floor / default / policy / org, with history);
- the conflict report, the self-approval log and refused checks (last 90 days);
- CSV for each report (`/admin/sod/reports/{name}?format=csv`).

Who can do what:
- Read: `manage_org_settings`, `manage_compliance` or `view_audit_log`.
- An org admin (`manage_org_settings`) drafts an org rule or policy; a DIFFERENT person decides it.
- A Super Admin drafts a platform rule; a different Super Admin decides it.

## Operator notes

- Schema: `docs/s30_part1.sql`, applied with `apps/api/scripts/_apply_s30.py --all` (SCHEMA OK, 2026-11-08). It writes
  the platform catalog only.
- `_verify_cleanup.cleanup` always sets `sod.teardown = 'fixture'`, so every verify's fixture approvals (now recorded in
  the append-only decision log) tear down by fixture id.
