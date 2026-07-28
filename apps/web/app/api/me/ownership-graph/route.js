import { forwardToApi } from "@/lib/apiForward";

// Member-facing ownership graph. The focal entity is derived server-side from
// the caller's identity (their own delegate-grant principals); data flows
// through the member visibility engine (resolve_entity_set) + restricted-access
// filter. A member can never aim this at another member's tree.
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const sp = {
    direction: searchParams.get("direction") || undefined,
    edge_types: searchParams.get("edge_types") || undefined,
    as_of: searchParams.get("as_of") || undefined,
    max_depth: searchParams.get("max_depth") || undefined,
    focal_id: searchParams.get("focal_id") || undefined,
  };
  return forwardToApi(`/api/v1/me/ownership-graph`, { searchParams: sp });
}
