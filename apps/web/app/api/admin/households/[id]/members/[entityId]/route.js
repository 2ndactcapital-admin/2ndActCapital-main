import { forwardToApi } from "@/lib/apiForward";

// PORTENTRY2 — remove a member from a household (audited; refused while it owns an account of the household).
export async function DELETE(_request, { params }) {
  const { id, entityId } = await params;
  return forwardToApi(
    `/api/v1/admin/households/${encodeURIComponent(id)}/members/${encodeURIComponent(entityId)}`,
    { method: "DELETE" },
  );
}
