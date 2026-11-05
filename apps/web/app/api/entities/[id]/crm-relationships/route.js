import { forwardToApi } from "@/lib/apiForward";

// CRMDETAIL1 — the record's governed relationships (GET) and "add a relationship" (POST). Server-side forward; the
// browser never calls FastAPI and never sends an org_id.
export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/crm-relationships`);
}

export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/crm-relationships`, { method: "POST", body });
}
