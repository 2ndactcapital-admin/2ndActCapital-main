"use server";

// Server actions for the Workflow Manager admin screens (Phase 3). Each wraps a
// server-side API call (auth enforced server-side — Org Admin own org or Super
// Admin) and returns a plain {ok, ...} result the client component can render.
// org_id is resolved server-side from the JWT; it is never passed from the
// client.

import {
  createWorkflow,
  createWorkflowTrigger,
  getWorkflows,
  getWorkflowTriggers,
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

export async function createEventTriggerAction(workflowDefinitionId) {
  // Chancery Phase 7 — configure a 'document_confirmed' event trigger. Returns
  // the refreshed trigger list so the viewer updates in place. This only
  // configures WHICH runs auto-start; every started run still honours each
  // step's autonomy tier (Tier-1 still pauses for approval).
  try {
    const trigger = await createWorkflowTrigger({
      workflow_definition_id: workflowDefinitionId,
      event_type: "document_confirmed",
      is_active: true,
    });
    const triggers = await getWorkflowTriggers();
    return { ok: true, trigger, triggers };
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
