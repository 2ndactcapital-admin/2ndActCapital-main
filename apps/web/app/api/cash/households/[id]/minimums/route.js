import { forwardToApi } from "@/lib/apiForward";

// CASH01 — an entity's / the household's minimum months of coverage.
export async function PUT(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/cash/households/${encodeURIComponent(id)}/minimums`, { method: "PUT", body });
}
