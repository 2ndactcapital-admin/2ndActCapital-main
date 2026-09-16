import { forwardToApi } from "@/lib/apiForward";

// taskKey is a registry key like "ai.model.default" — encodeURIComponent
// keeps the dots intact through the proxy without Next.js treating them as
// extra path segments.
export async function PUT(request, { params }) {
  const { org_id, taskKey } = await params;
  const body = await request.json();
  return forwardToApi(
    `/api/v1/orgs/${org_id}/settings/ai-tasks/${encodeURIComponent(taskKey)}`,
    { method: "PUT", body },
  );
}
