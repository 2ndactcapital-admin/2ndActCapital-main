import { forwardToApi } from "@/lib/apiForward";

// org_id comes from the route path, never a request body (standing rule); the
// backend still re-checks that the caller may read/write that org.
export async function GET(request, { params }) {
  const { org_id } = await params;
  return forwardToApi(`/api/v1/orgs/${org_id}/settings/model-selections`);
}

export async function PUT(request, { params }) {
  const { org_id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/orgs/${org_id}/settings/model-selections`, {
    method: "PUT",
    body,
  });
}
