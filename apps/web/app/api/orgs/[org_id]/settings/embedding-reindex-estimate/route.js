import { forwardToApi } from "@/lib/apiForward";

// LiteLLM Phase C — the re-indexing friction dialog's data source. org_id
// comes from the route path (standing rule); new_model is the candidate value
// the admin is about to save, read from the query string.
export async function GET(request, { params }) {
  const { org_id } = await params;
  const newModel = new URL(request.url).searchParams.get("new_model") || "";
  return forwardToApi(`/api/v1/orgs/${org_id}/settings/embedding-reindex-estimate`, {
    searchParams: { new_model: newModel },
  });
}
