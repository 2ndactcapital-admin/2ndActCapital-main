import { forwardToApi } from "@/lib/apiForward";

// CRMDETAIL1 — edit a liability (a new version; the prior one stays as history).
export async function PATCH(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/liabilities/${encodeURIComponent(id)}`, { method: "PATCH", body });
}
