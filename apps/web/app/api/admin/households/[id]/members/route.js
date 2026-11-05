import { forwardToApi } from "@/lib/apiForward";

// PORTENTRY2 (MUSE-OLIVO-H-02 / G-02) — add a person or a legal entity to an existing household.
export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/admin/households/${encodeURIComponent(id)}/members`, { method: "POST", body });
}
