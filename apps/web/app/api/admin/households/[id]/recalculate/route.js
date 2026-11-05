import { forwardToApi } from "@/lib/apiForward";

// BUGFIX3 (MUSE-OLIVO-H) — the household's Recalculate: the API runs the members' family-scoped rollup and returns
// the household's net worth. The API decides who may (internal + manage_members | manage_portfolio).
export async function POST(_request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/admin/households/${encodeURIComponent(id)}/recalculate`, { method: "POST" });
}
