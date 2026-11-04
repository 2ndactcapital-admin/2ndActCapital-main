import { forwardToApi } from "@/lib/apiForward";

// Staff-facing ownership graph. Data flows through the staff visibility engine
// + restricted-access filter server-side (FastAPI); this route only forwards
// the caller's Auth0 token and the view params.
export async function GET(request, { params }) {
  const { id } = await params;
  const { searchParams } = new URL(request.url);
  const sp = {
    direction: searchParams.get("direction") || undefined,
    edge_types: searchParams.get("edge_types") || undefined,
    as_of: searchParams.get("as_of") || undefined,
    max_depth: searchParams.get("max_depth") || undefined,
  };
  // BUGFIX2 (WF-03): the staff tree has its own API path; /ownership-graph is the
  // CRM overview's flat {nodes, edges} read and shadowed the tree for years.
  return forwardToApi(`/api/v1/entities/${id}/ownership-tree`, { searchParams: sp });
}
