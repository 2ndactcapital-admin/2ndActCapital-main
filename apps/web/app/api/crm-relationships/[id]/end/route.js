import { forwardToApi } from "@/lib/apiForward";

// CRMDETAIL1 — end a relationship (valid_to; the row stays as history).
export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json().catch(() => ({}));
  return forwardToApi(`/api/v1/crm-relationships/${encodeURIComponent(id)}/end`, { method: "POST", body });
}
