AGENTIC SUBSTRATE — DISCOVERY ONLY. Read-only investigation, NO
CODE CHANGES, NO SCHEMA CHANGES. 6 tasks, findings-only.

Establishes the real facts needed to decide two small but
hard-to-retrofit schema changes: capability annotation on the
action registry, and review_role on the proposal queue. Both were
flagged in the agentic architecture design work as "cheap now,
expensive to retrofit across S26-S29a" — but several of the
decisions inside them cannot be made correctly without knowing
what already exists.

THERE IS NO HUMAN AVAILABLE. Report each task's findings, THEN
CONTINUE IMMEDIATELY to the next in the same response.

DO NOT: write code, modify schema, create any file other than the
findings document in Task 6, install anything.

=== TASK 1: THE SOC VOCABULARY (S30) ===
S30 (SOC manager) shipped, all six phases plus follow-on UI. The
agentic design says each registry verb should carry a "capability
(SOC vocabulary)" — but that assumes a real, existing vocabulary.
Report:
  1a. What vocabulary does S30 actually define? Exact table(s),
      exact column(s), the real distinct values currently stored.
      Is it a fixed enum, a CHECK constraint, a reference table,
      or free text?
  1b. Is that vocabulary genuinely at the right granularity to
      annotate ~85 individual registry verbs, or is it coarser
      (e.g. describing job functions rather than capabilities)?
      Report honestly — "it exists but is the wrong shape" is a
      real and useful finding.
  1c. If it is NOT the right shape: what would a verb-level
      capability vocabulary need that S30's doesn't have? Do not
      design one — just report the gap precisely.

=== TASK 2: THE REAL AUTONOMY TIER STATE ===
  2a. assistant_action_catalog.default_autonomy — every distinct
      value actually stored, with counts. Is there a CHECK
      constraint or enum, or is it free text?
  2b. Confirm the claim that NO Tier-1 (draft-and-approve) value
      exists in real use. If true, what does the workflow layer
      use instead — workflow_steps.autonomy_tier is an integer
      1/2/3 per the scheduler work. Are these two DIFFERENT
      tier systems that don't share a vocabulary? Report the real
      relationship between them, not the assumed one.
  2c. Does anything today distinguish "what a human caller gets"
      from "what an agent caller would get" for the same verb?

=== TASK 3: THE TWO PERMISSION AXES, PRECISELY ===
  3a. Report the real, current contents of `roles` and `profiles`
      — every row, both vocabularies side by side. Confirm the
      known mismatch (RBAC lowercase snake_case vs SOC display
      names) and whether ANY mapping between them exists in
      schema or code today.
  3b. resolve_field_access_bulk / resolve_tab_visibility (from
      the CRM UDF module) bind to BOTH profile_permissions and
      permission_set_permissions with most-restrictive-wins.
      Report their real signature and whether that pattern is
      genuinely reusable for routing a proposal to a reviewer,
      or whether routing is a different enough problem that
      reuse would be forced.
  3c. For each of the 8 proposed agents (Chancery, Custodial Ops,
      Portfolio & Suitability, Deal & SPV, Fund Admin & Billing,
      Compliance Analyst, Hollis, Authoring) — does a plausible
      reviewer role ALREADY exist on either axis, or would new
      ones need creating? Report per agent.

=== TASK 4: THE PROPOSAL QUEUE, AS IT REALLY EXISTS ===
The agentic design assumes "Tier-1 proposal rows" as the landing
place for all agent output.
  4a. What is the real, current proposed-state mechanism? Is it
      assistant_activities, member_todos, workflow_run_steps with
      status='pending', or something else? Report what actually
      exists and is in real use.
  4b. Can that mechanism carry every object type an agent would
      produce (documents, adjustments, memos, scores,
      obligations), or is it shaped for one specific kind of
      proposal? If the latter, report exactly what constrains it.
  4c. Is there any existing routing/assignment concept on it at
      all today, or is it genuinely a flat, global queue?

=== TASK 5: THE THREE KNOWN-BAD REGISTRY ROWS ===
Confirm against live data (these were reported, verify them):
  5a. crm.draft_note marked reversible=false despite being a
      draft action.
  5b. Three rows whose action_key prefix drifts from their module
      value (reported: entity_graph->entity.*,
      queries->entities.*/investments.*). Name the exact rows.
  5c. spv.subscribe and spv.record_transaction — report their
      real current default_autonomy and whether the argument
      that they are mis-tiered holds against what they actually
      do in code.
  5d. Report the full current registry: every row, module,
      action_key, access_type, default_autonomy, reversible,
      required_permission. This is the real baseline any
      capability annotation would be applied to.

=== TASK 6: WRITE FINDINGS ONLY ===
Write docs/AGENTIC_SUBSTRATE_DISCOVERY.md — a structured report
of everything found in Tasks 1-5. This is a discovery record, not
a design document: report facts, note gaps, do NOT propose schema.
Commit this file. No other file should be created or modified.

=== VERIFICATION ===
No verify script — this is discovery-only and the report is the
deliverable. (run_sprint.sh will emit "FATAL: expected verify
script not found" at Step 3; that is expected noise here, not a
failure. Confirm the real outcome from git.)

Confirm docs/AGENTIC_SUBSTRATE_DISCOVERY.md exists with real,
specific findings for all 5 investigation tasks, and confirm via
git diff that NO other file was created or changed.
