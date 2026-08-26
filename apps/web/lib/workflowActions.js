"use server";

// Server actions for the Workflow Manager admin screens (Phase 3). Each wraps a
// server-side API call (auth enforced server-side — Org Admin own org or Super
// Admin) and returns a plain {ok, ...} result the client component can render.
// org_id is resolved server-side from the JWT; it is never passed from the
// client.

import {
  createWorkflow,
  getWorkflows,
  saveWorkflowVersion,
} from "@/lib/api";

export async function createWorkflowAction(name, description) {
  try {
    const workflow = await createWorkflow({
      name: name || null,
      description,
    });
    const workflows = await getWorkflows();
    return { ok: true, workflow, workflows };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

// `createEventTriggerAction` was removed by schedulerux. It existed only for the
// old Triggers table's one write — a create form hardcoded to
// 'document_confirmed' — and its sole caller is gone. It also returned the
// refreshed trigger list, which is now an envelope ({rows, permissions}) rather
// than an array, so leaving it in place would have left a dead export that
// returns the wrong shape to whoever picked it up next.
//
// Trigger writes now go through the Next.js API routes under
// /api/admin/workflow-triggers (CLAUDE.md Rule 5), which the client component
// calls directly so a 422 from the API reaches the form intact.

export async function saveWorkflowVersionAction(definitionId, bpmnXml, changeSummary) {
  try {
    const version = await saveWorkflowVersion(definitionId, {
      bpmn_xml: bpmnXml,
      change_summary: changeSummary || null,
    });
    return { ok: true, version };
  } catch (error) {
    return { ok: false, error: error.message };
  }
}
