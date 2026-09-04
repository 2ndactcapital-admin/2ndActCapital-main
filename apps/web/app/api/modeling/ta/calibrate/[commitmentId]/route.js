import { forwardToApi } from "@/lib/apiForward";

// Calibration (TA Model Sprint 4, Task 3). Mirrors the real backend path 1:1
// (POST /api/v1/modeling/ta/calibrate/{commitment_id}), gated server-side on
// manage_portfolio — a REAL, stricter gate than the view_portfolio read
// endpoints above it (Task 1b), enforced entirely by the API; this route
// does not duplicate or loosen that check. The body (including `dry_run`) is
// passed through verbatim — the real Pydantic model (CalibrateBody,
// extra="forbid") is the only validation.
export async function POST(request, { params }) {
  const { commitmentId } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/modeling/ta/calibrate/${commitmentId}`, {
    method: "POST",
    body,
  });
}
