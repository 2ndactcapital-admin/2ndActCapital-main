import { forwardToApi } from "@/lib/apiForward";

// The Run History screen's live list.
//
// CLAUDE.md Rule 5: the browser never calls FastAPI directly. The access token
// carries org_id and the caller's identity, and there is deliberately nothing
// here that would let a caller supply either — the list is scoped server-side
// to the caller's org (all orgs for a Super Admin).
//
// THE QUERY ALLOW-LIST IS EXHAUSTIVE. Only these four names are forwarded, so a
// parameter the API has not been asked to honour cannot be smuggled through
// this route by a caller who guessed a column name. Each value is still
// validated by FastAPI — an unknown status or period comes back as a real 422,
// which the screen surfaces verbatim.
const FORWARDED = ["status", "period", "since", "until"];

export async function GET(request) {
  const incoming = new URL(request.url).searchParams;
  const searchParams = {};
  for (const key of FORWARDED) {
    const value = incoming.get(key);
    if (value !== null && value !== "") searchParams[key] = value;
  }
  return forwardToApi("/api/v1/admin/workflow-runs", { searchParams });
}
