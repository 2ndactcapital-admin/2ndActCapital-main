import { forwardToApi } from "@/lib/apiForward";

// One global security — read for anyone with view_portfolio, PATCH for a Super
// Admin only.
//
// The refusal lives in FastAPI, not here. This route forwards the PATCH
// unconditionally and lets the server answer 403, which is the only answer that
// holds when the caller is curl rather than the app.
export async function GET(request, { params }) {
  const { securityId } = await params;
  return forwardToApi(
    `/api/v1/portfolio/global-securities/${encodeURIComponent(securityId)}`,
  );
}

export async function PATCH(request, { params }) {
  const { securityId } = await params;
  const body = await request.json();
  return forwardToApi(
    `/api/v1/portfolio/global-securities/${encodeURIComponent(securityId)}`,
    { method: "PATCH", body },
  );
}
