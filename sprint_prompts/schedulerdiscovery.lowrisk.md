WORKFLOW SCHEDULER — DISCOVERY ONLY. Read-only investigation, NO
CODE CHANGES. 5 tasks, findings-only. Establishes real facts the
design will build from — same pattern as litellmdiscovery earlier
this project: report, do not modify.

THERE IS NO HUMAN AVAILABLE. Report each task's findings, THEN
CONTINUE IMMEDIATELY to the next task in the same response —
never stop and wait.

DO NOT: write any code, create any file other than the findings
document specified in Task 5, modify any schema, install
anything.

=== TASK 1: real current run/step schema and cost/duration
capture ===
Report the REAL current columns on workflow_runs and
workflow_run_steps. Does step-level duration or cost capture
already exist for ANY step type, or only for AI-involved steps
via ai_decision_log? If only the latter, report exactly how an
AI-involved step's cost gets attributed back to its run — the
real join path, not an assumption.

=== TASK 2: real permission model for workflow authoring ===
Confirm the REAL current permission(s) gating who can create,
edit, or delete a workflow definition today (if any exist at
all). Report the exact permission name(s), and the real deployed
role grants for each, per the established pattern already proven
for view_portfolio/manage_portfolio in the portfolio UX sprints —
this sprint's own future permission work must reuse real,
existing names/patterns, not invent new ones.

=== TASK 3: real existing notification mechanism ===
Find the REAL, current, working mechanism the platform uses
today to notify a user of something (any real example — a
completed workflow run, an assigned task, anything). Report its
exact real invocation point and shape. This is what the
scheduler's future failure-alerting must reuse, not a new
notification system.

=== TASK 4: real Render service topology, re-confirmed ===
Confirm the REAL, current list of Render services for this
project (known from earlier discovery: 2ndactcapital-api, and as
of tonight, a second service for LiteLLM — hollisworks-litellm).
Report whether Render's account/plan genuinely offers a native
"Cron Job" service type distinct from a Web Service — check this
directly against Render's real, current service-type options
available to this account, not assumed from general knowledge.

=== TASK 5: WRITE FINDINGS ONLY ===
Write docs/WORKFLOW_SCHEDULER_DISCOVERY_FINDINGS.md — a real,
structured report of everything found in Tasks 1-4. This is a
discovery record, not a design document — report facts, not
recommendations. Commit this file. No other file should be
created or modified.

=== VERIFICATION ===
There is no code to verify — this is discovery-only. Confirm
docs/WORKFLOW_SCHEDULER_DISCOVERY_FINDINGS.md was created and
contains real, specific findings (not placeholders) for all 4
tasks. Confirm via git diff that NO file other than this one was
created or changed.
