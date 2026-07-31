import { forwardToApi } from "@/lib/apiForward";

// Chancery Phase 9 — basic (NON-semantic) document search. Forwards the query to
// the FastAPI endpoint, which does plain ILIKE matching on filename, category,
// and extracted text. NOT the Phase-11 semantic search. Rule 5: browser never
// calls FastAPI directly.
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  return forwardToApi("/api/v1/document-search", {
    searchParams: {
      q: searchParams.get("q") || "",
      limit: searchParams.get("limit") || "50",
    },
  });
}
