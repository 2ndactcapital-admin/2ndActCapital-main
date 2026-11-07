import { forwardToApi } from "@/lib/apiForward";

// BUGFIX4 — the household's own page: net worth (gross / liabilities / net, as-of), top holdings and whether this
// caller may Recalculate. The API decides who may read it (internal firm roles; a member is refused).
export async function GET(_request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/households/${encodeURIComponent(id)}/summary`);
}
