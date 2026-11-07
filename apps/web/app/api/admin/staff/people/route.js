import { forwardToApi } from "@/lib/apiForward";

// VISIBILITY1 — people of the org with their manager and teams.
export async function GET() {
  return forwardToApi("/api/v1/admin/staff/people");
}
