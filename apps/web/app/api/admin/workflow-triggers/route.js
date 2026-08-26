import { forwardToApi } from "@/lib/apiForward";

// The Triggers screen — list and create.
//
// CLAUDE.md Rule 5: the browser never calls FastAPI directly. The access token
// carries org_id and the caller's identity; there is deliberately nothing here
// that would let a caller supply either, and no query allow-list because the
// list endpoint takes no parameters — it is already scoped server-side to the
// caller's org (all orgs for a Super Admin).
//
// GET returns the ENVELOPE {rows, permissions}. The screen renders write
// controls from `permissions.can_write` alone.
export async function GET() {
  return forwardToApi("/api/v1/admin/workflow-triggers");
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/admin/workflow-triggers", {
    method: "POST",
    body,
  });
}
