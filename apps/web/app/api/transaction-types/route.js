import { forwardToApi } from "@/lib/apiForward";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const sp = {};
  if (searchParams.get("category")) sp.category = searchParams.get("category");
  if (searchParams.get("security_type")) sp.security_type = searchParams.get("security_type");
  return forwardToApi("/api/v1/transaction-types", {
    searchParams: Object.keys(sp).length ? sp : undefined,
  });
}
