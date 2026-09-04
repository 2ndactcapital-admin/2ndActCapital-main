import { forwardToApi } from "@/lib/apiForward";

// The obligation ledger (TA Model Sprint 4, Task 2) — a real, 36-month
// forward capital-call visibility view over one commitment's live
// projection, computed at read time and never persisted. Mirrors the real
// backend path 1:1 (GET /api/v1/modeling/ta/obligations/{commitment_id}),
// gated server-side on view_portfolio — the same read-only gate as the
// projection endpoint, not a new permission.
const ALLOWED = ["strategy_key", "periods_per_year"];

export async function GET(request, { params }) {
  const { commitmentId } = await params;
  const { searchParams } = new URL(request.url);
  const query = {};
  for (const key of ALLOWED) {
    const value = searchParams.get(key);
    if (value !== null && value !== "") query[key] = value;
  }
  return forwardToApi(`/api/v1/modeling/ta/obligations/${commitmentId}`, {
    searchParams: query,
  });
}
