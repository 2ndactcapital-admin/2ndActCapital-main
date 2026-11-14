import { forwardToApi } from "@/lib/apiForward";

// NAV3 — the record page's Holdings tab: held directly / through ownership (one net-worth core), at the page's as-of.
export async function GET(request, { params }) {
  const { id } = await params;
  const asOf = new URL(request.url).searchParams.get("as_of");
  return forwardToApi(`/api/v1/entities/${encodeURIComponent(id)}/holdings-split`,
    { searchParams: asOf ? { as_of: asOf } : undefined });
}
