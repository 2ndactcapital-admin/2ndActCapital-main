import { forwardToApi } from "@/lib/apiForward";

// The TA settings screen — read and write (TA Model Sprint 2).
//
// CLAUDE.md Rule 5: the browser never calls FastAPI directly, and Rule 6:
// org_id travels only in the caller's own verified JWT, never a request
// param or body — there is deliberately nothing here that would let a
// caller supply it. GET hits the real, open-read backend path; PUT hits the
// real, can_manage_org_settings-gated admin path. Both return the SAME
// envelope shape ({...settings, strategy_overrides, permissions}) so the
// screen can render a PUT response exactly like a GET response.
export async function GET() {
  return forwardToApi("/api/v1/modeling/ta/defaults");
}

export async function PUT(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/admin/modeling/ta/defaults", {
    method: "PUT",
    body,
  });
}
