ACTION REGISTRY — DEFECTS, TIERS, AND THE PROPOSE SURFACE.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

WRITE THE VERIFY SCRIPT BUT DO NOT RUN IT. The operator runs it.
Stop once it is written and your other tasks are complete.

CONTEXT: assistant_action_catalog has 16 rows, not the ~85 the
agentic design assumed. That taxonomy was a proposed menu, never
built. At 16 verbs, required_permission IS the capability
vocabulary — a separate capability column would be a second
parallel vocabulary maintained against a 16-row table, which is
exactly the registry duplication this codebase already suffers
from. So the long-open "capability annotation" item is DISSOLVED,
not solved: fix required_permission's defects and tools_for filters
on it directly. Revisit only when verb count genuinely outgrows
permissions.

DECISIONS ALREADY MADE, DO NOT RE-LITIGATE:

- DROP default_autonomy. It correlates 1:1 with access_type across
  all 16 rows — every read 'auto', every write 'confirm', no
  exceptions. It has never encoded a decision anyone made. A field
  that looks like policy and isn't will eventually be trusted.
- ADD `tier`, integer, per the established three-test rule: does it
  move money or create an obligation to; does it produce an
  artifact a third party relies on; does it mutate ownership,
  economic terms, or a posted ledger line.
    Tier 1: spv.subscribe, spv.record_transaction,
            entity.link_ownership, spv_carry.propose_from_realization
    Tier 2: crm.draft_note, litellm.reload_model_cost_map
    Tier 3: all 10 reads
  NOTE spv_carry.propose_from_realization is Tier 1, not 2. It
  proposes rather than posts, but carry economics ARE test three
  and the proposal is the artifact the GP relies on.
- TIER DIRECTION, and this must be written down: Tier 1 is the
  HIGHEST-stakes class. The agent contract's "tier ceiling 1" reads
  as permissive-low — same number, opposite intuition, and the two
  meet in tools_for. Name the column `max_tier` on the agent side,
  with Tier 1 as the ceiling, and document the direction in a
  comment that reads correctly at 11pm.

=== THE FOUR DATA DEFECTS ===

  1. spv.subscribe — access_type 'write', commits capital, and has
     NO required_permission AT ALL, while spv.show_captable (a
     READ of the same SPV) requires manage_deals. This is the only
     verb in the registry that creates a financial obligation and
     the only write with no gate. FIX THIS FIRST. Task 1 decides
     the right permission from what already exists; also consider
     whether show_captable's gate is itself wrong.
  2. crm.draft_note — reversible=false on a DRAFT. Deleting a draft
     is trivial. NOTE: this was deliberately set false earlier
     because the /undo endpoint it unlocks is hollow (no undo_token
     stored, entity_notes has no soft-delete). Do NOT flip it back
     to true unless that undo path is genuinely made real. Report
     the conflict rather than silently choosing.
  3. required_permission MIXES AXES. entity.link_ownership holds
     'staff', which is a ROLE; every other value (manage_deals,
     manage_billing, author_workflows) is a PERMISSION. One column,
     two vocabularies. Fix to a real permission.
  4. THREE MODULES, ONE DOMAIN. entities / entity / entity_graph
     collapse to `entity`. Module is write-only metadata read by
     nothing but sync_catalog's upsert (established by an earlier
     sprint) — but CONFIRM that still holds before relying on it.

=== ON `reversible` FOR READS ===
All 10 reads carry reversible=false, which conflates "cannot be
undone" with "does not apply". Making it nullable is more correct
but creates a three-state column every consumer must handle.
DECIDE on evidence: grep for every real reader of `reversible`. If
nothing reads it for a read verb, leave it false and document that
it is meaningless there. If something does, make it nullable.
Report which and why.

=== ADD ONE VERB: propose() ===
The universal write surface every agent depends on. One row.
`propose(object_type, payload, rationale)` — an agent's output
lands as an agent_proposals row (that table exists, built last
sprint with a real maker-checker CHECK constraint). Do NOT add
other speculative verbs: the registry is thin against the seven
agents (Chancery/Custodial Ops and Compliance Analyst have ZERO
verbs today) but verbs get added when the agent needing them is
built. The taxonomy is a menu, not a backlog.

Facts that change decisions:
- sync_catalog rebuilds the registry from SOURCE at every startup.
  A database-only fix silently reverts on the next deploy. Edit
  services/assistant_actions/*.py; the DB is downstream.
- An upsert keyed on action_key INSERTS on a rename and leaves the
  old row orphaned. A module collapse that renames action_keys
  will duplicate unless handled. This happened before — prove the
  row count afterwards.
- workflow_steps.action_registry_key is a SOFT text reference with
  NO FK. Renaming an action_key will not cascade and WILL silently
  break any workflow step referencing the old value.
- RLS policies are PER-OPERATION and context must be set. Both
  failure modes surface as "row not found", never "permission
  denied".
- NEVER let code assert a cause it cannot know.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Every real reference to the action_keys that would change
      under the module collapse — in code, in workflow_steps, in
      BPMN fixtures. State plainly whether renaming is SAFE or
      whether module should be corrected instead.
  1b. The right permission for spv.subscribe from the existing
      catalog, and whether spv.show_captable's manage_deals gate
      is itself wrong.
  1c. Every real reader of `reversible`, to settle the reads
      question on evidence.
  1d. Confirm module is still write-only metadata.

=== TASK 2-5: IMPLEMENT ===
The defects, the tier column, the module collapse per 1a's
finding, and propose(). Edit SOURCE files, not just the database.

=== TASK 6: PROOF (write the script; the operator runs it) ===
  - Report Task 1's four findings.
  - spv.subscribe now has a real permission gate — and a caller
    without it is genuinely refused, proven at the call path.
  - default_autonomy is gone from schema AND source.
  - Every row has a tier; the four Tier-1 verbs are exactly the
    ones listed above.
  - propose() exists and is invocable.
  - The module collapse left no orphaned action_keys — prove the
    exact row count.
  - Every workflow_steps.action_registry_key still resolves —
    prove by a real join.
  - sync_catalog does NOT revert any of it — prove by running it
    and re-reading.
  - required_permission holds only real permissions, no roles.
  - Cross-org: the registry is org-scoped and no other org was
    touched.

=== TASK 7: STATUS ===
Update docs/PROJECT_STATUS.md and
docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md. Record: the capability
item is DISSOLVED (required_permission is the vocabulary at this
scale, revisit when verb count outgrows it); the tier direction and
the max_tier naming decision; that the registry is thin against
five of seven agents and verbs are added with the agent that needs
them.

=== VERIFY: apps/api/scripts/verify_actionregistryfix.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only. MUST print a
'TOTAL: N PASS, M FAIL' line and exit non-zero on failure. Must
hydrate its own secrets from Doppler over HTTPS at startup
(verify_agenticmakerchecker.py is the pattern). Set RLS context on
every connection touching an RLS-protected table.

  [Y] Task 1's four findings reported
  [Y] spv.subscribe gated; an ungated caller is refused at the
      call path
  [Y] default_autonomy gone from schema and source
  [Y] Every row has a tier; the four Tier-1 verbs are exactly
      correct
  [Y] propose() exists and is invocable
  [Y] No orphaned action_keys; exact row count proven
  [Y] Every workflow_steps.action_registry_key resolves
  [Y] sync_catalog does not revert the fixes
  [Y] required_permission holds only permissions, never roles
  [Y] Cross-org isolation
  [Y] Teardown: zero leftover rows
