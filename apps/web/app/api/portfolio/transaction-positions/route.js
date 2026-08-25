import { forwardToApi } from "@/lib/apiForward";

// Current positions, thin, for the "record against…" picker on the create form.
// Deliberately not /api/portfolio/positions: that call resolves a valuation per
// asset, which a picker does not need and which is its expensive part.
const ALLOWED = ["owner_entity_id", "search", "limit"];

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const params = {};
  for (const key of ALLOWED) {
    const value = searchParams.get(key);
    if (value !== null && value !== "") params[key] = value;
  }
  return forwardToApi("/api/v1/portfolio/transaction-positions", {
    searchParams: params,
  });
}
