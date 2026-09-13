REGISTRY DEFECTS + ESCALATION ENUM. 5 tasks + verification.
Corrects five confirmed-wrong rows in assistant_action_catalog and
establishes the fixed escalation-reason vocabulary. All defects
below were verified live against the deployed database by
agenticdiscovery.lowrisk — see docs/AGENTIC_SUBSTRATE_DISCOVERY.md.

CONFIRMED REAL FACTS, DO NOT RE-DERIVE:
- The registry has exactly 16 rows, all in org
  00000000-0000-0000-0000-000000000001. Full baseline table is in
  the discovery doc, Task 5d.
- assistant_action_catalog.default_autonomy is plain text with NO
  CHECK constraint. The Python type hint
  Literal["suggest","confirm","auto"] is type-checker-only.
  'suggest' is valid and used by zero rows.
- workflow_steps.action_registry_key is a SOFT text reference to
  assistant_action_catalog.action_key with NO FK constraint.
  Renaming an action_key will NOT cascade and WILL silently break
  any workflow step referencing the old value. This is the single
  biggest risk in this sprint.
- The escalation_reason enum type was created in Part 1 and
  verified live. No column uses it yet — that is deliberate.
- REGISTRY.sync_catalog(pool, org_id) upserts the registry from
  code at startup (main.py:_startup). Editing the database
  directly WITHOUT editing the source in
  services/assistant_actions/*.py means the next startup silently
  reverts the fix. Source files are the real fix; the DB is
  downstream of them.
- DATABASE RENAME BLAST RADIUS IS ZERO, verified live: the only
  workflow_steps row with an action_registry_key points at
  marketplace.show_new_deals (not one of the five). member_todos
  .action_key holds URL paths (/admin/workflows/runs), not registry
  keys — a same-named column for a different concept. Task 1a
  therefore only needs to search CODE (routers, services, frontend,
  apps/api/fixtures/*.bpmn), not the database.
- Because the DB is clean, renaming action_key to match module is
  the PREFERRED direction unless Task 1a finds real code references.
- The escalation_reason enum was ALREADY CREATED and verified live
  before this sprint ran — all six labels confirmed present in
  correct order. Do not re-create it; just assert it exists.


THERE IS NO HUMAN AVAILABLE. Report findings, then continue
immediately in the same response. If uncertain, continue.

STANDING RULES: no interactive prompts.

=== TASK 1: DISCOVER — the rename blast radius ===
Report findings, THEN CONTINUE IMMEDIATELY in the same response.
  1a. Find EVERY reference to the four drifting action_keys
      (entity.link_ownership, entity.show_hierarchy,
      entities.count, investments.count) and the fifth milder one
      (litellm.reload_model_cost_map, module litellm_ops)
      anywhere in the codebase AND the live database — including
      workflow_steps.action_registry_key, any BPMN XML in
      apps/api/fixtures/, member_todos.action_key, and any
      hardcoded string in a router, service, or frontend file.
  1b. Report the real registration source file for each of the
      five. Note that spv_carry registers from
      services/spv_carry_runs.py, OUTSIDE the
      assistant_actions/ package — confirm whether any of these
      five do something similar.
  1c. Based on 1a: state plainly whether renaming is SAFE (zero
      live references outside the registry itself) or UNSAFE
      (real references exist that would break). If UNSAFE for
      any given key, do NOT rename it — fix the module value
      instead so module and prefix agree, and report why that
      direction was chosen. Renaming is not the goal;
      module/prefix agreement is.

=== TASK 2: FIX THE FIVE DRIFTS ===
Per Task 1c's real finding, per key: either rename the action_key
to match its module, or correct the module to match the key's
prefix — whichever is genuinely safe. Edit the SOURCE files, not
just the database. Report which direction was taken for each of
the five and why.

=== TASK 3: FIX crm.draft_note's reversible FLAG ===
crm.draft_note has reversible=false despite being an explicit
draft-and-confirm action whose own description says "The member
reviews the draft before it is saved." Set reversible=true in the
source file. Confirm no code branches on this flag in a way that
changes behavior unexpectedly — report what reads `reversible`
before changing it.

=== TASK 4: REAL PROOF ===
  - Report Task 1's three findings explicitly, especially 1c.
  - Every one of the five drifts is resolved: module and
    action_key prefix agree, across BOTH the source file and the
    live database row.
  - crm.draft_note reversible=true, in source and in the live row.
  - A real application startup (or a direct call to
    REGISTRY.sync_catalog) does NOT revert any of these fixes —
    prove by running it and re-reading the rows afterward. This
    is the assertion that catches the "fixed the DB, forgot the
    source" failure mode.
  - The registry still has exactly 16 rows afterward — no
    duplicates created by a rename that inserted rather than
    updated. This is a real risk: an upsert keyed on action_key
    will INSERT a new row for a renamed key and leave the old one
    orphaned. Prove the count, and prove no orphaned old key
    remains.
  - Every workflow_steps row's action_registry_key still resolves
    to a real registry row after the changes — proven by a real
    join, not by inspection.
  - The escalation_reason enum exists with exactly the six
    expected labels, in order.
  - Cross-org: confirm these are org-scoped rows and no other
    org's registry was touched (today only one org has rows, but
    prove the operation was scoped, not global).

=== TASK 5: UPDATE PROJECT STATUS ===
Update docs/PROJECT_STATUS.md: five registry defects corrected,
escalation_reason enum created and deliberately unwired pending a
real agent-run table. Note explicitly that assistant_action_catalog
.default_autonomy currently correlates 1:1 with access_type across
all 16 rows (every write=confirm, every read=auto) — recorded as a
real observation, NOT fixed here, because whether that correlation
is a deliberate invariant or an artifact of n=16 is an open design
question this sprint does not resolve.

=== VERIFICATION: apps/api/scripts/verify_registryfix.py ===
Pass/fail only. No interactive prompts.

Assertions:
  [Y] Report Task 1's three findings explicitly
  [Y] All five module/prefix drifts resolved, in source AND live DB
  [Y] crm.draft_note reversible=true, in source AND live DB
  [Y] sync_catalog does not revert the fixes — proven by running it
  [Y] Registry still has exactly 16 rows; no orphaned old keys
  [Y] Every workflow_steps.action_registry_key still resolves,
      proven by a real join
  [Y] escalation_reason enum has exactly the six expected labels
  [Y] Operation was org-scoped, not global
  [Y] Teardown: zero leftover rows
