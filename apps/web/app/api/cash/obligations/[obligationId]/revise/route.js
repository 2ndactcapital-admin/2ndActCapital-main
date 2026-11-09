import { forwardToApi } from "@/lib/apiForward";

// CASH01 — a new version of a manual obligation (bi-temporal; the old version stays as history).
export async function POST(request, { params }) {
  const { obligationId } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/cash/obligations/${encodeURIComponent(obligationId)}/revise`, { method: "POST", body });
}
