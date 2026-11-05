import { forwardToApi } from "@/lib/apiForward";

// CRMDETAIL1 — typed detail fields (labels and types from the API).
export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/details`);
}

export async function PUT(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/details`, { method: "PUT", body });
}
