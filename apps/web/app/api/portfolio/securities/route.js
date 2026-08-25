import { forwardToApi } from "@/lib/apiForward";

// Securities & Assets grid — list and create.
//
// CLAUDE.md Rule 5: the browser never calls FastAPI directly. org_id travels in
// the Auth0 access token that forwardToApi attaches; it is never a query param
// or a body field, and there is deliberately nothing in the allow-list below
// that would let a caller supply one.
//
// The allow-list is also the second reason this file exists. A pass-through
// that forwarded every query parameter would let the browser send
// `security_type` or `price_coverage` straight at an endpoint that reads them
// off the GLOBAL master — harmless as filters, but the habit of forwarding
// whatever arrives is what eventually forwards something that is not.
const ALLOWED = [
  "search",
  "asset_type",
  "asset_class",
  "valuation_method",
  "taxonomy_key",
  "taxonomy_prefix",
  "security_type",
  "linked",
  "include_inactive",
  "include_history",
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
  return forwardToApi("/api/v1/portfolio/securities", { searchParams: params });
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/portfolio/securities", { method: "POST", body });
}
