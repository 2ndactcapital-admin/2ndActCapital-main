import { forwardToApi } from "@/lib/apiForward";

// VISIBILITY1 — remove one assignment from a family's Team panel (audited server-side).
export async function DELETE(request, { params }) {
  const { id } = await params;
  return forwardToApi(`/api/v1/staff-visibility/assignments/${encodeURIComponent(id)}`, { method: "DELETE" });
}
