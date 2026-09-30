import { forwardToApi } from "@/lib/apiForward";

// Approve or reject a SUSPENDED Tier-1 Service Task (tiergating.structural).
// Forwarded server-side to FastAPI, which re-checks review_agent_proposals
// AND per-proposal maker-checker eligibility — this route trusts neither the
// browser's own idea of who may decide nor the button being visible at all.
export async function POST(request, { params }) {
  const { runId, stepId } = await params;
  const body = await request.json().catch(() => ({}));
  return forwardToApi(
    `/api/v1/admin/workflow-runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(stepId)}/decision`,
    { method: "POST", body },
  );
}
