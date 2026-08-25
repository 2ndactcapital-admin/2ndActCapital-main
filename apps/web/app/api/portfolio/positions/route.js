import { forwardToApi } from "@/lib/apiForward";

// Positions grid — list and create.
//
// CLAUDE.md Rule 5: the browser never calls FastAPI directly. org_id travels in
// the Auth0 access token that forwardToApi attaches; it is never a query param
// or a body field, and there is deliberately nothing in the allow-list below
// that would let a caller supply one.
const ALLOWED = [
  "owner_entity_id",
  "asset_id",
  "taxonomy_key",
  "taxonomy_prefix",
  "source_system",
  "authority",
  "ownership_basis",
  "as_of_from",
  "as_of_to",
  "superseded",
  "include_history",
  "search",
  "resolve_values",
  "value_as_of",
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
  return forwardToApi("/api/v1/portfolio/positions", { searchParams: params });
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/portfolio/positions", { method: "POST", body });
}
