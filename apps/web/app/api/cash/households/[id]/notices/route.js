import { forwardToApi } from "@/lib/apiForward";

// CASH01 — record a capital-call notice (matched to a commitment; supersedes the projection).
export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/cash/households/${encodeURIComponent(id)}/notices`, { method: "POST", body });
}
