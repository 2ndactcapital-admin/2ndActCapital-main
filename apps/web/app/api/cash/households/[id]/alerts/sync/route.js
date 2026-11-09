import { forwardToApi } from "@/lib/apiForward";

// CASH01 — open / close the household's coverage to-dos.
export async function POST(_request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/cash/households/${encodeURIComponent(id)}/alerts/sync`, { method: "POST", body: {} });
}
