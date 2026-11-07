import { forwardToApi } from "@/lib/apiForward";

// VISIBILITY1 — assign several families to one person or team (audited server-side).
export async function POST(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/admin/staff-visibility/bulk-assign", { method: "POST", body });
}
