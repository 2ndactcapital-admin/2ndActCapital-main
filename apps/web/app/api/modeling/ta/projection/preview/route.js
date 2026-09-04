import { forwardToApi } from "@/lib/apiForward";

// The "what if" preview tool (TA Model Sprint 3). Mirrors the real backend
// path 1:1 (POST /api/v1/modeling/ta/projection/preview) — an UNSAVED,
// non-persisted computation (Sprint 1's own proof), gated on view_portfolio
// same as the GET projection above it. The body is passed through verbatim;
// the real Pydantic model (ProjectionPreviewBody, extra="forbid") is the only
// validation — this route does not re-implement or loosen it.
export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/modeling/ta/projection/preview", {
    method: "POST",
    body,
  });
}
