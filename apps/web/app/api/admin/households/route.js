import { forwardToApi } from "@/lib/apiForward";

// PERMS1 (REP-01) — Portfolio → Households management: the list envelope and
// "New household". org_id is never sent from here — the API reads it from the
// caller's own verified context (CLAUDE.md Rule 6); the gate (internal role +
// manage_members) is the API's.
export async function GET() {
  return forwardToApi("/api/v1/admin/households");
}

export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/admin/households", { method: "POST", body });
}
