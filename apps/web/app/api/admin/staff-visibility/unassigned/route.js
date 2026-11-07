import { forwardToApi } from "@/lib/apiForward";

// VISIBILITY1 — families no one is assigned to.
export async function GET() {
  return forwardToApi("/api/v1/admin/staff-visibility/unassigned");
}
