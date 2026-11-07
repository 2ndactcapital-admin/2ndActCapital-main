import { forwardToApi } from "@/lib/apiForward";

// VISIBILITY1 — set or clear who a user reports to (cycle-checked, audited server-side).
export async function PUT(request, { params }) {
  const { id } = await params;
  const body = await request.json();
  return forwardToApi(`/api/v1/admin/staff/users/${encodeURIComponent(id)}/manager`, { method: "PUT", body });
}
