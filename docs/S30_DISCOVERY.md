# S30 — Segregation of duties: Task 1 discovery (2026-11-08)

Repo at `46adaee` (merge wftemplates1) + the live database (`_discover_s30.py`, read only). Every finding below was read
from the code or the catalog. Nothing here comes from the prompt's wording alone.

## 1a. Every approval / maker-checker site

**How the sites are built.** Most approvals are services.human_tasks (HT) kinds. 23 kinds are registered
`maker_checker=True`. Each module decides in three layers:

1. The module's own pre-check in `decide`.
2. `HT.complete_task`, which refuses `task.maker_id == actor`. `workflow_engine.complete_user_task` then re-checks
   `proposed_by`, and the DB CHECK `workflow_run_steps_maker_checker` backs it.
3. The module's `_apply` / on_complete callback, which re-checks against the subject row. That row's own DB CHECK backs
   it.

Every site refused the maker by comparing **login ids** (`users.id`). The only site that ever allowed self-approval is
agent_proposals.review_proposal (selfapproval.structural). It applies only when the role-holder set of
`review_agent_proposals` other than the maker is empty, with `self_approved = true` and a reason, and the DB CHECK
`agent_proposals_maker_checker_chk` backs it. Super Admin was never exempt from the maker test anywhere: the comparison
runs before any permission check.

**S30 migration.**
- Every enforcement line below now calls `services.sod`: `enforce` at the deciding layer, `gate` at an inner layer, or
  `can_check` plus a BUGFIX4 `maker_names` refusal.
- Every display line calls `sod.may_check_display`.
- Each site keeps its own exception class and its own refusal text.
- The DB CHECK constraints are unchanged (the verify reads them from the catalog).

Legend for the "migrated" column:
- ✓ enforce: the deciding layer; allowed, self-approved and refused checks are all recorded in `sod_decisions`.
- ✓ gate: an inner layer; only a refusal is recorded.
- ✓ display: the screen offers Approve; the server re-checks.

### Enforcement sites

| site | file:function | item type | maker column(s) | who may check | refusal (code; DB backstop) | self-approval | migrated |
|---|---|---|---|---|---|---|---|
| `human_tasks.complete_task` | services/human_tasks.py:complete_task | every HT maker-checker kind (TASK_KIND_ITEM_TYPES) | context.human_task.maker_id | holder of the task's eligible permission (Super Admin passes) | HT.MakerCheckerError, maker_names text; workflow_run_steps_maker_checker | no | ✓ enforce (records the decision with the completed task) |
| `workflow_engine.complete_user_task` | services/workflow_engine.py:complete_user_task | workflow_user_task (or the HT kind's type) | workflow_run_steps.proposed_by | step's assigned_permission holder | E.MakerCheckerError; workflow_run_steps_maker_checker | no | ✓ enforce (a plain User Task) / gate (an HT task) |
| `agent_proposals.review_proposal:agent_proposal` | services/agent_proposals.py:review_proposal | agent_proposal | proposed_by | review_agent_proposals (Super Admin passes) | MakerCheckerError / NotEligibleError / SelfApprovalReasonRequiredError; agent_proposals_maker_checker_chk | **yes**: empty role-holder set, disclosed (`self_approved`, reason) | ✓ enforce + governed self-approval (policy, sod_decisions, compliance to-do) |
| `agent_proposals.review_proposal:workflow_step` | services/workflow_engine.py:resolve_tier_approval → review_proposal | workflow_step | proposed_by (= started_by) **+ the trigger's owner (S30)** | review_agent_proposals | as above | **yes**, as above | ✓ enforce (Task 8: the trigger owner is a maker too) |
| `agent_proposals.eligibility` | services/agent_proposals.py:is_eligible_reviewer | agent_proposal | maker_id | review_agent_proposals | returns False | n/a | ✓ (pure evaluation, nothing recorded) |
| `account_models.decide` | services/account_models.py:decide | account_model_link | maker_id | approve_proposals | MakerCheckerViolation(MAKER_CHECKER_RULE) | no | ✓ gate |
| `account_models._apply_outcome` | services/account_models.py:_apply_outcome | account_model_link | maker_id | approve_proposals | MakerCheckerViolation; account_model_links_maker_checker_chk | no | ✓ gate |
| `authority.decide` | services/authority.py:decide | authority_change | maker_id | approve_authority | MakerCheckerViolation; — | no | ✓ gate (+ household_id for org household rules) |
| `authority._apply` | services/authority.py:_apply | authority_change | maker_id | approve_authority | MakerCheckerViolation; household_authority_maker_checker_chk, account_authority_settings_maker_checker_chk | no | ✓ gate |
| `lot_method.decide` | services/lot_method.py:decide | lot_method_change | maker_id | approve_proposals | MakerCheckerViolation | no | ✓ gate |
| `lot_method._apply` | services/lot_method.py:_apply | lot_method_change | maker_id | approve_proposals | MakerCheckerViolation; household_lot_method_maker_checker_chk, account_lot_method_settings_maker_checker_chk | no | ✓ gate |
| `blocks._decide_change` | services/blocks.py:_decide_change | allocation_policy / allocation_correction | maker_id | approve_blocks | MakerCheckerViolation | no | ✓ gate |
| `blocks._apply_policy` | services/blocks.py:_apply_policy | allocation_policy | maker_id | approve_blocks | MakerCheckerViolation; allocation_policies_maker_checker_chk | no | ✓ gate |
| `blocks._apply_correction` | services/blocks.py:_apply_correction | allocation_correction | maker_id | approve_blocks | MakerCheckerViolation; allocation_corrections_maker_checker_chk | no | ✓ gate |
| `blocks.decide` | services/blocks.py:decide | trade_block | maker_ids[] | approve_blocks | MakerCheckerViolation | no | ✓ gate |
| `blocks._apply_block_outcome` | services/blocks.py:_apply_block_outcome | trade_block | maker_ids[] | approve_blocks | MakerCheckerViolation; trade_blocks_maker_checker_chk (guard trigger) | no | ✓ gate |
| `execution.decide` | services/execution.py:decide | execution_instruction | maker_ids[] | approve_execution | MakerCheckerViolation | no | ✓ gate |
| `execution._apply_outcome` | services/execution.py:_apply_outcome | execution_instruction | maker_ids[] | approve_execution | MakerCheckerViolation; execution_instructions_* guard triggers | no | ✓ gate |
| `execution._verify_upstream` | services/execution.py:_verify_upstream | rebalance_proposal (the upstream) | maker_id vs checker_id | — | MakerCheckerViolation | no | ✓ gate |
| `gifts.decide` | services/gifts.py:decide | gift_proposal | maker_id AND submitted_by | approve_tax | C.MakerCheckerViolation | no | ✓ gate |
| `gifts._apply` | services/gifts.py:_apply | gift_proposal | maker_id, submitted_by | approve_tax | C.MakerCheckerViolation; gift_proposals_maker_checker_chk | no | ✓ gate |
| `tilts.decide` | services/tilts.py:decide | tilt_set | maker_id AND submitted_by | approve_replication | C.MakerCheckerViolation | no | ✓ gate |
| `tilts._apply` | services/tilts.py:_apply | tilt_set | maker_id, submitted_by | approve_replication | C.MakerCheckerViolation; tilt_sets_maker_checker_chk | no | ✓ gate |
| `section351.decide` | services/section351.py:decide | section351_plan | maker_id AND submitted_by | approve_proposals | MakerCheckerViolation | no | ✓ gate |
| `section351._apply` | services/section351.py:_apply | section351_plan | maker_id, submitted_by | approve_proposals | MakerCheckerViolation; section351_plans_maker_checker_chk | no | ✓ gate |
| `model_provider._publish_precheck` | services/model_provider.py:_publish_precheck | provider_catalog_version | requested_by + version_maker_ids[] | Super Admin | HT.MakerCheckerError | no | ✓ gate |
| `model_provider.decide_publish` | services/model_provider.py:decide_publish | provider_catalog_version | requested_by + version_maker_ids[] | Super Admin | MakerCheckerViolation; catalog_versions_maker_checker_chk | no | ✓ gate |
| `model_provider._sub_precheck` | services/model_provider.py:_sub_precheck | provider_subscription | requested_by | Super Admin | HT.MakerCheckerError | no | ✓ gate |
| `model_provider.decide_subscription` | services/model_provider.py:decide_subscription | provider_subscription | requested_by | Super Admin | MakerCheckerViolation; provider_subscriptions_maker_checker_chk | no | ✓ gate |
| `models.decide` | services/models.py:decide | model_version | maker_ids[] | approve_models | MakerCheckerViolation | no | ✓ gate |
| `models._apply_outcome` | services/models.py:_apply_outcome | model_version | maker_ids[] | approve_models | MakerCheckerViolation; model_versions guard trigger | no | ✓ gate |
| `note_replication.decide` | services/note_replication.py:decide | note_replica | maker_ids[] | approve_replication | MakerCheckerViolation | no | ✓ gate |
| `note_replication._apply` | services/note_replication.py:_apply | note_replica | maker_ids[] | approve_replication | MakerCheckerViolation; note_replicas guard trigger | no | ✓ gate |
| `options_sleeve.decide_package` | services/options_sleeve.py:decide_package | option_package | maker_ids[] | approve_replication | MakerCheckerViolation | no | ✓ gate |
| `options_sleeve._apply_decision` | services/options_sleeve.py:_apply_decision | option_package | maker_ids[] | approve_replication | MakerCheckerViolation; option_packages guard trigger | no | ✓ gate |
| `rebalance_proposals.decide` | services/rebalance_proposals.py:decide | rebalance_proposal | maker_id (NULL = system) | approve_proposals | MakerCheckerViolation | no | ✓ gate |
| `rebalance_proposals._apply_outcome` | services/rebalance_proposals.py:_apply_outcome | rebalance_proposal | maker_id | approve_proposals | MakerCheckerViolation; rebalance_proposals_maker_checker_chk | no | ✓ gate |
| `long_short.decide_margin_agreement` | services/long_short.py:decide_margin_agreement | margin_agreement | maker_id | approve_proposals | MakerCheckerViolation | no | ✓ gate |
| `long_short._apply_margin_outcome` | services/long_short.py:_apply_margin_outcome | margin_agreement | maker_id | approve_proposals | MakerCheckerViolation; suitability_records_maker_checker_chk | no | ✓ gate |
| `held_away.decide_import` | services/held_away.py:decide_import | held_away_import | entered_by | approve_held_away | MakerCheckerViolation (maker_names text) | no | ✓ can_check + record_refusal + maker_names |
| `held_away._apply_import` | services/held_away.py:_apply_import | held_away_import | entered_by | approve_held_away | MakerCheckerViolation; held_away_imports_maker_checker_chk | no | ✓ gate |
| `integrated_import.decide` | services/integrated_import.py:decide | portfolio_import | entered_by | manage_portfolio (text: Super Admin) | PermissionError (maker_names text) | no | ✓ can_check + record_refusal + maker_names |
| `integrated_import._apply` | services/integrated_import.py:_apply | portfolio_import | entered_by | manage_portfolio | PermissionError; provisional_imports_checker_chk | no | ✓ gate |
| `hypothetical_policy.decide` | services/hypothetical_policy.py:decide | hypothetical_policy | maker_id | manage_hypothetical_policy | C.MakerCheckerViolation | no | ✓ gate |
| `hypothetical_policy._apply` | services/hypothetical_policy.py:_apply | hypothetical_policy | maker_id | manage_hypothetical_policy | C.MakerCheckerViolation; hypothetical_policies_maker_checker_chk | no | ✓ gate |
| `substitutions.decide` | services/substitutions.py:decide | substitution_set / substitution_denial | maker_id (NULL = system denial) | approve_tax | MakerCheckerViolation | no | ✓ gate |
| `substitutions._set_outcome` | services/substitutions.py:_set_outcome | substitution_set | maker_id | approve_tax | MakerCheckerViolation; substitution_sets_maker_checker_chk | no | ✓ gate |
| `substitutions._deny_outcome` | services/substitutions.py:_deny_outcome | substitution_denial | maker_id | approve_tax | MakerCheckerViolation; substitution_denials_maker_checker_chk | no | ✓ gate |
| `substitutions.retire_set` | services/substitutions.py:retire_set | substitution_set | maker_id | approve_tax | MakerCheckerViolation (no HT task) | no | ✓ enforce |
| `substitutions.retire_denial` | services/substitutions.py:retire_denial | substitution_denial | maker_id | approve_tax | MakerCheckerViolation (no HT task) | no | ✓ enforce |
| `tax_profiles.decide` | services/tax_profiles.py:decide | tax_profile / loss_carryforward | entered_by | approve_tax | MakerCheckerViolation | no | ✓ gate |
| `tax_profiles._apply_outcome` | services/tax_profiles.py:_apply_outcome | tax_profile / loss_carryforward | entered_by | approve_tax | MakerCheckerViolation; tax_profiles_maker_checker_chk, loss_carryforwards_maker_checker_chk | no | ✓ gate |
| `replica_terms.decide` | services/replica_terms.py:decide | replica_terms_version | maker_ids[] | approve_replication | MakerCheckerViolation (the HT kind is NOT maker-checker) | no | ✓ gate |
| `replica_terms._apply_terms_decision` | services/replica_terms.py:_apply_terms_decision | replica_terms_version | maker_ids[] | approve_replication | MakerCheckerViolation; replica_terms_versions guard | no | ✓ enforce (the deciding layer — HT does no maker test for this kind) |
| `workflow_templates.decide_platform_version` | services/workflow_templates.py:decide_platform_version | workflow_template_version | drafted_by | Super Admin | MakerCheckerViolation; wtv_maker_checker_chk | no | ✓ enforce |
| `disclosures.publish` | services/disclosures.py:publish | disclosure_version | drafted_by | manage_disclosures (platform: Super Admin) | MakerCheckerViolation; disclosure_versions_maker_checker_chk | no | ✓ enforce |
| `security_data.uploads.decide` | services/security_data/uploads.py:decide | data_upload | uploaded_by | manage_org_settings | UploadMakerChecker; data_uploads_maker_checker_chk | no | ✓ enforce |
| `fee_runs.approve` | services/fee_runs.py:approve | fee_run | assistant_activities.proposed_by | (no router caller) | MakerCheckerError; assistant_activities_maker_checker_chk | no | ✓ can_check + record_refusal |
| `spv_carry_runs.approve` | services/spv_carry_runs.py:approve | carry_run | assistant_activities.proposed_by | (no router caller) | MakerCheckerError; assistant_activities_maker_checker_chk | no | ✓ can_check + record_refusal |
| `trading_authority.approve_money_movement` | services/trading_authority.py:approve_money_movement | money_movement | proposed_by | trading_authority_grants ≥ limited | MakerCheckerError; assistant_activities_maker_checker_chk | no | ✓ enforce (`assert_maker_checker` kept, delegating to sod's pure rule) |
| `sod_admin.decide_rule` | services/sod_admin.py:decide_rule | sod_rule / sod_platform_rule | drafted_by | manage_org_settings (platform: Super Admin) | SoDAdminRefused (403); sod_rules_maker_checker_chk | no | ✓ enforce (new in S30) |
| `workflow_autonomy.decide_setting` | services/workflow_autonomy.py:decide_setting | workflow_autonomy_setting / workflow_autonomy_optin | maker_id (+ every trigger owner of the version, for an opt-in) | manage_org_settings (opt-in: author_workflows or manage_org_settings) | AutonomyRefused (403); was_maker_checker_chk | no | ✓ enforce (new in S29B) |
| `role_grant:role` | routers/admin.py:assign_role (PUT /admin/users/{id}/role) and approve_pending_user (POST /admin/users/{id}/approve — s30 fix 1) | role_grant | the granter (the receiver is the checker; persons, not logins) | — | SoDAdminRefused → 403 (new in S30); every refusal in sod_decisions | no | ✓ enforce (new: the floor, behind enforce_floor) |
| `role_grant:permission_set` | routers/profiles.py:assign_permission_set (POST /admin/permission-sets/{id}/users) | role_grant | the granter (the receiver is the checker; persons, not logins) | — | SoDAdminRefused → 403 (new in S30); every refusal in sod_decisions | no | ✓ enforce (new: the floor, behind enforce_floor) |
| `role_grant:profile` | routers/profiles.py:set_user_profile (PUT /admin/users/{id}/profile — s30 fix 1: a profile carries permissions) | role_grant | the granter (the receiver is the checker; persons, not logins) | — | SoDAdminRefused → 403 (new in S30); every refusal in sod_decisions | no | ✓ enforce (new: the floor, behind enforce_floor) |
| `role_grant:profile_permission` | routers/profiles.py:toggle_profile_permission (PUT /admin/profiles/{id}/permissions, granted=true, when the caller's person holds the profile — s30 fix 1, services.sod_admin.check_bundle_edit) | role_grant | the granter (the receiver is the checker; persons, not logins) | — | SoDAdminRefused → 403 (new in S30); every refusal in sod_decisions | no | ✓ enforce (new: the floor, behind enforce_floor) |
| `role_grant:permission_set_permission` | routers/profiles.py:toggle_permission_set_permission (PUT /admin/permission-sets/{id}/permissions, granted=true, when the caller's person holds the set — s30 fix 1, check_bundle_edit) | role_grant | the granter (the receiver is the checker; persons, not logins) | — | SoDAdminRefused → 403 (new in S30); every refusal in sod_decisions | no | ✓ enforce (new: the floor, behind enforce_floor) |

### Display sites (the screen's Approve / Publish offer; the server re-checks through the rows above)

All migrated to `sod.may_check_display` (✓ display):
- services/human_tasks.py: `tasks_for_user`, `can_decide`
- services/authority.py `change_actions`, services/lot_method.py `change_actions`
- services/blocks.py `actions_for` (block + correction), routers/blocks.py (policies, `is_maker`)
- services/execution.py `actions_for`, routers/execution.py `is_maker`
- services/gifts.py, services/tilts.py and services/section351.py actions
- services/note_replication.py and services/options_sleeve.py `actions_for`
- services/rebalance_proposals.py (approve / reject)
- services/held_away.py `import_actions`
- services/substitutions.py `set_actions` / `denial_actions` (4 lines)
- services/tax_profiles.py `actions_for`
- services/workflow_templates.py `platform_overview.can_decide`
- services/bulk_approvals.py `_decidability`
- routers/account_models.py (2), routers/models.py (2), routers/long_short.py, routers/portentry.py,
  routers/replication_rep10.py, routers/replication.py, routers/disclosures.py, routers/security_data.py

Not migrated, each with its reason:
- `workflow_engine.start_workflow_run` `exclude_user_id=started_by`: a notification recipient list, not a decision.
- rebalance_proposals `_submit`, long_short `acknowledge` / `presubmit`: the REVERSE rule (only the maker may submit or
  acknowledge), not a check.
- routers/workflows.py `can_review`: permission only, with no maker comparison.

**Every path that grants a role or permissions (s30 fix 1 sweep)**: every `INSERT INTO user_roles` /
`user_permission_sets`, every `users.profile_id` write, and every `profile_permissions` / `permission_set_permissions`
insert in routers/ and services/ was checked. The five `role_grant:*` rows above all go through
`services.sod_admin.check_role_grant`. Before fix 1 only `assign_role` and `assign_permission_set` did. The paths left
outside it, each for a stated reason:
- `services/invites.py:create_invite` → `grant_org_admin`: the grantee is a NEW row created in the same transaction.
  No login or person link exists yet, so it can never be the inviter.
- `services/users.py` provisioning → `grant_named_role(…, 'super_admin')`: no human grants anything here. The grant
  comes from the Doppler secret `PLATFORM_STAFF_EMAILS` plus a verified email.
- `routers/admin.py:link_pending_login`: this moves a login onto an existing person and retires the pending row. It
  grants no role (the pending row holds none).
- `services/rbac.py` `ensure_*` helpers (role_permissions): these are seeding and system helpers. No route edits a
  role's permissions.
- Revocations (deactivate, delete/anonymize, `granted=false` toggles): these grant nothing.

**[FIND]** There are no Python callers of `fee_runs.approve`, `spv_carry_runs.approve` or
`trading_authority.approve_money_movement` from any router. They are migrated anyway, so they decide the same way the
day they are wired.

## 1b. Roles, permissions, permission sets: who proposes / who approves

64 permissions. The live roles (2nd Act org) with holders: admin 2, org_admin 4, super_admin 2, support_staff 2,
member 3. advisor, compliance, fund_finance and investment_committee each hold `review_agent_proposals` and have
**0 holders**. Hollisworks: super_admin 1.

The make / check split per item type is the duty catalog (`sod_duties.permissions`, docs/s30_part1.sql §8). It pairs
`manage_X` with `approve_X`:
- authority, blocks, execution, held_away, models, proposals, replication and tax have separate make and check
  permissions.
- Some types use ONE permission for both duties: disclosures (`manage_disclosures`), hypothetical policy
  (`manage_hypothetical_policy`), integrated import (`manage_portfolio`), security-data uploads
  (`manage_org_settings`) and billing (`manage_billing`).

**[FIND] org_admin holds both sides of almost every pair** (approve_* and manage_* across authority, blocks, execution,
held-away, models, proposals, replication and tax). Every org admin is therefore on the conflict report, as expected.
They are still stopped per item by the floor and default rules: the matrix warns, the per-item rule blocks.

Profiles carry `permission_key` text: Adviser, CSA / Ops, Org Admin, Member and Community Member, plus fixture leftovers
(`udf01bverify_*`, `WFMGR4 Reviewer`). There is one real permission set, Compliance Override. The verify-fixture sets
`udf01bverify_pset1/2` are still live leftovers (**[FIND]**, not this sprint's).

**[FIND] the self-approval emptiness gate reads ROLE holders only**, while `holds_review_permission` reads the full
Principal (roles ∪ profile ∪ permission sets, PERMS1). S30 keeps that exactly: `sod.eligible_checkers` is role-based,
replaying selfapproval.structural. A person who holds `review_agent_proposals` only through a profile can check, but
does not count towards "another eligible checker exists".

## 1c. Identity: the "same person" test

- One person = one `users` row. Extra logins live in `user_identities` (auth0_sub → user_id) and resolve to the same
  row (services.users step 1), so two logins of one person are already one id at every site (bugfix4).
- **Gap: a person could once have a SECOND `users` row**, a placeholder or duplicate sign-in later linked. Two paths
  retire such a row:
  - `services.users` duplicate merge. The retired row KEEPS its `auth0_sub`, and the same sub is a `user_identities`
    row of the target.
  - `POST /admin/users/{id}/link` (operator). The retired row's `auth0_sub` is set NULL and moved to the target. The
    mapping was recorded only in `audit_log`.
- Live: 2 retired rows.
  - `3320ed63…` resolves through the duplicate-merge rule to `f46eb620…`.
  - `80f1744f…` has no sub and no audit row naming its target. **[FIND]** Its person is unknowable today.
- **No maker column in the database references either retired row**: 124 maker-like FK columns scanned, 0 rows. So the
  person test changes no live decision (Task 7).
- S30 fix: `public.user_person_links` (retired row → person). Both link paths now write it. `services.sod` also derives
  the duplicate-merge rule live.

## 1d. The workflow engine's Tier-1 path and scheduled runs

- A Tier-1 Service Task suspends: `_suspend_step` creates an agent_proposals row with `proposed_by = workflow_runs.
  started_by`. A NULL maker is refused, never "anyone may approve".
- `resolve_tier_approval` decides through `agent_proposals.review_proposal`, including the disclosed self-approval.
- A scheduled run: `workflow_scheduler._fire` passes the trigger's `created_by` as `started_by`. **The trigger's owner
  IS the maker.**
- An event-triggered run (`domain_events.publish_event`) passes `created_by or trigger.created_by`, so the event's
  creator wins when present. **The trigger owner was then NOT a maker**, and could approve a Tier-1 step its own trigger
  produced.
- S30 (Task 8): `resolve_tier_approval` passes the run's trigger owner (`context.trigger_id` →
  `workflow_triggers.created_by`) as an extra maker.
- Live triggers: 8 scheduled, all with an owner. 1 event trigger has `created_by` NULL (**[FIND]**): its runs have no
  trigger owner, so nothing changes for it.
- `POST /admin/workflow-triggers` already recorded the actor. It now refuses a create without one, and the platform-job
  trigger creator refuses a NULL owner.
- Registered verbs: 26 (16 write, 10 read). Before S30 none declared a duty. Every write verb now has one
  (`sod.VERB_DUTIES` / `AssistantAction.duty`), and the shared validator refuses a write verb with none.
