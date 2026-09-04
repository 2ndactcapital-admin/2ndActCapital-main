import { forwardToApi } from "@/lib/apiForward";

// Member/staff-facing commitment projection (TA Model Sprint 3). Mirrors the
// real backend path 1:1 (apps/api/routers/modeling_ta.py's
// GET /api/v1/modeling/ta/projection/{commitment_id}) — gated server-side on
// view_portfolio, not a new permission (Task 1b). CLAUDE.md Rule 5: the
// browser never calls FastAPI directly; org_id travels only in the caller's
// own Auth0 token via forwardToApi.
const ALLOWED = ["strategy_key", "periods_per_year", "horizon_periods"];

export async function GET(request, { params }) {
  const { commitmentId } = await params;
  const { searchParams } = new URL(request.url);
  const query = {};
  for (const key of ALLOWED) {
    const value = searchParams.get(key);
    if (value !== null && value !== "") query[key] = value;
  }
  return forwardToApi(`/api/v1/modeling/ta/projection/${commitmentId}`, {
    searchParams: query,
  });
}
