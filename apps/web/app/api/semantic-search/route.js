import { forwardToApi } from "@/lib/apiForward";

// Chancery Phase 11b — semantic (vector) document search. Forwards the query to
// the FastAPI endpoint, which embeds it via the org's configured provider
// (currently always Voyage), ranks by pgvector cosine similarity, and enforces
// the same visibility engines as everything else. A DELIBERATELY SEPARATE action
// from the Phase-9 keyword /document-search. Rule 5: browser never calls FastAPI
// directly.
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  return forwardToApi("/api/v1/semantic-search", {
    searchParams: {
      q: searchParams.get("q") || "",
      limit: searchParams.get("limit") || "20",
    },
  });
}
