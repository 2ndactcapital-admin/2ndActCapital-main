import { forwardToApi } from "@/lib/apiForward";

// CASH01 — end a manual obligation (kept as history, never deleted).
export async function POST(request, { params }) {
  const { obligationId } = await params;
  const body = await request.json().catch(() => ({}));
  return forwardToApi(`/api/v1/cash/obligations/${encodeURIComponent(obligationId)}/end`, { method: "POST", body });
}
