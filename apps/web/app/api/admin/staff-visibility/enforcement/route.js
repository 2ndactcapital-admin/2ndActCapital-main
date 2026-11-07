import { forwardToApi } from "@/lib/apiForward";

// VISIBILITY1 — the org switch (org admin only, confirmed against the preview, audited server-side).
export async function PUT(request) {
  const body = await request.json();
  return forwardToApi("/api/v1/admin/staff-visibility/enforcement", { method: "PUT", body });
}
