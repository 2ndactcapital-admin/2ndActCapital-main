import { forwardToApi } from "@/lib/apiForward";

// CASH03 — recompute the household's ladder and instrument plan (audited server-side; manage_cash_policy).
export async function POST(_request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/cash/households/${encodeURIComponent(id)}/plan/regenerate`, { method: "POST", body: {} });
}
