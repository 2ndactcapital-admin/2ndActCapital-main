import { forwardToApi } from "@/lib/apiForward";

// VISIBILITY1 — the "what will each person see" preview (org admin only, server-side).
export async function GET() {
  return forwardToApi("/api/v1/admin/staff-visibility");
}
