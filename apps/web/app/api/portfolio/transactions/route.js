import { forwardToApi } from "@/lib/apiForward";

// Transactions grid — list and create.
//
// CLAUDE.md Rule 5: the browser never calls FastAPI directly. org_id travels in
// the Auth0 access token that forwardToApi attaches; it is never a query param
// or a body field, and there is deliberately nothing in the allow-list below
// that would let a caller supply one.
const ALLOWED = [
  "position_id",
  "asset_id",
  "owner_entity_id",
  "transaction_type_code",
  "transaction_type_category",
  "trade_from",
  "trade_to",
  // Tri-state on the wire: absent, "true" or "false". The allow-list copies the
  // value through verbatim rather than coercing it, because "false" is a REAL
  // filter here (the realized-gain population) and a truthiness check would
  // drop it and silently widen the query back to everything.
  "is_corporate_action_adjustment",
  "source_system",
  "authority",
  "include_history",
  "search",
  "limit",
  "offset",
];

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const params = {};
  for (const key of ALLOWED) {
    const value = searchParams.get(key);
    if (value !== null && value !== "") params[key] = value;
  }
  return forwardToApi("/api/v1/portfolio/transactions", { searchParams: params });
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/portfolio/transactions", { method: "POST", body });
}
