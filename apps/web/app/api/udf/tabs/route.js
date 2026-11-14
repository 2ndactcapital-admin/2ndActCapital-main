import { forwardToApi } from "@/lib/apiForward";

// NAV3 — custom tabs for a record type (the EXISTING GET /udf/tabs; hidden tabs are never returned by the server).
export async function GET(request) {
  const appliesTo = new URL(request.url).searchParams.get("applies_to") || "";
  return forwardToApi("/api/v1/udf/tabs", { searchParams: { applies_to: appliesTo } });
}
