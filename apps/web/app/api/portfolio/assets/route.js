import { forwardToApi } from "@/lib/apiForward";

// Tenant assets, for the new-position asset picker. Rule 5 — via FastAPI, with
// the caller's token; org scoping happens server-side from JWT claims.
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const params = {};
  const search = searchParams.get("search");
  const limit = searchParams.get("limit");
  if (search) params.search = search;
  if (limit) params.limit = limit;
  return forwardToApi("/api/v1/portfolio/assets", { searchParams: params });
}
