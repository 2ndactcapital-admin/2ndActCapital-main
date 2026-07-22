import { forwardToApi } from "@/lib/apiForward";

// org_id comes from the route path, never a request body (standing rule); the
// backend still re-checks that the caller may read/write that org.
export async function GET(request, { params }) {
  const { org_id } = await params;
  const detail = new URL(request.url).searchParams.get("detail");
  return forwardToApi(`/api/v1/orgs/${org_id}/settings`, {
    searchParams: detail ? { detail } : undefined,
  });
}

export async function PUT(request, { params }) {
  const { org_id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/orgs/${org_id}/settings`, {
    method: "PUT",
    body,
  });
}
