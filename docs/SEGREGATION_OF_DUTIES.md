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
     No item type is in this state since S29B: role grants were the last, and their floor is now ON.)
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
  **Superseded by S29B Task 0 (Joe, 2026-11-08, "in order to pass security exams, that rule has to be turned on"):**
  platform policy `policy:role_grant` **v2** sets `enforce_floor = true` for every organization (v1 retired, kept; the
  change audited as `sod.platform_rule_versioned`). No one grants themselves a role, profile or permission set, on any
  grant path, from any login of the same person, Super Admin included. An organization cannot turn it off: an org
  policy can only tighten `enforce_floor` (`Context.policy()` ORs the platform value in), and `draft_rule` refuses the
  relaxation with 403.

Since S29B the role-grant floor is ON for everyone (an org has nothing to turn on). Only a Super Admin (platform policy) can turn a floor OFF. The person test
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

**Super Admins in the conflict report (s30 fix 1, the rule).** A Super Admin holds every duty inherently. The report
always lists them, because hiding them would understate who can both make and check. They are their own group:
`all_duties: true`, sorted after the people whose roles grant the conflict, and shown on the page as "Super Admin —
every duty" rather than one line per item type. Their per-item `conflicts` stay in the JSON and the CSV. Per item, the
floor and default rules stop them exactly as they stop anyone else.

**Every grant path is checked (s30 fix 1).** A grant to yourself is a floor conflict on every path that gives a person
a role or permissions, from any login of that person, Super Admin included. Each path has its own site key in
`services.sod_admin.GRANT_SITES`:
- `role_grant:role` — assign a role, or approve a pending login
- `role_grant:permission_set` — assign a permission set
- `role_grant:profile` — assign a profile
- `role_grant:profile_permission` / `role_grant:permission_set_permission` — add a permission to a profile or set that
  the caller's own person holds (`check_bundle_edit`)

The paths deliberately left outside it are listed with their reasons in docs/S30_DISCOVERY.md 1a.

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

## S29B — unattended workflow writes

Two item types decide S29B's switches: `workflow_autonomy_setting` (an org turning unattended writes on, raising a
limit, needing fewer shadow runs) and `workflow_autonomy_optin` (a workflow version signed off to run unattended — the
makers are the requester AND every owner of a trigger that starts that workflow, so the trigger owner is never the
checker). Site: `workflow_autonomy.decide_setting`. See docs/WORKFLOW_AUTONOMY.md.

## CASH02 — cash policies

Item type `cash_policy`: a household's or entity's cash policy (the three sleeves' boundaries, rungs, objective and
minimum coverage — `cash_policies`). Maker duty `set_cash_policy`, checker duty `approve_cash_policy`, both granted by
`manage_cash_policy`. No floor class (a policy is a target, not money movement — the money-movement floor duties come
with cash07); `self_approval_capable = false` (the table's CHECK forbids `approved_by ∈ maker_ids`), platform policy
`never` with the floor enforced. **Makers = the drafter, every editor of the draft, and the submitter.** Sites:
`cash_policy.decide` (gate, refusals recorded) → `human_tasks.complete_task` (kind `cash_policy_approval`, the decision
recorded) → `cash_policy._apply` (inner gate). Bulk approve (`bulk_approvals` type `cash_policy`) calls the same
`cash_policy.decide` per item. See docs/cash/CASH_POLICY.md.

## CASH07 — payment instructions (a FLOOR item type)

Item type `payment_instruction`, floor class **`transfer`** (with `gift_proposal`, `section351_plan`,
`money_movement`): money between a family's own accounts (`payment_instructions`). Maker duty `initiate_payment`,
checker duty `approve_payment` (permissions of the same names). Platform rules `floor:payment_instruction` and
`policy:payment_instruction` (never; enforce_floor true). **No self-approval, ever** — no organization setting (an org
policy turning self-approval on is ignored for a floor item) and no role, Super Admin included. Makers = the drafter,
every editor and the submitter (`maker_ids`); the DB CHECK `payment_instructions_maker_checker_chk` backs it, and a
manual reconciliation must be by someone other than the sender (`payment_instructions_reconciler_chk`). Sites:
`payments.decide` (gate, refusals recorded) → `human_tasks.complete_task` (kind `payment_instruction_approval`) →
`payments._precheck` (every maker, before the engine commits) → `payments._apply` (inner gate). Bulk approve (type
`payment`) calls `payments.decide` per item. An org with one eligible checker (2nd Act today) waits — by design.
See docs/cash/CASH07_NOTES.md §4.

## CASH08 — payees, bank-detail changes, the first payment, the third-party switch (four FLOOR item types)

All four are floor class **`transfer`**, never self-approval capable, with a platform `floor:` rule and a `policy:`
(never; enforce_floor true) each — no organization setting and no role (Super Admin included) relaxes them.

- **`payee`** — maker duty `create_payee` (`manage_payees`), checker duty `approve_payee` (`approve_payees`). Makers =
  the creator, every editor, the submitter (`cash_payees.maker_ids`); the DB CHECK `cash_payees_maker_checker_chk` backs
  it. Sites: `cash_payees.decide` → `human_tasks.complete_task` (kind `payee_approval`) → `cash_payees._precheck` →
  `cash_payees._apply`. A resumed (manually suspended) payee is re-approved the same way; the resumer is a maker.
- **`payee_bank_change`** — maker duty `change_bank_details` (`manage_payees`), checker duty `approve_bank_change`
  (`approve_payees`). The requester never approves (`cash_payees_bank_change_checker_chk` backs it), and an approval
  carries the CALL-BACK attestation (who, the number ON FILE, when — never a number from the request). Sites:
  `cash_payees.decide_bank_change` → `human_tasks.complete_task` (kind `payee_bank_change_approval`) →
  `cash_payees._bank_precheck` (refuses the generic inbox: no attestation) → `cash_payees._bank_apply`.
- **`first_payment_to_new_payee`** — maker duty `create_payee`, checker duty `approve_first_payment`
  (`approve_payment`): the first payment instruction to a payee is approved by someone who is neither the payee's
  creator nor its approver (makers = creator, approver and the instruction's makers). Site `cash_payees.first_payment_gate`
  (called from `payments._precheck`) — DORMANT while third-party payments are off.
- **`third_party_payments_setting`** — maker duty `request_third_party_switch`, checker duty
  `approve_third_party_switch` (`manage_org_settings`). Both people must be Super Admins (checked by the service on
  each principal — S30 itself has no notion of it) and the request names a counsel sign-off document. Sites:
  `cash_payees.decide_third_party_switch` / `_switch_precheck` (`_switch_checks`) → `human_tasks.complete_task` (kind
  `third_party_switch_approval`). In this release every approval of the switch is refused (`THIRD_PARTY_RELEASE_LOCKED`).

See docs/cash/CASH08_NOTES.md §4 and docs/cash/CASH_PAYEES.md.

## CASH06 — cash rules (a FLOOR item type)

Item type `cash_rule`, floor class **`transfer`**: a standing rule that drafts payment instructions between a family's
own accounts (`cash_rules`) is money-movement CONFIGURATION. Maker duty `initiate_payment`, checker duty
`approve_payment` (cash07's duties, reused). Platform rules `floor:cash_rule` and `policy:cash_rule` (never;
enforce_floor true). **No self-approval, ever.** Makers = every drafter / editor / submitter of the version, the rule's
OWNER (always — `cash_rules_owner_maker_chk`), and a resume's requester; the DB CHECKs `cash_rules_maker_checker_chk` /
`cash_rules_resume_checker_chk` back it. Sites: `cash_rules.decide` (gate, refusals recorded) →
`human_tasks.complete_task` (kind `cash_rule_approval`, activate or resume) → `cash_rules._precheck` (every maker, the
definition re-validated) → `cash_rules._apply` (inner gate). An edit is a new version; the active one runs until it is
approved. Pause / retire are immediate (tightening).

The rule's OWNER is the trigger owner (S30 Task 8) and the MAKER of every instruction the rule drafts
(`payments.draft_and_submit`, `maker_ids = [owner]`), so the `payment_instruction` floor makes each instruction's
checker someone else — the owner's attempt is refused and recorded in `sod_decisions`. See docs/cash/CASH06_NOTES.md §7.

