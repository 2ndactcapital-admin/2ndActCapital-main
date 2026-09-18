WORKFLOW MANAGER WAVE 2 — DISCOVERY ONLY.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

READ-ONLY. NO CODE CHANGES. NO SCHEMA CHANGES. The findings
document is the deliverable. There is no verify script — say so
rather than writing one, and expect run_sprint.sh to emit
"FATAL: expected verify script not found" at Step 3. That is
expected noise here, not a failure.

WHY: Wave 2 (NL-authored, editable BPMN workflows) gates the entire
agentic layer — agents execute AS workflow instances, so no agent
can run until this exists. The agentic substrate is otherwise
complete: propose(), agent_proposals, maker-checker with an
emptiness gate, action tiers, escalation_reason. Wave 2 is now the
critical path.

The design work names SpiffWorkflow, bpmn-js, RestrictedPython and
a verb-tier gating architecture — but records none of it as scoped
sprints. Meanwhile a BPMN generator demonstrably EXISTS (its
token-limit truncation bug was fixed in workflowbpmnfix.structural),
and workflow_definitions / workflow_steps are real tables the
scheduler fires against. So SOMETHING is built. This sprint
establishes exactly how much, so the build sprints are scoped
against reality rather than against a design doc.

DECIDED, DO NOT RAISE AS AN OPEN QUESTION:
- The bpmn-js watermark is ACCEPTED for now. This is pre-production;
  the licensing decision belongs at customer-facing launch, not
  now. Do not treat it as blocking and do not propose alternatives.

Known already, do not re-derive:
- SpiffWorkflow 3.1.2 IS installed (confirmed in a real Render
  build log).
- workflow_triggers now supports real scheduled firing (RRULE,
  timezone-aware, idempotent claim) and a Render cron service runs
  the tick every 5 minutes.
- assistant_action_catalog has 17 verbs, each with a tier (1 =
  highest stakes) and a required_permission that is now a real
  permission on every row.
- litellm.reload_model_cost_map is the one workflow_invocable
  action registered so far.

=== TASK 1: WHAT IS ACTUALLY BUILT ===
Report findings, then continue immediately.
  1a. The real BPMN generator — exact file, what it produces, what
      consumes its output, and whether the produced XML is ever
      persisted or only transient.
  1b. Is SpiffWorkflow actually IMPORTED and USED anywhere, or
      merely installed? If used, by what and for what.
  1c. The real workflow_definitions and workflow_steps schema, and
      how a definition's BPMN relates to its steps rows — is BPMN
      canonical with steps derived, steps canonical with BPMN
      derived, or are they independently maintained?
  1d. The real execution engine — what actually runs a workflow
      today, how a Service Task invokes a registry verb, and where
      Tier-1 suspension is (or is not) enforced.

=== TASK 2: THE FRONTEND ===
  2a. Is bpmn-js installed in apps/web? If so, is it rendered
      anywhere or only a dependency?
  2b. What workflow UI genuinely exists today — the triggers
      screen and run console are known; report anything else,
      especially anything resembling an editor or canvas.

=== TASK 3: THE SCRIPT ENGINE ===
  3a. Is RestrictedPython installed? Used? If a BPMN script task
      exists at all, what executes it today.
  3b. If nothing executes scripts, say so plainly — that is a real
      finding, not a gap to paper over.

=== TASK 4: THE SEQUENCING DEPENDENCY ===
An earlier design note said workflow steps target entities and
series, so Wave 2 should land AFTER the Investment/Series
restructure. Report the real status of that restructure: is it
done, partially done, or unstarted? And does anything in
workflow_steps actually reference a series or investment today?
This determines whether Wave 2 is genuinely unblocked or waiting.

=== TASK 5: THE VERB-TIER GATING GAP ===
Registry verbs now carry a tier. Report what, if anything, in the
workflow engine reads it — and specifically whether a Tier-1 verb
invoked from a Service Task suspends for approval today or simply
executes. If it executes, that is the single most important
finding in this sprint; state it prominently.

=== TASK 6: WRITE FINDINGS ONLY ===
Write docs/WORKFLOW_WAVE2_DISCOVERY.md — a structured report of
Tasks 1-5. Facts and gaps only; do NOT propose schema, design the
build, or write a roadmap. Commit this file. No other file should
be created or modified — prove it with git diff.

Report honestly when something does not exist. "Nothing executes
scripts today" and "no editor exists" are useful, correct answers.
Do not soften a gap into a partial.
