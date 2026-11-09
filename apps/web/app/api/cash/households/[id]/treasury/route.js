import { forwardToApi } from "@/lib/apiForward";

// CASH01 — a household's coverage + obligations envelope (the API decides the scope; another family's is a 404).
export async function GET(request, { params }) {
  const { id } = await params;
  const horizon = new URL(request.url).searchParams.get("horizon_months");
  return forwardToApi(`/api/v1/cash/households/${encodeURIComponent(id)}/treasury`,
    { searchParams: { horizon_months: horizon } });
}
