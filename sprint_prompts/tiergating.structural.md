WORKFLOW VERB-TIER GATING — MAKE TIER 1 GENUINELY SUSPEND.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

WRITE THE VERIFY SCRIPT BUT DO NOT RUN IT. The operator runs it.
Stop once it is written and your other tasks are complete.

THE GAP, confirmed live in wave2discovery.lowrisk (see
docs/WORKFLOW_WAVE2_DISCOVERY.md Task 5):

The workflow engine reads a Service Task's action_registry_key and
NOTHING ELSE. workflow_steps.autonomy_tier is fetched but used only
for a display count in the run console. A Tier-1 verb invoked from
a Service Task simply EXECUTES — no suspension, no approval.

Worse, WorkflowDiagramEditor.jsx already OFFERS a "Tier 1 — approval
required" option that does nothing. A UI promising an approval gate
the engine does not enforce is worse than no gate at all.

Tier 1 means: moves money or creates an obligation; produces an
artifact a third party relies on; or mutates ownership, economic
terms, or a posted ledger line. This is the prerequisite for the
agentic layer — "agents propose, deterministic code disposes" is
false while a Tier-1 step executes unattended.

Only two verbs are workflow_invocable today, so current blast
radius is small — but the gap is structural and every future
opt-in inherits it silently.

THERE ARE TWO TIER CONCEPTS AND THEY DO NOT CONNECT TODAY:
  - assistant_action_catalog.tier — intrinsic to the VERB (added by
    actionregistryfix.structural). Tier 1 = highest stakes.
  - workflow_steps.autonomy_tier — the AUTHOR's choice in the
    diagram, via the twoa:governance extension.

DECISIONS ALREADY MADE — DO NOT RE-LITIGATE:

- EFFECTIVE TIER IS MOST-RESTRICTIVE-WINS. Tier 1 is the highest-
  stakes class, so most restrictive = LOWEST number = min(). An
  author may UPGRADE a step (mark a Tier-3 verb as requiring
  approval) but may NEVER DOWNGRADE one (a Tier-1 verb stays Tier 1
  whatever the diagram says). This is the same most-restrictive-
  wins rule CLAUDE.md documents for dual-path permission resolution
  — follow it, do not invent a second rule.
- TIER DIRECTION: Tier 1 is highest-stakes. The agent contract's
  "tier ceiling 1" reads as permissive-low — same number, opposite
  intuition. Write the direction in a comment wherever tiers are
  compared, one that reads correctly at 11pm.
- APPROVAL REUSES agent_proposals. It already has a computed
  maker-checker rule (holds review_agent_proposals AND is not the
  maker), a database CHECK constraint enforcing it, and an
  emptiness-gated disclosed self-approval for single-reviewer orgs.
  A suspended Tier-1 step becomes a proposal. Do NOT build a second
  approval mechanism.
- NOTIFICATION REUSES member_todos via the established sibling
  pattern (create_held_run_alerts, create_credential_failure_alerts,
  create_model_availability_alerts). Do not build a new channel.

THE OPEN QUESTION TO SETTLE AND JUSTIFY:
A SCHEDULED run has started_by = NULL — nobody clicked. So who is
the MAKER for maker-checker purposes? Recommended: the trigger's
author, since they authorised the run. Confirm workflow_triggers
records who created it; if it does not, report that plainly and
decide — do NOT silently treat a NULL maker as "anyone may approve",
which would make every scheduled Tier-1 step self-approvable by
omission.

Facts that change decisions:
- _drive auto-completes ServiceTasks; that is where suspension must
  intercept. ScriptTasks are now refused at validation and parse
  (scripttaskrefusal.structural), so they are out of scope.
- EVERY reviewer role currently has zero holders except org_admin
  (1). So in the real 2nd Act org, most Tier-1 approvals will take
  the disclosed self-approval path. That is a staffing fact; state
  it in the status update.
- RLS: reads WITHOUT context set return None/0/[] SILENTLY, never
  an error. The previous sprint's verify had TWELVE such reads, and
  a verify script can "pass" by comparing empty against empty. Set
  RLS context on EVERY read, including before/after comparisons.
- NEVER let code assert a cause it cannot know.
- TEARDOWN: audit_log is a CHILD of users — clear audit_log,
  assistant_activities and agent_proposals before deleting fixture
  users. Fixture emails derive from the FULL user UUID, never a
  prefix slice.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. The exact point in _drive where a ServiceTask auto-completes,
      and how a run can be paused and later resumed — does
      SpiffWorkflow's serialised state support resuming mid-run
      cleanly, or does resumption need something else?
  1b. What Tier 2 was intended to mean in the workflow engine's own
      design. Tier 1 MUST suspend — that is settled. Report what
      Tier 2 should do and justify it; do not guess.
  1c. Whether workflow_triggers records its creator (the scheduled-
      run maker question above).
  1d. The run-status vocabulary, and whether a status for "awaiting
      approval" exists or must be added.

=== TASK 2: EFFECTIVE TIER ===
Compute effective tier as min(registry tier, diagram tier) at
execution. Surface it in the run console so the display matches
what the engine actually does.

=== TASK 3: SUSPEND AND RESUME ===
A Service Task whose effective tier is 1 does NOT execute. The run
pauses, an agent_proposals row is created with the correct maker,
and eligible reviewers are alerted. On APPROVAL the run resumes and
the verb executes EXACTLY ONCE. On REJECTION the verb never
executes and the run ends in a clear terminal state.

=== TASK 4: PROOF (write the script; the operator runs it) ===
  - Report Task 1's four findings.
  - A Tier-1 Service Task does NOT execute on reaching it — prove
    the verb's side effect genuinely did not happen, not merely
    that the run status changed.
  - A Tier-1 verb marked Tier 3 in the diagram STILL suspends — the
    author cannot downgrade. This is the assertion that proves
    most-restrictive-wins.
  - A Tier-3 verb marked Tier 1 in the diagram DOES suspend — the
    author can upgrade.
  - Approval resumes the run and the verb executes EXACTLY ONCE —
    prove the count, since a resume that double-fires a Tier-1 verb
    is worse than the original gap.
  - Rejection means the verb never executes.
  - The maker cannot approve their own suspended step when another
    eligible reviewer exists.
  - A scheduled run's maker is resolved per Task 1c's decision, and
    is never NULL-meaning-anyone.
  - A Tier-3 step still executes immediately — no regression.
  - The run console shows the EFFECTIVE tier, not the diagram's.
  - Cross-org: org A's reviewer cannot approve org B's suspended
    step.
  - Every read uses RLS context; every before/after comparison
    compares REAL rows, never None against None.

=== TASK 5: STATUS ===
Update docs/PROJECT_STATUS.md,
docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md and
docs/WORKFLOW_WAVE2_DISCOVERY.md. Record: effective tier is
most-restrictive-wins; the scheduled-run maker decision; that the
diagram editor's Tier-1 option is now REAL; and that with only
org_admin holding a reviewer grant, most approvals currently take
the disclosed self-approval path. Wave 2's remaining item after this
is the NL-to-workflow-template library only.

=== VERIFY: apps/api/scripts/verify_tiergating.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only. MUST print a
'TOTAL: N PASS, M FAIL' line and exit non-zero on failure. Must
hydrate its own secrets from Doppler over HTTPS at startup
(verify_scripttaskrefusal.py is the pattern). The check() helper
MUST be declared check(passed, label, detail) AND must assert
isinstance(passed, bool) — the previous sprint's helper had its
arguments reversed, so every assertion reported PASS regardless of
outcome.

  [Y] Task 1's four findings reported
  [Y] A Tier-1 step does not execute — side effect proven absent
  [Y] The author cannot downgrade a Tier-1 verb
  [Y] The author can upgrade a Tier-3 verb
  [Y] Approval resumes and the verb executes EXACTLY ONCE
  [Y] Rejection means it never executes
  [Y] The maker cannot approve their own step when another
      reviewer exists
  [Y] A scheduled run's maker is never NULL-meaning-anyone
  [Y] Tier-3 steps execute immediately — no regression
  [Y] The run console shows the effective tier
  [Y] Cross-org isolation
  [Y] Teardown: zero leftover rows
