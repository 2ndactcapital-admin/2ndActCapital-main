import { forwardToApi } from "@/lib/apiForward";

// POST forwarded server-side with the caller's own token. The body never
// carries an org_id — the API reads it from the verified session.
export async function POST(request) {
  const body = await request.json().catch(() => ({}));
  return forwardToApi("/api/v1/fee-chat/save", { method: "POST", body });
}
