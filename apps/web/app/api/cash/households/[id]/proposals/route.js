import { forwardToApi } from "@/lib/apiForward";

// CASH04 — a DRAFT cash proposal per account of the household's active cash policies (manage_proposals; audited).
export async function POST(_request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/cash/households/${encodeURIComponent(id)}/proposals`, { method: "POST", body: {} });
}
