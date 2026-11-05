import { forwardToApi } from "@/lib/apiForward";

// CRMDETAIL1 — gross / liabilities / net for one record.
export async function GET(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/net-worth`);
}
