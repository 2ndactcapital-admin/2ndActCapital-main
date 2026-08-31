import { forwardToApi } from "@/lib/apiForward";

// Client components never call FastAPI directly. This forwards server-side,
// where the session (and therefore the org) is resolved — no org_id is ever
// carried on the client's side of the forward.
export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const qs = searchParams.toString();
  return forwardToApi(`/api/v1/fee-chat/conversation${qs ? `?${qs}` : ""}`);
}
