import { forwardToApi } from "@/lib/apiForward";

// CRMDETAIL1 — end a liability (repaid / closed; history kept, never deleted).
export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json().catch(() => ({}));
  return forwardToApi(`/api/v1/liabilities/${encodeURIComponent(id)}/end`, { method: "POST", body });
}
