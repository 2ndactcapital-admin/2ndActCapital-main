import { forwardToApi } from "@/lib/apiForward";

// One run's detail — origin, step-by-step history and, for a held run, the real
// alerted-user set. Read-only; there is no write endpoint on a run, so this
// route has no POST/PATCH/DELETE to forward.
export async function GET(_request, { params }) {
  const { runId } = await params;
  return forwardToApi(
    `/api/v1/admin/workflow-runs/${encodeURIComponent(runId)}`,
  );
}
