import { forwardToApi } from "@/lib/apiForward";

// Dry-run preview: the next occurrences of a recurrence, BEFORE it is saved.
//
// Nothing is computed here. The whole body is forwarded to the API, which runs
// the SAME services.workflow_schedule recurrence the firing loop runs — a
// preview that agreed with the scheduler only because both "use RRULE" would be
// free to diverge, and the divergence would surface as a workflow running at a
// time the author was never shown.
//
// This route sits above /[triggerId] in the segment order, so "preview" is
// never matched as a trigger id.
export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/admin/workflow-triggers/preview", {
    method: "POST",
    body,
  });
}
