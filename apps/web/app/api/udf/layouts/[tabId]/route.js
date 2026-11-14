import { forwardToApi } from "@/lib/apiForward";

// NAV3 — one custom tab's resolved layout (the EXISTING GET /udf/layouts/{tab}; 403 when the tab is hidden).
export async function GET(request, { params }) {
  const { tabId } = await params;
  return forwardToApi(`/api/v1/udf/layouts/${encodeURIComponent(tabId)}`);
}
