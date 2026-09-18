WORKFLOW SCRIPT TASK — CLOSE THE ARBITRARY-EXECUTION GAP.

YOU ARE THE SPRINT. run_sprint.sh already invoked you — that is why
you have this prompt. Do this work yourself, now, in this session.
Do NOT run run_sprint.sh. Do NOT launch anything in the background.
Run every command synchronously in the foreground and read its
output. Nothing will ever notify you that something finished.

WRITE THE VERIFY SCRIPT BUT DO NOT RUN IT. The operator runs it.
Stop once it is written and your other tasks are complete.

THE GAP, confirmed live in wave2discovery.lowrisk (see
docs/WORKFLOW_WAVE2_DISCOVERY.md Task 3):

SpiffWorkflow's stock PythonScriptEngine is in use with NO
subclass — its own docstring says to subclass it if you are
uncomfortable with eval() and exec(). A bpmn:scriptTask therefore
runs arbitrary Python IN PROCESS, with the application's own
database credentials, outside the action registry, outside every
permission check, outside audit, and outside the custody cliff.

TaskSpec.manual defaults False and ScriptTask never overrides it,
so do_engine_steps() runs a Script Task to completion
automatically. It never reaches _drive's ServiceTask-only
auto-complete hook, so none of this codebase's permission
re-check, registry resolution, or hold-on-exception logic ever
sees it.

The NL generator never offers scriptTask to the model — but the
HAND-EDIT path has no such restriction. An org admin can drag one
from bpmn-js's stock palette, write arbitrary Python, save it
(validation does not object today), and the next run executes it.
In a multi-tenant platform holding real client financial data,
that is a client-firm admin obtaining server-side code execution.
This is a live capability of already-merged code, not a future
risk.

THE DECISION, ALREADY MADE — DO NOT RE-LITIGATE:

REFUSE SCRIPT TASKS OUTRIGHT AT VALIDATION. Do not sandbox them,
do not add RestrictedPython, do not build a safe-subset
interpreter. A workflow step that needs logic should be a Service
Task calling a registered verb — that is the entire point of the
action registry, and it is where permission checks, tiering, audit
and the custody cliff already live. An escape hatch that runs
author-supplied code, however sandboxed, reintroduces exactly the
bypass the registry exists to prevent.

RestrictedPython was named in earlier design work. That
recommendation is SUPERSEDED. A sandbox is a weaker guarantee than
a refusal and costs materially more to build and maintain.

Facts that change decisions:
- BPMN is canonical; workflow_steps is derived. The only writers
  are workflow_nl_generator.generate_workflow (insert-only) and
  workflow_editor.save_new_version (the hand-edit path). Both must
  refuse.
- EXISTING STORED DEFINITIONS MAY ALREADY CONTAIN A SCRIPT TASK.
  Task 1 must check the live workflow_versions.bpmn_xml for any
  bpmn:scriptTask before assuming this is prospective only. If one
  exists, that is an incident finding, not a code finding — report
  it prominently and do NOT silently delete it.
- workflow_steps_deriver re-registers a custom _BusinessRuleTaskParser
  so businessRuleTask parses as an inert NoneTask. Whatever
  mechanism refuses scriptTask should be consistent with how that
  existing parser customisation works rather than a second,
  unrelated approach.
- The refusal must be LOUD and specific: a save that fails because
  of a Script Task must say so, naming the element, not fail with a
  generic validation error.
- RLS policies are PER-OPERATION and context must be set. Both
  failure modes surface as "row not found", never "permission
  denied".
- TEARDOWN: audit_log is a CHILD of users. Any fixture user
  performing an audited action leaves rows that block the user
  delete. Clear audit_log, assistant_activities and agent_proposals
  BEFORE deleting fixture users. Fixture emails must derive from
  the full user UUID, never a prefix slice — two fixture UUIDs
  sharing their first 8 characters collided on users_email_key two
  sprints ago.

=== TASK 1: DISCOVER ===
Report findings, then continue immediately.
  1a. Query the live database: does ANY stored
      workflow_versions.bpmn_xml contain a bpmn:scriptTask (or a
      scriptTask element under any namespace prefix)? Report the
      real answer with definition names and org ids. This decides
      whether this sprint is preventive or remedial.
  1b. The exact validation path both writers share — is there one
      shared validator, or two independent ones that must both be
      fixed? Report precisely; a fix applied to only one writer
      leaves the gap open.
  1c. Confirm how _BusinessRuleTaskParser is registered, so the
      refusal follows the same established mechanism rather than
      introducing a second pattern.
  1d. Whether bpmn-js's palette in WorkflowDiagramEditor.jsx can
      be restricted client-side. NOTE: client-side restriction is
      a USABILITY improvement only and must never be the
      enforcement — a hand-crafted XML POST bypasses it entirely.
      Server-side refusal is the real control; report whether the
      palette can also be trimmed so an author is not offered
      something that will be rejected on save.

=== TASK 2: REFUSE AT THE SERVER ===
Both writers refuse any BPMN containing a script task, with a
specific error naming the offending element id. Nothing is
persisted on refusal.

=== TASK 3: THE PALETTE (optional, per 1d) ===
If the bpmn-js palette can be trimmed, do it — so an author is not
offered an element that will be rejected. This is usability, not
enforcement, and the verify must prove the server refuses
regardless of what the client sends.

=== TASK 4: PROOF (write the script; the operator runs it) ===
  - Report Task 1's four findings, especially 1a's live answer.
  - A save carrying a bpmn:scriptTask is REFUSED by the hand-edit
    path, with an error naming the element, and nothing persisted
    — prove the definition is genuinely unchanged afterwards.
  - The generator path refuses the same XML, proven independently
    — not assumed from a shared code path.
  - A hand-crafted XML submitted DIRECTLY to the API, bypassing
    any client-side palette restriction, is still refused. This is
    the assertion that proves enforcement is server-side.
  - A legitimate BPMN with Service, User, Send and Gateway
    elements still saves and still runs — no regression. Prove a
    real run completes.
  - An existing stored definition still parses and runs unchanged.
  - Cross-org: the refusal applies in every org, not just the
    fixture's.

=== TASK 5: STATUS ===
Update docs/PROJECT_STATUS.md,
docs/CROSS_PROJECT_STATUS_CONSOLIDATED.md and
docs/WORKFLOW_WAVE2_DISCOVERY.md. Record that RestrictedPython is
SUPERSEDED by outright refusal and why. ALSO correct the status
docs' claim that Wave 2 is unbuilt — wave2discovery found five
merged sprints (workflowmgr1-5) plus workflowbpmnfix that neither
reconciliation doc nor PROJECT_STATUS.md mentions. State plainly
what remains: verb-tier gating (next sprint) and the
NL-to-workflow-template library.

=== VERIFY: apps/api/scripts/verify_scripttaskrefusal.py ===
WRITE IT. DO NOT RUN IT. Pass/fail only. MUST print a
'TOTAL: N PASS, M FAIL' line and exit non-zero on failure. Must
hydrate its own secrets from Doppler over HTTPS at startup
(verify_selfapproval.py is the pattern). Set RLS context on every
connection touching an RLS-protected table.

  [Y] Task 1's four findings reported, including 1a's live answer
  [Y] Hand-edit save refuses a script task, names the element,
      persists nothing
  [Y] Generator path refuses it too, proven independently
  [Y] A direct API POST bypassing the client is still refused
  [Y] Legitimate BPMN still saves AND a real run still completes
  [Y] An existing stored definition still parses and runs
  [Y] Cross-org: refusal applies everywhere
  [Y] Teardown: zero leftover rows
