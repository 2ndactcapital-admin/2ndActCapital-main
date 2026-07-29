"use server";

// Server actions for the Workflow Manager admin screens (Phase 3). Each wraps a
// server-side API call (auth enforced server-side — Org Admin own org or Super
// Admin) and returns a plain {ok, ...} result the client component can render.
// org_id is resolved server-side from the JWT; it is never passed from the
// client.

import { createWorkflow, getWorkflows, saveWorkflowVersion } from "@/lib/api";

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
