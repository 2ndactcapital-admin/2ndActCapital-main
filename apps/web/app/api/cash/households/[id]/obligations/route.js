import { forwardToApi } from "@/lib/apiForward";

// CASH01 — add a manual obligation (the API gates it: manage_portfolio, internal roles only).
export async function POST(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/cash/households/${encodeURIComponent(id)}/obligations`, { method: "POST", body });
}
